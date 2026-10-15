"""Deterministic module resolution.

The interpretation agent assigns roles to numbered dimension marks (``ModuleDraft``). This module turns
that draft into a dimensioned ``FurnitureModule`` and keeps track of where every number came from
(``Provenance``). From the provenance it derives the consistency issues, the cross-checks against the
other views of the same module, and the open questions that drive the feedback loop: the model is asked
once more, and whatever is still missing is asked of the user, whose answers (``Override``) are applied
on top of the draft.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from src.furniture.legend import legend_materials
from src.furniture.rules import ConstructionRules
from src.furniture.schema import (
    CabinetKind,
    Cell,
    Column,
    ColumnDraft,
    DimensionChain,
    DimensionMark,
    FurnitureModule,
    Issue,
    MaterialSpec,
    ModuleDraft,
    ModuleModel,
    ModuleView,
    Orientation,
    Override,
    Provenance,
    Question,
    QuestionReason,
    TextRun,
    UncertainItem,
    Unit,
    ViewCheck,
    ViewKind,
)
from src.schema.enums import ConfidenceLevel
from src.schema.extraction import BoundingBox

# Snapping a free value to a printed mark: same number, label within this page distance of the view.
_SNAP_VALUE_TOLERANCE = 0.05
_SNAP_MAX_DISTANCE = 0.08
# Dimension chain taken for unlabelled columns/rows: every member within this page distance of the view.
_CHAIN_VIEW_MARGIN = 0.04
# Marks and texts belong to the nearest view of the module within this page distance.
_VIEW_MARGIN = 0.06
_TEXT_MARGIN = 0.04
_SUM_TOLERANCE_MM = 5
# Another view showing an overall dimension within this ratio of the expected one, but not equal to it,
# is a contradiction; further off it measures something else.
_VIEW_MISMATCH_RATIO = 0.15
_NICHE_WIDTHS_MM = (450, 600, 900)
_NICHE_TOLERANCE_MM = 10
_SHELF_SPAN_MAX_MM = 900
_SINGLE_DOOR_MAX_MM = 600
_MAX_CANDIDATES = 12

MAIN_DIMENSIONS = ("width", "height", "depth", "plinth")
# Sources that are readings of the drawing (or the user), as opposed to guesses and defaults.
_READ_SOURCES: frozenset[Provenance] = frozenset({"mark", "model", "user", "cross_view", "derived"})
_SHARED_SOURCES: frozenset[Provenance] = frozenset({"derived", "estimated", "missing"})
# Question reasons worth another model run; "assumed" values (depth, plinth) are rule defaults.
REFINE_REASONS = frozenset({"missing", "estimated", "unknown_material"})
# Review items that need the user's attention (the rest are checks of plausible values).
ATTENTION_REASONS = frozenset({"missing", "unknown_material", "conflict", "model_unsure"})
_FRONT_VIEWS: frozenset[ViewKind] = frozenset({"front_closed", "front_open", "interior"})
_VIEW_DIMENSIONS: dict[ViewKind, tuple[tuple[str, Orientation], ...]] = {
    "front_closed": (("width", "horizontal"), ("height", "vertical")),
    "front_open": (("width", "horizontal"), ("height", "vertical")),
    "interior": (("width", "horizontal"), ("height", "vertical")),
    "side": (("height", "vertical"), ("depth", "horizontal")),
    "section": (("height", "vertical"), ("depth", "horizontal")),
    "plan": (("width", "horizontal"), ("depth", "vertical")),
    "detail": (),
}
# Direction in which a view shows the module depth.
_DEPTH_DIRECTION: dict[ViewKind, Orientation] = {"side": "horizontal", "section": "horizontal", "plan": "vertical"}
_LABELS = {"width": "Module width", "height": "Module height", "depth": "Depth", "plinth": "Plinth height"}

Resolved = tuple[float | None, str | None, Provenance]
CheckStatus = Literal["match", "mismatch", "absent"]


def slugify_module_id(raw: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "-", raw.upper()).strip("-")
    return slug or "MODULE"


def _normalize_mark_id(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip().upper()
    return f"D{text}" if text.isdigit() else text


def _ids(*values: str | None) -> list[str]:
    return [v for v in values if v]


def _factor(units: Unit) -> float:
    return 10.0 if units == "cm" else 1.0


def _centre_distance(box: BoundingBox, target: BoundingBox) -> float:
    cx, cy = (box.x_min + box.x_max) / 2, (box.y_min + box.y_max) / 2
    dx = max(target.x_min - cx, 0.0, cx - target.x_max)
    dy = max(target.y_min - cy, 0.0, cy - target.y_max)
    return math.hypot(dx, dy)


def _expand(box: BoundingBox, margin: float) -> BoundingBox:
    return BoundingBox(
        x_min=max(0.0, box.x_min - margin),
        y_min=max(0.0, box.y_min - margin),
        x_max=min(1.0, box.x_max + margin),
        y_max=min(1.0, box.y_max + margin),
        label=box.label,
    )


def module_region(module: FurnitureModule | ModuleDraft, margin: float) -> BoundingBox:
    """Union of the module's main view and its other views, expanded by ``margin``."""
    boxes = [module.view_bbox, *(v.bbox for v in module.views)]
    union = BoundingBox(
        x_min=min(b.x_min for b in boxes),
        y_min=min(b.y_min for b in boxes),
        x_max=max(b.x_max for b in boxes),
        y_max=max(b.y_max for b in boxes),
    )
    return _expand(union, margin)


def _all_views(view_bbox: BoundingBox, views: list[ModuleView]) -> list[ModuleView]:
    return [ModuleView(kind="front_closed", bbox=view_bbox, label="main view"), *views]


def _view_owner(marks: list[DimensionMark], views: list[ModuleView]) -> dict[str, int]:
    """Mark id → index of the nearest view (within the view margin)."""
    owner: dict[str, int] = {}
    for mark in marks:
        distances = [(_centre_distance(mark.bbox, v.bbox), i) for i, v in enumerate(views)]
        distance, index = min(distances)
        if distance <= _VIEW_MARGIN:
            owner[mark.id] = index
    return owner


def _view_extent(
    marks: list[DimensionMark],
    chains: list[DimensionChain],
    orientation: Orientation,
    factor: float,
    view_bbox: BoundingBox,
) -> list[tuple[float, str]]:
    """Candidate OVERALL dimensions of a view in mm: sums of dimension chains lying wholly in it, their
    overall marks, and single dimensions labelled outside the drawing (an overall dimension line).

    Chain members (parts) and labels inside the view (internal openings) are not overall dimensions.
    """
    ids = {m.id for m in marks}
    in_chain = {i for c in chains for i in c.mark_ids}
    overall_ids = {c.overall_mark_id for c in chains if c.overall_mark_id}
    found = [
        (m.value * factor, m.id)
        for m in marks
        if m.orientation == orientation
        and m.id not in in_chain
        and (m.id in overall_ids or _centre_distance(m.bbox, view_bbox) > 0)
    ]
    found += [
        (c.sum_value * factor, c.mark_ids[0]) for c in chains if c.orientation == orientation and set(c.mark_ids) <= ids
    ]
    return found


