from __future__ import annotations

import multiprocessing
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from src.webapp import api
from src.webapp.store import Job, ProjectStore, now_iso


def _contend(root: str, project_id: str, start, active, maximum) -> None:
    store = ProjectStore(Path(root))
    start.wait()
    for _ in range(12):
        with store.lock(project_id):
            with active.get_lock():
                active.value += 1
                maximum.value = max(maximum.value, active.value)
            time.sleep(0.005)
            with active.get_lock():
                active.value -= 1


def test_project_lock_serializes_processes(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create_project("test", [("drawing.png", b"png")])
    context = multiprocessing.get_context("fork")
    start = context.Event()
    active = context.Value("i", 0)
    maximum = context.Value("i", 0)
    processes = [
        context.Process(target=_contend, args=(str(tmp_path), project.id, start, active, maximum)) for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert maximum.value == 1


def test_startup_resumes_stale_analysis(tmp_path, monkeypatch):
    output = tmp_path / "output"
    monkeypatch.setenv("FA_OUTPUT_DIR", str(output))
    store = ProjectStore(output / "webapp" / "projects")
    project = store.create_project("test", [("drawing.png", b"png")])
    store.save_job(
        project.id,
        Job(status="running", owner="dead-host:123", heartbeat_at=(datetime.now() - timedelta(minutes=2)).isoformat()),
    )
    resumed = threading.Event()

    async def fake_analysis(project_id, project_store, config, rules):
        resumed.set()
        with project_store.lock(project_id):
            job = project_store.load_job(project_id)
            job.status, job.finished_at = "done", now_iso()
            project_store.save_job(project_id, job)

    monkeypatch.setattr(api, "run_analysis", fake_analysis)
    with TestClient(api.app) as client:
        deadline = time.monotonic() + 5
        while not resumed.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert resumed.is_set()
        response = client.get(f"/api/projects/{project.id}")
        assert response.json()["job"]["status"] == "done"
        activity = client.get(f"/api/projects/{project.id}/activity").json()
        assert any(entry["action"] == "analyze_resume" for entry in activity)


def test_fresh_foreign_job_is_running_and_rejects_analyze(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path / "output"))
    with TestClient(api.app) as client:
        project_id = client.post("/api/projects", files=[("files", ("drawing.png", b"png", "image/png"))]).json()["id"]
        store = client.app.state.store
        with store.lock(project_id):
            store.save_job(
                project_id,
                Job(status="running", owner="another-host:987654", heartbeat_at=now_iso()),
            )
        details = client.get(f"/api/projects/{project_id}").json()
        assert details["job"]["status"] == "running"
        response = client.post(f"/api/projects/{project_id}/analyze")
        assert response.status_code == 409
