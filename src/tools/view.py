from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.messages import BinaryContent

from src.document.loader import DocumentLoader
from src.vision.client import safe_vision_analyze

if TYPE_CHECKING:
    from src.vision.client import VisionClient

# Shared furniture-specific vision prompts.
PANEL_DETAIL_PROMPT = (
    "Analyze this region of a furniture / cabinet drawing in detail.\n"
    "Report ALL visible data as structured key-value pairs (one per line):\n"
    "PANEL: <part name> <length>x<width>x<thickness>mm (e.g. PANEL: Blat 800x600x18mm)\n"
    "DIMENSION: <value with unit, e.g. 800mm, 600, 1950>\n"
    "BOARD: <material spec, e.g. MFC 18mm, MDF lakierowany, EGGER U399 ST9>\n"
    "EDGE_BANDING: <edge=material, e.g. top=ABS, left=PVC>\n"
    "HARDWARE: <name and code, e.g. Zawias Blum CLIP top, Prowadnica Hettich Quadro>\n"
    "TEXT: <exact text found on drawing>\n"
    "SCALE: <e.g. 1:20, 1:50>\n"
    "NOTE: <any specification or callout>\n"
    "Dimensions are in millimeters. Report exact values only. Do NOT describe or explain."
)
LEGEND_PROMPT = (
    "Scan this region for a legend, material schedule, or specification table.\n"
    "Look for: board types (MFC, MDF, plywood), thickness values, edge banding materials,\n"
    "hardware schedules (hinges, runners, handles), finish/color specs (RAL codes),\n"
    "and any cutting-list or BOM information.\n"
    "Report as key-value pairs:\n"
    "MATERIAL: <spec, e.g. Plyta meblowa 18mm MFC>\n"
    "EDGE: <spec, e.g. Okleina ABS 1mm>\n"
    "HARDWARE: <name, code, qty>\n"
    "FINISH: <spec, e.g. RAL 9005 polmat>\n"
    "TEXT: <any other text>\n"
)
HARDWARE_SEARCH_PROMPT = (
    "Find ALL furniture hardware items in this region.\n"
    "Look for annotations and symbols for:\n"
    "- Hinges (zawias), runners/slides (prowadnica), handles (uchwyt/galka)\n"
    "- Legs/Feet (noga/stopka), locks (zamek), pull-out mechanisms (wysuwacz)\n"
    "- Brackets (katownik), soft-close (samozamykacz)\n"
    "Report each item as:\n"
    "HARDWARE: <name> <brand/code if visible> <qty if shown>\n"
    "Report exact text. Do NOT describe or explain."
)


def view_overview(
    loader: DocumentLoader,
    annotate_grid: bool = True,
    page_index: int = 0,
) -> list:
    """Return a page overview that the agent can inspect."""
    image_bytes, media_type = loader.get_overview_bytes(
        annotate_grid=annotate_grid,
        page_index=page_index,
        max_width=900 if loader.page_count > 1 else None,
    )
    return [
        f"Overview of page {page_index + 1}/{loader.page_count}. "
        "Coordinates range from (0.0, 0.0) at top-left to (1.0, 1.0) at bottom-right.",
        BinaryContent(data=image_bytes, media_type=media_type),
    ]


async def get_tile(
    loader: DocumentLoader,
    row: int,
    col: int,
    grid_size: int = 3,
    page_index: int = 0,
    vision: VisionClient | None = None,
) -> list:
    """Returns [text_description, BinaryContent or vision analysis] as a flat list."""
    tiles = loader.get_tiles(grid_size=grid_size, page_index=page_index)
    for t in tiles:
        if t["row"] == row and t["col"] == col:
            label = t["label"]
            coords = f"({t['x_min']:.2f},{t['y_min']:.2f})-({t['x_max']:.2f},{t['y_max']:.2f})"
            img_bytes, media_type = t["image_bytes"], t["media_type"]

            if vision:
                analysis, fallback_img = await safe_vision_analyze(
                    vision,
                    img_bytes,
                    media_type,
                    f"Analyze tile '{label}' at {coords} of a furniture / cabinet drawing.\n"
                    "Report ALL visible data as key-value pairs (one per line). Examples:\n"
                    "TEXT: <exact text found>\n"
                    "PANEL: <name> <length>x<width>x<thickness>mm (e.g. Blat 800x600x18mm)\n"
                    "DIMENSION: <value with unit, e.g. 800mm, 600, 1:20>\n"
                    "BOARD: <board type, e.g. MFC 18mm, MDF, Plyta meblowa>\n"
                    "EDGE_BANDING: <edge=material, e.g. top=ABS, left=ABS>\n"
                    "HARDWARE: <name and code, e.g. Zawias Blum CLIP top>\n"
                    "SCALE: <e.g. 1:20, 1:50>\n"
                    "NOTE: <text of any note or specification>\n"
                    "Dimensions are in millimeters. Report exact values only. Do NOT describe or explain.",
                    f"tile_{label}",
                )
                if analysis:
                    return [
                        f"Page {page_index + 1}/{loader.page_count}, tile '{label}' at coords {coords} "
                        f"(vision analysis):\n{analysis}",
                    ]
                # Fallback: vision failed, return raw image
                return [
                    f"Page {page_index + 1}/{loader.page_count}, tile '{label}' at coords {coords}. "
                    "Inspect this image carefully.",
                    fallback_img,
                ]

            return [
                f"Page {page_index + 1}/{loader.page_count}, tile '{label}' at coords {coords}. "
                "Inspect this image carefully.",
                BinaryContent(data=img_bytes, media_type=media_type),
            ]
    return [f"Tile ({row},{col}) not found in {grid_size}x{grid_size} grid"]


