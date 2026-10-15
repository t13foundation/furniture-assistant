"""Background analysis job: every page → marks → interpretation agent → modules → 3D/cut list."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pypdfium2  # type: ignore[import-untyped]
from config.settings import AppConfig
from PIL import Image
from pydantic_ai import Agent

from src.furniture.annotate import render_marked_page, render_marked_region
from src.furniture.build import build_module
from src.furniture.interpret import (
    REFINE_MARGIN,
    create_interpretation_agent,
    create_refine_agent,
    interpret_page,
    refine_draft,
)
from src.furniture.marks import build_chains, extract_marks
from src.furniture.resolve import (
    assemble_module,
    cross_drawing_checks,
    gap_score,
    module_region,
    needs_refine,
    resolve_module,
)
from src.furniture.rules import ConstructionRules
from src.furniture.schema import DimensionMark, FurnitureModule, ModuleDraft, ModuleModel
from src.schema.enums import DrawingType
from src.webapp.store import Job, JobItem, ProjectStore, now_iso

logger = logging.getLogger(__name__)

ANALYSED_TYPES = frozenset(
    {DrawingType.ELEVATION, DrawingType.IMAGE, DrawingType.OTHER, DrawingType.SECTION, DrawingType.CAD}
)
# Longest side of the page image sent to the interpretation agent.
AGENT_IMAGE_MAX_PX = 2400


def _page_count(path: Path) -> int:
    if path.suffix.lower() != ".pdf":
        return 1
    pdf = pypdfium2.PdfDocument(str(path))
    try:
        return len(pdf)
    finally:
        pdf.close()


def _agent_image(png_path: Path, marks: list[DimensionMark]) -> bytes:
    with Image.open(png_path) as image:
        image.thumbnail((AGENT_IMAGE_MAX_PX, AGENT_IMAGE_MAX_PX))
        return render_marked_page(image, marks)


def _region_image(png_path: Path, module: FurnitureModule) -> bytes:
    """The page around the module (all its views) with mark labels, for the refine agent."""
    with Image.open(png_path) as image:
        return render_marked_region(image, module.marks, module_region(module, REFINE_MARGIN))


def plan_items(store: ProjectStore, project_id: str) -> list[JobItem]:
    """One item per page of analysable files; one skipped item per non-furniture or P3D file."""
    items: list[JobItem] = []
    for file in store.load_project(project_id).files:
        path = store.upload_path(project_id, file.index)
        if path.suffix.lower() == ".p3d" or file.drawing_type not in ANALYSED_TYPES:
            reason = (
                "P3D needs a Palette CAD PDF/DWG/DXF export"
                if path.suffix.lower() == ".p3d"
                else f"{file.drawing_type.value} is not a furniture elevation"
            )
            items.append(JobItem(file_index=file.index, page_index=0, status="skipped", error=reason))
            continue
        try:
            pages = _page_count(path)
        except Exception as exc:  # unreadable upload: report on the item, keep the job going
            items.append(JobItem(file_index=file.index, page_index=0, status="error", error=str(exc)))
            continue
        items.extend(JobItem(file_index=file.index, page_index=p) for p in range(pages))
    return items


def module_state(model: ModuleModel) -> dict:
    """The edit/refine state carried over when a module is rebuilt."""
    return {
        "draft": model.draft,
        "base_module": model.base_module,
        "overrides": model.overrides,
        "refine_status": model.refine_status,
        "refine_attempts": model.refine_attempts,
        "refine_error": model.refine_error,
    }


def rebuild(module: FurnitureModule, rules: ConstructionRules, **state) -> ModuleModel:
    """Carcass, cut list, hardware and processes for ``module``, with the given edit/refine state."""
    return build_module(module, rules).model_copy(update=state)


def apply_refined(
    latest: ModuleModel, new_draft: ModuleDraft | None, rules: ConstructionRules, error: str | None = None
) -> ModuleModel:
    """Merge a model re-run into the module as it is NOW (with the user's latest overrides).

    The new interpretation is kept only if it closes gaps; otherwise the module stays as it is.
    """
    attempts = latest.refine_attempts + 1
    failed = {"refine_attempts": attempts, "refine_status": "error"}
    if new_draft is None or latest.draft is None:
        return latest.model_copy(update={**failed, "refine_error": error or "No model interpretation to refine"})
    try:
        candidate = assemble_module(new_draft, latest.module, latest.overrides, rules)
    except Exception as exc:
        return latest.model_copy(update={**failed, "refine_error": f"{type(exc).__name__}: {exc}"})
    state = {**module_state(latest), "refine_attempts": attempts, "refine_status": "idle", "refine_error": None}
    module = latest.module
    if gap_score(candidate) >= gap_score(module):
        kept = module.model_copy(update={"notes": [*module.notes, "Model re-run found nothing new."]})
        return latest.model_copy(update={"module": kept, **state})
    remaining = {q.id for q in candidate.questions}
    filled = [q.label for q in module.questions if q.id not in remaining]
    candidate.notes.append(
        f"Model re-run filled: {', '.join(filled)}." if filled else "Model re-run resolved conflicts."
    )
    return rebuild(candidate, rules, **{**state, "draft": new_draft})


async def refine_and_save(
    store: ProjectStore,
    project_id: str,
    module_id: str,
    agent: Agent[None, ModuleDraft],
    png_path: Path,
    rules: ConstructionRules,
) -> None:
    """Ask the model again about the module's open questions, then merge under the project lock.

    The model call runs without the lock; edits the user makes meanwhile are kept because the result
    is applied to the module as stored at that moment.
    """
    model = store.load_module(project_id, module_id)
    new_draft, error = None, None
    try:
        if model.draft is None:
            raise ValueError("Module has no model interpretation to refine")
        image_png = await asyncio.to_thread(_region_image, png_path, model.module)
        new_draft = await refine_draft(agent, image_png, model.module, model.draft)
    except Exception as exc:
        logger.exception("Refining %s/%s failed", project_id, module_id)
        error = f"{type(exc).__name__}: {exc}"
    with store.lock(project_id):
        try:
            latest = store.load_module(project_id, module_id)
        except KeyError:
            return  # removed meanwhile (project re-analysed)
        store.save_module(project_id, apply_refined(latest, new_draft, rules, error), action="refine")
        store.append_audit(project_id, "refine_finish", "system", module_id, error and f"Error: {error}")


def recheck_project(store: ProjectStore, project_id: str, rules: ConstructionRules) -> None:
    """Cross-check modules drawn on several pages/files and rebuild the ones that changed (hold the lock)."""
    models = {m.module.module_id: m for m in store.list_modules(project_id)}
    for module_id, module in cross_drawing_checks(list(models.values())).items():
        store.save_module(project_id, rebuild(module, rules, **module_state(models[module_id])))


def _file_index(store: ProjectStore, project_id: str, file_name: str) -> int:
    return next(f.index for f in store.load_project(project_id).files if f.name == file_name)


async def refine_stored_module(
    store: ProjectStore, project_id: str, module_id: str, config: AppConfig, rules: ConstructionRules
) -> None:
    """Background job behind the "Ask the model again" button (``refine_status`` is already running)."""
    try:
        model = store.load_module(project_id, module_id)
        agent = create_refine_agent(config.models.orchestrator, config.api.base_url, config.api.api_key)
        file_index = _file_index(store, project_id, model.module.file_name)
        png_path = await asyncio.to_thread(
            store.page_png, project_id, file_index, model.module.page_index, config.document
        )
    except Exception as exc:
        logger.exception("Refining %s/%s could not start", project_id, module_id)
        with store.lock(project_id):
            latest = store.load_module(project_id, module_id)
            store.save_module(
                project_id, apply_refined(latest, None, rules, f"{type(exc).__name__}: {exc}"), action="refine"
            )
            store.append_audit(project_id, "refine_finish", "system", module_id, f"Error: {exc}")
        return
    await refine_and_save(store, project_id, module_id, agent, png_path, rules)
    with store.lock(project_id):
        recheck_project(store, project_id, rules)


async def run_analysis(project_id: str, store: ProjectStore, config: AppConfig, rules: ConstructionRules) -> None:
    job = Job(status="running", started_at=now_iso())
    with store.lock(project_id):
        store.save_job(project_id, job)
    try:
        with store.lock(project_id):
            store.clear_modules(project_id)
        job.items = plan_items(store, project_id)
        with store.lock(project_id):
            store.save_job(project_id, job)
        model_args = (config.models.orchestrator, config.api.base_url, config.api.api_key)
        agent = create_interpretation_agent(*model_args)
        refine_agent = create_refine_agent(*model_args)
    except Exception as exc:
        logger.exception("Analysis of %s could not start", project_id)
        job.status, job.error, job.finished_at = "error", str(exc), now_iso()
        with store.lock(project_id):
            store.save_job(project_id, job)
            store.append_audit(project_id, "analyze_finish", "system", detail=f"Error: {exc}")
        return

    project = store.load_project(project_id)
    agent_succeeded = False
    for item in job.items:
        if item.status != "pending":
            continue
        item.status = "running"
        with store.lock(project_id):
            store.save_job(project_id, job)
        file = project.files[item.file_index]
        path = store.upload_path(project_id, item.file_index)
        try:
            marks, runs = await asyncio.to_thread(extract_marks, str(path), item.page_index)
            chains = build_chains(marks)
            png_path = await asyncio.to_thread(
                store.page_png, project_id, item.file_index, item.page_index, config.document
            )
            image_png = await asyncio.to_thread(_agent_image, png_path, marks)
            page_units = marks[0].unit if marks else "cm"
            try:
                draft = await interpret_page(agent, image_png, marks, chains, runs, page_units)
            except Exception as exc:
                if not agent_succeeded:
                    # The model never answered: the run itself cannot work (server down, model not loaded).
                    raise _RunAbortedError(str(exc)) from exc
                raise
            agent_succeeded = True
            for module_draft in draft.modules:
                units = marks[0].unit if marks else draft.units
                module = resolve_module(
                    module_draft, marks, chains, units, file.name, item.page_index, rules, texts=runs
                )
                with store.lock(project_id):
                    module.module_id = store.unique_module_id(project_id, module.module_id)
                    store.save_module(project_id, rebuild(module, rules, draft=module_draft), action="analysis")
                if needs_refine(module):
                    # Feedback loop: one focused model re-run on what the first pass left open.
                    await refine_and_save(store, project_id, module.module_id, refine_agent, png_path, rules)
                item.module_ids.append(module.module_id)
            item.status = "done"
        except _RunAbortedError as exc:
            logger.error("Analysis of %s aborted: %s", project_id, exc)
            item.status, item.error = "error", str(exc)
            with store.lock(project_id):
                store.save_job(project_id, job)
                store.append_audit(project_id, "analyze_finish", "system", detail=f"Error: {exc}")
            return
        except Exception as exc:
            logger.exception("Page %s/%s of %s failed", file.name, item.page_index, project_id)
            item.status, item.error = "error", f"{type(exc).__name__}: {exc}"
        with store.lock(project_id):
            store.save_job(project_id, job)
    with store.lock(project_id):
        recheck_project(store, project_id, rules)
        job.status, job.finished_at = "done", now_iso()
        store.save_job(project_id, job)
        store.append_audit(project_id, "analyze_finish", "system")


class _RunAbortedError(RuntimeError):
    pass
