from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from config.agent_prompts import EXPLORATION_PROMPT, SUMMARIZE_PROMPT, VALIDATION_DIFF_PROMPT
from config.settings import AppConfig, TracingConfig
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.usage import UsageLimits

from src.pipeline.agent import (
    DrawingDeps,
    create_unified_agent,
    create_validation_agent,
    make_loader,
)
from src.pipeline.tokens import (
    ContextOverflowError,
    DrawingMetrics,
    TokenBudget,
    ToolCallRecord,
    measure_schema_overhead,
)
from src.schema.enums import ConfidenceLevel, DrawingType, classify_drawing
from src.schema.extraction import SCALAR_FIELDS, ExtractionResult, RunMetadata
from src.schema.geometry import reconcile_panel_geometry
from src.schema.validation import (
    cross_validate_fields,
    merge_diff,
    normalize_values,
    validate_result,
)
from src.telemetry import tracing_instrumentation_settings
from src.tools.cache import ToolCache
from src.vision.client import VisionClient

logger = logging.getLogger(__name__)

DRAWING_EXTENSIONS = {".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".dwg", ".p3d"}


def _repair_truncated_json(raw: str) -> str | None:
    """Best-effort repair of JSON truncated mid-output by a max_tokens cap.

    Strategy: scan to find the last complete object/element boundary and close
    any open arrays and objects. Returns the repaired string, or None if no
    truncation is detected or repair fails.

    This recovers the leading portion of a verbose extraction (panels, title
    block) instead of discarding the whole output and falling back to the
    simple extractor.
    """
    s = raw.strip()
    if not s:
        return None
    try:
        json.loads(s)
        return None
    except json.JSONDecodeError:
        pass

    last_obj = max(s.rfind("}"), s.rfind("]"))
    if last_obj < 2:
        return None
    candidate = s[: last_obj + 1]
    candidate = re.sub(r",\s*$", "", candidate)
    open_stack: list[str] = []
    in_string = False
    escape = False
    for ch in candidate:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            open_stack.append(ch)
        elif ch == "}":
            if open_stack and open_stack[-1] == "{":
                open_stack.pop()
        elif ch == "]" and open_stack and open_stack[-1] == "[":
            open_stack.pop()
    closers = {"{": "}", "[": "]"}
    suffix = "".join(closers[c] for c in reversed(open_stack))
    repaired = candidate + suffix
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        return None


def derive_project_hint(source_file: str) -> str:
    """Derive a project-name hint from the drawing's folder hierarchy.

    The sample-data layout organises projects as
    ``sample-data/<CUSTOMER> - <PROJECT>/<drawing>`` (e.g.
    ``sample-data/<PROJEKT>/2 SZAFA.png``). Drawings may sit in a
    nested subfolder (e.g. ``.../2026.01.21 PROJEKTY OSTATECZNE/ZM01_04.pdf``),
    so we walk UP the ancestors to find the first one matching the
    ``<X> - <Y>`` project marker and return the ``<Y>`` part.

    Returns "" when no meaningful hint can be derived.
    """
    try:
        # All ancestors from immediate parent up to the root, closest-first.
        ancestors = list(Path(source_file).parents)
    except Exception:
        return ""

    # 1. Prefer the first ancestor matching the "<CUSTOMER> - <PROJECT>" pattern.
    for parent in ancestors:
        name = parent.name
        if " - " in name:
            parts = name.split(" - ", 1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()

    # 2. Fall back to the first meaningful (non-data-root) ancestor.
    skip = {"sample-data", ".", "", "0"}
    for parent in ancestors:
        name = parent.name
        if name and name.lower() not in skip:
            return name

    return ""


def _apply_project_hint(result: ExtractionResult, source_file: str) -> None:
    """Use the source package's project marker as the canonical project name."""
    hint = derive_project_hint(source_file)
    if not hint:
        return
    result.project_name.value = hint
    result.project_name.confidence = ConfidenceLevel.HIGH
    result.project_name.source_region = None
    result.project_name.notes = "Canonical project name derived from source folder."


class DrawingPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.model_name = config.models.orchestrator
        self.api_base = config.api.base_url
        self.api_key = config.api.api_key
        self.context_window = config.pipeline.context_window
        self._langfuse = None
        self._current_span = None
        self._current_span_context = None
        self._instrumentation: InstrumentationSettings | bool = False

        # Create vision client if a separate vision model is configured.
        vision_model = config.models.vision
        if vision_model and vision_model != self.model_name:
            vision_api_base = config.models.vision_api_base or self.api_base
            self.vision_client = VisionClient(
                api_base=vision_api_base,
                model_name=vision_model,
                api_key=self.api_key,
            )
            logger.info(f"Vision model: {vision_model} via LM Studio at {vision_api_base}")
            if not self.vision_client.check_available_sync():
                logger.warning(
                    f"Vision model '{vision_model}' not available at {vision_api_base}. "
                    "Tools will fall back to raw images."
                )
                self.vision_client = None
        else:
            vision_api_base = None
            self.vision_client = None

        self.run_metadata = RunMetadata(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            orchestrator_model=config.models.orchestrator,
            vision_model=config.models.vision,
            api_base_url=config.api.base_url,
            pipeline_mode=config.pipeline.mode,
            validation_mode=config.pipeline.validation_mode,
            max_passes=config.pipeline.max_passes,
            confidence_threshold=config.pipeline.confidence_threshold,
            ocr_backend=config.ocr.backend,
            cli_args=config.cli_args,
        )

        # Initialize tracing if enabled
        if config.tracing.enabled and config.tracing.backend != "none":
            self._init_tracing(config.tracing)

        self.schema_overhead = measure_schema_overhead()
        logger.info(
            f"Pipeline overhead: ~{self.schema_overhead.total_fixed_tokens:,} tokens/req "
            f"(schema={self.schema_overhead.output_schema_tokens}, "
            f"tools={self.schema_overhead.tool_schemas_tokens}, "
            f"prompt={self.schema_overhead.system_prompt_tokens})"
        )

    def _init_tracing(self, tracing_config: TracingConfig) -> None:
        """Initialize Langfuse tracing with OTel instrumentation."""
        try:
            from pydantic_ai import Agent

            if tracing_config.backend == "langfuse":
                from langfuse import Langfuse

                self._langfuse = Langfuse(
                    should_export_span=lambda span: True,
                )
                logger.info("Tracing: Langfuse initialized")

            settings = tracing_instrumentation_settings(tracing_config)
            self._instrumentation = settings
            Agent.instrument_all(settings)
            logger.info(
                "Tracing: PydanticAI instrumentation enabled (content=%s, binary_content=%s)",
                settings.include_content,
                settings.include_binary_content,
            )
        except ImportError as e:
            logger.warning(f"Tracing dependencies not installed: {e}. Tracing disabled.")
        except Exception as e:
            logger.warning(f"Tracing initialization failed: {e}. Tracing disabled.")

    def _start_drawing_trace(self, filename: str, drawing_type: str) -> None:
        """Start a Langfuse trace named after the drawing file.

        The span must be the current OpenTelemetry context so PydanticAI agent
        observations become children instead of separate ``agent run`` traces.
        """
        if self._langfuse:
            self._current_span_context = self._langfuse.start_as_current_observation(
                name=filename,
                as_type="span",
                metadata={
                    "drawing.filename": filename,
                    "drawing.type": drawing_type,
                    "model.orchestrator": self.model_name,
                },
            )
            self._current_span = self._current_span_context.__enter__()

    def _finish_drawing_trace(self, error: BaseException | None = None) -> None:
        """Close the active drawing trace and flush telemetry."""
        if not self._langfuse:
            return

        try:
            if self._current_span_context:
                exc_type = type(error) if error else None
                traceback = error.__traceback__ if error else None
                self._current_span_context.__exit__(exc_type, error, traceback)
        finally:
            self._current_span_context = None
            self._current_span = None
            self._langfuse.flush()

    @staticmethod
    def _extract_tool_calls(messages: list) -> list[ToolCallRecord]:
        """Extract tool call records from agent messages."""
        from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart

        records: list[ToolCallRecord] = []
        pending_calls: dict[str, tuple[str, dict]] = {}

        for msg in messages:
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if isinstance(part, ToolCallPart):
                        tool_name = part.tool_name
                        args = part.args if isinstance(part.args, dict) else {}
                        # Sanitize: remove base64 data from args
                        sanitized = {
                            k: (v[:100] + "..." if isinstance(v, str) and len(v) > 100 else v) for k, v in args.items()
                        }
                        pending_calls[part.tool_call_id] = (tool_name, sanitized)
            elif hasattr(msg, "parts"):
                for part in msg.parts:
                    if isinstance(part, ToolReturnPart) and part.tool_call_id in pending_calls:
                        tool_name, args = pending_calls.pop(part.tool_call_id)
                        content_str = str(part.content)
                        result_summary = content_str[:200] + ("..." if len(content_str) > 200 else "")
                        records.append(
                            ToolCallRecord(
                                tool_name=tool_name,
                                args=args,
                                result_summary=result_summary,
                            )
                        )

        # Add any calls without returns
        for _tool_call_id, (tool_name, args) in pending_calls.items():
            records.append(ToolCallRecord(tool_name=tool_name, args=args))

        return records

    def discover_drawings(self, input_dir: str) -> list[Path]:
        root = Path(input_dir)
        if root.is_file() and root.suffix.lower() in DRAWING_EXTENSIONS:
            return [root]

        return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in DRAWING_EXTENSIONS)

    async def _extract_single(
        self,
        deps: DrawingDeps,
        drawing_type: DrawingType,
        filename: str,
        source_file: str,
        budget: TokenBudget,
        tool_calls: list[ToolCallRecord],
    ) -> tuple[ExtractionResult, TokenBudget]:
        """Inspect every page tile, then summarize the combined evidence to JSON."""
        from src.pipeline.agent import create_summarizing_agent

        logger.info("  Two-phase mode: starting forced tile explore -> summarize extraction")
        page_count = deps.loader.page_count
        project_hint = derive_project_hint(source_file)
        hint_line = f'Project hint (folder-derived): "{project_hint}". ' if project_hint else ""
        explore_agent = create_unified_agent(
            model_name=self.model_name,
            api_base=self.api_base,
            api_key=self.api_key,
            instructions=EXPLORATION_PROMPT,
            instrument=self._instrumentation,
        )
        evidence_sections: list[str] = []
        tile_evidence: list[str] = []
        tile_count = page_count * 9
        tile_number = 0

        for page_index in range(page_count):
            tiles = deps.loader.get_tiles(
                grid_size=3,
                overlap=self.config.document.tile_overlap,
                page_index=page_index,
            )
            for tile in sorted(tiles, key=lambda item: (item["row"], item["col"])):
                tile_number += 1
                row = tile["row"]
                col = tile["col"]
                label = tile["label"]
                coords = f"({tile['x_min']:.2f},{tile['y_min']:.2f})-({tile['x_max']:.2f},{tile['y_max']:.2f})"
                explore_prompt = (
                    f"Analyze this forced coverage tile from {filename}. Source file: {source_file}. "
                    f"Drawing type: {drawing_type.value}. {hint_line}"
                    f"Document pages: {page_count}. Coverage tile {tile_number}/{tile_count}: "
                    f"page_index={page_index}, row={row}, col={col}, label={label}, coords={coords}. "
                    "Inspect the attached tile. Report exact furniture cutting-list evidence visible "
                    "in this tile, including source coordinates. If it contains no relevant evidence, "
                    "say so explicitly. Use registered tools only for focused follow-up. "
                    "Return a compact plain-text evidence log, not JSON."
                )
                budget.log_text(f"explore_tile_{page_index}_{row}_{col}", explore_prompt)
                explore_result = await explore_agent.run(
                    user_prompt=[
                        explore_prompt,
                        BinaryContent(data=tile["image_bytes"], media_type=tile["media_type"]),
                    ],
                    deps=deps,
                    usage_limits=UsageLimits(
                        input_tokens_limit=budget.max_input_tokens,
                        request_limit=self.config.pipeline.max_iterations,
                    ),
                    model_settings={"max_tokens": 2048},
                )
                usage = explore_result.usage()
                budget.record_actual(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
                evidence = explore_result.output.strip()
                tile_evidence.append(evidence)
                evidence_sections.append(
                    f"=== TILE page_index={page_index} row={row} col={col} "
                    f"label={label} coords={coords} ===\n"
                    f"{evidence or 'No evidence returned.'}"
                )
                tool_calls.append(
                    ToolCallRecord(
                        tool_name="forced_tile_coverage",
                        args={"page_index": page_index, "row": row, "col": col},
                        result_summary=(f"Inspected {label} at {coords}; evidence_chars={len(evidence)}"),
                    )
                )
                tool_calls.extend(self._extract_tool_calls(explore_result.all_messages()))

        evidence_log = "\n\n".join(evidence_sections)
        logger.info(
            "  Phase 1 (explore): %s chars, %s/%s forced tiles, %s total tool records",
            len(evidence_log),
            len(tile_evidence),
            tile_count,
            len(tool_calls),
        )
        if not any(tile_evidence):
            raise ValueError("Exploration produced no useful evidence")

        summarize_agent = create_summarizing_agent(
            model_name=self.model_name,
            api_base=self.api_base,
            api_key=self.api_key,
            instructions=SUMMARIZE_PROMPT,
            instrument=self._instrumentation,
        )
        summarize_prompt = (
            "Convert the following exploration findings into the JSON ExtractionResult.\n"
            f"Filename: {filename}\nDrawing type: {drawing_type.value}\n{hint_line}\n"
            f"project_name hint: use '{project_hint}' if findings do not state one.\n\n"
            f"=== EXPLORATION FINDINGS ===\n{evidence_log}\n=== END FINDINGS ===\n\n"
            "Respond with ONLY the JSON object."
        )
        summarize_result = await summarize_agent.run(
            user_prompt=summarize_prompt,
            usage_limits=UsageLimits(input_tokens_limit=budget.max_input_tokens, request_limit=3),
            model_settings={"max_tokens": 16384},
        )
        usage = summarize_result.usage()
        budget.record_actual(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        logger.info(
            "  Phase 2 (summarize): %s tokens",
            usage.input_tokens + usage.output_tokens,
        )
        raw_output = summarize_result.output.strip()
        if raw_output.startswith("```"):
            raw_output = re.sub(r"^```\w*\n?", "", raw_output)
            raw_output = re.sub(r"\n?```$", "", raw_output)
            raw_output = raw_output.strip()
        try:
            try:
                data = json.loads(raw_output)
            except json.JSONDecodeError as error:
                recovered = _repair_truncated_json(raw_output)
                if recovered is not None:
                    logger.warning(
                        "  Summarize: JSON truncated (%s); recovered %s/%s chars",
                        error,
                        len(recovered),
                        len(raw_output),
                    )
                    data = json.loads(recovered)
                else:
                    raise
            if isinstance(data, dict):
                data.setdefault("source_file", source_file)
                data.setdefault("drawing_type", drawing_type.value)
            extraction_result = ExtractionResult.model_validate(data)
            logger.info(
                "  Two-phase: produced ExtractionResult (%s chars JSON), %s tool records",
                len(raw_output),
                len(tool_calls),
            )
            return extraction_result, budget
        except (json.JSONDecodeError, Exception) as error:
            logger.warning(
                "  Two-phase: JSON parse failed (%s), raw: %s...",
                error,
                raw_output[:200],
            )
            raise ValueError(f"Two-phase JSON parse failed: {error}") from error

    def _identify_validation_targets(self, result: ExtractionResult) -> list[str]:
        """Identify fields that need re-verification."""
        CRITICAL_FIELDS = [
            "project_name",
            "client",
            "cabinet_id",
            "scale",
            "units",
        ]
        IMPORTANT_FIELDS: list[str] = []

        targets = []
        for name in CRITICAL_FIELDS + IMPORTANT_FIELDS:
            field = getattr(result, name, None)
            if field is None:
                continue
            if (
                (name in CRITICAL_FIELDS and (field.value is None or field.value == ""))
                or (field.value and field.confidence.value == "LOW")
                or (field.value and ("also " in field.value.lower() or " vs " in field.value.lower()))
            ):
                targets.append(name)

        return targets

    async def _validate(
        self,
        result: ExtractionResult,
        deps: DrawingDeps,
        budget: TokenBudget,
        max_iterations: int | None = None,
    ) -> tuple[ExtractionResult, TokenBudget]:
        """Phase 3: Lightweight validation — re-verify low-confidence/missing fields.

        Uses diff-based approach: returns only changed fields instead of full replacement.
        """
        if max_iterations is None:
            max_iterations = self.config.pipeline.validation_max_iterations

        fields_to_check = self._identify_validation_targets(result)

        # "always-critical" mode: always verify the 7 most important fields
        if getattr(self.config.pipeline, "validation_mode", "confidence-based") == "always-critical":
            CRITICAL_FIELDS = [
                "project_name",
                "client",
                "cabinet_id",
                "scale",
                "units",
            ]
            for name in CRITICAL_FIELDS:
                if name not in fields_to_check:
                    fields_to_check.append(name)

        if not fields_to_check:
            logger.info("  Validation: no low-confidence fields, skipping")
            return result, budget

        logger.info(f"  Validation: checking fields: {', '.join(fields_to_check)}")

        agent = create_validation_agent(
            model_name=self.model_name,
            api_base=self.api_base,
            api_key=self.api_key,
            instructions=VALIDATION_DIFF_PROMPT,
            instrument=self._instrumentation,
        )

        # Build a compact summary of only the fields to verify (not full JSON)
        fields_summary_lines = []
        for name in fields_to_check:
            field = getattr(result, name, None)
            if field:
                fields_summary_lines.append(
                    f"- {name}: value={field.value!r}, confidence={field.confidence.value}, "
                    f"source_region={field.source_region!r}"
                )
            else:
                fields_summary_lines.append(f"- {name}: (missing)")
        fields_summary = "\n".join(fields_summary_lines)

        user_prompt = (
            f"Source file: {deps.filename}\n"
            f"Document pages: {deps.loader.page_count}. Use zero-based page_index with "
            "visual tools and re-inspect the page containing each field. For multi-page "
            "documents, do not call view_overview during validation; use focused tools "
            "such as read_title_block, which return compact text evidence.\n"
            f"Fields to verify ({len(fields_to_check)}):\n{fields_summary}\n\n"
            "Re-inspect the drawing using tools to verify these values. "
            "Return ONLY changed fields as a JSON diff, or {} if no changes needed."
        )

        usage_limits = UsageLimits(
            input_tokens_limit=budget.max_input_tokens,
            request_limit=max_iterations,
        )

        try:
            validation_result = await agent.run(
                user_prompt=user_prompt,
                deps=deps,
                usage_limits=usage_limits,
                model_settings={"max_tokens": 2048},
            )
            usage = validation_result.usage()
            budget.record_actual(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )

            # Parse diff and merge into result
            diff_text = validation_result.output.strip()
            if diff_text.startswith("```"):
                import re

                diff_text = re.sub(r"^```\w*\n?", "", diff_text)
                diff_text = re.sub(r"\n?```$", "", diff_text)
                diff_text = diff_text.strip()

            import json

            try:
                diff_data = json.loads(diff_text)
                if isinstance(diff_data, dict):
                    diffs = merge_diff(result, diff_data)
                    logger.info(f"  Validation: merged {len(diffs)} field corrections")
                else:
                    logger.warning("  Validation: diff was not a dict, skipping merge")
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"  Validation: diff parse failed ({e}), keeping original result")

            logger.info("  Validation: completed re-verification")
            return result, budget
        except Exception as e:
            logger.warning(f"  Validation phase failed: {e}")
            return result, budget

    def _process_p3d(self, path: Path) -> tuple[ExtractionResult, DrawingMetrics]:
        """Record a proprietary Palette CAD source for explicit export review."""
        message = "P3D requires a Palette CAD export as PDF, DWG, or DXF before panel extraction."
        logger.warning("Skipping direct P3D extraction for %s: %s", path.name, message)
        result = ExtractionResult(
            source_file=str(path),
            drawing_type=DrawingType.CAD,
            extraction_timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            model_used="not-processed",
            run_metadata=self.run_metadata,
            needs_review=True,
            review_fields=["source_format"],
            unresolvable_fields=[message],
            notes=message,
        )
        _apply_project_hint(result, str(path))
        return result, DrawingMetrics(
            filename=path.name,
            drawing_type=DrawingType.CAD.value,
            context_window=self.context_window or 0,
        )

    async def process_drawing(
        self,
        drawing_path: str,
        mode: str | None = None,
    ) -> tuple[ExtractionResult, DrawingMetrics]:
        """Process a supported drawing or record a P3D export requirement.

        DWG files are rasterized locally before entering the normal extraction
        flow. P3D is a proprietary Palette CAD container without an available
        reader, so it is retained as an explicit review result.
        """
        path = Path(drawing_path)
        if path.suffix.lower() == ".p3d":
            return self._process_p3d(path)

        loader = make_loader(str(path), self.config.document)
        filename = path.name
        drawing_type = classify_drawing(filename)

        if self._langfuse:
            self._start_drawing_trace(filename, drawing_type.value)

        trace_error: BaseException | None = None
        try:
            logger.info(f"Processing: {filename} (type: {drawing_type.value})")

            if mode is not None:
                logger.info(f"  (mode arg '{mode}' ignored — the pipeline flow is fixed)")

            cw = self.context_window
            metrics = DrawingMetrics(
                filename=filename,
                drawing_type=drawing_type.value,
                context_window=cw or 0,
            )

            deps = DrawingDeps(
                loader=loader,
                drawing_path=str(path),
                filename=filename,
                vision_client=self.vision_client,
                tool_cache=ToolCache(max_size=100),  # shared cache
            )

            # Global token budget across all phases
            global_budget = TokenBudget(self.model_name, cw, pass_name="global")

            result, metrics = await self._process_single_agent(
                deps,
                drawing_type,
                filename,
                str(path),
                cw,
                metrics,
                global_budget,
            )
            _apply_project_hint(result, str(path))
            return result, metrics
        except BaseException as e:
            trace_error = e
            raise
        finally:
            if self._langfuse:
                self._finish_drawing_trace(trace_error)

    async def _process_single_agent(
        self,
        deps: DrawingDeps,
        drawing_type: DrawingType,
        filename: str,
        source_file: str,
        cw: int | None,
        metrics: DrawingMetrics,
        global_budget: TokenBudget,
    ) -> tuple[ExtractionResult, DrawingMetrics]:
        """Run explore → summarize → validate, with a simple extraction fallback.

        The exploration and validation calls use tool-capable text agents; the
        summarizer converts the evidence log into the structured result.
        """
        logger.info("  Mode: explore_summarize_validate")

        # Phase 1: deterministic tile exploration and summarization
        budget = TokenBudget(self.model_name, cw, pass_name="extraction")
        tool_calls: list[ToolCallRecord] = []
        try:
            result, budget = await self._extract_single(
                deps,
                drawing_type,
                filename,
                source_file,
                budget,
                tool_calls,
            )
            result.model_used = self.model_name
            result.extraction_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            metrics.passes.append(budget.to_metrics())
            metrics.tool_calls.extend(tool_calls)
        except ContextOverflowError as e:
            logger.error(f"  Main extraction: context overflow ({e})")
            metrics.passes.append(budget.to_metrics())
            metrics.tool_calls.extend(tool_calls)
            return await self._fallback_to_simple(
                deps,
                drawing_type,
                filename,
                source_file,
                metrics,
            )
        except Exception as e:
            logger.error(f"  Main extraction: extraction failed ({e})")
            metrics.passes.append(budget.to_metrics())
            metrics.tool_calls.extend(tool_calls)
            return await self._fallback_to_simple(
                deps,
                drawing_type,
                filename,
                source_file,
                metrics,
            )

        # Global budget tracking
        global_budget.record_actual(
            input_tokens=budget.actual_input_tokens,
            output_tokens=budget.actual_output_tokens,
        )

        # Smart validation: always run on critical fields, not just low-confidence
        validation_budget = TokenBudget(self.model_name, cw, pass_name="validation")
        try:
            result, validation_budget = await self._validate(
                result,
                deps,
                validation_budget,
            )
            metrics.passes.append(validation_budget.to_metrics())
            global_budget.record_actual(
                input_tokens=validation_budget.actual_input_tokens,
                output_tokens=validation_budget.actual_output_tokens,
            )
        except Exception as e:
            logger.warning(f"  Validation failed: {e}")
            metrics.passes.append(validation_budget.to_metrics())

        passes_ok = sum(1 for p in metrics.passes if p.total_input_tokens > 0)
        result.passes_completed = passes_ok

        total_tokens = sum(p.total_input_tokens + p.total_output_tokens for p in metrics.passes)
        metrics.total_tokens = total_tokens

        logger.info(f"  Drawing total: {total_tokens:,} tokens (mode: explore_summarize_validate)")

        self._finalize_result(result, metrics)
        return result, metrics

    def _finalize_result(self, result: ExtractionResult, metrics: DrawingMetrics) -> None:
        """Apply the same deterministic checks to primary and fallback results."""
        warnings = validate_result(result)
        if warnings:
            logger.info(f"  Validation warnings: {'; '.join(warnings)}")

        corrections = normalize_values(result)
        if corrections:
            logger.info(f"  Value corrections: {'; '.join(corrections)}")

        geometry_warnings = reconcile_panel_geometry(result)
        if geometry_warnings:
            logger.info(f"  Geometry warnings: {'; '.join(geometry_warnings)}")

        cross_warnings = cross_validate_fields(result)
        if cross_warnings:
            logger.info(f"  Cross-field warnings: {'; '.join(cross_warnings)}")

        result.review_fields = [
            name
            for name in SCALAR_FIELDS
            if (field := getattr(result, name, None)) and field.confidence == ConfidenceLevel.LOW
        ]
        if (
            any("panel" in warning.lower() for warning in [*warnings, *geometry_warnings, *cross_warnings])
            and "panels" not in result.review_fields
        ):
            result.review_fields.append("panels")
        result.needs_review = bool(warnings or geometry_warnings or cross_warnings)
        result.tokens_used = metrics.total_tokens

        if metrics.tool_calls:
            result.tool_call_log = [
                {"tool": tc.tool_name, "args": tc.args, "result_summary": tc.result_summary}
                for tc in metrics.tool_calls
            ]

    async def _fallback_to_simple(
        self,
        deps: DrawingDeps,
        drawing_type: DrawingType,
        filename: str,
        source_file: str,
        metrics: DrawingMetrics,
    ) -> tuple[ExtractionResult, DrawingMetrics]:
        """Last-resort fallback: use the two-call direct extractor.

        Called when the main extraction flow fails (context overflow or error).
        Uses simple_extractor which has no agent loop — just 2 API calls.
        """
        from src.pipeline.simple_extractor import extract_drawing

        logger.warning("  Falling back to simple extractor (no agent loop)")
        try:
            result, simple_metrics = await extract_drawing(
                loader=deps.loader,
                api_base=self.api_base,
                model_name=self.model_name,
                api_key=self.api_key,
            )
            result.source_file = source_file
            result.drawing_type = drawing_type
            result.model_used = self.model_name
            result.extraction_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            result.run_metadata = self.run_metadata

            budget = TokenBudget(self.model_name, None, pass_name="simple")
            budget.record_actual(
                input_tokens=simple_metrics.get("total_tokens", 0),
                output_tokens=0,
            )
            metrics.passes.append(budget.to_metrics())
            metrics.total_tokens = sum(
                pass_metrics.total_input_tokens + pass_metrics.total_output_tokens for pass_metrics in metrics.passes
            )
            result.tokens_used = metrics.total_tokens
            result.passes_completed = 1

            self._finalize_result(result, metrics)

            logger.info(
                f"  Simple extractor: {metrics.total_tokens:,} tokens, {simple_metrics.get('fields_extracted', 0)} fields"
            )
            return result, metrics
        except Exception as e:
            logger.error(f"  Simple extractor also failed: {e}")
            result = ExtractionResult(
                source_file=source_file,
                drawing_type=drawing_type,
                extraction_timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                model_used=self.model_name,
                run_metadata=self.run_metadata,
                needs_review=True,
                review_fields=["extraction"],
                unresolvable_fields=["Primary and fallback extraction both failed."],
                notes="Primary and fallback extraction both failed; no panels were produced.",
            )
            if metrics.tool_calls:
                result.tool_call_log = [
                    {"tool": tc.tool_name, "args": tc.args, "result_summary": tc.result_summary}
                    for tc in metrics.tool_calls
                ]
            return result, metrics

    async def process_directory(
        self,
        input_dir: str | None = None,
    ) -> tuple[list[ExtractionResult], list[DrawingMetrics]]:
        drawings = self.discover_drawings(input_dir or self.config.input_dir)
        logger.info(f"Found {len(drawings)} drawings in {input_dir or self.config.input_dir}")

        results: list[ExtractionResult] = []
        all_metrics: list[DrawingMetrics] = []
        for i, drawing in enumerate(drawings, 1):
            logger.info(f"[{i}/{len(drawings)}] {drawing.name}")
            try:
                result, metrics = await self.process_drawing(str(drawing))
                results.append(result)
                all_metrics.append(metrics)
            except Exception as e:
                logger.error(f"Failed to process {drawing.name}: {e}")

        if self._langfuse:
            self._langfuse.flush()

        return results, all_metrics
