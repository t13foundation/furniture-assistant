from __future__ import annotations

import asyncio
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from src.furniture.schema import CellDraft, ColumnDraft, MaterialSpec, ModuleDraft, PageDraft
from src.schema.extraction import BoundingBox
from src.webapp import analysis, api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path))
    with TestClient(api.app) as test_client:
        yield test_client


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="PNG")
    return out.getvalue()


def _create(client: TestClient) -> str:
    response = client.post(
        "/api/projects", data={"name": "Demo"}, files=[("files", ("szafa.png", _png(), "image/png"))]
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_create_project_and_reject_unsupported_files(client):
    project_id = _create(client)
    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["project"]["files"][0]["name"] == "szafa.png"
    assert detail["modules"] == []

    rejected = client.post("/api/projects", files=[("files", ("koszty.xlsx", b"x", "application/octet-stream"))])
    assert rejected.status_code == 400

    page = client.get(f"/api/projects/{project_id}/pages/0/0.png")
    assert page.status_code == 200
    assert page.headers["content-type"] == "image/png"
    assert client.get(f"/api/projects/{project_id}/pages/0/3.png").status_code == 404
    assert client.get("/api/projects/nope").status_code == 404


def test_put_module_builds_and_exports(client, two_door_module):
    project_id = _create(client)
    response = client.put(f"/api/projects/{project_id}/modules/TEST", json=two_door_module.model_dump(mode="json"))
    assert response.status_code == 200
    model = response.json()
    assert len(model["solids"]) == 9
    assert model["module"]["issues"] == []

    listed = client.get(f"/api/projects/{project_id}").json()["modules"]
    assert listed[0]["module_id"] == "TEST"
    assert listed[0]["issue_counts"] == {"error": 0, "warning": 0}

    csv_response = client.get(f"/api/projects/{project_id}/modules/TEST/export/cutlist.csv")
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert len(csv_response.text.strip().splitlines()) == 8
    for fmt in ("cutlist.xlsx", "sheets.svg", "panels.dxf"):
        assert client.get(f"/api/projects/{project_id}/export/{fmt}").status_code == 200
    assert client.get(f"/api/projects/{project_id}/export/model.obj").status_code == 404


def test_put_module_recomputes_derived_issues(client, two_door_module):
    project_id = _create(client)
    body = two_door_module.model_dump(mode="json")
    body["columns"][0]["width_mm"] = 500
    model = client.put(f"/api/projects/{project_id}/modules/TEST", json=body).json()
    assert [i["code"] for i in model["module"]["issues"]] == ["COLUMNS_NOT_SUM"]
    assert model["module"]["confidence"] == "LOW"
    assert client.put(f"/api/projects/{project_id}/modules/TEST", json={"width_mm": 1}).status_code == 422


def test_second_analyze_while_running_conflicts(client, monkeypatch):
    async def slow_analysis(*_args, **_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(api, "run_analysis", slow_analysis)
    project_id = _create(client)
    assert client.post(f"/api/projects/{project_id}/analyze").status_code == 202
    assert client.post(f"/api/projects/{project_id}/analyze").status_code == 409
    assert client.post("/api/projects/missing/analyze").status_code == 404


def _wait_for(client: TestClient, url: str, done, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(url).json()
        if done(body):
            return body
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting on {url}")


def test_feedback_loop_reruns_model_then_takes_user_answers(client, monkeypatch):
    box = BoundingBox(x_min=0.1, y_min=0.1, x_max=0.9, y_max=0.9)
    first = PageDraft(
        units="mm",
        modules=[
            ModuleDraft(
                module_id="S1",
                title="Szafa",
                view_bbox=box,
                height_value=2200,
                columns=[ColumnDraft(cells=[CellDraft(col=0, row=0, front="door_left")])],
            )
        ],
    )
    refine_calls: list[list[str]] = []
    from types import SimpleNamespace

    async def interpret(*_args, **kwargs):
        await kwargs["on_usage"](SimpleNamespace(requests=1, input_tokens=100, output_tokens=20))
        return first

    async def refine(_agent, _image, module, draft, **kwargs):
        await kwargs["on_usage"](SimpleNamespace(requests=1, input_tokens=30, output_tokens=10))
        refine_calls.append([q.id for q in module.questions])
        # The model finds the materials but still not the width.
        materials = [MaterialSpec(key="M1", name="Płyta biała", kind="laminated_board")]
        return draft.model_copy(
            update={"materials": materials, "carcass_material_key": "M1", "front_material_key": "M1"}
        )

    monkeypatch.setattr(analysis, "create_interpretation_agent", lambda *_args: None)
    monkeypatch.setattr(analysis, "create_refine_agent", lambda *_args: None)
    monkeypatch.setattr(analysis, "interpret_page", interpret)
    monkeypatch.setattr(analysis, "refine_draft", refine)

    uploaded = client.post(
        "/api/projects",
        files=[
            ("files", ("drawing-1.png", _png(), "image/png")),
            ("files", ("drawing-2.png", _png(), "image/png")),
        ],
    )
    project_id = uploaded.json()["id"]
    api.app.state.config.models.cost_per_million_input = 2.0
    api.app.state.config.models.cost_per_million_output = 4.0
    assert client.post(f"/api/projects/{project_id}/analyze").status_code == 202
    analyzed = _wait_for(client, f"/api/projects/{project_id}", lambda body: body["job"]["status"] == "done")
    assert analyzed["job"]["usage"] == {
        "requests": 4,
        "input_tokens": 260,
        "output_tokens": 60,
        "estimated_cost": 0.00076,
    }
    assert len(analyzed["job"]["usage_breakdown"]) == 4
    assert analyzed["usage"] == analyzed["job"]["usage"]

    url = f"/api/projects/{project_id}/modules/S1"
    model = client.get(url).json()
    assert refine_calls == [
        ["width", "depth", "plinth", "material:carcass", "material:front"],
        ["width", "depth", "plinth", "material:carcass", "material:front"],
    ]
    assert model["refine_attempts"] == 1
    assert model["module"]["status"] == "incomplete"
    assert [q["id"] for q in model["module"]["questions"]] == ["width", "depth", "plinth"]
    assert model["solids"] == []

    answered = client.post(
        f"{url}/answers",
        json={"overrides": [{"target": "width", "value_mm": 600}, {"target": "depth", "value_mm": 560}]},
    )
    assert answered.status_code == 200
    module = answered.json()["module"]
    assert module["status"] == "ok"
    assert module["dimension_sources"]["width"] == "user"
    assert [q["id"] for q in module["questions"]] == ["plinth"]
    assert len(answered.json()["solids"]) > 0
    assert (
        client.post(f"{url}/answers", json={"overrides": [{"target": "col:7:width", "value_mm": 1}]}).status_code == 422
    )

    # Asking the model again re-resolves the interpretation and keeps the user's answers.
    assert client.post(f"{url}/refine").status_code == 202
    refined = _wait_for(client, url, lambda body: body["refine_status"] != "running")
    assert refined["refine_attempts"] == 2
    assert refined["module"]["width_mm"] == 600
    assert [o["target"] for o in refined["overrides"]] == ["width", "depth"]
    final_detail = client.get(f"/api/projects/{project_id}").json()
    assert final_detail["usage"]["requests"] == 5
    assert final_detail["usage"]["input_tokens"] == 290
    # The manual re-run is its own job: its usage is that one model call; the project total keeps everything.
    assert final_detail["job"]["usage"]["requests"] == 1
    assert final_detail["job"]["usage"]["input_tokens"] == 30


def test_refine_needs_a_model_interpretation(client, two_door_module):
    project_id = _create(client)
    client.put(f"/api/projects/{project_id}/modules/TEST", json=two_door_module.model_dump(mode="json"))
    assert client.post(f"/api/projects/{project_id}/modules/TEST/refine").status_code == 422


def test_removing_an_answer_on_a_hand_made_module_restores_its_value(client, two_door_module):
    project_id = _create(client)
    url = f"/api/projects/{project_id}/modules/TEST"
    client.put(url, json=two_door_module.model_dump(mode="json"))
    answered = client.post(f"{url}/answers", json={"overrides": [{"target": "depth", "value_mm": 450}]}).json()
    assert answered["module"]["depth_mm"] == 450
    removed = client.post(f"{url}/answers", json={"overrides": [{"target": "depth"}]}).json()
    assert removed["module"]["depth_mm"] == 600
    assert removed["overrides"] == []


def test_model_rerun_keeps_answers_given_while_it_ran(tmp_path):
    from src.furniture.resolve import resolve_module
    from src.furniture.rules import ConstructionRules
    from src.webapp.analysis import apply_refined, rebuild
    from src.webapp.store import ProjectStore

    rules = ConstructionRules()
    box = BoundingBox(x_min=0.1, y_min=0.1, x_max=0.9, y_max=0.9)
    draft = ModuleDraft(module_id="S1", title="Szafa", view_bbox=box, height_value=2200)
    module = resolve_module(draft, [], [], "mm", "x.png", 0, rules)
    store = ProjectStore(tmp_path)
    project = store.create_project("p", [("x.png", b"x")])
    store.save_module(project.id, rebuild(module, rules, draft=draft))

    # While the model runs, the user answers the width ...
    before = store.load_module(project.id, "S1")
    from src.furniture.resolve import assemble_module
    from src.furniture.schema import Override

    answer = [Override(target="width", value_mm=900)]
    store.save_module(
        project.id, rebuild(assemble_module(draft, before.module, answer, rules), rules, draft=draft, overrides=answer)
    )
    # ... and the re-run (which found the materials) is merged into the module as it is now.
    materials = [MaterialSpec(key="M1", name="Biała", kind="laminated_board")]
    new_draft = draft.model_copy(update={"materials": materials, "carcass_material_key": "M1"})
    merged = apply_refined(store.load_module(project.id, "S1"), new_draft, rules)
    assert merged.module.width_mm == 900
    assert [o.target for o in merged.overrides] == ["width"]
    assert merged.module.carcass_material_key == "M1"


def test_concurrent_module_writes_never_fail_or_corrupt(tmp_path, two_door_module):
    import threading

    from src.furniture.schema import ModuleModel
    from src.webapp.store import ProjectStore

    store = ProjectStore(tmp_path)
    project = store.create_project("p", [("x.png", b"x")])
    errors: list[Exception] = []

    def write(n: int) -> None:
        try:
            for _ in range(20):
                store.save_module(project.id, ModuleModel(module=two_door_module.model_copy(update={"title": f"t{n}"})))
        except Exception as exc:  # pragma: no cover - the assertion reports it
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert store.load_module(project.id, "TEST").module.title.startswith("t")
