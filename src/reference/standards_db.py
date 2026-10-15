"""Furniture material and hardware reference catalogs.

Used by cross-reference tools to validate board types, edge banding materials,
and hardware items extracted from furniture drawings.
"""

from __future__ import annotations

# ── Board types (płyty meblowe) ────────────────────────────────────
# Keyed by common abbreviation; value is a human-readable description.
KNOWN_BOARD_TYPES: dict[str, str] = {
    "MFC": "Melamine-faced chipboard (płyta wiórowa melaminowana)",
    "MDF": "Medium-density fibreboard (płyta MDF)",
    "HDF": "High-density fibreboard (płyta HDF)",
    "OSB": "Oriented strand board (płyta OSB)",
    "PLYWOOD": "Plywood (sklejka)",
    "MEBLOWA": "Furniture board (płyta meblowa lite)",
    "WIOROWA": "Chipboard (płyta wiórowa)",
    "GIPSOWO": "Plasterboard (płyta gipsowo-kartonowa / GK)",
    "FULL": "Solid wood board (płyta lite drewno)",
}

# ── Bare-word Polish aliases (oklejenia / płyty) ──────────────────
# Models frequently extract the generic Polish word "Płyta" (board) without a
# specific designation. These aliases map common bare words and partial phrases
# to a canonical board type so the cross-reference tool recognises them instead
# of returning a dead-end ``found: False`` (which previously caused the agent to
# drop the panel entirely — see manual-quality-plan.md §7).
#
# Keys are matched case-insensitively as substrings of the input text.
BOARD_TYPE_ALIASES: dict[str, str] = {
    # The bare word "płyta" / "plyta" — default to MFC (the most common
    # furniture board in European cabinetmaking).
    "PŁYTA": "MFC",
    "PLYTA": "MFC",
    # Full Polish phrases (with/without diacritics) → canonical board id
    "PŁYTA WIÓROWA": "WIOROWA",
    "PLYTA WIOROWA": "WIOROWA",
    "PŁYTA MDF": "MDF",
    "PLYTA MDF": "MDF",
    "PŁYTA HDF": "HDF",
    "PŁYTA OSB": "OSB",
    "PŁYTA MEBLOWA": "MEBLOWA",
    "PLYTA MEBLOWA": "MEBLOWA",
    "PŁYTA GIPSOWO": "GIPSOWO",
    "PŁYTA LITE": "FULL",
    "SKLEJKA": "PLYWOOD",
}

# Common board thicknesses in mm (European furniture standard)
KNOWN_BOARD_THICKNESSES: list[int] = [8, 10, 12, 15, 16, 18, 19, 22, 25, 28, 30, 36, 38]

# Common board-type tokens found on drawings (for keyword matching)
BOARD_TYPE_KEYWORDS: list[str] = [
    "mfc",
    "mdf",
    "hdf",
    "osb",
    "sklejka",
    "plywood",
    "płyta wiórowa",
    "plyta wiorowa",
    "płyta meblowa",
    "plyta meblowa",
    "g-k",
    "gk",
    "gipsowo-kartonowa",
    "lite drewno",
]

# ── Edge banding materials (okleiny) ──────────────────────────────
KNOWN_EDGE_MATERIALS: dict[str, str] = {
    "ABS": "ABS edge banding (okleina ABS)",
    "PVC": "PVC edge banding (okleina PCV)",
    "AKRYL": "Acrylic edge banding (okleina akrylowa)",
    "MELAMINA": "Melamine edge banding (okleina melaminowa)",
    "DREWNO": "Real wood veneer edge banding (okleina drewniana)",
    "POLIPROPYLEN": "Polypropylene edge banding (okleina PP)",
}

# ── Hardware / fitting categories (okucia) ────────────────────────
KNOWN_HARDWARE_TYPES: dict[str, str] = {
    "ZAWIAS": "Hinge (zawias)",
    "PROWADNICA": "Runner / slide (prowadnica)",
    "UCHWYT": "Handle / knob (uchwyt / gałka)",
    "NOGA": "Leg / adjustable foot (noga / stopa regulacyjna)",
    "ZAMEK": "Lock (zamek)",
    "WYSUWACZ": "Pull-out mechanism (wysuwacz)",
    "KĄTOWNIK": "Bracket / angle (kątownik)",
    "ZAWIAS_PIAN": "Piano hinge (zawias pianinowy)",
    "SAMOZAMYKACZ": "Soft-close / self-closing mechanism (samozamykacz)",
    "KRZYŻAK": "Cross brace / connector (krzyżak)",
}

# Common hardware brand codes found on drawings
HARDWARE_BRANDS: list[str] = ["blum", "hettich", "hafele", "grass", "salice", "ferrari", "epk", "gtv"]
