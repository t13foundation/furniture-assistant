from __future__ import annotations

from typing import Any

from config.settings import TracingConfig
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart
from pydantic_ai.models.instrumented import InstrumentationSettings


class ContentSafeInstrumentationSettings(InstrumentationSettings):
    """Redact retry feedback that PydanticAI 0.8.1 otherwise traces unconditionally."""

    def _sanitize_retry_content(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        if self.include_content:
            return messages

        sanitized_messages: list[ModelMessage] = []
        for message in messages:
            if isinstance(message, ModelRequest):
                parts = [
                    RetryPromptPart(
                        content="[redacted]",
                        tool_name=part.tool_name,
                        tool_call_id=part.tool_call_id,
                        timestamp=part.timestamp,
                    )
                    if isinstance(part, RetryPromptPart)
                    else part
                    for part in message.parts
                ]
                sanitized_messages.append(ModelRequest(parts=parts, instructions=message.instructions))
            else:
                sanitized_messages.append(message)
        return sanitized_messages

    def messages_to_otel_events(self, messages: list[ModelMessage]) -> list[Any]:
        return super().messages_to_otel_events(self._sanitize_retry_content(messages))

    def messages_to_otel_messages(self, messages: list[ModelMessage]) -> list[Any]:
        return super().messages_to_otel_messages(self._sanitize_retry_content(messages))


def tracing_instrumentation_settings(config: TracingConfig) -> ContentSafeInstrumentationSettings:
    return ContentSafeInstrumentationSettings(
        include_content=config.include_content,
        include_binary_content=config.include_binary_content,
    )