def _adjacent_chain(
    chains: list[DimensionChain],
    marks_by_id: dict[str, DimensionMark],
    view: BoundingBox,
    orientation: Orientation,
    sizes: set[int],
) -> DimensionChain | None:
    """The chain of ``orientation`` with a member count in ``sizes`` lying closest along the view."""
    fits: list[tuple[float, DimensionChain]] = []
    for chain in chains:
        if chain.orientation != orientation or len(chain.mark_ids) not in sizes:
            continue
        distances = [_centre_distance(marks_by_id[i].bbox, view) for i in chain.mark_ids]
        if max(distances) <= _CHAIN_VIEW_MARGIN:
            fits.append((sum(distances) / len(distances), chain))
    return min(fits, key=lambda f: f[0])[1] if fits else None


class _Resolver:
    def __init__(self, marks: list[DimensionMark], units: Unit, view: BoundingBox) -> None:
        self.marks = marks
        self.by_id = {m.id: m for m in marks}
        self.factor = _factor(units)
        self.view = view

    def from_mark(self, mark_id: str) -> Resolved:
        return self.by_id[mark_id].value * self.factor, mark_id, "mark"

    def chain_total(self, chain: DimensionChain) -> Resolved:
        """The chain's overall mark when it agrees with the sum, else the bare sum."""
        if chain.consistent and chain.overall_mark_id:
            return self.from_mark(chain.overall_mark_id)
        return chain.sum_value * self.factor, None, "derived"

    def snap(self, value: float, orientation: Orientation | None) -> DimensionMark | None:
        """The mark printing ``value`` (same orientation) closest to the module view, if near enough."""
        candidates = [
            (_centre_distance(m.bbox, self.view), m)
            for m in self.marks
            if abs(m.value - value) <= _SNAP_VALUE_TOLERANCE and orientation in (None, m.orientation)
        ]
        candidates = [c for c in candidates if c[0] <= _SNAP_MAX_DISTANCE]
        return min(candidates, key=lambda c: c[0])[1] if candidates else None

    def resolve(self, mark: str | None, value: float | None, orientation: Orientation | None = None) -> Resolved:
        """From the referenced mark, else from the free value (snapped to a matching printed mark).

        Models often answer with the printed number instead of the mark id; such a value is snapped
        to the matching mark next to the module view so it stays traceable to the text layer.
        """
        mark_id = _normalize_mark_id(mark)
        if mark_id in self.by_id:
            return self.from_mark(mark_id)
        if value is None or value <= 0:
            return None, None, "missing"
        snapped = self.snap(value, orientation)
        if snapped:
            return self.from_mark(snapped.id)
        return value * self.factor, None, "model"


def _share(values: list[Resolved], total: float) -> list[Resolved]:
    """Unknown entries split what the known ones leave of ``total``: exact for one, a guess for several."""
    unknown = [i for i, (v, _, _) in enumerate(values) if v is None]
    if not unknown:
        return values
    rest = max(0.0, total - sum(v for v, _, _ in values if v is not None))
    if total <= 0:
        share: Resolved = (0.0, None, "missing")
    else:
        share = (rest / len(unknown), None, "derived" if len(unknown) == 1 else "estimated")
    return [share if i in unknown else value for i, value in enumerate(values)]


def _side_view_depth(
    marks: list[DimensionMark],
    chains: list[DimensionChain],
    view_bbox: BoundingBox,
    views: list[ModuleView],
    units: Unit,
) -> Resolved | None:
    """Depth read from a side view, section or plan of the module, when they agree on one overall value.

    Several different overall dimensions across the depth (e.g. an adjacent element) are ambiguous: the
    depth then stays a default for the user to confirm.
    """
    all_views = _all_views(view_bbox, views)
    owner = _view_owner(marks, all_views)
    found: list[tuple[float, str]] = []
    for index, view in enumerate(all_views):
        orientation = _DEPTH_DIRECTION.get(view.kind)
        if orientation is not None:
            view_marks = [m for m in marks if owner.get(m.id) == index]
            found += _view_extent(view_marks, chains, orientation, _factor(units), view.bbox)
    if not found:
        return None
    value, mark_id = max(found)
    if any(abs(other - value) > _SUM_TOLERANCE_MM for other, _ in found):
        return None
    return value, mark_id, "cross_view"


# Most specific furniture first: desks and kitchens are often clad, so cladding words only decide alone.
_KIND_PATTERNS: tuple[tuple[CabinetKind, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(pattern, re.I))
    for kind, pattern in (
        ("reception_desk", r"\blad(?:a|y|ą|zie)\b|recepcj"),
        ("kitchen", r"kuchn|\bzlew|zmywark|coffee point"),
        ("wardrobe", r"\bszaf(?:a|y|ie)\b|garderob|drąż|drazek|lokers|locker"),
        ("bench", r"siedzisk|\bławk|\blawk"),
        ("shelving", r"\bregał|\bregal\b|półki otwarte|polki otwarte"),
        ("wall_cladding", r"lamel|okładzin|okladzin|panel[ey]? (?:ścienn|scienn)"),
    )
)


def _infer_kind(draft: ModuleDraft, texts: list[TextRun]) -> CabinetKind:
    """Fallback when the model gave no kind: title, then the texts printed around the module, then the model notes
    (free reasoning such as "cladding, not a wardrobe" mentions other kinds, so it decides last)."""
    for description in (draft.title, " ".join(t.text for t in texts), " ".join(draft.notes)):
        kind = next((kind for kind, pattern in _KIND_PATTERNS if pattern.search(description)), None)
        if kind:
            return kind
    return "cabinet"


# Cabinet codes as printed on drawings and quotes: ZM03, ZK01, ZM 05 (not legend symbols such as D8).
_CABINET_CODE = re.compile(r"\b([A-Z]{2,4})[- ]?(\d{2,3})\b", re.I)


def _group_id(draft: ModuleDraft) -> str:
    if draft.group_id:
        return draft.group_id.strip().upper()
    for text in (draft.module_id, draft.title, *draft.notes):
        code = _CABINET_CODE.search(text.strip())
        if code:
            return f"{code.group(1)}{code.group(2)}".upper()
    return ""


