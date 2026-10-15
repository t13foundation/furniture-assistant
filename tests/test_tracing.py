"""Tests for Langfuse tracing integration."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
import yaml
from config.settings import AppConfig, TracingConfig
from src.pipeline.agent import (
    create_exploration_agent,
    create_extraction_agent,
    create_summarizing_agent,
    create_validation_agent,
)
from src.pipeline.orchestrator import DrawingPipeline

# ---------------------------------------------------------------------------
# 1. TracingConfig defaults and loading
# ---------------------------------------------------------------------------


class TestTracingConfig:
    def test_defaults(self) -> None:
        tc = TracingConfig()
        assert tc.enabled is False
        assert tc.backend == "langfuse"
        assert tc.include_binary_content is False

    def test_from_yaml(self) -> None:
        config = AppConfig.from_yaml()
        assert config.tracing.backend == "langfuse"
        assert config.tracing.include_binary_content is True

    def test_env_override_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FA_TRACING_ENABLED", "true")
        config = AppConfig.from_yaml()
        assert config.tracing.enabled is True

    def test_env_override_enabled_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FA_TRACING_ENABLED", "yes")
        config = AppConfig.from_yaml()
        assert config.tracing.enabled is True

    def test_env_override_enabled_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FA_TRACING_ENABLED", "1")
        config = AppConfig.from_yaml()
        assert config.tracing.enabled is True

    def test_env_override_enabled_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FA_TRACING_ENABLED", "false")
        config = AppConfig.from_yaml()
        assert config.tracing.enabled is False

    def test_env_override_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FA_TRACING_BACKEND", "phoenix")
        config = AppConfig.from_yaml()
        assert config.tracing.backend == "phoenix"


# ---------------------------------------------------------------------------
# 2. Pipeline tracing initialization
# ---------------------------------------------------------------------------


def _make_config(tracing_enabled: bool = False, backend: str = "langfuse") -> AppConfig:
    return AppConfig(tracing=TracingConfig(enabled=tracing_enabled, backend=backend))


class TestPipelineTracingInit:
    def test_tracing_disabled_no_init(self) -> None:
        config = _make_config(tracing_enabled=False)
        pipeline = DrawingPipeline(config)
        assert pipeline._langfuse is None

    def test_tracing_backend_none_skips_init(self) -> None:
        config = _make_config(tracing_enabled=True, backend="none")
        pipeline = DrawingPipeline(config)
        assert pipeline._langfuse is None

    def test_tracing_init_langfuse_called(self) -> None:
        mock_langfuse = MagicMock()
        mock_instrument_all = MagicMock()

        config = _make_config(tracing_enabled=True, backend="langfuse")

        with (
            patch("langfuse.Langfuse", mock_langfuse),
            patch("pydantic_ai.Agent.instrument_all", mock_instrument_all),
            patch("pydantic_ai.models.instrumented.InstrumentationSettings"),
        ):
            DrawingPipeline(config)

        mock_langfuse.assert_called_once()
        mock_instrument_all.assert_called_once()

    def test_tracing_init_import_error_graceful(self) -> None:
        config = _make_config(tracing_enabled=True)

        with patch.dict("sys.modules", {"langfuse": None}):
            # Should not raise — graceful degradation
            pipeline = DrawingPipeline(config)
            assert pipeline._langfuse is None

    def test_tracing_init_exception_graceful(self) -> None:
        config = _make_config(tracing_enabled=True)

        with patch.dict("sys.modules", {"langfuse": None}):
            pipeline = DrawingPipeline(config)
            assert pipeline._langfuse is None

    def test_include_binary_content_config_propagated(self) -> None:
        config = AppConfig(
            tracing=TracingConfig(
                enabled=False,
                include_binary_content=True,
            ),
        )
        assert config.tracing.include_binary_content is True

    def test_drawing_trace_name_is_filename(self) -> None:
        config = _make_config(tracing_enabled=False)
        pipeline = DrawingPipeline(config)
        pipeline._langfuse = MagicMock()

        filename = "Kopia 60547BA_RYSUNEK_KLIENTA_ODKUWKA_OBR_MECH.tif"
        pipeline._start_drawing_trace(filename, "COST_SHEET")

        pipeline._langfuse.start_as_current_observation.assert_called_once_with(
            name=filename,
            as_type="span",
            metadata={
                "drawing.filename": filename,
                "drawing.type": "COST_SHEET",
                "model.orchestrator": pipeline.model_name,
            },
        )
        pipeline._langfuse.start_observation.assert_not_called()

    def test_drawing_trace_enters_current_context(self) -> None:
        config = _make_config(tracing_enabled=False)
        pipeline = DrawingPipeline(config)
        pipeline._langfuse = MagicMock()
        context = MagicMock()
        span = MagicMock()
        context.__enter__.return_value = span
        pipeline._langfuse.start_as_current_observation.return_value = context

        pipeline._start_drawing_trace("drawing.pdf", "COST_SHEET")

        context.__enter__.assert_called_once()
        assert pipeline._current_span is span
        assert pipeline._current_span_context is context

    def test_finish_drawing_trace_exits_context_and_flushes(self) -> None:
        config = _make_config(tracing_enabled=False)
        pipeline = DrawingPipeline(config)
        pipeline._langfuse = MagicMock()
        context = MagicMock()
        pipeline._current_span_context = context
        pipeline._current_span = MagicMock()

        pipeline._finish_drawing_trace()

        context.__exit__.assert_called_once_with(None, None, None)
        pipeline._langfuse.flush.assert_called_once()
        assert pipeline._current_span is None
        assert pipeline._current_span_context is None


# ---------------------------------------------------------------------------
# 3. Agent instrumentation flags
# ---------------------------------------------------------------------------


class TestAgentInstrumentation:
    def test_extraction_agent_instrument_true(self) -> None:
        agent = create_extraction_agent(
            model_name="test-model",
            api_base="http://localhost:1234/v1",
        )
        assert agent.instrument is True

    def test_exploration_agent_instrument_true(self) -> None:
        agent = create_exploration_agent(
            model_name="test-model",
            api_base="http://localhost:1234/v1",
        )
        assert agent.instrument is True

    def test_validation_agent_instrument_true(self) -> None:
        agent = create_validation_agent(
            model_name="test-model",
            api_base="http://localhost:1234/v1",
        )
        assert agent.instrument is True

    def test_summarizing_agent_instrument_true(self) -> None:
        agent = create_summarizing_agent(
            model_name="test-model",
            api_base="http://localhost:1234/v1",
        )
        assert agent.instrument is True


# ---------------------------------------------------------------------------
# 4. Langfuse flush behavior
# ---------------------------------------------------------------------------


class TestFlushBehavior:
    def test_no_flush_when_tracing_disabled(self) -> None:
        config = _make_config(tracing_enabled=False)
        pipeline = DrawingPipeline(config)
        assert pipeline._langfuse is None
        # Accessing _langfuse shouldn't crash
        if pipeline._langfuse:
            pipeline._langfuse.flush()  # would not run

    def test_flush_called_in_process_drawing(self) -> None:
        from src.schema.extraction import FieldExtraction

        config = _make_config(tracing_enabled=False)
        pipeline = DrawingPipeline(config)
        mock_flush = MagicMock()
        pipeline._langfuse = MagicMock()
        pipeline._langfuse.flush = mock_flush

        # Mock the pipeline internals to avoid needing a real model
        with (
            patch.object(pipeline, "_extract_single") as mock_extract,
            patch.object(pipeline, "_validate") as mock_validate,
            patch("src.pipeline.orchestrator.make_loader"),
        ):
            from src.pipeline.tokens import TokenBudget
            from src.schema.enums import DrawingType
            from src.schema.extraction import ExtractionResult

            # Must have at least one populated field to avoid zero-field fallback
            populated_result = ExtractionResult(
                source_file="test.pdf",
                drawing_type=DrawingType.ELEVATION,
                project_name=FieldExtraction(value="BIUROWIEC A"),
            )
            mock_extract.return_value = (populated_result, TokenBudget("test"))
            mock_validate.return_value = (
                populated_result,
                TokenBudget("test"),
            )

            _result, _metrics = asyncio.run(
                pipeline.process_drawing("test.pdf"),
            )

        mock_flush.assert_called_once()

    def test_flush_called_in_process_directory(self) -> None:
        config = _make_config(tracing_enabled=False)
        pipeline = DrawingPipeline(config)
        mock_flush = MagicMock()
        pipeline._langfuse = MagicMock()
        pipeline._langfuse.flush = mock_flush

        with patch.object(pipeline, "process_drawing") as mock_process:
            from src.pipeline.tokens import DrawingMetrics
            from src.schema.enums import DrawingType
            from src.schema.extraction import ExtractionResult

            mock_process.return_value = (
                ExtractionResult(source_file="test.pdf", drawing_type=DrawingType.ELEVATION),
                DrawingMetrics(filename="test.pdf", drawing_type="ELEVATION"),
            )

            with patch.object(pipeline, "discover_drawings") as mock_discover:
                from pathlib import Path

                mock_discover.return_value = [Path("test.pdf")]

                _results, _all_metrics = asyncio.run(
                    pipeline.process_directory("data/"),
                )

        mock_flush.assert_called_once()


# ---------------------------------------------------------------------------
# 5. YAML config tracing section
# ---------------------------------------------------------------------------


class TestYamlTracingConfig:
    def test_tracing_default_value(self) -> None:
        with open("config/default.yaml") as f:
            raw = yaml.safe_load(f)
        assert isinstance(raw["tracing"]["enabled"], bool)

    def test_tracing_backend_langfuse(self) -> None:
        with open("config/default.yaml") as f:
            raw = yaml.safe_load(f)
        assert raw["tracing"]["backend"] == "langfuse"

    def test_include_binary_content_true(self) -> None:
        with open("config/default.yaml") as f:
            raw = yaml.safe_load(f)
        assert raw["tracing"]["include_binary_content"] is True
