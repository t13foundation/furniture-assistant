"""FastAPI backend for the furniture drawing → 3D web app.

Run: ``make web`` (uvicorn on 127.0.0.1:8000). Serves ``webapp/dist`` at ``/`` when built.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from config.settings import AppConfig
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.furniture.export import write_cut_list_csv, write_cut_list_xlsx, write_panels_dxf, write_sheets_svg
from src.furniture.resolve import (
    ATTENTION_REASONS,
    apply_overrides,
    assemble_module,
    finalize_module,
    merge_overrides,
    overrides_from_edit,
)
from src.furniture.rules import ConstructionRules, load_rules
from src.furniture.schema import CabinetKind, FurnitureModule, ModuleModel, Override, Question
from src.pipeline.orchestrator import DRAWING_EXTENSIONS
from src.webapp import reports
from src.webapp.analysis import module_state, rebuild, recheck_project, refine_stored_module, run_analysis
from src.webapp.audit import audit_head_hash, verify_audit
from src.webapp.auth import (
    COOKIE_NAME,
    SESSION_SECONDS,
    auth_enabled,
    auth_middleware,
    create_session,
    environment_mode,
    is_production,
    login_retry_after,
    record_login_result,
    require_production_secret,
    verify_password,
)
from src.webapp.store import (
    ActivityEntry,
    Job,
    JobItem,
    ModuleVersion,
    Project,
    ProjectNotFoundError,
    ProjectStore,
    UsageBreakdown,
    UsageTotals,
    now_iso,
)
from src.webapp.upload_limits import check_file_size


class RequestTooLarge(Exception):
    pass


class UploadRequestLimitMiddleware:
    """Reject upload request bodies above the configured cap before buffering them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not _is_upload_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        limit = _env_int("FA_MAX_REQUEST_MB", 200) * 1024 * 1024
        headers = dict(scope["headers"])
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    await self._respond_too_large(send, limit)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestTooLarge:
            await self._respond_too_large(send, limit)

    @staticmethod
    async def _respond_too_large(send, limit: int) -> None:
        body = json.dumps({"detail": f"Request exceeds {limit // (1024 * 1024)} MB"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _is_upload_path(path: str) -> bool:
    return path == "/api/projects" or (path.startswith("/api/projects/") and path.endswith("/quote"))


class StatusResponse(BaseModel):
    status: str


class JobResponse(BaseModel):
    status: Literal["queued", "running", "done", "error"] = "queued"
    started_at: str | None = None
    finished_at: str | None = None
    items: list[JobItem] = Field(default_factory=list)
    error: str | None = None
    usage: UsageTotals = Field(default_factory=UsageTotals)
    usage_breakdown: list[UsageBreakdown] = Field(default_factory=list)


class AuthMeResponse(BaseModel):
    username: str | None
    auth_enabled: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model_base_url: str
    projects_root_writable: bool
    auth_enabled: bool


class ProjectListResponse(Project):
    job_status: str | None
    module_count: int


class ModuleListEntry(BaseModel):
    module_id: str
    title: str
    file_name: str
    page_index: int
    group_id: str
    kind: CabinetKind
    status: str
    question_count: int
    attention_count: int
    refine_status: str
    issue_counts: dict[str, int]


class ProjectDetailResponse(BaseModel):
    project: Project
    job: JobResponse | None
    usage: UsageTotals
    modules: list[ModuleListEntry]


class ReviewEntry(BaseModel):
    module_id: str
    title: str
    group_id: str
    kind: CabinetKind
    status: str
    questions: list[Question]


class ActivityIntegrityResponse(BaseModel):
    chain_status: Literal["unverified", "verified", "invalid"]
    chain_error: str | None
    head_hash: str | None


REPO_ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = REPO_ROOT / ".env"
DIST_DIR = REPO_ROOT / "webapp" / "dist"

EXPORTS: dict[str, tuple[str, Callable[[list[ModuleModel], ConstructionRules, Path], Path]]] = {
    "cutlist.csv": ("text/csv", lambda models, _rules, path: write_cut_list_csv(models, path)),
    "cutlist.xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        lambda models, _rules, path: write_cut_list_xlsx(models, path),
    ),
    "sheets.svg": ("image/svg+xml", write_sheets_svg),
    "panels.dxf": ("application/dxf", lambda models, _rules, path: write_panels_dxf(models, path)),
}


def _cookie_secure_override() -> bool | None:
    value = os.environ.get("FA_COOKIE_SECURE")
    if value is None:
        return None
    normalized = value.lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise RuntimeError("FA_COOKIE_SECURE must be a boolean: true/false, yes/no, or 1/0")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    mode = environment_mode()
    if mode == "production":
        require_production_secret()
    else:
        process_env = os.environ.get("FA_ENV")
        load_dotenv(DOTENV_PATH)
        if process_env is None:
            os.environ.pop("FA_ENV", None)
    _cookie_secure_override()
    config = AppConfig.from_yaml(REPO_ROOT / "config" / "default.yaml")
    output_dir = Path(config.output.dir)
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    app.state.config = config
    app.state.rules = load_rules(REPO_ROOT / "config" / "furniture.yaml")
    app.state.store = ProjectStore(output_dir / "webapp" / "projects")
    app.state.tasks = {}
    await _recover_jobs(app)
    yield
    for task in app.state.tasks.values():
        task.cancel()


app = FastAPI(title="Furniture Assistant", lifespan=lifespan)
app.middleware("http")(auth_middleware)
app.add_middleware(UploadRequestLimitMiddleware)
# Before this module's routes: /export/valuation.xlsx must win over the generic /export/{fmt}.
app.include_router(reports.router)


class LoginBody(BaseModel):
    username: str
    password: str


@app.get("/api/auth/me", response_model=AuthMeResponse)
def auth_me(request: Request) -> AuthMeResponse:
    return AuthMeResponse(username=getattr(request.state, "username", None), auth_enabled=auth_enabled())


@app.get("/api/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Liveness/readiness for probes and proxies; public even when auth is enabled. Degraded when projects cannot be written."""
    root = _store(request).root
    try:
        root.mkdir(parents=True, exist_ok=True)
        writable = os.access(root, os.W_OK)
    except OSError:
        writable = False
    return HealthResponse(
        status="ok" if writable else "degraded",
        model_base_url=request.app.state.config.api.base_url,
        projects_root_writable=writable,
        auth_enabled=auth_enabled(),
    )


@app.post("/api/auth/login")
def login(request: Request, body: LoginBody):
    ip = request.client.host if request.client else "unknown"
    retry_after = login_retry_after(ip, body.username)
    if retry_after:
        raise HTTPException(429, "Too many failed sign-in attempts", headers={"Retry-After": str(retry_after)})
    ok = auth_enabled() and verify_password(body.username, body.password)
    record_login_result(ip, body.username, ok)
    if not ok:
        raise HTTPException(401, "Invalid username or password")
    response = JSONResponse({"username": body.username})
    secure_override = _cookie_secure_override()
    secure = secure_override
    if secure is None:
        secure = is_production() or os.environ.get("FA_PUBLIC_URL", "").lower().startswith("https://")
    response.set_cookie(
        COOKIE_NAME,
        create_session(body.username),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=SESSION_SECONDS,
        path="/",
    )
    return response


@app.post("/api/auth/logout")
def logout():
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


def _store(request: Request) -> ProjectStore:
    return request.app.state.store


JOB_STALE_AFTER = timedelta(seconds=30)
JOB_HEARTBEAT_SECONDS = 5


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _job_fresh(job: Job | None) -> bool:
    if job is None or job.status not in ("queued", "running") or not job.heartbeat_at:
        return False
    if job.owner:
        host, separator, pid = job.owner.rpartition(":")
        if separator and host == socket.gethostname():
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError:
                return False
            except (PermissionError, ValueError):
                pass
    try:
        heartbeat = datetime.fromisoformat(job.heartbeat_at)
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.astimezone().astimezone(UTC)
        return datetime.now(UTC) - heartbeat < JOB_STALE_AFTER
    except (TypeError, ValueError):
        return False


def _job_is_running(request: Request, project_id: str) -> bool:
    store = _store(request)
    with store.lock(project_id):
        return _job_fresh(store.load_job(project_id))


def _touch_job(store: ProjectStore, project_id: str, claim_token: str) -> None:
    with store.lock(project_id):
        job = store.load_job(project_id)
        if job and job.claim_token == claim_token and job.status in ("queued", "running"):
            job.heartbeat_at = now_iso()
            store.save_job(project_id, job, claim_token=claim_token)


async def _run_job_with_heartbeat(
    app: FastAPI, project_id: str, claim_token: str, runner: Callable[[str], Coroutine[Any, Any, None]]
) -> None:
    store: ProjectStore = app.state.store

    def active_claim() -> Job | None:
        with store.lock(project_id):
            job = store.load_job(project_id)
            if job is not None and job.claim_token == claim_token and job.status in ("queued", "running"):
                return job
            return None

    job = await asyncio.to_thread(active_claim)
    if job is None or not job.owner:
        return
    owner = job.owner
    task = asyncio.create_task(runner(claim_token))
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=JOB_HEARTBEAT_SECONDS)
            if not done:
                await asyncio.to_thread(_touch_job, store, project_id, claim_token)
        await task
    except asyncio.CancelledError:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        raise
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"

        def mark_error() -> None:
            with store.lock(project_id):
                current = store.load_job(project_id)
                if (
                    current
                    and current.owner == owner
                    and current.claim_token == claim_token
                    and current.status
                    in (
                        "queued",
                        "running",
                    )
                ):
                    current.status, current.error, current.finished_at = "error", error_message, now_iso()
                    store.save_job(project_id, current, claim_token=claim_token)

        await asyncio.to_thread(mark_error)
        return

    def mark_done() -> None:
        with store.lock(project_id):
            current = store.load_job(project_id)
            if (
                current
                and current.owner == owner
                and current.claim_token == claim_token
                and current.status
                in (
                    "queued",
                    "running",
                )
            ):
                current.status = "done"
                current.finished_at = now_iso()
                current.heartbeat_at = now_iso()
                if current.kind == "refine" and current.refine_module_id:
                    module = store.load_module(project_id, current.refine_module_id)
                    if module.refine_status == "error":
                        current.status, current.error = "error", module.refine_error
                store.save_job(project_id, current, claim_token=claim_token)

    await asyncio.to_thread(mark_done)


def _start_analysis(app: FastAPI, project_id: str, claim_token: str) -> None:
    store = app.state.store
    app.state.tasks[project_id] = asyncio.create_task(
        _run_job_with_heartbeat(
            app,
            project_id,
            claim_token,
            lambda claim: run_analysis(project_id, store, app.state.config, app.state.rules, claim),
        )
    )


async def _recover_jobs(app: FastAPI) -> None:
    store = app.state.store
    for project in await asyncio.to_thread(store.list_projects):
        claim_token = await asyncio.to_thread(_recover_project_job, store, project.id)
        if claim_token:
            _start_analysis(app, project.id, claim_token)


def _recover_project_job(store: ProjectStore, project_id: str) -> str | None:
    with store.lock(project_id):
        job = store.load_job(project_id)
        if not job or job.status not in ("queued", "running") or _job_fresh(job):
            return None
        if job.kind == "refine":
            job.claim_token = secrets.token_hex(16)
            job.status, job.error, job.finished_at = (
                "error",
                "Model re-run interrupted by server restart; start it again",
                now_iso(),
            )
            job.heartbeat_at = now_iso()
            store.save_job(project_id, job)
            if job.refine_module_id:
                try:
                    module = store.load_module(project_id, job.refine_module_id)
                except KeyError:
                    pass
                else:
                    module.refine_status = "error"
                    module.refine_error = job.error
                    store.save_module(project_id, module, action="system")
            store.append_audit(project_id, "refine_interrupted", "system", job.refine_module_id, job.error)
            return None
        job.status, job.owner, job.heartbeat_at = "queued", _owner(), now_iso()
        job.claim_token = secrets.token_hex(16)
        store.save_job(project_id, job)
        store.append_audit(project_id, "analyze_resume", "system")
        return job.claim_token


def _tasks(request: Request) -> dict[str, asyncio.Task]:
    return request.app.state.tasks


def _require_project(store: ProjectStore, project_id: str) -> None:
    try:
        store.project_dir(project_id)
    except ProjectNotFoundError:
        raise HTTPException(404, f"Unknown project {project_id}") from None


def _running(request: Request, project_id: str) -> bool:
    return _job_is_running(request, project_id)


def _usage_at_current_rates(request: Request, usage: UsageTotals) -> UsageTotals:
    rates = request.app.state.config.models
    return usage.with_estimated_cost(rates.cost_per_million_input, rates.cost_per_million_output)


async def _job(request: Request, project_id: str) -> Job | None:
    """Return the durable job record (recovering stale analysis first) with usage priced at the current rates."""
    store = _store(request)
    claim_token = await asyncio.to_thread(_recover_project_job, store, project_id)
    if claim_token:
        _start_analysis(request.app, project_id, claim_token)
    job = await asyncio.to_thread(store.load_job, project_id)
    if job is None:
        return None
    return job.model_copy(
        update={
            "usage": _usage_at_current_rates(request, job.usage),
            "usage_breakdown": [
                row.model_copy(update={"usage": _usage_at_current_rates(request, row.usage)})
                for row in job.usage_breakdown
            ],
        }
    )


@app.get("/api/projects", response_model=list[ProjectListResponse])
async def list_projects(request: Request) -> list[ProjectListResponse]:
    store = _store(request)
    rows = []
    for project in await asyncio.to_thread(store.list_projects):
        job = await _job(request, project.id)
        rows.append(
            ProjectListResponse(
                **project.model_dump(mode="json"),
                job_status=job.status if job else None,
                module_count=len(await asyncio.to_thread(store.list_modules, project.id)),
            )
        )
    return rows


@app.post("/api/projects", status_code=201)
async def create_project(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    name: Annotated[str, Form()] = "",
) -> dict[str, str]:
    rejected = [f.filename for f in files if Path(f.filename or "").suffix.lower() not in DRAWING_EXTENSIONS]
    if rejected or not files:
        detail = f"Unsupported file type: {', '.join(str(r) for r in rejected)}" if rejected else "No files uploaded"
        raise HTTPException(400, detail)
    await _check_upload_limits(files)
    uploads = [(f.filename or "upload", await f.read()) for f in files]
    store = _store(request)
    project = store.create_project(name, uploads)
    with store.lock(project.id):
        store.append_audit(project.id, "create_project", request.state.username, detail=project.name)
    return {"id": project.id}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


async def _check_upload_limits(files: list[UploadFile]) -> None:
    """413 when a project has too many files or one file is too large."""
    max_files = _env_int("FA_MAX_FILES", 50)
    max_mb = _env_int("FA_MAX_UPLOAD_MB", 50)
    if len(files) > max_files:
        raise HTTPException(413, f"At most {max_files} files per project")
    for file in files:
        await check_file_size(file, max_mb)


async def _probe_model_server(request: Request) -> None:
    config = request.app.state.config
    base_url = config.api.base_url.rstrip("/")
    api_key = config.api.api_key
    if "api.openai.com" in base_url and (not api_key or api_key == "lm-studio"):
        api_key = os.environ.get("OPENAI_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise HTTPException(503, f"Model server at {base_url} is unavailable: {exc}") from None


@app.post(
    "/api/projects/{project_id}/analyze",
    status_code=202,
    responses={503: {"description": "Model server unavailable"}},
)
async def analyze(request: Request, project_id: str) -> StatusResponse:
    store = _store(request)
    _require_project(store, project_id)

    def busy() -> str | None:
        with store.lock(project_id):
            current = store.load_job(project_id)
            if current is not None and _job_fresh(current):
                return "Analysis already running" if current.kind == "analysis" else "A model re-run is in progress"
        return None

    # A running job answers 409 before the probe, so an unreachable model never masks it.
    if message := await asyncio.to_thread(busy):
        raise HTTPException(409, message)
    await _probe_model_server(request)

    def reserve() -> str:
        with store.lock(project_id):
            current = store.load_job(project_id)
            if current is not None and _job_fresh(current):
                raise HTTPException(
                    409, "Analysis already running" if current.kind == "analysis" else "A model re-run is in progress"
                )
            claim_token = secrets.token_hex(16)
            job = Job(status="queued", owner=_owner(), claim_token=claim_token, heartbeat_at=now_iso())
            store.save_job(project_id, job)
            store.append_audit(project_id, "analyze_start", request.state.username)
            return claim_token

    claim_token = await asyncio.to_thread(reserve)
    _start_analysis(request.app, project_id, claim_token)
    return StatusResponse(status="queued")


@app.get("/api/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project(request: Request, project_id: str) -> ProjectDetailResponse:
    store = _store(request)
    _require_project(store, project_id)
    job = await _job(request, project_id)
    modules = [
        ModuleListEntry(
            module_id=m.module.module_id,
            title=m.module.title,
            file_name=m.module.file_name,
            page_index=m.module.page_index,
            group_id=m.module.group_id,
            kind=m.module.kind,
            status=m.module.status,
            question_count=len(m.module.questions),
            attention_count=sum(q.reason in ATTENTION_REASONS for q in m.module.questions),
            refine_status=m.refine_status,
            issue_counts={
                "error": sum(i.severity == "error" for i in m.module.issues),
                "warning": sum(i.severity == "warning" for i in m.module.issues),
            },
        )
        for m in store.list_modules(project_id)
    ]
    return ProjectDetailResponse(
        project=store.load_project(project_id),
        job=JobResponse(**job.model_dump()) if job else None,
        usage=_usage_at_current_rates(request, store.load_usage(project_id)),
        modules=modules,
    )


@app.get("/api/projects/{project_id}/activity", response_model=list[ActivityEntry])
def project_activity(request: Request, project_id: str) -> list[ActivityEntry]:
    store = _store(request)
    _require_project(store, project_id)
    with store.lock(project_id):
        snapshot, _ = store.audit_snapshot(project_id)
    return store.list_activity_snapshot(snapshot)


@app.get("/api/projects/{project_id}/activity/integrity", response_model=ActivityIntegrityResponse)
def project_activity_integrity(request: Request, project_id: str) -> ActivityIntegrityResponse:
    store = _store(request)
    _require_project(store, project_id)
    with store.lock(project_id):
        snapshot, project = store.audit_snapshot(project_id)
    valid, error = verify_audit(snapshot, project.audit_chain_since, project.audit_legacy_hash)
    chain_status = "invalid" if not valid else "verified" if project.audit_chain_since is not None else "unverified"
    return ActivityIntegrityResponse(
        chain_status=chain_status,
        chain_error=error,
        head_hash=audit_head_hash(snapshot) if valid and chain_status == "verified" else None,
    )


@app.get("/api/projects/{project_id}/audit.jsonl")
def download_audit(request: Request, project_id: str) -> FileResponse:
    store = _store(request)
    _require_project(store, project_id)
    path = store.project_dir(project_id) / "audit.jsonl"
    if not path.is_file():
        raise HTTPException(404, "Audit log not found")
    return FileResponse(path, media_type="application/x-ndjson", filename="audit.jsonl")


@app.get("/api/projects/{project_id}/review", response_model=list[ReviewEntry])
def project_review(request: Request, project_id: str) -> list[ReviewEntry]:
    store = _store(request)
    _require_project(store, project_id)
    return [
        ReviewEntry(
            module_id=model.module.module_id,
            title=model.module.title,
            group_id=model.module.group_id,
            kind=model.module.kind,
            status=model.module.status,
            questions=model.module.questions,
        )
        for model in store.list_modules(project_id)
    ]


@app.get("/api/projects/{project_id}/pages/{file_index}/{page_index}.png")
async def page_image(request: Request, project_id: str, file_index: int, page_index: int) -> FileResponse:
    store = _store(request)
    _require_project(store, project_id)
    try:
        path = await asyncio.to_thread(
            store.page_png, project_id, file_index, page_index, request.app.state.config.document
        )
    except IndexError:
        raise HTTPException(404, "Unknown page") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return FileResponse(path, media_type="image/png")


def _refine_running(request: Request, project_id: str, module_id: str | None = None) -> bool:
    store = _store(request)
    with store.lock(project_id):
        job = store.load_job(project_id)
        return bool(
            job is not None
            and _job_fresh(job)
            and job.kind == "refine"
            and (module_id is None or job.refine_module_id == module_id)
        )


@app.get("/api/projects/{project_id}/modules/{module_id}")
def get_module(request: Request, project_id: str, module_id: str) -> ModuleModel:
    store = _store(request)
    _require_project(store, project_id)
    try:
        model = store.load_module(project_id, module_id)
    except KeyError:
        raise HTTPException(404, f"Unknown module {module_id}") from None
    if model.refine_status == "running" and not _refine_running(request, project_id, module_id):
        # The re-run was lost (server restarted): report it instead of spinning forever.
        with store.lock(project_id):
            model = store.load_module(project_id, module_id).model_copy(
                update={"refine_status": "error", "refine_error": "Model re-run interrupted (server restarted)"}
            )
            store.save_module(project_id, model, action="system")
    return model


@app.get("/api/projects/{project_id}/modules/{module_id}/versions")
def module_versions(request: Request, project_id: str, module_id: str) -> list[ModuleVersion]:
    store = _store(request)
    _require_project(store, project_id)
    with store.lock(project_id):
        try:
            store.load_module(project_id, module_id)
        except KeyError:
            raise HTTPException(404, f"Unknown module {module_id}") from None
        return store.list_versions(project_id, module_id)


@app.get("/api/projects/{project_id}/modules/{module_id}/versions/{version}", response_model=ModuleModel)
def module_version(request: Request, project_id: str, module_id: str, version: int) -> ModuleModel:
    store = _store(request)
    _require_project(store, project_id)
    with store.lock(project_id):
        try:
            return store.load_version(project_id, module_id, version)
        except KeyError:
            raise HTTPException(404, f"Unknown module version {version}") from None


@app.post("/api/projects/{project_id}/modules/{module_id}/versions/{version}/restore", response_model=ModuleModel)
def restore_module_version(request: Request, project_id: str, module_id: str, version: int) -> ModuleModel:
    store = _store(request)
    _require_project(store, project_id)
    with store.lock(project_id):
        try:
            restored = store.load_version(project_id, module_id, version)
        except KeyError:
            raise HTTPException(404, f"Unknown module version {version}") from None
        restored.module.module_id = module_id
        store.save_module(
            project_id, restored, user=getattr(request.state, "username", None) or "system", action="restore"
        )
        store.append_audit(
            project_id, "restore", getattr(request.state, "username", None), module_id, f"Version {version}"
        )
        recheck_project(store, project_id, request.app.state.rules)
        return store.load_module(project_id, module_id)


def _change(override: Override) -> str:
    """Audit wording of one override: ``target=value``, or ``target cleared`` when it carries no value."""
    values = override.model_dump(exclude={"target"}, exclude_none=True)
    if not values:
        return f"{override.target} cleared"
    value = override.material.name if override.material else next(iter(values.values()))
    return f"{override.target}={value}"


def _changes(before: list[Override], after: list[Override]) -> str | None:
    """The overrides added or changed by a save, for the audit trail."""
    changed = [_change(o) for o in after if o not in before]
    removed = [f"{o.target} cleared" for o in before if o.target not in {a.target for a in after}]
    return "; ".join(changed + removed) or None


def _save_and_recheck(
    request: Request, project_id: str, model: ModuleModel, action: str = "save", detail: str | None = None
) -> ModuleModel:
    """Save and cross-check; call with the project lock held."""
    store = _store(request)
    user = getattr(request.state, "username", None)
    store.save_module(project_id, model, user=user or "system", action=action)
    store.append_audit(project_id, action, user, model.module.module_id, detail)
    recheck_project(store, project_id, request.app.state.rules)
    return store.load_module(project_id, model.module.module_id)


def _assemble(model: ModuleModel, overrides: list[Override], rules: ConstructionRules) -> FurnitureModule:
    """The module = the model's interpretation (or the hand-made base) with ``overrides`` applied."""
    if model.draft is not None:
        return assemble_module(model.draft, model.module, overrides, rules)
    base = model.base_module or model.module
    return finalize_module(apply_overrides(base, overrides))


@app.put("/api/projects/{project_id}/modules/{module_id}")
def update_module(request: Request, project_id: str, module_id: str, module: FurnitureModule) -> ModuleModel:
    """Editor save: changes become overrides on the model's interpretation, then checks + rebuild."""
    store = _store(request)
    _require_project(store, project_id)
    rules = request.app.state.rules
    module.module_id = module_id
    with store.lock(project_id):
        try:
            current = store.load_module(project_id, module_id)
        except KeyError:
            current = None
        if current is None or current.draft is None:
            # A hand-made module: the edited module becomes the new base (earlier overrides are baked in).
            base = finalize_module(module)
            return _save_and_recheck(
                request, project_id, rebuild(base, rules, base_module=base, overrides=[]), "editor_save"
            )
        overrides = merge_overrides(current.overrides, overrides_from_edit(current.module, module))
        try:
            assembled = _assemble(current, overrides, rules)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        state = {**module_state(current), "overrides": overrides}
        model = rebuild(assembled, rules, **state)
        return _save_and_recheck(request, project_id, model, "editor_save", _changes(current.overrides, overrides))


class AnswersBody(BaseModel):
    overrides: list[Override]


@app.post("/api/projects/{project_id}/modules/{module_id}/answers")
def answer_questions(request: Request, project_id: str, module_id: str, body: AnswersBody) -> ModuleModel:
    """Apply the user's answers (overrides; one without values removes that target's override)."""
    get_module(request, project_id, module_id)
    store = _store(request)
    rules = request.app.state.rules
    with store.lock(project_id):
        current = store.load_module(project_id, module_id)
        overrides = merge_overrides(current.overrides, body.overrides)
        try:
            module = _assemble(current, overrides, rules)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        state = {**module_state(current), "overrides": overrides}
        if current.draft is None and current.base_module is None:
            state["base_module"] = current.module  # module saved before bases were tracked
        model = rebuild(module, rules, **state)
        # No answers = re-run the checks and rebuild (e.g. after a rules change).
        action = "answers" if body.overrides else "recheck"
        return _save_and_recheck(request, project_id, model, action, _changes(current.overrides, overrides))


@app.post(
    "/api/projects/{project_id}/modules/{module_id}/refine",
    status_code=202,
    responses={503: {"description": "Model server unavailable"}},
)
async def refine(request: Request, project_id: str, module_id: str) -> StatusResponse:
    """Ask the model once more about the module's open questions (background; poll the module)."""
    store = _store(request)
    _require_project(store, project_id)

    def precheck() -> None:
        with store.lock(project_id):
            if _job_fresh(store.load_job(project_id)):
                raise HTTPException(409, "Analysis or model re-run already running")
            try:
                current = store.load_module(project_id, module_id)
            except KeyError:
                raise HTTPException(404, f"Unknown module {module_id}") from None
            if current.draft is None:
                raise HTTPException(422, "Module was created by hand; there is no model interpretation to refine")

    # Conflicts and invalid modules answer before the probe; reserve() re-checks under the lock afterwards.
    await asyncio.to_thread(precheck)
    await _probe_model_server(request)

    def reserve() -> str:
        with store.lock(project_id):
            active = store.load_job(project_id)
            if _job_fresh(active):
                raise HTTPException(409, "Analysis or model re-run already running")
            try:
                latest = store.load_module(project_id, module_id)
            except KeyError:
                raise HTTPException(404, f"Unknown module {module_id}") from None
            if latest.draft is None:
                raise HTTPException(422, "Module was created by hand; there is no model interpretation to refine")
            owner, claim_token = _owner(), secrets.token_hex(16)
            store.save_module(
                project_id,
                latest.model_copy(update={"refine_status": "running", "refine_error": None}),
                user=getattr(request.state, "username", None) or "system",
                action="refine",
            )
            store.save_job(
                project_id,
                Job(
                    status="running",
                    kind="refine",
                    refine_module_id=module_id,
                    owner=owner,
                    claim_token=claim_token,
                    heartbeat_at=now_iso(),
                    started_at=now_iso(),
                ),
            )
            store.append_audit(project_id, "refine_start", getattr(request.state, "username", None), module_id)
            return claim_token

    claim_token = await asyncio.to_thread(reserve)
    _tasks(request)[project_id] = asyncio.create_task(
        _run_job_with_heartbeat(
            request.app,
            project_id,
            claim_token,
            lambda claim: refine_stored_module(
                store, project_id, module_id, request.app.state.config, request.app.state.rules, claim
            ),
        )
    )
    return StatusResponse(status="running")


def _export(request: Request, project_id: str, models: list[ModuleModel], stem: str, fmt: str) -> FileResponse:
    if fmt not in EXPORTS:
        raise HTTPException(404, f"Unknown export format {fmt}")
    media_type, writer = EXPORTS[fmt]
    store = _store(request)
    with store.lock(project_id):
        path = store.exports_dir(project_id) / f"{stem}.{fmt}"
        writer(models, request.app.state.rules, path)
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/api/projects/{project_id}/modules/{module_id}/export/{fmt}")
def export_module(request: Request, project_id: str, module_id: str, fmt: str) -> FileResponse:
    model = get_module(request, project_id, module_id)
    return _export(request, project_id, [model], module_id, fmt)


@app.get("/api/projects/{project_id}/export/{fmt}")
def export_project(request: Request, project_id: str, fmt: str) -> FileResponse:
    store = _store(request)
    _require_project(store, project_id)
    return _export(request, project_id, store.list_modules(project_id), "project", fmt)


if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="webapp")
