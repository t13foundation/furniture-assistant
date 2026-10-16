"""Interpretation agents: assign roles to numbered dimension marks, and re-run on what is still missing."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from config.agent_prompts import MODULE_INTERPRETATION_PROMPT, MODULE_REFINE_PROMPT
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

from src.furniture.resolve import module_region
from src.furniture.schema import (
    DimensionChain,
    DimensionMark,
    FurnitureModule,
    ModuleDraft,
    PageDraft,
    TextRun,
    Unit,
)
from src.pipeline.agent import create_model

_MAX_OUTPUT_TOKENS = 16384
_TEXT_RUNS_LIMIT = 6000
# Page region around the module shown to the refine agent.
REFINE_MARGIN = 0.06


def interpretation_model(model_name: str, api_base: str, api_key: str | None) -> Model:
    """LM Studio / OpenAI-compatible chat model; OpenAI's own API goes through the Responses API.

    OpenAI reasoning models (gpt-6 family) reject function tools — which structured output uses — on
    Chat Completions. The key falls back to ``OPENAI_API_KEY`` when the configured one is the LM Studio
    placeholder.
    """
    if "api.openai.com" not in api_base:
        return create_model(model_name, api_base, api_key)
    if not api_key or api_key == "lm-studio":
        api_key = os.environ.get("OPENAI_API_KEY")
    return OpenAIResponsesModel(model_name, provider=OpenAIProvider(base_url=api_base, api_key=api_key))


def create_interpretation_agent(
    model_name: str, api_base: str = "http://localhost:1234/v1", api_key: str | None = None
) -> Agent[None, PageDraft]:
    return Agent(
        model=interpretation_model(model_name, api_base, api_key),
        output_type=PageDraft,
        instructions=MODULE_INTERPRETATION_PROMPT,
        retries=2,
    )


def create_refine_agent(
    model_name: str, api_base: str = "http://localhost:1234/v1", api_key: str | None = None
) -> Agent[None, ModuleDraft]:
    return Agent(
        model=interpretation_model(model_name, api_base, api_key),
        output_type=ModuleDraft,
        instructions=MODULE_REFINE_PROMPT,
        retries=2,
    )


def _mark_line(mark: DimensionMark) -> str:
    b = mark.bbox
    chain = f" chain {mark.chain_id}" if mark.chain_id else ""
    return f"{mark.id}: {mark.value:g} {mark.orientation} at x={(b.x_min + b.x_max) / 2:.3f} y={(b.y_min + b.y_max) / 2:.3f}{chain}"


def _chain_line(chain: DimensionChain) -> str:
    line = f"{chain.id} {chain.orientation}: {' + '.join(chain.mark_ids)} = {chain.sum_value:g}"
    if chain.overall_mark_id:
        state = "matches" if chain.consistent else "DOES NOT match"
        line += f"; overall mark {chain.overall_mark_id} {state}"
    return line


def _marks_block(marks: list[DimensionMark], chains: list[DimensionChain], units: Unit) -> str:
    if not marks:
        return (
            "This page has no text layer, so there are no numbered marks. Read dimensions from the image "
            "and return them in the *_value fields; set units to the drawing's units."
        )
    block = (
        f"Dimension marks read from the PDF text layer (units: {units}; x/y = label centre, normalized "
        "0-1 page coordinates, y from the top):\n" + "\n".join(_mark_line(m) for m in marks)
    )
    if chains:
        block += (
            "\n\nDimension chains (marks aligned on one dimension line, left to right or top to bottom):\n"
            + "\n".join(_chain_line(c) for c in chains)
        )
    return block


def page_prompt(marks: list[DimensionMark], chains: list[DimensionChain], text_runs: list[TextRun], units: Unit) -> str:
    texts = " | ".join(t.text for t in text_runs)[:_TEXT_RUNS_LIMIT]
    return f"Page units: {units}\n\n{_marks_block(marks, chains, units)}\n\nText on the page:\n{texts or '(none)'}"


async def interpret_page(
    agent: Agent[None, PageDraft],
    image_png: bytes,
    marks: list[DimensionMark],
    chains: list[DimensionChain],
    text_runs: list[TextRun],
    units: Unit,
    on_usage: Callable[[RunUsage], Awaitable[None]] | None = None,
) -> PageDraft:
    usage = RunUsage()
    try:
        result = await agent.run(
            [page_prompt(marks, chains, text_runs, units), BinaryContent(data=image_png, media_type="image/png")],
            model_settings={"max_tokens": _MAX_OUTPUT_TOKENS},
            usage=usage,
        )
    finally:
        if on_usage:
            await on_usage(usage)
    return result.output


def refine_prompt(module: FurnitureModule, draft: ModuleDraft) -> str:
    """The previous interpretation, what is still missing, and the marks/texts around the module."""
    region = module_region(module, REFINE_MARGIN)

    def inside(box) -> bool:
        cx, cy = (box.x_min + box.x_max) / 2, (box.y_min + box.y_max) / 2
        return region.x_min <= cx <= region.x_max and region.y_min <= cy <= region.y_max

    marks = [m for m in module.marks if inside(m.bbox)]
    ids = {m.id for m in marks}
    chains = [c for c in module.chains if set(c.mark_ids) & ids]
    gaps = "\n".join(
        f"- {q.label} ({q.reason}"
        + (f", currently {q.current_mm:g} mm" if q.current_mm else "")
        + (f"; candidate marks: {', '.join(q.candidate_mark_ids)}" if q.candidate_mark_ids else "")
        + ")"
        for q in module.questions
    )
    texts = " | ".join(t.text for t in module.texts)[:_TEXT_RUNS_LIMIT]
    return (
        f"Module {draft.module_id} '{draft.title}' (page units: {module.units}).\n\n"
        f"Your previous interpretation:\n{draft.model_dump_json(exclude_none=True)}\n\n"
        f"Still missing or only guessed:\n{gaps or '- nothing'}\n\n"
        f"{_marks_block(marks, chains, module.units)}\n\nText around the module:\n{texts or '(none)'}"
    )


async def refine_draft(
    agent: Agent[None, ModuleDraft],
    image_png: bytes,
    module: FurnitureModule,
    draft: ModuleDraft,
    on_usage: Callable[[RunUsage], Awaitable[None]] | None = None,
) -> ModuleDraft:
    """Ask the model once more for the module's open questions; returns the full corrected draft."""
    usage = RunUsage()
    try:
        result = await agent.run(
            [refine_prompt(module, draft), BinaryContent(data=image_png, media_type="image/png")],
            model_settings={"max_tokens": _MAX_OUTPUT_TOKENS},
            usage=usage,
        )
    finally:
        if on_usage:
            await on_usage(usage)
    return result.output.model_copy(update={"module_id": draft.module_id})
