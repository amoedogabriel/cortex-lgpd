"""Enumerações de domínio estritas em conformidade com a LGPD (Lei 13.709/2018) e LAI (Lei 12.527/2011)."""

from enum import StrEnum


class JobStatus(StrEnum):
    """Estados do ciclo de vida de processamento e tarjamento de um documento PDF."""

    RECEIVED = "RECEIVED"
    ANALYZING = "ANALYZING"
    OCR_RUNNING = "OCR_RUNNING"
    PARTIAL_REVIEW_READY = "PARTIAL_REVIEW_READY"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    REDACTING = "REDACTING"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DataCategory(StrEnum):
    """Taxonomia estrita de categorias de dados conforme seção 2 da SPEC.md."""

    PERSONAL_COMMON = "PERSONAL_COMMON"  # Dado Pessoal Comum (Art. 5º, I, LGPD)
    PERSONAL_SENSITIVE = "PERSONAL_SENSITIVE"  # Dado Pessoal Sensível (Art. 5º, II, LGPD)
    FINANCIAL_BANKING = "FINANCIAL_BANKING"  # Dado Financeiro/Bancário (Alto grau de proteção, não sensível)
    MINOR_OR_DEPENDENT = "MINOR_OR_DEPENDENT"  # Crianças, Adolescentes e Dependentes (Art. 14, LGPD)


class ConfidenceLevel(StrEnum):
    """Níveis de confiança da detecção automatizada."""

    HIGH = "HIGH"  # >= 0.85
    MEDIUM = "MEDIUM"  # 0.60 a 0.84
    LOW = "LOW"  # < 0.60


class RedactionActionMode(StrEnum):
    """Modo de ação individual ou recomendado para cada ocorrência detectada."""

    BLACKOUT = "BLACKOUT"  # Tarja Preta Total
    PARTIAL_MASK = "PARTIAL_MASK"  # Mascaramento Parcial (***.XXX.XXX-**)
    KEEP = "KEEP"  # Manter visível sem tarjar


class DocumentProfile(StrEnum):
    """Perfil documental detectado na análise da Página 1 para recomendação automática."""

    PUBLIC_TRANSPARENCY = "PUBLIC_TRANSPARENCY"  # Editais, resultados, listas de aprovados, extratos de contrato
    RESTRICTED_PROCESS = "RESTRICTED_PROCESS"  # Despachos, processos SEI, laudos, folhas, requerimentos internos
