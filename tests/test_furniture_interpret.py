from __future__ import annotations

from src.furniture.build import build_module
from src.furniture.resolve import apply_overrides, finalize_module, resolve_module
from src.furniture.rules import ConstructionRules
from src.furniture.schema import (
    CellDraft,
    ColumnDraft,
    DimensionChain,
    DimensionMark,
    MaterialSpec,
    ModuleDraft,
    Orientation,
    Override,
)
from src.schema.enums import ConfidenceLevel
from src.schema.extraction import BoundingBox

RULES = ConstructionRules()
BOX = BoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.6)


def _mark(mark_id: str, value: float, orientation: Orientation = "horizontal") -> DimensionMark:
    return DimensionMark(id=mark_id, value=value, unit="mm", page_index=0, bbox=BOX, orientation=orientation)


MARKS = [
    _mark("D1", 1200),
    _mark("D2", 500),
    _mark("D3", 500),
    _mark("D4", 2200, "vertical"),
    _mark("D5", 100, "vertical"),
    _mark("D6", 600),
]


def _draft(**overrides) -> ModuleDraft:
    base = {
        "module_id": "zm 05/a",
        "title": "Zabudowa",
        "view_bbox": BOX,
        "width_mark": "D1",
        "height_mark": "D4",
        "depth_mark": "D6",
        "plinth_mark": "D5",
        "columns": [
            ColumnDraft(width_mark="D2", cells=[CellDraft(col=0, row=0, front="door_left")]),
            ColumnDraft(width_mark="D3", cells=[CellDraft(col=1, row=0, front="door_right")]),
        ],
    }
    return ModuleDraft.model_validate({**base, **overrides})


def _resolve(draft: ModuleDraft, marks=MARKS, chains=()):
    return resolve_module(draft, list(marks), list(chains), "mm", "x.pdf", 0, RULES)


def test_columns_not_summing_to_width_is_an_error():
    module = _resolve(_draft())
    codes = {i.code: i for i in module.issues}
    assert codes["COLUMNS_NOT_SUM"].severity == "error"
    assert {"D1", "D2", "D3"} <= set(codes["COLUMNS_NOT_SUM"].mark_ids)
    assert module.confidence == ConfidenceLevel.LOW
    assert module.module_id == "ZM-05-A"


def test_consistent_module_from_marks_is_high_confidence():
    draft = _draft(
        columns=[
            ColumnDraft(width_mark="D2", cells=[CellDraft(col=0, row=0, front="door_left")]),
            ColumnDraft(width_value=700, cells=[CellDraft(col=1, row=0, front="open", shelves=2)]),
        ]
    )
    module = _resolve(draft)
    assert module.issues == []
    assert module.confidence == ConfidenceLevel.HIGH
    assert module.source_mark_ids == {"width": "D1", "height": "D4", "depth": "D6", "plinth": "D5"}
    assert [c.width_mm for c in module.columns] == [500, 700]
    assert module.columns[0].cells[0].height_mm == 2100


def test_missing_depth_is_assumed_with_warning():
    module = _resolve(_draft(depth_mark=None))
    depth = next(i for i in module.issues if i.code == "DEPTH_ASSUMED")
    assert depth.severity == "warning"
    assert module.depth_mm == RULES.depth_default_mm
    assert module.depth_source == "assumed"


def test_missing_width_is_incomplete_and_builds_nothing():
    module = _resolve(_draft(width_mark=None))
    assert module.status == "incomplete"
    assert any(i.code == "DIMENSION_MISSING" and i.severity == "error" for i in module.issues)
    assert build_module(module, RULES).solids == []


def test_unknown_cell_heights_share_the_remaining_carcass_height():
    draft = _draft(
        columns=[
            ColumnDraft(
                width_value=1200,
                cells=[
                    CellDraft(col=0, row=0, front="open", height_value=500),
                    CellDraft(col=0, row=1, front="drawer"),
                    CellDraft(col=0, row=2, front="drawer"),
                ],
            )
        ]
    )
    module = _resolve(draft)
    assert [c.height_mm for c in module.columns[0].cells] == [500, 800, 800]
    assert not any(i.code == "ROWS_NOT_SUM" for i in module.issues)


def test_rows_exceeding_carcass_height_are_an_error():
    draft = _draft(
        columns=[
            ColumnDraft(
                width_value=1200,
                cells=[
                    CellDraft(col=0, row=0, front="open", height_value=1500),
                    CellDraft(col=0, row=1, front="drawer", height_value=700),
                ],
            )
        ]
    )
    module = _resolve(draft)
    assert any(i.code == "ROWS_NOT_SUM" and i.severity == "error" for i in module.issues)


def test_inconsistent_chain_touching_module_marks_is_reported():
    chain = DimensionChain(
        id="C1", orientation="horizontal", mark_ids=["D2", "D3"], sum_value=1000, overall_mark_id="D1", consistent=False
    )
    module = _resolve(_draft(), chains=[chain])
    mismatch = next(i for i in module.issues if i.code == "CHAIN_MISMATCH")
    assert set(mismatch.mark_ids) == {"D1", "D2", "D3"}


