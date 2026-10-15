from __future__ import annotations

import pytest
from src.furniture.build import build_module
from src.furniture.legend import legend_materials
from src.furniture.marks import extract_marks
from src.furniture.resolve import apply_overrides, overrides_from_edit, resolve_module
from src.furniture.rules import ConstructionRules
from src.furniture.schema import (
    Cell,
    CellDraft,
    Column,
    ColumnDraft,
    MaterialSpec,
    ModuleDraft,
    Override,
    TextRun,
)
from src.schema.extraction import BoundingBox

from tests.conftest import sample_file

BOX = BoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.9)


def _text(text: str) -> TextRun:
    return TextRun(text=text, bbox=BoundingBox(x_min=0.2, y_min=0.2, x_max=0.4, y_max=0.22))


def test_legend_parser_keeps_decor_text_and_infers_material_kind():
    materials = legend_materials(
        [
            _text("MATERIAŁY ZABUDÓW MEBLOWYCH"),
            _text("Kronospan, Orzech Rockford Ciemny K087 PW"),
            _text("MDF lakierowany, kolor czarny RAL 9005"),
            _text("Fenix NTM Nero Ingo"),
            _text("Corian / Grantex"),
        ]
    )
    assert [(m.key, m.kind) for m in materials] == [
        ("L1", "laminated_board"),
        ("L2", "lacquered_mdf"),
        ("L3", "hpl"),
        ("L4", "stone"),
    ]
    assert materials[0].name == "Kronospan, Orzech Rockford Ciemny K087 PW"


@pytest.mark.parametrize(
    "file_name, expected",
    [
        ("GLADIOGROUP_P02_PW-ZM05_12.pdf", ("Kronospan", "K087 PW")),
        ("06_A_METROPILITAN_NRF-zabudowy meblowe_widoki.pdf", ("EGGER", "H309 ST12")),
    ],
)
def test_real_drawing_text_exposes_material_specs(file_name: str, expected: tuple[str, str]):
    path = sample_file(file_name)
    if path is None:
        pytest.skip(f"sample drawing not available: {file_name}")
    _, runs = extract_marks(str(path), 0)
    materials = legend_materials(runs)
    names = " | ".join(m.name for m in materials)
    assert all(piece.casefold() in names.casefold() for piece in expected)


def test_resolution_infers_kind_group_legend_and_material_suggestions():
    draft = ModuleDraft(
        module_id="ZM03-A",
        title="Widok A",
        view_bbox=BOX,
        width_value=1200,
        height_value=2200,
        columns=[ColumnDraft(cells=[CellDraft(col=0, row=0, front="door_left", height_value=2100)])],
    )
    module = resolve_module(
        draft, [], [], "mm", "wall.pdf", 0, ConstructionRules(), [_text("Kronospan K087 PW laminat")]
    )
    assert module.group_id == "ZM03"
    assert module.kind == "cabinet"
    assert module.legend_materials[0].key == "L1"
    question = next(q for q in module.questions if q.id == "material:carcass")
    assert question.candidate_values == ["L1"]
    updated = apply_overrides(module, [Override(target="material:carcass", material_key="L1")])
    assert updated.carcass_material_key == "L1"
    assert any(m.key == "L1" for m in updated.materials)


def test_kind_inference_and_kind_group_overrides(two_door_module):
    draft = ModuleDraft(module_id="ZM03-B", title="Widok B", view_bbox=BOX, width_value=1200, height_value=2200)
    inferred = resolve_module(
        draft, [], [], "mm", "wall.pdf", 0, ConstructionRules(), [_text("LAMELE płyta laminowana")]
    )
    assert inferred.kind == "wall_cladding"
    assert inferred.group_id == "ZM03"
    edited = inferred.model_copy(update={"kind": "wardrobe", "group_id": "CUSTOM"})
    overrides = overrides_from_edit(inferred, edited)
    assert {override.target for override in overrides} >= {"kind", "group"}
    updated = apply_overrides(inferred, overrides)
    assert (updated.kind, updated.group_id) == ("wardrobe", "CUSTOM")
    assert apply_overrides(two_door_module, [Override(target="kind", kind="bench")]).kind == "bench"


def test_material_candidates_prioritize_carcass_and_front_keywords():
    draft = ModuleDraft(
        module_id="ZM1",
        title="Cabinet",
        view_bbox=BOX,
        width_value=1200,
        height_value=2200,
        columns=[ColumnDraft(cells=[CellDraft(col=0, row=0, front="door_left", height_value=2100)])],
    )
    module = resolve_module(
        draft,
        [],
        [],
        "mm",
        "x.pdf",
        0,
        ConstructionRules(),
        [_text("RAL 9005"), _text("FRONTY EGGER H1369 ST40")],
    )
    questions = {question.id: question for question in module.questions}
    assert questions["material:carcass"].candidate_values == ["L1", "L2"]
    assert questions["material:front"].candidate_values == ["L2", "L1"]


def test_implausible_dimensions_warn_and_create_conflict_question():
    module = resolve_module(
        ModuleDraft(module_id="ZM03", title="Cabinet", view_bbox=BOX, width_value=9000, height_value=2200),
        [],
        [],
        "mm",
        "x.png",
        0,
        ConstructionRules(),
    )
    warning = next(issue for issue in module.issues if issue.code == "DIMENSION_IMPLAUSIBLE")
    assert warning.severity == "warning"
    assert any(
        question.id == "width" and question.reason == "conflict" and question.note == warning.message
        for question in module.questions
    )


def test_wall_cladding_build_has_panels_battens_and_m2_installation(two_door_module):
    module = two_door_module.model_copy(deep=True)
    module.kind = "wall_cladding"
    module.plinth_mm = 0
    module.depth_mm = 20
    module.columns = [
        Column(index=0, width_mm=1200, cells=[Cell(id="c0r0", col=0, row=0, front="open", height_mm=2200)])
    ]
    result = build_module(module, ConstructionRules())
    assert [solid.name for solid in result.solids].count("Panel okładziny") == 1
    battens = [solid for solid in result.solids if solid.name == "Listwa montażowa"]
    assert len(battens) == 4
    assert not any(solid.name in {"Bok", "Plecy", "Cokół", "Wieniec górny"} for solid in result.solids)
    assert all(item.category == "mounting" for item in result.hardware)
    assert {line.unit for line in result.processes} == {"m2"}
    rules = ConstructionRules()
    # Battens stay inside the cladding height even when it divides the spacing evenly.
    assert max(solid.position_mm[1] + solid.size_mm[1] for solid in battens) <= module.height_mm
    # The legend's board thickness wins over the construction default.
    module.materials = [
        MaterialSpec(key="M9", name="Płyta laminowana czarna 30", kind="laminated_board", thickness_mm=30)
    ]
    module.front_material_key = "M9"
    thick = build_module(module, rules)
    panel = next(solid for solid in thick.solids if solid.name == "Panel okładziny")
    assert panel.thickness_mm == 30


def test_kind_hardware_rules_can_disable_categories(two_door_module):
    module = two_door_module.model_copy(update={"kind": "bench"})
    hardware_rules = ConstructionRules().hardware.__class__(disabled_categories_by_kind={"bench": ("leg",)})
    result = build_module(module, ConstructionRules(hardware=hardware_rules))
    assert all(item.category != "leg" for item in result.hardware)
