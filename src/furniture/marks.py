"""Deterministic dimension marks from the PDF text layer, grouped into dimension chains.

Architectural elevations print every dimension as a text object next to its
dimension line. Reading those numbers with their positions (instead of asking a
vision model) gives exact values; the LLM only assigns roles to the numbered marks.
"""

from __future__ import annotations

import itertools
import math
import re
import statistics
from pathlib import Path

import pypdfium2  # type: ignore[import-untyped]
import pypdfium2.raw as pdfium_c  # type: ignore[import-untyped]

from src.furniture.schema import DimensionChain, DimensionMark, Orientation, TextRun, Unit
from src.schema.extraction import BoundingBox

_NUMBER_RE = re.compile(r"\d{1,4}([.,]\d)?")
_MERGED_RE = re.compile(r"\d{1,4}(?:[.,]\d)?(?:\s+\d{1,4}(?:[.,]\d)?)+")

# Chain grouping tolerances are expressed in multiples of the median text height of
# the marks (about 5-6 pt on 1:50 sheets), so they are independent of page size and units.
_ALIGN_TOL = 0.7  # centre alignment for marks of one chain (≈4 pt)
_PERP_TOL = 10.0  # perpendicular distance from a chain to its overall mark (≈60 pt)
_GAP_FACTOR = 3.0  # break a chain when the gap is this many times the scale-expected gap
_MISMATCH_TOL = 0.05


def _clamp(v: float) -> float:
    return min(1.0, max(0.0, v))


def _orientation(textpage, rect: tuple[float, float, float, float]) -> Orientation:
    """Text rotation from the first glyph's angle; bbox aspect as fallback."""
    left, bottom, right, top = rect
    index = pdfium_c.FPDFText_GetCharIndexAtPos(textpage.raw, (left + right) / 2, (bottom + top) / 2, 2.0, 2.0)
    if index >= 0:
        angle = pdfium_c.FPDFText_GetCharAngle(textpage.raw, index)
        if angle >= 0:
            return "vertical" if abs(math.sin(angle)) > 0.7 else "horizontal"
    return "vertical" if (top - bottom) > (right - left) else "horizontal"


