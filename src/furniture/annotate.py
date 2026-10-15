"""Page images with numbered dimension-mark labels, used as interpretation-agent input."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from src.furniture.schema import DimensionMark
from src.schema.extraction import BoundingBox


def draw_marks(image: Image.Image, marks: list[DimensionMark]) -> Image.Image:
    """Copy of ``image`` with a red ``D<n>`` label just above-left of every mark bbox."""
    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=max(12, canvas.height // 150))
    width, height = canvas.size
    pad = 2
    for mark in marks:
        left, top = mark.bbox.x_min * width, mark.bbox.y_min * height
        box = draw.textbbox((0, 0), mark.id, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        x0 = max(0.0, left - tw - 2 * pad)
        y0 = max(0.0, top - th - 2 * pad)
        draw.rounded_rectangle((x0, y0, x0 + tw + 2 * pad, y0 + th + 2 * pad), radius=3, fill=(220, 30, 30))
        draw.text((x0 + pad - box[0], y0 + pad - box[1]), mark.id, fill=(255, 255, 255), font=font)
    return canvas


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def render_marked_page(image: Image.Image, marks: list[DimensionMark]) -> bytes:
    """PNG of the whole page with mark labels."""
    return _png(draw_marks(image, marks))


def render_marked_region(
    image: Image.Image, marks: list[DimensionMark], region: BoundingBox, max_px: int = 2000
) -> bytes:
    """PNG of ``region`` (normalized page box) with mark labels, at most ``max_px`` on its longer side."""
    width, height = image.size
    crop = draw_marks(image, marks).crop(
        (
            round(region.x_min * width),
            round(region.y_min * height),
            round(region.x_max * width),
            round(region.y_max * height),
        )
    )
    crop.thumbnail((max_px, max_px))
    return _png(crop)
