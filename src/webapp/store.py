"""File-backed project store: uploads, cached page renders, built modules and the analysis job.

Layout under ``<output.dir>/webapp/projects/<project_id>/``::

    project.json            Project
    job.json                Job
    uploads/<file name>     original uploads
    pages/<file>_<page>.png page renders (cached on first request)
    modules/<module>.json   ModuleModel
    exports/                generated downloads (overwritten per request)
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from config.settings import DocumentConfig
from pydantic import BaseModel, Field, ValidationError

from src.furniture.schema import ModuleModel
from src.pipeline.agent import make_loader
from src.schema.enums import DrawingType, classify_drawing
from src.webapp.audit import checkpoint_for_existing_log, entry_hash, last_audit_record


class ProjectFile(BaseModel):
    index: int
    name: str
    drawing_type: DrawingType


class Project(BaseModel):
    id: str
    name: str
    created_at: str
    files: list[ProjectFile] = Field(default_factory=list)
    audit_chain_since: int | None = None
    audit_legacy_hash: str | None = None


class UsageTotals(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float | None = None

    def with_estimated_cost(
        self, cost_per_million_input: float | None, cost_per_million_output: float | None
    ) -> UsageTotals:
        if cost_per_million_input is None or cost_per_million_output is None:
            cost = None
        else:
            cost = round(
                (self.input_tokens * cost_per_million_input + self.output_tokens * cost_per_million_output) / 1_000_000,
                8,
            )
        return self.model_copy(update={"estimated_cost": cost})


class UsageBreakdown(BaseModel):
    file_name: str
    page_index: int
    module_id: str | None = None
    usage: UsageTotals


class JobItem(BaseModel):
    file_index: int
    page_index: int
    status: Literal["pending", "running", "done", "skipped", "error"] = "pending"
    module_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class Job(BaseModel):
    status: Literal["queued", "running", "done", "error"] = "queued"
    started_at: str | None = None
    finished_at: str | None = None
    items: list[JobItem] = Field(default_factory=list)
    error: str | None = None
    usage: UsageTotals = Field(default_factory=UsageTotals)
    usage_breakdown: list[UsageBreakdown] = Field(default_factory=list)
    kind: Literal["analysis", "refine"] = "analysis"
    refine_module_id: str | None = None
    owner: str | None = None
    claim_token: str | None = None
    heartbeat_at: str | None = None


class ModuleVersion(BaseModel):
    version: int
    at: str
    user: str
    action: str
    summary: str


class ActivityEntry(BaseModel):
    at: str
    user: str
    action: str
    module_id: str | None = None
    detail: str | None = None


class ProjectNotFoundError(KeyError):
    pass


class JobSupersededError(RuntimeError):
    """Raised when a runner attempts to write after another run claimed the job."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_atomic(path: Path, text: str) -> None:
    """Write via a unique temporary file so concurrent writers never share or publish a partial file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _unique_name(name: str, taken: set[str]) -> str:
    candidate, stem, suffix, n = name, Path(name).stem, Path(name).suffix, 2
    while candidate in taken:
        candidate = f"{stem}-{n}{suffix}"
        n += 1
    return candidate


class ProjectLock(AbstractContextManager["ProjectLock"]):
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.thread_lock = threading.RLock()
        self.local = threading.local()

    def __enter__(self) -> ProjectLock:
        self.thread_lock.acquire()
        depth = getattr(self.local, "depth", 0)
        if depth == 0:
            stream = None
            try:
                stream = (self.directory / ".lock").open("a")
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            except BaseException:
                if stream is not None:
                    stream.close()
                self.thread_lock.release()
                raise
            self.local.stream = stream
        self.local.depth = depth + 1
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        depth = self.local.depth - 1
        self.local.depth = depth
        try:
            if depth == 0:
                stream = self.local.stream
                del self.local.stream
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
        finally:
            self.thread_lock.release()


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._locks: dict[str, ProjectLock] = {}
        self._locks_guard = threading.Lock()

    def lock(self, project_id: str) -> ProjectLock:
        """Serialize project access within threads and across processes on a shared filesystem."""
        project_id = Path(project_id).name
        with self._locks_guard:
            lock = self._locks.get(project_id)
            if lock is None:
                lock = self._locks[project_id] = ProjectLock(self.root / project_id)
            return lock

    def project_dir(self, project_id: str) -> Path:
        path = self.root / Path(project_id).name
        if not (path / "project.json").exists():
            raise ProjectNotFoundError(project_id)
        return path

    # ── Projects ──────────────────────────────────────────────────────

    def create_project(self, name: str, uploads: list[tuple[str, bytes]]) -> Project:
        project_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
        base = self.root / project_id
        (base / "uploads").mkdir(parents=True)
        files: list[ProjectFile] = []
        taken: set[str] = set()
        for index, (filename, data) in enumerate(uploads):
            safe = _unique_name(Path(filename).name, taken)
            taken.add(safe)
            (base / "uploads" / safe).write_bytes(data)
            files.append(ProjectFile(index=index, name=safe, drawing_type=classify_drawing(safe)))
        project = Project(
            id=project_id, name=name or (files[0].name if files else project_id), created_at=now_iso(), files=files
        )
        _write_atomic(base / "project.json", project.model_dump_json(indent=2))
        return project

    def list_projects(self) -> list[Project]:
        if not self.root.exists():
            return []
        projects = [
            Project.model_validate_json((p / "project.json").read_text(encoding="utf-8"))
            for p in self.root.iterdir()
            if (p / "project.json").exists()
        ]
        return sorted(projects, key=lambda p: p.created_at, reverse=True)

    def load_project(self, project_id: str) -> Project:
        return Project.model_validate_json((self.project_dir(project_id) / "project.json").read_text(encoding="utf-8"))

    def upload_path(self, project_id: str, file_index: int) -> Path:
        project = self.load_project(project_id)
        return self.project_dir(project_id) / "uploads" / project.files[file_index].name

    def exports_dir(self, project_id: str) -> Path:
        path = self.project_dir(project_id) / "exports"
        path.mkdir(exist_ok=True)
        return path

    # ── Pages ─────────────────────────────────────────────────────────

    def page_png(self, project_id: str, file_index: int, page_index: int, document: DocumentConfig) -> Path:
        """Cached PNG render of one page; the first request renders every page of the file."""
        pages = self.project_dir(project_id) / "pages"
        target = pages / f"{file_index}_{page_index}.png"
        if target.exists():
            return target
        pages.mkdir(exist_ok=True)
        loader = make_loader(str(self.upload_path(project_id, file_index)), document)
        if not 0 <= page_index < loader.page_count:
            raise IndexError(f"Page {page_index} out of range")
        for index, image in enumerate(loader.iter_pages()):
            out = pages / f"{file_index}_{index}.png"
            if not out.exists():
                fd, tmp = tempfile.mkstemp(dir=pages, prefix=f".{out.name}.", suffix=".tmp")
                with os.fdopen(fd, "wb") as f:
                    image.save(f, format="PNG")
                os.replace(tmp, out)
        return target

    # ── Modules ───────────────────────────────────────────────────────
    def _check_job_claim(self, project_id: str, claim_token: str | None) -> None:
        if claim_token is None:
            return
        current = self.load_job(project_id)
        if current is None or current.claim_token != claim_token:
            raise JobSupersededError(f"Job claim for project {project_id} was superseded")

    def _modules_dir(self, project_id: str) -> Path:
        path = self.project_dir(project_id) / "modules"
        path.mkdir(exist_ok=True)
        return path

    def save_module(
        self,
        project_id: str,
        model: ModuleModel,
        user: str = "system",
        action: str = "save",
        claim_token: str | None = None,
    ) -> None:
        """Save current state and a durable, monotonic version under the project lock.

        A save that changes nothing (e.g. a cross-check pass over an unaffected module) adds no version."""
        with self.lock(project_id):
            self._check_job_claim(project_id, claim_token)
            module_id = Path(model.module.module_id).name
            modules = self._modules_dir(project_id)
            history = modules / "history" / module_id
            history.mkdir(parents=True, exist_ok=True)
            data = model.model_dump_json()
            latest = max((int(path.stem) for path in history.glob("*.json") if path.stem.isdigit()), default=0)
            if latest and (history / f"{latest}.json").read_text(encoding="utf-8") == data:
                return
            version = latest + 1
            _write_atomic(history / f"{version}.json", data)
            index_path = history / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
            index.append(
                {
                    "version": version,
                    "at": now_iso(),
                    "user": user,
                    "action": action,
                    "summary": f"{model.module.title} · {model.module.status} · {len(model.module.issues)} issues",
                }
            )
            _write_atomic(index_path, json.dumps(index, ensure_ascii=False, indent=2))
            _write_atomic(modules / f"{module_id}.json", data)

    def list_versions(self, project_id: str, module_id: str) -> list[ModuleVersion]:
        """Newest first."""
        path = self._modules_dir(project_id) / "history" / Path(module_id).name / "index.json"
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return [ModuleVersion.model_validate(entry) for entry in reversed(entries)]

    def load_version(self, project_id: str, module_id: str, version: int) -> ModuleModel:
        path = self._modules_dir(project_id) / "history" / Path(module_id).name / f"{version}.json"
        if not path.is_file():
            raise KeyError(version)
        return ModuleModel.model_validate_json(path.read_text(encoding="utf-8"))

    def append_audit(
        self, project_id: str, action: str, user: str | None, module_id: str | None = None, detail: str | None = None
    ) -> None:
        with self.lock(project_id):
            directory = self.project_dir(project_id)
            path = directory / "audit.jsonl"
            project = self.load_project(project_id)
            initialized = project.audit_chain_since is None
            if initialized:
                raw = path.read_bytes() if path.exists() else b""
                try:
                    checkpoint = checkpoint_for_existing_log(raw)
                except ValueError as exc:
                    raise ValueError(f"cannot extend invalid audit chain: {exc}") from None
                if checkpoint is None:
                    lines = raw.splitlines(keepends=True)
                    project.audit_chain_since = len(lines) + 1
                    prefix = raw + (b"\n" if raw and not raw.endswith(b"\n") else b"")
                    project.audit_legacy_hash = hashlib.sha256(prefix).hexdigest() if prefix else "0" * 64
                else:
                    project.audit_chain_since, project.audit_legacy_hash = checkpoint
            if project.audit_chain_since is not None and project.audit_legacy_hash is None:
                raise ValueError("audit chain checkpoint is incomplete")
            last, terminated = last_audit_record(path)
            if last is None:
                previous = project.audit_legacy_hash or "0" * 64
                if project.audit_chain_since == 1:
                    previous = "0" * 64
            elif "hash" in last:
                previous = last["hash"]
                payload = {key: value for key, value in last.items() if key != "hash"}
                if (
                    not isinstance(previous, str)
                    or not isinstance(last.get("prev_hash"), str)
                    or entry_hash(payload) != previous
                ):
                    raise ValueError("audit log final record hash is invalid")
            elif initialized:
                previous = project.audit_legacy_hash or "0" * 64
            else:
                raise ValueError("audit log final record is unhashed after chain start")
            entry = {
                "at": now_iso(),
                "user": user or "system",
                "action": action,
                "module_id": module_id,
                "detail": detail,
                "prev_hash": previous,
            }
            entry["hash"] = entry_hash(entry)
            with path.open("ab") as stream:
                if last is not None and not terminated:
                    stream.write(b"\n")
                stream.write(
                    json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
                )
            if initialized:
                _write_atomic(directory / "project.json", project.model_dump_json(indent=2))

    @staticmethod
    def list_activity_snapshot(snapshot: bytes) -> list[ActivityEntry]:
        """Newest first; malformed records are omitted so the integrity result remains visible."""
        entries: list[ActivityEntry] = []
        for line in snapshot.splitlines():
            if not line:
                continue
            try:
                entries.append(ActivityEntry.model_validate_json(line))
            except (UnicodeDecodeError, ValueError, ValidationError):
                continue
        return list(reversed(entries))

    def audit_snapshot(self, project_id: str) -> tuple[bytes, Project]:
        with self.lock(project_id):
            directory = self.project_dir(project_id)
            path = directory / "audit.jsonl"
            snapshot = path.read_bytes() if path.exists() else b""
            project = self.load_project(project_id)
            if project.audit_chain_since is None:
                try:
                    checkpoint = checkpoint_for_existing_log(snapshot)
                except ValueError:
                    checkpoint = None
                if checkpoint is not None:
                    project.audit_chain_since, project.audit_legacy_hash = checkpoint
                    _write_atomic(directory / "project.json", project.model_dump_json(indent=2))
            return snapshot, project

    def pricing_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "pricing.yaml"

    def quote_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "quote.xlsx"

    def load_module(self, project_id: str, module_id: str) -> ModuleModel:
        path = self._modules_dir(project_id) / f"{Path(module_id).name}.json"
        if not path.exists():
            raise KeyError(module_id)
        return ModuleModel.model_validate_json(path.read_text(encoding="utf-8"))

    def list_modules(self, project_id: str) -> list[ModuleModel]:
        models = [
            ModuleModel.model_validate_json(p.read_text(encoding="utf-8"))
            for p in self._modules_dir(project_id).glob("*.json")
        ]
        return sorted(models, key=lambda m: (m.module.file_name, m.module.page_index, m.module.module_id))

    def unique_module_id(self, project_id: str, module_id: str) -> str:
        """``module_id`` or the first free ``-2``, ``-3``... suffix among saved modules."""
        existing = {p.stem for p in self._modules_dir(project_id).glob("*.json")}
        candidate, n = module_id, 2
        while candidate in existing:
            candidate = f"{module_id}-{n}"
            n += 1
        return candidate

    def clear_modules(self, project_id: str, claim_token: str | None = None) -> None:
        """Drop current modules and archive old history before a re-analysis.

        Restoring a version from the previous analysis would silently resurrect stale geometry,
        so the old data remains on disk but outside the active history.
        """
        with self.lock(project_id):
            self._check_job_claim(project_id, claim_token)
            modules = self._modules_dir(project_id)
            for path in modules.glob("*.json"):
                path.unlink()
            history = modules / "history"
            if history.exists():
                history.rename(modules / f"history-archive-{time.strftime('%Y%m%d_%H%M%S')}")

    # ── Job ───────────────────────────────────────────────────────────

    def save_job(self, project_id: str, job: Job, claim_token: str | None = None) -> None:
        with self.lock(project_id):
            self._check_job_claim(project_id, claim_token)
            existing = self.load_job(project_id)
            if (
                existing
                and existing.owner == job.owner
                and existing.heartbeat_at
                and (job.heartbeat_at is None or existing.heartbeat_at > job.heartbeat_at)
            ):
                job.heartbeat_at = existing.heartbeat_at
            _write_atomic(self.project_dir(project_id) / "job.json", job.model_dump_json(indent=2))

    def load_job(self, project_id: str) -> Job | None:
        path = self.project_dir(project_id) / "job.json"
        return Job.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None

    def load_usage(self, project_id: str) -> UsageTotals:
        path = self.project_dir(project_id) / "usage.json"
        return UsageTotals.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else UsageTotals()

    def record_usage(
        self,
        project_id: str,
        usage: UsageTotals,
        *,
        job: Job | None = None,
        file_name: str,
        page_index: int,
        module_id: str | None = None,
        claim_token: str | None = None,
    ) -> None:
        """Persist token totals under the project lock, including an optional job breakdown.

        With ``claim_token``, a superseded run (another worker took the job over) raises instead of writing.
        """
        with self.lock(project_id):
            self._check_job_claim(project_id, claim_token)

            def add(total: UsageTotals, increment: UsageTotals) -> UsageTotals:
                return UsageTotals(
                    requests=total.requests + increment.requests,
                    input_tokens=total.input_tokens + increment.input_tokens,
                    output_tokens=total.output_tokens + increment.output_tokens,
                )

            total = add(self.load_usage(project_id), usage)
            _write_atomic(self.project_dir(project_id) / "usage.json", total.model_dump_json(indent=2))
            if job is not None:
                job.usage = add(job.usage, usage)
                entry = next(
                    (
                        row
                        for row in job.usage_breakdown
                        if row.file_name == file_name and row.page_index == page_index and row.module_id == module_id
                    ),
                    None,
                )
                if entry is None:
                    job.usage_breakdown.append(
                        UsageBreakdown(file_name=file_name, page_index=page_index, module_id=module_id, usage=usage)
                    )
                else:
                    entry.usage = add(entry.usage, usage)
                self.save_job(project_id, job, claim_token)
