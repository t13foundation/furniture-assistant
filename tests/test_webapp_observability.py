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
    async with lifespan(app):
        if enabled == "true":
            await run_agent()

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
