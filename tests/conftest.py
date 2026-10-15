from __future__ import annotations

from pathlib import Path

import pytest
from src.furniture.schema import Cell, Column, FurnitureModule
from src.schema.enums import ConfidenceLevel, DrawingType
from src.schema.extraction import BoundingBox, ExtractionResult, FieldExtraction, Hardware, Panel

# Sample drawings: two committed sheets in samples/, any other project package in the
# gitignored sample-data/<PROJECT>/{*.pdf,*.png,*.jpg,*.xlsx,*.dwg,*.p3d}.
ROOT = Path(__file__).parent.parent
SAMPLE_ROOTS = (ROOT / "samples", ROOT / "sample-data")


def sample_file(pattern: str) -> Path | None:
    """A sample drawing by file name (glob allowed): the committed samples/ first, then anywhere under the ignored sample-data/."""
    for root in SAMPLE_ROOTS:
        found = next(iter(sorted(root.rglob(pattern))), None) if root.is_dir() else None
        if found:
            return found
    return None


def _first_file(ext: str) -> str | None:
    """Return the first file with the given extension under samples/ or sample-data/, or None."""
    found = sample_file(f"*{ext}")
    return str(found) if found else None


@pytest.fixture
def two_door_module() -> FurnitureModule:
    """1200x2200x600 mm wardrobe on a 100 mm plinth: two 600 mm columns, left and right door."""
    return FurnitureModule(
        module_id="TEST",
        title="Test wardrobe",
        file_name="test.pdf",
        page_index=0,
        view_bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.9),
        units="mm",
        width_mm=1200,
        height_mm=2200,
        depth_mm=600,
        depth_source="drawing",
        plinth_mm=100,
        columns=[
            Column(index=0, width_mm=600, cells=[Cell(id="c0r0", col=0, row=0, front="door_left", height_mm=2100)]),
            Column(index=1, width_mm=600, cells=[Cell(id="c1r0", col=1, row=0, front="door_right", height_mm=2100)]),
        ],
    )


@pytest.fixture
def sample_result() -> ExtractionResult:
    """Furniture ExtractionResult with one panel and one hardware item."""
    return ExtractionResult(
        source_file="test_elevation.pdf",
        drawing_type=DrawingType.ELEVATION,
        extraction_timestamp="2026-07-26T10:00:00",
        model_used="test-model",
        passes_completed=3,
        project_name=FieldExtraction(value="BIUROWIEC A", confidence=ConfidenceLevel.HIGH),
        client=FieldExtraction(value="Klient testowy", confidence=ConfidenceLevel.HIGH),
        cabinet_id=FieldExtraction(value="S1", confidence=ConfidenceLevel.HIGH),
        scale=FieldExtraction(value="1:20", confidence=ConfidenceLevel.HIGH),
        units=FieldExtraction(value="mm", confidence=ConfidenceLevel.HIGH),
        panels=[
            Panel(
                part_id="P1",
                name="Blat",
                length_mm=800,
                width_mm=600,
                thickness_mm=18,
                board_type="MFC 18mm",
                qty=2,
                confidence=ConfidenceLevel.HIGH,
            ),
        ],
        hardware=[
            Hardware(name="Zawias Blum", code="Blum CLIP top", qty=4),
        ],
    )


@pytest.fixture
def sample_pdf_path() -> str | None:
    """First PDF found under samples/ or sample-data/, or None if neither has one."""
    return _first_file(".pdf")


@pytest.fixture
def sample_image_path() -> str | None:
    """First raster image (PNG/JPG) found under samples/ or sample-data/, or None."""
    for ext in (".png", ".jpg", ".jpeg"):
        path = _first_file(ext)
        if path:
            return path
    return None


@pytest.fixture
def sample_tif_path() -> str | None:
    """First TIF found under samples/ or sample-data/, or None (TIFs are uncommon here)."""
    return _first_file(".tif")
