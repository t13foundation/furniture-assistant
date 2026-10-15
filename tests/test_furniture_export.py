from __future__ import annotations

import csv

import ezdxf
import openpyxl
from src.furniture.build import build_module
from src.furniture.export import write_cut_list_csv, write_cut_list_xlsx, write_panels_dxf, write_sheets_svg
from src.furniture.nest import nest_cut_list
from src.furniture.rules import ConstructionRules
from src.schema.extraction import Panel

RULES = ConstructionRules()


def test_exports_cover_every_piece(two_door_module, tmp_path):
    models = [build_module(two_door_module, RULES)]

    with write_cut_list_csv(models, tmp_path / "cut.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 7
    assert sum(int(r["qty"]) for r in rows) == 9

    workbook = openpyxl.load_workbook(write_cut_list_xlsx(models, tmp_path / "cut.xlsx"))
    assert workbook["Cutting List"].max_row - 1 == 7

    svg = write_sheets_svg(models, RULES, tmp_path / "sheets.svg").read_text(encoding="utf-8")
    assert svg.count("data-part=") == 9

    dxf = ezdxf.readfile(write_panels_dxf(models, tmp_path / "panels.dxf"))
    polylines = dxf.modelspace().query("LWPOLYLINE")
    assert len(polylines) == 9
    assert {p.dxf.layer for p in polylines} == {"T18", "T8", "T19"}


def test_nesting_respects_sheet_bounds_grain_and_oversize():
    panels = [
        Panel(part_id="A", name="Bok", length_mm=2100, width_mm=600, thickness_mm=18, qty=5, grain_direction="L"),
        Panel(part_id="B", name="Półka", length_mm=900, width_mm=550, thickness_mm=18, qty=6),
        Panel(part_id="X", name="Blat", length_mm=3200, width_mm=600, thickness_mm=18, qty=1),
    ]
    sheets = nest_cut_list(panels, RULES)
    placements = [p for s in sheets for p in s.placements]
    assert len(placements) == 11
    assert [u for s in sheets for u in s.unplaced] == ["X"]
    for sheet in sheets:
        for p in sheet.placements:
            assert p.x >= RULES.sheet_trim_mm and p.y >= RULES.sheet_trim_mm
            assert p.x + p.w <= sheet.length - RULES.sheet_trim_mm
            assert p.y + p.h <= sheet.width - RULES.sheet_trim_mm
            if p.name == "Bok":
                assert not p.rotated and p.w == 2100
