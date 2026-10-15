"""Simple direct extraction — 2-step linked workflow, no agent loop.

Step 1: Analyze title block tile → extract core fields + translate to English
Step 2: Analyze specifications tile → fill remaining fields, guided by Step 1 results
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TypedDict

import httpx

from src.document.loader import DocumentLoader
from src.schema.enums import ConfidenceLevel, classify_drawing
from src.schema.extraction import ExtractionResult, FieldExtraction, RunMetadata

logger = logging.getLogger(__name__)

TITLE_BLOCK_PROMPT = """\
Analyze this section of a furniture drawing (title block / legend area).
Extract all visible data and return ONLY valid JSON — no markdown fences, no explanation.

Required fields (use null if not visible):
- project_name: {"value": "original", "value_eng": "english"}
- client: {"value": "original", "value_eng": "english"}
- cabinet_id: {"value": "original", "value_eng": "english"}
- scale: {"value": "original", "value_eng": "english"}
- units: {"value": "original", "value_eng": "english"}
- panels: [list of {"name": "...", "length_mm": N, "width_mm": N, "thickness_mm": N, "board_type": "...", "qty": N}]

Example:
{"project_name": {"value": "BIUROWIEC A"}, "panels": [{"name": "Blat", "length_mm": 800, "width_mm": 600, "thickness_mm": 18, "board_type": "MFC 18mm", "qty": 2}]}"""

SPECS_PROMPT = """\
Analyze this section of a furniture drawing (specifications / dimension / notes area).
Previous extraction found these fields already: {known_fields}

Extract ONLY fields not yet found. Return ONLY valid JSON — no markdown fences, no explanation.

Look for:
- panels: additional panel elements with dimensions (length_mm, width_mm, thickness_mm in mm)
- hardware: list of {"name": "...", "code": "...", "qty": N}
- board_type annotations (MFC, MDF, sklejka)
- edge_banding notes (okleina, ABS, PVC)

Use the same field format as the first step."""


class WorkflowStep(TypedDict):
    tile: tuple[int, int]
    focus: str
    prompt: str


# 3x3 grid positions: row 0=top, 2=bottom. col 0=left, 2=right.
WORKFLOW_STEPS: list[WorkflowStep] = [
    {
        "tile": (2, 2),  # bottom-right = title block
        "focus": "title block data",
        "prompt": TITLE_BLOCK_PROMPT,
    },
    {
        "tile": (2, 0),  # bottom-left = specifications/notes
        "focus": "specifications and notes",
        "prompt": SPECS_PROMPT,
    },
]


def extract_json(text: str) -> dict | None:
    """Extract JSON from model output, handling markdown fences."""
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    cleaned = re.sub(r"```json\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    if cleaned.startswith("{"):
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def to_field(raw: str | dict | bool | int | float | None, confidence: str = "MEDIUM") -> FieldExtraction:
    """Convert raw extraction value to FieldExtraction, handling nested {value, value_eng}."""
    if raw is None:
        return FieldExtraction(value=None, confidence=ConfidenceLevel.LOW)

    if isinstance(raw, bool):
        return FieldExtraction(value=str(raw), value_eng=str(raw), confidence=ConfidenceLevel(confidence))

    if isinstance(raw, dict):
        return FieldExtraction(
            value=raw.get("value"),
            value_eng=raw.get("value_eng"),
            confidence=ConfidenceLevel(confidence),
        )

    return FieldExtraction(value=str(raw), confidence=ConfidenceLevel(confidence))


def merge_results(base: dict, new: dict) -> dict:
    """Merge new extraction into base, only filling null/missing fields."""
    if not new:
        return base
    for key, value in new.items():
        if value is None:
            continue
        existing = base.get(key)
        if existing is None:
            base[key] = value
        elif isinstance(existing, dict) and isinstance(value, dict):
            for sub_key, sub_val in value.items():
                if sub_val is not None and existing.get(sub_key) is None:
                    existing[sub_key] = sub_val
    return base


def known_fields_summary(merged: dict) -> str:
    """Summarize which fields have been found for the follow-up prompt."""
    found = []
    for key, val in merged.items():
        if val is None:
            continue
        if isinstance(val, dict) and val.get("value"):
            found.append(f"{key}={val['value']}")
        elif isinstance(val, (str, bool)) and val:
            found.append(f"{key}={val}")
    return ", ".join(found) if found else "none"


async def call_vlm(
    api_base: str,
    model_name: str,
    api_key: str,
    prompt: str,
    image_b64: str,
) -> tuple[dict | None, int]:
    """Call the VL model with an image + prompt. Returns (parsed_json, total_tokens)."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }
        ],
        "max_tokens": 4000,
        "temperature": 0.1,
    }

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            f"{api_base}/chat/completions",
            headers=headers,
            json=payload,
        )
        data = resp.json()

    usage = data.get("usage", {})
    tokens = usage.get("total_tokens", 0)
    msg = data["choices"][0]["message"]
    content = msg.get("content", "")

    parsed = extract_json(content)
    if parsed is None:
        logger.warning(f"  Could not parse JSON from: {content[:300]}")
    return parsed, tokens


