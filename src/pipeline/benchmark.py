from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from config.settings import AppConfig

from src.pipeline.orchestrator import DrawingPipeline
from src.schema.extraction import ExtractionResult

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkReport:
    results_by_model: dict[str, list[ExtractionResult]]
    timing_by_model: dict[str, float] = field(default_factory=dict)
    token_usage_by_model: dict[str, int] = field(default_factory=dict)


class ModelBenchmarkRunner:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.models = config.models.benchmark

    async def run_all(self, input_dir: str) -> BenchmarkReport:
        results_by_model: dict[str, list[ExtractionResult]] = {}
        timing_by_model: dict[str, float] = {}
        token_usage_by_model: dict[str, int] = {}

        for model_name in self.models:
            logger.info(f"=== Benchmarking model: {model_name} ===")

            import copy

            model_config = copy.deepcopy(self.config)
            model_config.models.orchestrator = model_name
            model_config.models.vision = model_name
            model_config.input_dir = input_dir

            pipeline = DrawingPipeline(model_config)

            start = time.time()
            try:
                results, _metrics = await pipeline.process_directory(input_dir)
            except Exception as e:
                logger.error(f"Model {model_name} failed: {e}")
                results = []
            elapsed = time.time() - start

            results_by_model[model_name] = results
            timing_by_model[model_name] = elapsed
            token_usage_by_model[model_name] = sum(r.tokens_used for r in results)

            logger.info(
                f"Model {model_name}: {len(results)} drawings, "
                f"{elapsed:.1f}s, {token_usage_by_model[model_name]} tokens"
            )

        return BenchmarkReport(
            results_by_model=results_by_model,
            timing_by_model=timing_by_model,
            token_usage_by_model=token_usage_by_model,
        )
