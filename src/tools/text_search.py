from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.messages import BinaryContent

from src.document.loader import DocumentLoader
from src.document.regions import Region
from src.reference.keywords import (
    BOARD_KEYWORDS,
    DIMENSION_KEYWORDS,
    EDGE_BANDING_KEYWORDS,
    HARDWARE_KEYWORDS,
    PANEL_KEYWORDS,
)
from src.vision.client import safe_vision_analyze

if TYPE_CHECKING:
    from src.vision.client import VisionClient


KEYWORD_CATEGORIES: dict[str, dict[str, list[str]]] = {
    "panel": PANEL_KEYWORDS,
    "hardware": HARDWARE_KEYWORDS,
    "edge_banding": EDGE_BANDING_KEYWORDS,
    "board_type": BOARD_KEYWORDS,
    "dimension": DIMENSION_KEYWORDS,
}


def _flatten_keywords(category: str) -> list[str]:
    kw_dict = KEYWORD_CATEGORIES.get(category, {})
    return [kw.lower() for kws in kw_dict.values() for kw in kws]


async def search_text(
    loader: DocumentLoader,
    keywords: list[str],
    region: Region | None = None,
    vision: VisionClient | None = None,
    page_index: int = 0,
) -> list:
    """Returns flat list: [text, BinaryContent or vision analysis, ...]."""
    img_bytes: bytes
    media_type: str
    region_desc: str

    if region:
        img_bytes, media_type = loader.crop_region_bytes(
            region.x_min,
            region.y_min,
            region.x_max,
            region.y_max,
            enhance=True,
            page_index=page_index,
        )
        region_desc = f"({region.x_min:.2f},{region.y_min:.2f})-({region.x_max:.2f},{region.y_max:.2f})"
    else:
        img_bytes, media_type = loader.get_overview_bytes(
            annotate_grid=False,
            page_index=page_index,
        )
        region_desc = "full_drawing"

    lower_keywords = [kw.lower() for kw in keywords]

    matched_categories: list[str] = []
    for cat_name, kw_dict in KEYWORD_CATEGORIES.items():
        flat = [kw.lower() for kws in kw_dict.values() for kw in kws]
        if any(kw in flat for kw in lower_keywords):
            matched_categories.append(cat_name)

    cats = ", ".join(matched_categories) if matched_categories else "custom"

    if vision:
        analysis, fallback_img = await safe_vision_analyze(
            vision,
            img_bytes,
            media_type,
            f"Search {region_desc} on page {page_index + 1}/{loader.page_count} "
            f"of a furniture drawing for these keywords: {', '.join(keywords)}. "
            f"Related categories: {cats}.\n"
            "Report each match as:\n"
            "MATCH: <keyword> — <exact surrounding text> — <location>\n"
            "Report ALL occurrences with exact text. Do NOT describe or explain.",
            f"text_search_page_{page_index}_{region_desc}",
        )
        if analysis:
            return [
                f"Search {region_desc} on page {page_index + 1}/{loader.page_count} "
                f"for '{', '.join(keywords)}' (vision analysis):\n{analysis}",
            ]
        return [
            f"Searching {region_desc} on page {page_index + 1}/{loader.page_count} "
            f"for keywords: {', '.join(keywords)}. Matched categories: {cats}.",
            fallback_img,
            "Look for the specified keywords in the image. "
            "Report any matching text found with its surrounding context.",
        ]

    return [
        f"Searching {region_desc} on page {page_index + 1}/{loader.page_count} "
        f"for keywords: {', '.join(keywords)}. Matched categories: {cats}.",
        BinaryContent(data=img_bytes, media_type=media_type),
        "Look for the specified keywords in the image. Report any matching text found with its surrounding context.",
    ]
