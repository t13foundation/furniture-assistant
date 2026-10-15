from __future__ import annotations

import subprocess
import sys


def test_tracing_content_config_defaults_safe_and_can_be_opted_in(monkeypatch):
    from config.settings import AppConfig

    monkeypatch.delenv("FA_TRACING_INCLUDE_CONTENT", raising=False)
    monkeypatch.delenv("FA_TRACING_INCLUDE_BINARY_CONTENT", raising=False)
    config = AppConfig.from_yaml("config/default.yaml")
    assert config.tracing.include_content is False
    assert config.tracing.include_binary_content is False

    monkeypatch.setenv("FA_TRACING_INCLUDE_CONTENT", "true")
    monkeypatch.setenv("FA_TRACING_INCLUDE_BINARY_CONTENT", "yes")
    config = AppConfig.from_yaml("config/default.yaml")
    assert config.tracing.include_content is True
    assert config.tracing.include_binary_content is True


def test_exporter_is_initialized_when_backend_changes(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from pydantic_ai import Agent
    from src.webapp import observability

    monkeypatch.setattr(observability, "_tracing_backend", None)
    monkeypatch.setattr(observability, "_tracing_client", None)
    monkeypatch.setattr(Agent, "_instrument_default", False)
    langfuse = Mock()
    monkeypatch.setattr("langfuse.Langfuse", langfuse)

    def config(backend):
        return SimpleNamespace(
            tracing=SimpleNamespace(
                enabled=True,
                backend=backend,
                include_content=False,
                include_binary_content=False,
            )
        )

    observability.configure_tracing(config("phoenix"))
    observability.configure_tracing(config("none"))
    observability.configure_tracing(config("langfuse"))
    langfuse.assert_called_once_with(should_export_span=langfuse.call_args.kwargs["should_export_span"])


def test_pydantic_ai_traces_are_emitted_only_when_enabled():
    code = """
import asyncio
import json
from types import SimpleNamespace
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.test import TestModel
from src.webapp.observability import configure_tracing

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)
def config(enabled):
    return SimpleNamespace(
        tracing=SimpleNamespace(
            enabled=enabled, backend="phoenix", include_content=False, include_binary_content=False
        )
    )

async def run_agent():
    await Agent(TestModel(), output_type=str).run(
        ["PRIVATE_DRAWING_TEXT", BinaryContent(data=b"PRIVATE_IMAGE_BYTES", media_type="image/png")]
    )

configure_tracing(config(False))
asyncio.run(run_agent())
disabled_spans = exporter.get_finished_spans()
assert not disabled_spans
configure_tracing(config(True))
asyncio.run(run_agent())
spans = exporter.get_finished_spans()
assert any(span.name == "agent run" for span in spans)
serialized = json.dumps(
    [
        {
            "attributes": dict(span.attributes),
            "events": [dict(event.attributes) for event in span.events],
        }
        for span in spans
    ],
    default=str,
)
assert "PRIVATE_DRAWING_TEXT" not in serialized
assert "UFJJVkFURV9JTUFHRV9CWVRFUw==" not in serialized
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_retry_validation_content_is_redacted_from_spans():
    code = """
import asyncio
import json
from types import SimpleNamespace
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel
from src.webapp.observability import configure_tracing

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)
config = SimpleNamespace(
    tracing=SimpleNamespace(
        enabled=True, backend="phoenix", include_content=False, include_binary_content=False
    )
)
configure_tracing(config)
agent = Agent(TestModel(custom_output_text="invalid"), output_type=str, retries=1)
attempts = 0
@agent.output_validator
def reject(_output):
    global attempts
    attempts += 1
    if attempts == 1:
        raise ModelRetry("PRIVATE_DRAWING_TEXT")

asyncio.run(agent.run("drawing"))

spans = exporter.get_finished_spans()
assert spans
serialized = json.dumps(
    [
        {
            "attributes": dict(span.attributes),
            "events": [dict(event.attributes) for event in span.events],
        }
        for span in spans
    ],
    default=str,
)
assert "PRIVATE_DRAWING_TEXT" not in serialized, serialized
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_legacy_pipeline_agent_uses_content_safe_instrumentation():
    code = """
import asyncio
import json
from unittest.mock import patch
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.models.test import TestModel
from config.settings import TracingConfig
from src.pipeline.agent import create_summarizing_agent
from src.telemetry import tracing_instrumentation_settings
from src.webapp.observability import configure_tracing

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracing = TracingConfig(enabled=True, backend="phoenix")
configure_tracing(type("Config", (), {"tracing": tracing})())
settings = tracing_instrumentation_settings(tracing)
with patch("src.pipeline.agent.create_model", return_value=TestModel()):
    agent = create_summarizing_agent(
        "test-model",
        instrument=settings,
    )
    asyncio.run(agent.run("PRIVATE_DRAWING_TEXT"))
serialized = json.dumps(
    [
        {"attributes": dict(span.attributes), "events": [dict(event.attributes) for event in span.events]}
        for span in exporter.get_finished_spans()
    ],
    default=str,
)
assert "PRIVATE_DRAWING_TEXT" not in serialized
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_instrumentation_is_reapplied_after_disabled_lifespan():
    code = """
import asyncio
import os
import tempfile
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from src.webapp.api import app, lifespan

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)
os.environ["FA_OUTPUT_DIR"] = tempfile.mkdtemp()
os.environ["FA_TRACING_BACKEND"] = "phoenix"

async def run_agent():
    await Agent(TestModel(), output_type=str).run("hello")

async def run_lifespan(enabled):
    os.environ["FA_TRACING_ENABLED"] = enabled
    previous_instrumentation = Agent._instrument_default
    async with lifespan(app):
        if enabled == "true":
            await run_agent()
    assert Agent._instrument_default is previous_instrumentation
async def main():
    await run_lifespan("true")
    first_count = len(exporter.get_finished_spans())
    assert first_count
    await run_lifespan("false")
    await run_lifespan("true")
    assert len(exporter.get_finished_spans()) > first_count

asyncio.run(main())
"""
    subprocess.run([sys.executable, "-c", code], check=True)