def _resolve(
    draft: ModuleDraft,
    marks: list[DimensionMark],
    chains: list[DimensionChain],
    units: Unit,
    file_name: str,
    page_index: int,
    rules: ConstructionRules,
    texts: list[TextRun],
) -> FurnitureModule:
    resolver = _Resolver(marks, units, draft.view_bbox)
    width = resolver.resolve(draft.width_mark, draft.width_value, "horizontal")
    height = resolver.resolve(draft.height_mark, draft.height_value, "vertical")
    depth = resolver.resolve(draft.depth_mark, draft.depth_value)
    plinth = resolver.resolve(draft.plinth_mark, draft.plinth_value, "vertical")

    # Columns/rows the model left without any dimension take the members of the dimension chain
    # running along the view, when the chain has exactly one member per column/row (+1 for the plinth).
    widths = [resolver.resolve(c.width_mark, c.width_value, "horizontal") for c in draft.columns]
    if len(draft.columns) >= 2 and all(w[0] is None for w in widths):
        column_chain = _adjacent_chain(chains, resolver.by_id, draft.view_bbox, "horizontal", {len(draft.columns)})
        if column_chain:
            widths = [resolver.from_mark(i) for i in column_chain.mark_ids]
            if width[0] is None:
                width = resolver.chain_total(column_chain)

    row_counts = {
        len(c.cells)
        for c in draft.columns
        if c.cells and all(cd.height_mark is None and cd.height_value is None for cd in c.cells)
    }
    row_chain = _adjacent_chain(
        chains, resolver.by_id, draft.view_bbox, "vertical", {n for count in row_counts for n in (count, count + 1)}
    )
    if row_chain:
        if height[0] is None:
            height = resolver.chain_total(row_chain)
        if plinth[0] is None and len(row_chain.mark_ids) - 1 in row_counts:
            plinth = resolver.from_mark(row_chain.mark_ids[-1])
    if depth[0] is None:
        depth = _side_view_depth(marks, chains, draft.view_bbox, draft.views, units) or (
            rules.depth_default_mm,
            None,
            "default",
        )
    if plinth[0] is None:
        plinth = (rules.plinth_default_mm, None, "default")

    total_width = width[0] or 0.0
    carcass_height = max(0.0, (height[0] or 0.0) - (plinth[0] or 0.0)) if height[0] else 0.0
    column_drafts = draft.columns or [ColumnDraft()]
    widths = _share(widths or [(None, None, "missing")], total_width)

    columns: list[Column] = []
    for index, (column_draft, (col_width, col_mark, col_source)) in enumerate(zip(column_drafts, widths, strict=True)):
        cell_drafts = sorted(column_draft.cells, key=lambda c: c.row)
        heights = [resolver.resolve(c.height_mark, c.height_value, "vertical") for c in cell_drafts]
        fits_chain = row_chain and cell_drafts and len(row_chain.mark_ids) - len(cell_drafts) in (0, 1)
        if fits_chain and all(h[0] is None for h in heights):
            heights = [resolver.from_mark(i) for i in row_chain.mark_ids[: len(cell_drafts)]]
        heights = _share(heights, carcass_height)
        cells = [
            Cell(
                id=f"c{index}r{row}",
                col=index,
                row=row,
                front=cd.front,
                height_mm=h or 0.0,
                height_source=source,
                shelves=max(0, cd.shelves),
                material_key=cd.material_key,
                label=cd.label,
                source_mark_ids=_ids(mark),
            )
            for row, (cd, (h, mark, source)) in enumerate(zip(cell_drafts, heights, strict=True))
        ]
        if not cells:
            cells = [
                Cell(
                    id=f"c{index}r0",
                    col=index,
                    row=0,
                    front="open",
                    height_mm=carcass_height,
                    height_source="derived" if carcass_height > 0 else "missing",
                )
            ]
        columns.append(
            Column(
                index=index,
                width_mm=col_width or 0.0,
                width_source=col_source,
                cells=cells,
                source_mark_ids=_ids(col_mark),
            )
        )

    dims = {"width": width, "height": height, "depth": depth, "plinth": plinth}
    region = module_region(draft, _TEXT_MARGIN)
    nearby_texts = [t for t in texts if _centre_distance(t.bbox, region) == 0]
    parsed_legend = legend_materials(nearby_texts) or legend_materials(texts)
    legend = draft.materials or parsed_legend
    kind = draft.kind or _infer_kind(draft, nearby_texts)
    group_id = _group_id(draft)
    return FurnitureModule(
        module_id=slugify_module_id(draft.module_id),
        title=draft.title,
        file_name=file_name,
        page_index=page_index,
        view_bbox=draft.view_bbox,
        views=draft.views,
        units=units,
        width_mm=width[0] or 0.0,
        height_mm=height[0] or 0.0,
        depth_mm=depth[0] or 0.0,
        depth_source="assumed" if depth[2] == "default" else "drawing",
        plinth_mm=plinth[0] or 0.0,
        carcass_material_key=draft.carcass_material_key,
        front_material_key=draft.front_material_key,
        columns=columns,
        marks=marks,
        chains=chains,
        texts=nearby_texts,
        materials=draft.materials or legend,
        legend_materials=parsed_legend,
        group_id=group_id,
        kind=kind,
        notes=draft.notes,
        source_mark_ids={k: v[1] for k, v in dims.items()},
        dimension_sources={k: v[2] for k, v in dims.items()},
        model_uncertain={
            target: item.reason or "Flagged by the model."
            for item in draft.uncertain
            if (target := _uncertain_target(item, columns))
        },
    )


def resolve_module(
    draft: ModuleDraft,
    marks: list[DimensionMark],
    chains: list[DimensionChain],
    units: Unit,
    file_name: str,
    page_index: int,
    rules: ConstructionRules,
    texts: list[TextRun] | None = None,
) -> FurnitureModule:
    """Turn the agent's role assignment into a dimensioned module with issues, checks and questions."""
    return finalize_module(_resolve(draft, marks, chains, units, file_name, page_index, rules, texts or []), rules)


def assemble_module(
    draft: ModuleDraft, base: FurnitureModule, overrides: list[Override], rules: ConstructionRules
) -> FurnitureModule:
    """Re-resolve ``draft`` in the page context of ``base`` and apply the user's overrides on top."""
    module = _resolve(draft, base.marks, base.chains, base.units, base.file_name, base.page_index, rules, base.texts)
    module.module_id = base.module_id
    module.view_checks = [c for c in base.view_checks if c.source == "drawing"]
    return finalize_module(apply_overrides(module, overrides, rules), rules)


# ── Checks ──────────────────────────────────────────────────────────────────


