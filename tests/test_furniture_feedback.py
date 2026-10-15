"""Feedback loop: provenance → open questions → answers/overrides, view and drawing cross-checks."""

from __future__ import annotations

from src.furniture.resolve import (
    apply_overrides,
    cross_drawing_checks,
    finalize_module,
    merge_overrides,
    overrides_from_edit,
    resolve_module,
)
from src.furniture.rules import ConstructionRules
from src.furniture.schema import (
    CellDraft,
    ColumnDraft,
    DimensionChain,
    DimensionMark,
    MaterialSpec,
    ModuleDraft,
    ModuleModel,
    ModuleView,
    Orientation,
    Override,
    UncertainItem,
)
from src.schema.extraction import BoundingBox

RULES = ConstructionRules()
VIEW = BoundingBox(x_min=0.1, y_min=0.1, x_max=0.3, y_max=0.6)
OPEN_VIEW = BoundingBox(x_min=0.4, y_min=0.1, x_max=0.6, y_max=0.6)
SIDE_VIEW = BoundingBox(x_min=0.7, y_min=0.1, x_max=0.8, y_max=0.6)
MATERIALS = [MaterialSpec(key="M1", name="Płyta biała", kind="laminated_board")]


def _mark(mark_id: str, value: float, orientation: Orientation, x: float, y: float) -> DimensionMark:
    box = BoundingBox(x_min=x - 0.01, y_min=y - 0.005, x_max=x + 0.01, y_max=y + 0.005)
    return DimensionMark(id=mark_id, value=value, unit="mm", page_index=0, bbox=box, orientation=orientation)


def _draft(**overrides) -> ModuleDraft:
    base = {
        "module_id": "ZM05",
        "title": "ZM05",
        "view_bbox": VIEW,
        "width_value": 1500,
        "height_value": 2200,
        "plinth_value": 100,
        "depth_value": 600,
        "materials": MATERIALS,
        "carcass_material_key": "M1",
        "front_material_key": "M1",
        "columns": [
            ColumnDraft(width_value=500, cells=[CellDraft(col=0, row=0, front="open")]),
            ColumnDraft(cells=[CellDraft(col=1, row=0, front="open")]),
            ColumnDraft(cells=[CellDraft(col=2, row=0, front="open")]),
        ],
    }
    return ModuleDraft.model_validate({**base, **overrides})


def _resolve(draft: ModuleDraft, marks=(), chains=()):
    return resolve_module(draft, list(marks), list(chains), "mm", "x.pdf", 0, RULES)


def test_guessed_columns_are_asked_and_an_answer_makes_the_rest_exact():
    module = _resolve(_draft())
    assert [(c.width_mm, c.width_source) for c in module.columns] == [
        (500, "model"),
        (500, "estimated"),
        (500, "estimated"),
    ]
    assert [q.id for q in module.questions] == ["col:1:width", "col:2:width"]

    answered = finalize_module(apply_overrides(module, [Override(target="col:1:width", value_mm=400)]))
    assert [(c.width_mm, c.width_source) for c in answered.columns] == [
        (500, "model"),
        (400, "user"),
        (600, "derived"),
    ]
    assert answered.questions == []
    assert not any(i.code == "COLUMNS_NOT_SUM" for i in answered.issues)


def test_missing_width_answered_with_a_mark_builds_the_module():
    marks = [_mark("D1", 1500, "horizontal", 0.2, 0.62)]
    module = _resolve(_draft(width_value=None, columns=[]), marks=marks)
    assert module.status == "incomplete"
    width_question = next(q for q in module.questions if q.id == "width")
    assert width_question.reason == "missing"
    assert width_question.candidate_mark_ids == ["D1"]

    answered = finalize_module(apply_overrides(module, [Override(target="width", mark_id="D1")]))
    assert answered.status == "ok"
    assert answered.dimension_sources["width"] == "mark"
    assert answered.source_mark_ids["width"] == "D1"
    assert [(c.width_mm, c.width_source) for c in answered.columns] == [(1500, "derived")]


