from __future__ import annotations

import itertools

from src.furniture.build import build_module
from src.furniture.rules import ConstructionRules
from src.furniture.schema import Cell, Column, FurnitureModule, PanelSolid

RULES = ConstructionRules()


def _overlap(a: PanelSolid, b: PanelSolid) -> bool:
    """Positive-volume AABB intersection (shared faces allowed)."""
    return all(
        min(pa + sa, pb + sb) - max(pa, pb) > 1e-6
        for pa, sa, pb, sb in zip(a.position_mm, a.size_mm, b.position_mm, b.size_mm, strict=True)
    )


def test_two_door_module_solids_and_cut_list(two_door_module):
    model = build_module(two_door_module, RULES)

    assert len(model.solids) == 9
    rows = {(p.name, p.length_mm, p.width_mm, p.thickness_mm): p.qty for p in model.cut_list}
    assert rows == {
        ("Bok", 2100, 600, 18): 2,
        ("Wieniec górny", 1164, 600, 18): 1,
        ("Wieniec dolny", 1164, 600, 18): 1,
        ("Plecy", 2064, 1164, 8): 1,
        ("Cokół", 1200, 100, 18): 1,
        ("Przegroda pionowa", 2064, 592, 18): 1,
        ("Front", 2097, 597, 19): 2,
    }
    front_row = next(p for p in model.cut_list if p.name == "Front")
    assert front_row.notes.split(",") == [s.part_id for s in model.solids if s.role == "front"]


def test_solids_stay_inside_module_envelope(two_door_module):
    model = build_module(two_door_module, RULES)
    limits = (1200, 2200, 600 + RULES.front_thickness_mm)
    for solid in model.solids:
        for pos, size, limit in zip(solid.position_mm, solid.size_mm, limits, strict=True):
            assert pos >= 0
            assert pos + size <= limit + 1e-6, solid.name


def test_carcass_solids_do_not_overlap(two_door_module):
    model = build_module(two_door_module, RULES)
    carcass = [s for s in model.solids if s.role != "front"]
    for a, b in itertools.combinations(carcass, 2):
        assert not _overlap(a, b), (a.name, b.name)


def test_drawers_shelves_and_rows_build_without_collisions(two_door_module):
    module = two_door_module.model_copy(
        update={
            "columns": [
                Column(
                    index=0,
                    width_mm=600,
                    cells=[
                        Cell(id="c0r0", col=0, row=0, front="door_double", height_mm=1500, shelves=3),
                        Cell(id="c0r1", col=0, row=1, front="drawer", height_mm=300),
                        Cell(id="c0r2", col=0, row=2, front="drawer", height_mm=300),
                    ],
                ),
                Column(
                    index=1,
                    width_mm=600,
                    cells=[
                        Cell(id="c1r0", col=1, row=0, front="open", height_mm=1200, shelves=2),
                        Cell(id="c1r1", col=1, row=1, front="niche_appliance", height_mm=900),
                    ],
                ),
            ]
        }
    )
    model = build_module(module, RULES)
    names = [s.name for s in model.solids]
    assert names.count("Przegroda pozioma") == 3
    assert names.count("Półka") == 5
    assert names.count("Front szuflady") == 2
    assert names.count("Front") == 2  # door_double; open cell and niche have no front
    assert names.count("Dno szuflady") == 2
    inner = [s for s in model.solids if s.role != "front"]
    for a, b in itertools.combinations(inner, 2):
        assert not _overlap(a, b), (a.name, a.cell_id, b.name, b.cell_id)
    fronts = [s for s in model.solids if s.role == "front"]
    for a, b in itertools.combinations(fronts, 2):
        assert not _overlap(a, b), (a.cell_id, b.cell_id)


def test_incomplete_module_has_no_solids(two_door_module):
    module: FurnitureModule = two_door_module.model_copy(update={"status": "incomplete"})
    model = build_module(module, RULES)
    assert model.solids == []
    assert model.cut_list == []