def validate_module(module: FurnitureModule, rules: ConstructionRules | None = None) -> list[Issue]:
    """Geometry, plausibility and material checks of the module layout."""
    issues: list[Issue] = []
    if module.status == "ok" and module.columns:
        total = sum(c.width_mm for c in module.columns)
        if abs(total - module.width_mm) > _SUM_TOLERANCE_MM:
            issues.append(
                Issue(
                    code="COLUMNS_NOT_SUM",
                    severity="error",
                    message=f"Column widths sum to {total:g} mm but the module is {module.width_mm:g} mm wide.",
                    mark_ids=[
                        *_ids(module.source_mark_ids.get("width")),
                        *(m for c in module.columns for m in c.source_mark_ids),
                    ],
                )
            )
        carcass = module.height_mm - module.plinth_mm
        for column in module.columns:
            rows = sum(cell.height_mm for cell in column.cells)
            if column.cells and abs(rows - carcass) > _SUM_TOLERANCE_MM:
                issues.append(
                    Issue(
                        code="ROWS_NOT_SUM",
                        severity="error",
                        message=f"Column {column.index + 1}: cell heights sum to {rows:g} mm, "
                        f"carcass height (height - plinth) is {carcass:g} mm.",
                        mark_ids=[m for cell in column.cells for m in cell.source_mark_ids],
                        cell_ids=[cell.id for cell in column.cells],
                    )
                )
    for column in module.columns:
        for cell in column.cells:
            if cell.front == "niche_appliance" and not any(
                abs(column.width_mm - w) <= _NICHE_TOLERANCE_MM for w in _NICHE_WIDTHS_MM
            ):
                issues.append(
                    Issue(
                        code="NICHE_NONSTANDARD",
                        severity="warning",
                        message=f"Appliance niche {column.width_mm:g} mm wide; standard widths are 450/600/900 mm.",
                        mark_ids=column.source_mark_ids,
                        cell_ids=[cell.id],
                    )
                )
            if cell.shelves > 0 and column.width_mm > _SHELF_SPAN_MAX_MM:
                issues.append(
                    Issue(
                        code="SHELF_SPAN",
                        severity="warning",
                        message=f"Shelf span {column.width_mm:g} mm exceeds {_SHELF_SPAN_MAX_MM} mm; risk of sagging.",
                        mark_ids=column.source_mark_ids,
                        cell_ids=[cell.id],
                    )
                )

            if cell.front in ("door_left", "door_right") and column.width_mm > _SINGLE_DOOR_MAX_MM:
                issues.append(
                    Issue(
                        code="DOOR_TOO_WIDE",
                        severity="warning",
                        message=f"Single door {column.width_mm:g} mm wide exceeds {_SINGLE_DOOR_MAX_MM} mm.",
                        mark_ids=column.source_mark_ids,
                        cell_ids=[cell.id],
                    )
                )
    known = {m.key for m in [*module.materials, *module.legend_materials]}
    referenced = [
        ("carcass", module.carcass_material_key, []),
        ("front", module.front_material_key, []),
        *(("cell", cell.material_key, [cell.id]) for column in module.columns for cell in column.cells),
    ]
    for what, key, cell_ids in referenced:
        if key and key not in known:
            issues.append(
                Issue(
                    code="MATERIAL_UNKNOWN",
                    severity="warning",
                    message=f"{what.capitalize()} material {key!r} is not in the legend.",
                    cell_ids=cell_ids,
                )
            )
    if rules:
        issues.extend(_dimension_implausible(module, rules))
    return issues


def _material_candidates(module: FurnitureModule, part: str) -> list[MaterialSpec]:
    materials = module.legend_materials or module.materials
    terms = ("laminat", "płyta", "plyta", "korpus") if part == "carcass" else ("front", "fronty")

    def score(material: MaterialSpec) -> tuple[int, int]:
        name = material.name.casefold()
        decor = bool(re.search(r"\b(?:[A-Z]\d{3,4}|K\d{3}\s*[A-Z0-9]*|RAL\s*\d{4})\b", material.name, re.I))
        return sum(term in name for term in terms), int(decor)

    return sorted(materials, key=score, reverse=True)


def _dimension_implausible(module: FurnitureModule, rules: ConstructionRules) -> list[Issue]:
    checks = [
        ("width", module.width_mm, rules.plausibility.width_max_mm, 0),
        ("height", module.height_mm, rules.plausibility.height_max_mm, rules.plausibility.height_min_mm),
        (
            "depth",
            module.depth_mm,
            rules.plausibility.depth_max_mm,
            rules.plausibility.wall_cladding_depth_min_mm
            if module.kind == "wall_cladding"
            else rules.plausibility.depth_min_mm,
        ),
        ("plinth", module.plinth_mm, rules.plausibility.plinth_max_mm, 0),
    ]
    issues = [
        Issue(
            code="DIMENSION_IMPLAUSIBLE",
            severity="warning",
            message=f"{name.capitalize()} {value:g} mm is outside expected {lower:g}-{upper:g} mm."
            if upper > 0
            else f"{name.capitalize()} {value:g} mm is below expected {lower:g} mm.",
            mark_ids=_ids(module.source_mark_ids.get(name)),
        )
        for name, value, upper, lower in checks
        if value > 0 and ((upper > 0 and value > upper) or value < lower)
    ]
    issues += [
        Issue(
            code="DIMENSION_IMPLAUSIBLE",
            severity="warning",
            message=f"Column {column.index + 1} width {column.width_mm:g} mm is below "
            f"{rules.plausibility.column_width_min_mm:g} mm.",
            mark_ids=column.source_mark_ids,
        )
        for column in module.columns
        if 0 < column.width_mm < rules.plausibility.column_width_min_mm
    ]
    issues += [
        Issue(
            code="DIMENSION_IMPLAUSIBLE",
            severity="warning",
            message=f"Cell {cell.id} height {cell.height_mm:g} mm is below "
            f"{rules.plausibility.cell_height_min_mm:g} mm.",
            mark_ids=cell.source_mark_ids,
            cell_ids=[cell.id],
        )
        for column in module.columns
        for cell in column.cells
        if 0 < cell.height_mm < rules.plausibility.cell_height_min_mm
    ]
    return issues


def _dimension_value(module: FurnitureModule, dimension: str) -> tuple[float, Provenance, str | None]:
    """(mm, provenance, source mark) of width/height/depth/plinth, ``col:<i>:width`` or ``cell:<id>:height``."""
    if dimension in MAIN_DIMENSIONS:
        return (
            getattr(module, f"{dimension}_mm"),
            module.dimension_sources.get(dimension, "user"),
            module.source_mark_ids.get(dimension),
        )
    kind, key, _ = dimension.split(":")
    if kind == "col":
        column = module.columns[int(key)]
        return column.width_mm, column.width_source, next(iter(column.source_mark_ids), None)
    cell = next(c for col in module.columns for c in col.cells if c.id == key)
    return cell.height_mm, cell.height_source, next(iter(cell.source_mark_ids), None)


def _compare(expected: float, found: float, *, structural: bool) -> CheckStatus:
    """match / mismatch / absent; a structurally corresponding dimension can only match or mismatch."""
    delta = abs(expected - found)
    if delta <= _SUM_TOLERANCE_MM:
        return "match"
    if structural or delta <= _VIEW_MISMATCH_RATIO * expected:
        return "mismatch"
    return "absent"


