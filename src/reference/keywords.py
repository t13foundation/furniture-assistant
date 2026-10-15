"""Furniture domain keyword dictionaries (PL/EN/DE).

Used by the text-search tool and validation to classify found text into
panel, hardware, edge-banding, and board-type categories.
"""

from __future__ import annotations

PANEL_KEYWORDS: dict[str, list[str]] = {
    "PL": [
        "blat",
        "bok",
        "front",
        "plecy",
        "dno",
        "szczyt",
        "półka",
        "polka",
        "listwa",
        "korpus",
        "drzwi",
        "szuflada",
        "element",
        "płyta",
    ],
    "EN": [
        "top",
        "side",
        "front",
        "back",
        "bottom",
        "shelf",
        "panel",
        "drawer",
        "door",
        "carcass",
        "element",
        "board",
        "plinth",
    ],
    "DE": [
        "Platte",
        "Seite",
        "Front",
        "Rückwand",
        "Boden",
        "Regal",
        "Schublade",
        "Tür",
        "Korpus",
        "Element",
        "Deckel",
    ],
}

HARDWARE_KEYWORDS: dict[str, list[str]] = {
    "PL": [
        "zawias",
        "prowadnica",
        "uchwyt",
        "gałka",
        "noga",
        "zamek",
        "wysuwacz",
        "kątownik",
        "samozamykacz",
        "okucie",
        "stopka",
    ],
    "EN": [
        "hinge",
        "runner",
        "slide",
        "handle",
        "knob",
        "leg",
        "lock",
        "pull-out",
        "bracket",
        "soft-close",
        "fitting",
        "foot",
    ],
    "DE": [
        "Scharnier",
        "Führung",
        "Griff",
        "Knauf",
        "Fuß",
        "Schloss",
        "Auszug",
        "Winkel",
        "Beschlag",
    ],
}

EDGE_BANDING_KEYWORDS: dict[str, list[str]] = {
    "PL": ["okleina", "obrzeże", "obsadka", "ABS", "PCV", "PVC", "akryl"],
    "EN": ["edge band", "edging", "tape", "ABS", "PVC", "acrylic"],
    "DE": ["Kante", "Kantenband", "Umleimer", "ABS", "PVC"],
}

BOARD_KEYWORDS: dict[str, list[str]] = {
    "PL": ["płyta wiórowa", "płyta meblowa", "płyta MDF", "sklejka", "płyta gipsowa"],
    "EN": ["chipboard", "MDF", "HDF", "plywood", "plasterboard"],
    "DE": ["Spanplatte", "MDF-Platte", "Sperrholz", "Gipsplatte"],
}

DIMENSION_KEYWORDS: dict[str, list[str]] = {
    "PL": ["wymiar", "szerokość", "wysokość", "głębokość", "grubość", "długość"],
    "EN": ["dimension", "width", "height", "depth", "thickness", "length"],
    "DE": ["Maß", "Breite", "Höhe", "Tiefe", "Dicke", "Länge"],
}


# Flat lists for quick membership checks (all languages flattened)
ALL_PANEL_KEYWORDS_FLAT: list[str] = [kw.lower() for kws in PANEL_KEYWORDS.values() for kw in kws]
ALL_HARDWARE_KEYWORDS_FLAT: list[str] = [kw.lower() for kws in HARDWARE_KEYWORDS.values() for kw in kws]
ALL_EDGE_BANDING_KEYWORDS_FLAT: list[str] = [kw.lower() for kws in EDGE_BANDING_KEYWORDS.values() for kw in kws]
ALL_BOARD_KEYWORDS_FLAT: list[str] = [kw.lower() for kws in BOARD_KEYWORDS.values() for kw in kws]
