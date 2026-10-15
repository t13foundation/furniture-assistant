from __future__ import annotations

import asyncio
import base64
import logging

import httpx
from pydantic_ai.messages import BinaryContent

logger = logging.getLogger(__name__)

# Responses that indicate the vision model failed to process the image
_FAILURE_PHRASES = [
    "unable to view",
    "cannot view",
    "cannot see",
    "unable to analyze",
    "i'm unable",
    "as a text-based",
    "as a language model",
    "i cannot process",
    "no image provided",
]


class VisionClient:
    """Calls a separate vision model (e.g. InternVL3) through LM Studio's API.

    Used by agent tools to delegate image analysis to a dedicated VL model,
    returning text descriptions to the orchestrator instead of raw images.
    """

    def __init__(
        self,
        api_base: str,
        model_name: str,
        api_key: str = "not-needed",
        timeout: int = 180,
        max_retries: int = 3,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._call_count = 0
        self._failure_count = 0

    async def analyze(
        self,
        image_bytes: bytes,
        media_type: str,
        prompt: str,
    ) -> str:
        """Send image + prompt to the vision model with retry logic.

        On failure, retries with simplified prompt and exponential backoff.
        Tracks failure rate and logs warnings when it exceeds 30%.
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        current_prompt = prompt
        current_bytes = image_bytes

        last_response = ""
        for attempt in range(self.max_retries):
            self._call_count += 1

            try:
                b64_current = base64.b64encode(current_bytes).decode()
                image_url = f"data:{media_type};base64,{b64_current}"
                payload = {
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": current_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": image_url},
                                },
                            ],
                        }
                    ],
                    "max_tokens": 4000,
                    "temperature": 0.1,
                }

                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.api_base}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                content = data["choices"][0]["message"].get("content", "")
                usage = data.get("usage", {})
                tokens = usage.get("total_tokens", 0)

                # Check for failure phrases
                if self._is_failure_response(content):
                    self._failure_count += 1
                    logger.warning(
                        f"Vision [{self.model_name}]: attempt {attempt + 1} returned "
                        f"failure phrase ({len(content)} chars). Retrying..."
                    )
                    if attempt < self.max_retries - 1:
                        # Simplify prompt on retry
                        current_prompt = "Read all text visible in this image. Report exact values only."
                        await asyncio.sleep(2**attempt)
                    last_response = content
                    continue

                # Check for empty/very short response
                if len(content.strip()) < 10:
                    self._failure_count += 1
                    logger.warning(
                        f"Vision [{self.model_name}]: attempt {attempt + 1} returned "
                        f"short response ({len(content)} chars). Retrying..."
                    )
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2**attempt)
                    last_response = content
                    continue

                # Success
                logger.debug(
                    f"Vision [{self.model_name}]: {tokens} tokens, "
                    f"{len(content)} chars response (attempt {attempt + 1})"
                )

                self._log_failure_rate()
                return content

            except Exception as e:
                self._failure_count += 1
                logger.warning(f"Vision [{self.model_name}]: attempt {attempt + 1} failed: {type(e).__name__}: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)
                continue

        # All retries exhausted — return best available response
        logger.warning(
            f"Vision [{self.model_name}]: all {self.max_retries} retries exhausted, "
            f"returning last response ({len(last_response)} chars)"
        )
        self._log_failure_rate()
        return last_response

    def _is_failure_response(self, content: str) -> bool:
        """Check if the model response indicates it couldn't process the image."""
        lower = content.lower()
        return any(phrase in lower for phrase in _FAILURE_PHRASES)

    def _log_failure_rate(self) -> None:
        """Log warning if failure rate exceeds 30%."""
        if self._call_count >= 5:
            rate = self._failure_count / self._call_count
            if rate > 0.3:
                logger.warning(
                    f"Vision [{self.model_name}]: high failure rate "
                    f"{rate:.0%} ({self._failure_count}/{self._call_count} calls)"
                )

    async def is_available(self) -> bool:
        """Check if the vision model server is reachable and the model is loaded."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.api_base}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if resp.status_code != 200:
                    return False
                data = resp.json()
                model_ids = [m.get("id", "") for m in data.get("data", [])]
                return any(self.model_name in mid for mid in model_ids)
        except (httpx.HTTPError, Exception) as e:
            logger.warning(f"Vision model health check failed: {e}")
            return False

    def check_available_sync(self) -> bool:
        """Synchronous health check for use during pipeline init."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(
                    f"{self.api_base}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if resp.status_code != 200:
                    return False
                data = resp.json()
                model_ids = [m.get("id", "") for m in data.get("data", [])]
                return any(self.model_name in mid for mid in model_ids)
        except (httpx.HTTPError, Exception) as e:
            logger.warning(f"Vision model health check failed: {e}")
            return False


async def safe_vision_analyze(
    vision: VisionClient | None,
    img_bytes: bytes,
    media_type: str,
    prompt: str,
    fallback_label: str,
) -> tuple[str | None, BinaryContent | None]:
    """Try vision analysis, fall back to raw image on failure.

    Returns:
        (analysis_text, None) on success, or (None, BinaryContent) on failure.
        (None, None) if vision is None (caller should handle raw-image mode).
    """
    if vision is None:
        return None, None

    try:
        analysis = await vision.analyze(img_bytes, media_type, prompt)
        if analysis and len(analysis.strip()) >= 10:
            return analysis, None
        # Vision returned empty/short — try simple OCR fallback
        logger.warning(f"Vision analysis returned short response for '{fallback_label}', trying OCR fallback")
        ocr_text = await _ocr_fallback(vision, img_bytes, media_type)
        if ocr_text:
            return ocr_text, None
        # Both failed — return raw image
        return None, BinaryContent(data=img_bytes, media_type=media_type)
    except Exception as e:
        logger.warning(f"Vision analysis failed for '{fallback_label}': {e}. Falling back to raw image.")
        return None, BinaryContent(data=img_bytes, media_type=media_type)


async def _ocr_fallback(
    vision: VisionClient,
    img_bytes: bytes,
    media_type: str,
) -> str | None:
    """Simple OCR fallback using the same vision model with a basic prompt.

    Sends a simplified "read all text" prompt when the structured prompt fails.
    """
    try:
        return await vision.analyze(
            img_bytes,
            media_type,
            "Read all text visible in this image. List each piece of text on a separate line.",
        )
    except Exception as e:
        logger.debug(f"OCR fallback also failed: {e}")
        return None