def test_assumed_defaults_and_unknown_materials_are_questions():
    module = _resolve(
        _draft(depth_value=None, plinth_value=None, front_material_key=None, columns=[], carcass_material_key="M9")
    )
    reasons = {q.id: q.reason for q in module.questions}
    # No fronts in the default open cell, so no front material is needed.
    assert reasons == {"depth": "assumed", "plinth": "assumed", "material:carcass": "unknown_material"}

    new = MaterialSpec(key="ignored", name="Dąb dziki", kind="veneer")
    answered = finalize_module(apply_overrides(module, [Override(target="material:carcass", material=new)]))
    assert answered.carcass_material_key == "M2"
    assert answered.materials[-1].name == "Dąb dziki"


def test_merge_replaces_by_target_and_empty_override_removes():
    first = [Override(target="width", value_mm=1000), Override(target="depth", value_mm=500)]
    merged = merge_overrides(first, [Override(target="width", value_mm=1200), Override(target="depth")])
    assert [(o.target, o.value_mm) for o in merged] == [("width", 1200)]


def test_editor_changes_become_overrides():
    module = _resolve(_draft())
    edited = module.model_copy(deep=True)
    edited.width_mm = 1600
    edited.columns[0].cells[0].front = "drawer"
    edited.columns[0].cells[0].shelves = 2
    targets = {o.target: o for o in overrides_from_edit(module, edited)}
    assert targets["width"].value_mm == 1600
    assert targets["cell:c0r0:front"].front == "drawer"
    assert targets["cell:c0r0:shelves"].shelves == 2
    assert len(targets) == 3


def _two_view_module(open_view_marks: list[DimensionMark], open_chain: list[str]):
    marks = [
        _mark("D1", 2200, "vertical", 0.08, 0.35),  # main view height
        _mark("D2", 500, "horizontal", 0.15, 0.62),
        _mark("D3", 1000, "horizontal", 0.25, 0.62),
        *open_view_marks,
    ]
    by_id = {m.id: m for m in marks}
    chains = [
        DimensionChain(id="C1", orientation="horizontal", mark_ids=["D2", "D3"], sum_value=1500),
        DimensionChain(
            id="C2", orientation="horizontal", mark_ids=open_chain, sum_value=sum(by_id[i].value for i in open_chain)
        ),
    ]
    draft = _draft(
        height_value=None,
        height_mark="D1",
        columns=[
            ColumnDraft(width_mark="D2", cells=[CellDraft(col=0, row=0, front="open")]),
            ColumnDraft(width_mark="D3", cells=[CellDraft(col=1, row=0, front="open")]),
        ],
        views=[ModuleView(kind="front_open", bbox=OPEN_VIEW, label="otwarta")],
    )
    return _resolve(draft, marks=marks, chains=chains)


def test_open_view_confirms_height_and_contradicts_column_width():
    module = _two_view_module(
        [
            _mark("D4", 2200, "vertical", 0.38, 0.35),  # open view repeats the height
            _mark("D5", 500, "horizontal", 0.45, 0.62),
            _mark("D6", 950, "horizontal", 0.55, 0.62),  # ... but the second column differs
            _mark("D7", 1500, "horizontal", 0.5, 0.64),  # overall width: same element
        ],
        ["D5", "D6"],
    )
    checks = {c.dimension: c for c in module.view_checks}
    assert (checks["height"].status, checks["height"].mark_id) == ("match", "D4")
    assert (checks["width"].status, checks["width"].mark_id) == ("match", "D7")
    assert checks["col:0:width"].status == "match"
    assert (checks["col:1:width"].status, checks["col:1:width"].found_mm) == ("mismatch", 950)
    mismatch = next(i for i in module.issues if i.code == "VIEW_MISMATCH")
    assert mismatch.severity == "error"
    assert set(mismatch.mark_ids) == {"D3", "D6"}


def test_view_of_a_differently_sized_element_is_not_compared_column_by_column():
    # The model attached a neighbouring module's elevation (1300 wide) as a second view.
    module = _two_view_module(
        [_mark("D5", 300, "horizontal", 0.45, 0.62), _mark("D6", 1000, "horizontal", 0.55, 0.62)],
        ["D5", "D6"],
    )
    checks = {c.dimension: c for c in module.view_checks}
    assert checks["width"].status == "mismatch"
    assert "col:0:width" not in checks
    assert sum(i.code == "VIEW_MISMATCH" for i in module.issues) == 1