async def extract_drawing(
    loader: DocumentLoader,
    api_base: str,
    model_name: str,
    api_key: str = "not-needed",
    tile_overlap: float = 0.15,
    run_metadata: RunMetadata | None = None,
) -> tuple[ExtractionResult, dict]:
    """Extract data from a drawing using a 2-step linked workflow."""
    import base64

    filename = loader.path.name
    drawing_type = classify_drawing(filename)
    tiles = loader.get_tiles(grid_size=3, overlap=tile_overlap)
    tile_map = {(t["row"], t["col"]): t for t in tiles}

    merged: dict = {}
    per_tile: list[dict] = []
    total_tokens = 0
    tiles_processed = 0

    for step_idx, step in enumerate(WORKFLOW_STEPS):
        row, col = step["tile"]
        tile = tile_map.get((row, col))
        if tile is None:
            continue

        # Build prompt — Step 2 gets context from Step 1
        prompt = step["prompt"]
        if step_idx == 1:
            prompt = prompt.replace("{known_fields}", known_fields_summary(merged))

        b64 = base64.b64encode(tile["image_bytes"]).decode()

        logger.info(f"  Step {step_idx + 1}: tile ({row},{col}) '{tile['label']}' — {step['focus']}...")

        parsed, tokens = await call_vlm(api_base, model_name, api_key, prompt, b64)
        total_tokens += tokens
        tiles_processed += 1

        if parsed:
            merged = merge_results(merged, parsed)
            per_tile.append(parsed)
            logger.info(f"  Step {step_idx + 1}: extracted {list(parsed.keys())}")
        else:
            per_tile.append({})
            logger.warning(f"  Step {step_idx + 1}: no valid JSON")

        logger.info(
            f"  Step {step_idx + 1}: {tokens} tokens, merged fields: {len([k for k, v in merged.items() if v is not None])}"
        )

    # Deduplicate fields from overlapping tiles
    from src.document.dedup import dedup_fields

    merged, conflicts = dedup_fields(merged, per_tile)
    if conflicts:
        logger.warning(f"  Tile conflicts detected: {'; '.join(conflicts)}")

    # Build ExtractionResult (furniture cutting list)
    from src.schema.extraction import Hardware, Panel

    panels: list[Panel] = []
    for p_data in merged.get("panels") or []:
        if isinstance(p_data, dict):
            panels.append(
                Panel(
                    name=p_data.get("name"),
                    length_mm=p_data.get("length_mm"),
                    width_mm=p_data.get("width_mm"),
                    thickness_mm=p_data.get("thickness_mm"),
                    board_type=p_data.get("board_type"),
                    qty=p_data.get("qty", 1) or 1,
                )
            )

    hardware: list[Hardware] = []
    for h_data in merged.get("hardware") or []:
        if isinstance(h_data, dict):
            hardware.append(
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
        model_used=model_name,
        passes_completed=tiles_processed,
        project_name=to_field(merged.get("project_name")),
        client=to_field(merged.get("client")),
        cabinet_id=to_field(merged.get("cabinet_id")),
        scale=to_field(merged.get("scale")),
        units=to_field(merged.get("units")),
        panels=panels,
        hardware=hardware,
        tokens_used=total_tokens,
    )
    if run_metadata is not None:
        result.run_metadata = run_metadata

    metrics = {
        "tiles_processed": tiles_processed,
        "total_tokens": total_tokens,
        "fields_extracted": sum(1 for v in merged.values() if v is not None),
    }

    return result, metrics
