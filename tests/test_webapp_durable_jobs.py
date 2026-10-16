from __future__ import annotations

import asyncio
import multiprocessing
import os
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.webapp import api
from src.webapp.store import Job, JobSupersededError, ProjectLock, ProjectStore, now_iso


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
        Job(
            status="running",
            owner="dead-host:123",
            heartbeat_at=(datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
        ),
    )
    resumed = threading.Event()

    async def fake_analysis(project_id, project_store, config, rules, claim_token):
        resumed.set()
        with project_store.lock(project_id):
            job = project_store.load_job(project_id)
            job.status, job.finished_at = "done", now_iso()
            project_store.save_job(project_id, job, claim_token)

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
        store = cast(FastAPI, client.app).state.store
        with store.lock(project_id):
            store.save_job(
                project_id,
                Job(status="running", owner="another-host:987654", heartbeat_at=now_iso()),
            )
        details = client.get(f"/api/projects/{project_id}").json()
        assert details["job"]["status"] == "running"
        response = client.post(f"/api/projects/{project_id}/analyze")
        assert response.status_code == 409


def test_touch_job_advances_heartbeat(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create_project("test", [("drawing.png", b"png")])
    old_heartbeat = "2000-01-01T00:00:00"
    claim_token = "claim"
    store.save_job(
        project.id,
        Job(status="running", owner="another-host:1", claim_token=claim_token, heartbeat_at=old_heartbeat),
    )

    api._touch_job(store, project.id, claim_token)

    job = store.load_job(project.id)
    assert job is not None and job.heartbeat_at is not None and job.heartbeat_at > old_heartbeat


def test_live_job_stays_fresh_and_rejects_second_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(api, "JOB_STALE_AFTER", timedelta(seconds=30))
    monkeypatch.setattr(api, "JOB_HEARTBEAT_SECONDS", 0.01)
    heartbeat = threading.Event()
    touch = api._touch_job

    def touched(store: ProjectStore, project_id: str, claim_token: str) -> None:
        touch(store, project_id, claim_token)
        heartbeat.set()

    monkeypatch.setattr(api, "_touch_job", touched)

    async def keep_running(project_id, store, _config, _rules, claim_token):
        await asyncio.Event().wait()

    monkeypatch.setattr(api, "run_analysis", keep_running)
    with TestClient(api.app) as client:
        project_id = client.post("/api/projects", files=[("files", ("drawing.png", b"png", "image/png"))]).json()["id"]
        assert client.post(f"/api/projects/{project_id}/analyze").status_code == 202
        assert heartbeat.wait(timeout=2)
        details = client.get(f"/api/projects/{project_id}").json()
        assert details["job"]["status"] == "queued"
        assert client.post(f"/api/projects/{project_id}/analyze").status_code == 409


def test_missing_project_pricing_and_quote_are_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path / "output"))
    with TestClient(api.app) as client:
        assert client.get("/api/projects/missing/pricing").status_code == 404
        assert client.get("/api/projects/missing/quote").status_code == 404


