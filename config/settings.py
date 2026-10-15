from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Environment-value converter: a callable (str/int/bool types or a lambda) applied to the raw env string.
EnvConverter = Callable[[str], Any]


@dataclass
class ApiConfig:
    base_url: str = "http://localhost:1234/v1"
    timeout: int = 120
    # LM Studio accepts a placeholder key for its local OpenAI-compatible API.
    api_key: str = "lm-studio"


@dataclass
class ModelConfig:
    orchestrator: str = "qwen3.6-35b"
    vision: str = "qwen3.6-35b"
    vision_api_base: str | None = None
    benchmark: list[str] = field(default_factory=lambda: ["qwen3.6-35b", "qwen3.5-35b"])


@dataclass
class PipelineConfig:
    max_passes: int = 3
    confidence_threshold: str = "MEDIUM"
    max_iterations: int = 20
    retry_attempts: int = 3
    retry_max_wait: int = 30
    validation_max_iterations: int = 25
    context_window: int | None = None
    # Compatibility field; the explore/summarize/validate flow is fixed.
    mode: str = "single"
    # Validation strategy: "confidence-based" or "always-critical"
    validation_mode: str = "confidence-based"


@dataclass
class DocumentConfig:
    overview_max_width: int = 256
    detail_max_px: int = 640
    title_block_max_px: int = 4000
    grid_divisions: int = 10
    pdf_render_scale: float = 2.0
    overview_format: str = "JPEG"
    overview_quality: int = 75
    detail_format: str = "JPEG"
    detail_quality: int = 70
    tile_overlap: float = 0.15


@dataclass
class OutputConfig:
    dir: str = "results"
    format: str = "xlsx"


@dataclass
class OcrConfig:
    backend: str = "vlm"  # "vlm" | "olmocr" | "glm-ocr"
    api_base: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    mode: str = "api"  # "api" | "sdk" (GLM-OCR only)


@dataclass
class TracingConfig:
    enabled: bool = False
    backend: str = "langfuse"  # "langfuse" | "phoenix" | "none"
    include_binary_content: bool = False


@dataclass
class AppConfig:
    api: ApiConfig = field(default_factory=ApiConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    document: DocumentConfig = field(default_factory=DocumentConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)
    input_dir: str = "sample-data"
    cli_args: dict = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path = "config/default.yaml", **overrides: object) -> AppConfig:
        yaml_path = Path(path)
        raw: dict = {}
        if yaml_path.exists():
            with open(yaml_path) as f:
                raw = yaml.safe_load(f) or {}

        api = _build_section(
            raw,
            "api",
            ApiConfig,
            {
                "FA_API_BASE": ("base_url", str),
                "FA_API_TIMEOUT": ("timeout", int),
            },
        )

        models = _build_section(
            raw,
            "models",
            ModelConfig,
            {
                "FA_ORCHESTRATOR_MODEL": ("orchestrator", str),
                "FA_VISION_MODEL": ("vision", str),
                "FA_VISION_API_BASE": ("vision_api_base", str),
            },
        )

        # FA_BENCHMARK_MODELS: comma-separated
        bench_env = os.getenv("FA_BENCHMARK_MODELS")
        if bench_env:
            models.benchmark = [m.strip() for m in bench_env.split(",") if m.strip()]
        elif "models" in raw and "benchmark" in raw["models"]:
            models.benchmark = raw["models"]["benchmark"]

        pipeline = _build_section(
            raw,
            "pipeline",
            PipelineConfig,
            {
                "FA_CONFIDENCE_THRESHOLD": ("confidence_threshold", str),
                "FA_MAX_PASSES": ("max_passes", int),
                "FA_MAX_ITERATIONS": ("max_iterations", int),
                "FA_RETRY_ATTEMPTS": ("retry_attempts", int),
                "FA_CONTEXT_WINDOW": ("context_window", int),
                "FA_PIPELINE_MODE": ("mode", str),
                "FA_VALIDATION_MODE": ("validation_mode", str),
            },
        )

        document = _build_section(raw, "document", DocumentConfig, {})

        output = _build_section(
            raw,
            "output",
            OutputConfig,
            {
                "FA_OUTPUT_DIR": ("dir", str),
                "FA_OUTPUT_FORMAT": ("format", str),
            },
        )

        ocr = _build_section(
            raw,
            "ocr",
            OcrConfig,
            {
                "FA_OCR_BACKEND": ("backend", str),
                "FA_OCR_API_BASE": ("api_base", str),
                "FA_OCR_API_KEY": ("api_key", str),
                "FA_OCR_MODEL": ("model_name", str),
                "FA_OCR_MODE": ("mode", str),
            },
        )

        tracing = _build_section(
            raw,
            "tracing",
            TracingConfig,
            {
                "FA_TRACING_ENABLED": ("enabled", lambda v: v.lower() in ("true", "1", "yes")),
                "FA_TRACING_BACKEND": ("backend", str),
                "FA_TRACING_INCLUDE_BINARY_CONTENT": (
                    "include_binary_content",
                    lambda v: v.lower() in ("true", "1", "yes"),
                ),
            },
        )

        input_dir = os.getenv("FA_INPUT_DIR", raw.get("input_dir", "data"))

        config = cls(
            api=api,
            models=models,
            pipeline=pipeline,
            document=document,
            output=output,
            ocr=ocr,
            tracing=tracing,
            input_dir=input_dir,
        )

        # Apply overrides (highest precedence — from CLI args)
        cli_args_data: Any = overrides.pop("cli_args", None)

        for key, value in overrides.items():
            if value is None:
                continue
            parts = key.split(".", 1)
            if len(parts) == 2:
                section = getattr(config, parts[0])
                setattr(section, parts[1], value)
            else:
                setattr(config, key, value)

        config.cli_args = cli_args_data if isinstance(cli_args_data, dict) else {}

        return config


def _build_section[T](
    raw: dict,
    section_key: str,
    section_cls: type[T],
    env_map: dict[str, tuple[str, EnvConverter]],
) -> T:
    section_data = raw.get(section_key, {})
    fields_ = getattr(section_cls, "__dataclass_fields__", {})
    kwargs = {k: v for k, v in section_data.items() if k in fields_}

    for env_var, (field_name, field_type) in env_map.items():
        env_val = os.getenv(env_var)
        if env_val is not None:
            kwargs[field_name] = field_type(env_val)

    return section_cls(**kwargs)
