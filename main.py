from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

from config.settings import AppConfig
from src.output.comparison import (
    write_benchmark_report,
    write_consolidated_summary,
    write_review_report,
)
from src.output.ocr_comparison import write_ocr_comparison_report
from src.output.writer import write_json, write_token_report, write_xlsx
from src.pipeline.agent import make_loader
from src.pipeline.benchmark import ModelBenchmarkRunner
from src.pipeline.comparator import OCRComparator
from src.pipeline.orchestrator import DrawingPipeline
from src.pipeline.simple_extractor import extract_drawing


def parse_args(argv: list[str] | None = None) -> dict:
    import argparse

    parser = argparse.ArgumentParser(
        description="Furniture cutting-list extraction pipeline — extract panel/part data from architectural and CAD drawings for CNC manufacturing",
    )
    parser.add_argument(
        "--config",
        "-c",
        default="config/default.yaml",
        help="Path to YAML config file (default: config/default.yaml)",
    )
    parser.add_argument(
        "--input-dir",
        "-i",
        help="Path to directory containing drawing files (PDF/TIF/image/DWG/P3D)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output directory for results",
    )
    parser.add_argument(
        "--api-base",
        help="Override API base URL (e.g., http://localhost:1234/v1 for LM Studio)",
    )
    parser.add_argument(
        "--orchestrator",
        help="Override orchestrator model (agent driver, tool-calling)",
    )
    parser.add_argument(
        "--vision",
        help="Override vision model (drawing analysis)",
    )
    parser.add_argument(
        "--benchmark",
        "-b",
        action="store_true",
        help="Run multi-model benchmark comparison",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="Process a single drawing file instead of batch",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Use simple direct extraction (no agent loop, no tools)",
    )
    parser.add_argument(
        "--step-by-step",
        action="store_true",
        help="Process all 9 tiles sequentially with progressive output",
    )
    parser.add_argument(
        "--ocr",
        choices=["vlm", "olmocr", "glm-ocr"],
        help="OCR backend to use (default: vlm)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare OCR backends (VLM vs OLMOCR vs GLM-OCR)",
    )

    args = parser.parse_args(argv)

    overrides: dict = {}
    if args.input_dir:
        overrides["input_dir"] = args.input_dir
    if args.api_base:
        overrides["api.base_url"] = args.api_base
    if args.output:
        overrides["output.dir"] = args.output
    if args.orchestrator:
        overrides["models.orchestrator"] = args.orchestrator
    if args.vision:
        overrides["models.vision"] = args.vision

    cli_args = {
        "input_dir": args.input_dir,
        "output_dir": args.output,
        "api_base": args.api_base,
        "orchestrator_model": args.orchestrator,
        "vision_model": args.vision,
        "benchmark": args.benchmark,
        "single": args.single,
        "simple": args.simple,
        "step_by_step": args.step_by_step,
        "ocr_backend": args.ocr,
        "compare": args.compare,
        "verbose": args.verbose,
    }

    overrides["cli_args"] = cli_args

    return {
        "config_path": args.config,
        "overrides": overrides,
        "benchmark": args.benchmark,
        "compare": args.compare,
        "single": args.single,
        "simple": args.simple,
        "step_by_step": args.step_by_step,
        "ocr": args.ocr,
        "verbose": args.verbose,
    }


async def run_simple(config: AppConfig) -> None:
    """Run simple direct extraction — no agent, no tools, just tile-by-tile JSON."""
    import time
    from pathlib import Path

    from src.schema.extraction import RunMetadata

    input_path = Path(config.input_dir)
    if input_path.is_file():
        drawing_path = str(input_path)
    else:
        drawings = sorted(
            p for ext in (".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg") for p in input_path.rglob(f"*{ext}")
        )
        if not drawings:
            print("No drawings found.")
            return
        drawing_path = str(drawings[0])

    print(f"Simple extraction: {Path(drawing_path).name}")

    loader = make_loader(drawing_path, config.document)

    run_metadata = RunMetadata(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        orchestrator_model=config.models.orchestrator,
        vision_model=config.models.vision,
        api_base_url=config.api.base_url,
        pipeline_mode=config.pipeline.mode,
        validation_mode=config.pipeline.validation_mode,
        max_passes=config.pipeline.max_passes,
        confidence_threshold=config.pipeline.confidence_threshold,
        ocr_backend=config.ocr.backend,
        cli_args=config.cli_args,
    )

    result, metrics = await extract_drawing(
        loader=loader,
        api_base=config.api.base_url,
        model_name=config.models.orchestrator,
        api_key=config.api.api_key,
        tile_overlap=config.document.tile_overlap,
        run_metadata=run_metadata,
    )

    json_path = write_json(result, config.output.dir)
    print(f"Result: {json_path}")
    print(
        f"Tiles: {metrics['tiles_processed']}, Tokens: {metrics['total_tokens']:,}, Fields: {metrics['fields_extracted']}"
    )
    print(f"Project: {result.project_name.value}, Client: {result.client.value}")
    print(f"Cabinet: {result.cabinet_id.value}, Panels: {len(result.panels)}, Hardware: {len(result.hardware)}")
    if result.needs_review:
        fields = ", ".join(result.review_fields) or "(none listed)"
        print(f"Needs review: {fields}")


