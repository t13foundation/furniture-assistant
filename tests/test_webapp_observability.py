from __future__ import annotations

import subprocess
import sys


def test_pydantic_ai_traces_are_emitted_only_when_enabled():
    code = """
import asyncio
from types import SimpleNamespace
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from src.webapp.observability import configure_tracing

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)
def config(enabled):
    return SimpleNamespace(
        tracing=SimpleNamespace(enabled=enabled, backend=\"phoenix\", include_binary_content=False)
    )

async def run_agent():
    await Agent(TestModel(), output_type=str).run(\"hello\")

configure_tracing(config(False))
asyncio.run(run_agent())
disabled_spans = exporter.get_finished_spans()
assert not disabled_spans
configure_tracing(config(True))
asyncio.run(run_agent())
assert any(span.name == \"agent run\" for span in exporter.get_finished_spans())
"""
    subprocess.run([sys.executable, "-c", code], check=True)
