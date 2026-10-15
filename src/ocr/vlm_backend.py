"""VLM backend — uses OpenAI-compatible VL model for direct JSON extraction."""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)


class VLMBackend:
    """LM Studio OpenAI-compatible VL model backend.

    Sends image + prompt directly and expects JSON output.
    """

    def __init__(
        self,
        api_base: str = "http://localhost:1234/v1",
        model_name: str = "qwen3.6-35b",
        api_key: str = "not-needed",
    ) -> None:
        self.name = "vlm"
        self.api_base = api_base
        self.model_name = model_name
        self.api_key = api_key

    async def _call_api(
        self,
        prompt: str,
        image_bytes: bytes,
        media_type: str,
        max_tokens: int = 4000,
    ) -> tuple[str, int]:
        """Call the VL model API. Returns (response_text, total_tokens)."""
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
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
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

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """Extract JSON from model output, handling markdown fences."""
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
        prompt: str,
    ) -> tuple[str, int]:
        """Extract raw text from image via VL model."""
        content, tokens = await self._call_api(prompt, image_bytes, media_type)
        return content, tokens

    async def extract_json(
        self,
        image_bytes: bytes,
        media_type: str,
        prompt: str,
    ) -> tuple[dict | None, int]:
        """Extract structured JSON from image via VL model."""
        content, tokens = await self._call_api(prompt, image_bytes, media_type)
        parsed = self._parse_json(content)
        if parsed is None:
            logger.warning(f"  VLM: could not parse JSON from: {content[:300]}")
        return parsed, tokens