def page_view_checks(module: FurnitureModule) -> list[ViewCheck]:
    """Compare the module's dimensions with the other views of it on the same page.

    Overall width/height/depth are compared with the overall dimensions the view shows in that direction
    (chain sums, overall marks, single dimensions labelled outside the drawing). Column widths and cell
    heights are compared member by member with a chain of a view shown to be the same element.
    """
    if not module.views or not module.marks:
        return []
    factor = _factor(module.units)
    views = _all_views(module.view_bbox, module.views)
    owner = _view_owner(module.marks, views)
    checks: list[ViewCheck] = []
    for index, view in enumerate(views[1:], start=1):
        where = f"{view.kind} view" + (f" '{view.label}'" if view.label else "")
        view_marks = [m for m in module.marks if owner.get(m.id) == index]
        overall_status: dict[str, CheckStatus] = {}
        for dimension, orientation in _VIEW_DIMENSIONS[view.kind]:
            expected, source, source_mark = _dimension_value(module, dimension)
            # A value read in this very view is not an independent confirmation of itself.
            if source not in _READ_SOURCES or expected <= 0 or owner.get(source_mark or "") == index:
                continue
            others = [m for m in view_marks if m.id != source_mark]
            extent = _view_extent(others, module.chains, orientation, factor, view.bbox)
            exact = [e for e in extent if abs(e[0] - expected) <= _SUM_TOLERANCE_MM]
            found_mm, mark_id = exact[0] if exact else max(extent, default=(None, None))
            status: CheckStatus = "absent" if found_mm is None else _compare(expected, found_mm, structural=False)
            overall_status[dimension] = status
            checks.append(
                ViewCheck(
                    dimension=dimension,
                    expected_mm=expected,
                    where=where,
                    source="view",
                    status=status,
                    found_mm=found_mm if status != "absent" else None,
                    mark_id=mark_id if status != "absent" else None,
                )
            )
        if view.kind not in _FRONT_VIEWS:
            continue
        ids = {m.id for m in view_marks}
        view_chains = [c for c in module.chains if set(c.mark_ids) <= ids]
        by_id = {m.id: m for m in module.marks}
        targets: list[tuple[str, str]] = []
        # Member-by-member comparison only in a view shown to be the same element (its overall size
        # matches); otherwise a neighbouring module the view was mistaken for would raise false alarms.
        column_chain = next(
            (c for c in view_chains if c.orientation == "horizontal" and len(c.mark_ids) == len(module.columns) > 1),
            None,
        )
        if column_chain and overall_status.get("width") == "match":
            targets += [(f"col:{i}:width", mark_id) for i, mark_id in enumerate(column_chain.mark_ids)]
        if overall_status.get("height") == "match" and module.width_mm > 0:
            # Each vertical chain describes the rows of the column it stands next to (columns are laid out
            # proportionally across the view).
            starts = [sum(c.width_mm for c in module.columns[:i]) / module.width_mm for i in range(len(module.columns))]
            span = view.bbox.x_max - view.bbox.x_min
            ranges = [
                (view.bbox.x_min + start * span, view.bbox.x_min + (start + col.width_mm / module.width_mm) * span)
                for start, col in zip(starts, module.columns, strict=True)
            ]
            for chain in (c for c in view_chains if c.orientation == "vertical"):
                cx = sum((by_id[i].bbox.x_min + by_id[i].bbox.x_max) / 2 for i in chain.mark_ids) / len(chain.mark_ids)
                nearest = min(range(len(ranges)), key=lambda i: max(ranges[i][0] - cx, 0.0, cx - ranges[i][1]))
                column = module.columns[nearest]
                if len(column.cells) > 1 and len(chain.mark_ids) - len(column.cells) in (0, 1):
                    targets += [
                        (f"cell:{cell.id}:height", mark_id)
                        for cell, mark_id in zip(column.cells, chain.mark_ids, strict=False)
                    ]
        for dimension, mark_id in targets:
            expected, source, source_mark = _dimension_value(module, dimension)
            if source not in _READ_SOURCES or mark_id == source_mark:
                continue
            found_mm = by_id[mark_id].value * factor
            checks.append(
                ViewCheck(
                    dimension=dimension,
                    expected_mm=expected,
                    where=where,
                    source="view",
                    status=_compare(expected, found_mm, structural=True),
                    found_mm=found_mm,
                    mark_id=mark_id,
                )
            )
    return checks


def _used_mark_ids(module: FurnitureModule) -> set[str]:
    used = set(_ids(*module.source_mark_ids.values()))
    used.update(m for c in module.columns for m in c.source_mark_ids)
    used.update(m for c in module.columns for cell in c.cells for m in cell.source_mark_ids)
    return used


def _candidates(module: FurnitureModule, orientation: Orientation, kinds: frozenset[ViewKind]) -> list[str]:
    """Unused marks of ``orientation`` belonging to the module's views of ``kinds``, nearest first."""
    views = _all_views(module.view_bbox, module.views)
    owner = _view_owner(module.marks, views)
    used = _used_mark_ids(module)
    found = [
        (_centre_distance(m.bbox, module.view_bbox), m.id)
        for m in module.marks
        if m.orientation == orientation and m.id not in used and m.id in owner and views[owner[m.id]].kind in kinds
    ]
    return [mark_id for _, mark_id in sorted(found)[:_MAX_CANDIDATES]]


_DIMENSION_ORIENTATION: dict[str, Orientation] = {"width": "horizontal", "height": "vertical", "plinth": "vertical"}
# A target asked for several reasons is shown with the most important one.
_REASON_PRIORITY: tuple[QuestionReason, ...] = (
    "missing",
    "unknown_material",
    "conflict",
    "estimated",
    "model_unsure",
    "unverified",
    "assumed",
)


def _cell_lookup(module: FurnitureModule) -> dict[str, tuple[Column, Cell]]:
    return {cell.id: (column, cell) for column in module.columns for cell in column.cells}


def _question(module: FurnitureModule, target: str, reason: QuestionReason, note: str) -> Question | None:
    """Question for an override target in the module's current state; None if the target no longer exists."""

    def make(**fields) -> Question:
        return Question(id=target, reason=reason, note=note, **fields)

    if target in MAIN_DIMENSIONS:
        orientation = _DIMENSION_ORIENTATION.get(target)
        if target == "depth":
            candidates = _candidates(module, "horizontal", frozenset({"side", "section"})) + _candidates(
                module, "vertical", frozenset({"plan"})
            )
        else:
            candidates = _candidates(module, orientation or "horizontal", _FRONT_VIEWS)
        value = getattr(module, f"{target}_mm")
        return make(
            kind="dimension",
            label=_LABELS[target],
            orientation=orientation,
            current_mm=value if value > 0 else None,
            candidate_mark_ids=candidates[:_MAX_CANDIDATES],
        )
    if target in ("material:carcass", "material:front"):
        part = target.split(":")[1]
        key = module.carcass_material_key if part == "carcass" else module.front_material_key
        return make(
            kind="material",
            label=f"{part.capitalize()} material",
            current_value=key,
            candidate_values=[m.key for m in _material_candidates(module, part)],
        )
    parts = target.split(":")
    if len(parts) != 3:
        return None
    if parts[0] == "col":
        index = int(parts[1]) if parts[1].isdigit() else -1
        if not 0 <= index < len(module.columns):
            return None
        return make(
            kind="dimension",
            label=f"Column {index + 1} width",
            orientation="horizontal",
            current_mm=module.columns[index].width_mm,
            candidate_mark_ids=_candidates(module, "horizontal", _FRONT_VIEWS),
        )
    found = _cell_lookup(module).get(parts[1])
    if parts[0] != "cell" or found is None:
        return None
    column, cell = found
    place = f"Column {column.index + 1}, row {cell.row + 1}" + (f" ({cell.label})" if cell.label else "")
    if parts[2] == "height":
        return make(
            kind="dimension",
            label=f"{place} height",
            orientation="vertical",
            current_mm=cell.height_mm,
            cell_id=cell.id,
            candidate_mark_ids=_candidates(module, "vertical", _FRONT_VIEWS),
        )
    if parts[2] == "front":
        return make(kind="front", label=f"{place} front", current_value=cell.front, cell_id=cell.id)
    if parts[2] == "shelves":
        return make(kind="shelves", label=f"{place} shelves", current_value=str(cell.shelves), cell_id=cell.id)
    if parts[2] == "material":
        return make(
            kind="material",
            label=f"{place} front material",
            current_value=cell.material_key,
            cell_id=cell.id,
            candidate_values=[m.key for m in _material_candidates(module, "front")],
        )
    return None


