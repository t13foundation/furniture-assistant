from __future__ import annotations

import asyncio
import os
import sys
import types
from collections.abc import Callable
from typing import Any

from src.ocr.glm_ocr_backend import GlmOCRBackend


class LazyGLMOCRModule(types.ModuleType):
    def __init__(self, parse: Callable[[str], Any]) -> None:
        super().__init__("glmocr")
        self._parse = parse

    def __getattr__(self, name: str) -> Any:
        if name == "parse":
            return self._parse
        raise AttributeError(name)


def test_glm_ocr_sdk_resolves_lazy_parse_export(monkeypatch: Any) -> None:
    calls: list[tuple[str, bytes]] = []

    def parse(path: str) -> types.SimpleNamespace:
        with open(path, "rb") as image_file:
            calls.append((path, image_file.read()))
        return types.SimpleNamespace(json_result="recognized text")

    monkeypatch.setitem(sys.modules, "glmocr", LazyGLMOCRModule(parse))

    result = asyncio.run(GlmOCRBackend(mode="sdk")._call_sdk(b"image bytes"))

    assert result == ("recognized text", 0)
    assert len(calls) == 1
    assert calls[0][1] == b"image bytes"
    assert not os.path.exists(calls[0][0])
