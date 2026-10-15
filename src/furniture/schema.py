"""Furniture module schema: dimension marks, modules, solids, hardware, processes and LLM-facing drafts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.schema.enums import ConfidenceLevel
from src.schema.extraction import BoundingBox, EdgeBanding, Panel

MaterialKind = Literal[
    "laminated_board",
    "veneer",
    "lacquered_mdf",
    "hpl",
    "mirror",
    "glass",
    "stone",
    "upholstery",
    "metal",
    "other",
]
FrontType = Literal["door_left", "door_right", "door_double", "drawer", "open", "niche_appliance", "panel_fixed"]
Unit = Literal["cm", "mm"]
Orientation = Literal["horizontal", "vertical"]
IssueCode = Literal[
    "CHAIN_MISMATCH",
    "COLUMNS_NOT_SUM",
    "ROWS_NOT_SUM",
    "DIMENSION_MISSING",
    "DEPTH_ASSUMED",
    "UNVERIFIED_DIMENSIONS",
    "NICHE_NONSTANDARD",
    "SHELF_SPAN",
    "DOOR_TOO_WIDE",
    "MATERIAL_UNKNOWN",
    "VIEW_MISMATCH",
    "DIMENSION_IMPLAUSIBLE",
]
SolidRole = Literal["carcass", "front", "back", "plinth", "divider", "shelf", "drawer_box"]
# Where a dimension came from: a printed mark, a value the model read, the user, an exact remainder
# of known dimensions, an equal-share guess, a construction-rule default, another view or drawing of
# the same element, or nothing at all.
Provenance = Literal["mark", "model", "user", "derived", "estimated", "default", "cross_view", "missing"]
# The same module is usually drawn several times: closed and open fronts, interior, side view or
# section, plan, details. Each view repeats some dimensions, which are cross-checked.
ViewKind = Literal["front_closed", "front_open", "interior", "side", "section", "plan", "detail"]
# missing / estimated / assumed / unknown_material: the drawing did not give the value;
# conflict: the value takes part in a contradiction (error issue); model_unsure: the model flagged it;
# unverified: the model read a value that no printed mark confirms.
QuestionReason = Literal[
    "missing", "estimated", "assumed", "unknown_material", "conflict", "model_unsure", "unverified"
]
HardwareCategory = Literal[
    "hinge",
    "runner",
    "handle",
    "push_open",
    "shelf_support",
    "hanging_rail",
    "rail_holder",
    "leg",
    "connector",
    "back_fixing",
    "lighting",
    "mounting",
    "other",
]
ProcessKind = Literal["cutting", "edge_banding", "drilling", "assembly", "hardware_mounting", "installation"]
# What the module is; hardware, processes and construction depend on it.
CabinetKind = Literal["cabinet", "wardrobe", "kitchen", "shelving", "bench", "reception_desk", "wall_cladding", "other"]


class MaterialSpec(BaseModel):
    key: str
    name: str
    kind: MaterialKind = "other"
    thickness_mm: float | None = None


class TextRun(BaseModel):
    """A text object of the PDF page with its normalized position."""

    text: str
    bbox: BoundingBox


class ModuleView(BaseModel):
    kind: ViewKind
    bbox: BoundingBox
    label: str = ""


class ViewCheck(BaseModel):
    """One dimension of the module compared against another view or another drawing of it."""

    dimension: str  # width | height | depth | col:<i>:width | cell:<cell_id>:height
    expected_mm: float
    where: str  # e.g. "front_open view 'otwarta'" or "PROJEKT_..._ZABUDOWY.pdf p.3 (ZM05)"
    source: Literal["view", "drawing"]
    status: Literal["match", "mismatch", "absent"]
    found_mm: float | None = None
    mark_id: str | None = None


class DimensionMark(BaseModel):
    id: str
    value: float
    unit: Unit
    page_index: int
    bbox: BoundingBox
    orientation: Orientation
    chain_id: str | None = None
    source: Literal["pdf_text", "vlm"] = "pdf_text"


class DimensionChain(BaseModel):
    id: str
    orientation: Orientation
    mark_ids: list[str]
    sum_value: float
    overall_mark_id: str | None = None
    consistent: bool | None = None


class Issue(BaseModel):
    code: IssueCode
    severity: Literal["error", "warning"]
    message: str
    mark_ids: list[str] = Field(default_factory=list)
    cell_ids: list[str] = Field(default_factory=list)


class Question(BaseModel):
    """Data the drawing did not provide; answered by the user (Override) or a model re-run.

    ``id`` is the Override target it is answered with: ``width``, ``height``, ``depth``, ``plinth``,
    ``col:<index>:width``, ``cell:<cell_id>:height``, ``material:carcass``, ``material:front``.
    """

    id: str
    kind: Literal["dimension", "material", "front", "shelves"]
    label: str
    reason: QuestionReason
    orientation: Orientation | None = None
    current_mm: float | None = None
    # Current front type / shelf count / material key for non-dimension items.
    current_value: str | None = None
    cell_id: str | None = None
    # Why it is asked: the model's own doubt, the contradicting issue, ...
    note: str = ""
    candidate_mark_ids: list[str] = Field(default_factory=list)
    # Material questions: suggested material keys (legend entries found in the PDF text).
    candidate_values: list[str] = Field(default_factory=list)


class Override(BaseModel):
    """A user-provided value applied on top of the model's interpretation.

    Targets: the Question ids plus ``cell:<cell_id>:front``, ``cell:<cell_id>:shelves`` and
    ``cell:<cell_id>:material``. Dimensions take ``mark_id`` (value read from that mark) or
    ``value_mm``; materials take an existing ``material_key`` or a new ``material`` legend entry.
    """

    target: str
    value_mm: float | None = None
    mark_id: str | None = None
    material_key: str | None = None
    material: MaterialSpec | None = None
    front: FrontType | None = None
    shelves: int | None = None
    kind: CabinetKind | None = None  # target "kind"
    group_id: str | None = None  # target "group"


class Cell(BaseModel):
    id: str
    col: int
    row: int
    front: FrontType
    height_mm: float
    height_source: Provenance = "model"
    shelves: int = 0
    material_key: str | None = None
    label: str = ""
    source_mark_ids: list[str] = Field(default_factory=list)


class Column(BaseModel):
    index: int
    width_mm: float
    width_source: Provenance = "model"
    cells: list[Cell]
    source_mark_ids: list[str] = Field(default_factory=list)


class FurnitureModule(BaseModel):
    module_id: str
    title: str
    file_name: str
    page_index: int
    view_bbox: BoundingBox
    units: Unit
    width_mm: float
    height_mm: float
    depth_mm: float
    depth_source: Literal["drawing", "assumed"]
    plinth_mm: float
    carcass_material_key: str | None = None
    front_material_key: str | None = None
    materials: list[MaterialSpec] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    marks: list[DimensionMark] = Field(default_factory=list)
    chains: list[DimensionChain] = Field(default_factory=list)
    texts: list[TextRun] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    status: Literal["ok", "incomplete"] = "ok"
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    notes: list[str] = Field(default_factory=list)
    source_mark_ids: dict[str, str | None] = Field(default_factory=dict)
    # Provenance of width / height / depth / plinth.
    dimension_sources: dict[str, Provenance] = Field(default_factory=dict)
    # Other views of this module on the page, and the cross-check of its dimensions against them
    # and against the same module on other drawings of the project.
    views: list[ModuleView] = Field(default_factory=list)
    view_checks: list[ViewCheck] = Field(default_factory=list)
    # Override targets the model flagged as unsure (→ its reason), and targets the user has set/confirmed.
    model_uncertain: dict[str, str] = Field(default_factory=dict)
    confirmed: list[str] = Field(default_factory=list)
    # Cabinet code shared by modules that are walls/runs of one cabinet (e.g. ZM03-A/B/C → "ZM03").
    group_id: str = ""
    kind: CabinetKind = "cabinet"
    # Materials found deterministically in the page's legend text (offered when the model listed none).
    legend_materials: list[MaterialSpec] = Field(default_factory=list)


class PanelSolid(BaseModel):
    part_id: str
    name: str
    role: SolidRole
    cell_id: str | None = None
    material_key: str | None = None
    # Min corner; x right, y up, z toward viewer. Origin = floor, left, rear face of the module.
    position_mm: tuple[float, float, float]
    size_mm: tuple[float, float, float]
    length_mm: float
    width_mm: float
    thickness_mm: float
    edge_banding: EdgeBanding = Field(default_factory=EdgeBanding)
    grain_direction: str | None = None


class HardwareItem(BaseModel):
    """A fitting (okucie) of the module, from construction rules or detected in the drawing text."""

    key: str
    category: HardwareCategory
    name: str
    spec: str = ""
    qty: float
    unit: Literal["szt", "kpl", "mb"] = "szt"
    cell_ids: list[str] = Field(default_factory=list)
    source: Literal["rule", "drawing"] = "rule"
    source_text: str | None = None


class ProcessLine(BaseModel):
    """One production / mounting operation with its quantity and estimated time."""

    process: ProcessKind
    operation: str
    qty: float
    unit: Literal["szt", "mb", "m2", "kpl"]
    minutes: float
    material: str | None = None
    part_ids: list[str] = Field(default_factory=list)


# ── LLM-facing drafts (role assignment over numbered marks) ─────────────────


class CellDraft(BaseModel):
    col: int
    row: int
    front: FrontType
    height_mark: str | None = None
    height_value: float | None = None
    shelves: int = 0
    material_key: str | None = None
    label: str = ""


class ColumnDraft(BaseModel):
    width_mark: str | None = None
    width_value: float | None = None
    cells: list[CellDraft] = Field(default_factory=list)


UncertainField = Literal[
    "width",
    "height",
    "depth",
    "plinth",
    "column_width",
    "cell_height",
    "cell_front",
    "cell_shelves",
    "carcass_material",
    "front_material",
]


class UncertainItem(BaseModel):
    """A value of the draft the model flags as unsure (column/row are 1-based, left→right, top→bottom)."""

    field: UncertainField
    column: int | None = None
    row: int | None = None
    reason: str = ""


class ModuleDraft(BaseModel):
    module_id: str
    title: str
    view_bbox: BoundingBox
    width_mark: str | None = None
    width_value: float | None = None
    height_mark: str | None = None
    height_value: float | None = None
    depth_mark: str | None = None
    depth_value: float | None = None
    plinth_mark: str | None = None
    plinth_value: float | None = None
    columns: list[ColumnDraft] = Field(default_factory=list)
    # Other views of the same module on this page (open/interior/side/section/plan/detail).
    views: list[ModuleView] = Field(default_factory=list)
    carcass_material_key: str | None = None
    front_material_key: str | None = None
    materials: list[MaterialSpec] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Values the model itself is not sure about.
    uncertain: list[UncertainItem] = Field(default_factory=list)
    # Cabinet this module belongs to (e.g. "ZM03" for its walls A/B/C) and what kind of furniture it is.
    group_id: str | None = None
    kind: CabinetKind | None = None  # None = the model did not say


class PageDraft(BaseModel):
    units: Unit = "cm"
    modules: list[ModuleDraft] = Field(default_factory=list)


class ModuleModel(BaseModel):
    """A built module: interpretation, 3D solids, cut list, hardware, processes, and edit state.

    ``module`` = resolve(``draft``) with ``overrides`` applied on top; without a draft, ``overrides``
    applied to ``base_module``. ``refine_*`` track the model re-run that fills open questions.
    """

    module: FurnitureModule
    solids: list[PanelSolid] = Field(default_factory=list)
    cut_list: list[Panel] = Field(default_factory=list)
    hardware: list[HardwareItem] = Field(default_factory=list)
    processes: list[ProcessLine] = Field(default_factory=list)
    draft: ModuleDraft | None = None
    # Without a draft (module created/edited by hand): the module the overrides are applied to.
    base_module: FurnitureModule | None = None
    overrides: list[Override] = Field(default_factory=list)
    refine_status: Literal["idle", "running", "error"] = "idle"
    refine_attempts: int = 0
    refine_error: str | None = None