def _split_rect(
    text: str, rect: tuple[float, float, float, float], orientation: Orientation
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Split a merged run like ``"50 50"`` into per-number rects, proportional to character count."""
    tokens = text.split()
    total = sum(len(t) for t in tokens) or 1
    left, bottom, right, top = rect
    parts: list[tuple[str, tuple[float, float, float, float]]] = []
    offset = 0
    for token in tokens:
        start, end = offset / total, (offset + len(token)) / total
        offset += len(token)
        if orientation == "horizontal":
            span = right - left
            parts.append((token, (left + start * span, bottom, left + end * span, top)))
        else:
            # Vertical text (rotated 90°) reads bottom→top.
            span = top - bottom
            parts.append((token, (left, bottom + start * span, right, bottom + end * span)))
    return parts


def detect_units(values: list[float]) -> Unit:
    """cm when the 95th percentile of the numbers is below 500 (the sample sheets), else mm."""
    if not values:
        return "cm"
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
    return "cm" if p95 < 500 else "mm"


def _to_box(rect: tuple[float, float, float, float], width: float, height: float) -> BoundingBox:
    """PDF rect (left, bottom, right, top in points, y up) → normalized page box with y from the top."""
    left, bottom, right, top = rect
    return BoundingBox(
        x_min=_clamp(left / width),
        x_max=_clamp(right / width),
        y_min=_clamp(1 - top / height),
        y_max=_clamp(1 - bottom / height),
    )


def extract_marks(pdf_path: str, page_index: int) -> tuple[list[DimensionMark], list[TextRun]]:
    """Numeric text objects of a PDF page as dimension marks, plus every text run with its position.

    Non-PDF inputs and scanned pages (no text layer) return ``([], [])``.
    """
    if Path(pdf_path).suffix.lower() != ".pdf":
        return [], []
    pdf = pypdfium2.PdfDocument(pdf_path)
    try:
        page = pdf[page_index]
        width, height = page.get_size()
        textpage = page.get_textpage()
        count = textpage.count_rects()
        if count == 0:
            return [], []

        runs: list[TextRun] = []
        raw: list[tuple[float, Orientation, tuple[float, float, float, float]]] = []
        for i in range(count):
            rect = textpage.get_rect(i)
            text = textpage.get_text_bounded(*rect).strip()
            if not text:
                continue
            runs.append(TextRun(text=text, bbox=_to_box(rect, width, height)))
            if _NUMBER_RE.fullmatch(text):
                raw.append((float(text.replace(",", ".")), _orientation(textpage, rect), rect))
            elif _MERGED_RE.fullmatch(text):
                orientation = _orientation(textpage, rect)
                for token, sub in _split_rect(text, rect, orientation):
                    raw.append((float(token.replace(",", ".")), orientation, sub))
    finally:
        pdf.close()

    unit = detect_units([value for value, _, _ in raw])
    boxes = [(value, orientation, _to_box(rect, width, height)) for value, orientation, rect in raw]
    boxes.sort(key=lambda item: (item[2].y_min, item[2].x_min))
    marks = [
        DimensionMark(
            id=f"D{n}",
            value=value,
            unit=unit,
            page_index=page_index,
            bbox=bbox,
            orientation=orientation,
        )
        for n, (value, orientation, bbox) in enumerate(boxes, start=1)
    ]
    return marks, runs


# ── Chains ──────────────────────────────────────────────────────────────────


def _axis(mark: DimensionMark) -> tuple[float, float]:
    """(along-chain centre, perpendicular centre) for the mark's orientation."""
    b = mark.bbox
    cx, cy = (b.x_min + b.x_max) / 2, (b.y_min + b.y_max) / 2
    return (cx, cy) if mark.orientation == "horizontal" else (cy, cx)


def _text_height(mark: DimensionMark) -> float:
    b = mark.bbox
    return (b.y_max - b.y_min) if mark.orientation == "horizontal" else (b.x_max - b.x_min)


def _span(mark: DimensionMark) -> tuple[float, float]:
    b = mark.bbox
    return (b.x_min, b.x_max) if mark.orientation == "horizontal" else (b.y_min, b.y_max)


def _aligned_groups(marks: list[DimensionMark], tol: float) -> list[list[DimensionMark]]:
    """Group marks whose perpendicular centres lie within ``tol`` of the group's first mark."""
    ordered = sorted(marks, key=lambda m: _axis(m)[1])
    groups: list[list[DimensionMark]] = []
    for mark in ordered:
        if groups and abs(_axis(mark)[1] - _axis(groups[-1][0])[1]) <= tol:
            groups[-1].append(mark)
        else:
            groups.append([mark])
    return [sorted(g, key=lambda m: _axis(m)[0]) for g in groups]


def _scale(groups: list[list[DimensionMark]]) -> float | None:
    """Median page-distance per drawing unit between neighbouring marks of aligned rows."""
    ratios = [
        (_axis(b)[0] - _axis(a)[0]) / ((a.value + b.value) / 2)
        for group in groups
        for a, b in itertools.pairwise(group)
        if a.value + b.value > 0
    ]
    return statistics.median(ratios) if ratios else None


def _split_gaps(group: list[DimensionMark], scale: float | None, text_h: float) -> list[list[DimensionMark]]:
    """Break an aligned row where the gap is far larger than the dimension values explain.

    Different modules on one sheet share a dimension-line row; their chains must not merge.
    """
    if scale is None:
        return [group]
    runs: list[list[DimensionMark]] = [[group[0]]]
    for a, b in itertools.pairwise(group):
        expected = (a.value + b.value) / 2 * scale
        if _axis(b)[0] - _axis(a)[0] > _GAP_FACTOR * expected + 2 * text_h:
            runs.append([b])
        else:
            runs[-1].append(b)
    return runs


def build_chains(marks: list[DimensionMark]) -> list[DimensionChain]:
    """Group aligned marks into dimension chains and match each chain to its overall mark.

    Sets ``mark.chain_id`` in place. ``consistent`` is True when a nearby mark equals the
    chain sum (±1 unit), False when the nearest candidate is only within 5 %, None otherwise.
    """
    chains: list[tuple[DimensionChain, list[DimensionMark]]] = []
    for orientation in ("horizontal", "vertical"):
        oriented = [m for m in marks if m.orientation == orientation]
        if not oriented:
            continue
        text_h = statistics.median(_text_height(m) for m in oriented)
        groups = _aligned_groups(oriented, _ALIGN_TOL * text_h)
        scale = _scale(groups)
        for group in groups:
            for run in _split_gaps(group, scale, text_h):
                if len(run) < 2:
                    continue
                chain = DimensionChain(
                    id=f"C{len(chains) + 1}",
                    orientation=orientation,
                    mark_ids=[m.id for m in run],
                    sum_value=round(sum(m.value for m in run), 3),
                )
                chains.append((chain, run))

    by_orientation = {o: [m for m in marks if m.orientation == o] for o in ("horizontal", "vertical")}
    text_hs = {o: statistics.median(_text_height(m) for m in ms) if ms else 0.0 for o, ms in by_orientation.items()}

    def candidates(chain: DimensionChain, run: list[DimensionMark], tolerance: float) -> list[DimensionMark]:
        lo, hi = _span(run[0])[0], _span(run[-1])[1]
        perp = statistics.mean(_axis(m)[1] for m in run)
        limit = _PERP_TOL * text_hs[chain.orientation]
        found = [
            m
            for m in by_orientation[chain.orientation]
            if m.id not in chain.mark_ids
            and lo <= _axis(m)[0] <= hi
            and abs(_axis(m)[1] - perp) <= limit
            and abs(m.value - chain.sum_value) <= tolerance
        ]
        return sorted(found, key=lambda m: abs(_axis(m)[1] - perp))

    exact_overalls: set[str] = set()
    for chain, run in chains:
        found = candidates(chain, run, 1.0)
        if found:
            chain.overall_mark_id = found[0].id
            chain.consistent = True
            exact_overalls.add(found[0].id)
    for chain, run in chains:
        if chain.consistent:
            continue
        tolerance = _MISMATCH_TOL * chain.sum_value
        near = [m for m in candidates(chain, run, tolerance) if m.id not in exact_overalls]
        if near:
            chain.overall_mark_id = near[0].id
            chain.consistent = False

    by_id = {m.id: m for m in marks}
    for chain, _ in chains:
        for mark_id in chain.mark_ids:
            by_id[mark_id].chain_id = chain.id
    return [chain for chain, _ in chains]
