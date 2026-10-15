"""Cut-list exports for built furniture modules: CSV, XLSX, nested-sheet SVG, panel DXF."""

from __future__ import annotations

import csv
from pathlib import Path

import ezdxf

from src.furniture.nest import nest_cut_list, piece_ids, sheets_to_svg
from src.furniture.rules import ConstructionRules
from src.furniture.schema import ModuleModel
from src.output.writer import write_xlsx
from src.schema.enums import DrawingType
from src.schema.extraction import ExtractionResult, FieldExtraction

CSV_COLUMNS = [
    "module_id",
    "part_id",
    "name",
    "qty",
    "length_mm",
    "width_mm",
    "thickness_mm",
    "material",
    "edge_top",
    "edge_right",
    "edge_bottom",
    "edge_left",
    "grain",
]

_DXF_GAP_MM = 50
_DXF_PER_ROW = 10
_DXF_TEXT_HEIGHT_MM = 25


def _prepare(path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def write_cut_list_csv(models: list[ModuleModel], path: str | Path) -> Path:
    out = _prepare(path)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for model in models:
            for p in model.cut_list:
                e = p.edge_banding
                writer.writerow(
                    [
                        model.module.module_id,
                        p.part_id,
                        p.name,
                        p.qty,
                        f"{p.length_mm:g}",
                        f"{p.width_mm:g}",
                        f"{p.thickness_mm:g}",
                        p.board_type or p.material_code or "",
                        e.top or "",
                        e.right or "",
                        e.bottom or "",
                        e.left or "",
                        p.grain_direction or "",
                    ]
                )
    return out


def write_cut_list_xlsx(models: list[ModuleModel], path: str | Path) -> Path:
    """Reuses the pipeline workbook (Cutting List / Production Cut List / Material Summary)."""
    results = [
        ExtractionResult(
            source_file=m.module.file_name,
            drawing_type=DrawingType.ELEVATION,
            cabinet_id=FieldExtraction(value=m.module.module_id, confidence=m.module.confidence),
            panels=m.cut_list,
            model_used="furniture-builder",
        )
        for m in models
    ]
    return write_xlsx(results, str(_prepare(path)))


def write_sheets_svg(models: list[ModuleModel], rules: ConstructionRules, path: str | Path) -> Path:
    out = _prepare(path)
    panels = [p for m in models for p in m.cut_list]
    out.write_text(sheets_to_svg(nest_cut_list(panels, rules)), encoding="utf-8")
    return out


def write_panels_dxf(models: list[ModuleModel], path: str | Path) -> Path:
    """Every physical piece as a closed rectangle on layer ``T<thickness>``, laid out in a grid."""
    out = _prepare(path)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    pieces = [(part_id, p) for m in models for p in m.cut_list for part_id in piece_ids(p)]
    x = y = row_height = 0.0
    for index, (part_id, panel) in enumerate(pieces):
        if index and index % _DXF_PER_ROW == 0:
            x, y, row_height = 0.0, y - row_height - _DXF_GAP_MM, 0.0
        length, width = panel.length_mm or 0.0, panel.width_mm or 0.0
        layer = f"T{panel.thickness_mm or 0:g}"
        if layer not in doc.layers:
            doc.layers.add(layer)
        msp.add_lwpolyline(
            [(x, y), (x + length, y), (x + length, y - width), (x, y - width)],
            close=True,
            dxfattribs={"layer": layer},
        )
        msp.add_text(
            f"{part_id} {panel.name or ''}",
            height=_DXF_TEXT_HEIGHT_MM,
            dxfattribs={"layer": layer, "insert": (x + 10, y - 10 - _DXF_TEXT_HEIGHT_MM)},
        )
        x += length + _DXF_GAP_MM
        row_height = max(row_height, width)
    doc.saveas(out)
    return out