def _uncertain_target(item: UncertainItem, columns: list[Column]) -> str | None:
    """Override target of a value the model flagged (1-based column/row → ids)."""
    simple = {
        "width": "width",
        "height": "height",
        "depth": "depth",
        "plinth": "plinth",
        "carcass_material": "material:carcass",
        "front_material": "material:front",
    }
    if item.field in simple:
        return simple[item.field]
    if item.column is None or not 1 <= item.column <= len(columns):
        return None
    if item.field == "column_width":
        return f"col:{item.column - 1}:width"
    cell_id = f"c{item.column - 1}r{(item.row or 1) - 1}"
    if not any(cell.id == cell_id for cell in columns[item.column - 1].cells):
        return None
    return f"cell:{cell_id}:{item.field.removeprefix('cell_')}"


def _conflict_targets(module: FurnitureModule) -> list[tuple[str, str]]:
    """(target, explanation) for values taking part in a contradiction."""
    sources = {target: mark for target, mark in module.source_mark_ids.items() if mark}
    sources.update({f"col:{c.index}:width": m for c in module.columns for m in c.source_mark_ids})
    sources.update(
        {f"cell:{cell.id}:height": m for c in module.columns for cell in c.cells for m in cell.source_mark_ids}
    )
    found: list[tuple[str, str]] = []
    for issue in module.issues:
        if issue.code == "COLUMNS_NOT_SUM":
            found += [("width", issue.message)] + [(f"col:{c.index}:width", issue.message) for c in module.columns]
        elif issue.code == "ROWS_NOT_SUM":
            found += [(f"cell:{cell_id}:height", issue.message) for cell_id in issue.cell_ids]
        elif issue.code == "DIMENSION_IMPLAUSIBLE":
            message = issue.message.casefold()
            dimension = next(
                (name for name in ("width", "height", "depth", "plinth") if message.startswith(name)), None
            )
            if dimension:
                found.append((dimension, issue.message))
            elif message.startswith("column "):
                match = re.match(r"column (\d+)", message)
                if match:
                    found.append((f"col:{int(match.group(1)) - 1}:width", issue.message))
            elif message.startswith("cell "):
                cell_id = issue.message.split()[1]
                found.append((f"cell:{cell_id}:height", issue.message))
    for check in module.view_checks:
        if check.status == "mismatch":
            found.append((check.dimension, f"{check.found_mm:g} mm in the {check.where}."))
    return found


def open_questions(module: FurnitureModule) -> list[Question]:
    """Everything worth a look: missing, guessed or defaulted values, unknown materials, values in a
    contradiction, values the model flagged, and values the model read that no printed mark confirms.

    Values the user has already set or confirmed are not asked again (unless still missing).
    """
    wanted: dict[str, tuple[QuestionReason, str]] = {}

    def want(target: str, reason: QuestionReason, note: str = "") -> None:
        if target in module.confirmed and reason != "missing":
            return
        current = wanted.get(target)
        if current is None or _REASON_PRIORITY.index(reason) < _REASON_PRIORITY.index(current[0]):
            wanted[target] = (reason, note)

    sources = module.dimension_sources
    for dimension in ("width", "height"):
        if sources.get(dimension) == "missing" or getattr(module, f"{dimension}_mm") <= 0:
            want(dimension, "missing")
    if module.width_mm > 0 and 0 < module.height_mm <= module.plinth_mm:
        note = f"Height {module.height_mm:g} mm is not above the {module.plinth_mm:g} mm plinth."
        want("height", "conflict", note)
        want("plinth", "conflict", note)
    for dimension in ("depth", "plinth"):
        if sources.get(dimension) == "default":
            want(dimension, "assumed", f"Construction default {getattr(module, f'{dimension}_mm'):g} mm.")
    for column in module.columns:
        if column.width_source == "estimated":
            want(f"col:{column.index}:width", "estimated", "Equal share of the width the other columns leave.")
        for cell in column.cells:
            if cell.height_source == "estimated":
                want(f"cell:{cell.id}:height", "estimated", "Equal share of the height the other rows leave.")
    known = {m.key for m in [*module.materials, *module.legend_materials]}
    has_fronts = any(c.front not in ("open", "niche_appliance") for col in module.columns for c in col.cells)
    for part, key, needed in (
        ("carcass", module.carcass_material_key, True),
        ("front", module.front_material_key, has_fronts),
    ):
        if needed and key not in known:
            want(f"material:{part}", "unknown_material")
    for target, note in _conflict_targets(module):
        want(target, "conflict", note)
    for target, reason in module.model_uncertain.items():
        want(target, "model_unsure", reason)
    if module.marks:
        unverified = "Read by the model; no printed dimension confirms it."
        for dimension in MAIN_DIMENSIONS:
            if sources.get(dimension) == "model":
                want(dimension, "unverified", unverified)
        for column in module.columns:
            if column.width_source == "model":
                want(f"col:{column.index}:width", "unverified", unverified)
            for cell in column.cells:
                if cell.height_source == "model":
                    want(f"cell:{cell.id}:height", "unverified", unverified)
    questions = [_question(module, target, reason, note) for target, (reason, note) in wanted.items()]
    return [q for q in questions if q is not None]


