"""Collect model comparison results from JSON files and produce an XLSX report.

Scans a directory tree (default: results/model_comparison/) for JSON result
files, groups them by model (subdirectory name), and generates:
  - Field Comparison sheet (side-by-side per drawing)
  - Confidence Heatmap
  - Best-per-Model summary
  - Resources overview
  - Review-needed report

Usage:
    uv run python scripts/collect_comparison.py
    uv run python scripts/collect_comparison.py -o results/run2
    uv run python scripts/collect_comparison.py -d results/model_comparison
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.output.comparison import (
    COMPARISON_FIELDS,
    HEADER_FILL,
    HEADER_FONT,
    write_benchmark_report,
    write_consolidated_summary,
    write_review_report,
)
from src.schema.extraction import ExtractionResult


def discover_results(root: Path) -> dict[str, list[ExtractionResult]]:
    """Walk <root>/<model_dir>/**/*.json and group by model directory name."""
    results_by_model: dict[str, list[ExtractionResult]] = {}

    for model_dir in sorted(root.iterdir()):
        if not model_dir.is_dir():
            continue

        model_name = model_dir.name
        json_files = sorted(model_dir.rglob("*.json"))

        if not json_files:
            print(f"  {model_name}: no JSON files found, skipping")
            continue

        results: list[ExtractionResult] = []
        failures = 0

        for jf in json_files:
            try:
                results.append(ExtractionResult.model_validate_json(jf.read_text(encoding="utf-8")))
            except Exception as e:
                print(f"  WARNING: failed to parse {jf.name}: {e}")
                failures += 1

        if results:
            results_by_model[model_name] = results

        status = f"{len(results)} results"
        if failures:
            status += f", {failures} parse errors"
        print(f"  {model_name}: {status}")

    return results_by_model


def write_field_detail_sheet(wb, results_by_model: dict[str, list[ExtractionResult]]) -> None:
    """Per-field detail sheet: one row per (drawing, field) with model values."""

    ws = wb.create_sheet("Field Detail")

    models = list(results_by_model.keys())
    headers = ["Drawing", "Field", *models]
    ws.append(headers)

    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT

    drawings: dict[str, dict[str, ExtractionResult]] = {}
    for model_name, results in results_by_model.items():
        for r in results:
            key = Path(r.source_file).name
            drawings.setdefault(key, {})[model_name] = r

    for drawing_name in sorted(drawings):
        model_results = drawings[drawing_name]
        for field_name in COMPARISON_FIELDS:
            row = [drawing_name, field_name]
            for model in models:
                result = model_results.get(model)
                if result:
                    f = getattr(result, field_name, None)
                    row.append(f.value or "" if f else "")
                else:
                    row.append("")
            ws.append(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and compare model results")
    parser.add_argument(
        "-d",
        "--dir",
        default="results/model_comparison",
        help="Root directory containing per-model subdirectories (default: results/model_comparison)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output directory for reports (default: <dir>)",
    )
    args = parser.parse_args()

    root = Path(args.dir)
    output_dir = Path(args.output) if args.output else root

    if not root.exists():
        print(f"Directory not found: {root}")
        sys.exit(1)

    print(f"Scanning: {root}/")
    print()
    results_by_model = discover_results(root)

    if not results_by_model:
        print("\nNo results found.")
        sys.exit(1)

    models = list(results_by_model.keys())
    total_results = sum(len(r) for r in results_by_model.values())
    print(f"\nFound {total_results} results across {len(models)} model(s): {', '.join(models)}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Main comparison report
    benchmark_path = write_benchmark_report(
        results_by_model,
        str(output_dir / "model_comparison.xlsx"),
    )
    print(f"\nComparison report: {benchmark_path}")

    # Best-per-drawing summary
    summary_path = write_consolidated_summary(
        results_by_model,
        str(output_dir / "best_per_drawing.xlsx"),
    )
    print(f"Best-per-drawing:  {summary_path}")

    # Review report
    all_results = [r for results in results_by_model.values() for r in results]
    review_path = write_review_report(
        all_results,
        str(output_dir / "review_needed.xlsx"),
    )
    needs_review = [r for r in all_results if r.needs_review]
    print(f"Review needed:     {review_path} ({len(needs_review)}/{len(all_results)} results)")

    # Add Field Detail sheet to the main report
    from openpyxl import load_workbook

    wb = load_workbook(str(benchmark_path))
    write_field_detail_sheet(wb, results_by_model)
    wb.save(str(benchmark_path))

    # Summary to console
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Model Summary")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    for model, results in results_by_model.items():
        fields_per_result = []
        for r in results:
            filled = sum(1 for fn in COMPARISON_FIELDS if (f := getattr(r, fn, None)) and f.value)
            fields_per_result.append(filled)
        avg = sum(fields_per_result) / len(fields_per_result) if fields_per_result else 0
        total_tokens = sum(r.tokens_used for r in results)
        review_count = sum(1 for r in results if r.needs_review)
        print(f"  {model}:")
        print(f"    Drawings: {len(results)}, Avg fields: {avg:.1f}/{len(COMPARISON_FIELDS)}, Tokens: {total_tokens:,}")
        print(f"    Needs review: {review_count}/{len(results)}")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    main()
