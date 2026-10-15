"""Ground truth comparison and accuracy reporting for extraction pipeline.

Compares pipeline JSON results against manually-verified ground truth,
computing per-field accuracy with exact and fuzzy matching.

Usage:
    uv run python -m src.evaluation.accuracy
    uv run python -m src.evaluation.accuracy --results-dir results/ --ground-truth results/ground_truth.json
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

# Fields to compare (in ExtractionResult order)
COMPARABLE_FIELDS = [
    "project_name",
    "client",
    "cabinet_id",
    "scale",
    "units",
]


@dataclass
class FieldComparison:
    field_name: str
    gt_value: str | None  # ground truth
    pred_value: str | None  # pipeline prediction
    confidence: str = ""
    exact_match: bool = False
    fuzzy_score: float = 0.0
    found: bool = False  # pipeline found *something* (even if wrong)
    status: str = ""  # "correct", "partial", "wrong", "missing", "no_gt"


@dataclass
class DrawingComparison:
    filename: str
    fields: list[FieldComparison] = field(default_factory=list)
    fields_correct: int = 0
    fields_partial: int = 0
    fields_found: int = 0
    fields_with_gt: int = 0
    accuracy: float = 0.0  # (correct + 0.5*partial) / fields_with_gt
    # Panel / hardware (cutting-list) comparison
    panel_comparisons: list[PanelComparison] = field(default_factory=list)
    hardware_comparisons: list[HardwareComparison] = field(default_factory=list)
    panels_gt: int = 0
    panels_matched: int = 0
    panels_partial: int = 0
    panels_missing: int = 0
    panels_pred_extra: int = 0  # predicted panels with no GT counterpart (over-extraction)
    panel_accuracy: float = 0.0
    hardware_gt: int = 0
    hardware_matched: int = 0
    hardware_partial: int = 0
    hardware_missing: int = 0
    hardware_accuracy: float = 0.0


@dataclass
class AccuracyReport:
    drawings: list[DrawingComparison] = field(default_factory=list)
    per_field_accuracy: dict[str, float] = field(default_factory=dict)
    per_field_found_rate: dict[str, float] = field(default_factory=dict)
    overall_accuracy: float = 0.0
    overall_found_rate: float = 0.0
    confidence_calibration: dict[str, float] = field(default_factory=dict)
    # Cutting-list (panels + hardware) accuracy, aggregated across all drawings
    panel_accuracy: float = 0.0
    panel_recall: float = 0.0  # fraction of GT panels found (matched or partial)
    panels_gt: int = 0
    panels_matched: int = 0
    panels_missing: int = 0
    hardware_accuracy: float = 0.0
    hardware_recall: float = 0.0
    hardware_gt: int = 0
    hardware_matched: int = 0


def normalize_value(value: str | None) -> str | None:
    """Normalize a value for comparison."""
    if value is None:
        return None
    v = str(value).strip().lower()
    # Remove surrounding quotes
    v = v.strip("'\"")
    # Normalize decimal separators
    v = v.replace(",", ".")
    # Normalize whitespace
    v = re.sub(r"\s+", " ", v).strip()
    return v if v else None


def normalize_bool(value: str | None) -> str | None:
    """Normalize boolean-like values."""
    if value is None:
        return None
    v = normalize_value(value)
    if v in ("true", "yes", "1", "tak"):
        return "true"
    if v in ("false", "no", "0", "nie"):
        return "false"
    return v


def compare_values(gt: str | None, pred: str | None, field_name: str = "") -> FieldComparison:
    """Compare a ground truth value against a predicted value."""
    gt_norm = normalize_value(gt)
    pred_norm = normalize_value(pred)

    comp = FieldComparison(
        field_name=field_name,
        gt_value=gt,
        pred_value=pred,
    )

    # No ground truth — can't evaluate
    if gt_norm is None:
        comp.status = "no_gt"
        return comp

    # GT exists but prediction is missing
    if pred_norm is None:
        comp.status = "missing"
        return comp

    comp.found = True

    # Exact match
    if gt_norm == pred_norm:
        comp.exact_match = True
        comp.fuzzy_score = 100.0
        comp.status = "correct"
        return comp

    # Fuzzy match
    score = fuzz.ratio(gt_norm, pred_norm)
    comp.fuzzy_score = score

    # Also check if one contains the other
    if gt_norm in pred_norm or pred_norm in gt_norm:
        score = max(score, 80.0)
        comp.fuzzy_score = score

    if score >= 85:
        comp.exact_match = True
        comp.status = "correct"
    elif score >= 60:
        comp.status = "partial"
    else:
        comp.status = "wrong"

    return comp


# Tolerance in millimeters for matching panel dimensions (furniture panels are
# commonly rounded to the nearest mm on drawings).
DIMENSION_TOLERANCE_MM = 2.0


def _panel_key(name: str | None, part_id: str | None) -> str:
    """Normalized identity key used to match a GT panel to a predicted one."""
    base = (part_id or name or "").strip().lower()
    return re.sub(r"\s+", " ", base)


def _dim_match(
    gt: float | int | None,
    pred: float | int | None,
    tol: float = DIMENSION_TOLERANCE_MM,
) -> bool | None:
    """Compare two dimensions; True/False, or None when either side is missing."""
    if gt is None or pred is None:
        return None
    try:
        return abs(float(gt) - float(pred)) <= tol
    except (TypeError, ValueError):
        return None


@dataclass
class PanelComparison:
    name: str
    matched: bool = False
    length_ok: bool | None = None
    width_ok: bool | None = None
    thickness_ok: bool | None = None
    board_ok: bool | None = None
    gt_panel: dict = field(default_factory=dict)
    pred_panel: dict | None = None
    status: str = "missing"  # matched | partial | missing


@dataclass
class HardwareComparison:
    name: str
    matched: bool = False
    qty_ok: bool | None = None
    code_ok: bool | None = None
    gt_hw: dict = field(default_factory=dict)
    pred_hw: dict | None = None
    status: str = "missing"  # matched | partial | missing


def _greedy_match(gt_items, pred_items, key_fn, threshold: float = 70.0):
    """Greedy 1:1 match of GT items to predicted items by string similarity.

    Returns a list of ``(gt_item, matched_pred_or_None, score)`` tuples.
    """
    available = list(pred_items)
    pairs = []
    for gt in gt_items:
        gt_key = key_fn(gt)
        best_idx, best_score = -1, 0.0
        for i, pred in enumerate(available):
            pred_key = key_fn(pred)
            score = 100.0 if (gt_key and gt_key == pred_key) else fuzz.ratio(gt_key, pred_key)
            if score > best_score:
                best_score, best_idx = score, i
        if best_idx >= 0 and best_score >= threshold:
            pairs.append((gt, available.pop(best_idx), best_score))
        else:
            pairs.append((gt, None, best_score))
    return pairs


def compare_panels(gt_panels: list[dict], pred_panels: list[dict]) -> list[PanelComparison]:
    """Match GT panels to predicted panels and compare dimensions / board type."""
    comparisons: list[PanelComparison] = []
    pairs = _greedy_match(
        gt_panels,
        pred_panels,
        key_fn=lambda p: _panel_key(p.get("name"), p.get("part_id")),
    )
    for gt, pred, _score in pairs:
        comp = PanelComparison(name=gt.get("name") or gt.get("part_id") or "?", gt_panel=gt)
        if pred is None:
            comparisons.append(comp)
            continue
        comp.pred_panel = pred
        comp.matched = True
        comp.length_ok = _dim_match(gt.get("length_mm"), pred.get("length_mm"))
        comp.width_ok = _dim_match(gt.get("width_mm"), pred.get("width_mm"))
        comp.thickness_ok = _dim_match(gt.get("thickness_mm"), pred.get("thickness_mm"))
        gt_board, pred_board = gt.get("board_type"), pred.get("board_type")
        gt_board_n, pred_board_n = normalize_value(gt_board), normalize_value(pred_board)
        if gt_board_n is not None and pred_board_n is not None:
            comp.board_ok = fuzz.ratio(gt_board_n, pred_board_n) >= 80
        checks = [c for c in (comp.length_ok, comp.width_ok, comp.thickness_ok, comp.board_ok) if c is not None]
        comp.status = "matched" if (checks and all(checks)) else "partial"
        comparisons.append(comp)
    return comparisons


def compare_hardware(gt_hw: list[dict], pred_hw: list[dict]) -> list[HardwareComparison]:
    """Match GT hardware items to predicted ones and compare qty / code."""
    comparisons: list[HardwareComparison] = []
    pairs = _greedy_match(
        gt_hw,
        pred_hw,
        key_fn=lambda h: re.sub(r"\s+", " ", (h.get("name") or "").strip().lower()),
    )
    for gt, pred, _score in pairs:
        comp = HardwareComparison(name=gt.get("name") or "?", gt_hw=gt)
        if pred is None:
            comparisons.append(comp)
            continue
        comp.pred_hw = pred
        comp.matched = True
        if gt.get("qty") is not None and pred.get("qty") is not None:
            try:
                comp.qty_ok = int(gt["qty"]) == int(pred["qty"])
            except (TypeError, ValueError):
                comp.qty_ok = None
        gt_code, pred_code = gt.get("code"), pred.get("code")
        gt_code_n, pred_code_n = normalize_value(gt_code), normalize_value(pred_code)
        if gt_code_n is not None and pred_code_n is not None:
            comp.code_ok = fuzz.ratio(gt_code_n, pred_code_n) >= 80
        checks = [c for c in (comp.qty_ok, comp.code_ok) if c is not None]
        comp.status = "matched" if (checks and all(checks)) else "partial"
        comparisons.append(comp)
    return comparisons


def load_ground_truth(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load ground truth from JSON file.

    Returns: {filename: {field_name: value}}
    """
    with open(path) as f:
        data = json.load(f)
    return data.get("drawings", data)