async def zoom_region(
    loader: DocumentLoader,
    x_min: float,
    y_min: float,
    x_max: float,
    y_max: float,
    purpose: str = "",
    page_index: int = 0,
    vision: VisionClient | None = None,
) -> list:
    """Returns [text_description, BinaryContent or vision analysis] as a flat list."""
    x_min = max(0.0, min(1.0, x_min))
    y_min = max(0.0, min(1.0, y_min))
    x_max = max(0.0, min(1.0, x_max))
    y_max = max(0.0, min(1.0, y_max))

    if x_max <= x_min:
        x_max = min(x_min + 0.1, 1.0)
    if y_max <= y_min:
        y_max = min(y_min + 0.1, 1.0)

    img_bytes, media_type = loader.crop_region_bytes(
        x_min,
        y_min,
        x_max,
        y_max,
        page_index=page_index,
    )
    coords = f"({x_min:.2f},{y_min:.2f})-({x_max:.2f},{y_max:.2f})"
    description = f"Region {coords} on page {page_index + 1}/{loader.page_count} ({purpose or 'detail'})"

    if vision:
        analysis, fallback_img = await safe_vision_analyze(
            vision,
            img_bytes,
            media_type,
            f"Analyze region {coords} on page {page_index + 1}/{loader.page_count} "
            "of a furniture / cabinet drawing.\n"
            f"Purpose: {purpose or 'Extract all visible data'}\n"
            "Report ALL visible data as key-value pairs (one per line). Examples:\n"
            "TEXT: <exact text found>\n"
            "PANEL: <name> <length>x<width>x<thickness>mm\n"
            "DIMENSION: <value with unit>\n"
            "BOARD: <board type>\n"
            "EDGE_BANDING: <edge=material>\n"
            "HARDWARE: <name and code>\n"
            "NOTE: <text>\n"
            "Dimensions are in millimeters. Report exact values only. Do NOT describe or explain.",
            f"region_{coords}",
        )
        if analysis:
            return [f"{description}, vision analysis:\n{analysis}"]
        return [f"{description}. Inspect carefully.", fallback_img]
    return [
        f"{description}. Inspect carefully.",
        BinaryContent(data=img_bytes, media_type=media_type),
    ]


async def read_panel_detail(
    loader,
    x_min,
    y_min,
    x_max,
    y_max,
    panel_name,
    vision=None,
    page_index: int = 0,
):
    """Targeted extraction of a single panel's dimensions, material, edge banding."""
    x_min = max(0.0, min(1.0, x_min))
    y_min = max(0.0, min(1.0, y_min))
    x_max = max(0.0, min(1.0, x_max))
    y_max = max(0.0, min(1.0, y_max))
    if x_max <= x_min:
        x_max = min(x_min + 0.15, 1.0)
    if y_max <= y_min:
        y_max = min(y_min + 0.15, 1.0)
    img_bytes, media_type = loader.crop_region_bytes(
        x_min,
        y_min,
        x_max,
        y_max,
        page_index=page_index,
    )
    coords = f"({x_min:.2f},{y_min:.2f})-({x_max:.2f},{y_max:.2f})"
    prompt = (
        f"Focus on the panel '{panel_name}' in this region of page "
        f"{page_index + 1}/{loader.page_count} of a furniture drawing.\n"
        f"Region: {coords}\n\n"
        "Extract THIS PANEL's specific data:\n"
        f"- Part name: {panel_name} (confirm or correct)\n"
        "- Dimensions: length x width x thickness in mm\n"
        "- Board type: material specification\n"
        "- Edge banding: which edges, material\n"
        "- Quantity: how many of this exact panel\n"
        "- Hardware: any fittings attached to this panel\n\n"
        "Do NOT report other panels. Focus only on this panel.\n"
        "Dimensions are in millimeters."
    )
    if vision:
        analysis, fallback_img = await safe_vision_analyze(
            vision,
            img_bytes,
            media_type,
            prompt,
            f"panel_page_{page_index}_{panel_name}_{coords}",
        )
        if analysis:
            return [
                f"Panel '{panel_name}' on page {page_index + 1}/{loader.page_count} "
                f"at {coords} (vision analysis):\n{analysis}",
            ]
        return [
            f"Panel '{panel_name}' on page {page_index + 1}/{loader.page_count} at {coords}.",
            fallback_img,
        ]
    return [
        f"Panel '{panel_name}' on page {page_index + 1}/{loader.page_count} at {coords}. "
        "Inspect for dimensions, material, edge banding.",
        BinaryContent(data=img_bytes, media_type=media_type),
    ]


