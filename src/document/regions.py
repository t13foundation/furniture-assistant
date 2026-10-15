from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Region:
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    label: str = ""
    purpose: str = ""

    def __post_init__(self) -> None:
        self.x_min = max(0.0, min(1.0, self.x_min))
        self.y_min = max(0.0, min(1.0, self.y_min))
        self.x_max = max(0.0, min(1.0, self.x_max))
        self.y_max = max(0.0, min(1.0, self.y_max))


@dataclass
class TitleBlockCell:
    """A single cell within a title block grid."""

    row: int
    col: int
    x_min: float  # normalized coords within the full drawing
    y_min: float
    x_max: float
    y_max: float
    label: str = ""  # detected cell label text (e.g. "Material", "Nr rys.")
    image_bytes: bytes = b""
    media_type: str = "image/png"


def normalized_to_pixel(region: Region, width: int, height: int) -> tuple[int, int, int, int]:
    return (
        int(region.x_min * width),
        int(region.y_min * height),
        int(region.x_max * width),
        int(region.y_max * height),
    )


def pixel_to_normalized(x: int, y: int, w: int, h: int, width: int, height: int) -> Region:
    return Region(
        x_min=x / width,
        y_min=y / height,
        x_max=(x + w) / width,
        y_max=(y + h) / height,
    )


# Common title block positions in technical drawings
TITLE_BLOCK_REGIONS: list[Region] = [
    Region(0.65, 0.80, 1.0, 1.0, "bottom-right", "title_block"),
    Region(0.0, 0.80, 0.35, 1.0, "bottom-left", "title_block"),
    Region(0.65, 0.0, 1.0, 0.20, "top-right", "title_block"),
]
