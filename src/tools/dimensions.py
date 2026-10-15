from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.messages import BinaryContent

from src.document.loader import DocumentLoader
from src.document.regions import Region
from src.vision.client import safe_vision_analyze

if TYPE_CHECKING:
    from src.vision.client import VisionClient

DIMENSION_VISION_PROMPT = (
    "Extract ALL dimensional annotations from this region of a furniture drawing.\n"
    "Report as one value per line:\n"
    "LINEAR: <value in mm, e.g. 800mm>\n"
    "PANEL: <name> <length x width> x <thickness>, e.g. PANEL: Blat 800x600x18\n"
    "TOLERANCE: <±value, e.g. ±0.5>\n"
    "ANGLE: <value, e.g. 45°>\n"
    "Report exact numeric values with units. Do NOT describe or explain."
)


async def read_dimensions(
    loader: DocumentLoader,
    region: Region | None = None,
    vision: VisionClient | None = None,
    page_index: int = 0,
) -> list:
    """Returns flat list: [text, BinaryContent or vision analysis, ...]."""
    if region:
        img_bytes, media_type = loader.crop_region_bytes(
            region.x_min,
            region.y_min,
            region.x_max,
            region.y_max,
            page_index=page_index,
        )
        coords = f"({region.x_min:.2f},{region.y_min:.2f})-({region.x_max:.2f},{region.y_max:.2f})"

        if vision:
            analysis, fallback_img = await safe_vision_analyze(
                vision,
                img_bytes,
                media_type,
                DIMENSION_VISION_PROMPT,
                f"dimensions_page_{page_index}_{coords}",
            )
            if analysis:
                return [
                    f"Dimensions on page {page_index + 1}/{loader.page_count} "
                    f"in region {coords} (vision analysis):\n{analysis}",
                ]
            return [
                f"Dimensions on page {page_index + 1}/{loader.page_count} in region {coords}:",
                fallback_img,
                "Extract dimensional values, tolerances, and Ø symbols. "
                "For rotational parts, find max diameter and max height. "
                "For other parts, find width x length x height.",
            ]

        return [
            f"Dimensions on page {page_index + 1}/{loader.page_count} in region {coords}:",
            BinaryContent(data=img_bytes, media_type=media_type),
            "Extract dimensional values, tolerances, and Ø symbols. "
            "For rotational parts, find max diameter and max height. "
            "For other parts, find width x length x height.",
        ]

    half_x, half_y = 0.5, 0.5
    regions = {
        "left_half": Region(0.0, 0.0, half_x, 1.0, "left_half"),
        "right_half": Region(half_x, 0.0, 1.0, 1.0, "right_half"),
        "top_half": Region(0.0, 0.0, 1.0, half_y, "top_half"),
        "bottom_half": Region(0.0, half_y, 1.0, 1.0, "bottom_half"),
    }

    parts: list = []
    for name, r in regions.items():
        img_bytes, media_type = loader.crop_region_bytes(
            r.x_min,
            r.y_min,
            r.x_max,
            r.y_max,
            page_index=page_index,
        )

        if vision:
            analysis, fallback_img = await safe_vision_analyze(
                vision,
                img_bytes,
                media_type,
                DIMENSION_VISION_PROMPT,
                f"dimensions_page_{page_index}_{name}",
            )
            if analysis:
                parts.append(
                    f"{name} on page {page_index + 1}/{loader.page_count} (vision analysis):\n{analysis}",
                )
            else:
                parts.append(f"{name} on page {page_index + 1}/{loader.page_count}:")
                parts.append(fallback_img)
        else:
            parts.append(f"{name} on page {page_index + 1}/{loader.page_count}:")
            parts.append(BinaryContent(data=img_bytes, media_type=media_type))

    if not vision:
        parts.append(
            "Scan each half for dimension annotations. "
            "Look for: Ø (diameter), linear dimensions with values, "
            "tolerances (±values), and max/min markers."
        )
    return parts