def finalize_module(module: FurnitureModule, rules: ConstructionRules | None = None) -> FurnitureModule:
    """Recompute status, issues, view checks, questions and confidence from the module's data."""
    m = module.model_copy(deep=True)
    sources = m.dimension_sources
    m.status = "ok" if m.width_mm > 0 and m.height_mm > m.plinth_mm else "incomplete"
    m.depth_source = "assumed" if sources.get("depth") == "default" else "drawing"
    issues: list[Issue] = []
    missing = [d for d in ("width", "height") if getattr(m, f"{d}_mm") <= 0]
    if missing:
        issues.append(
            Issue(
                code="DIMENSION_MISSING",
                severity="error",
                message=f"Module {' and '.join(missing)} could not be resolved; no 3D model is built.",
            )
        )
    elif m.height_mm <= m.plinth_mm:
        issues.append(
            Issue(
                code="DIMENSION_MISSING",
                severity="error",
                message=(
                    f"Height {m.height_mm:g} mm leaves no carcass above the {m.plinth_mm:g} mm plinth; "
                    "no 3D model is built."
                ),
            )
        )
    if sources.get("depth") == "default":
        issues.append(
            Issue(
                code="DEPTH_ASSUMED",
                severity="warning",
                message=f"Depth not shown on this page; assumed {m.depth_mm:g} mm.",
            )
        )
    used = _used_mark_ids(m)
    by_id = {mark.id: mark for mark in m.marks}
    for chain in m.chains:
        chain_marks = {*chain.mark_ids, *_ids(chain.overall_mark_id)}
        if chain.consistent is False and chain_marks & used:
            overall = by_id.get(chain.overall_mark_id or "")
            issues.append(
                Issue(
                    code="CHAIN_MISMATCH",
                    severity="error",
                    message=(
                        f"Dimension chain {chain.id} sums to {chain.sum_value:g} but the overall dimension "
                        f"reads {overall.value:g}."
                        if overall
                        else f"Dimension chain {chain.id} is inconsistent."
                    ),
                    mark_ids=sorted(chain_marks),
                )
            )
    if not m.marks and any(sources.get(d) == "model" for d in MAIN_DIMENSIONS):
        issues.append(
            Issue(
                code="UNVERIFIED_DIMENSIONS",
                severity="warning",
                message="Raster page: dimensions were read by the vision model and are not verified.",
            )
        )
    m.view_checks = page_view_checks(m) + [c for c in m.view_checks if c.source == "drawing"]
    for check in m.view_checks:
        if check.status == "mismatch":
            _, _, source_mark = _dimension_value(m, check.dimension)
            issues.append(
                Issue(
                    code="VIEW_MISMATCH",
                    severity="error",
                    message=(
                        f"{check.dimension} is {check.expected_mm:g} mm here but {check.found_mm:g} mm "
                        f"in the {check.where}."
                    ),
                    mark_ids=_ids(source_mark, check.mark_id),
                    cell_ids=[check.dimension.split(":")[1]] if check.dimension.startswith("cell:") else [],
                )
            )
    issues += validate_module(m, rules or ConstructionRules())
    m.issues = issues
    m.questions = open_questions(m)
    if any(i.severity == "error" for i in issues):
        m.confidence = ConfidenceLevel.LOW
    elif not issues and all(sources.get(d) == "mark" for d in MAIN_DIMENSIONS):
        m.confidence = ConfidenceLevel.HIGH
    else:
        m.confidence = ConfidenceLevel.MEDIUM
    return m


def gap_score(module: FurnitureModule) -> int:
    """How much is still unknown or contradictory; lower is better (compares model re-runs)."""
    weights = {
        "missing": 100,
        "conflict": 10,
        "estimated": 10,
        "unknown_material": 5,
        "model_unsure": 3,
        "unverified": 1,
        "assumed": 1,
    }
    errors = sum(1 for i in module.issues if i.severity == "error")
    return sum(weights[q.reason] for q in module.questions) + 20 * errors


def needs_refine(module: FurnitureModule) -> bool:
    return any(q.reason in REFINE_REASONS for q in module.questions)


# ── Overrides (user answers and edits) ──────────────────────────────────────


def _is_removal(override: Override) -> bool:
    fields = (
        override.value_mm,
        override.mark_id,
        override.material_key,
        override.material,
        override.front,
        override.kind,
        override.group_id,
    )
    return all(v is None for v in fields) and override.shelves is None


def merge_overrides(existing: list[Override], incoming: list[Override]) -> list[Override]:
    """Later overrides replace earlier ones of the same target; one without any value removes it."""
    merged = {o.target: o for o in existing}
    for override in incoming:
        if _is_removal(override):
            merged.pop(override.target, None)
        else:
            merged[override.target] = override
    return list(merged.values())


def _override_dimension(override: Override, module: FurnitureModule) -> tuple[float, str | None, Provenance]:
    if override.mark_id:
        mark = next((m for m in module.marks if m.id == _normalize_mark_id(override.mark_id)), None)
        if mark is None:
            raise ValueError(f"{override.target}: unknown mark {override.mark_id}")
        return mark.value * _factor(module.units), mark.id, "mark"
    if override.value_mm is not None and override.value_mm > 0:
        return override.value_mm, None, "user"
    raise ValueError(f"{override.target}: a positive value_mm or a mark_id is required")


def _override_material(override: Override, module: FurnitureModule) -> str:
    """Key of an existing legend material, or of the new one appended to the legend."""
    keys = {m.key for m in module.materials}
    if override.material_key:
        if override.material_key in keys:
            return override.material_key
        legend_entry = next((m for m in module.legend_materials if m.key == override.material_key), None)
        if legend_entry is None:
            raise ValueError(f"{override.target}: unknown material {override.material_key}")
        module.materials.append(legend_entry.model_copy())
        return legend_entry.key
    if override.material is None:
        raise ValueError(f"{override.target}: material_key or material is required")
    existing = next((m for m in module.materials if m.name == override.material.name), None)
    if existing:
        return existing.key
    n = len(keys) + 1
    while f"M{n}" in keys:
        n += 1
    module.materials.append(override.material.model_copy(update={"key": f"M{n}"}))
    return f"M{n}"


