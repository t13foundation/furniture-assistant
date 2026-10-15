"""OCR backend comparator — runs step-by-step extraction with multiple backends."""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import AppConfig

from src.ocr.factory import create_ocr_backend
from src.pipeline.agent import make_loader
from src.pipeline.step_by_step import extract_drawing_step_by_step
from src.schema.enums import DrawingType
from src.schema.extraction import ExtractionResult

logger = logging.getLogger(__name__)

ALL_BACKENDS = ["vlm", "olmocr", "glm-ocr"]

COMPARISON_FIELDS = [
    "project_name",
    "client",
    "cabinet_id",
    "scale",
    "units",
]


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped.lower() if stripped else None


@dataclass
class BackendResult:
    backend_name: str
    result: ExtractionResult
    metrics: dict = field(default_factory=dict)


@dataclass
class FieldAgreement:
    field_name: str
    values: dict[str, str | None] = field(default_factory=dict)
    normalized: dict[str, str | None] = field(default_factory=dict)
    agree_count: int = 0
    disagree_count: int = 0
    missing_count: int = 0
    total_backends: int = 0

    @property
    def agreement_ratio(self) -> float:
        non_null = self.total_backends - self.missing_count
        if non_null <= 1:
            return 0.0
        return self.agree_count / non_null


@dataclass
class ComparisonResult:
    drawing_name: str
    drawing_type: DrawingType
    backends: list[BackendResult] = field(default_factory=list)
    field_agreements: list[FieldAgreement] = field(default_factory=list)
    timing: dict[str, float] = field(default_factory=dict)


class OCRComparator:
    def __init__(self, config: AppConfig, backends: list[str] | None = None) -> None:
        self.config = config
        self.backends = backends or ALL_BACKENDS

    async def compare_drawing(self, drawing_path: str) -> ComparisonResult:
        path = Path(drawing_path)
        from src.schema.enums import classify_drawing

        drawing_type = classify_drawing(path.name)
        comp = ComparisonResult(
            drawing_name=path.name,
            drawing_type=drawing_type,
        )

        logger.info(f"=== Comparing {len(self.backends)} backends on {path.name} (type={drawing_type.value}) ===")

        for i, backend_name in enumerate(self.backends, 1):
            logger.info(f"[{i}/{len(self.backends)}] Starting backend: {backend_name}")

            cfg = copy.deepcopy(self.config)
            cfg.ocr.backend = backend_name

            try:
                ocr_backend = create_ocr_backend(cfg)
                loader = make_loader(str(path), cfg.document)
                logger.debug(f"  {backend_name}: backend and loader created")
            except Exception as e:
                logger.error(f"  {backend_name}: setup failed — {e}")
                continue

            start = time.time()
            try:
                result, metrics = await extract_drawing_step_by_step(
                    loader=loader,
                    ocr_backend=ocr_backend,
                )
            except Exception as e:
                elapsed = time.time() - start
                comp.timing[backend_name] = elapsed
                logger.error(f"  {backend_name}: extraction failed after {elapsed:.1f}s — {e}")
                continue

            elapsed = time.time() - start
            comp.timing[backend_name] = elapsed
            comp.backends.append(
                BackendResult(
                    backend_name=backend_name,
                    result=result,
                    metrics=metrics,
                )
            )

            tokens_per_sec = metrics["total_tokens"] / elapsed if elapsed > 0 else 0
            logger.info(
                f"  {backend_name} done: {metrics['fields_extracted']} fields, "
                f"{metrics['total_tokens']:,} tokens, {elapsed:.1f}s "
                f"({tokens_per_sec:.0f} tok/s), "
                f"{metrics['tiles_processed']}/9 tiles, "
                f"review={'YES' if result.needs_review else 'no'}"
            )

            for field_name in COMPARISON_FIELDS:
                f = getattr(result, field_name, None)
                if f and f.value:
                    logger.debug(f"    {field_name}: {f.value!r} [{f.confidence.value}]")

        comp.field_agreements = _compute_agreements(comp.backends)
        _log_agreement_summary(comp)
        return comp

    async def compare_all(self, input_dir: str) -> list[ComparisonResult]:
        input_path = Path(input_dir)
        if input_path.is_file():
            paths = [input_path]
        else:
            paths = sorted(
                p for ext in (".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg") for p in input_path.rglob(f"*{ext}")
            )

        if not paths:
            logger.warning("No drawings found.")
            return []

        logger.info(
            f"Starting OCR comparison: {len(paths)} drawings x {len(self.backends)} backends "
            f"({', '.join(self.backends)})"
        )
        start_all = time.time()

        results = []
        for i, p in enumerate(paths, 1):
            logger.info(f"--- Drawing {i}/{len(paths)}: {p.name} ---")
            comp = await self.compare_drawing(str(p))
            results.append(comp)

        total_elapsed = time.time() - start_all
        _log_batch_summary(results, total_elapsed)
        return results


