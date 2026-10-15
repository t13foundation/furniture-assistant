"""Token estimation, context window budget tracking, and metrics collection.

Estimates tokens for text and base64-encoded images before sending to the model,
tracks cumulative usage against the model's context window limit, and produces
structured metrics for reporting.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Known context window sizes (tokens) for common models.
KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "qwen3.6-35b": 262_144,
    "qwen3.5-35b": 262_144,
    "qwen3.5-122b": 262_144,
    "qwen3-vl-8b": 262_144,
    "qwen3-vl-30b": 262_144,
    "qwopus-27b": 131_072,
    "gemma3-27b": 131_072,
    "gemma-4": 131_072,
}

DEFAULT_CONTEXT_WINDOW = 131_072

RESPONSE_RESERVE = 4_096

BASE64_BYTES_PER_TOKEN = 1.25
CHARS_PER_TOKEN = 4.0


class ContextOverflowError(Exception):
    """Raised when accumulated tokens exceed the model's context window."""


def estimate_text_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def estimate_image_tokens(base64_str: str) -> int:
    return math.ceil(len(base64_str) / BASE64_BYTES_PER_TOKEN)


def get_context_window(model_name: str, override: int | None = None) -> int:
    if override is not None:
        return override
    normalized = model_name.lower()
    for key, window in KNOWN_CONTEXT_WINDOWS.items():
        if key in normalized:
            return window
    return DEFAULT_CONTEXT_WINDOW


# ── Schema overhead measurement ──────────────────────────────────────


@dataclass
class SchemaOverhead:
    output_schema_tokens: int
    tool_schemas_tokens: int
    system_prompt_tokens: int
    per_pass_prompts: dict[str, int]
    total_fixed_tokens: int


def measure_schema_overhead() -> SchemaOverhead:
    """Measure the fixed token overhead per request from schemas + prompts.

    Measures:
    - ExtractionResult JSON schema (sent as response_format)
    - Tool parameter schemas (8 tools)
    - System prompts per pass
    """
    from src.pipeline.agent import create_extraction_agent
    from src.pipeline.passes import ALL_PASSES
    from src.schema.extraction import ExtractionResult

    # Output schema — get directly from Pydantic model
    output_schema = ExtractionResult.model_json_schema()
    output_schema_chars = len(json.dumps(output_schema))
    output_schema_tokens = estimate_text_tokens(" " * output_schema_chars)

    # Tool schemas
    agent = create_extraction_agent("measure", "http://localhost:1234/v1")
    tool_schemas_chars = 0
    for tool in agent._function_toolset.tools.values():
        fs = tool.function_schema
        if fs and fs.json_schema:
            tool_schemas_chars += len(json.dumps(fs.json_schema))
    tool_schemas_tokens = estimate_text_tokens(" " * tool_schemas_chars)

    # Per-pass prompts
    per_pass: dict[str, int] = {}
    for pass_config in ALL_PASSES:
        per_pass[pass_config.pass_name] = estimate_text_tokens(pass_config.prompt)

    # Use the longest prompt as the representative fixed prompt cost
    system_prompt_tokens = max(per_pass.values())

    total = output_schema_tokens + tool_schemas_tokens + system_prompt_tokens

    return SchemaOverhead(
        output_schema_tokens=output_schema_tokens,
        tool_schemas_tokens=tool_schemas_tokens,
        system_prompt_tokens=system_prompt_tokens,
        per_pass_prompts=per_pass,
        total_fixed_tokens=total,
    )


# ── Per-pass metrics ─────────────────────────────────────────────────


@dataclass
class FieldDifficulty:
    """Tracks how hard a field was to extract during exploration."""

    tool_calls: int = 0
    readings: int = 0
    has_conflict: bool = False


@dataclass
class ToolCallRecord:
    """Structured record of a single agent tool call."""

    tool_name: str
    args: dict = field(default_factory=dict)
    result_summary: str = ""
    field_referenced: str | None = None


@dataclass
class PassMetrics:
    pass_name: str
    request_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    peak_input_tokens: int = 0
    overflow: bool = False
    overflow_tokens: int = 0
    estimated_image_tokens: int = 0


