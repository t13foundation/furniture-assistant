"""Cross-reference validation tools for furniture materials and hardware.

IMPORTANT design principle: a failing validation must NEVER cause the agent to
drop a panel. When a board / edge / hardware string is not recognised, the tool
returns ``found: False`` together with an explicit ``keep_panel: True`` flag and
a note instructing the agent to KEEP the item with LOW confidence. The previous
behaviour (a bare ``found: False`` dead-end) caused the agent to silently delete
panels, producing empty ``panels: []`` results — see manual-quality-plan.md §7.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from src.reference.standards_db import (
    BOARD_TYPE_ALIASES,
    KNOWN_BOARD_TYPES,
    KNOWN_EDGE_MATERIALS,
    KNOWN_HARDWARE_TYPES,
)

# Shared "keep the item" instruction appended to every not-found response so the
# agent has a clear recovery path instead of dropping the data.
_KEEP_PANEL_NOTE = (
    "Not in the reference database. KEEP this item as-is with LOW confidence — "
    "do NOT drop it. The catalog is illustrative, not exhaustive."
)


def validate_board_type(board_text: str) -> dict[str, str | bool]:
    """Validate a board / panel material against known furniture board types.

    Recognition order: (1) exact substring on canonical ids, (2) Polish bare-word
    aliases (e.g. the generic "Płyta" → MFC), (3) fuzzy match, (4) not-found with
    an explicit ``keep_panel`` instruction so the agent never drops the panel.
    """
    text = board_text.strip().upper()

    # 1. Exact substring match against canonical board ids
    for board_id, description in KNOWN_BOARD_TYPES.items():
        if board_id.upper() in text or text in board_id.upper():
            return {
                "found": True,
                "board_type": board_id,
                "description": description,
                "confidence": "HIGH",
            }

    # 2. Polish bare-word / phrase aliases (handles the generic "Płyta" case).
    # Iterate longest-first so "PŁYTA WIÓROWA" matches before the bare "PŁYTA".
    for alias in sorted(BOARD_TYPE_ALIASES, key=len, reverse=True):
        canonical_id = BOARD_TYPE_ALIASES[alias]
        if alias in text:
            return {
                "found": True,
                "board_type": canonical_id,
                "description": KNOWN_BOARD_TYPES[canonical_id],
                "confidence": "MEDIUM",
                "matched_alias": alias,
                "note": f"Matched alias '{alias}' → '{canonical_id}'. Verify the exact designation on the drawing.",
            }

    # 3. Fuzzy match against canonical ids
    best_match = max(
        ((bid, fuzz.ratio(text, bid.upper())) for bid in KNOWN_BOARD_TYPES),
        key=lambda x: x[1],
    )

    if best_match[1] >= 60:
        return {
            "found": True,
            "board_type": best_match[0],
            "description": KNOWN_BOARD_TYPES[best_match[0]],
            "confidence": "MEDIUM",
            "note": "Fuzzy match — verify exact board designation",
        }

    # 4. Not found — keep the panel with LOW confidence (never drop)
    return {
        "found": False,
        "input": board_text,
        "board_type": board_text,
        "confidence": "LOW",
        "keep_panel": True,
        "note": _KEEP_PANEL_NOTE,
    }


def validate_edge_material(edge_text: str) -> dict[str, str | bool]:
    """Validate an edge-banding material against known furniture materials.

    Now includes a fuzzy fallback (previously absent) and the keep-panel
    instruction on not-found, consistent with validate_board_type.
    """
    text = edge_text.strip().upper()

    # 1. Exact substring match
    for mat_id, description in KNOWN_EDGE_MATERIALS.items():
        if mat_id.upper() in text or text in mat_id.upper():
            return {
                "found": True,
                "material": mat_id,
                "description": description,
                "confidence": "HIGH",
            }

    # 2. Fuzzy match (threshold 60, matching board_type behaviour)
    best_match = max(
        ((mid, fuzz.ratio(text, mid.upper())) for mid in KNOWN_EDGE_MATERIALS),
        key=lambda x: x[1],
    )

    if best_match[1] >= 60:
        return {
            "found": True,
            "material": best_match[0],
            "description": KNOWN_EDGE_MATERIALS[best_match[0]],
            "confidence": "MEDIUM",
            "note": "Fuzzy match — verify exact edge material",
        }

    # 3. Not found — keep the panel with LOW confidence (never drop)
    return {
        "found": False,
        "input": edge_text,
        "material": edge_text,
        "confidence": "LOW",
        "keep_panel": True,
        "note": _KEEP_PANEL_NOTE,
    }


def validate_hardware(hardware_text: str) -> dict[str, str | bool]:
    """Match a hardware / fitting description against known furniture fitting categories."""
    text = hardware_text.strip().upper()

    for hw_id, description in KNOWN_HARDWARE_TYPES.items():
        if hw_id.upper() in text or text in hw_id.upper():
            return {
                "found": True,
                "category": hw_id,
                "description": description,
                "confidence": "HIGH",
            }

    best_match = max(
        ((hw_id, fuzz.ratio(text, hw_id.upper())) for hw_id in KNOWN_HARDWARE_TYPES),
        key=lambda x: x[1],
    )

    if best_match[1] >= 50:
        return {
            "found": True,
            "category": best_match[0],
            "description": KNOWN_HARDWARE_TYPES[best_match[0]],
            "confidence": "MEDIUM",
            "note": "Fuzzy match — verify exact hardware type",
        }

    # Not found — keep the hardware item with LOW confidence (never drop)
    return {
        "found": False,
        "input": hardware_text,
        "category": hardware_text,
        "confidence": "LOW",
        "keep_panel": True,
        "note": _KEEP_PANEL_NOTE,
    }
