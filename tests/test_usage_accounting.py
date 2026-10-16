from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from src.furniture.interpret import interpret_page
from src.furniture.schema import PageDraft
from src.webapp import analysis, api
from src.webapp.store import Job, UsageTotals


def test_failed_output_validation_records_all_attempt_usage(tmp_path, monkeypatch):
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path))
    agent = Agent(
        TestModel(custom_output_args={"units": "invalid"}),
        output_type=PageDraft,
        retries=2,
    )
    with TestClient(api.app):
        store = api.app.state.store
        project = store.create_project("Demo", [("drawing.png", b"data")])
        job = Job(status="running")
        store.save_job(project.id, job)
        on_usage = analysis._usage_callback(store, project.id, "drawing.png", 0, job=job)

        try:
            asyncio.run(interpret_page(agent, b"image", [], [], [], "mm", on_usage=on_usage))
        except Exception as exc:
            assert "validation" in str(exc).lower()
        else:
            raise AssertionError("invalid model output should exhaust validation retries")

        assert store.load_usage(project.id).requests == 3
        recorded_job = store.load_job(project.id)
        assert recorded_job is not None
        assert recorded_job.usage.requests == 3
        assert recorded_job.usage_breakdown[0].usage.requests == 3


def test_usage_cost_uses_current_rates_for_persisted_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_OUTPUT_DIR", str(tmp_path))
    with TestClient(api.app) as client:
        project_id = client.post(
            "/api/projects", data={"name": "Demo"}, files=[("files", ("drawing.png", b"data", "image/png"))]
        ).json()["id"]
        store = api.app.state.store
        store.record_usage(
            project_id,
            UsageTotals(requests=2, input_tokens=100, output_tokens=10),
            job=Job(status="done"),
            file_name="drawing.png",
            page_index=0,
        )

        assert store.load_usage(project_id).estimated_cost is None
        api.app.state.config.models.cost_per_million_input = 2.0
        api.app.state.config.models.cost_per_million_output = 4.0
        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["usage"]["estimated_cost"] == 0.00024
        assert detail["job"]["usage"]["estimated_cost"] == 0.00024
        assert detail["job"]["usage_breakdown"][0]["usage"]["estimated_cost"] == 0.00024

        api.app.state.config.models.cost_per_million_input = 4.0
        api.app.state.config.models.cost_per_million_output = 8.0
        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["usage"]["estimated_cost"] == 0.00048
        assert detail["job"]["usage"]["estimated_cost"] == 0.00048
