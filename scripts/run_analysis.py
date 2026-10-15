#!/usr/bin/env python3
"""Run a chosen extraction method/model over all drawings and summarize outputs.

Usage examples:
    uv run python scripts/run_analysis.py --method pipeline \
      --orchestrator qwen/qwen3-vl-8b --vision qwen/qwen3-vl-8b \
      --api-base http://localhost:1234/v1

    uv run python scripts/run_analysis.py --method simple --output results/full_simple

    uv run python scripts/run_analysis.py --method step-by-step --ocr olmocr
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import AppConfig  # noqa: E402
from src.output.comparison import COMPARISON_FIELDS, write_review_report  # noqa: E402
from src.output.writer import write_json, write_token_report, write_xlsx  # noqa: E402
from src.pipeline.agent import make_loader  # noqa: E402
from src.pipeline.orchestrator import DrawingPipeline  # noqa: E402
from src.schema.extraction import ExtractionResult, RunMetadata  # noqa: E402

DRAWING_EXTENSIONS = {".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".dwg", ".p3d"}


@dataclass
class RunStats:
    source_file: str
    ok: bool
    elapsed_s: float
    fields_filled: int = 0
    high_confidence: int = 0
    tokens: int = 0
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one extraction method/model over the full dataset and write summary reports.",
    )
    parser.add_argument("--config", "-c", default="config/default.yaml", help="Config YAML path")
    parser.add_argument("--input", "-i", default="sample-data", help="Input drawing directory")
    parser.add_argument("--output", "-o", default=None, help="Output directory; defaults to results/full_run_<stamp>")
    parser.add_argument(
        "--method",
        choices=["pipeline", "simple", "step-by-step"],
        default="pipeline",
        help="Extraction method to run",
    )
    parser.add_argument("--api-base", help="Override OpenAI-compatible API base URL")
    parser.add_argument("--orchestrator", help="Override orchestrator model")
    parser.add_argument("--vision", help="Override vision model")
    parser.add_argument("--ocr", choices=["vlm", "olmocr", "glm-ocr"], help="OCR backend for step-by-step")
    parser.add_argument("--limit", type=int, help="Process only the first N drawings")
    parser.add_argument("--continue-on-error", action="store_true", default=True, help="Keep running after failures")
    parser.add_argument(
        "--stop-on-error", action="store_false", dest="continue_on_error", help="Stop after first failure"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose pipeline logging")
    return parser.parse_args()


def safe_label(value: str) -> str:
    return value.replace("/", "_").replace(" ", "_").replace(":", "_")


def find_drawings(input_dir: Path) -> list[Path]:
    if input_dir.is_file():
        return [input_dir] if input_dir.suffix.lower() in DRAWING_EXTENSIONS else []

    drawings = [
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in DRAWING_EXTENSIONS and not path.name.startswith(".")
    ]
    return sorted(drawings, key=lambda p: str(p.relative_to(input_dir)).lower())


def build_config(args: argparse.Namespace) -> AppConfig:
    overrides: dict[str, object] = {
        "input_dir": str(
            (PROJECT_ROOT / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
        ),
        "cli_args": vars(args),
    }
    if args.output:
        output_path = Path(args.output)
        overrides["output.dir"] = str(output_path if output_path.is_absolute() else PROJECT_ROOT / output_path)
    if args.api_base:
        overrides["api.base_url"] = args.api_base
    if args.orchestrator:
        overrides["models.orchestrator"] = args.orchestrator
    if args.vision:
        overrides["models.vision"] = args.vision

    config = AppConfig.from_yaml(args.config, **overrides)
    if args.method == "pipeline":
        config.pipeline.mode = "single"
    if args.ocr:
        config.ocr.backend = args.ocr
    return config


def default_output_dir(args: argparse.Namespace, config: AppConfig) -> Path:
    if args.output:
        out = Path(args.output)
        return out if out.is_absolute() else PROJECT_ROOT / out

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model = safe_label(args.orchestrator or config.models.orchestrator)
    method = safe_label(args.method)
    return PROJECT_ROOT / "results" / f"full_run_{stamp}_{method}_{model}"


def make_run_metadata(config: AppConfig) -> RunMetadata:
    return RunMetadata(
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


async def run_simple_method(drawing: Path, config: AppConfig) -> tuple[ExtractionResult, dict[str, Any]]:
    from src.pipeline.simple_extractor import extract_drawing

    loader = make_loader(str(drawing), config.document)
    return await extract_drawing(
        loader=loader,
        api_base=config.api.base_url,
        model_name=config.models.orchestrator,
        api_key=config.api.api_key,
        tile_overlap=config.document.tile_overlap,
        run_metadata=make_run_metadata(config),
    )


async def run_step_by_step_method(drawing: Path, config: AppConfig) -> tuple[ExtractionResult, dict[str, Any]]:
    from src.ocr.factory import create_ocr_backend
    from src.pipeline.step_by_step import extract_drawing_step_by_step

    loader = make_loader(str(drawing), config.document)
    ocr_backend = create_ocr_backend(config)
    return await extract_drawing_step_by_step(
        loader=loader,
        ocr_backend=ocr_backend,
        tile_overlap=config.document.tile_overlap,
    )


async def process_drawing(
    drawing: Path,
    method: str,
    pipeline: DrawingPipeline | None,
    config: AppConfig,
) -> tuple[ExtractionResult, Any]:
    if method == "pipeline":
        if pipeline is None:
            raise RuntimeError("Pipeline is required for agent methods")
        return await pipeline.process_drawing(str(drawing))
    if method == "simple":
        return await run_simple_method(drawing, config)
    if method == "step-by-step":
        return await run_step_by_step_method(drawing, config)
    raise ValueError(f"Unsupported method: {method}")


def field_value(result: ExtractionResult, field_name: str) -> str | None:
    field = getattr(result, field_name, None)
    return field.value if field and field.value not in ("", None) else None


def count_fields(result: ExtractionResult) -> int:
    return sum(1 for field_name in COMPARISON_FIELDS if field_value(result, field_name))


def count_high_confidence(result: ExtractionResult) -> int:
    return sum(
        1
        for field_name in COMPARISON_FIELDS
        if (field := getattr(result, field_name, None)) and field.confidence.value == "HIGH"
    )


def result_tokens(result: ExtractionResult, metrics: Any) -> int:
    if result.tokens_used:
        return result.tokens_used
    if isinstance(metrics, dict):
        return int(metrics.get("total_tokens") or 0)
    return int(getattr(metrics, "total_tokens", 0) or 0)


def format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_markdown_summary(
    output_dir: Path,
    args: argparse.Namespace,
    config: AppConfig,
    drawings: list[Path],
    results: list[ExtractionResult],
    stats: list[RunStats],
    started_at: datetime,
    elapsed_s: float,
) -> Path:
    path = output_dir / "summary.md"
    successes = [s for s in stats if s.ok]
    failures = [s for s in stats if not s.ok]
    total_tokens = sum(s.tokens for s in successes)
    total_possible = len(successes) * len(COMPARISON_FIELDS)
    total_filled = sum(s.fields_filled for s in successes)
    total_high = sum(s.high_confidence for s in successes)
    needs_review = [r for r in results if r.needs_review]

    by_type = Counter(r.drawing_type.value for r in results)
    field_counts: dict[str, int] = {}
    confidence_counts: dict[str, Counter[str]] = {}
    for field_name in COMPARISON_FIELDS:
        field_counts[field_name] = sum(1 for r in results if field_value(r, field_name))
        confidence_counts[field_name] = Counter(
            getattr(r, field_name).confidence.value for r in results if getattr(r, field_name, None)
        )

    lines = [
        "# Full Dataset Extraction Summary",
        "",
        f"**Date**: {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Method**: `{args.method}`",
        f"**Orchestrator**: `{config.models.orchestrator}`",
        f"**Vision**: `{config.models.vision}`",
        f"**OCR backend**: `{config.ocr.backend}`",
        f"**API base**: `{config.api.base_url}`",
        f"**Input**: `{config.input_dir}`",
        f"**Output**: `{output_dir}`",
        "",
        "## Overview",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Drawings discovered | {len(drawings)} |",
        f"| Drawings processed successfully | {len(successes)} |",
        f"| Failures | {len(failures)} |",
        f"| Elapsed time | {elapsed_s / 60:.1f} min |",
        f"| Total tokens | {total_tokens:,} |",
        f"| Avg tokens / successful drawing | {total_tokens / len(successes):,.0f} |"
        if successes
        else "| Avg tokens / successful drawing | 0 |",
        f"| Field fill rate | {format_pct(total_filled / total_possible) if total_possible else '0.0%'} |",
        f"| HIGH-confidence field rate | {format_pct(total_high / total_possible) if total_possible else '0.0%'} |",
        f"| Drawings needing review | {len(needs_review)} |",
        "",
        "## Drawing Types",
        "",
        "| Drawing Type | Count |",
        "|---|---:|",
    ]

    for drawing_type, count in sorted(by_type.items()):
        lines.append(f"| {drawing_type} | {count} |")

    lines.extend(
        [
            "",
            "## Per-Drawing Results",
            "",
            "| Drawing | Type | Fields | HIGH | Review | Tokens | Time (s) |",
            "|---|---|---:|---:|---|---:|---:|",
        ]
    )
    stats_by_file = {Path(s.source_file).name: s for s in stats}
    for result in sorted(results, key=lambda r: Path(r.source_file).name):
        s = stats_by_file.get(Path(result.source_file).name)
        lines.append(
            "| "
            + " | ".join(
                [
                    Path(result.source_file).name,
                    result.drawing_type.value,
                    f"{count_fields(result)}/{len(COMPARISON_FIELDS)}",
                    str(count_high_confidence(result)),
                    "YES" if result.needs_review else "NO",
                    f"{(s.tokens if s else result.tokens_used):,}",
                    f"{s.elapsed_s:.1f}" if s else "",
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Field Coverage",
            "",
            "| Field | Filled | Fill Rate | HIGH | MEDIUM | LOW |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for field_name in COMPARISON_FIELDS:
        conf = confidence_counts[field_name]
        filled = field_counts[field_name]
        rate = filled / len(results) if results else 0
        lines.append(
            f"| `{field_name}` | {filled}/{len(results)} | {format_pct(rate)} | "
            f"{conf.get('HIGH', 0)} | {conf.get('MEDIUM', 0)} | {conf.get('LOW', 0)} |"
        )

    if failures:
        lines.extend(["", "## Failures", "", "| Drawing | Error |", "|---|---|"])
        for failure in failures:
            lines.append(f"| {Path(failure.source_file).name} | {failure.error} |")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Field coverage measures whether the pipeline produced a non-empty value, not ground-truth accuracy.",
            "- Use `uv run python -m src.evaluation.accuracy --results-dir <output-dir>` when matching ground truth exists for this dataset.",
            "- XLSX outputs in this folder contain the raw extraction table, review report, and token report when token metrics are available.",
            "",
        ]
    )

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def main() -> None:
    args = parse_args()

    import logging

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = build_config(args)
    output_dir = default_output_dir(args, config)
    config.output.dir = str(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_path = Path(config.input_dir)
    drawings = find_drawings(input_path)
    if args.limit:
        drawings = drawings[: args.limit]

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Full Dataset Extraction")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Method:       {args.method}")
    print(f"Orchestrator: {config.models.orchestrator}")
    print(f"Vision:       {config.models.vision}")
    print(f"OCR backend:  {config.ocr.backend}")
    print(f"Input:        {input_path}")
    print(f"Output:       {output_dir}")
    print(f"Drawings:     {len(drawings)}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if not drawings:
        print("No drawings found.")
        return

    started_at = datetime.now()
    run_started = time.perf_counter()
    pipeline = DrawingPipeline(config) if args.method == "pipeline" else None
    results: list[ExtractionResult] = []
    metrics_list: list[Any] = []
    stats: list[RunStats] = []

    for idx, drawing in enumerate(drawings, start=1):
        print(f"\n[{idx}/{len(drawings)}] {drawing.name}")
        one_started = time.perf_counter()
        try:
            result, metrics = await process_drawing(drawing, args.method, pipeline, config)
            elapsed = time.perf_counter() - one_started
            tokens = result_tokens(result, metrics)
            if not result.tokens_used and tokens:
                result.tokens_used = tokens
            results.append(result)
            metrics_list.append(metrics)
            json_path = write_json(result, str(output_dir))
            stats.append(
                RunStats(
                    source_file=result.source_file,
                    ok=True,
                    elapsed_s=elapsed,
                    fields_filled=count_fields(result),
                    high_confidence=count_high_confidence(result),
                    tokens=tokens,
                )
            )
            print(
                f"  ok: {count_fields(result)}/{len(COMPARISON_FIELDS)} fields, {tokens:,} tokens -> {json_path.name}"
            )
        except Exception as exc:
            elapsed = time.perf_counter() - one_started
            stats.append(RunStats(source_file=str(drawing), ok=False, elapsed_s=elapsed, error=str(exc)))
            print(f"  FAILED: {exc}")
            if not args.continue_on_error:
                raise

    elapsed_s = time.perf_counter() - run_started
    if not results:
        print("\nNo drawings processed successfully.")
        return

    xlsx_path = write_xlsx(results, str(output_dir / "extraction_results.xlsx"))
    review_path = write_review_report(results, str(output_dir / "review_needed.xlsx"))

    token_path = None
    drawing_metrics = [m for m in metrics_list if hasattr(m, "passes")]
    if drawing_metrics:
        schema_overhead = getattr(pipeline, "schema_overhead", None)
        token_path = write_token_report(drawing_metrics, str(output_dir / "token_report.xlsx"), schema_overhead)

    summary_path = write_markdown_summary(output_dir, args, config, drawings, results, stats, started_at, elapsed_s)

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Reports")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Summary:       {summary_path}")
    print(f"Results XLSX:  {xlsx_path}")
    print(f"Review XLSX:   {review_path}")
    if token_path:
        print(f"Token XLSX:    {token_path}")
    print(f"JSON files:    {output_dir}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    asyncio.run(main())
