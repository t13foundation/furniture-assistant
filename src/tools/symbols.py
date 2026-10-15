from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.messages import BinaryContent

from src.document.loader import DocumentLoader
from src.document.regions import Region
from src.vision.client import safe_vision_analyze

if TYPE_CHECKING:
    from src.vision.client import VisionClient

SYMBOL_CATEGORIES = {
    "hardware": ["hinge", "zawias", "runner", "prowadnica", "handle", "uchwyt", "Blum", "Hettich"],
    "edge_banding": ["okleina", "edge", "ABS", "PVC", "Kante"],
    "dimension": ["mm", "cm", "szerokość", "wysokość", "głębokość"],
}


async def find_symbols(
    loader: DocumentLoader,
    region: Region,
    symbol_types: list[str] | None = None,
    vision: VisionClient | None = None,
    page_index: int = 0,
) -> list:
    """Returns flat list: [text, BinaryContent or vision analysis, ...]."""
    img_bytes, media_type = loader.crop_region_bytes(
        region.x_min,
        region.y_min,
        region.x_max,
        region.y_max,
        page_index=page_index,
    )

    types = symbol_types or ["all"]
    if "all" in types:
        types = list(SYMBOL_CATEGORIES.keys())

    categories = {t: SYMBOL_CATEGORIES.get(t, []) for t in types}
    coords = f"({region.x_min:.2f},{region.y_min:.2f})-({region.x_max:.2f},{region.y_max:.2f})"

    if vision:
        cat_desc = ", ".join(f"{t}: {', '.join(syms)}" for t, syms in categories.items())
        analysis, fallback_img = await safe_vision_analyze(
            vision,
            img_bytes,
            media_type,
            f"Find furniture symbols and markers in region {coords} on page "
            f"{page_index + 1}/{loader.page_count} of a furniture drawing.\n"
            f"Symbol categories: {cat_desc}\n"
            "Report each found symbol as:\n"
            "SYMBOL: <type> <exact value> at <location>\n"
            "Example: HARDWARE: zawias Blum at cabinet door\n"
            "Example: EDGE_BANDING: ABS 1mm at front edge\n"
            "Report exact values. Do NOT describe or explain.",
            f"symbols_page_{page_index}_{coords}",
        )
        if analysis:
            return [
                f"Region {coords} on page {page_index + 1}/{loader.page_count} "
                f"— symbol search (vision analysis):\n{analysis}",
            ]
        return [
            f"Region {coords} on page {page_index + 1}/{loader.page_count}. Look for these symbols:",
            fallback_img,
        ]

    parts = [
        f"Region {coords} on page {page_index + 1}/{loader.page_count}. Look for these symbols:",
        BinaryContent(data=img_bytes, media_type=media_type),
    ]

    for cat, symbols in categories.items():
        parts.append(f"Category '{cat}': look for {', '.join(symbols)}")

    return parts