def test_depth_is_read_from_the_side_view_when_the_model_gave_none():
    marks = [_mark("D1", 580, "horizontal", 0.75, 0.62), _mark("D2", 2200, "vertical", 0.82, 0.35)]
    draft = _draft(depth_value=None, views=[ModuleView(kind="side", bbox=SIDE_VIEW)])
    module = _resolve(draft, marks=marks)
    assert module.depth_mm == 580
    assert module.dimension_sources["depth"] == "cross_view"
    assert module.source_mark_ids["depth"] == "D1"
    assert not any(i.code == "DEPTH_ASSUMED" for i in module.issues)
    assert next(c for c in module.view_checks if c.dimension == "height").status == "match"


def test_same_module_on_two_drawings_is_cross_checked():
    here = _resolve(_draft(depth_value=None))
    elsewhere = _resolve(_draft(width_value=1450)).model_copy(update={"module_id": "ZM05-2", "file_name": "y.pdf"})
    updated = cross_drawing_checks([ModuleModel(module=here), ModuleModel(module=elsewhere)])

    mine = updated["ZM05"]
    assert mine.depth_mm == 600
    assert mine.dimension_sources["depth"] == "cross_view"
    width_check = next(c for c in mine.view_checks if c.dimension == "width")
    assert (width_check.status, width_check.found_mm, width_check.source) == ("mismatch", 1450, "drawing")
    assert any(i.code == "VIEW_MISMATCH" for i in mine.issues)
    assert next(c for c in updated["ZM05-2"].view_checks if c.dimension == "height").status == "match"


def test_model_flags_contradictions_and_unconfirmed_values_are_review_items():
    marks = [_mark("D1", 1500, "horizontal", 0.2, 0.62)]
    draft = _draft(
        width_mark="D1",
        width_value=None,
        columns=[
            ColumnDraft(width_value=700, cells=[CellDraft(col=0, row=0, front="drawer")]),
            ColumnDraft(width_value=700, cells=[CellDraft(col=1, row=0, front="door_left", shelves=2)]),
        ],
        uncertain=[
            UncertainItem(field="cell_front", column=2, row=1, reason="Swing symbol barely visible."),
            UncertainItem(field="cell_shelves", column=9, row=1, reason="Out of range: ignored."),
        ],
    )
    module = _resolve(draft, marks=marks)
    review = {q.id: q for q in module.questions}
    # 700 + 700 ≠ 1500: both widths and the module width take part in the contradiction.
    assert review["width"].reason == "conflict"
    assert review["col:0:width"].reason == "conflict"
    assert "1400" in review["col:0:width"].note
    assert (review["cell:c1r0:front"].reason, review["cell:c1r0:front"].current_value) == ("model_unsure", "door_left")
    assert review["cell:c1r0:front"].note == "Swing symbol barely visible."
    assert review["cell:c1r0:front"].kind == "front"
    # Read by the model with no printed mark on a page that has a text layer.
    assert review["height"].reason == "unverified"
    assert not any(q.id.startswith("cell:c8") for q in module.questions)


def test_confirmed_values_leave_the_review_list():
    marks = [_mark("D1", 1500, "horizontal", 0.2, 0.62)]
    draft = _draft(
        width_mark="D1",
        width_value=None,
        columns=[ColumnDraft(width_value=1500, cells=[CellDraft(col=0, row=0, front="drawer")])],
        uncertain=[UncertainItem(field="cell_front", column=1, row=1, reason="unclear")],
    )
    module = _resolve(draft, marks=marks)
    assert "cell:c0r0:front" in {q.id for q in module.questions}

    confirmed = finalize_module(
        apply_overrides(
            module,
            [Override(target="cell:c0r0:front", front="drawer"), Override(target="height", value_mm=2200)],
        )
    )
    ids = {q.id for q in confirmed.questions}
    assert "cell:c0r0:front" not in ids
    assert "height" not in ids
    assert confirmed.columns[0].cells[0].front == "drawer"
    assert confirmed.dimension_sources["height"] == "user"


