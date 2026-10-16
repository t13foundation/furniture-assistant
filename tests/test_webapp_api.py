from __future__ import annotations

import asyncio
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from src.furniture.schema import CellDraft, ColumnDraft, MaterialSpec, ModuleDraft, ModuleModel, PageDraft
from src.schema.extraction import BoundingBox
from src.webapp import analysis, api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path))

    async def model_available(_request):
        return None

    monkeypatch.setattr(api, "_probe_model_server", model_available)
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


def test_rename_project_updates_name_and_audit(client):
    project_id = _create(client)

    renamed = client.patch(f"/api/projects/{project_id}", json={"name": "  renamed workspace  "})

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "renamed workspace"
    activity = client.get(f"/api/projects/{project_id}/activity").json()
    assert activity[0]["action"] == "rename_project"
    assert activity[0]["detail"] == "renamed workspace"
    assert client.patch(f"/api/projects/{project_id}", json={"name": "  "}).status_code == 422
    assert client.patch(f"/api/projects/{project_id}", json={"name": "x" * 121}).status_code == 422


def test_delete_project_moves_to_trash_and_hides_it(client, tmp_path):
    project_id = _create(client)

    deleted = client.delete(f"/api/projects/{project_id}")

    assert deleted.status_code == 204
    trash = tmp_path / "webapp" / "trash"
    trashed_project = next(trash.glob(f"{project_id}-*"))
    audit = (trashed_project / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(audit[-1])["action"] == "delete_project"
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert client.get("/api/projects").json() == []
    assert client.delete(f"/api/projects/{project_id}").status_code == 404


def test_delete_project_conflicts_while_analysis_runs(client, monkeypatch):
    started = Event()
    release = Event()

    async def slow_analysis(*_args, **_kwargs):
        started.set()
        await asyncio.to_thread(release.wait)

    monkeypatch.setattr(api, "run_analysis", slow_analysis)
    project_id = _create(client)
    assert client.post(f"/api/projects/{project_id}/analyze").status_code == 202
    try:
        assert started.wait(timeout=3)
        assert client.delete(f"/api/projects/{project_id}").status_code == 409
        assert client.get(f"/api/projects/{project_id}").status_code == 200
        assert [project["id"] for project in client.get("/api/projects").json()] == [project_id]
    finally:
        release.set()


def test_analyze_rechecks_project_after_waiting_for_its_lock(client, monkeypatch):
    project_id = _create(client)
    store = client.app.state.store
    project_lock = store.lock(project_id)
    lock_requested = Event()
    original_lock = store.lock

    def observe_lock(requested_project_id):
        lock = original_lock(requested_project_id)
        if requested_project_id == project_id:
            lock_requested.set()
        return lock

    monkeypatch.setattr(store, "lock", observe_lock)
    project_lock.__enter__()
    released = False
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            response = executor.submit(client.post, f"/api/projects/{project_id}/analyze")
            assert lock_requested.wait(timeout=3)
            store.move_project_to_trash(project_id)
            project_lock.__exit__(None, None, None)
            released = True
            assert response.result(timeout=5).status_code == 404
    finally:
        if not released:
            project_lock.__exit__(None, None, None)


def test_delete_project_conflicts_while_refine_runs(client, monkeypatch, two_door_module):
    started = Event()
    release = Event()
    project_id = _create(client)
    draft = ModuleDraft(
        module_id="TEST",
        title="Test wardrobe",
        view_bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.9),
    )
    model = analysis.rebuild(two_door_module, client.app.state.rules, draft=draft)
    client.app.state.store.save_module(project_id, model)

    async def slow_refine(*_args, **_kwargs):
        started.set()
        await asyncio.to_thread(release.wait)

    monkeypatch.setattr(api, "refine_stored_module", slow_refine)
    assert client.post(f"/api/projects/{project_id}/modules/TEST/refine").status_code == 202
    try:
        assert started.wait(timeout=3)
        assert client.delete(f"/api/projects/{project_id}").status_code == 409
        assert client.get(f"/api/projects/{project_id}").status_code == 200
        assert [project["id"] for project in client.get("/api/projects").json()] == [project_id]
    finally:
        release.set()


def test_delete_project_does_not_audit_failed_move(client, monkeypatch):
    project_id = _create(client)
    store = client.app.state.store

    def fail_move(_project_id):
        raise OSError("trash is not writable")

    monkeypatch.setattr(store, "move_project_to_trash", fail_move)
    with pytest.raises(OSError, match="trash is not writable"):
        client.delete(f"/api/projects/{project_id}")
    assert client.get(f"/api/projects/{project_id}").status_code == 200
    assert all(
        entry["action"] != "delete_project" for entry in client.get(f"/api/projects/{project_id}/activity").json()
    )


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


