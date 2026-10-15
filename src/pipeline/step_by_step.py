"""Step-by-step tile-by-tile drawing analysis.

Processes all 9 tiles of the 3x3 grid sequentially with progressive output.
Works with any OCR backend (VLM, OLMOCR, GLM-OCR).
"""

from __future__ import annotations

import logging
import time

from src.document.loader import DocumentLoader
from src.ocr.base import OCRBackend
from src.ocr.vlm_backend import VLMBackend
from src.pipeline.simple_extractor import (
    known_fields_summary,
    merge_results,
    to_field,
)
from src.schema.enums import DrawingType, classify_drawing
from src.schema.extraction import ExtractionResult

logger = logging.getLogger(__name__)

# Smart tile order: title block first, specs second, then drawing body, then headers
TILE_ORDER = [
    {"tile": (2, 2), "label": "bottom-right", "focus": "title block data", "category": "title_block"},
    {"tile": (2, 0), "label": "bottom-left", "focus": "specifications and notes", "category": "specs"},
    {"tile": (2, 1), "label": "bottom-center", "focus": "title block continuation", "category": "title_block"},
    {"tile": (1, 1), "label": "center", "focus": "main drawing body", "category": "drawing_body"},
    {"tile": (1, 0), "label": "middle-left", "focus": "side views and dimensions", "category": "drawing_body"},
    {"tile": (1, 2), "label": "middle-right", "focus": "dimensions and symbols", "category": "drawing_body"},
    {"tile": (0, 0), "label": "top-left", "focus": "revision table", "category": "header"},
    {"tile": (0, 1), "label": "top-center", "focus": "general notes", "category": "header"},
    {"tile": (0, 2), "label": "top-right", "focus": "additional references", "category": "header"},
]

CATEGORY_INSTRUCTIONS = {
    "title_block": ("Focus on extracting: project name, client, cabinet id, scale, units."),
    "specs": (
        "Focus on extracting: board type annotations (MFC, MDF), edge banding (okleina), "
        "hardware items, notes and specifications."
    ),
    "drawing_body": (
        "Focus on: panel dimensions in millimeters (length x width), panel names "
        "(blat, bok, front, plecy, dno, półka), thickness, quantity."
    ),
    "header": ("Focus on: project header, scale, date, and any additional specifications."),
}

BASE_PROMPT = """\
Analyze this section of a furniture drawing ({tile_position} area — {focus}).
Previously found from other tiles: {known_fields}

Extract ONLY fields visible in THIS tile that have NOT already been found.
Return ONLY valid JSON — no markdown fences, no explanation.

{category_instructions}

Required fields (use null if not visible):
- project_name: {{"value": "original", "value_eng": "english"}}
- client: {{"value": "original", "value_eng": "english"}}
- cabinet_id: {{"value": "original", "value_eng": "english"}}
- scale: {{"value": "original", "value_eng": "english"}}
- units: {{"value": "original", "value_eng": "english"}}
- panels: [list of {"name": "...", "length_mm": N, "width_mm": N, "thickness_mm": N, "board_type": "...", "qty": N}]
- hardware: [list of {"name": "...", "code": "...", "qty": N}]"""

DIVIDER = "=" * 70


def _count_found(merged: dict) -> int:
    return sum(1 for v in merged.values() if v is not None)


