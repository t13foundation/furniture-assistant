"""Tests for token estimation, budget tracking, and metrics."""

from __future__ import annotations

import base64

import pytest
from src.pipeline.tokens import (
    ContextOverflowError,
    DrawingMetrics,
    PassMetrics,
    SchemaOverhead,
    TokenBudget,
    estimate_image_tokens,
    estimate_text_tokens,
    get_context_window,
    measure_schema_overhead,
)


class TestTokenEstimation:
    def test_text_tokens_non_empty(self):
        tokens = estimate_text_tokens("Hello, world!")
        assert tokens > 0

    def test_text_tokens_empty(self):
        assert estimate_text_tokens("") == 0

    def test_text_tokens_longer_text_more_tokens(self):
        short = estimate_text_tokens("Hi")
        long = estimate_text_tokens("A" * 1000)
        assert long > short

    def test_image_tokens_from_base64(self):
        fake_b64 = base64.b64encode(b"\x00" * 1024).decode()
        tokens = estimate_image_tokens(fake_b64)
        assert tokens > 0

    def test_image_tokens_larger_image_more_tokens(self):
        small = estimate_image_tokens(base64.b64encode(b"\x00" * 1024).decode())
        large = estimate_image_tokens(base64.b64encode(b"\x00" * 10240).decode())
        assert large > small


class TestContextWindow:
    def test_known_model(self):
        assert get_context_window("qwen3.6-35b") == 262_144

    def test_model_with_prefix(self):
        assert get_context_window("mlx-community/Qwen3.6-35B-A3B-4bit") == 262_144

    def test_unknown_model_returns_default(self):
        assert get_context_window("unknown-model") == 131_072

    def test_override_takes_precedence(self):
        assert get_context_window("qwen3.6-35b", override=8192) == 8192


class TestTokenBudget:
    def test_initial_state(self):
        budget = TokenBudget("qwen3.6-35b")
        assert budget.context_window == 262_144
        assert budget.max_input_tokens == 262_144 - 4096
        assert budget.estimated_tokens == 0
        assert budget.remaining_tokens == budget.max_input_tokens
        assert budget.usage_pct == 0.0

    def test_log_text_accumulates(self):
        budget = TokenBudget("qwen3.6-35b")
        budget.log_text("prompt1", "Hello")
        budget.log_text("prompt2", "World")
        assert budget.estimated_tokens > 0

    def test_log_image_accumulates(self):
        budget = TokenBudget("qwen3.6-35b")
        b64 = base64.b64encode(b"\x00" * 5000).decode()
        budget.log_image("overview", b64)
        assert budget.estimated_tokens > 0
        assert budget.estimated_image_tokens > 0

    def test_would_exceed_with_large_estimate(self):
        budget = TokenBudget("qwen3.6-35b", context_window=100)
        budget.log_text("big", "A" * 1000)
        assert budget.would_exceed()

    def test_would_not_exceed_small_estimate(self):
        budget = TokenBudget("qwen3.6-35b")
        budget.log_text("tiny", "Hi")
        assert not budget.would_exceed()

    def test_record_actual_usage(self):
        budget = TokenBudget("qwen3.6-35b")
        budget.record_actual(input_tokens=5000, output_tokens=200)
        assert budget.actual_input_tokens == 5000
        assert budget.actual_output_tokens == 200
        assert budget.peak_input_tokens == 5000
        assert budget.request_count == 1

    def test_record_actual_accumulates_usage(self):
        budget = TokenBudget("qwen3.6-35b")
        budget.record_actual(input_tokens=5000, output_tokens=200)
        budget.record_actual(input_tokens=8000, output_tokens=300)
        assert budget.actual_input_tokens == 13_000
        assert budget.actual_output_tokens == 500
        assert budget.peak_input_tokens == 8000
        assert budget.request_count == 2

    def test_summary_includes_all_fields(self):
        budget = TokenBudget("qwen3.6-35b")
        s = budget.summary()
        assert "qwen3.6-35b" in s
        assert "262,144" in s
        assert "usage=" in s

    def test_remaining_tokens_clamped_to_zero(self):
        budget = TokenBudget("qwen3.6-35b", context_window=50)
        budget.log_text("overflow", "A" * 10000)
        assert budget.remaining_tokens == 0


class TestContextOverflow:
    def test_check_before_send_raises_on_overflow(self):
        budget = TokenBudget("qwen3.6-35b", context_window=100)
        budget.record_actual(input_tokens=200_000, output_tokens=100)
        with pytest.raises(ContextOverflowError, match="Context window exceeded"):
            budget.check_before_send()

    def test_check_before_send_ok_when_under_limit(self):
        budget = TokenBudget("qwen3.6-35b")
        budget.record_actual(input_tokens=1000, output_tokens=50)
        budget.check_before_send()  # should not raise


class TestToMetrics:
    def test_to_metrics_returns_pass_metrics(self):
        budget = TokenBudget("qwen3.6-35b", pass_name="extraction")
        budget.log_image("overview", base64.b64encode(b"\x00" * 5000).decode())
        budget.record_actual(input_tokens=50_000, output_tokens=2000)
        budget.record_actual(input_tokens=80_000, output_tokens=3000)

        metrics = budget.to_metrics()
        assert isinstance(metrics, PassMetrics)
        assert metrics.pass_name == "extraction"
        assert metrics.request_count == 2
        assert metrics.total_input_tokens == 130_000
        assert metrics.total_output_tokens == 5000
        assert metrics.peak_input_tokens == 80_000
        assert metrics.estimated_image_tokens > 0

    def test_to_metrics_with_overflow(self):
        budget = TokenBudget("qwen3.6-35b", context_window=1000, pass_name="validation")
        budget.record_actual(input_tokens=200_000, output_tokens=100)
        metrics = budget.to_metrics()
        assert metrics.overflow is True
        assert metrics.overflow_tokens == 200_000 - (1000 - 4096)


class TestSchemaOverhead:
    def test_measure_schema_overhead(self):
        overhead = measure_schema_overhead()
        assert isinstance(overhead, SchemaOverhead)
        assert overhead.output_schema_tokens > 0
        assert overhead.tool_schemas_tokens > 0
        assert overhead.system_prompt_tokens > 0
        assert overhead.total_fixed_tokens > 0
        assert len(overhead.per_pass_prompts) == 3
        assert overhead.total_fixed_tokens == (
            overhead.output_schema_tokens + overhead.tool_schemas_tokens + overhead.system_prompt_tokens
        )


class TestDrawingMetrics:
    def test_drawing_metrics_defaults(self):
        m = DrawingMetrics(filename="test.pdf", drawing_type="FORGING")
        assert m.passes == []
        assert m.total_tokens == 0
        assert m.overflow is False
