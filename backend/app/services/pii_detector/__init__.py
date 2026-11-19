"""
Pacote do Motor de Identificação de Dados Pessoais de Alta Performance (`pii_detector`).
Combina Regex pré-compilados, validação matemática de dígitos verificadores,
processamento em lote com `spaCy` (`nlp.pipe` + `EntityRuler`) e filtro contextual UFAM.
"""

from app.services.pii_detector.context_filter import (
    SEI_CONTEXT_WINDOW_CHARS,
    is_false_positive_institutional_text,
    is_sei_process_linked_to_natural_person,
    should_suppress_detection,
)
from app.services.pii_detector.entity_mapper import (
    DetectedPIIEntity,
    compute_confidence_level,
    compute_entity_group_id,
    detect_pii_in_pages,
    detect_pii_in_single_page,
)
from app.services.pii_detector.nlp_analyzer import (
    UFAM_ENTITY_RULER_PATTERNS,
    RawTextSpanDetection,
    analyze_pages_batch,
    ensure_ufam_entity_ruler_configured,
)
from app.services.pii_detector.regex_patterns import COMPILED_REGEX_SPECS, RegexPatternSpec
from app.services.pii_detector.validators import (
    UFAM_INSTITUTIONAL_CNPJS,
    is_ufam_institutional_cnpj,
    strip_non_digits,
    validate_cnh,
    validate_cnpj,
    validate_cpf,
    validate_pis_pasep_nit,
    validate_titulo_eleitor,
)

__all__ = [
    "UFAM_INSTITUTIONAL_CNPJS",
    "strip_non_digits",
    "validate_cpf",
    "validate_cnpj",
    "is_ufam_institutional_cnpj",
    "validate_cnh",
    "validate_pis_pasep_nit",
    "validate_titulo_eleitor",
    "RegexPatternSpec",
    "COMPILED_REGEX_SPECS",
    "SEI_CONTEXT_WINDOW_CHARS",
    "is_false_positive_institutional_text",
    "is_sei_process_linked_to_natural_person",
    "should_suppress_detection",
    "UFAM_ENTITY_RULER_PATTERNS",
    "RawTextSpanDetection",
    "ensure_ufam_entity_ruler_configured",
    "analyze_pages_batch",
    "DetectedPIIEntity",
    "compute_confidence_level",
    "compute_entity_group_id",
    "detect_pii_in_pages",
    "detect_pii_in_single_page",
]
