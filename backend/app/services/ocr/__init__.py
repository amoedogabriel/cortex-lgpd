"""Pacote de OCR acelerado via ONNX Runtime (`RapidOCR` / PaddleOCR)."""

from app.services.ocr.ocr_engine import (
    FALLBACK_CONFIDENCE_THRESHOLD,
    FALLBACK_OCR_DPI,
    PRIMARY_OCR_DPI,
    OCRPageResult,
    OCRTokenSpan,
    run_ocr_on_page,
)

__all__ = [
    "PRIMARY_OCR_DPI",
    "FALLBACK_OCR_DPI",
    "FALLBACK_CONFIDENCE_THRESHOLD",
    "OCRTokenSpan",
    "OCRPageResult",
    "run_ocr_on_page",
]
