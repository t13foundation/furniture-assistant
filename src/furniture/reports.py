from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from src.furniture.nest import nest_cut_list
from src.furniture.rules import ConstructionRules
from src.furniture.schema import HardwareCategory, ModuleModel, ProcessKind, ProcessLine


class BoardSummaryRow(BaseModel):
    material: str | None
    thickness_mm: float
    parts: int
    area_m2: float
    sheets: int
    utilization: float
    module_ids: list[str]
    # Parts longer/wider than the stock sheet: they cannot be cut and are not in ``sheets``.
    unplaced: list[str] = Field(default_factory=list)


class EdgeSummaryRow(BaseModel):
    material: str
    length_m: float
    module_ids: list[str]
    # Length per module (allocation weights for the valuation).
    per_module: dict[str, float] = Field(default_factory=dict)


class HardwareSummaryRow(BaseModel):
    category: HardwareCategory
    name: str
    spec: str
    unit: str
    qty: float
    module_ids: list[str]
    # Quantity per module (allocation weights for the valuation).
    per_module: dict[str, float] = Field(default_factory=dict)


class MaterialSummary(BaseModel):
    boards: list[BoardSummaryRow]
    edges: list[EdgeSummaryRow]
    hardware: list[HardwareSummaryRow]
    module_count: int
    incomplete_module_ids: list[str]


class ProcessCell(BaseModel):
    # None when the module's lines for this process use different units (only minutes add up).
    qty: float | None
    unit: str | None
    minutes: float


class ProcessMatrixRow(BaseModel):
    module_id: str
    title: str
    file_name: str
    page_index: int
    status: str
    processes: dict[ProcessKind, ProcessCell] = Field(default_factory=dict)


class ProcessTotal(BaseModel):
    process: ProcessKind
    lines: int
    minutes: float


class ProcessEntry(ProcessLine):
    module_id: str


class ProcessSummary(BaseModel):
    rows: list[ProcessMatrixRow]
    totals: list[ProcessTotal]
    total_minutes: float
    lines: list[ProcessEntry]


def material_summary(models: list[ModuleModel], rules: ConstructionRules) -> MaterialSummary:
    boards: dict[tuple[str | None, float], dict] = {}
    edges: dict[str, dict] = {}
    hardware: dict[tuple, dict] = {}
    for model in models:
        module_id = model.module.module_id
        for panel in model.cut_list:
            key = (panel.board_type, panel.thickness_mm or 0)
            row = boards.setdefault(key, {"parts": 0, "area": 0.0, "module_ids": set()})
            row["parts"] += panel.qty
            row["area"] += (panel.length_mm or 0) * (panel.width_mm or 0) * panel.qty / 1_000_000
            row["module_ids"].add(module_id)
        for solid in model.solids:
            edge = solid.edge_banding
            for material in (edge.top, edge.bottom):
                if material:
                    row = edges.setdefault(material, {"length": 0.0, "per_module": defaultdict(float)})
                    row["length"] += solid.length_mm / 1000
                    row["per_module"][module_id] += solid.length_mm / 1000
            for material in (edge.left, edge.right):
                if material:
                    row = edges.setdefault(material, {"length": 0.0, "per_module": defaultdict(float)})
                    row["length"] += solid.width_mm / 1000
                    row["per_module"][module_id] += solid.width_mm / 1000
        for item in model.hardware:
            key = item.category, item.name, item.spec, item.unit
            row = hardware.setdefault(key, {"qty": 0.0, "per_module": defaultdict(float)})
            row["qty"] += item.qty
            row["per_module"][module_id] += item.qty
    board_rows = []
    for (material, thickness), values in boards.items():
        panels = [
            panel
            for model in models
            for panel in model.cut_list
            if panel.board_type == material and (panel.thickness_mm or 0) == thickness
        ]
        sheets = nest_cut_list(panels, rules)
        sheet_area = rules.sheet_length_mm * rules.sheet_width_mm / 1_000_000
        # A sheet listing only unplaceable parts is not a sheet to buy.
        count = sum(1 for sheet in sheets if sheet.placements)
        unplaced = [part for sheet in sheets for part in sheet.unplaced]
        board_rows.append(
            BoardSummaryRow(
                material=material,
                thickness_mm=thickness,
                parts=values["parts"],
                area_m2=round(values["area"], 4),
                sheets=count,
                utilization=min(1.0, values["area"] / (count * sheet_area)) if count else 0,
                module_ids=sorted(values["module_ids"]),
                unplaced=unplaced,
            )
        )
    return MaterialSummary(
        boards=board_rows,
        edges=[
            EdgeSummaryRow(
                material=name,
                length_m=round(v["length"], 3),
                module_ids=sorted(v["per_module"]),
                per_module={k: round(q, 3) for k, q in v["per_module"].items()},
            )
            for name, v in edges.items()
        ],
        hardware=[
            HardwareSummaryRow(
                category=key[0],
                name=key[1],
                spec=key[2],
                unit=key[3],
                qty=round(v["qty"], 3),
                module_ids=sorted(v["per_module"]),
                per_module={k: round(q, 3) for k, q in v["per_module"].items()},
            )
            for key, v in hardware.items()
        ],
        module_count=len(models),
        incomplete_module_ids=[m.module.module_id for m in models if m.module.status == "incomplete"],
    )


def process_summary(models: list[ModuleModel]) -> ProcessSummary:
    rows, totals, entries = [], defaultdict(lambda: [0, 0.0]), []
    for model in models:
        matrix: dict[ProcessKind, ProcessCell] = {}
        agg: dict[ProcessKind, list] = defaultdict(lambda: [0.0, set(), 0.0])
        for line in model.processes:
            entries.append(ProcessEntry(module_id=model.module.module_id, **line.model_dump()))
            agg[line.process][0] += line.qty
            agg[line.process][1].add(line.unit)
            agg[line.process][2] += line.minutes
            totals[line.process][0] += 1
            totals[line.process][1] += line.minutes
        matrix = {
            key: ProcessCell(
                qty=round(value[0], 3) if len(value[1]) == 1 else None,
                unit=next(iter(value[1])) if len(value[1]) == 1 else None,
                minutes=round(value[2], 2),
            )
            for key, value in agg.items()
        }
        rows.append(
            ProcessMatrixRow(
                module_id=model.module.module_id,
                title=model.module.title,
                file_name=model.module.file_name,
                page_index=model.module.page_index,
                status=model.module.status,
                processes=matrix,
            )
        )
    return ProcessSummary(
        rows=rows,
        totals=[ProcessTotal(process=key, lines=value[0], minutes=round(value[1], 2)) for key, value in totals.items()],
        total_minutes=round(sum(value[1] for value in totals.values()), 2),
        lines=entries,
    )