def apply_overrides(
    module: FurnitureModule, overrides: list[Override], rules: ConstructionRules | None = None
) -> FurnitureModule:
    """Apply user answers/edits, then re-split guessed column widths and cell heights around them.

    Raises ValueError for unknown targets, marks or materials.
    """
    m = module.model_copy(deep=True)
    cells = {c.id: c for col in m.columns for c in col.cells}
    for override in overrides:
        target = override.target
        parts = target.split(":")
        if target not in m.confirmed:
            m.confirmed.append(target)
        if target in MAIN_DIMENSIONS:
            value, mark_id, source = _override_dimension(override, m)
            setattr(m, f"{target}_mm", value)
            m.source_mark_ids[target] = mark_id
            m.dimension_sources[target] = source
        elif len(parts) == 3 and parts[0] == "col" and parts[2] == "width":
            if not parts[1].isdigit() or int(parts[1]) >= len(m.columns):
                raise ValueError(f"{target}: no such column")
            column = m.columns[int(parts[1])]
            column.width_mm, mark_id, column.width_source = _override_dimension(override, m)
            column.source_mark_ids = _ids(mark_id)
        elif len(parts) == 3 and parts[0] == "cell":
            cell = cells.get(parts[1])
            if cell is None:
                raise ValueError(f"{target}: no such cell")
            if parts[2] == "height":
                cell.height_mm, mark_id, cell.height_source = _override_dimension(override, m)
                cell.source_mark_ids = _ids(mark_id)
            elif parts[2] == "front" and override.front:
                cell.front = override.front
            elif parts[2] == "shelves" and override.shelves is not None and override.shelves >= 0:
                cell.shelves = override.shelves
            elif parts[2] == "material":
                # An empty key resets the cell to the module's front material.
                cell.material_key = None if override.material_key == "" else _override_material(override, m)
            else:
                raise ValueError(f"{target}: unsupported cell field or missing value")
        elif target in ("kind", "group"):
            if target == "kind" and override.kind is not None:
                m.kind = override.kind
            elif target == "group" and override.group_id is not None:
                m.group_id = override.group_id.strip().upper()
            else:
                raise ValueError(f"{target}: matching kind or group_id is required")
        elif target in ("material:carcass", "material:front"):
            key = _override_material(override, m)
            if target == "material:carcass":
                m.carcass_material_key = key
            else:
                m.front_material_key = key
        else:
            raise ValueError(f"Unknown override target {target!r}")

    # Guessed or zero-sized widths/heights share whatever the known ones leave.
    def shared(source: Provenance, size: float) -> bool:
        return source in _SHARED_SOURCES or size <= 0

    widths = _share(
        [
            (None, None, "missing") if shared(c.width_source, c.width_mm) else (c.width_mm, None, c.width_source)
            for c in m.columns
        ],
        m.width_mm,
    )
    for column, (width, _, source) in zip(m.columns, widths, strict=True):
        if shared(column.width_source, column.width_mm):
            column.width_mm, column.width_source, column.source_mark_ids = width or 0.0, source, []
    carcass = max(0.0, m.height_mm - m.plinth_mm) if m.height_mm > 0 else 0.0
    for column in m.columns:
        heights = _share(
            [
                (None, None, "missing")
                if shared(c.height_source, c.height_mm)
                else (c.height_mm, None, c.height_source)
                for c in column.cells
            ],
            carcass,
        )
        for cell, (height, _, source) in zip(column.cells, heights, strict=True):
            if shared(cell.height_source, cell.height_mm):
                cell.height_mm, cell.height_source, cell.source_mark_ids = height or 0.0, source, []
    return m


def overrides_from_edit(current: FurnitureModule, edited: FurnitureModule) -> list[Override]:
    """The editor's changes to a module as overrides (dimensions, column widths, cell fields, materials)."""
    overrides: list[Override] = []
    for dimension in MAIN_DIMENSIONS:
        new = getattr(edited, f"{dimension}_mm")
        if abs(new - getattr(current, f"{dimension}_mm")) > 0.5 and new > 0:
            overrides.append(Override(target=dimension, value_mm=new))
    for old_col, new_col in zip(current.columns, edited.columns, strict=False):
        if abs(new_col.width_mm - old_col.width_mm) > 0.5 and new_col.width_mm > 0:
            overrides.append(Override(target=f"col:{old_col.index}:width", value_mm=new_col.width_mm))
        new_cells = {c.id: c for c in new_col.cells}
        for old in old_col.cells:
            new = new_cells.get(old.id)
            if new is None:
                continue
            if abs(new.height_mm - old.height_mm) > 0.5 and new.height_mm > 0:
                overrides.append(Override(target=f"cell:{old.id}:height", value_mm=new.height_mm))
            if new.front != old.front:
                overrides.append(Override(target=f"cell:{old.id}:front", front=new.front))
            if new.shelves != old.shelves:
                overrides.append(Override(target=f"cell:{old.id}:shelves", shelves=new.shelves))
            if new.material_key != old.material_key and new.material_key:
                overrides.append(Override(target=f"cell:{old.id}:material", material_key=new.material_key))
    for target, old_key, new_key in (
        ("material:carcass", current.carcass_material_key, edited.carcass_material_key),
        ("material:front", current.front_material_key, edited.front_material_key),
    ):
        if new_key and new_key != old_key:
            overrides.append(Override(target=target, material_key=new_key))
    if edited.kind != current.kind:
        overrides.append(Override(target="kind", kind=edited.kind))
    if edited.group_id != current.group_id:
        overrides.append(Override(target="group", group_id=edited.group_id))
    return overrides


# ── Same module on other drawings ───────────────────────────────────────────


def _base_id(module_id: str, ids: set[str]) -> str:
    """``ZM05-2`` → ``ZM05`` when ``ZM05`` exists (the store suffixes repeated module ids)."""
    match = re.fullmatch(r"(.+)-\d+", module_id)
    return match.group(1) if match and match.group(1) in ids else module_id


def cross_drawing_checks(models: list[ModuleModel]) -> dict[str, FurnitureModule]:
    """Compare modules drawn on several pages/files (same module id); returns the updated modules by id.

    Width/height/depth read on one drawing are checked against the others (mismatch → VIEW_MISMATCH);
    a depth only assumed here is taken from a drawing that shows it (provenance ``cross_view``).
    """
    ids = {m.module.module_id for m in models}
    groups: dict[str, list[FurnitureModule]] = {}
    for model in models:
        groups.setdefault(_base_id(model.module.module_id, ids), []).append(model.module)
    updated: dict[str, FurnitureModule] = {}
    for members in groups.values():
        for module in members:
            m = module.model_copy(deep=True)
            m.view_checks = [c for c in m.view_checks if c.source == "view"]
            twins = [t for t in members if t.module_id != m.module_id]
            for twin in twins:
                where = f"{twin.file_name} p.{twin.page_index + 1} ({twin.module_id})"
                for dimension in ("width", "height", "depth"):
                    mine, my_source, _ = _dimension_value(m, dimension)
                    theirs, their_source, their_mark = _dimension_value(twin, dimension)
                    if their_source not in _READ_SOURCES or their_source == "cross_view" or theirs <= 0:
                        continue
                    if dimension == "depth" and my_source == "default":
                        m.depth_mm, m.dimension_sources["depth"] = theirs, "cross_view"
                        m.source_mark_ids["depth"] = None
                        note = f"Depth {theirs:g} mm taken from {where}."
                        if note not in m.notes:
                            m.notes.append(note)
                        mine, my_source = theirs, "cross_view"
                    if my_source not in _READ_SOURCES or mine <= 0:
                        continue
                    m.view_checks.append(
                        ViewCheck(
                            dimension=dimension,
                            expected_mm=mine,
                            where=where,
                            source="drawing",
                            status=_compare(mine, theirs, structural=True),
                            found_mm=theirs,
                            mark_id=None
                            if twin.page_index != m.page_index or twin.file_name != m.file_name
                            else their_mark,
                        )
                    )
            if twins or module.view_checks != m.view_checks:
                updated[m.module_id] = finalize_module(m)
    return updated
