"""Sheet nesting of cut-list panels (rectpack guillotine) and an SVG sheet layout."""

from __future__ import annotations

import math
from html import escape

from pydantic import BaseModel, Field
from rectpack import GuillotineBssfSas, PackingBin, PackingMode, newPacker

from src.furniture.rules import ConstructionRules
from src.schema.extraction import Panel

_MAX_SHEETS = 10_000
_SVG_SCALE = 0.2  # px per mm
_SVG_GAP = 40


class Placement(BaseModel):
    part_id: str
    name: str
    x: float
    y: float
    w: float
    h: float
    rotated: bool


class Sheet(BaseModel):
    thickness_mm: float
    material: str | None
    length: float
    width: float
    placements: list[Placement] = Field(default_factory=list)
    unplaced: list[str] = Field(default_factory=list)


def piece_ids(panel: Panel) -> list[str]:
    """One part id per physical piece: the solid ids in ``notes`` when they match ``qty``."""
    ids = [p.strip() for p in panel.notes.split(",") if p.strip()]
    if len(ids) == panel.qty:
        return ids
    base = panel.part_id or panel.name or "part"
    return [base if panel.qty == 1 else f"{base}#{i + 1}" for i in range(panel.qty)]


def _pack(
    panels: list[Panel], thickness: float, material: str | None, rules: ConstructionRules, rotation: bool
) -> list[Sheet]:
    usable_l = rules.sheet_length_mm - 2 * rules.sheet_trim_mm
    usable_w = rules.sheet_width_mm - 2 * rules.sheet_trim_mm
    kerf = rules.kerf_mm
    packer = newPacker(
        mode=PackingMode.Offline, bin_algo=PackingBin.Global, pack_algo=GuillotineBssfSas, rotation=rotation
    )
    packer.add_bin(math.floor(usable_l), math.floor(usable_w), count=_MAX_SHEETS)
    pieces: dict[int, tuple[str, Panel]] = {}
    unplaced: list[str] = []
    for panel in panels:
        length, width = panel.length_mm or 0.0, panel.width_mm or 0.0
        fits = (length <= usable_l and width <= usable_w) or (rotation and width <= usable_l and length <= usable_w)
        for part_id in piece_ids(panel):
            if not fits or length <= 0 or width <= 0:
                unplaced.append(part_id)
                continue
            rid = len(pieces)
            pieces[rid] = (part_id, panel)
            packer.add_rect(math.ceil(length + kerf), math.ceil(width + kerf), rid=rid)
    packer.pack()

    sheets: dict[int, Sheet] = {}
    placed: set[int] = set()
    for bin_index, x, y, w, _h, rid in packer.rect_list():
        part_id, panel = pieces[rid]
        length, width = panel.length_mm or 0.0, panel.width_mm or 0.0
        rotated = w != math.ceil(length + kerf)
        sheet = sheets.setdefault(
            bin_index,
            Sheet(thickness_mm=thickness, material=material, length=rules.sheet_length_mm, width=rules.sheet_width_mm),
        )
        sheet.placements.append(
            Placement(
                part_id=part_id,
                name=panel.name or "",
                x=x + rules.sheet_trim_mm,
                y=y + rules.sheet_trim_mm,
                w=width if rotated else length,
                h=length if rotated else width,
                rotated=rotated,
            )
        )
        placed.add(rid)
    unplaced += [pieces[rid][0] for rid in pieces if rid not in placed]
    result = [sheets[k] for k in sorted(sheets)]
    if unplaced:
        if not result:
            result.append(
                Sheet(
                    thickness_mm=thickness, material=material, length=rules.sheet_length_mm, width=rules.sheet_width_mm
                )
            )
        result[0].unplaced = unplaced
    return result


def nest_cut_list(cut_list: list[Panel], rules: ConstructionRules) -> list[Sheet]:
    """Nest per (thickness, board type); grain-oriented panels ("L") are packed without rotation."""
    groups: dict[tuple[float, str | None], list[Panel]] = {}
    for panel in cut_list:
        groups.setdefault((panel.thickness_mm or 0.0, panel.board_type), []).append(panel)
    sheets: list[Sheet] = []
    for (thickness, material), panels in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        free = [p for p in panels if p.grain_direction != "L"]
        grained = [p for p in panels if p.grain_direction == "L"]
        if free:
            sheets += _pack(free, thickness, material, rules, rotation=True)
        if grained:
            sheets += _pack(grained, thickness, material, rules, rotation=False)
    return sheets


def sheets_to_svg(sheets: list[Sheet]) -> str:
    """One sheet per row at 0.2 px/mm; every placed part is a ``<rect data-part=...>`` with a label."""
    s = _SVG_SCALE
    width = max((sheet.length for sheet in sheets), default=0) * s + 2 * _SVG_GAP
    y = float(_SVG_GAP)
    body: list[str] = []
    for index, sheet in enumerate(sheets, start=1):
        title = f"Sheet {index}: {sheet.thickness_mm:g} mm {sheet.material or 'unspecified board'}"
        body.append(f'<text x="{_SVG_GAP}" y="{y - 8:.1f}" font-size="14">{escape(title)}</text>')
        body.append(
            f'<rect x="{_SVG_GAP}" y="{y:.1f}" width="{sheet.length * s:.1f}" height="{sheet.width * s:.1f}" '
            'fill="#f4efe6" stroke="#555" stroke-width="1"/>'
        )
        for p in sheet.placements:
            px, py, pw, ph = _SVG_GAP + p.x * s, y + p.y * s, p.w * s, p.h * s
            body.append(
                f'<rect data-part="{escape(p.part_id)}" x="{px:.1f}" y="{py:.1f}" width="{pw:.1f}" height="{ph:.1f}" '
                'fill="#d8c7a8" stroke="#333" stroke-width="0.6"/>'
            )
            label = f"{p.name} {max(p.w, p.h):g}x{min(p.w, p.h):g}"
            body.append(f'<text x="{px + 3:.1f}" y="{py + 11:.1f}" font-size="9">{escape(label)}</text>')
        y += sheet.width * s
        if sheet.unplaced:
            y += 16
            body.append(
                f'<text x="{_SVG_GAP}" y="{y:.1f}" font-size="12" fill="#c00">'
                f"Unplaced (larger than sheet): {escape(', '.join(sheet.unplaced))}</text>"
            )
        y += _SVG_GAP + 16
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{y:.0f}" '
        f'viewBox="0 0 {width:.0f} {y:.0f}" font-family="sans-serif">\n' + "\n".join(body) + "\n</svg>\n"
    )
