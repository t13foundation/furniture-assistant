from __future__ import annotations

from collections import defaultdict
from typing import Literal

from src.furniture.rules import ConstructionRules
from src.furniture.schema import FurnitureModule, HardwareItem, PanelSolid, ProcessKind, ProcessLine
from src.schema.extraction import Panel


def derive_processes(
    module: FurnitureModule,
    solids: list[PanelSolid],
    cut_list: list[Panel],
    hardware: list[HardwareItem],
    rules: ConstructionRules,
) -> list[ProcessLine]:
    """Estimate shop and installation time using the configured standards."""
    standards = rules.processes
    lines: list[ProcessLine] = []

    def add(
        process: ProcessKind,
        operation: str,
        qty: float,
        unit: Literal["szt", "mb", "m2", "kpl"],
        minutes: float,
        material: str | None = None,
        part_ids: list[str] | None = None,
    ) -> None:
        if qty > 0:
            lines.append(
                ProcessLine(
                    process=process,
                    operation=operation,
                    qty=round(qty, 3),
                    unit=unit,
                    minutes=round(minutes, 2),
                    material=material,
                    part_ids=part_ids or [],
                )
            )

    if module.kind == "wall_cladding":
        area = (
            sum(solid.size_mm[0] * solid.size_mm[1] for solid in solids if solid.name == "Panel okładziny") / 1_000_000
        )
        add(
            "installation",
            "Montaż okładziny ściennej",
            area,
            "m2",
            area * standards.wall_cladding_installation_per_m2_minutes,
        )
        return lines

    groups: dict[tuple[str | None, float], list[Panel]] = defaultdict(list)
    for panel in cut_list:
        groups[(panel.board_type, panel.thickness_mm or 0)].append(panel)
    for (material, thickness), panels in groups.items():
        parts = sum(panel.qty for panel in panels)
        metres = sum(((panel.length_mm or 0) + (panel.width_mm or 0)) * 2 * panel.qty for panel in panels) / 1000
        add(
            "cutting",
            f"Rozkrój {thickness:g} mm",
            parts,
            "szt",
            parts * standards.cut_part_minutes + metres * standards.cut_per_metre_minutes,
            material,
            [panel.part_id for panel in panels if panel.part_id],
        )

    # build.py stores the visible front edge as left for vertical pieces and top for horizontal
    # pieces; top/bottom run along cut-list length, left/right along width. Fronts have all edges.
    edges: dict[str, float] = defaultdict(float)
    for solid in solids:
        edge = solid.edge_banding
        for name in (edge.top, edge.bottom):
            if name:
                edges[name] += solid.length_mm / 1000
        for name in (edge.left, edge.right):
            if name:
                edges[name] += solid.width_mm / 1000
    for material, length in edges.items():
        add(
            "edge_banding",
            f"Oklejanie — {material}",
            length,
            "mb",
            length * standards.edge_band_per_metre_minutes,
            material,
        )

    qty = defaultdict(float)
    for item in hardware:
        qty[item.category] += item.qty
    # (operation, holes, minutes per hole)
    drilling = (
        ("Otwory pod zawiasy", qty["hinge"], standards.hinge_cup_minutes),
        ("Otwory pod konfirmaty", qty["connector"] * 2, standards.confirmat_hole_minutes),
        ("Otwory pod podpórki półek", qty["shelf_support"], standards.shelf_support_hole_minutes),
        ("Otwory pod prowadnice", qty["runner"] * 4, standards.runner_screw_minutes),
    )
    for operation, holes, per_hole in drilling:
        add("drilling", operation, holes, "szt", holes * per_hole)

    drawers = sum(1 for solid in solids if solid.name == "Dno szuflady")
    back_count = sum(1 for solid in solids if solid.name == "Plecy")
    carcass_count = int(any(solid.role == "carcass" for solid in solids))
    add("assembly", "Montaż korpusu", carcass_count, "szt", carcass_count * standards.carcass_assembly_minutes)
    add("assembly", "Montaż szuflad", drawers, "szt", drawers * standards.drawer_assembly_minutes)
    add("assembly", "Montaż pleców", back_count, "szt", back_count * standards.back_assembly_minutes)

    mounting = (
        ("Montaż zawiasów", qty["hinge"], standards.hinge_mount_minutes),
        ("Regulacja frontów", sum(solid.role == "front" for solid in solids), standards.front_adjustment_minutes),
        ("Montaż prowadnic", qty["runner"], standards.runner_mount_minutes),
        ("Montaż uchwytów / push-open", qty["handle"] + qty["push_open"], standards.handle_mount_minutes),
        # One rail per hanging_rail item (its quantity is the rail length in mb).
        (
            "Montaż drążków",
            sum(1 for item in hardware if item.category == "hanging_rail"),
            standards.rail_mount_minutes,
        ),
        ("Montaż nóg", qty["leg"], standards.leg_mount_minutes),
    )
    for operation, count, per_item in mounting:
        unit = "kpl" if operation == "Montaż prowadnic" else "szt"
        add("hardware_mounting", operation, count, unit, count * per_item)
    front_area = sum(solid.size_mm[0] * solid.size_mm[1] for solid in solids if solid.role == "front") / 1_000_000
    install = (
        standards.installation_per_module_minutes
        + front_area * standards.installation_per_m2_minutes
        + module.width_mm / 1000 * standards.installation_per_metre_minutes
    )
    add("installation", "Montaż na miejscu", 1, "szt", install)
    return lines
