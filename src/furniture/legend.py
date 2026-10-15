from __future__ import annotations

import re
import unicodedata

from src.furniture.schema import MaterialKind, MaterialSpec, TextRun

_KIND_PATTERNS: tuple[tuple[MaterialKind, re.Pattern[str]], ...] = (
    ("laminated_board", re.compile(r"laminat|płyta\s+lamin|melamin|egger|kronospan", re.I)),
    ("veneer", re.compile(r"fornir|veneer", re.I)),
    ("lacquered_mdf", re.compile(r"mdf|lakier|lacquer", re.I)),
    ("hpl", re.compile(r"\bhpl\b|fenix", re.I)),
    ("mirror", re.compile(r"lustro|mirror", re.I)),
    ("glass", re.compile(r"szkło|szklo|glass", re.I)),
    ("stone", re.compile(r"corian|grantex|konglomerat|kamień|kamien|stone", re.I)),
    ("upholstery", re.compile(r"tapicer|tkanin|upholster|fabric", re.I)),
    ("metal", re.compile(r"stal|metal", re.I)),
)
_MATERIAL_WORDS = re.compile(
    r"laminat|płyta|plyta|mdf|lakier|fornir|veneer|\bhpl\b|fenix|lustro|mirror|szkło|szklo|glass|"
    r"corian|grantex|konglomerat|kamień|kamien|stone|tapicer|tkanin|upholster|fabric|stal|metal|\bral\b|"
    r"egger|kronospan",
    re.I,
)
_LEGEND_CONTEXT = re.compile(
    r"materiał|material|laminat|płyta|plyta|mdf|lakier|fornir|veneer|\bhpl\b|fenix|"
    r"lustro|mirror|szkło|szklo|glass|corian|grantex|konglomerat|kamień|kamien|"
    r"tapicer|tkanin|stal|metal|\bral\b|egger|kronospan",
    re.I,
)


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _kind(name: str) -> MaterialKind:
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(name):
            return kind
    return "other"


def legend_materials(texts: list[TextRun]) -> list[MaterialSpec]:
    """Parse material specifications from PDF text runs, retaining source wording and first-seen order."""
    lines = [run.text.strip(" \t:;,.\u2022") for run in texts if run.text.strip()]
    context = " ".join(lines)
    if not _LEGEND_CONTEXT.search(context):
        return []

    # Legend headings, labels and the material name often occupy adjacent PDF text runs.
    # Keep meaningful full specifications and omit generic labels such as "Laminat drewnopodobny".
    materials: list[MaterialSpec] = []
    seen: set[str] = set()
    for line in lines:
        if not _MATERIAL_WORDS.search(line) or len(line) > 240:
            continue
        name = re.sub(r"^\d+[.)]?\s*", "", line).strip(" \t:;,.\u2022")
        normalized = _normalize(name)
        if not normalized or normalized in seen:
            continue
        # A manufacturer/decor/RAL or a meaningful material description is useful as a candidate;
        # don't turn section headings or the generic word "materiał" into materials.
        if len(normalized) < 5 or normalized in {"materiały zabudów meblowych", "materialy zabudow meblowych"}:
            continue
        seen.add(normalized)
        materials.append(MaterialSpec(key=f"L{len(materials) + 1}", name=name, kind=_kind(name)))
    return materials