def test_label_inside_the_view_is_not_taken_as_its_overall_size():
    # The open view only labels a 1100 mm interior opening (inside the drawing): no contradiction.
    marks = [_mark("D1", 1500, "horizontal", 0.2, 0.62), _mark("D2", 1100, "horizontal", 0.5, 0.35)]
    draft = _draft(
        width_mark="D1",
        width_value=None,
        columns=[],
        views=[ModuleView(kind="front_open", bbox=OPEN_VIEW)],
    )
    module = _resolve(draft, marks=marks)
    assert [c.status for c in module.view_checks if c.dimension == "width"] == ["absent"]
    assert not any(i.code == "VIEW_MISMATCH" for i in module.issues)


def test_side_view_with_conflicting_overall_depths_leaves_depth_to_the_user():
    marks = [_mark("D1", 580, "horizontal", 0.75, 0.62), _mark("D2", 700, "horizontal", 0.75, 0.64)]
    module = _resolve(_draft(depth_value=None, views=[ModuleView(kind="side", bbox=SIDE_VIEW)]), marks=marks)
    assert module.dimension_sources["depth"] == "default"
    assert "depth" in {q.id for q in module.questions}


def test_row_chains_are_compared_with_the_column_they_stand_next_to():
    # Main view (x 0.1-0.3): left column rows 1000/1100, right column rows 600/1500. The open view repeats
    # both splits on the matching side; nothing contradicts.
    marks = [
        _mark("D1", 2200, "vertical", 0.08, 0.35),
        _mark("D2", 2200, "vertical", 0.36, 0.35),  # open view overall height
        _mark("D3", 1000, "vertical", 0.39, 0.25),  # open view, left column rows
        _mark("D4", 1100, "vertical", 0.39, 0.45),
        _mark("D5", 600, "vertical", 0.61, 0.2),  # open view, right column rows
        _mark("D6", 1500, "vertical", 0.61, 0.45),
    ]
    chains = [
        DimensionChain(id="C1", orientation="vertical", mark_ids=["D3", "D4"], sum_value=2100),
        DimensionChain(id="C2", orientation="vertical", mark_ids=["D5", "D6"], sum_value=2100),
    ]
    draft = _draft(
        height_value=None,
        height_mark="D1",
        width_value=1000,
        columns=[
            ColumnDraft(
                width_value=500,
                cells=[
                    CellDraft(col=0, row=0, front="open", height_value=1000),
                    CellDraft(col=0, row=1, front="open", height_value=1100),
                ],
            ),
            ColumnDraft(
                width_value=500,
                cells=[
                    CellDraft(col=1, row=0, front="open", height_value=600),
                    CellDraft(col=1, row=1, front="open", height_value=1500),
                ],
            ),
        ],
        views=[ModuleView(kind="front_open", bbox=OPEN_VIEW)],
    )
    module = _resolve(draft, marks=marks, chains=chains)
    cell_checks = {c.dimension: c.status for c in module.view_checks if c.dimension.startswith("cell:")}
    assert cell_checks == {
        "cell:c0r0:height": "match",
        "cell:c0r1:height": "match",
        "cell:c1r0:height": "match",
        "cell:c1r1:height": "match",
    }


def test_height_not_above_the_plinth_is_flagged():
    module = _resolve(_draft(height_value=80))
    assert module.status == "incomplete"
    assert any(i.code == "DIMENSION_MISSING" and "plinth" in i.message for i in module.issues)
    assert {q.id: q.reason for q in module.questions}["height"] == "conflict"


def test_cell_material_can_be_reset_to_the_module_default():
    module = _resolve(_draft(materials=[*MATERIALS, MaterialSpec(key="M2", name="Dąb", kind="veneer")]))
    with_oak = apply_overrides(module, [Override(target="cell:c0r0:material", material_key="M2")])
    assert with_oak.columns[0].cells[0].material_key == "M2"
    reset = apply_overrides(with_oak, [Override(target="cell:c0r0:material", material_key="")])
    assert reset.columns[0].cells[0].material_key is None


def test_zero_width_columns_take_the_answered_module_width():
    module = _resolve(_draft(width_value=None, columns=[ColumnDraft(cells=[CellDraft(col=0, row=0, front="open")])]))
    answered = finalize_module(apply_overrides(module, [Override(target="width", value_mm=900)]))
    assert [(c.width_mm, c.width_source) for c in answered.columns] == [(900, "derived")]
    assert answered.status == "ok"
