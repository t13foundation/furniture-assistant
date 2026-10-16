from __future__ import annotations

import fcntl
import json
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.furniture.pricing import Pricing, Valuation, load_pricing, save_pricing, value_project, write_valuation_xlsx
from src.furniture.quote import QuoteComparison, QuoteLine, compare_quote, parse_quote
from src.furniture.reports import MaterialSummary, ProcessSummary, material_summary, process_summary
from src.furniture.schema import ModuleModel
from src.webapp.store import ProjectNotFoundError
from src.webapp.upload_limits import check_file_size

_PRICING_THREAD_LOCK = threading.RLock()


@contextmanager
def _workspace_pricing_lock() -> Generator[None]:
    lock_path = _pricing_path().with_name(".pricing.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PRICING_THREAD_LOCK, lock_path.open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


router = APIRouter()


class ProjectPricing(BaseModel):
    pricing: Pricing
    inherited: bool


def _project_pricing(request: Request, project_id: str) -> tuple[Pricing, bool]:
    store = request.app.state.store
    try:
        project_dir = store.project_dir(project_id)
    except ProjectNotFoundError:
        raise HTTPException(404, f"Unknown project {project_id}") from None
    with store.lock(project_id):
        path = project_dir / "pricing.yaml"
        return (load_pricing(path), False) if path.exists() else (load_pricing(_pricing_path()), True)


def _valuation(request: Request, project_id: str):
    models = _models(request, project_id)
    pricing, _ = _project_pricing(request, project_id)
    return models, value_project(models, request.app.state.rules, pricing)


def _pricing_path() -> Path:
    # Keep api.py out of module import time to avoid a circular import.
    return Path(__file__).resolve().parents[2] / "config" / "pricing.yaml"


def _models(request: Request, project_id: str):
    try:
        request.app.state.store.project_dir(project_id)
        return request.app.state.store.list_modules(project_id)
    except ProjectNotFoundError:
        raise HTTPException(404, f"Unknown project {project_id}") from None


@router.get("/api/projects/{project_id}/summary/materials")
def get_material_summary(request: Request, project_id: str) -> MaterialSummary:
    return material_summary(_models(request, project_id), request.app.state.rules)


@router.get("/api/projects/{project_id}/summary/processes")
def get_process_summary(request: Request, project_id: str) -> ProcessSummary:
    return process_summary(_models(request, project_id))


@router.get("/api/projects/{project_id}/valuation")
def get_valuation(request: Request, project_id: str) -> Valuation:
    return _valuation(request, project_id)[1]


@router.get("/api/projects/{project_id}/export/valuation.xlsx")
def export_valuation(request: Request, project_id: str) -> FileResponse:
    models, valuation = _valuation(request, project_id)
    store = request.app.state.store
    with store.lock(project_id):
        output = store.exports_dir(project_id) / "valuation.xlsx"
        write_valuation_xlsx(valuation, models, output)
    return FileResponse(
        output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=output.name
    )


@router.get("/api/projects/{project_id}/pricing", response_model=ProjectPricing)
def get_project_pricing(request: Request, project_id: str) -> ProjectPricing:
    pricing, inherited = _project_pricing(request, project_id)
    return ProjectPricing(pricing=pricing, inherited=inherited)


@router.put("/api/projects/{project_id}/pricing", response_model=ProjectPricing)
def put_project_pricing(request: Request, project_id: str, pricing: Pricing) -> ProjectPricing:
    store = request.app.state.store
    try:
        store.project_dir(project_id)
    except ProjectNotFoundError:
        raise HTTPException(404, f"Unknown project {project_id}") from None
    with store.lock(project_id):
        saved = save_pricing(pricing, store.pricing_path(project_id))
        store.append_audit(
            project_id, "pricing_change", getattr(request.state, "username", None), detail="Project pricing saved"
        )
    return ProjectPricing(pricing=saved, inherited=False)


@router.delete("/api/projects/{project_id}/pricing", response_model=ProjectPricing)
def delete_project_pricing(request: Request, project_id: str) -> ProjectPricing:
    store = request.app.state.store
    try:
        store.project_dir(project_id)
    except ProjectNotFoundError:
        raise HTTPException(404, f"Unknown project {project_id}") from None
    with store.lock(project_id):
        store.pricing_path(project_id).unlink(missing_ok=True)
        store.append_audit(
            project_id,
            "pricing_change",
            getattr(request.state, "username", None),
            detail="Reverted to workspace pricing",
        )
    pricing, inherited = _project_pricing(request, project_id)
    return ProjectPricing(pricing=pricing, inherited=inherited)


@router.post("/api/projects/{project_id}/quote", response_model=QuoteComparison)
async def upload_quote(request: Request, project_id: str, file: Annotated[UploadFile, File()]) -> QuoteComparison:
    store = request.app.state.store
    try:
        store.project_dir(project_id)
    except ProjectNotFoundError:
        raise HTTPException(404, f"Unknown project {project_id}") from None
    if Path(file.filename or "").suffix.lower() != ".xlsx":
        raise HTTPException(400, "Client quote must be an .xlsx file")
    max_mb = int(os.environ.get("FA_MAX_UPLOAD_MB", "50").strip() or 50)
    await check_file_size(file, max_mb)
    content = await file.read()
    with store.lock(project_id):
        try:
            lines = parse_quote(BytesIO(content))
        except Exception as exc:
            raise HTTPException(422, f"Could not read quote workbook: {exc}") from exc
        store.quote_path(project_id).write_bytes(content)
        (store.project_dir(project_id) / "quote.json").write_text(
            json.dumps([line.model_dump(mode="json") for line in lines], ensure_ascii=False), encoding="utf-8"
        )
        store.append_audit(project_id, "quote_upload", getattr(request.state, "username", None), detail=file.filename)
    models, valuation = _valuation(request, project_id)
    return _compare(lines, models, valuation)


def _compare(lines: list[QuoteLine], models: list[ModuleModel], valuation: Valuation) -> QuoteComparison:
    unpriced = {module_id for line in valuation.lines if line.amount is None for module_id in line.module_ids}
    return compare_quote(lines, models, valuation.per_module, unpriced)


@router.get("/api/projects/{project_id}/quote", response_model=QuoteComparison)
def get_quote(request: Request, project_id: str) -> QuoteComparison:
    store = request.app.state.store
    try:
        project_dir = store.project_dir(project_id)
    except ProjectNotFoundError:
        raise HTTPException(404, f"Unknown project {project_id}") from None
    with store.lock(project_id):
        path = project_dir / "quote.json"
        if not path.exists():
            raise HTTPException(404, "No client quote uploaded")
        lines = [QuoteLine.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
    models, valuation = _valuation(request, project_id)
    return _compare(lines, models, valuation)


@router.get("/api/pricing", response_model=Pricing)
def get_pricing() -> Pricing:
    with _workspace_pricing_lock():
        return load_pricing(_pricing_path())


@router.put("/api/pricing", response_model=Pricing)
def put_pricing(pricing: Pricing) -> Pricing:
    with _workspace_pricing_lock():
        return save_pricing(pricing, _pricing_path())