def test_raster_page_values_are_flagged_unverified():
    draft = _draft(
        width_mark=None,
        width_value=1200,
        height_mark=None,
        height_value=2200,
        depth_mark=None,
        plinth_mark=None,
        columns=[],
    )
    module = _resolve(draft, marks=[])
    assert any(i.code == "UNVERIFIED_DIMENSIONS" for i in module.issues)
    assert [c.width_mm for c in module.columns] == [1200]


def test_unknown_material_key_is_warned_asked_and_cleared_by_an_answer():
    draft = _draft(
        columns=[ColumnDraft(width_mark="D1", cells=[CellDraft(col=0, row=0, front="drawer")])],
        materials=[MaterialSpec(key="M1", name="Dąb", kind="veneer")],
        carcass_material_key="M1",
        front_material_key="M2",
    )
    module = _resolve(draft)
    assert [i.code for i in module.issues] == ["MATERIAL_UNKNOWN"]
    assert [q.id for q in module.questions] == ["material:front"]

    answered = finalize_module(apply_overrides(module, [Override(target="material:front", material_key="M1")]))
    assert answered.issues == []
    assert answered.questions == []


def test_printed_values_snap_to_nearby_marks_of_matching_orientation():
    far = BoundingBox(x_min=0.9, y_min=0.9, x_max=0.95, y_max=0.95)
    marks = [
        _mark("D1", 1200),
        _mark("D4", 2200, "vertical"),
        _mark("D5", 100, "vertical"),
        # Same number, wrong orientation: must not be taken as the width.
        DimensionMark(id="D7", value=600, unit="mm", page_index=0, bbox=BOX, orientation="vertical"),
        # Same number, far from the view: belongs to another module.
        DimensionMark(id="D8", value=600, unit="mm", page_index=0, bbox=far, orientation="horizontal"),
    ]
    draft = _draft(
        width_mark=None,
        width_value=1200,
        height_mark=None,
        height_value=2200,
        plinth_mark=None,
        plinth_value=100,
        depth_mark=None,
        columns=[
            ColumnDraft(width_value=600, cells=[CellDraft(col=0, row=0, front="door_left")]),
            ColumnDraft(width_value=600, cells=[CellDraft(col=1, row=0, front="door_right")]),
        ],
    )
    module = _resolve(draft, marks=marks)
    assert module.source_mark_ids == {"width": "D1", "height": "D4", "depth": None, "plinth": "D5"}
    assert [c.source_mark_ids for c in module.columns] == [[], []]
    assert [c.width_mm for c in module.columns] == [600, 600]


def _placed(mark_id: str, value: float, orientation: Orientation, x: float, y: float) -> DimensionMark:
    box = BoundingBox(x_min=x - 0.01, y_min=y - 0.005, x_max=x + 0.01, y_max=y + 0.005)
    return DimensionMark(id=mark_id, value=value, unit="mm", page_index=0, bbox=box, orientation=orientation)


def test_unlabelled_columns_and_rows_take_the_chains_along_the_view():
    # View at x 0.1-0.4, y 0.1-0.6: width chain below it, height chain (top to bottom) left of it.
    marks = [
        _placed("D1", 500, "horizontal", 0.2, 0.62),
        _placed("D2", 700, "horizontal", 0.33, 0.62),
        _placed("D3", 1200, "horizontal", 0.25, 0.66),
        _placed("D4", 300, "vertical", 0.08, 0.2),
        _placed("D5", 1800, "vertical", 0.08, 0.4),
        _placed("D6", 100, "vertical", 0.08, 0.58),
        _placed("D7", 2200, "vertical", 0.05, 0.4),
        # A chain of another module with the same member count, far from the view.
        _placed("D8", 600, "horizontal", 0.8, 0.62),
        _placed("D9", 600, "horizontal", 0.9, 0.62),
    ]
    chains = [
        DimensionChain(
            id="C1",
            orientation="horizontal",
            mark_ids=["D1", "D2"],
            sum_value=1200,
            overall_mark_id="D3",
            consistent=True,
        ),
        DimensionChain(id="C2", orientation="horizontal", mark_ids=["D8", "D9"], sum_value=1200),
        DimensionChain(
            id="C3",
            orientation="vertical",
            mark_ids=["D4", "D5", "D6"],
            sum_value=2200,
            overall_mark_id="D7",
            consistent=True,
        ),
    ]
    draft = _draft(
        width_mark=None,
        height_mark=None,
        depth_mark=None,
        plinth_mark=None,
        columns=[
            ColumnDraft(cells=[CellDraft(col=0, row=0, front="door_left"), CellDraft(col=0, row=1, front="drawer")]),
            ColumnDraft(cells=[CellDraft(col=1, row=0, front="open")]),
        ],
    )
    module = _resolve(draft, marks=marks, chains=chains)

    assert module.status == "ok"
    assert module.source_mark_ids == {"width": "D3", "height": "D7", "depth": None, "plinth": "D6"}
    assert [(c.width_mm, c.source_mark_ids) for c in module.columns] == [(500, ["D1"]), (700, ["D2"])]
    assert [(c.height_mm, c.source_mark_ids) for c in module.columns[0].cells] == [(300, ["D4"]), (1800, ["D5"])]
    assert [c.height_mm for c in module.columns[1].cells] == [2100]