def test_job_claim_fences_old_runner_writes(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create_project("test", [("drawing.png", b"png")])
    old = Job(status="running", owner="worker:1", claim_token="old", heartbeat_at=now_iso())
    store.save_job(project.id, old)
    store.save_job(
        project.id,
        Job(status="running", owner="worker:2", claim_token="new", heartbeat_at=now_iso()),
    )

    with store.lock(project.id), pytest.raises(JobSupersededError):
        store.save_job(project.id, old, claim_token="old")
    saved = store.load_job(project.id)
    assert saved is not None and saved.claim_token == "new"


def test_heartbeat_timestamps_are_utc_and_legacy_naive_is_fresh(monkeypatch):
    assert datetime.fromisoformat(now_iso()).utcoffset() == UTC.utcoffset(datetime.now(UTC))
    monkeypatch.setattr(api, "JOB_STALE_AFTER", timedelta(seconds=30))
    legacy = (datetime.now() - timedelta(seconds=2)).isoformat()
    assert api._job_fresh(Job(status="running", owner="remote:1", heartbeat_at=legacy))


def test_legacy_naive_heartbeat_uses_local_timezone(monkeypatch):
    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    time.tzset()
    try:
        monkeypatch.setattr(api, "JOB_STALE_AFTER", timedelta(seconds=30))
        heartbeat = (datetime.now() - timedelta(hours=2)).isoformat()
        assert not api._job_fresh(Job(status="running", owner="remote:1", heartbeat_at=heartbeat))
    finally:
        if previous_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", previous_tz)
        time.tzset()


def test_stale_refine_recovery_invalidates_old_runner_claim(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create_project("test", [("drawing.png", b"png")])
    old = Job(
        status="running",
        kind="refine",
        owner="remote:1",
        claim_token="old",
        heartbeat_at=(datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
    )
    store.save_job(project.id, old)

    assert not api._recover_project_job(store, project.id)
    saved = store.load_job(project.id)
    assert saved is not None and saved.status == "error" and saved.claim_token != "old"
    with pytest.raises(JobSupersededError):
        store.save_job(project.id, old, claim_token="old")


def test_cancelling_heartbeat_wrapper_cancels_runner(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path)
    project = store.create_project("test", [("drawing.png", b"png")])
    store.save_job(
        project.id,
        Job(status="running", owner="worker:1", claim_token="run", heartbeat_at=now_iso()),
    )
    app = FastAPI()
    app.state.store = store
    monkeypatch.setattr(api, "JOB_HEARTBEAT_SECONDS", 3600)

    async def verify_cancellation():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def runner(_claim_token: str) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        wrapper = asyncio.create_task(api._run_job_with_heartbeat(app, project.id, "run", runner))
        await started.wait()
        wrapper.cancel()
        with suppress(asyncio.CancelledError):
            await wrapper
        assert cancelled.is_set()

    asyncio.run(verify_cancellation())


def test_job_read_recovers_stale_job_after_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(api, "JOB_STALE_AFTER", timedelta(seconds=1))
    started = threading.Event()

    async def fake_analysis(project_id, store, config, rules, claim_token):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(api, "run_analysis", fake_analysis)
    with TestClient(api.app) as client:
        project_id = client.post("/api/projects", files=[("files", ("drawing.png", b"png", "image/png"))]).json()["id"]
        store = ProjectStore(tmp_path / "output" / "webapp" / "projects")
        store.save_job(
            project_id,
            Job(
                status="running",
                owner="dead-host:123",
                claim_token="dead",
                heartbeat_at=(datetime.now(UTC) - timedelta(seconds=2)).isoformat(),
            ),
        )
        assert client.get(f"/api/projects/{project_id}").status_code == 200
        assert started.wait(timeout=2)
        job = store.load_job(project_id)
        assert job is not None and job.claim_token != "dead"


def test_analyze_lock_wait_leaves_event_loop_responsive(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(api, "run_analysis", lambda *_args: asyncio.sleep(0))
    with TestClient(api.app) as client:
        project_id = client.post("/api/projects", files=[("files", ("drawing.png", b"png", "image/png"))]).json()["id"]
        store = ProjectStore(tmp_path / "output" / "webapp" / "projects")
        holder_thread = threading.get_ident()
        lock_attempted, health_done = threading.Event(), threading.Event()
        original_enter = ProjectLock.__enter__

        def observed_enter(lock: ProjectLock) -> ProjectLock:
            if lock.directory.name == project_id and threading.get_ident() != holder_thread:
                lock_attempted.set()
            return original_enter(lock)

        monkeypatch.setattr(ProjectLock, "__enter__", observed_enter)

        def request_analysis() -> None:
            assert client.post(f"/api/projects/{project_id}/analyze").status_code == 202

        def request_health() -> None:
            assert client.get("/api/health").status_code == 200
            health_done.set()

        with store.lock(project_id):
            request_thread = threading.Thread(target=request_analysis)
            request_thread.start()
            assert lock_attempted.wait(timeout=2)
            health_thread = threading.Thread(target=request_health)
            health_thread.start()
            responsive = health_done.wait(timeout=2)

        request_thread.join(timeout=2)
        health_thread.join(timeout=2)
        assert responsive
        assert not request_thread.is_alive()
        assert not health_thread.is_alive()


def test_delayed_heartbeat_wrapper_does_not_adopt_successor_claim(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create_project("test", [("drawing.png", b"png")])
    app = FastAPI()
    app.state.store = store
    store.save_job(
        project.id,
        Job(status="running", owner="worker:1", claim_token="old", heartbeat_at=now_iso()),
    )

    async def verify_claim() -> None:
        started = False

        async def runner(_claim_token: str) -> None:
            nonlocal started
            started = True

        wrapper = asyncio.create_task(api._run_job_with_heartbeat(app, project.id, "old", runner))
        store.save_job(
            project.id,
            Job(status="running", owner="worker:2", claim_token="new", heartbeat_at=now_iso()),
        )
        await wrapper
        assert not started

    asyncio.run(verify_claim())
