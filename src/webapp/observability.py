from __future__ import annotations

import json
import logging
import os
import re
import threading
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
project_id_var: ContextVar[str | None] = ContextVar("project_id", default=None)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_logging_lock = threading.Lock()
_logging_handler: logging.Handler | None = None
_tracing_lock = threading.Lock()
_tracing_backend: str | None = None
_tracing_client: Any | None = None


def valid_request_id(value: str | None) -> bool:
    return value is not None and _REQUEST_ID_RE.fullmatch(value) is not None


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.project_id = project_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_var.get()),
            "project_id": getattr(record, "project_id", project_id_var.get()),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """Install one application logging handler without replacing uvicorn handlers."""
    global _logging_handler
    with _logging_lock:
        level_name = os.environ.get("FA_LOG_LEVEL", "INFO").upper()
        level = logging.getLevelNamesMapping().get(level_name)
        if not isinstance(level, int):
            level = logging.INFO
        root = logging.getLogger()
        root.setLevel(level)
        if _logging_handler is None:
            _logging_handler = logging.StreamHandler()
            _logging_handler.addFilter(ContextFilter())
            root.addHandler(_logging_handler)
        if os.environ.get("FA_LOG_FORMAT", "text").lower() == "json":
            _logging_handler.setFormatter(JsonFormatter())
        else:
            _logging_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s "
                    "[request_id=%(request_id)s project_id=%(project_id)s] %(message)s"
                )
            )


def configure_tracing(config: Any) -> Any | None:
    """Enable PydanticAI spans and initialize each configured exporter once per process."""
    global _tracing_backend, _tracing_client
    from pydantic_ai import Agent

    if not config.tracing.enabled or config.tracing.backend == "none":
        Agent.instrument_all(False)
        return None
    with _tracing_lock:
        try:
            if _tracing_backend != config.tracing.backend:
                _tracing_client = None
                if config.tracing.backend == "langfuse":
                    from langfuse import Langfuse

                    _tracing_client = Langfuse(should_export_span=lambda span: True)
                _tracing_backend = config.tracing.backend

            from src.telemetry import tracing_instrumentation_settings

            Agent.instrument_all(tracing_instrumentation_settings(config.tracing))
            logging.getLogger(__name__).info("PydanticAI tracing enabled (backend=%s)", config.tracing.backend)
            return _tracing_client
        except Exception:
            logging.getLogger(__name__).exception("PydanticAI tracing initialization failed")
            return None
