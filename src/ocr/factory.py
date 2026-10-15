"""Factory for creating OCR backend instances from config."""

from __future__ import annotations

from config.settings import AppConfig


def create_ocr_backend(config: AppConfig):
    """Create the appropriate OCR backend based on config.

    Returns one of: VLMBackend, OlmOCRBackend, GlmOCRBackend.
    """
    backend_type = config.ocr.backend

    if backend_type == "olmocr":
        from src.ocr.olmocr_backend import OlmOCRBackend

        return OlmOCRBackend(
            api_base=config.ocr.api_base or config.api.base_url,
            model_name=config.ocr.model_name or "allenai/olmOCR-2-7B-1025-FP8",
            api_key=config.ocr.api_key or config.api.api_key,
        )

    if backend_type == "glm-ocr":
        from src.ocr.glm_ocr_backend import GlmOCRBackend

        return GlmOCRBackend(
            mode=config.ocr.mode,
            api_base=config.ocr.api_base or config.api.base_url,
            api_key=config.ocr.api_key or config.api.api_key,
            model_name=config.ocr.model_name or "glm-ocr",
        )

    # Default: VLM backend
    from src.ocr.vlm_backend import VLMBackend

    return VLMBackend(
        api_base=config.api.base_url,
        model_name=config.models.orchestrator,
        api_key=config.api.api_key,
    )
