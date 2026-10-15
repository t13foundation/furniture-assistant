"""GLM-OCR backend — Z.ai's 0.9B multimodal OCR model.

Excellent table/layout recognition. Supports LM Studio API mode and SDK mode
(local Python inference).

Deployment:
    API mode:  load GLM-OCR in LM Studio and enable http://localhost:1234/v1
    SDK mode:  pip install glmocr
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from importlib import import_module

import httpx

logger = logging.getLogger(__name__)

GLM_OCR_TEXT_PROMPT = "OCR this document image. Preserve tables as Markdown."


class GlmOCRBackend:
    """GLM-OCR backend — 0.9B model with excellent table recognition.

    Supports two modes:
    - "api": Uses LM Studio's OpenAI-compatible endpoint
    - "sdk": Uses the glmocr Python package for local inference
    """

    def __init__(
        self,
        mode: str = "api",
        api_base: str = "http://localhost:1234/v1",
        api_key: str = "not-needed",
        model_name: str = "glm-ocr",
    ) -> None:
        self.name = "glm-ocr"
        self.mode = mode
        self.api_base = api_base
        self.api_key = api_key
        self.model_name = model_name

    async def _call_api(
        self,
        prompt: str,
        image_bytes: bytes,
        media_type: str,
        max_tokens: int = 4096,
    ) -> tuple[str, int]:
        """Call the GLM-OCR model via OpenAI-compatible API."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        b64 = base64.b64encode(image_bytes).decode()
        data_uri = f"data:{media_type};base64,{b64}"

        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=payload,
            )
            data = resp.json()

        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        content = data["choices"][0]["message"].get("content", "")
        return content, tokens

    async def _call_sdk(
        self,
        image_bytes: bytes,
    ) -> tuple[str, int]:
        """Use GLM-OCR SDK for local inference."""
        try:
            parse = import_module("glmocr").__dict__["parse"]
        except ImportError:
            msg = "glmocr package not installed. Run: pip install glmocr"
            raise ImportError(msg) from None

        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(image_bytes)
            temp_path = f.name

        try:
            result = await asyncio.to_thread(parse, temp_path)
            text = result.json_result if hasattr(result, "json_result") else str(result)
            return text, 0
        finally:
            Path(temp_path).unlink(missing_ok=True)

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """Extract JSON from model output."""
        text = text.strip()
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

        cleaned = re.sub(r"```json\s*", "", text)
        cleaned = re.sub(r"```\s*", "", cleaned).strip()
        if cleaned.startswith("{"):
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

        match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return None

    async def extract_text(
        self,
        image_bytes: bytes,
        media_type: str,
        prompt: str | None = None,
    ) -> tuple[str, int]:
        """Extract raw Markdown/text from document image."""
        if self.mode == "sdk":
            return await self._call_sdk(image_bytes)

        prompt = prompt or GLM_OCR_TEXT_PROMPT
        content, tokens = await self._call_api(prompt, image_bytes, media_type)
        return content, tokens

    async def extract_json(
        self,
        image_bytes: bytes,
        media_type: str,
        prompt: str,
    ) -> tuple[dict | None, int]:
        """Extract structured JSON from document image."""
        if self.mode == "sdk":
            text, _ = await self._call_sdk(image_bytes)
            parsed = self._parse_json(text)
            return parsed, 0

        content, tokens = await self._call_api(prompt, image_bytes, media_type)
        parsed = self._parse_json(content)
        if parsed is None:
            logger.warning(f"  GLM-OCR: could not parse JSON from: {content[:300]}")
        return parsed, tokens
