from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.furniture.build import build_module
from src.furniture.pricing import BoardPrice, Pricing, value_project
from src.furniture.reports import material_summary, process_summary
from src.furniture.rules import load_rules
from src.furniture.schema import MaterialSpec
from src.webapp.reports import router
from src.webapp.store import ProjectStore


def _model(module):
    module = module.model_copy(deep=True)
    module.materials = [MaterialSpec(key="M1", name="White board", kind="laminated_board", thickness_mm=18)]
    module.carcass_material_key = "M1"
    module.front_material_key = "M1"
    return build_module(module, load_rules())


def test_material_process_summaries_and_priced_totals(two_door_module):
    model = _model(two_door_module)
    pricing = Pricing(
        boards=[BoardPrice(match="white", price_per_m2=100)],
        edge_banding_per_m=2,
        hardware={"hinge": 3, "handle": 1, "leg": 2, "connector": 0.2, "back_fixing": 0.1},
        labour_rate_per_hour=60,
    )
    valuation = value_project([model], load_rules(), pricing)
    for group in ("boards", "edges", "hardware", "processes"):
        priced_sum = sum(line.amount or 0 for line in valuation.lines if line.group == group)
        assert valuation.totals[group] == round(priced_sum, 2)
    assert valuation.totals["total_net"] == round(
        sum(valuation.totals[k] for k in ("boards", "edges", "hardware", "processes")), 2
    )
    assert valuation.unpriced == sum(line.amount is None for line in valuation.lines)
    assert all(line.amount is None or line.amount >= 0 for line in valuation.lines)
    assert valuation.per_module[0].amount == valuation.totals["total_net"]
    assert model.processes and model.hardware


def test_reports_router_endpoints(two_door_module, tmp_path, monkeypatch):
    import src.webapp.reports as report_routes

    pricing_path = tmp_path / "config" / "pricing.yaml"
    monkeypatch.setattr(report_routes, "_pricing_path", lambda: pricing_path)
    store = ProjectStore(tmp_path / "projects")
    project = store.create_project("test", [("drawing.png", b"test")])
    model = _model(two_door_module)
    store.save_module(project.id, model)

    app = FastAPI()
    app.include_router(router)
    app.state.store = store
    app.state.rules = load_rules()
    with TestClient(app) as client:
        materials = client.get(f"/api/projects/{project.id}/summary/materials")
        processes = client.get(f"/api/projects/{project.id}/summary/processes")
        valuation = client.get(f"/api/projects/{project.id}/valuation")
        workbook = client.get(f"/api/projects/{project.id}/export/valuation.xlsx")
        assert materials.status_code == processes.status_code == valuation.status_code == 200
        assert materials.json()["module_count"] == 1
        assert processes.json()["total_minutes"] > 0
        assert workbook.status_code == 200 and workbook.content[:2] == b"PK"
        assert client.get("/api/pricing").json()["currency"] == "PLN"
        updated = client.put(
            "/api/pricing", json={"currency": "PLN", "boards": [], "hardware": {}, "labour_rate_per_hour": 80}
        )
        assert updated.status_code == 200 and updated.json()["updated_at"]
        assert client.get(f"/api/projects/{project.id}/valuation").json()["unpriced"] > 0
        assert client.get("/api/projects/unknown/valuation").status_code == 404


def test_board_name_rules_respect_their_thickness(two_door_module):
    pricing = Pricing(
        boards=[
            BoardPrice(match="white", thickness_mm=19, price_per_m2=300),
            BoardPrice(match="white", thickness_mm=18, price_per_m2=100),
        ]
    )
    valuation = value_project([_model(two_door_module)], load_rules(), pricing)
    boards = {line.name: line.unit_price for line in valuation.lines if line.group == "boards"}
    assert boards == {"White board 18 mm": 100, "White board 19 mm": 300, "White board 8 mm": None}
    # No labour rate configured: process lines are unpriced, not free.
    processes = [line for line in valuation.lines if line.group == "processes"]
    assert processes and all(line.amount is None for line in processes)


def test_shared_hardware_cost_is_split_by_each_modules_quantity(two_door_module):
    small = _model(two_door_module.model_copy(update={"height_mm": 1900}))  # 1797 mm doors: 4 hinges each
    tall = _model(two_door_module.model_copy(update={"module_id": "TALL", "height_mm": 2400}))  # 5 hinges each
    valuation = value_project([small, tall], load_rules(), Pricing(hardware={"hinge": 10}))
    per_module = {m.module_id: m.amount for m in valuation.per_module}
    assert per_module == {"TEST": 80, "TALL": 100}


def test_parts_larger_than_the_sheet_are_reported_not_bought(two_door_module):
    tall = _model(two_door_module.model_copy(update={"height_mm": 3100, "plinth_mm": 100}))
    summary = material_summary([tall], load_rules())
    row = next(r for r in summary.boards if r.thickness_mm == 18)
    assert row.unplaced  # 3000 mm sides do not fit a 2800 mm sheet
    assert all(r.sheets == 0 or r.utilization > 0 for r in summary.boards)
    valuation = value_project([tall], load_rules(), Pricing(boards=[BoardPrice(price_per_m2=10, thickness_mm=18)]))
    assert any("arkusz" in note for note in valuation.notes)


def test_process_matrix_does_not_sum_mixed_units(two_door_module):
    model = _model(two_door_module)
    units = {line.unit for line in model.processes if line.process == "hardware_mounting"}
    cell = process_summary([model]).rows[0].processes["hardware_mounting"]
    if len(units) > 1:
        assert (cell.qty, cell.unit) == (None, None)
    else:
        assert cell.unit == next(iter(units))
    assert cell.minutes > 0


def test_pricing_rejects_invalid_currency():
    with pytest.raises(ValueError):
        Pricing(currency="PLN!")
