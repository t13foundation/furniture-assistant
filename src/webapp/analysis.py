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
from src.webapp.store import JobItem, JobSupersededError, ProjectStore, now_iso

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


def _fenced(store: ProjectStore, project_id: str, claim_token: str, operation):
    with store.lock(project_id):
        store._check_job_claim(project_id, claim_token)
        return operation()


async def _fenced_async(store: ProjectStore, project_id: str, claim_token: str, operation):
    return await asyncio.to_thread(_fenced, store, project_id, claim_token, operation)


async def refine_and_save(
    store: ProjectStore,
    project_id: str,
    module_id: str,
    agent: Agent[None, ModuleDraft],
    png_path: Path,
    rules: ConstructionRules,
    claim_token: str,
) -> None:
    """Ask the model again and merge the result only while this run owns the project claim."""
    model = await asyncio.to_thread(store.load_module, project_id, module_id)
    new_draft, error = None, None
    try:
        if model.draft is None:
            raise ValueError("Module has no model interpretation to refine")
        image_png = await asyncio.to_thread(_region_image, png_path, model.module)
        new_draft = await refine_draft(agent, image_png, model.module, model.draft)
    except Exception as exc:
        logger.exception("Refining %s/%s failed", project_id, module_id)
        error = f"{type(exc).__name__}: {exc}"

    def save() -> None:
        try:
            latest = store.load_module(project_id, module_id)
        except KeyError:
            return
        store.save_module(
            project_id, apply_refined(latest, new_draft, rules, error), action="refine", claim_token=claim_token
        )
        store.append_audit(project_id, "refine_finish", "system", module_id, error and f"Error: {error}")

    await _fenced_async(store, project_id, claim_token, save)


def recheck_project(
    store: ProjectStore, project_id: str, rules: ConstructionRules, claim_token: str | None = None
) -> None:
    """Cross-check modules drawn on several pages/files (hold the project lock)."""
    models = {m.module.module_id: m for m in store.list_modules(project_id)}
    for module_id, module in cross_drawing_checks(list(models.values())).items():
        store.save_module(
            project_id,
            rebuild(module, rules, **module_state(models[module_id])),
            claim_token=claim_token,
        )


def _file_index(store: ProjectStore, project_id: str, file_name: str) -> int:
    return next(f.index for f in store.load_project(project_id).files if f.name == file_name)


async def refine_stored_module(
    store: ProjectStore,
    project_id: str,
    module_id: str,
    config: AppConfig,
    rules: ConstructionRules,
    claim_token: str,
) -> None:
    """Background job behind the "Ask the model again" button."""
    try:
        model = await asyncio.to_thread(store.load_module, project_id, module_id)
        agent = create_refine_agent(config.models.orchestrator, config.api.base_url, config.api.api_key)
        file_index = await asyncio.to_thread(_file_index, store, project_id, model.module.file_name)
        png_path = await asyncio.to_thread(
            store.page_png, project_id, file_index, model.module.page_index, config.document
        )
    except Exception as exc:
        logger.exception("Refining %s/%s could not start", project_id, module_id)
        error_message = f"{type(exc).__name__}: {exc}"

        def mark_refine_error() -> None:
            latest = store.load_module(project_id, module_id)
            store.save_module(
                project_id,
                apply_refined(latest, None, rules, error_message),
                action="refine",
                claim_token=claim_token,
            )
            store.append_audit(project_id, "refine_finish", "system", module_id, f"Error: {error_message}")

        await _fenced_async(store, project_id, claim_token, mark_refine_error)
        return
    await refine_and_save(store, project_id, module_id, agent, png_path, rules, claim_token)
    await _fenced_async(store, project_id, claim_token, lambda: recheck_project(store, project_id, rules, claim_token))


async def run_analysis(
    project_id: str, store: ProjectStore, config: AppConfig, rules: ConstructionRules, claim_token: str
) -> None:
    job = await asyncio.to_thread(store.load_job, project_id)
    if job is None:
        raise JobSupersededError(f"Job claim for project {project_id} is missing")
    job.status, job.started_at, job.finished_at, job.error = "running", job.started_at or now_iso(), None, None

    def start_job() -> None:
        store.save_job(project_id, job, claim_token=claim_token)
        # Resume from a clean module set: a page may have partially written before its checkpoint.
        store.clear_modules(project_id, claim_token=claim_token)

    await _fenced_async(store, project_id, claim_token, start_job)
    try:
        job.items = await asyncio.to_thread(plan_items, store, project_id)
        await _fenced_async(store, project_id, claim_token, lambda: store.save_job(project_id, job, claim_token))
        model_args = (config.models.orchestrator, config.api.base_url, config.api.api_key)
        agent = create_interpretation_agent(*model_args)
        refine_agent = create_refine_agent(*model_args)
    except Exception as exc:
        logger.exception("Analysis of %s could not start", project_id)
        job.status, job.error, job.finished_at = "error", str(exc), now_iso()
        error_message = str(exc)

        def save_start_error() -> None:
            store.save_job(project_id, job, claim_token)
            store.append_audit(project_id, "analyze_finish", "system", detail=f"Error: {error_message}")

        await _fenced_async(store, project_id, claim_token, save_start_error)
        return

    project = await asyncio.to_thread(store.load_project, project_id)
    agent_succeeded = False
    for item in job.items:
        if item.status != "pending":
            continue
        item.status = "running"
        await _fenced_async(store, project_id, claim_token, lambda: store.save_job(project_id, job, claim_token))
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
                    # No model response means the run cannot proceed (server down or model unloaded).
                    raise _RunAbortedError(str(exc)) from exc
                raise
            agent_succeeded = True
            for module_draft in draft.modules:
                units = marks[0].unit if marks else draft.units
                module = resolve_module(
                    module_draft, marks, chains, units, file.name, item.page_index, rules, texts=runs
                )

                def save_module(module=module, module_draft=module_draft) -> None:
                    module.module_id = store.unique_module_id(project_id, module.module_id)
                    store.save_module(
                        project_id,
                        rebuild(module, rules, draft=module_draft),
                        action="analysis",
                        claim_token=claim_token,
                    )

                await _fenced_async(store, project_id, claim_token, save_module)
                if needs_refine(module):
                    await refine_and_save(
                        store, project_id, module.module_id, refine_agent, png_path, rules, claim_token
                    )
                item.module_ids.append(module.module_id)
            item.status = "done"
        except _RunAbortedError as exc:
            logger.error("Analysis of %s aborted: %s", project_id, exc)
            item.status, item.error = "error", str(exc)
            job.status, job.error, job.finished_at = "error", str(exc), now_iso()
            error_message = str(exc)

            def save_aborted(error_message=error_message) -> None:
                store.save_job(project_id, job, claim_token)
                store.append_audit(project_id, "analyze_finish", "system", detail=f"Error: {error_message}")

            await _fenced_async(store, project_id, claim_token, save_aborted)
            return
        except JobSupersededError:
            raise
        except Exception as exc:
            logger.exception("Page %s/%s of %s failed", file.name, item.page_index, project_id)
            item.status, item.error = "error", f"{type(exc).__name__}: {exc}"
        await _fenced_async(store, project_id, claim_token, lambda: store.save_job(project_id, job, claim_token))

    def finish() -> None:
        recheck_project(store, project_id, rules, claim_token)
        job.status, job.finished_at = "done", now_iso()
        store.save_job(project_id, job, claim_token)
        store.append_audit(project_id, "analyze_finish", "system")

    await _fenced_async(store, project_id, claim_token, finish)


class _RunAbortedError(RuntimeError):
    pass
