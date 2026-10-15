from __future__ import annotations

from src.furniture.build import build_module
from src.furniture.hardware import derive_hardware
from src.furniture.rules import ConstructionRules, load_rules
from src.furniture.schema import Cell, TextRun
from src.schema.extraction import BoundingBox


def test_hinge_leg_and_drawer_hardware(two_door_module):
    rules = load_rules()
    model = build_module(two_door_module, rules)
    hinges = sum(item.qty for item in model.hardware if item.category == "hinge")
    assert hinges == 10  # 5 per 2100 mm leaf
    assert next(item.qty for item in model.hardware if item.category == "leg") == 4

    drawer = two_door_module.model_copy(deep=True)
    drawer.module_id = "DRAWER"
    drawer.columns[0].cells = [Cell(id="d0", col=0, row=0, front="drawer", height_mm=250)]
    drawer.columns = drawer.columns[:1]
    drawer.width_mm = drawer.columns[0].width_mm
    drawer.texts = [
        TextRun(text="Uchwyt LUCK CZARNY", bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.2, y_max=0.12)),
        TextRun(text="dł 32 cm", bbox=BoundingBox(x_min=0.1, y_min=0.13, x_max=0.2, y_max=0.15)),
    ]
    hardware = derive_hardware(drawer, build_module(drawer, rules).solids, rules)
    runner = next(item for item in hardware if item.category == "runner")
    handle = next(item for item in hardware if item.category == "handle")
    assert (runner.qty, runner.unit, runner.spec) == (1, "kpl", "500 mm")
    assert handle.name == "Uchwyt LUCK CZARNY"
    assert handle.spec == "320 mm" and handle.source == "drawing"
    assert handle.source_text is not None and "dł 32 cm" in handle.source_text

    drawer.texts = [TextRun(text="push to open", bbox=BoundingBox(x_min=0, y_min=0, x_max=1, y_max=1))]
    hardware = derive_hardware(drawer, build_module(drawer, rules).solids, rules)
    assert next(item for item in hardware if item.category == "push_open").qty == 1
    assert not any(item.category == "handle" for item in hardware)


def test_adjustable_legs_scale_with_module_width(two_door_module):
    module = two_door_module.model_copy(update={"width_mm": 1801})
    legs = next(item for item in derive_hardware(module, [], ConstructionRules()) if item.category == "leg")
    assert legs.qty == 8


def test_edge_band_metres_match_each_banded_solid(two_door_module):
    model = build_module(two_door_module, load_rules())
    edge_line = next(line for line in model.processes if line.process == "edge_banding")
    expected = sum(
        solid.length_mm / 1000 * sum(bool(edge) for edge in (solid.edge_banding.top, solid.edge_banding.bottom))
        + solid.width_mm / 1000 * sum(bool(edge) for edge in (solid.edge_banding.left, solid.edge_banding.right))
        for solid in model.solids
    )
    assert abs(edge_line.qty - expected) < 1e-6
