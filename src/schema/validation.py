"""Cross-field validation and diff-merge for furniture ExtractionResult."""

from __future__ import annotations

import contextlib
import logging
import re

from pydantic import BaseModel

from src.schema.enums import ConfidenceLevel
from src.schema.extraction import SCALAR_FIELDS, ExtractionResult, FieldExtraction, Panel

logger = logging.getLogger(__name__)

# Fields that should never be overwritten by validation
_SKIP_FIELDS = {
    "source_file",
    "drawing_type",
    "extraction_timestamp",
    "model_used",
    "passes_completed",
    "tokens_used",
    "tool_call_log",
    "panels",
    "hardware",
    "notes",
    "run_metadata",
}


class FieldDiff(BaseModel):
    """Records what changed during validation."""

    field_name: str
    old_value: str | None = None
    new_value: str | None = None
    old_confidence: str = ""
    new_confidence: str = ""
    reason: str = ""


def merge_diff(
    result: ExtractionResult,
    diff_data: dict,
) -> list[FieldDiff]:
    """Merge a JSON diff into an ExtractionResult's scalar fields (in-place).

    Only SCALAR_FIELDS (project_name, client, cabinet_id, scale, units) are
    diff-merged. Panel-level corrections use a separate ``panel_<i>_`` prefix.

    Args:
        result: The extraction result to update.
        diff_data: Dict mapping field names to partial FieldExtraction dicts.
            Example: {"client": {"value": "KLIENT", "confidence": "HIGH"}}

    Returns:
        List of FieldDiff records for fields that actually changed.
    """
    diffs: list[FieldDiff] = []

    for field_name, field_data in diff_data.items():
        # Handle panel-level corrections (e.g. "panel_0_length_mm")
        if field_name.startswith("panel_"):
            _apply_panel_diff(result, field_name, field_data)
            continue

        if field_name in _SKIP_FIELDS:
            continue
        if field_name not in SCALAR_FIELDS:
            continue
        if not isinstance(field_data, dict):
            continue

        current = getattr(result, field_name, None)
        if current is None:
            current = FieldExtraction()

        old_value = current.value
        old_confidence = current.confidence.value

        changed = False
        missing_value = False
        if "value" in field_data:
            new_value = field_data["value"]
            if isinstance(new_value, str) and new_value.strip().lower() in {
                "",
                "none",
                "null",
                "n/a",
                "unknown",
                "not found",
            }:
                new_value = None
                missing_value = True
            if new_value is not None and new_value != old_value:
                current.value = new_value
                changed = True
        if "confidence" in field_data and not missing_value:
            new_conf = field_data["confidence"]
            if new_conf != old_confidence:
                current.confidence = ConfidenceLevel(new_conf)
                changed = True
        if "notes" in field_data and field_data["notes"] != current.notes:
            current.notes = field_data["notes"]
            changed = True
        if "value_eng" in field_data and field_data["value_eng"] != current.value_eng:
            current.value_eng = field_data["value_eng"]
            changed = True

        if changed:
            diffs.append(
                FieldDiff(
                    field_name=field_name,
                    old_value=old_value,
                    new_value=current.value,
                    old_confidence=old_confidence,
                    new_confidence=current.confidence.value,
                )
            )

        setattr(result, field_name, current)

    for d in diffs:
        logger.info(
            f"  Validation: {d.field_name}: "
            f"'{d.old_value}' -> '{d.new_value}' "
            f"({d.old_confidence} -> {d.new_confidence})"
        )

    return diffs


def _apply_panel_diff(result: ExtractionResult, key: str, value) -> None:
    """Apply a panel-level correction from a validation diff.

    Keys are ``panel_<index>_<field>`` e.g. ``panel_0_length_mm``.
    """
    parts = key.split("_", 2)
    if len(parts) < 3:
        return
    try:
        idx = int(parts[1])
    except ValueError:
        return
    attr = parts[2]

    if idx < 0 or idx >= len(result.panels):
        return

    panel = result.panels[idx]
    if attr == "confidence":
        with contextlib.suppress(ValueError, TypeError):
            panel.confidence = ConfidenceLevel(value)
    elif hasattr(panel, attr):
        with contextlib.suppress(ValueError, TypeError):
            setattr(panel, attr, value)


def panel_review_reasons(panel: Panel) -> list[str]:
    """Return deterministic reasons a panel is unsafe for production."""
    reasons: list[str] = []
    if panel.confidence == ConfidenceLevel.LOW:
        reasons.append("LOW confidence")

    for label, value in (
        ("Length", panel.length_mm),
        ("Width", panel.width_mm),
        ("Thickness", panel.thickness_mm),
    ):
        if value is None:
            reasons.append(f"Missing {label.lower()}")
        elif value <= 0:
            reasons.append(f"{label} must be positive")

    if not (panel.board_type or "").strip() and not (panel.material_code or "").strip():
        reasons.append("Missing board type/material code")
    if panel.qty <= 0:
        reasons.append("Quantity must be positive")
    return reasons


