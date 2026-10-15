"""Rule-based carcass builder: furniture module layout → positioned panel solids → cut list.

Coordinates in mm: x right, y up, z toward the viewer; origin at floor level, left side,
rear face of the module. Every panel is an axis-aligned box (``position_mm`` = min corner).
Construction is overlay: fronts sit in front of the carcass (z from depth to depth + front
thickness) and cover the carcass edges, separated by ``front_gap_mm``.
"""

from __future__ import annotations

from src.furniture.hardware import derive_hardware
from src.furniture.processes import derive_processes
from src.furniture.rules import ConstructionRules
from src.furniture.schema import Cell, FurnitureModule, ModuleModel, PanelSolid, SolidRole
from src.schema.extraction import EdgeBanding, Panel

_DEFAULT_EDGE = "obrzeże"
_SHELF_SIDE_CLEARANCE_MM = 2
_DRAWER_LIFT_MM = 5


def _cut_dims(size: tuple[float, float, float]) -> tuple[float, float, float]:
    """(length, width, thickness): thickness is the smallest extent, length the larger of the others."""
    ordered = sorted(size)
    return round(ordered[2], 1), round(ordered[1], 1), round(ordered[0], 1)


class _Builder:
    def __init__(self, module: FurnitureModule, rules: ConstructionRules) -> None:
        self.module = module
        self.rules = rules
        self.materials = {m.key: m for m in module.materials}
        self.solids: list[PanelSolid] = []

    def material_name(self, key: str | None) -> str | None:
        material = self.materials.get(key or "")
        return material.name if material else None

    def edge_name(self, key: str | None) -> str:
        return self.material_name(key) or key or _DEFAULT_EDGE

    def add(
        self,
        name: str,
        role: SolidRole,
        position: tuple[float, float, float],
        size: tuple[float, float, float],
        *,
        material_key: str | None,
        edges: EdgeBanding | None = None,
        grain: str | None = None,
        cell_id: str | None = None,
    ) -> None:
        if min(size) <= 0:
            return
        length, width, thickness = _cut_dims(size)
        self.solids.append(
            PanelSolid(
                part_id=f"{self.module.module_id}-{len(self.solids) + 1:02d}",
                name=name,
                role=role,
                cell_id=cell_id,
                material_key=material_key,
                position_mm=tuple(round(v, 1) for v in position),  # type: ignore[arg-type]
                size_mm=tuple(round(v, 1) for v in size),  # type: ignore[arg-type]
                length_mm=length,
                width_mm=width,
                thickness_mm=thickness,
                edge_banding=edges or EdgeBanding(),
                grain_direction=grain,
            )
        )

    # Edge-banding convention: EdgeBanding has no "front edge" field, so the visible front
    # edge of a carcass part is stored as ``left`` for vertical parts (sides, vertical
    # dividers) and as ``top`` for horizontal parts (top/bottom, shelves, horizontal dividers).
    def vertical_edges(self) -> EdgeBanding:
        return EdgeBanding(left=self.edge_name(self.module.carcass_material_key))

    def horizontal_edges(self) -> EdgeBanding:
        return EdgeBanding(top=self.edge_name(self.module.carcass_material_key))

    def front_edges(self, key: str | None) -> EdgeBanding:
        name = self.edge_name(key)
        return EdgeBanding(top=name, right=name, bottom=name, left=name)

    def build(self) -> list[PanelSolid]:
        m, r = self.module, self.rules
        if m.kind == "wall_cladding":
            return self.build_cladding()
        W, H, D, P = m.width_mm, m.height_mm, m.depth_mm, m.plinth_mm
        t, back = r.carcass_thickness_mm, r.back_thickness_mm
        Hc = H - P
        carcass = m.carcass_material_key

        self.add("Bok", "carcass", (0, P, 0), (t, Hc, D), material_key=carcass, edges=self.vertical_edges(), grain="L")
        self.add(
            "Bok", "carcass", (W - t, P, 0), (t, Hc, D), material_key=carcass, edges=self.vertical_edges(), grain="L"
        )
        self.add(
            "Wieniec górny",
            "carcass",
            (t, P + Hc - t, 0),
            (W - 2 * t, t, D),
            material_key=carcass,
            edges=self.horizontal_edges(),
        )
        self.add(
            "Wieniec dolny",
            "carcass",
            (t, P, 0),
            (W - 2 * t, t, D),
            material_key=carcass,
            edges=self.horizontal_edges(),
        )
        self.add("Plecy", "back", (t, P + t, 0), (W - 2 * t, Hc - 2 * t, back), material_key=carcass)
        self.add("Cokół", "plinth", (0, 0, D - r.plinth_recess_mm - t), (W, P, t), material_key=carcass)

        columns = m.columns
        # Geometry follows the module outline: when columns/rows do not add up (already reported
        # as COLUMNS_NOT_SUM / ROWS_NOT_SUM) they are scaled proportionally to fit it.
        col_total = sum(c.width_mm for c in columns)
        col_scale = W / col_total if col_total > 0 else 1.0
        x = 0.0
        for ci, column in enumerate(columns):
            cw = column.width_mm * col_scale
            x_start, x_end = x, x + cw
            x = x_end
            first, last = ci == 0, ci == len(columns) - 1
            if not last:
                self.add(
                    "Przegroda pionowa",
                    "divider",
                    (x_end - t / 2, P + t, back),
                    (t, Hc - 2 * t, D - back),
                    material_key=carcass,
                    edges=self.vertical_edges(),
                )
            x0 = x_start + (t if first else t / 2)
            x1 = x_end - (t if last else t / 2)
            self.build_column(column.cells, cw, x_start, x0, x1 - x0)

        return self.solids

    def _cladding_thickness(self, key: str | None) -> float:
        """The legend's board thickness when it names one, else the construction rule."""
        for material in self.module.materials:
            if material.key == key and material.thickness_mm:
                return material.thickness_mm
        return self.rules.cladding_thickness_mm

    def build_cladding(self) -> list[PanelSolid]:
        m, r = self.module, self.rules
        total_width = sum(column.width_mm for column in m.columns)
        if total_width <= 0:
            return self.solids
        x = 0.0
        for column in m.columns:
            width = column.width_mm * m.width_mm / total_width
            total_height = sum(cell.height_mm for cell in column.cells)
            y = m.height_mm
            for cell in column.cells:
                height = cell.height_mm * m.height_mm / total_height if total_height > 0 else 0
                y -= height
                key = cell.material_key or m.front_material_key
                self.add(
                    "Panel okładziny",
                    "front",
                    (x, y, 0),
                    (width, height, self._cladding_thickness(key)),
                    material_key=key,
                    edges=self.front_edges(key),
                    grain="L",
                    cell_id=cell.id,
                )
            x += width
        spacing = max(r.mounting_batten_spacing_mm, 1)
        thickness = r.mounting_batten_thickness_mm
        # Battens stay inside the cladding height (no batten poking out at the very top).
        for y in range(0, int(m.height_mm) - int(thickness) + 1, int(spacing)):
            self.add(
                "Listwa montażowa",
                "carcass",
                (0, float(y), -r.mounting_batten_depth_mm),
                (m.width_mm, thickness, r.mounting_batten_depth_mm),
                material_key=m.carcass_material_key,
            )
        return self.solids

    def build_column(self, cells: list[Cell], cw: float, x_start: float, x0: float, iw: float) -> None:
        m, r = self.module, self.rules
        D, P = m.depth_mm, m.plinth_mm
        t, back = r.carcass_thickness_mm, r.back_thickness_mm
        Hc = m.height_mm - P
        row_total = sum(c.height_mm for c in cells)
        row_scale = Hc / row_total if row_total > 0 else 1.0

        y_top = P + Hc
        for ri, cell in enumerate(cells):
            ch = cell.height_mm * row_scale
            y_bot = y_top - ch
            top_row, bottom_row = ri == 0, ri == len(cells) - 1
            if not bottom_row:
                self.add(
                    "Przegroda pozioma",
                    "divider",
                    (x0, y_bot - t / 2, back),
                    (iw, t, D - back),
                    material_key=m.carcass_material_key,
                    edges=self.horizontal_edges(),
                    cell_id=cell.id,
                )
            lo = y_bot + (t if bottom_row else t / 2)
            hi = y_top - (t if top_row else t / 2)
            self.build_cell(cell, cw, ch, x_start, y_bot, x0, iw, lo, hi)
            y_top = y_bot

    def build_cell(
        self, cell: Cell, cw: float, ch: float, x_start: float, y_bot: float, x0: float, iw: float, lo: float, hi: float
    ) -> None:
        m, r = self.module, self.rules
        D = m.depth_mm
        t, back, g, ft = r.carcass_thickness_mm, r.back_thickness_mm, r.front_gap_mm, r.front_thickness_mm

        for k in range(1, cell.shelves + 1):
            y = lo + k * (hi - lo) / (cell.shelves + 1) - t / 2
            self.add(
                "Półka",
                "shelf",
                (x0 + _SHELF_SIDE_CLEARANCE_MM / 2, y, back),
                (iw - _SHELF_SIDE_CLEARANCE_MM, t, D - back - r.shelf_setback_mm),
                material_key=m.carcass_material_key,
                edges=self.horizontal_edges(),
                cell_id=cell.id,
            )

        front_key = cell.material_key or m.front_material_key
        front = {"material_key": front_key, "edges": self.front_edges(front_key), "grain": "L", "cell_id": cell.id}
        if cell.front in ("door_left", "door_right", "panel_fixed"):
            self.add("Front", "front", (x_start + g / 2, y_bot + g / 2, D), (cw - g, ch - g, ft), **front)
        elif cell.front == "door_double":
            half = cw / 2
            for x in (x_start, x_start + half):
                self.add("Front", "front", (x + g / 2, y_bot + g / 2, D), (half - g, ch - g, ft), **front)
        elif cell.front == "drawer":
            self.add("Front szuflady", "front", (x_start + g / 2, y_bot + g / 2, D), (cw - g, ch - g, ft), **front)
            self.build_drawer_box(cell, x0, iw, ch, lo, hi)

    def build_drawer_box(self, cell: Cell, x0: float, iw: float, ch: float, lo: float, hi: float) -> None:
        m, r = self.module, self.rules
        bt, bottom = r.drawer_box_thickness_mm, r.drawer_box_bottom_mm
        bw = iw - 2 * r.drawer_runner_clearance_mm
        bh = min(ch - r.drawer_box_height_reduction_mm, hi - lo - bottom - 2 * _DRAWER_LIFT_MM)
        bd = m.depth_mm - r.drawer_box_depth_reduction_mm
        if bw <= 2 * bt or bh <= 0 or bd <= 2 * bt:
            return
        x, y, z = x0 + r.drawer_runner_clearance_mm, lo + _DRAWER_LIFT_MM, m.depth_mm - bd
        parts = [
            ("Dno szuflady", (x, y, z), (bw, bottom, bd)),
            ("Bok szuflady", (x, y + bottom, z), (bt, bh, bd)),
            ("Bok szuflady", (x + bw - bt, y + bottom, z), (bt, bh, bd)),
            ("Tył szuflady", (x + bt, y + bottom, z), (bw - 2 * bt, bh, bt)),
            ("Przód szuflady", (x + bt, y + bottom, z + bd - bt), (bw - 2 * bt, bh, bt)),
        ]
        for name, position, size in parts:
            self.add(name, "drawer_box", position, size, material_key=m.carcass_material_key, cell_id=cell.id)


