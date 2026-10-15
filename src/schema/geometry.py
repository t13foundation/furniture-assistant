"""Geometry-first reconciliation for extracted furniture panels."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from src.schema.enums import ConfidenceLevel
from src.schema.extraction import ExtractionResult, GeometryGroup, GeometrySummary, Panel

_CONFIDENCE_RANK = {
    ConfidenceLevel.HIGH: 2,
    ConfidenceLevel.MEDIUM: 1,
    ConfidenceLevel.LOW: 0,
}


def _category(name: str | None) -> str:
    text = (name or "").replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    words = set(text.split())
    if words & {"lewy", "left"} and words & {"bok", "side"}:
        return "side_left"
    if words & {"prawy", "right"} and words & {"bok", "side"}:
        return "side_right"
    for category, words in (
        ("side", ("bok", "side")),
        ("front", ("front", "drzwi", "door")),
        ("shelf", ("polka", "shelf")),
        ("top", ("blat", "top")),
        ("bottom", ("dno", "bottom")),
        ("back", ("plecy", "back")),
        ("rail", ("listwa", "rail")),
    ):
        if any(word in text.split() for word in words):
            return category
    return "panel"


def _dimension(value: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    return round(float(value), 1)


def _confidence(panels: list[Panel]) -> ConfidenceLevel:
    return min((panel.confidence for panel in panels), key=lambda value: _CONFIDENCE_RANK[value])


def reconcile_panel_geometry(result: ExtractionResult) -> list[str]:
    """Build a conservative geometric inventory without deleting panel rows.

    Complete dimensions are grouped by semantic category and normalized
    dimensions. Incomplete records remain isolated. Repeated groups expose a
    minimum/maximum quantity range instead of guessing whether they are
    duplicate views or separate physical pieces.
    """

    buckets: dict[tuple[object, ...], list[tuple[int, Panel]]] = defaultdict(list)
    unresolved: list[tuple[int, Panel]] = []
    for index, panel in enumerate(result.panels):
        length = _dimension(panel.length_mm)
        width = _dimension(panel.width_mm)
        if length is None or width is None:
            unresolved.append((index, panel))
            continue
        long_side, short_side = sorted((length, width), reverse=True)
        buckets[
            (
                _category(panel.name),
                long_side,
                short_side,
                _dimension(panel.thickness_mm),
            )
        ].append((index, panel))

    groups: list[GeometryGroup] = []
    notes: list[str] = []
    minimum_total = 0
    maximum_total = 0
    group_number = 0

    for key, entries in buckets.items():
        category, length, width, thickness = key
        panels = [panel for _, panel in entries]
        observed_qty = sum(max(panel.qty, 0) for panel in panels)
        minimum_qty = max(max(panel.qty, 0) for panel in panels)
        maximum_qty = observed_qty
        group_number += 1
        group_notes = ""
        if len(entries) > 1:
            group_notes = (
                "Powtarzająca się geometria; minimum zakłada duplikat widoku, "
                "maksimum zakłada osobne elementy. Wymaga weryfikacji."
            )
            notes.append(f"Grupa G{group_number:03d} ma {len(entries)} rekordy o tej samej geometrii.")
        groups.append(
            GeometryGroup(
                group_id=f"G{group_number:03d}",
                category=str(category),
                length_mm=length,
                width_mm=width,
                thickness_mm=thickness,
                source_panel_indices=[index for index, _ in entries],
                observed_qty=observed_qty,
                minimum_qty=minimum_qty,
                maximum_qty=maximum_qty,
                confidence=_confidence(panels),
                notes=group_notes,
            )
        )
        minimum_total += minimum_qty
        maximum_total += maximum_qty

    for index, panel in unresolved:
        group_number += 1
        qty = max(panel.qty, 0)
        groups.append(
            GeometryGroup(
                group_id=f"G{group_number:03d}",
                category=_category(panel.name),
                source_panel_indices=[index],
                observed_qty=qty,
                minimum_qty=0,
                maximum_qty=qty,
                confidence=panel.confidence,
                notes="Wymiary niekompletne; liczba elementów nie może być potwierdzona geometrycznie.",
            )
        )
        maximum_total += qty

    ambiguous = sum(1 for group in groups if group.minimum_qty != group.maximum_qty)
    result.geometry_groups = groups
    result.geometry_summary = GeometrySummary(
        panel_records=len(result.panels),
        resolved_records=len(result.panels) - len(unresolved),
        unresolved_records=len(unresolved),
        geometry_groups=len(groups),
        ambiguous_groups=ambiguous,
        minimum_panel_count=minimum_total,
        maximum_panel_count=maximum_total,
        notes=notes,
    )

    warnings: list[str] = []
    if ambiguous:
        warnings.append(f"Geometry reconciliation: {ambiguous} panel groups have an ambiguous quantity range")
    if unresolved:
        warnings.append(f"Geometry reconciliation: {len(unresolved)} panel records have incomplete dimensions")
    return warnings