def _print_header(filename: str, drawing_type: DrawingType, backend_name: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  Step-by-Step Extraction: {filename}")
    print(f"  Drawing Type: {drawing_type.value} | OCR Backend: {backend_name}")
    print("  Grid: 3x3 (9 tiles)")
    print(f"{DIVIDER}")


def _print_step(
    step_num: int,
    total: int,
    label: str,
    focus: str,
    known: str,
    is_ocr: bool,
) -> None:
    print(f"\n--- Step {step_num}/{total}: {label} ({focus}) ---")
    print(f"  Already found: {known or 'none'}")


def _print_ocr_output(ocr_text: str, backend_name: str) -> None:
    lines = ocr_text.strip().split("\n")
    print(f"  OCR output ({backend_name}):")
    for line in lines[:12]:
        print(f"    {line[:90]}")
    if len(lines) > 12:
        print(f"    ... ({len(lines) - 12} more lines)")


def _print_extracted(parsed: dict | None, tokens: int, running_fields: int, running_tokens: int) -> None:
    if parsed:
        found = [
            f"{k}={v['value']}" if isinstance(v, dict) and v.get("value") else k
            for k, v in parsed.items()
            if v is not None
        ]
        print(f"  Extracted: {', '.join(found) or 'no new fields'}")
    else:
        print("  No new fields found (JSON parse failed)")
    print(f"  Running total: {running_fields} fields | {running_tokens:,} tokens")


def _print_final(result: ExtractionResult, total_fields: int, total_tokens: int, tiles_ok: int) -> None:
    print(f"\n{DIVIDER}")
    print(f"  FINAL RESULT — {total_fields} fields extracted | {total_tokens:,} tokens")
    print(f"{DIVIDER}")

    print(f"  Project:   {result.project_name.value or '—'}")
    print(f"  Client:    {result.client.value or '—'}")
    print(f"  Cabinet:   {result.cabinet_id.value or '—'}")
    print(f"  Scale:     {result.scale.value or '—'}")
    print(f"  Units:     {result.units.value or '—'}")
    print(f"  Panels:    {len(result.panels)}")
    for panel in result.panels:
        dims = f"{panel.length_mm or '?'}x{panel.width_mm or '?'}x{panel.thickness_mm or '?'}mm"
        print(f"    {panel.name or 'unnamed':20s} {dims:20s} {panel.board_type or ''} x{panel.qty}")
    print(f"  Hardware:  {len(result.hardware)}")
    for hw in result.hardware:
        print(f"    {hw.name or 'unnamed':20s} {hw.code or ''} x{hw.qty}")

    if result.needs_review:
        print(f"\n  Fields needing review: {', '.join(result.review_fields)}")

    print(f"\n  Tiles processed: {tiles_ok}/9")
    print(f"  Total tokens:    {total_tokens:,}")
    print(f"{DIVIDER}\n")


async def extract_drawing_step_by_step(
    loader: DocumentLoader,
    ocr_backend: OCRBackend,
    tile_overlap: float = 0.15,
) -> tuple[ExtractionResult, dict]:
    """Extract data from a drawing by analyzing all 9 tiles sequentially.

    Prints progressive output after each tile.
    """
    filename = loader.path.name
    drawing_type = classify_drawing(filename)
    tiles = loader.get_tiles(grid_size=3, overlap=tile_overlap)
    tile_map = {(t["row"], t["col"]): t for t in tiles}

    is_ocr_backend = not isinstance(ocr_backend, VLMBackend)

    _print_header(filename, drawing_type, ocr_backend.name)

    merged: dict = {}
    per_tile: list[dict] = []
    total_tokens = 0
    tiles_processed = 0

    for step_idx, step in enumerate(TILE_ORDER, 1):
        row, col = step["tile"]
        tile = tile_map.get((row, col))
        if tile is None:
            continue

        known = known_fields_summary(merged)
        _print_step(step_idx, len(TILE_ORDER), step["label"], step["focus"], known, is_ocr_backend)

        # Build prompt
        prompt = BASE_PROMPT.format(
            tile_position=step["label"],
            focus=step["focus"],
            known_fields=known or "none",
            category_instructions=CATEGORY_INSTRUCTIONS[step["category"]],
        )

        image_bytes = tile["image_bytes"]
        media_type = tile["media_type"]

        # Phase A: OCR text extraction (for dedicated OCR backends)
        if is_ocr_backend:
            ocr_text, ocr_tokens = await ocr_backend.extract_text(image_bytes, media_type)
            total_tokens += ocr_tokens
            _print_ocr_output(ocr_text, ocr_backend.name)

        # Phase B: Structured JSON extraction
        parsed, tokens = await ocr_backend.extract_json(image_bytes, media_type, prompt)
        total_tokens += tokens
        tiles_processed += 1

        if parsed:
            merged = merge_results(merged, parsed)
            per_tile.append(parsed)
        else:
            per_tile.append({})

        _print_extracted(parsed, tokens, _count_found(merged), total_tokens)

    # Deduplicate fields from overlapping tiles
    from src.document.dedup import dedup_fields

    merged, conflicts = dedup_fields(merged, per_tile)
    if conflicts:
        print(f"\n  ⚠ Tile conflicts: {'; '.join(conflicts)}")
        logger.warning(f"Tile conflicts: {'; '.join(conflicts)}")

    # Build ExtractionResult (furniture cutting list)
    from src.schema.extraction import Hardware, Panel

    panels_list: list[Panel] = []
    for p_data in merged.get("panels") or []:
        if isinstance(p_data, dict):
            panels_list.append(
                Panel(
                    name=p_data.get("name"),
                    length_mm=p_data.get("length_mm"),
                    width_mm=p_data.get("width_mm"),
                    thickness_mm=p_data.get("thickness_mm"),
                    board_type=p_data.get("board_type"),
                    qty=p_data.get("qty", 1) or 1,
                )
            )

    hardware_list: list[Hardware] = []
    for h_data in merged.get("hardware") or []:
        if isinstance(h_data, dict):
            hardware_list.append(
                Hardware(
                    name=h_data.get("name"),
                    code=h_data.get("code"),
                    qty=h_data.get("qty", 1) or 1,
                )
            )

    result = ExtractionResult(
        source_file=str(loader.path),
        drawing_type=drawing_type,
        extraction_timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        model_used=ocr_backend.name,
        passes_completed=tiles_processed,
        project_name=to_field(merged.get("project_name")),
        client=to_field(merged.get("client")),
        cabinet_id=to_field(merged.get("cabinet_id")),
        scale=to_field(merged.get("scale")),
        units=to_field(merged.get("units")),
        panels=panels_list,
        hardware=hardware_list,
        tokens_used=total_tokens,
    )

    from src.schema.extraction import SCALAR_FIELDS

    result.needs_review = not result.panels or any(
        f.value and f.confidence.value == "LOW" for name in SCALAR_FIELDS if (f := getattr(result, name, None))
    )

    if result.needs_review:
        result.review_fields = [
            name
            for name in SCALAR_FIELDS
            if (f := getattr(result, name, None)) and f.value and f.confidence.value == "LOW"
        ]

    _print_final(result, _count_found(merged), total_tokens, tiles_processed)

    metrics = {
        "tiles_processed": tiles_processed,
        "total_tokens": total_tokens,
        "fields_extracted": _count_found(merged),
    }

    return result, metrics
