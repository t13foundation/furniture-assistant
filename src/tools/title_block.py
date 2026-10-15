from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.messages import BinaryContent

from src.document.loader import DocumentLoader
from src.document.regions import TITLE_BLOCK_REGIONS, Region
from src.vision.client import safe_vision_analyze

if TYPE_CHECKING:
    from src.vision.client import VisionClient

TITLE_BLOCK_VISION_PROMPT = (
    "Extract exact text from this title block / legend of a furniture drawing. "
    "Report ONLY what is literally visible as key-value pairs, one per line.\n"
    "Format: FIELD: value\n"
    "Fields to find:\n"
    "PROJECT_NAME: <project name or null>\n"
    "CLIENT: <client / investor or null>\n"
    "CABINET_ID: <cabinet / unit identifier or null>\n"
    "SCALE: <ratio e.g. 1:20 or null>\n"
    "UNITS: <measurement units, typically mm or null>\n"
    "DATE: <date or null>\n"
    "DESIGNER: <name or null>\n"
    "DATE: <date or null>\n"
    "DESIGNER: <name or null>\n"
    "Also report any other visible text not captured above.\n"
    "Do NOT describe or explain. Only report exact visible text values."
)

TITLE_BLOCK_CELL_PROMPT = (
    "Read the exact text value in this title block cell. "
    "Report the label text and the value text separately.\n"
    "Format: LABEL: <the field label text> | VALUE: <the value text>\n"
    "If the cell is empty or blank, respond with: EMPTY\n"
    "Report exact text only. Do not describe or explain."
)

TITLE_BLOCK_STRUCTURE_PROMPT = (
    "Analyze the grid structure of this title block from a furniture drawing.\n"
    "For each cell in the grid, report:\n"
    "CELL row,col: <label text visible in or above this cell>\n"
    "If a cell has no label, report: CELL row,col: (no label)\n"
    "Report the cell layout as a table. Focus on identifying field labels like "
    "'Project', 'Klient', 'Skala', 'Scale', 'Date', 'Data', 'Projekt', 'Name', etc.\n"
    "Report exact text only."
)


async def read_title_block(
    loader: DocumentLoader,
    region: Region | None = None,
    vision: VisionClient | None = None,
    page_index: int = 0,
) -> list:
    """Returns flat list of [text, BinaryContent or vision analysis, ...]."""
    regions = [region] if region else TITLE_BLOCK_REGIONS

    parts: list = []
    for r in regions:
        img_bytes, media_type = loader.crop_region_bytes(
            r.x_min,
            r.y_min,
            r.x_max,
            r.y_max,
            enhance=True,
            page_index=page_index,
        )
        label = (
            f"Title block '{r.label}' on page {page_index + 1}/{loader.page_count} "
            f"at ({r.x_min:.2f},{r.y_min:.2f})-({r.x_max:.2f},{r.y_max:.2f})"
        )

        if vision:
            analysis, fallback_img = await safe_vision_analyze(
                vision,
                img_bytes,
                media_type,
                TITLE_BLOCK_VISION_PROMPT,
                f"title_block_page_{page_index}_{r.label}",
            )
            if analysis:
                parts.append(f"{label} (vision analysis):\n{analysis}")
            else:
                parts.append(f"{label}:")
                parts.append(fallback_img)
        else:
            parts.append(f"{label}:")
            parts.append(BinaryContent(data=img_bytes, media_type=media_type))

    if not vision:
        parts.append(
            "Inspect each title block image to extract: drawing number, revision, "
            "material, client, description, weight, date."
        )
    return parts


async def read_title_block_cells(
    loader: DocumentLoader,
    region: Region | None = None,
    vision: VisionClient | None = None,
    page_index: int = 0,
) -> list:
    """Extract individual cells from the title block at high resolution.

    Returns a flat list of cell readings: ["CELL row,col: LABEL | VALUE", ...]
    Each cell is analyzed separately for better OCR accuracy on small text.
    """
    regions = [region] if region else [TITLE_BLOCK_REGIONS[0]]  # Default: bottom-right

    parts: list = []
    for r in regions:
        cells = loader.crop_title_block_cells(r, page_index=page_index)

        if not cells:
            # Fallback to regular title block reading
            return await read_title_block(loader, r, vision, page_index)

        parts.append(
            f"Title block '{r.label}' on page {page_index + 1}/{loader.page_count} "
            f"— {len(cells)} cells extracted at high resolution:"
        )

        for cell in cells:
            if vision:
                analysis, fallback_img = await safe_vision_analyze(
                    vision,
                    cell.image_bytes,
                    cell.media_type,
                    TITLE_BLOCK_CELL_PROMPT,
                    f"title_block_page_{page_index}_cell_{cell.row}_{cell.col}",
                )
                if analysis:
                    parts.append(f"  Cell ({cell.row},{cell.col}): {analysis.strip()}")
                else:
                    parts.append(f"  Cell ({cell.row},{cell.col}):")
                    parts.append(fallback_img)
            else:
                parts.append(f"  Cell ({cell.row},{cell.col}):")
                parts.append(BinaryContent(data=cell.image_bytes, media_type=cell.media_type))

    return parts


async def analyze_title_block_structure(
    loader: DocumentLoader,
    region: Region | None = None,
    vision: VisionClient | None = None,
    page_index: int = 0,
) -> list:
    """Two-stage title block extraction: discover structure, then per-cell reading.

    Stage 1: Send the title block image to discover the grid layout and labels.
    Returns structured description of the title block cells.
    """
    regions = [region] if region else [TITLE_BLOCK_REGIONS[0]]

    parts: list = []
    for r in regions:
        img_bytes, media_type = loader.crop_region_bytes(
            r.x_min,
            r.y_min,
            r.x_max,
            r.y_max,
            enhance=True,
            page_index=page_index,
        )
        label = (
            f"Title block structure '{r.label}' on page {page_index + 1}/{loader.page_count} "
            f"at ({r.x_min:.2f},{r.y_min:.2f})-({r.x_max:.2f},{r.y_max:.2f})"
        )

        if vision:
            analysis, fallback_img = await safe_vision_analyze(
                vision,
                img_bytes,
                media_type,
                TITLE_BLOCK_STRUCTURE_PROMPT,
                f"title_block_page_{page_index}_structure_{r.label}",
            )
            if analysis:
                parts.append(f"{label}:\n{analysis}")
            else:
                parts.append(f"{label}:")
                parts.append(fallback_img)
        else:
            parts.append(f"{label}:")
            parts.append(BinaryContent(data=img_bytes, media_type=media_type))

    # Also extract cells for per-cell reading
    for r in regions:
        cells = loader.crop_title_block_cells(r, page_index=page_index)
        parts.append(f"\nExtracted {len(cells)} individual cells for per-cell verification.")
        for cell in cells[:6]:  # Limit to first 6 cells to avoid context bloat
            if vision:
                analysis, _ = await safe_vision_analyze(
                    vision,
                    cell.image_bytes,
                    cell.media_type,
                    TITLE_BLOCK_CELL_PROMPT,
                    f"title_block_page_{page_index}_cell_{cell.row}_{cell.col}",
                )
                if analysis:
                    parts.append(f"  Cell ({cell.row},{cell.col}): {analysis.strip()}")

    return parts