def _compute_agreements(backends: list[BackendResult]) -> list[FieldAgreement]:
    if len(backends) < 2:
        return []

    agreements = []
    for field_name in COMPARISON_FIELDS:
        values: dict[str, str | None] = {}
        normalized: dict[str, str | None] = {}

        for br in backends:
            f = getattr(br.result, field_name, None)
            raw = f.value if f else None
            values[br.backend_name] = raw
            normalized[br.backend_name] = _normalize(raw)

        non_null_values = [v for v in normalized.values() if v is not None]
        unique_non_null = set(non_null_values)

        agree_count = 0
        if len(unique_non_null) == 1 and len(non_null_values) >= 2:
            agree_count = len(non_null_values)
        elif len(unique_non_null) > 1:
            most_common = max(unique_non_null, key=lambda v: non_null_values.count(v))
            agree_count = non_null_values.count(most_common)

        missing_count = sum(1 for v in normalized.values() if v is None)
        disagree_count = len(non_null_values) - agree_count

        agreements.append(
            FieldAgreement(
                field_name=field_name,
                values=values,
                normalized=normalized,
                agree_count=agree_count,
                disagree_count=disagree_count,
                missing_count=missing_count,
                total_backends=len(backends),
            )
        )

    return agreements


def _log_agreement_summary(comp: ComparisonResult) -> None:
    if not comp.field_agreements:
        logger.info("  No agreement analysis (< 2 backends succeeded)")
        return

    full = sum(1 for a in comp.field_agreements if a.agreement_ratio >= 0.8)
    partial = sum(1 for a in comp.field_agreements if 0.5 <= a.agreement_ratio < 0.8)
    disagree = sum(1 for a in comp.field_agreements if 0 < a.agreement_ratio < 0.5)
    no_data = sum(1 for a in comp.field_agreements if a.missing_count == a.total_backends)

    logger.info(
        f"  Agreement: {full} full, {partial} partial, "
        f"{disagree} disagree, {no_data} no-data "
        f"(across {len(comp.backends)} backends)"
    )

    for agr in comp.field_agreements:
        if agr.agreement_ratio < 0.8 and agr.missing_count < agr.total_backends:
            vals = ", ".join(f"{k}={v!r}" for k, v in agr.values.items())
            logger.info(f"    DISAGREE {agr.field_name}: {vals}")


def _log_batch_summary(results: list[ComparisonResult], total_elapsed: float) -> None:
    logger.info(f"=== Batch comparison done: {len(results)} drawings in {total_elapsed:.1f}s ===")

    backend_totals: dict[str, dict] = {}
    for comp in results:
        for br in comp.backends:
            name = br.backend_name
            totals = backend_totals.setdefault(name, {"drawings": 0, "tokens": 0, "time": 0.0, "fields": 0})
            totals["drawings"] += 1
            totals["tokens"] += br.result.tokens_used
            totals["time"] += comp.timing.get(name, 0.0)
            totals["fields"] += br.metrics.get("fields_extracted", 0)

    for name, t in sorted(backend_totals.items()):
        avg_fields = t["fields"] / t["drawings"] if t["drawings"] else 0
        logger.info(
            f"  {name}: {t['drawings']} drawings, {t['fields']} total fields "
            f"(avg {avg_fields:.1f}), {t['tokens']:,} tokens, {t['time']:.1f}s"
        )

    total_agreements = sum(len(r.field_agreements) for r in results)
    total_full = sum(sum(1 for a in r.field_agreements if a.agreement_ratio >= 0.8) for r in results)
    if total_agreements:
        logger.info(
            f"  Overall agreement: {total_full}/{total_agreements} fields ({total_full / total_agreements:.0%})"
        )
