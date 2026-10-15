from __future__ import annotations

from typing import Protocol


class OCRBackend(Protocol):
    """Protocol for OCR model backends.

    All backends must support extracting text and structured JSON from images.
    """

    name: str

    async def extract_text(
        self,
        image_bytes: bytes,
        media_type: str,
        prompt: str,
    ) -> tuple[str, int]:
        """Extract raw text/Markdown from an image.

        Returns (extracted_text, tokens_used).
        """
        ...

    async def extract_json(
        self,
        image_bytes: bytes,
        media_type: str,
        prompt: str,
    ) -> tuple[dict | None, int]:
        """Extract structured JSON from an image.

        Returns (parsed_dict, tokens_used).
        """
        ...