def validate_result(result: ExtractionResult) -> list[str]:
    """Post-extraction validation warnings for furniture cutting lists."""
    warnings: list[str] = []

    # Check panels
    for i, panel in enumerate(result.panels):
        prefix = f"Panel {i} ({panel.name or 'unnamed'})"

        for reason in panel_review_reasons(panel):
            warnings.append(f"{prefix}: {reason}")

        # Thickness should be a reasonable furniture value
        if panel.thickness_mm is not None and panel.thickness_mm > 50:
            warnings.append(f"{prefix}: thickness {panel.thickness_mm}mm seems unusually large")

        # Length should be positive and reasonable (< 5000mm for furniture)
        if panel.length_mm is not None and panel.length_mm > 5000:
            warnings.append(f"{prefix}: length {panel.length_mm}mm seems unusually large")

    # Identical dimensions across multiple panels is a strong signal that the model
    # copied the overall cabinet dimension instead of reading per-panel dimensions.
    # This was the dominant extraction error in initial testing.
    if len(result.panels) >= 2:
        dim_counts: dict[tuple, list[int]] = {}
        for i, p in enumerate(result.panels):
            key = (p.length_mm, p.width_mm)
            if p.length_mm is not None:
                dim_counts.setdefault(key, []).append(i)
        for (length, width), indices in dim_counts.items():
            if len(indices) >= 3:  # 3+ panels with identical LxW is almost certainly an error
                names = [result.panels[i].name or f"panel {i}" for i in indices]
                warnings.append(
                    f"{len(indices)} panels share identical dimensions {length}x{width}mm "
                    f"({', '.join(names)}) - likely copied the cabinet total, not per-panel sizes"
                )

    # Low-confidence scalar fields
    low_confidence = [
        name
        for name in SCALAR_FIELDS
        if getattr(result, name, None) and getattr(result, name).confidence.value == "LOW"
    ]
    if low_confidence:
        warnings.append(f"Low confidence scalar fields: {', '.join(low_confidence)}")

    # No panels extracted from a furniture drawing is suspicious
    if not result.panels and result.drawing_type.value in ("ELEVATION", "IMAGE"):
        warnings.append("furniture drawing with no panels extracted — dimensions may be missing")

    return warnings


def normalize_values(result: ExtractionResult) -> list[str]:
    """Post-extraction value normalization for furniture fields."""
    corrections: list[str] = []

    # Unit normalization: if units says "cm" and dims look like cm (< 500), convert to mm.
    units_val = (result.units.value or "").lower()
    if "cm" in units_val and "mm" not in units_val:
        converted = False
        for panel in result.panels:
            for attr in ("length_mm", "width_mm"):
                v = getattr(panel, attr, None)
                if v is not None and 0 < v < 500:
                    setattr(panel, attr, v * 10)
                    converted = True
        if converted:
            corrections.append("Converted dimensions from cm to mm (x10)")

    if units_val != "mm":
        result.units.value = "mm"
        result.units.confidence = ConfidenceLevel.HIGH
        corrections.append("Normalized output units to mm")

    for panel in result.panels:
        if panel.length_mm is not None and panel.width_mm is not None and panel.width_mm > panel.length_mm:
            # Ensure length >= width (convention: length is the longer dimension)
            panel.length_mm, panel.width_mm = panel.width_mm, panel.length_mm
            corrections.append(f"Panel '{panel.name}': swapped length/width to maintain length >= width")

        # Normalize board_type: strip extra whitespace
        if panel.board_type:
            cleaned = re.sub(r"\s+", " ", panel.board_type).strip()
            if cleaned != panel.board_type:
                panel.board_type = cleaned

    return corrections


def cross_validate_fields(result: ExtractionResult) -> list[str]:
    """Cross-field consistency checks for furniture extraction."""
    warnings: list[str] = []

    # If scale mentions mm but units says cm (or vice versa)
    scale_val = (result.scale.value or "").lower()
    units_val = (result.units.value or "").lower()
    if scale_val and units_val and "mm" in scale_val and "cm" in units_val:
        warnings.append("scale says mm but units says cm — possible inconsistency")

    # Check for duplicate panels (same name + dimensions)
    seen: dict[str, int] = {}
    for panel in result.panels:
        key = f"{panel.name}|{panel.length_mm}|{panel.width_mm}"
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count > 1:
            warnings.append(f"duplicate panel definition: {key} appears {count} times — consider merging with qty")

    return warnings
