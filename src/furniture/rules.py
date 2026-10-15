"""Shop construction rules (board thicknesses, gaps, sheet sizes) from ``config/furniture.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, cast

import yaml


@dataclass(frozen=True)
class HardwareRules:
    hinge_height_thresholds_mm: tuple[float, ...] = (900, 1600, 2000)
    hinge_counts: tuple[int, ...] = (2, 3, 4, 5)
    runner_lengths_mm: tuple[float, ...] = (250, 300, 350, 400, 450, 500, 550, 600)
    leg_base_qty: int = 4
    leg_extra_per_started_mm: float = 600
    leg_extra_qty: int = 2
    connector_per_joint: int = 4
    connector_depth_scale_mm: float = 600
    back_fixing_perimeter_step_mm: float = 250

    disabled_categories_by_kind: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessRules:
    cut_part_minutes: float = 2
    cut_per_metre_minutes: float = 1.5
    edge_band_per_metre_minutes: float = 2
    hinge_cup_minutes: float = 2
    confirmat_hole_minutes: float = 0.5
    shelf_support_hole_minutes: float = 0.4
    runner_screw_minutes: float = 0.5
    carcass_assembly_minutes: float = 20
    drawer_assembly_minutes: float = 8
    back_assembly_minutes: float = 8
    hinge_mount_minutes: float = 4
    front_adjustment_minutes: float = 3
    runner_mount_minutes: float = 5
    handle_mount_minutes: float = 2
    rail_mount_minutes: float = 5
    leg_mount_minutes: float = 5
    installation_per_module_minutes: float = 30
    installation_per_m2_minutes: float = 15
    wall_cladding_installation_per_m2_minutes: float = 15
    installation_per_metre_minutes: float = 10


@dataclass(frozen=True)
class PlausibilityRules:
    width_max_mm: float = 8000
    height_max_mm: float = 3200
    height_min_mm: float = 200
    depth_max_mm: float = 1200
    depth_min_mm: float = 100
    wall_cladding_depth_min_mm: float = 10
    column_width_min_mm: float = 100
    cell_height_min_mm: float = 50
    plinth_max_mm: float = 300


@dataclass(frozen=True)
class ConstructionRules:
    carcass_thickness_mm: float = 18
    front_thickness_mm: float = 19
    back_thickness_mm: float = 8
    front_gap_mm: float = 3
    plinth_default_mm: float = 100
    plinth_recess_mm: float = 50
    depth_default_mm: float = 600
    shelf_setback_mm: float = 10
    drawer_box_thickness_mm: float = 16
    drawer_box_bottom_mm: float = 8
    drawer_runner_clearance_mm: float = 13
    drawer_box_height_reduction_mm: float = 50
    drawer_box_depth_reduction_mm: float = 80
    sheet_length_mm: float = 2800
    sheet_width_mm: float = 2070
    sheet_trim_mm: float = 10
    kerf_mm: float = 4
    cladding_thickness_mm: float = 18
    mounting_batten_thickness_mm: float = 30
    mounting_batten_depth_mm: float = 40
    mounting_batten_spacing_mm: float = 600
    plausibility: PlausibilityRules = field(default_factory=PlausibilityRules)
    hardware: HardwareRules = field(default_factory=HardwareRules)
    processes: ProcessRules = field(default_factory=ProcessRules)


def _section[T](section: type[T], raw: object) -> T:
    if not isinstance(raw, dict):
        return section()
    valid = {item.name for item in fields(cast(Any, section))}
    values = {key: value for key, value in raw.items() if key in valid}
    for key in ("hinge_height_thresholds_mm", "hinge_counts", "runner_lengths_mm"):
        if key in values:
            values[key] = tuple(values[key])
    if "disabled_categories_by_kind" in values:
        values["disabled_categories_by_kind"] = {
            kind: tuple(categories) for kind, categories in values["disabled_categories_by_kind"].items()
        }
    return section(**values)


def load_rules(path: str | Path = "config/furniture.yaml") -> ConstructionRules:
    """Read shop rules; missing files, sections, and keys use backwards-compatible defaults."""
    file = Path(path)
    raw = yaml.safe_load(file.read_text()) or {} if file.exists() else {}
    known = {item.name for item in fields(ConstructionRules)} - {"hardware", "processes", "plausibility"}
    values = {key: float(value) for key, value in raw.items() if key in known}
    return ConstructionRules(
        **values,
        hardware=_section(HardwareRules, raw.get("hardware")),
        processes=_section(ProcessRules, raw.get("processes")),
        plausibility=_section(PlausibilityRules, raw.get("plausibility")),
    )
