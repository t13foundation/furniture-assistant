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
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from config.settings import DocumentConfig
from pydantic import BaseModel, Field

from src.furniture.schema import ModuleModel
from src.pipeline.agent import make_loader
from src.schema.enums import DrawingType, classify_drawing


class ProjectFile(BaseModel):
    index: int
    name: str
    drawing_type: DrawingType


class Project(BaseModel):
    id: str
    name: str
    created_at: str
    files: list[ProjectFile] = Field(default_factory=list)


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
                try:
                    stream = (self.directory / ".lock").open("a")
                except FileNotFoundError:
                    raise ProjectNotFoundError(self.directory.name) from None
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                # The project may have been moved to the trash while this thread waited for the lock.
                if not self.directory.is_dir():
                    raise ProjectNotFoundError(self.directory.name)
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

    def rename_project(self, project_id: str, name: str) -> Project:
        with self.lock(project_id):
            project = self.load_project(project_id)
            project.name = name
            _write_atomic(self.project_dir(project_id) / "project.json", project.model_dump_json(indent=2))
            return project

    def move_project_to_trash(self, project_id: str) -> Path:
        source = self.project_dir(project_id)
        trash = self.root.parent / "trash"
        trash.mkdir(parents=True, exist_ok=True)
        destination = trash / f"{source.name}-{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        shutil.move(str(source), str(destination))
        return destination

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
            self._append_audit(self.project_dir(project_id) / "audit.jsonl", action, user, module_id, detail)

    def append_audit_in_directory(
        self,
        project_id: str,
        directory: Path,
        action: str,
        user: str | None,
        module_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self.lock(project_id):
            self._append_audit(directory / "audit.jsonl", action, user, module_id, detail)

    @staticmethod
    def _append_audit(path: Path, action: str, user: str | None, module_id: str | None, detail: str | None) -> None:
        entry = {
            "at": now_iso(),
            "user": user or "system",
            "action": action,
            "module_id": module_id,
            "detail": detail,
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def list_activity(self, project_id: str) -> list[ActivityEntry]:
        """Newest first."""
        path = self.project_dir(project_id) / "audit.jsonl"
        if not path.exists():
            return []
        lines = reversed(path.read_text(encoding="utf-8").splitlines())
        return [ActivityEntry.model_validate_json(line) for line in lines if line]

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
