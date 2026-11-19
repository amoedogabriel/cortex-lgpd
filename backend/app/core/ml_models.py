"""Gerenciador Singleton de Pre-warming de Modelos de ML (OCR ONNX + spaCy NLP) em RAM."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


class MLModelRegistry:
    """
    Singleton thread-safe que carrega e mantém residentes em RAM:
    1. O motor de OCR baseado em ONNX Runtime (`RapidOCR` / PaddleOCR via `onnxruntime`)
    2. O modelo NLP `spaCy` (`pt_core_news_lg`, com fallback gracioso para `pt_core_news_sm` ou `spacy.blank('pt')`
       caso o pacote de pesos grandes ainda não tenha sido baixado no ambiente local).

    Elimina o cold start a cada requisição ou página processada.
    """

    _instance: MLModelRegistry | None = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> MLModelRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    instance._ocr_engine = None
                    instance._spacy_nlp = None
                    instance._spacy_model_name = "unloaded"
                    instance._init_lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    @property
    def is_warmed_up(self) -> bool:
        return bool(self._initialized and self._ocr_engine is not None and self._spacy_nlp is not None)

    @property
    def spacy_model_name(self) -> str:
        return self._spacy_model_name

    def warmup(self, *, load_ocr: bool = True, load_spacy: bool = True) -> dict[str, Any]:
        """
        Carrega os modelos em memória RAM e executa uma inferência sintética (dummy pass)
        para alocar os tensores e grafos do ONNX Runtime antecipadamente.
        """
        with self._init_lock:
            start_total = time.perf_counter()
            metrics: dict[str, Any] = {}

            if load_ocr and self._ocr_engine is None:
                t0 = time.perf_counter()
                self._ocr_engine = self._initialize_ocr_engine()
                metrics["ocr_warmup_ms"] = round((time.perf_counter() - t0) * 1000, 2)

            if load_spacy and self._spacy_nlp is None:
                t1 = time.perf_counter()
                self._spacy_nlp = self._initialize_spacy_pipeline()
                metrics["spacy_warmup_ms"] = round((time.perf_counter() - t1) * 1000, 2)
                metrics["spacy_model"] = self._spacy_model_name

            self._initialized = (self._ocr_engine is not None or not load_ocr) and (
                self._spacy_nlp is not None or not load_spacy
            )
            metrics["total_warmup_ms"] = round((time.perf_counter() - start_total) * 1000, 2)

            logger.info(
                "Pre-warming de modelos de ML concluído e residente em RAM",
                extra={"warmup_metrics": metrics},
            )
            return metrics

    def _initialize_ocr_engine(self) -> Any:
        """
        Inicializa o RapidOCR (PaddleOCR convertido para ONNX Runtime) otimizado para
        inferência em CPU (`det_limit_type='max'`, `det_limit_side_len=512`) e executa
        dummy inference com texto real para pré-aquecer TANTO o detector (`det`) QUANTO
        o reconhecedor (`rec`) na sessão ONNX.
        """
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR(
            use_angle_cls=False,
            det_limit_type="max",
            det_limit_side_len=512,
            det_model_path=None,
        )
        # Cria canvas sintético com caracteres visíveis para forçar o aquecimento de det + rec
        pil_dummy = Image.new("RGB", (320, 80), color=(255, 255, 255))
        draw = ImageDraw.Draw(pil_dummy)
        draw.text((20, 25), "UFAM WARMUP 123456", fill=(0, 0, 0))
        dummy_canvas = np.asarray(pil_dummy, dtype=np.uint8)
        engine(dummy_canvas)
        return engine

    def _initialize_spacy_pipeline(self) -> Any:
        """Carrega o modelo spaCy pt_core_news_lg (com fallback determinístico caso ausente no host)."""
        import spacy

        for candidate in ("pt_core_news_lg", "pt_core_news_md", "pt_core_news_sm"):
            try:
                nlp = spacy.load(candidate, disable=["lemmatizer", "textcat"])
                self._spacy_model_name = candidate
                _ = nlp("Universidade Federal do Amazonas")
                return nlp
            except OSError:
                continue

        # Fallback seguro caso nenhum pacote externo tenha sido baixado ainda no host local
        nlp = spacy.blank("pt")
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer")
        self._spacy_model_name = "pt_blank_fallback"
        _ = nlp("Universidade Federal do Amazonas")
        logger.warning(
            "Pacote pt_core_news_lg não encontrado localmente; utilizando spacy.blank('pt') como fallback."
        )
        return nlp

    def get_ocr_engine(self) -> Any:
        """Retorna a instância residente do motor RapidOCR (aquecendo se necessário)."""
        if self._ocr_engine is None:
            self.warmup(load_ocr=True, load_spacy=False)
        return self._ocr_engine

    def get_spacy_nlp(self) -> Any:
        """Retorna a instância residente do pipeline spaCy (aquecendo se necessário)."""
        if self._spacy_nlp is None:
            self.warmup(load_ocr=False, load_spacy=True)
        return self._spacy_nlp


ml_registry = MLModelRegistry()
