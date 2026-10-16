"""Shared tool definitions for all pipeline agents.

Tools are defined once here and registered on any agent via
`register_tools(agent, tool_defs)`. This eliminates the
duplication that previously existed across 3 agent factories.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent, RunContext

if TYPE_CHECKING:
    from src.pipeline.agent import DrawingDeps


@dataclass
class ToolDef:
    """A tool definition that can be registered on any agent."""

    name: str
    func: Callable
    is_async: bool = True


# ── Tool wrapper with caching ────────────────────────────────────


def _cached_tool(
    original_fn: Callable,
    tool_name: str,
) -> Callable:
    """Wrap a tool function to check/store results in the cache."""
    import functools

    @functools.wraps(original_fn)
    async def wrapper(ctx: RunContext[DrawingDeps], **kwargs):
        # Check cache first
        if ctx.deps.tool_cache is not None:
            cached = ctx.deps.tool_cache.get(tool_name, kwargs)
            if cached is not None:
                return cached

        # Execute the actual tool
        result = await original_fn(ctx, **kwargs)

        # Store in cache
        if ctx.deps.tool_cache is not None:
            ctx.deps.tool_cache.put(tool_name, kwargs, result)

        return result

    return wrapper


# ── Tool definitions (defined once) ──────────────────────────────


async def view_overview_tool(
    ctx: RunContext[DrawingDeps],
    annotate_grid: bool = True,
    page_index: int = 0,
) -> list:
    """View a low-resolution overview of one drawing page.

    Use this first to understand the page layout. ``page_index`` is zero-based.
    The grid shows normalized coordinates from 0.0 to 1.0.
    """
    from src.tools.view import view_overview

    return view_overview(ctx.deps.loader, annotate_grid, page_index)


async def get_tile_tool(
    ctx: RunContext[DrawingDeps],
    row: int,
    col: int,
    grid_size: int = 3,
    page_index: int = 0,
) -> list:
    """Get a tile from the drawing grid. The drawing is divided into a 3x3 grid.

    Row 0 = top, row 2 = bottom. Col 0 = left, col 2 = right.
    Each tile has 10% overlap with neighbors. Use this to inspect specific sections.
    """
    from src.tools.view import get_tile

    return await get_tile(
        ctx.deps.loader,
        row,
        col,
        grid_size,
        page_index,
        vision=ctx.deps.vision_client,
    )


async def zoom_region_tool(
    ctx: RunContext[DrawingDeps],
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    purpose: str = "",
    page_index: int = 0,
) -> list:
    """Crop and inspect a rectangular region of the drawing at high resolution.

    Coordinates are normalized (0.0 to 1.0): (0,0) is top-left, (1,1) is bottom-right.
    Use this to zoom into specific areas like the title block or symbol clusters.
    """
    from src.tools.view import zoom_region

    return await zoom_region(
        ctx.deps.loader,
        x_min,
        y_min,
        x_max,
        y_max,
        purpose,
        page_index=page_index,
        vision=ctx.deps.vision_client,
    )


async def read_title_block_tool(
    ctx: RunContext[DrawingDeps],
    page_index: int = 0,
) -> list:
    """Read the title block on one zero-based drawing page."""
    from src.tools.title_block import read_title_block

    return await read_title_block(
        ctx.deps.loader,
        vision=ctx.deps.vision_client,
        page_index=page_index,
    )


async def read_title_block_cells_tool(
    ctx: RunContext[DrawingDeps],
    page_index: int = 0,
) -> list:
    """Read individual title-block cells on one zero-based drawing page."""
    from src.tools.title_block import read_title_block_cells

    return await read_title_block_cells(
        ctx.deps.loader,
        vision=ctx.deps.vision_client,
        page_index=page_index,
    )


async def analyze_title_block_tool(
    ctx: RunContext[DrawingDeps],
    page_index: int = 0,
) -> list:
    """Analyze title-block structure on one zero-based drawing page."""
    from src.tools.title_block import analyze_title_block_structure

    return await analyze_title_block_structure(
        ctx.deps.loader,
        vision=ctx.deps.vision_client,
        page_index=page_index,
    )


async def find_symbols_tool(
    ctx: RunContext[DrawingDeps],
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    symbol_types: str | list[str] | None = None,
    page_index: int = 0,
) -> list:
    """Find technical symbols in a region. symbol_types: 'roughness', 'gd&t', 'weld', or 'all'.

    Returns cropped images of the region with hints about what to look for.
    """
    from src.document.regions import Region
    from src.tools.symbols import find_symbols

    region = Region(x_min, y_min, x_max, y_max)
    normalized = [symbol_types] if isinstance(symbol_types, str) else symbol_types
    return await find_symbols(
        ctx.deps.loader,
        region,
        normalized,
        vision=ctx.deps.vision_client,
        page_index=page_index,
    )


async def read_panel_detail_tool(
    ctx: RunContext[DrawingDeps],
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    panel_name: str,
    page_index: int = 0,
) -> list:
    """Read a specific panel's dimensions, material, and edge banding at high resolution.

    Provide the panel's approximate region and its name. Zooms in and extracts
    length, width, thickness, board type, and edge banding for that one panel.
    """
    from src.tools.view import read_panel_detail

    return await read_panel_detail(
        ctx.deps.loader,
        x_min,
        y_min,
        x_max,
        y_max,
        panel_name,
        vision=ctx.deps.vision_client,
        page_index=page_index,
    )


async def read_legend_tool(
    ctx: RunContext[DrawingDeps],
    page_index: int = 0,
) -> list:
    """Scan one zero-based page for material legends, spec tables, and BOM schedules."""
    from src.tools.view import read_legend

    return await read_legend(
        ctx.deps.loader,
        vision=ctx.deps.vision_client,
        page_index=page_index,
    )


async def find_hardware_tool(
    ctx: RunContext[DrawingDeps],
    x_min: float | None = None,
    y_min: float | None = None,
    x_max: float | None = None,
    y_max: float | None = None,
    page_index: int = 0,
) -> list:
    """Find furniture hardware: hinges, runners, handles, legs, locks, soft-close."""
    from src.tools.view import find_hardware

    return await find_hardware(
        ctx.deps.loader,
        x_min,
        y_min,
        x_max,
        y_max,
        vision=ctx.deps.vision_client,
        page_index=page_index,
    )


async def read_dimensions_tool(
    ctx: RunContext[DrawingDeps],
    x_min: float | None = None,
    y_min: float | None = None,
    x_max: float | None = None,
    y_max: float | None = None,
    page_index: int = 0,
) -> list:
    """Read dimensional annotations from the drawing.

    If coordinates are provided, inspects that specific region.
    Otherwise, scans the full drawing in quadrants.
    """
    from src.document.regions import Region
    from src.tools.dimensions import read_dimensions

    region = (
        Region(x_min, y_min, x_max, y_max)
        if x_min is not None and y_min is not None and x_max is not None and y_max is not None
        else None
    )
    return await read_dimensions(
        ctx.deps.loader,
        region,
        vision=ctx.deps.vision_client,
        page_index=page_index,
    )


async def search_text_tool(
    ctx: RunContext[DrawingDeps],
    keywords: list[str],
    x_min: float | None = None,
    y_min: float | None = None,
    x_max: float | None = None,
    y_max: float | None = None,
    page_index: int = 0,
) -> list:
    """Search for keywords in the drawing. Returns images with keyword match hints.

    Keywords can be in PL/EN/DE. If coordinates provided, searches that region only.
    """
    from src.document.regions import Region
    from src.tools.text_search import search_text

    region = (
        Region(x_min, y_min, x_max, y_max)
        if x_min is not None and y_min is not None and x_max is not None and y_max is not None
        else None
    )
    return await search_text(
        ctx.deps.loader,
        keywords,
        region,
        vision=ctx.deps.vision_client,
        page_index=page_index,
    )


async def validate_edge_material_tool(ctx: RunContext[DrawingDeps], edge_text: str) -> dict[str, str | bool]:
    """Validate an edge-banding material against known furniture materials.

    Returns match status, description, and confidence level.
    """
    from src.tools.cross_reference import validate_edge_material

    return validate_edge_material(edge_text)


async def validate_board_type_tool(ctx: RunContext[DrawingDeps], board_text: str) -> dict[str, str | bool]:
    """Validate a board/panel material against known furniture board types.

    Returns match status and confidence level.
    """
    from src.tools.cross_reference import validate_board_type

    return validate_board_type(board_text)


# ── Registry ─────────────────────────────────────────────────────

ALL_TOOLS: list[ToolDef] = [
    ToolDef("view_overview", view_overview_tool),
    ToolDef("get_tile", get_tile_tool),
    ToolDef("zoom_region", zoom_region_tool),
    ToolDef("read_panel_detail", read_panel_detail_tool),
    ToolDef("read_title_block", read_title_block_tool),
    ToolDef("read_title_block_cells", read_title_block_cells_tool),
    ToolDef("analyze_title_block", analyze_title_block_tool),
    ToolDef("read_legend", read_legend_tool),
    ToolDef("find_hardware", find_hardware_tool),
    ToolDef("read_dimensions", read_dimensions_tool),
    ToolDef("search_text", search_text_tool),
    ToolDef("validate_board_type", validate_board_type_tool),
]

# Subset for validation agent (fewer tools)
VALIDATION_TOOLS: list[ToolDef] = [
    ToolDef("get_tile", get_tile_tool),
    ToolDef("zoom_region", zoom_region_tool),
    ToolDef("read_title_block", read_title_block_tool),
    ToolDef("search_text", search_text_tool),
    ToolDef("validate_board_type", validate_board_type_tool),
]


def register_tools(agent: Agent[Any, Any], tool_defs: list[ToolDef]) -> None:
    """Register a list of tool definitions on an agent.

    Each tool is wrapped to check the DrawingDeps.tool_cache before
    executing. This provides automatic result caching across tool calls.
    """
    for tool_def in tool_defs:
        wrapped = _cached_tool(tool_def.func, tool_def.name)
        agent.tool(wrapped)