def cut_list(module: FurnitureModule, solids: list[PanelSolid]) -> list[Panel]:
    """Group identical solids into quantity rows (first solid's part_id; all ids in ``notes``)."""
    materials = {m.key: m for m in module.materials}
    groups: dict[tuple, list[PanelSolid]] = {}
    for solid in solids:
        e = solid.edge_banding
        key = (
            solid.name,
            solid.length_mm,
            solid.width_mm,
            solid.thickness_mm,
            solid.material_key,
            (e.top, e.right, e.bottom, e.left),
            solid.grain_direction,
        )
        groups.setdefault(key, []).append(solid)
    rows: list[Panel] = []
    for members in groups.values():
        first = members[0]
        material = materials.get(first.material_key or "")
        rows.append(
            Panel(
                part_id=first.part_id,
                name=first.name,
                length_mm=first.length_mm,
                width_mm=first.width_mm,
                thickness_mm=first.thickness_mm,
                board_type=material.name if material else None,
                material_code=first.material_key,
                edge_banding=first.edge_banding,
                grain_direction=first.grain_direction,
                qty=len(members),
                confidence=module.confidence,
                notes=",".join(s.part_id for s in members),
            )
        )
    return rows


def build_module(module: FurnitureModule, rules: ConstructionRules) -> ModuleModel:
    if module.status == "incomplete" or module.width_mm <= 0 or module.height_mm <= 0:
        return ModuleModel(module=module)
    solids = _Builder(module, rules).build()
    rows = cut_list(module, solids)
    hardware = derive_hardware(module, solids, rules)
    processes = derive_processes(module, solids, rows, hardware, rules)
    return ModuleModel(module=module, solids=solids, cut_list=rows, hardware=hardware, processes=processes)
