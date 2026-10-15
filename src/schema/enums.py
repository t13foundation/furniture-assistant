from __future__ import annotations

from enum import StrEnum


class DrawingType(StrEnum):
    """Type of document found in a furniture project package."""

    ELEVATION = "ELEVATION"  # furniture build-in / elevation (zabudowa meblowa, widok)
    FLOOR_PLAN = "FLOOR_PLAN"  # floor plan (rzut posadzek)
    WALL_PLAN = "WALL_PLAN"  # wall plan (rzut ścian)
    SECTION = "SECTION"  # cross-section (przekrój)
    CEILING_PLAN = "CEILING_PLAN"  # ceiling plan (rzut sufitu)
    ELECTRICAL = "ELECTRICAL"  # electrical plan (elektryka)
    COST_SHEET = "COST_SHEET"  # cost / quote spreadsheet (koszty, wycena)
    CAD = "CAD"  # native CAD drawing (DWG/P3D)
    IMAGE = "IMAGE"  # render / scan / photo (png, jpg)
    OTHER = "OTHER"


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# Order matters: most specific patterns first to avoid substring false positives.
# Checked case-insensitively against the full filename.
DRAWING_TYPE_PATTERNS: list[tuple[DrawingType, list[str]]] = [
    (DrawingType.COST_SHEET, ["KOSZTY", "WYCENA", "KOSZTA"]),
    (DrawingType.ELEVATION, ["ZABUDOWY MEBLOWE", "ZABUDOWY-MEBLOWE", "WIDOKI", "ZABUDOWA"]),
    (DrawingType.FLOOR_PLAN, ["RZUT_POSADZEK", "RZUT POSADZEK", "POSADZKI"]),
    (DrawingType.WALL_PLAN, ["RZUT_SCIAN", "RZUT ŚCIAN", "RZUT SCIAN", "ŚCIAN"]),
    (DrawingType.SECTION, ["PRZEKROJ", "PRZEKRÓJ"]),
    (DrawingType.CEILING_PLAN, ["RZUT_SUFI", "SUFITOW"]),
    (DrawingType.ELECTRICAL, ["ELEKTRYKA", "ELEKTRYCZ"]),
    (DrawingType.IMAGE, ["SZAFA", "RYSUNEK"]),
]


def classify_drawing(filename: str) -> DrawingType:
    """Classify a furniture project document by its filename.

    Falls back to checking the file extension for image files.
    """
    lower = filename.lower()
    if lower.endswith((".dwg", ".p3d")):
        return DrawingType.CAD

    upper = filename.upper()
    for dtype, patterns in DRAWING_TYPE_PATTERNS:
        if any(p in upper for p in patterns):
            return dtype

    # Fallback: image extensions
    if lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        return DrawingType.IMAGE

    return DrawingType.OTHER