def load_results(results_dir: str | Path) -> dict[str, dict]:
    """Load pipeline results from JSON files in a directory.

    Returns: {filename: {field_name: FieldExtraction_dict}}
    """
    results: dict[str, dict] = {}
    results_path = Path(results_dir)

    for json_file in results_path.glob("*.json"):
        if json_file.name == "ground_truth.json":
            continue
        with open(json_file) as f:
            data = json.load(f)
        source = data.get("source_file", "")
        filename = Path(source).name if source else json_file.stem + ".pdf"
        results[filename] = data

    return results


def _filename_id(filename: str) -> str:
    """Normalized identifier for matching a ground-truth file to a result file.

    Tries the legacy ``Kopia <code>`` pattern first (kept for backward
    compatibility with the forging-era sample filenames), then falls back to the
    file stem.
    """
    m = re.search(r"Kopia\s+(\S+)", filename, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return Path(filename).stem.upper()


def match_drawings(gt: dict[str, dict], results: dict[str, dict]) -> list[tuple[str, dict, dict]]:
    """Match ground-truth entries to pipeline results by filename.

    Robust to furniture filenames: first exact id match, then substring
    containment in either direction (handles e.g. ``2 SZAFA - NASZ RYSUNEK``).
    """
    matched: list[tuple[str, dict, dict]] = []
    used: set[str] = set()

    # Pass 1: exact id match
    for gt_file, gt_data in gt.items():
        gt_id = _filename_id(gt_file)
        for res_file, res_data in results.items():
            if res_file in used:
                continue
            if gt_id == _filename_id(res_file):
                matched.append((gt_file, gt_data, res_data))
                used.add(res_file)
                break

    # Pass 2: substring containment (either direction)
    for gt_file, gt_data in gt.items():
        if any(g == gt_file for g, _, _ in matched):
            continue
        gt_id = _filename_id(gt_file)
        for res_file, res_data in results.items():
            if res_file in used:
                continue
            res_id = _filename_id(res_file)
            if gt_id and res_id and (gt_id in res_id or res_id in gt_id):
                matched.append((gt_file, gt_data, res_data))
                used.add(res_file)
                break

    return matched


def compare_drawing(
    gt_data: dict[str, Any],
    result_data: dict[str, Any],
) -> DrawingComparison:
    """Compare ground truth against pipeline result for one drawing.

    Compares both the scalar project fields and the cutting-list payload
    (panels + hardware).
    """
    comparisons: list[FieldComparison] = []

    for field_name in COMPARABLE_FIELDS:
        gt_value = gt_data.get(field_name)

        # Extract prediction from result (may be nested FieldExtraction dict)
        field_data = result_data.get(field_name)
        if isinstance(field_data, dict):
            pred_value = field_data.get("value")
            confidence = field_data.get("confidence", "")
        else:
            pred_value = field_data
            confidence = ""

        comp = compare_values(gt_value, pred_value, field_name)
        comp.confidence = confidence
        comparisons.append(comp)

    # Scalar metrics
    fields_with_gt = [c for c in comparisons if c.status != "no_gt"]
    correct = sum(1 for c in fields_with_gt if c.status == "correct")
    partial = sum(1 for c in fields_with_gt if c.status == "partial")
    found = sum(1 for c in fields_with_gt if c.found)
    n_gt = len(fields_with_gt)
    accuracy = (correct + 0.5 * partial) / n_gt if n_gt > 0 else 0.0

    # Panel (cutting-list) comparison
    gt_panels = gt_data.get("panels") or []
    pred_panels = result_data.get("panels") or []
    panel_comps = compare_panels(list(gt_panels), list(pred_panels))
    panels_matched = sum(1 for c in panel_comps if c.status == "matched")
    panels_partial = sum(1 for c in panel_comps if c.status == "partial")
    panels_missing = sum(1 for c in panel_comps if c.status == "missing")
    panels_gt = len(panel_comps)
    panel_accuracy = (panels_matched + 0.5 * panels_partial) / panels_gt if panels_gt > 0 else 0.0
    matched_count = panels_matched + panels_partial
    panels_pred_extra = max(0, len(pred_panels) - matched_count)

    # Hardware comparison
    gt_hw = gt_data.get("hardware") or []
    pred_hw = result_data.get("hardware") or []
    hw_comps = compare_hardware(list(gt_hw), list(pred_hw))
    hardware_matched = sum(1 for c in hw_comps if c.status == "matched")
    hardware_partial = sum(1 for c in hw_comps if c.status == "partial")
    hardware_missing = sum(1 for c in hw_comps if c.status == "missing")
    hardware_gt = len(hw_comps)
    hardware_accuracy = (hardware_matched + 0.5 * hardware_partial) / hardware_gt if hardware_gt > 0 else 0.0

    return DrawingComparison(
        filename=gt_data.get("_filename", "unknown"),
        fields=comparisons,
        fields_correct=correct,
        fields_partial=partial,
        fields_found=found,
        fields_with_gt=n_gt,
        accuracy=accuracy,
        panel_comparisons=panel_comps,
        hardware_comparisons=hw_comps,
        panels_gt=panels_gt,
        panels_matched=panels_matched,
        panels_partial=panels_partial,
        panels_missing=panels_missing,
        panels_pred_extra=panels_pred_extra,
        panel_accuracy=panel_accuracy,
        hardware_gt=hardware_gt,
        hardware_matched=hardware_matched,
        hardware_partial=hardware_partial,
        hardware_missing=hardware_missing,
        hardware_accuracy=hardware_accuracy,
    )


def compute_report(drawings: list[DrawingComparison]) -> AccuracyReport:
    """Compute overall accuracy report from per-drawing comparisons."""
    report = AccuracyReport(drawings=drawings)

    # Per-field accuracy across all drawings
    for field_name in COMPARABLE_FIELDS:
        field_comps = [c for d in drawings for c in d.fields if c.field_name == field_name]
        evaluable = [c for c in field_comps if c.status != "no_gt"]
        if evaluable:
            correct = sum(1 for c in evaluable if c.status == "correct")
            partial = sum(1 for c in evaluable if c.status == "partial")
            found = sum(1 for c in evaluable if c.found)
            report.per_field_accuracy[field_name] = (correct + 0.5 * partial) / len(evaluable)
            report.per_field_found_rate[field_name] = found / len(evaluable)

    # Overall
    all_evaluable = [c for d in drawings for c in d.fields if c.status != "no_gt"]
    if all_evaluable:
        correct = sum(1 for c in all_evaluable if c.status == "correct")
        partial = sum(1 for c in all_evaluable if c.status == "partial")
        found = sum(1 for c in all_evaluable if c.found)
        report.overall_accuracy = (correct + 0.5 * partial) / len(all_evaluable)
        report.overall_found_rate = found / len(all_evaluable)

    # Confidence calibration: for HIGH confidence fields, how many are correct?
    for conf_level in ("HIGH", "MEDIUM", "LOW"):
        with_conf = [c for d in drawings for c in d.fields if c.confidence == conf_level and c.status != "no_gt"]
        if with_conf:
            correct = sum(1 for c in with_conf if c.status == "correct")
            report.confidence_calibration[conf_level] = correct / len(with_conf)

    # Aggregate panel / hardware (cutting-list) accuracy across all drawings
    panels_gt = sum(d.panels_gt for d in drawings)
    panels_matched = sum(d.panels_matched for d in drawings)
    panels_partial = sum(d.panels_partial for d in drawings)
    panels_missing = sum(d.panels_missing for d in drawings)
    if panels_gt > 0:
        report.panels_gt = panels_gt
        report.panels_matched = panels_matched
        report.panels_missing = panels_missing
        report.panel_accuracy = (panels_matched + 0.5 * panels_partial) / panels_gt
        report.panel_recall = (panels_matched + panels_partial) / panels_gt

    hardware_gt = sum(d.hardware_gt for d in drawings)
    hardware_matched = sum(d.hardware_matched for d in drawings)
    hardware_partial = sum(d.hardware_partial for d in drawings)
    if hardware_gt > 0:
        report.hardware_gt = hardware_gt
        report.hardware_matched = hardware_matched
        report.hardware_accuracy = (hardware_matched + 0.5 * hardware_partial) / hardware_gt
        report.hardware_recall = (hardware_matched + hardware_partial) / hardware_gt

    return report


def generate_report(report: AccuracyReport) -> str:
    """Generate markdown accuracy report."""
    lines = ["# Extraction Accuracy Report", ""]

    # Overall
    lines.append(f"**Overall accuracy**: {report.overall_accuracy:.1%}")
    lines.append(f"**Overall found rate**: {report.overall_found_rate:.1%}")
    lines.append("")

    # Per-drawing table
    lines.append("## Per-Drawing Accuracy")
    lines.append("")
    lines.append("| Drawing | Accuracy | Correct | Partial | Found | Total GT |")
    lines.append("|---------|----------|---------|---------|-------|----------|")
    for d in report.drawings:
        name = d.filename[:50] + "..." if len(d.filename) > 50 else d.filename
        lines.append(
            f"| {name} | {d.accuracy:.0%} | {d.fields_correct} | "
            f"{d.fields_partial} | {d.fields_found} | {d.fields_with_gt} |"
        )
    lines.append("")

    # Per-field table
    lines.append("## Per-Field Accuracy")
    lines.append("")
    lines.append("| Field | Accuracy | Found Rate |")
    lines.append("|-------|----------|------------|")
    for field_name in COMPARABLE_FIELDS:
        acc = report.per_field_accuracy.get(field_name, 0)
        found = report.per_field_found_rate.get(field_name, 0)
        lines.append(f"| {field_name} | {acc:.0%} | {found:.0%} |")
    lines.append("")

    # Cutting-list (panels + hardware) accuracy
    if report.panels_gt or report.hardware_gt:
        lines.append("## Cutting-List Accuracy")
        lines.append("")
        lines.append("| Payload | Accuracy | Recall | Matched | Partial | Missing | GT total |")
        lines.append("|---------|----------|--------|---------|---------|---------|----------|")
        panels_partial_total = report.panels_gt - report.panels_matched - report.panels_missing
        lines.append(
            f"| Panels | {report.panel_accuracy:.0%} | {report.panel_recall:.0%} | "
            f"{report.panels_matched} | {panels_partial_total} | {report.panels_missing} | {report.panels_gt} |"
        )
        hw_partial_total = report.hardware_gt - report.hardware_matched
        lines.append(
            f"| Hardware | {report.hardware_accuracy:.0%} | {report.hardware_recall:.0%} | "
            f"{report.hardware_matched} | {hw_partial_total} | — | {report.hardware_gt} |"
        )
        lines.append("")

    # Per-drawing panel breakdown
    drawings_with_panels = [d for d in report.drawings if d.panels_gt]
    if drawings_with_panels:
        lines.append("## Per-Drawing Panel Accuracy")
        lines.append("")
        lines.append("| Drawing | Panel Acc | Matched | Partial | Missing | Extra | GT |")
        lines.append("|---------|-----------|---------|---------|---------|-------|----|")
        for d in drawings_with_panels:
            name = d.filename[:50]
            lines.append(
                f"| {name} | {d.panel_accuracy:.0%} | {d.panels_matched} | "
                f"{d.panels_partial} | {d.panels_missing} | {d.panels_pred_extra} | {d.panels_gt} |"
            )
        lines.append("")

    # Confidence calibration
    if report.confidence_calibration:
        lines.append("## Confidence Calibration")
        lines.append("")
        lines.append("How often is each confidence level actually correct?")
        lines.append("")
        lines.append("| Confidence | Precision |")
        lines.append("|-----------|-----------|")
        for level in ("HIGH", "MEDIUM", "LOW"):
            if level in report.confidence_calibration:
                lines.append(f"| {level} | {report.confidence_calibration[level]:.0%} |")
        lines.append("")

    # Detailed per-drawing field breakdown
    lines.append("## Detailed Field Comparison")
    lines.append("")
    for d in report.drawings:
        lines.append(f"### {d.filename}")
        lines.append("")
        lines.append("| Field | Ground Truth | Predicted | Confidence | Status | Fuzzy |")
        lines.append("|-------|-------------|-----------|------------|--------|-------|")
        for c in d.fields:
            gt_str = str(c.gt_value) if c.gt_value is not None else "—"
            pred_str = str(c.pred_value) if c.pred_value is not None else "—"
            gt_short = gt_str[:40]
            pred_short = pred_str[:40]
            status_icon = {
                "correct": "+",
                "partial": "~",
                "wrong": "x",
                "missing": "-",
                "no_gt": "?",
            }.get(c.status, c.status)
            lines.append(
                f"| {c.field_name} | {gt_short} | {pred_short} | {c.confidence} | {status_icon} | {c.fuzzy_score:.0f} |"
            )
        lines.append("")

    return "\n".join(lines)


def run_evaluation(
    results_dir: str = "results",
    ground_truth_path: str = "results/ground_truth.json",
    output_path: str | None = None,
) -> AccuracyReport:
    """Run the full evaluation pipeline."""
    gt = load_ground_truth(ground_truth_path)
    results = load_results(results_dir)
    matched = match_drawings(gt, results)

    if not matched:
        print(
            f"No matching drawings found between ground truth ({len(gt)} entries) and results ({len(results)} entries)"
        )
        return AccuracyReport()

    print(f"Matched {len(matched)} drawings for evaluation")

    drawings: list[DrawingComparison] = []
    for gt_file, gt_data, result_data in matched:
        gt_data_copy = {**gt_data, "_filename": gt_file}
        comp = compare_drawing(gt_data_copy, result_data)
        drawings.append(comp)

    report = compute_report(drawings)
    markdown = generate_report(report)

    if output_path:
        Path(output_path).write_text(markdown)
        print(f"Report written to {output_path}")
    else:
        print(markdown)

    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate extraction accuracy")
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--ground-truth", default="results/ground_truth.json")
    parser.add_argument("--output", default=None, help="Output markdown file path")
    args = parser.parse_args()

    report = run_evaluation(args.results_dir, args.ground_truth, args.output)

    # Exit with error code if accuracy is too low (< 50%)
    if report.overall_accuracy < 0.5:
        sys.exit(1)
