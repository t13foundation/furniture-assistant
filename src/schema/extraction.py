from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.schema.enums import ConfidenceLevel, DrawingType


class FieldExtraction(BaseModel):
    """A single scalar field with confidence tracking (project-level fields)."""

    value: str | None = None
    value_eng: str | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    source_region: str | None = None
    notes: str = ""


class BoundingBox(BaseModel):
    x_min: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)
    label: str = ""


class LayoutMap(BaseModel):
    title_block: BoundingBox | None = None
    dimension_clusters: list[BoundingBox] = Field(default_factory=list)
    symbol_areas: list[BoundingBox] = Field(default_factory=list)
    text_regions: list[BoundingBox] = Field(default_factory=list)


class RunMetadata(BaseModel):
    timestamp: str
    orchestrator_model: str
    vision_model: str
    api_base_url: str
    pipeline_mode: str
    validation_mode: str
    max_passes: int
    confidence_threshold: str
    ocr_backend: str
    cli_args: dict[str, Any] = Field(default_factory=dict)


class EdgeBanding(BaseModel):
    """Edge banding (okleina) specification per panel edge."""

    top: str | None = None
    right: str | None = None
    bottom: str | None = None
    left: str | None = None


class Panel(BaseModel):
    """A single furniture panel (element płyty) in a cutting list.

    Maps to one row in a CNC cutting optimization sheet.
    """

    part_id: str | None = None
    name: str | None = None  # e.g. "Blat", "Bok lewy", "Front", "Plecy", "Dno"
    length_mm: float | None = None
    width_mm: float | None = None
    thickness_mm: float | None = None
    board_type: str | None = None  # e.g. "MFC 18mm", "MDF 18mm", "Płyta meblowa"
    material_code: str | None = None
    edge_banding: EdgeBanding = Field(default_factory=EdgeBanding)
    grain_direction: str | None = None  # "wzdłużnie" | "poprzecznie" | "L" | "Q"
    qty: int = 1
    holes_notes: str | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    notes: str = ""

    @field_validator("qty", mode="before")
    @classmethod
    def default_qty(cls, v):
        """Models often emit null for qty when not visible; default to 1."""
        return 1 if v is None else v


class GeometryGroup(BaseModel):
    """A geometry-first grouping of equivalent panel records.

    ``minimum_qty`` treats repeated records as duplicate views; ``maximum_qty``
    treats them as separate physical pieces. The range stays explicit until a
    human or stronger evidence resolves the ambiguity.
    """

    group_id: str
    category: str
    length_mm: float | None = None
    width_mm: float | None = None
    thickness_mm: float | None = None
    source_panel_indices: list[int] = Field(default_factory=list)
    observed_qty: int = 0
    minimum_qty: int = 0
    maximum_qty: int = 0
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    notes: str = ""


class GeometrySummary(BaseModel):
    """Deterministic geometry inventory for panel-count assessment."""

    panel_records: int = 0
    resolved_records: int = 0
    unresolved_records: int = 0
    geometry_groups: int = 0
    ambiguous_groups: int = 0
    minimum_panel_count: int = 0
    maximum_panel_count: int = 0
    notes: list[str] = Field(default_factory=list)


class Hardware(BaseModel):
    """A hardware / fitting item (okucie) in a furniture BOM."""

    name: str | None = None  # e.g. "Zawias", "Prowadnica", "Uchwyt", "Noga"
    code: str | None = None
    qty: int | None = 1
    notes: str = ""

    @field_validator("qty", mode="before")
    @classmethod
    def default_qty(cls, v):
        """Models often emit null for qty when not visible; default to 1."""
        return 1 if v is None else v


# Project-level scalar fields that carry FieldExtraction confidence.
# Used by validation, diff-merge, comparison, and output writers.
SCALAR_FIELDS = [
    "project_name",
    "client",
    "cabinet_id",
    "scale",
    "units",
]


class ExtractionResult(BaseModel):
    """Furniture cutting-list extraction result for a single drawing/file.

    Holds project-level scalar fields (with confidence) plus a list of panels
    and hardware items that form the CNC cutting list / BOM.
    """

    source_file: str
    drawing_type: DrawingType = DrawingType.OTHER
    extraction_timestamp: str = ""
    model_used: str = ""
    passes_completed: int = 0

    # Run metadata
    run_metadata: RunMetadata | None = None

    # Project-level scalar fields (FieldExtraction for confidence tracking)
    project_name: FieldExtraction = Field(default_factory=FieldExtraction)
    client: FieldExtraction = Field(default_factory=FieldExtraction)
    cabinet_id: FieldExtraction = Field(default_factory=FieldExtraction)
    scale: FieldExtraction = Field(default_factory=FieldExtraction)
    units: FieldExtraction = Field(default_factory=FieldExtraction)

    # Cutting list — the main CNC payload
    panels: list[Panel] = Field(default_factory=list)
    geometry_groups: list[GeometryGroup] = Field(default_factory=list)
    geometry_summary: GeometrySummary = Field(default_factory=GeometrySummary)
    hardware: list[Hardware] = Field(default_factory=list)

    # Free-text notes / specs found on the drawing
    notes: str = ""

    # Quality metadata
    needs_review: bool = False
    review_fields: list[str] = Field(default_factory=list)
    unresolvable_fields: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    tool_call_log: list[dict] = Field(default_factory=list)
