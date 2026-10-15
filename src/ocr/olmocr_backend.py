"""OLMOCR backend — Allen AI's 7B document OCR model.

Converts document images to clean Markdown via LM Studio's local API.

Deployment: load the OLMOCR model in LM Studio and enable its local server on
http://localhost:1234/v1.
"""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

OLMOCR_TEXT_PROMPT = "Convert this page to text, preserving tables as Markdown."

OLMOCR_JSON_PROMPT = """\
Analyze this section of a furniture drawing and extract data.
Return ONLY valid JSON — no markdown fences, no explanation.

{category_instructions}

Previously found: {known_fields}

Required fields (use null if not visible):
- project_name: {{"value": "original", "value_eng": "english"}}
- client: {{"value": "original", "value_eng": "english"}}
- cabinet_id: {{"value": "original", "value_eng": "english"}}
- scale: {{"value": "original", "value_eng": "english"}}
- units: {{"value": "original", "value_eng": "english"}}
- panels: [list of {"name": "...", "length_mm": N, "width_mm": N, "thickness_mm": N, "board_type": "...", "qty": N}]
- hardware: [list of {"name": "...", "code": "...", "qty": N}]"""


class OlmOCRBackend:
    """OLMOCR backend — specialized for document text linearization.

    OLMOCR excels at converting document images to clean Markdown with
    preserved tables, equations, and natural reading order.
    """

    def __init__(
        self,
        api_base: str = "http://localhost:1234/v1",
        model_name: str = "allenai/olmOCR-2-7B-1025-FP8",
        api_key: str = "not-needed",
    ) -> None:
        self.name = "olmocr"
        self.api_base = api_base
        self.model_name = model_name
        self.api_key = api_key

    async def _call_api(
        self,
        prompt: str,
        image_bytes: bytes,
        media_type: str,
        max_tokens: int = 4096,
    ) -> tuple[str, int]:
        """Call the OLMOCR model through LM Studio's OpenAI-compatible API."""
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
        """Extract raw Markdown text from document image."""
        prompt = prompt or OLMOCR_TEXT_PROMPT
        content, tokens = await self._call_api(prompt, image_bytes, media_type)
        return content, tokens

    async def extract_json(
        self,
        image_bytes: bytes,
        media_type: str,
        prompt: str,
    ) -> tuple[dict | None, int]:
        """Extract structured JSON from document image."""
        content, tokens = await self._call_api(prompt, image_bytes, media_type)
        parsed = self._parse_json(content)
        if parsed is None:
            logger.warning(f"  OLMOCR: could not parse JSON from: {content[:300]}")
        return parsed, tokens