def test_concurrent_analyze_requests_only_start_one_job(client, monkeypatch):
    both_probes_entered = Event()
    release_probes = Event()
    probe_count = 0

    async def blocked_probe(_request):
        nonlocal probe_count
        probe_count += 1
        if probe_count == 2:
            both_probes_entered.set()
        while not release_probes.is_set():
            await asyncio.sleep(0.01)

    async def slow_analysis(*_args, **_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(api, "_probe_model_server", blocked_probe)
    monkeypatch.setattr(api, "run_analysis", slow_analysis)
    project_id = _create(client)

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.post, f"/api/projects/{project_id}/analyze") for _ in range(2)]
        try:
            assert both_probes_entered.wait(timeout=5)
        finally:
            release_probes.set()
        statuses = [request.result(timeout=5).status_code for request in requests]

    assert sorted(statuses) == [202, 409]


def test_running_analysis_conflicts_before_unavailable_model_probe(client, monkeypatch):
    async def slow_analysis(*_args, **_kwargs):
        await asyncio.sleep(5)

    async def unavailable_probe(_request):
        raise api.HTTPException(503, "model server unavailable")

    monkeypatch.setattr(api, "run_analysis", slow_analysis)
    project_id = _create(client)
    assert client.post(f"/api/projects/{project_id}/analyze").status_code == 202
    monkeypatch.setattr(api, "_probe_model_server", unavailable_probe)

    response = client.post(f"/api/projects/{project_id}/analyze")

    assert response.status_code == 409


def _save_refinable_module(project_id: str, two_door_module) -> None:
    draft = ModuleDraft(
        module_id=two_door_module.module_id,
        title=two_door_module.title,
        view_bbox=two_door_module.view_bbox,
    )
    api.app.state.store.save_module(project_id, ModuleModel(module=two_door_module, draft=draft))


def test_concurrent_refine_requests_only_start_one_rerun(client, monkeypatch, two_door_module):
    both_probes_entered = Event()
    release_probes = Event()
    probe_count = 0

    async def blocked_probe(_request):
        nonlocal probe_count
        probe_count += 1
        if probe_count == 2:
            both_probes_entered.set()
        while not release_probes.is_set():
            await asyncio.sleep(0.01)

    async def slow_refine(*_args, **_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(api, "_probe_model_server", blocked_probe)
    monkeypatch.setattr(api, "refine_stored_module", slow_refine)
    project_id = _create(client)
    _save_refinable_module(project_id, two_door_module)
    url = f"/api/projects/{project_id}/modules/TEST/refine"

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.post, url) for _ in range(2)]
        try:
            assert both_probes_entered.wait(timeout=5)
        finally:
            release_probes.set()
        statuses = [request.result(timeout=5).status_code for request in requests]

    assert sorted(statuses) == [202, 409]


def test_analyze_and_refine_requests_conflict_after_probe(client, monkeypatch, two_door_module):
    both_probes_entered = Event()
    release_probes = Event()
    probe_count = 0

    async def blocked_probe(_request):
        nonlocal probe_count
        probe_count += 1
        if probe_count == 2:
            both_probes_entered.set()
        while not release_probes.is_set():
            await asyncio.sleep(0.01)

    async def slow_analysis(*_args, **_kwargs):
        await asyncio.sleep(5)

    async def slow_refine(*_args, **_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(api, "_probe_model_server", blocked_probe)
    monkeypatch.setattr(api, "run_analysis", slow_analysis)
    monkeypatch.setattr(api, "refine_stored_module", slow_refine)
    project_id = _create(client)
    _save_refinable_module(project_id, two_door_module)
    urls = [
        f"/api/projects/{project_id}/analyze",
        f"/api/projects/{project_id}/modules/TEST/refine",
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.post, url) for url in urls]
        try:
            assert both_probes_entered.wait(timeout=5)
        finally:
            release_probes.set()
        statuses = [request.result(timeout=5).status_code for request in requests]

    assert sorted(statuses) == [202, 409]


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

    async def interpret(*_args, **_kwargs):
        return first

    async def refine(_agent, _image, module, draft):
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

    project_id = _create(client)
    assert client.post(f"/api/projects/{project_id}/analyze").status_code == 202
    _wait_for(client, f"/api/projects/{project_id}", lambda body: body["job"]["status"] == "done")

    url = f"/api/projects/{project_id}/modules/S1"
    model = client.get(url).json()
    assert refine_calls == [["width", "depth", "plinth", "material:carcass", "material:front"]]
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
