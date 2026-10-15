from __future__ import annotations

import math
import re
from typing import Literal

from src.furniture.rules import ConstructionRules
from src.furniture.schema import FurnitureModule, HardwareCategory, HardwareItem, PanelSolid

_PUSH_OPEN = re.compile(r"tip\s*-?\s*on|push|bezuchwyt", re.IGNORECASE)
_HANDLE = re.compile(r"(?:uchwyt|gałka|galk[aą])[^\n|;]*", re.IGNORECASE)


def _items(module: FurnitureModule, solids: list[PanelSolid], rules: ConstructionRules) -> list[HardwareItem]:
    r = rules.hardware
    texts = [run.text for run in module.texts]
    drawing_text = " | ".join(texts)
    push_open_match = _PUSH_OPEN.search(drawing_text)
    handle_index = next((i for i, text in enumerate(texts) if _HANDLE.search(text)), None)
    handle_text = texts[handle_index].strip() if handle_index is not None else ""
    nearby = texts[max(0, handle_index - 1) : handle_index + 3] if handle_index is not None else []
    handle_source = " | ".join(text.strip() for text in nearby)
    length = re.search(r"(?:dł|dl|długość)\s*[:.]?\s*(\d+(?:[,.]\d+)?)\s*(mm|cm)?", handle_source, re.I)
    handle_spec = (
        f"{float(length.group(1).replace(',', '.')) * (10 if length.group(2) and length.group(2).casefold() == 'cm' else 1):g} mm"
        if length
        else ""
    )
    result: list[HardwareItem] = []

    def add(
        key: str,
        category: HardwareCategory,
        name: str,
        qty: float,
        *,
        spec: str = "",
        unit: Literal["szt", "kpl", "mb"] = "szt",
        cell_ids: list[str] | None = None,
        source: Literal["rule", "drawing"] = "rule",
        source_text: str | None = None,
    ) -> None:
        if qty > 0:
            result.append(
                HardwareItem(
                    key=key,
                    category=category,
                    name=name,
                    spec=spec,
                    qty=qty,
                    unit=unit,
                    cell_ids=cell_ids or [],
                    source=source,
                    source_text=source_text,
                )
            )

    if module.kind == "wall_cladding":
        battens = [solid for solid in solids if solid.name == "Listwa montażowa"]
        length_m = sum(solid.size_mm[0] for solid in battens) / 1000
        add("cladding-clips", "mounting", "Klips montażowy", length_m, unit="mb")
        add("cladding-screws", "mounting", "Wkręt montażowy", length_m, unit="mb")
        return result

    drawers: list[str] = []
    shelves: dict[str, int] = {}
    door_cells: set[str] = set()
    for col in module.columns:
        for cell in col.cells:
            if cell.front in ("door_left", "door_right", "door_double"):
                door_cells.add(cell.id)
            elif cell.front == "drawer":
                drawers.append(cell.id)
            if cell.shelves:
                shelves[cell.id] = cell.shelves
    # One leaf per built door front, with its real (built) height: rows are fitted to the carcass.
    leaves = [
        (solid.cell_id or "", solid.size_mm[1])
        for solid in solids
        if solid.role == "front" and solid.name == "Front" and solid.cell_id in door_cells
    ]

    thresholds = r.hinge_height_thresholds_mm
    counts = r.hinge_counts
    hinge_cells: list[str] = []
    hinge_total = 0
    for cell_id, height in leaves:
        hinge_total += counts[
            min(next((i for i, cap in enumerate(thresholds) if height <= cap), len(counts) - 1), len(counts) - 1)
        ]
        if cell_id not in hinge_cells:
            hinge_cells.append(cell_id)
    add("hinges", "hinge", "Zawias puszkowy", hinge_total, spec="Ø35", cell_ids=hinge_cells)
    if push_open_match:
        add(
            "push-open",
            "push_open",
            "Mechanizm push-to-open",
            len(leaves) + len(drawers),
            source="drawing",
            source_text=push_open_match.group(0),
        )
    elif handle_text:
        add(
            "drawing-handle",
            "handle",
            handle_text,
            len(leaves) + len(drawers),
            spec=handle_spec,
            source="drawing",
            source_text=handle_source,
        )
    else:
        add("handles", "handle", "Uchwyt", len(leaves) + len(drawers))

    depth = max((solid.size_mm[2] for solid in solids if solid.name == "Dno szuflady"), default=0)
    # Runners only for drawer boxes the builder could fit (a too-small cell gets a front but no box).
    built_boxes = {solid.cell_id for solid in solids if solid.name == "Dno szuflady"}
    boxed_drawers = [cell_id for cell_id in drawers if cell_id in built_boxes]
    if boxed_drawers:
        box_depth = depth or max(0, module.depth_mm - rules.drawer_box_depth_reduction_mm)
        runner_length = max((length for length in r.runner_lengths_mm if length <= box_depth), default=0)
        add(
            "drawer-runners",
            "runner",
            "Prowadnica",
            len(boxed_drawers),
            spec=f"{runner_length:g} mm" if runner_length else "",
            unit="kpl",
            cell_ids=boxed_drawers,
        )
    add(
        "shelf-supports",
        "shelf_support",
        "Podpórka półki",
        sum(shelves.values()) * 4,
        cell_ids=[key for key, count in shelves.items() if count],
    )

    rail_cells = [cell for col in module.columns for cell in col.cells if "drążek" in cell.label.casefold()]
    if not rail_cells:
        rail_runs = [run for run in module.texts if "drążek" in run.text.casefold()]
        if rail_runs and any(col.cells for col in module.columns):
            total_width = sum(col.width_mm for col in module.columns) or 1
            total_height = sum(cell.height_mm for col in module.columns for cell in col.cells) or 1
            boxes = []
            x_offset = 0.0
            for col in module.columns:
                col_width = col.width_mm / total_width
                y_offset = 0.0
                for cell in col.cells:
                    height = cell.height_mm / total_height
                    boxes.append((col, cell, x_offset, x_offset + col_width, y_offset, y_offset + height))
                    y_offset += height
                x_offset += col_width
            view = module.view_bbox
            for run in rail_runs:
                x = ((run.bbox.x_min + run.bbox.x_max) / 2 - view.x_min) / max(view.x_max - view.x_min, 1e-9)
                y = ((run.bbox.y_min + run.bbox.y_max) / 2 - view.y_min) / max(view.y_max - view.y_min, 1e-9)
                box = min(
                    boxes,
                    key=lambda b: (x - (b[2] + b[3]) / 2) ** 2 + (y - (b[4] + b[5]) / 2) ** 2,
                )
                if box[1] not in rail_cells:
                    rail_cells.append(box[1])
    for cell in rail_cells:
        col = next((col for col in module.columns if cell in col.cells), None)
        if col and col.width_mm > 0:
            add(f"rail-{cell.id}", "hanging_rail", "Drążek", col.width_mm / 1000, unit="mb", cell_ids=[cell.id])
            add(f"rail-holders-{cell.id}", "rail_holder", "Uchwyt drążka", 2, cell_ids=[cell.id])
    if module.width_mm > 0:
        legs = (
            r.leg_base_qty + max(0, math.ceil((module.width_mm - 1200) / r.leg_extra_per_started_mm)) * r.leg_extra_qty
        )
        add("adjustable-legs", "leg", "Noga regulowana", legs)
    joints = (2 + max(0, len(module.columns) - 1)) * 2
    connector_qty = math.ceil(joints * r.connector_per_joint * module.depth_mm / r.connector_depth_scale_mm)
    add("confirmat", "connector", "Konfirmat", connector_qty, spec="7x50 mm")
    perimeter = 2 * (
        max(0, module.width_mm - 2 * rules.carcass_thickness_mm)
        + max(0, module.height_mm - module.plinth_mm - 2 * rules.carcass_thickness_mm)
    )
    add("back-fixings", "back_fixing", "Wkręt do pleców", math.ceil(perimeter / r.back_fixing_perimeter_step_mm))
    if re.search(r"\bLED\b", drawing_text, re.IGNORECASE):
        add("led-strip", "lighting", "Taśma LED", module.width_mm / 1000, unit="mb")
    disabled = set(r.disabled_categories_by_kind.get(module.kind, ()))
    return [item for item in result if item.category not in disabled]


def derive_hardware(module: FurnitureModule, solids: list[PanelSolid], rules: ConstructionRules) -> list[HardwareItem]:
    """Estimate fittings from module construction and drawing text."""
    return _items(module, solids, rules)