@dataclass
class DrawingMetrics:
    filename: str
    drawing_type: str
    passes: list[PassMetrics] = field(default_factory=list)
    total_tokens: int = 0
    overflow: bool = False
    context_window: int = 0
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    field_difficulties: dict[str, FieldDifficulty] = field(default_factory=dict)


# ── Token budget tracker ─────────────────────────────────────────────


class TokenBudget:
    """Tracks cumulative token usage against a model's context window."""

    def __init__(
        self,
        model_name: str,
        context_window: int | None = None,
        pass_name: str = "",
    ) -> None:
        self.model_name = model_name
        self.pass_name = pass_name
        self.context_window = get_context_window(model_name, context_window)
        self.max_input_tokens = self.context_window - RESPONSE_RESERVE
        self.estimated_tokens = 0
        self.estimated_image_tokens = 0
        self.actual_input_tokens = 0
        self.actual_output_tokens = 0
        self.peak_input_tokens = 0
        self.request_count = 0
        self._overflow = False

    def log_text(self, label: str, text: str) -> int:
        tokens = estimate_text_tokens(text)
        self.estimated_tokens += tokens
        logger.debug(f"Token estimate [{label}]: ~{tokens} tokens ({len(text)} chars)")
        return tokens

    def log_image(self, label: str, base64_str: str) -> int:
        tokens = estimate_image_tokens(base64_str)
        self.estimated_tokens += tokens
        self.estimated_image_tokens += tokens
        logger.debug(f"Token estimate [{label}]: ~{tokens} tokens ({len(base64_str) // 1024} KB base64)")
        return tokens

    def record_actual(self, input_tokens: int, output_tokens: int) -> None:
        """Accumulate actual token counts from one API response."""
        self.actual_input_tokens += input_tokens
        self.actual_output_tokens += output_tokens
        self.peak_input_tokens = max(self.peak_input_tokens, input_tokens)
        self.request_count += 1
        logger.info(
            f"Token usage (req #{self.request_count}): "
            f"input={input_tokens:,}, output={output_tokens:,}, "
            f"peak={self.peak_input_tokens:,}, "
            f"context_window={self.context_window:,}"
        )
        if input_tokens > self.max_input_tokens:
            overflow_by = input_tokens - self.max_input_tokens
            self._overflow = True
            logger.warning(
                f"Context overflow! input={input_tokens:,} > max={self.max_input_tokens:,} "
                f"(overflow by {overflow_by:,} tokens)"
            )

    def check_before_send(self) -> None:
        """Check if last recorded input tokens exceed context window.

        Raises ContextOverflowError if the model already received more tokens
        than its context window can handle.
        """
        if self._overflow:
            raise ContextOverflowError(
                f"Context window exceeded for {self.model_name}: "
                f"peak_input={self.peak_input_tokens:,} > max={self.max_input_tokens:,} "
                f"(overflow by {self.peak_input_tokens - self.max_input_tokens:,} tokens)"
            )

    def would_exceed(self, additional_tokens: int = 0) -> bool:
        return (self.estimated_tokens + additional_tokens) > self.max_input_tokens

    @property
    def usage_pct(self) -> float:
        return (self.estimated_tokens / self.max_input_tokens) * 100 if self.max_input_tokens else 0

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_input_tokens - self.estimated_tokens)

    def to_metrics(self) -> PassMetrics:
        """Convert budget state to PassMetrics for reporting."""
        return PassMetrics(
            pass_name=self.pass_name,
            request_count=self.request_count,
            total_input_tokens=self.actual_input_tokens,
            total_output_tokens=self.actual_output_tokens,
            peak_input_tokens=self.peak_input_tokens,
            overflow=self._overflow,
            overflow_tokens=max(0, self.peak_input_tokens - self.max_input_tokens),
            estimated_image_tokens=self.estimated_image_tokens,
        )

    def summary(self) -> str:
        return (
            f"Token budget [{self.model_name}]: "
            f"context={self.context_window:,}, "
            f"estimated_input={self.estimated_tokens:,}, "
            f"actual_input={self.actual_input_tokens:,}, "
            f"output={self.actual_output_tokens:,}, "
            f"peak={self.peak_input_tokens:,}, "
            f"remaining={self.remaining_tokens:,}, "
            f"usage={self.usage_pct:.1f}%"
        )
