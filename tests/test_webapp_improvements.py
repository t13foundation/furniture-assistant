from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from src.furniture.rules import ConstructionRules
from src.furniture.schema import FurnitureModule, MaterialSpec, ModuleModel
from src.webapp import api

from tests.conftest import sample_file


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.delenv("FA_USERS_FILE", raising=False)
    with TestClient(api.app) as test_client:
        yield test_client


def _create(client: TestClient) -> str:
    response = client.post("/api/projects", files=[("files", ("cabinet.png", b"png", "image/png"))])
    assert response.status_code == 201
    return response.json()["id"]


def test_auth_disabled_and_login_protects_api(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("FA_USERS_FILE", str(tmp_path / "users.yaml"))
    monkeypatch.setenv("FA_SESSION_SECRET", "test-secret")
    with TestClient(api.app) as client:
        assert client.get("/api/auth/me").json() == {"username": None, "auth_enabled": False}
        assert _create(client)

    from src.webapp.auth import add_user

    add_user("operator", "s3cret")
    with TestClient(api.app) as client:
        assert client.get("/api/auth/me").json() == {"username": None, "auth_enabled": True}
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/login", json={"username": "operator", "password": "wrong"}).status_code == 401
        response = client.post("/api/auth/login", json={"username": "operator", "password": "s3cret"})
        assert response.status_code == 200
        assert response.json() == {"username": "operator"}
        assert response.cookies.get("fa_session")
        assert client.get("/api/projects").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/projects").status_code == 401


def test_health_is_public_and_reports_model_url(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("FA_USERS_FILE", str(tmp_path / "users.yaml"))
    monkeypatch.setenv("FA_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("FA_API_BASE", "http://model.example/v1")
    from src.webapp.auth import add_user

    add_user("operator", "s3cret")
    with TestClient(api.app) as client:
        assert client.get("/api/projects").status_code == 401
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model_base_url": "http://model.example/v1",
        "projects_root_writable": True,
        "auth_enabled": True,
    }


def test_upload_limits_return_413(client, monkeypatch):
    monkeypatch.setenv("FA_MAX_UPLOAD_MB", "0")
    response = client.post("/api/projects", files=[("files", ("cabinet.png", b"png", "image/png"))])
    assert response.status_code == 413
    assert response.json()["detail"] == "File cabinet.png exceeds 0 MB"

    monkeypatch.delenv("FA_MAX_UPLOAD_MB")
    monkeypatch.setenv("FA_MAX_FILES", "1")
    two = [("files", ("a.png", b"png", "image/png")), ("files", ("b.png", b"png", "image/png"))]
    response = client.post("/api/projects", files=two)
    assert response.status_code == 413
    assert response.json()["detail"] == "At most 1 files per project"
    assert client.get("/api/projects").json() == []


def test_audit_history_restore_and_review(client, two_door_module):
    project_id = _create(client)
    url = f"/api/projects/{project_id}/modules/TEST"
    original = two_door_module.model_dump(mode="json")
    assert client.put(url, json=original).status_code == 200
    changed = {**original, "title": "Changed"}
    assert client.put(url, json=changed).status_code == 200
    # Saving the same content again (or a cross-check pass that changes nothing) adds no version.
    assert client.post(f"{url}/answers", json={"overrides": []}).status_code == 200
    versions = client.get(f"{url}/versions").json()
    assert [entry["version"] for entry in versions] == [2, 1]
    assert client.get(f"{url}/versions/1").json()["module"]["title"] == original["title"]
    restored = client.post(f"{url}/versions/1/restore")
    assert restored.status_code == 200
    assert restored.json()["module"]["title"] == original["title"]
    activity = client.get(f"/api/projects/{project_id}/activity").json()
    assert activity[0]["action"] == "restore"
    assert {entry["action"] for entry in activity} >= {"create_project", "editor_save", "restore"}
    review = client.get(f"/api/projects/{project_id}/review").json()
    assert review[0]["group_id"] == two_door_module.group_id
    assert review[0]["kind"] == two_door_module.kind


def test_project_pricing_markup_and_quote_comparison(client, two_door_module):
    project_id = _create(client)
    assert (
        client.put(f"/api/projects/{project_id}/modules/TEST", json=two_door_module.model_dump(mode="json")).status_code
        == 200
    )
    pricing_url = f"/api/projects/{project_id}/pricing"
    inherited = client.get(pricing_url).json()
    assert inherited["inherited"] is True
    pricing = inherited["pricing"]
    pricing["markup_percent"] = 15
    saved = client.put(pricing_url, json=pricing)
    assert saved.status_code == 200 and saved.json()["inherited"] is False
    valuation = client.get(f"/api/projects/{project_id}/valuation").json()
    assert valuation["totals"]["markup"] == round(valuation["totals"]["total_net"] * 0.15, 2)
    assert valuation["totals"]["total_with_markup"] == valuation["totals"]["total_net"] + valuation["totals"]["markup"]
    assert valuation["per_group"][0]["group_id"] == (two_door_module.group_id or "TEST")

    workbook = Workbook()
    workbook.active.append(["ZM 01.1 - LADA test", 50000])
    stream = io.BytesIO()
    workbook.save(stream)
    quote_url = f"/api/projects/{project_id}/quote"
    quote = client.post(
        quote_url,
        files={
            "file": (
                "quote.xlsx",
                stream.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert quote.status_code == 200
    payload = quote.json()
    assert payload["lines"][0]["codes"] == ["ZM011"]
    assert payload["comparison"][0]["quote_amount"] == 50000
    assert client.get(quote_url).json()["lines"] == payload["lines"]
    reverted = client.delete(pricing_url)
    assert reverted.status_code == 200 and reverted.json()["inherited"] is True


def test_quote_manual_assignment_ignored_line_and_reimport(client, two_door_module):
    project_id = _create(client)
    assert (
        client.put(f"/api/projects/{project_id}/modules/TEST", json=two_door_module.model_dump(mode="json")).status_code
        == 200
    )
    workbook = Workbook()
    workbook.active.append(["ELEMENTY DODATKOWE", 50000])
    workbook.active.append(["POZYCJA DO POMINIĘCIA", 70000])
    stream = io.BytesIO()
    workbook.save(stream)
    upload = {
        "file": (
            "quote.xlsx",
            stream.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    url = f"/api/projects/{project_id}/quote"
    compared = client.post(url, files=upload).json()
    assert compared["comparison"][0]["match"] == "none"
    modules_url = f"/api/projects/{project_id}"
    module_id = client.get(modules_url).json()["modules"][0]["module_id"]
    assignment_url = f"{url}/assignments"
    mapped = client.put(
        assignment_url,
        json={"line_index": 0, "label": compared["lines"][0]["label"], "module_ids": [module_id]},
    )
    assert mapped.status_code == 200
    assert mapped.json()["comparison"][0]["match"] == "manual"
    assert mapped.json()["comparison"][0]["module_ids"] == [module_id]
    overlap = client.put(
        assignment_url,
        json={"line_index": 1, "label": compared["lines"][1]["label"], "module_ids": [module_id]},
    )
    assert overlap.status_code == 409

    ignored = client.put(
        assignment_url,
        json={"line_index": 1, "label": compared["lines"][1]["label"], "ignored": True},
    )
    assert ignored.status_code == 200
    assert ignored.json()["comparison"][1]["match"] == "ignored"
    assert ignored.json()["total_quote"] == 50000
    assert ignored.json()["suggested_markup_percent"] == mapped.json()["suggested_markup_percent"]

    reimported = client.post(url, files=upload)
    assert reimported.status_code == 200
    persisted = reimported.json()["comparison"]
    assert persisted[0]["match"] == "manual" and persisted[0]["module_ids"] == [module_id]
    assert persisted[1]["match"] == "ignored"


def test_quote_empty_import_is_retrievable_and_saves_atomically(client, monkeypatch):
    from src.webapp import reports

    project_id = _create(client)
    workbook = Workbook()
    stream = io.BytesIO()
    workbook.save(stream)
    original = reports._write_atomic
    written = []

    def write_atomic(path, text):
        written.append(path.name)
        original(path, text)

    monkeypatch.setattr(reports, "_write_atomic", write_atomic)
    url = f"/api/projects/{project_id}/quote"
    uploaded = client.post(
        url,
        files={
            "file": (
                "empty.xlsx",
                stream.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["lines"] == []
    assert client.get(url).status_code == 200
    assert client.get(url).json()["lines"] == []
    assert "quote.json" in written


def test_quote_reimport_carryover_uses_unique_label_and_amount(client):
    project_id = _create(client)
    url = f"/api/projects/{project_id}/quote"

    def workbook_upload(amounts):
        workbook = Workbook()
        for amount in amounts:
            workbook.active.append(["Transport", amount])
        stream = io.BytesIO()
        workbook.save(stream)
        return {
            "file": (
                "quote.xlsx",
                stream.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }

    first = client.post(url, files=workbook_upload([100, 500])).json()
    ignored = client.put(
        f"{url}/assignments",
        json={"line_index": 0, "label": first["lines"][0]["label"], "ignored": True},
    )
    assert ignored.status_code == 200

    imported = client.post(url, files=workbook_upload([500, 100]))
    assert imported.status_code == 200
    rows = imported.json()["comparison"]
    assert rows[0]["quote_amount"] == 500 and rows[0]["match"] == "none"
    assert rows[1]["quote_amount"] == 100 and rows[1]["match"] == "ignored"


def test_stale_manual_module_assignment_falls_back_to_code_match():
    from types import SimpleNamespace

    from src.furniture.pricing import ModuleAmount
    from src.furniture.quote import QuoteLine, compare_quote

    model = SimpleNamespace(module=SimpleNamespace(module_id="M1", group_id="ZM01", status="ok"))
    line = QuoteLine(label="ZM01 cabinet", codes=["ZM01"], amount=120, manual_module_ids=["deleted"])
    result = compare_quote(
        [line],
        [model],
        [ModuleAmount(module_id="M1", title="Cabinet", amount=100, markup=0, total_with_markup=100)],
    )
    assert result.comparison[0].match == "code"
    assert result.comparison[0].module_ids == ["M1"]
    assert line.manual_module_ids == []


def test_users_cli_and_markup_suggestion(tmp_path, monkeypatch, capsys):
    import sys
    from types import SimpleNamespace

    from src.furniture.pricing import ModuleAmount
    from src.furniture.quote import QuoteLine, compare_quote
    from src.webapp import users

    monkeypatch.setenv("FA_USERS_FILE", str(tmp_path / "users.yaml"))
    monkeypatch.setattr(sys, "argv", ["users", "add", "operator", "--password", "secret"])
    users.main()
    monkeypatch.setattr(sys, "argv", ["users", "list"])
    users.main()
    assert capsys.readouterr().out.strip() == "operator"
    monkeypatch.setattr(sys, "argv", ["users", "remove", "operator"])
    users.main()

    model = SimpleNamespace(module=SimpleNamespace(module_id="ZM01-1", group_id="ZM01", status="ok"))
    result = compare_quote(
        [QuoteLine(label="ZM 01.1 - Cabinet", codes=["ZM011"], amount=120)],
        [model],
        [ModuleAmount(module_id="ZM01-1", title="Cabinet", amount=100, markup=0, total_with_markup=100)],
    )
    assert result.comparison[0].app_amount == 100
    assert result.suggested_markup_percent == 20


def test_parse_real_client_quote_when_available():
    sample = sample_file("*WYCENA*.xlsx")
    if sample is None:
        pytest.skip("client quote sample is not available")
    from src.furniture.quote import parse_quote

    lines = parse_quote(sample)
    assert lines
    assert any(line.codes for line in lines)


def test_quote_line_covering_several_cabinets_is_compared_with_all_of_them(tmp_path):
    from types import SimpleNamespace

    from src.furniture.pricing import ModuleAmount
    from src.furniture.quote import compare_quote, parse_quote

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Lp.", "Opis", "Wartość"])
    # Client quotes separate code and description with an en dash.
    sheet.append([1, "ZKO2 \u2013 COFFEE POINT", 21500])
    sheet.append([2, "ZM09 ,ZM10,ZM11 \u2013 Lamele RAZEM 47,75m2", 20500])
    sheet.append([3, "ELEMENTY OZDOBNE S\u0141UP\u00d3W \u2013 4 KOMPLETY", 6000])
    workbook.save(tmp_path / "quote.xlsx")

    lines = parse_quote(tmp_path / "quote.xlsx")
    assert [line.codes for line in lines] == [["ZK02"], ["ZM09", "ZM10", "ZM11"], []]

    # The coffee point was read incompletely (priced at a fraction of its parts): it must not drive the markup.
    statuses = {"ZK02": "incomplete", "ZM09": "ok", "ZM10": "ok", "ZM11": "ok"}
    models = [SimpleNamespace(module=SimpleNamespace(module_id=m, group_id=m, status=s)) for m, s in statuses.items()]
    amounts = [ModuleAmount(module_id="ZK02", title="", amount=100, markup=0, total_with_markup=100)] + [
        ModuleAmount(module_id=m, title="", amount=1000, markup=0, total_with_markup=1000)
        for m in ("ZM09", "ZM10", "ZM11")
    ]
    result = compare_quote(lines, models, amounts)
    rows = result.comparison
    assert rows[1].app_amount == 3000 and rows[1].module_ids == ["ZM09", "ZM10", "ZM11"] and rows[1].complete
    assert not rows[0].complete
    assert rows[2].app_amount is None
    assert result.suggested_markup_percent == round((20500 / 3000 - 1) * 100, 2)
    assert result.suggestion_basis == "median quote/app ratio of 1 complete cabinet"


def test_clear_modules_archives_history_instead_of_deleting_it(client, two_door_module):
    from src.webapp.store import ProjectStore

    project_id = _create(client)
    url = f"/api/projects/{project_id}/modules/TEST"
    assert client.put(url, json=two_door_module.model_dump(mode="json")).status_code == 200
    store: ProjectStore = api.app.state.store
    with store.lock(project_id):
        store.clear_modules(project_id)
    modules_dir = store.project_dir(project_id) / "modules"
    assert not list(modules_dir.glob("*.json"))
    assert not (modules_dir / "history").exists()
    archives = [path for path in modules_dir.iterdir() if path.name.startswith("history-archive-")]
    assert len(archives) == 1 and (archives[0] / "TEST" / "1.json").is_file()
    assert client.get(f"{url}/versions").status_code == 404  # module is gone, its history archived


def test_valuation_per_cabinet_reconciles_with_the_marked_up_total(client, two_door_module):
    from src.furniture.pricing import Pricing, value_project

    def wrapped(module: FurnitureModule) -> ModuleModel:
        return ModuleModel(module=module, solids=[], cut_list=[])

    a = two_door_module.model_copy(deep=True)
    a.group_id = "ZM03"
    a.title = "WIDOK A / A VIEW"
    b = two_door_module.model_copy(deep=True)
    b.module_id = "ZM03-B"
    b.group_id = "ZM03"
    b.title = "BIUROWIEC / P6 / ZM03"
    for module in (a, b):
        module.materials = [MaterialSpec(key="M1", name="Płyta biała 18", kind="laminated_board", thickness_mm=18)]
        module.carcass_material_key = "M1"
    models = [wrapped(a), wrapped(b)]
    valuation = value_project(models, ConstructionRules(), Pricing(markup_percent=10))
    cabinet = valuation.per_group[0]
    assert cabinet.group_id == "ZM03"
    assert cabinet.title == "BIUROWIEC / P6 / ZM03"  # a view title names the view, not the cabinet
    assert cabinet.total_with_markup == pytest.approx(cabinet.amount + cabinet.markup)
    assert sum(row.total_with_markup for row in valuation.per_module) == pytest.approx(
        valuation.totals["total_with_markup"], abs=0.02
    )
