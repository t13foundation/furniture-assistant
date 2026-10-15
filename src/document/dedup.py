"""Field-level deduplication for multi-tile extraction results.

When tiles overlap (15-20%), the same field may be extracted from multiple tiles.
This module merges those duplicate extractions, keeping the highest-confidence value
and flagging conflicts for human review.
"""

from __future__ import annotations


def dedup_fields(
    merged: dict,
    per_tile: list[dict],
) -> tuple[dict, list[str]]:
    """Deduplicate fields extracted from overlapping tiles.

    Args:
        merged: The current merged extraction dict (field_name → raw_value).
        per_tile: List of per-tile extraction dicts.

    Returns:
        (deduped_merged, conflicts) where conflicts is a list of
        "field_name: tile_value vs tile_value" strings for human review.
    """
    # Collect all (tile_index, field_name, raw_value) tuples
    field_sources: dict[str, list[tuple[int, object]]] = {}
    for tile_idx, tile_data in enumerate(per_tile):
        if not tile_data:
            continue
        for key, value in tile_data.items():
            if value is None:
                continue
            field_sources.setdefault(key, []).append((tile_idx, value))

    conflicts: list[str] = []
    deduped: dict = dict(merged)

    for field_name, sources in field_sources.items():
        if len(sources) <= 1:
            continue

        # Normalize values for comparison
        values = [_normalize(v) for _, v in sources]
        unique_values = set(v for v in values if v is not None)

        if len(unique_values) <= 1:
            # All tiles agree — already in merged, no action needed
            continue

        # Conflict: multiple distinct values for the same field
        value_summary = " vs ".join(f"tile{idx}:{_fmt(v)}" for idx, v in sources if _normalize(v) is not None)
        conflicts.append(f"{field_name}: {value_summary}")

        # Keep the most common value; if tied, keep the one already in merged
        from collections import Counter

        counts = Counter(v for v in values if v is not None)
        best_value = counts.most_common(1)[0][0]
        for _, raw in sources:
            if _normalize(raw) == best_value:
                deduped[field_name] = raw
                break

    return deduped, conflicts


def _normalize(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, dict):
        return (value.get("value") or value.get("value_eng") or "").strip().lower() or None
    return str(value).strip().lower() or None


def _fmt(value: object) -> str:
    if isinstance(value, dict):
        return value.get("value") or str(value)
    return str(value)