async def run_step_by_step(config: AppConfig) -> None:
    """Run step-by-step tile-by-tile extraction with progressive output."""
    from pathlib import Path

    from src.ocr.factory import create_ocr_backend
    from src.pipeline.step_by_step import extract_drawing_step_by_step

    input_path = Path(config.input_dir)
    if input_path.is_file():
        drawing_path = str(input_path)
    else:
        drawings = sorted(
            p for ext in (".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg") for p in input_path.rglob(f"*{ext}")
        )
        if not drawings:
            print("No drawings found.")
            return
        drawing_path = str(drawings[0])

    ocr_backend = create_ocr_backend(config)
    loader = make_loader(drawing_path, config.document)

    result, metrics = await extract_drawing_step_by_step(
        loader=loader,
        ocr_backend=ocr_backend,
        tile_overlap=config.document.tile_overlap,
    )

    json_path = write_json(result, config.output.dir)
    print(f"Result saved: {json_path}")
    print(
        f"Tiles: {metrics['tiles_processed']}/9, Tokens: {metrics['total_tokens']:,}, Fields: {metrics['fields_extracted']}"
    )


async def run_single(config: AppConfig) -> None:
    pipeline = DrawingPipeline(config)
    result, metrics = await pipeline.process_drawing(config.input_dir)

    json_path = write_json(result, config.output.dir)
    print(f"Result: {json_path}")

    if result.needs_review:
        print(f"Fields needing review: {', '.join(result.review_fields)}")

    print(f"Tokens used: {metrics.total_tokens:,}, overflow: {metrics.overflow}")
    for p in metrics.passes:
        print(
            f"  {p.pass_name}: input={p.total_input_tokens:,}, output={p.total_output_tokens:,}, peak={p.peak_input_tokens:,}"
        )


async def run_batch(config: AppConfig) -> None:
    pipeline = DrawingPipeline(config)
    results, all_metrics = await pipeline.process_directory(config.input_dir)

    if not results:
        print("No drawings processed.")
        return

    xlsx_path = write_xlsx(results, f"{config.output.dir}/extraction_results.xlsx")
    print(f"Results: {xlsx_path} ({len(results)} drawings)")

    for r in results:
        write_json(r, f"{config.output.dir}/{r.model_used}")

    review_path = write_review_report(results, f"{config.output.dir}/review_needed.xlsx")
    needs_review = [r for r in results if r.needs_review]
    print(f"Review needed: {len(needs_review)}/{len(results)} drawings -> {review_path}")

    token_path = write_token_report(
        all_metrics,
        f"{config.output.dir}/token_report.xlsx",
        pipeline.schema_overhead,
    )
    overflow_count = sum(1 for m in all_metrics if m.overflow)
    total_tokens = sum(m.total_tokens for m in all_metrics)
    print(f"Token report: {token_path} (total: {total_tokens:,}, overflows: {overflow_count}/{len(results)})")


async def run_compare(config: AppConfig) -> None:
    comparator = OCRComparator(config)
    comparisons = await comparator.compare_all(config.input_dir)

    if not comparisons:
        print("No comparisons produced.")
        return

    report_path = write_ocr_comparison_report(
        comparisons,
        f"{config.output.dir}/ocr_comparison.xlsx",
    )
    print(f"Comparison report: {report_path}")

    for comp in comparisons:
        print(f"\n{comp.drawing_name}:")
        for br in comp.backends:
            fields = sum(
                1
                for fn in (
                    "project_name",
                    "client",
                    "cabinet_id",
                    "scale",
                    "units",
                )
                if (f := getattr(br.result, fn, None)) and f.value
            )
            print(
                f"  {br.backend_name:10s} {fields} fields, "
                f"{br.result.tokens_used:,} tokens, "
                f"{comp.timing.get(br.backend_name, 0):.1f}s"
            )
        if comp.field_agreements:
            full = sum(1 for a in comp.field_agreements if a.agreement_ratio >= 0.8)
            print(f"  Agreement: {full}/{len(comp.field_agreements)} fields")


async def run_benchmark(config: AppConfig) -> None:
    runner = ModelBenchmarkRunner(config)
    report = await runner.run_all(config.input_dir)

    if not report.results_by_model:
        print("No results collected.")
        return

    benchmark_path = write_benchmark_report(
        report.results_by_model,
        f"{config.output.dir}/benchmark_report.xlsx",
        report.timing_by_model,
        report.token_usage_by_model,
    )
    print(f"Benchmark report: {benchmark_path}")

    summary_path = write_consolidated_summary(
        report.results_by_model,
        f"{config.output.dir}/consolidated_summary.xlsx",
    )
    print(f"Consolidated summary: {summary_path}")

    all_results = [r for results in report.results_by_model.values() for r in results]
    review_path = write_review_report(all_results, f"{config.output.dir}/review_needed.xlsx")
    needs_review = [r for r in all_results if r.needs_review]
    print(f"Review needed: {len(needs_review)}/{len(all_results)} results -> {review_path}")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args["verbose"] else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = AppConfig.from_yaml(args["config_path"], **args["overrides"])

    if args["benchmark"]:
        asyncio.run(run_benchmark(config))
    elif args["compare"]:
        asyncio.run(run_compare(config))
    elif args["step_by_step"]:
        if args["ocr"]:
            config.ocr.backend = args["ocr"]
        asyncio.run(run_step_by_step(config))
    elif args["simple"]:
        asyncio.run(run_simple(config))
    elif args["single"]:
        asyncio.run(run_single(config))
    else:
        asyncio.run(run_batch(config))


if __name__ == "__main__":
    main()
