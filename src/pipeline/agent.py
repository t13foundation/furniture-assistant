from __future__ import annotations

from dataclasses import dataclass

from config.settings import DocumentConfig
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# Resolve forward reference for tool registry (circular import fix)
# Must come AFTER DrawingDeps is defined
import src.tools.registry as _reg
from src.document.loader import DocumentLoader
from src.schema.extraction import ExtractionResult, LayoutMap
from src.tools.cache import ToolCache
from src.tools.registry import ALL_TOOLS, VALIDATION_TOOLS, register_tools
from src.vision.client import VisionClient


@dataclass
class DrawingDeps:
    loader: DocumentLoader
    drawing_path: str
    filename: str = ""
    layout_map: LayoutMap | None = None
    previous_extraction: ExtractionResult | None = None
    vision_client: VisionClient | None = None
    tool_cache: ToolCache | None = None  # NEW: shared tool result cache


# Wire up DrawingDeps for the tool registry's type hint resolution
_reg.DrawingDeps = DrawingDeps


def make_loader(path: str, config: DocumentConfig | None = None) -> DocumentLoader:
    """Create a DocumentLoader with config-driven settings."""
    if config is not None:
        return DocumentLoader(
            path,
            overview_max_width=config.overview_max_width,
            detail_max_px=config.detail_max_px,
            grid_divisions=config.grid_divisions,
            pdf_render_scale=config.pdf_render_scale,
            overview_format=config.overview_format,
            overview_quality=config.overview_quality,
            detail_format=config.detail_format,
            detail_quality=config.detail_quality,
        )
    return DocumentLoader(path)


def create_model(
    model_name: str, api_base: str = "http://localhost:1234/v1", api_key: str | None = None
) -> OpenAIChatModel:
    # LM Studio exposes an OpenAI-compatible API but does not require a real key.
    import os

    if not api_key:
        api_key = os.environ.get("LM_STUDIO_API_KEY") or "lm-studio"
    provider = OpenAIProvider(base_url=api_base, api_key=api_key)
    return OpenAIChatModel(model_name, provider=provider)


def create_extraction_agent(
    model_name: str,
    api_base: str = "http://localhost:1234/v1",
    api_key: str | None = None,
    instructions: str = "",
) -> Agent[DrawingDeps, ExtractionResult]:
    model = create_model(model_name, api_base, api_key)
    agent: Agent[DrawingDeps, ExtractionResult] = Agent(
        model=model,
        deps_type=DrawingDeps,
        output_type=ExtractionResult,
        instructions=instructions,
        retries=5,
        instrument=True,
    )

    # Register all tools from shared registry (with caching)
    register_tools(agent, ALL_TOOLS)

    return agent


def create_exploration_agent(
    model_name: str,
    api_base: str = "http://localhost:1234/v1",
    api_key: str | None = None,
    instructions: str = "",
) -> Agent[DrawingDeps, str]:
    """Create an agent that explores drawings with tools and returns a text summary.

    Same tools as create_extraction_agent, but output_type=str so the model
    doesn't need to produce complex structured output.
    """
    model = create_model(model_name, api_base, api_key)
    agent: Agent[DrawingDeps, str] = Agent(
        model=model,
        deps_type=DrawingDeps,
        output_type=str,
        instructions=instructions,
        retries=2,
        instrument=True,
    )

    register_tools(agent, ALL_TOOLS)

    return agent


def create_validation_agent(
    model_name: str,
    api_base: str = "http://localhost:1234/v1",
    api_key: str | None = None,
    instructions: str = "",
) -> Agent[DrawingDeps, str]:
    """Create a validation agent that re-verifies specific fields against the drawing.

    Has a focused subset of tools for targeted re-verification:
    zoom_region, get_tile, read_title_block, search_text,
    validate_board_type, validate_edge_material, validate_hardware.
    """
    model = create_model(model_name, api_base, api_key)
    agent: Agent[DrawingDeps, str] = Agent(
        model=model,
        deps_type=DrawingDeps,
        output_type=str,
        instructions=instructions,
        retries=3,
        instrument=True,
    )

    # Register validation-subset tools from shared registry
    register_tools(agent, VALIDATION_TOOLS)

    return agent


def create_summarizing_agent(
    model_name: str,
    api_base: str = "http://localhost:1234/v1",
    api_key: str | None = None,
    instructions: str = "",
) -> Agent[None, str]:
    """Create a summarizing agent that formats text into JSON.

    Uses output_type=str instead of ExtractionResult to avoid relying on
    model's structured output support (which local models often lack).
    JSON is parsed manually by the caller.
    """
    from config.agent_prompts import SUMMARIZE_PROMPT

    model = create_model(model_name, api_base, api_key)
    agent: Agent[None, str] = Agent(
        model=model,
        output_type=str,
        instructions=instructions or SUMMARIZE_PROMPT,
        retries=3,
        instrument=True,
    )
    return agent


def create_unified_agent(
    model_name: str,
    api_base: str = "http://localhost:1234/v1",
    api_key: str | None = None,
    instructions: str = "",
) -> Agent[DrawingDeps, str]:
    """Create a tool-capable text agent for exploration or validation.

    Uses output_type=str so LM Studio models don't need structured output support.
    The caller decides whether the agent produces an evidence log or a JSON diff.
    This avoids the "empty model response"
    crashes that occur with output_type=ExtractionResult on local models.
    """
    model = create_model(model_name, api_base, api_key)
    agent: Agent[DrawingDeps, str] = Agent(
        model=model,
        deps_type=DrawingDeps,
        output_type=str,
        instructions=instructions,
        retries=5,
        instrument=True,
    )

    register_tools(agent, ALL_TOOLS)

    return agent