async def read_legend(loader, vision=None, page_index: int = 0):
    """Scan for material legends, spec tables, and BOM schedules."""
    legend_regions = [
        ("bottom_strip", 0.0, 0.75, 1.0, 1.0),
        ("right_strip", 0.7, 0.0, 1.0, 1.0),
    ]
    parts = []
    for name, x_min, y_min, x_max, y_max in legend_regions:
        img_bytes, media_type = loader.crop_region_bytes(
            x_min,
            y_min,
            x_max,
            y_max,
            page_index=page_index,
        )
        coords = f"({x_min:.2f},{y_min:.2f})-({x_max:.2f},{y_max:.2f})"
        if vision:
            analysis, fallback_img = await safe_vision_analyze(
                vision,
                img_bytes,
                media_type,
                LEGEND_PROMPT,
                f"legend_page_{page_index}_{name}",
            )
            if analysis:
                parts.append(
                    f"Legend '{name}' on page {page_index + 1}/{loader.page_count} "
                    f"at {coords} (vision analysis):\n{analysis}",
                )
            else:
                parts.append(f"Legend '{name}' on page {page_index + 1}/{loader.page_count} at {coords}:")
                parts.append(fallback_img)
        else:
            parts.append(f"Legend '{name}' on page {page_index + 1}/{loader.page_count} at {coords}:")
            parts.append(BinaryContent(data=img_bytes, media_type=media_type))
    return parts


async def find_hardware(
    loader,
    x_min=None,
    y_min=None,
    x_max=None,
    y_max=None,
    vision=None,
    page_index: int = 0,
):
    """Find furniture hardware items (hinges, runners, handles, legs)."""
    if all(v is not None for v in [x_min, y_min, x_max, y_max]):
        img_bytes, media_type = loader.crop_region_bytes(
            x_min,
            y_min,
            x_max,
            y_max,
            page_index=page_index,
        )
        coords = f"({x_min:.2f},{y_min:.2f})-({x_max:.2f},{y_max:.2f})"
        if vision:
            analysis, fallback_img = await safe_vision_analyze(
                vision,
                img_bytes,
                media_type,
                HARDWARE_SEARCH_PROMPT,
                f"hw_page_{page_index}_{coords}",
            )
            if analysis:
                return [
                    f"Hardware on page {page_index + 1}/{loader.page_count} in {coords} (vision analysis):\n{analysis}",
                ]
            return [
                f"Hardware on page {page_index + 1}/{loader.page_count} in {coords}.",
                fallback_img,
            ]
        return [
            f"Hardware on page {page_index + 1}/{loader.page_count} in {coords}. "
            "Look for furniture fitting annotations.",
            BinaryContent(data=img_bytes, media_type=media_type),
        ]
    # Full quadrant scan
    parts = []
    quads = [
        ("top_left", 0, 0, 0.5, 0.5),
        ("top_right", 0.5, 0, 1.0, 0.5),
        ("bottom_left", 0, 0.5, 0.5, 1.0),
        ("bottom_right", 0.5, 0.5, 1.0, 1.0),
    ]
    for name, qx_min, qy_min, qx_max, qy_max in quads:
        img_bytes, media_type = loader.crop_region_bytes(
            qx_min,
            qy_min,
            qx_max,
            qy_max,
            page_index=page_index,
        )
        if vision:
            analysis, fallback_img = await safe_vision_analyze(
                vision,
                img_bytes,
                media_type,
                HARDWARE_SEARCH_PROMPT,
                f"hw_page_{page_index}_{name}",
            )
            if analysis:
                parts.append(
                    f"Hardware on page {page_index + 1}/{loader.page_count} in {name} (vision analysis):\n{analysis}",
                )
            else:
                parts.append(f"Hardware on page {page_index + 1}/{loader.page_count} in {name}:")
                parts.append(fallback_img)
        else:
            parts.append(f"Hardware on page {page_index + 1}/{loader.page_count} in {name}:")
            parts.append(BinaryContent(data=img_bytes, media_type=media_type))
    return parts
