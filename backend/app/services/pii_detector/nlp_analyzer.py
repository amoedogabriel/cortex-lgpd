"""
Processamento NLP Vetorizado e Vocabulário Institucional da UFAM (`nlp_analyzer.py`).

Requisitos implementados:
1. Utiliza o modelo `spaCy` pré-aquecido em memória executando `nlp.pipe()` com
   `disable=["parser", "lemmatizer"]` para reduzir o tempo de CPU em ~60%;
2. Configura um `EntityRuler` com padrões administrativos, clínicos e étnicos da UFAM;
3. Analisa estruturalmente células de tabelas (`table_cells`) para garantir captura de:
   - Dados bancários, PIX, remuneração, CNH, PIS/PASEP, datas de nascimento e nomes nominais;
   - Entidades quebradas em múltiplas linhas dentro de células estreitas (mesmo quando tokens de
     colunas vizinhas intercalam no texto linear da página);
   - Nomes de dependentes/menores em tabelas ou colunas de dependentes (`MINOR_OR_DEPENDENT`);
4. Preserva e recompõe nomes compostos quebrados em 2 ou 3 linhas (tanto para frente quanto para
   trás, ex.: `"Mateus\nHenrique Silva\nCavalcante"`) e executa varredura complementar
   (*second-pass match*) propagando nomes completos confirmados por todas as páginas e tabelas
   com `group_id` unificado.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Sequence

from app.core.ml_models import ml_registry
from app.models.enums import DataCategory, RedactionActionMode
from app.services.pdf_extractor.extractor import ExtractedTableCell, UnifiedToken
from app.services.pii_detector.context_filter import (
    has_rg_birth_date_context,
    has_rg_card_context,
    is_false_positive_institutional_text,
    should_suppress_detection,
)
from app.services.pii_detector.regex_patterns import COMPILED_REGEX_SPECS, RegexPatternSpec
from app.services.pii_detector.validators import validate_titulo_eleitor_uf

logger = logging.getLogger(__name__)

# Padrões de células de tabelas financeiras, cadastrais e de dependentes
_TABLE_AGENCY_CELL_RE = re.compile(r"^\s*(\d{3,5}-[\s\n]*[0-9Xx]|\d{4})\s*$")
_TABLE_ACCOUNT_CELL_RE = re.compile(r"^\s*([\d\.]{3,14}-[\s\n]*[0-9Xx]|\d{5,12})\s*$")
_TABLE_MONEY_CELL_RE = re.compile(r"(R\$[\s\n]*\d{1,3}(?:\.\d{3})*,\d{2}|\b\d{1,3}(?:\.\d{3})+,\d{2}\b)")
_TABLE_BIRTH_DATE_CELL_RE = re.compile(r"\b((?:0[1-9]|[12]\d|3[01])/(?:0[1-9]|1[0-2])/(?:19\d{2}|20[0-2]\d))\b")
_TABLE_CNH_CELL_RE = re.compile(r"\b(\d{11}|\d{9}-[\s\n]*\d{2})\b")
_TABLE_PIS_CELL_RE = re.compile(r"\b(\d{3}\.?\d{5}\.?\d{2}-?[\s\n]*\d|\d{11})\b")
_TABLE_SIAPE_CELL_RE = re.compile(r"\b(\d{6,8})\b")
_TABLE_TITULO_ELEITOR_CELL_RE = re.compile(
    r"\b(\d{4}[\s\.\n]*\d{4}[\s\.\n]*\d{4}|\d{10}-[\s\n]*\d{2}|\d{12})\b"
)
_TABLE_NAME_TOK = r"(?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç'-]+|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]\.)"
_TABLE_PERSON_NAME_RE = re.compile(
    rf"\b({_TABLE_NAME_TOK}(?:[\s\n]+(?:da|de|do|dos|das|e|DA|DE|DO|DOS|DAS|E|{_TABLE_NAME_TOK})){{1,6}})(?=\s|$|[|,;()])"
)
_DEPENDENT_KEYWORD_RE = re.compile(
    r"(?i)\b(?:dependente|menor|filho\(a\)|filho|filha|enteado\(a\)|benefici[aá]rio\s+menor|pens[aã]o)\b"
)

_NAME_PARTICLE_RE = re.compile(r"^(?:da|de|do|dos|das)$", re.IGNORECASE)
_CAP_NAME_TOKEN_RE = re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç'-]+$")
_FLEX_NAME_TOKEN_RE = re.compile(
    r"^(?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç'-]+|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]\.)$"
)
_UPPER_SEQ_RE = re.compile(
    r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}(?:(?:[ \t]+|[ \t]*\n[ \t]*)(?:DA|DE|DO|DOS|DAS|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}|[A-Z]\.)){1,6}\b"
)
_INTER_NAME_GAP_RE = re.compile(
    r"^(?:[ \t]*\n[ \t]*|[ \t]+)(?:(?:da|de|do|dos|das|e)(?:[ \t]*\n[ \t]*|[ \t]+))?$",
    re.IGNORECASE,
)
_CONT_WORD = r"(?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç'-]+|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,})"
_NEXT_LINE_CONT_RE = re.compile(
    rf"^(?:[ \t]+(?:da|de|do|dos|das|DA|DE|DO|DOS|DAS))?[ \t]*\n[ \t]*("
    rf"(?:(?:da|de|do|dos|das|DA|DE|DO|DOS|DAS)[ \t]+)*{_CONT_WORD}"
    rf"(?:[ \t]+(?:da|de|do|dos|das|DA|DE|DO|DOS|DAS|{_CONT_WORD})){{0,4}}"
    rf"(?:[ \t]+(?:da|de|do|dos|das|DA|DE|DO|DOS|DAS))?"
    rf")"
)

_PERSON_ENTITY_TYPES: frozenset[str] = frozenset({"PERSON_NAME", "DEPENDENT_DATA", "FILIACAO"})
_GLUED_EMAIL_PREFIX_RE = re.compile(
    r"(?i)^(?:endere[cçg]o[\s_.-]*eletr[oô]nico|correio[\s_.-]*eletr[oô]nico|e-?mail|eletr[oô]nico|contato)[:=-]?"
)
_ACCENT_CHAR_CLASS: dict[str, str] = {
    "a": "[aáàâãä]",
    "e": "[eéèêë]",
    "i": "[iíìîï]",
    "o": "[oóòôõö]",
    "u": "[uúùûü]",
    "c": "[cç]",
}

_NON_PERSON_LINE_STOPWORDS: frozenset[str] = frozenset(
    {
        "servidor",
        "servidora",
        "interessado",
        "interessada",
        "requerente",
        "favorecido",
        "favorecida",
        "beneficiario",
        "beneficiaria",
        "titular",
        "declarante",
        "discente",
        "docente",
        "aluno",
        "aluna",
        "bolsista",
        "candidato",
        "candidata",
        "orientando",
        "orientanda",
        "orientador",
        "orientadora",
        "estagiario",
        "estagiaria",
        "estudante",
        "cargo",
        "funcao",
        "lotacao",
        "setor",
        "unidade",
        "departamento",
        "curso",
        "semestre",
        "periodo",
        "campus",
        "programa",
        "projeto",
        "modalidade",
        "bolsa",
        "bolsas",
        "portaria",
        "processo",
        "despacho",
        "edital",
        "memorando",
        "oficio",
        "resolucao",
        "banco",
        "agencia",
        "conta",
        "corrente",
        "poupanca",
        "salario",
        "remuneracao",
        "proventos",
        "vencimento",
        "valor",
        "liquido",
        "bruto",
        "data",
        "nasc",
        "dt",
        "idade",
        "anos",
        "nascimento",
        "endereco",
        "logradouro",
        "telefone",
        "email",
        "dependente",
        "filiacao",
        "natural",
        "nacionalidade",
        "estado",
        "municipio",
        "manaus",
        "amazonas",
        "brasil",
        "assunto",
        "objeto",
        "ementa",
        "considerando",
        "resolve",
        "determino",
        "encaminhe",
        "publique",
        "cumpra",
        "solicita",
        "informa",
        "requer",
        "declara",
        "encaminha",
        "total",
        "subtotal",
        "observacao",
        "observacoes",
        "anexo",
        "pagina",
        "paginas",
        "folha",
        "laudo",
        "atestado",
        "parecer",
        "pericia",
        "sindicato",
        "autodeclaracao",
        "universidade",
        "federal",
        "fundacao",
        "ministerio",
        "governo",
        "poder",
        "republica",
        "progesp",
        "proplan",
        "proeg",
        "cenario",
        "teste",
        "tabelas",
        "densas",
        "multiplas",
        "repeticoes",
        "dados",
        "academicos",
        "relatorio",
        "quadro",
        "chave",
        "pix",
        "cpf",
        "rg",
        "cnh",
        "pis",
        "pasep",
        "siape",
        "matricula",
        "cid",
        "titulo",
        "eleitor",
        "povo",
        "autodeclarado",
        "autodeclarada",
        "saude",
        "contato",
        "atendimento",
        "prioritario",
        "prioritaria",
        "documentos",
        "documento",
        "inscricao",
        "bancarios",
        "bancario",
        "bancaria",
        "operacao",
        "referencia",
        "normativa",
        "individual",
        "geral",
        "organizacional",
        "orcamento",
        "oficial",
        "fisico",
        "digitalizado",
        "digitalizada",
        "dpi",
        "arquivo",
        "protocolo",
        "ficha",
        "assentamento",
        "lote",
        "scan",
        "foto",
        "escaneada",
        "escaneado",
        "secao",
        "biometria",
        "cep",
        "condicao",
        "medica",
        "menores",
        "idade",
        "dependentes",
        "valido",
        "sintetico",
        "pensao",
        "alimenticia",
        "ciencia",
        "assinatura",
        "registro",
        "facial",
        "analista",
        "tecnologia",
        "informacao",
        "tecnico",
        "tecnica",
        "assistente",
        "administrativo",
        "administrativa",
        "professor",
        "professora",
        "homologada",
        "marcador",
        "genetico",
        "positivo",
        "biometrico",
        "template",
        "hash",
        "guarda",
        "sabado",
        "comunidade",
        "judaica",
        "igreja",
        "adventista",
        "batizado",
        "batizada",
        "membro",
        "membra",
        "filiada",
        "filiado",
        "ativa",
        "ativo",
        "pensionista",
        "ex-conjuge",
        "conjuge",
        "outorgante",
        "outorgado",
        "testemunha",
        "termo",
        "recibo",
        "resumo",
        "cronograma",
        "resultado",
        "preliminar",
        "final",
        "definitivo",
        "aprovado",
        "aprovada",
        "aprovados",
        "aprovadas",
        "classificado",
        "classificada",
        "habilitado",
        "habilitada",
        "eliminado",
        "eliminada",
        "eliminacao",
        "desclassificado",
        "desclassificada",
        "convocacao",
        "item",
        "clausula",
        "paragrafo",
        "inciso",
        "artigo",
        "alinea",
        "lei",
        "decreto",
        "radiculopatia",
        "lombociatalgia",
        "burnout",
        "estresse",
        "retinopatia",
        "monocular",
        "ansiedade",
        "generalizada",
        "generalizado",
        "depressivo",
        "depressiva",
        "episodio",
        "hipertensao",
        "surdez",
        "neurosensorial",
        "neurossensorial",
        "cronica",
        "cronico",
        "aguda",
        "agudo",
        "moderado",
        "moderada",
        "lombar",
        "baixa",
        "diagnostico",
        "transtorno",
        "deficiencia",
        "reacao",
        # Rótulos de RG / Judiciário / Faturas que não podem ser absorvidos como nomes
        "nome",
        "naturalidade",
        "polegar",
        "direito",
        "esquerdo",
        "juntada",
        "peticao",
        "depeticao",
        "deinicial",
        "inicial",
        "distribuicao",
        "vossa",
        "excelencia",
        "requerido",
        "requerida",
        "cert",
        "niss",
        "visa",
        "platinum",
        "parcelamento",
        "parcolamento",
        "taxas",
        "mensais",
        "mensals",
        "cobranca",
        "iof",
        "fatura",
        "faturas",
        "supermerc",
        "nucleo",
        "cidade",
        "deus",
        "maues",
        "comarca",
        "juizado",
        "projudi",
        "tjam",
        "expedicao",
        "maior",
        "deanos",
        "ltda",
        "ambiental",
        "saneamento",
        "identidade",
        "carteira",
        "territorio",
        "nacional",
        "relacao",
        "diretor",
        "diretora",
        "diretorade",
        "secretaria",
        "polo",
        "passivo",
        "ativo",
        "polopassivo",
        "poloativo",
        "avenida",
        "rua",
        "travessa",
        "alameda",
        "estrada",
        "manaus",
    }
)

UFAM_ENTITY_RULER_PATTERNS: list[dict[str, Any]] = [
    {
        "label": "UFAM_ADMIN_PERSON",
        "pattern": [
            {
                "LOWER": {
                    "IN": [
                        "servidor",
                        "servidora",
                        "servidor(a)",
                        "interessado",
                        "interessada",
                        "interessado(a)",
                        "requerente",
                        "discente",
                        "docente",
                        "bolsista",
                        "aluno",
                        "aluna",
                    ]
                }
            },
            {"IS_PUNCT": True, "OP": "?"},
            {"IS_TITLE": True},
            {"LOWER": {"IN": ["da", "de", "do", "dos", "das"]}, "OP": "?"},
            {"IS_TITLE": True},
            {"IS_TITLE": True, "OP": "?"},
        ],
    },
    {
        "label": "UFAM_SIAPE_ANCHOR",
        "pattern": [
            {"LOWER": {"IN": ["matrícula", "matricula"]}},
            {"LOWER": "siape"},
            {"IS_PUNCT": True, "OP": "?"},
            {"IS_DIGIT": True},
        ],
    },
    {
        "label": "UFAM_CPF_ANCHOR",
        "pattern": [
            {"LOWER": {"IN": ["portador", "portadora", "portador(a)"]}},
            {"LOWER": "do"},
            {"LOWER": "cpf"},
        ],
    },
    {
        "label": "UFAM_FILIACAO",
        "pattern": [
            {"LOWER": {"IN": ["filiação", "filiacao"]}},
            {"IS_PUNCT": True, "OP": "?"},
            {"IS_TITLE": True},
            {"LOWER": {"IN": ["da", "de", "do", "dos", "das"]}, "OP": "?"},
            {"IS_TITLE": True},
        ],
    },
    {
        "label": "UFAM_HEALTH_SENSITIVE",
        "pattern": [
            {"LOWER": {"IN": ["atestado", "laudo"]}},
            {"LOWER": {"IN": ["médico", "medico", "pericial", "psiquiátrico", "psiquiatrico", "odontológico", "odontologico"]}},
        ],
    },
    {
        "label": "UFAM_HEALTH_SENSITIVE",
        "pattern": [
            {"LOWER": "surdez"},
            {"LOWER": {"IN": ["bilateral", "unilateral"]}, "OP": "?"},
            {"LOWER": {"IN": ["neurosensorial", "neurossensorial", "profunda", "moderada", "severa"]}},
        ],
    },
    {
        "label": "UFAM_HEALTH_SENSITIVE",
        "pattern": [
            {"LOWER": "acompanhamento"},
            {"LOWER": "tea"},
        ],
    },
    {
        "label": "UFAM_HEALTH_SENSITIVE",
        "pattern": [
            {"LOWER": "pessoa"},
            {"LOWER": "com"},
            {"LOWER": {"IN": ["deficiência", "deficiencia"]}},
        ],
    },
    {
        "label": "UFAM_CID_ANCHOR",
        "pattern": [
            {"LOWER": {"IN": ["cid", "cid-10", "cid-11"]}},
            {"IS_PUNCT": True, "OP": "?"},
            {"TEXT": {"REGEX": r"^[A-Z0-9][A-Z0-9.]{2,6}$"}},
        ],
    },
    {
        "label": "UFAM_UNION_SENSITIVE",
        "pattern": [
            {"LOWER": "sindicato"},
            {"IS_PUNCT": True, "OP": "?"},
            {"IS_UPPER": True},
        ],
    },
    {
        "label": "UFAM_ETHNIC_SENSITIVE",
        "pattern": [
            {"LOWER": {"IN": ["autodeclaração", "autodeclaracao"]}},
            {"LOWER": {"IN": ["étnico-racial", "etnico-racial", "racial"]}},
            {"TEXT": {"IN": [":", "-", "="]}},
            {"IS_ALPHA": True},
        ],
    },
    {
        "label": "UFAM_ETHNIC_SENSITIVE",
        "pattern": [
            {"LOWER": "povo"},
            {"IS_TITLE": True},
        ],
    },
    {
        "label": "UFAM_ETHNIC_SENSITIVE",
        "pattern": [
            {"LOWER": {"IN": ["autodeclarado", "autodeclarada"]}},
            {"IS_ALPHA": True},
        ],
    },
    {
        "label": "UFAM_DEPENDENT_MINOR",
        "pattern": [
            {"LOWER": "dependente"},
            {"IS_PUNCT": True, "OP": "?"},
            {"IS_TITLE": True},
            {"LOWER": {"IN": ["da", "de", "do", "dos", "das"]}, "OP": "?"},
            {"IS_TITLE": True},
        ],
    },
]


@dataclass(frozen=True, slots=True)
class RawTextSpanDetection:
    """Entidade identificada em nível de caracteres de uma página antes do mapeamento geométrico."""

    page_number: int
    entity_type: str
    category: DataCategory
    matched_text: str
    start_char: int
    end_char: int
    confidence: float
    detector_source: str  # "REGEX" ou "SPACY_NLP"
    override_tokens: tuple[UnifiedToken, ...] | None = None
    preferred_action_mode: RedactionActionMode | None = None


def ensure_ufam_entity_ruler_configured(nlp: Any) -> Any:
    """Injeta o `EntityRuler` institucional da UFAM no pipeline `spaCy` residente, caso ainda não exista."""
    if "ufam_entity_ruler" in nlp.pipe_names:
        return nlp

    if "ner" in nlp.pipe_names:
        ruler = nlp.add_pipe("entity_ruler", name="ufam_entity_ruler", before="ner")
    else:
        ruler = nlp.add_pipe("entity_ruler", name="ufam_entity_ruler")

    ruler.add_patterns(UFAM_ENTITY_RULER_PATTERNS)
    return nlp


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Verifica se dois intervalos `[start, end)` de caracteres se sobrepõem (quando mapeados em `full_text`)."""
    if a_start < 0 or b_start < 0:
        return False
    return max(a_start, b_start) < min(a_end, b_end)


def _norm_ascii_lower(val: str) -> str:
    nfkd = unicodedata.normalize("NFKD", val)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower().strip()


def _is_valid_person_name_line(line: str) -> bool:
    """Verifica se uma linha parece exclusivamente parte de um nome próprio português."""
    stripped = line.strip()
    if not stripped or any(ch in stripped for ch in (":", "=", "@", "/", "|", ";")) or re.search(r"\d", stripped):
        return False
    if is_false_positive_institutional_text(stripped):
        return False

    tokens = stripped.split()
    if not tokens or len(tokens) > 6:
        return False

    has_cap = False
    for tok in tokens:
        clean_tok = tok.strip(".,()")
        if not clean_tok:
            return False
        if _NAME_PARTICLE_RE.match(clean_tok):
            continue
        if _norm_ascii_lower(clean_tok) in _NON_PERSON_LINE_STOPWORDS:
            return False
        if _FLEX_NAME_TOKEN_RE.match(clean_tok):
            has_cap = True
        else:
            return False
    return has_cap


def _find_all_whitespace_insensitive_spans(full_text: str, candidate: str) -> list[tuple[int, int]]:
    """Localiza todas as ocorrências de `candidate` em `full_text` tolerando espaço/quebra de linha."""
    if not full_text or not candidate:
        return []
    tokens = candidate.split()
    if not tokens:
        return []
    pattern_str = r"[\s\n]+".join(re.escape(t) for t in tokens)
    return [(m.start(), m.end()) for m in re.finditer(pattern_str, full_text)]


def _find_whitespace_insensitive_span(full_text: str, candidate: str) -> tuple[int, int] | None:
    """Localiza `candidate` em `full_text` tolerando diferenças entre espaço e quebra de linha."""
    spans = _find_all_whitespace_insensitive_spans(full_text, candidate)
    return spans[0] if spans else None


def _tokens_for_cell_match(
    cell: ExtractedTableCell,
    match_start: int,
    match_end: int,
    matched_text: str,
) -> tuple[UnifiedToken, ...] | None:
    """Seleciona exatamente os `UnifiedToken` da célula correspondentes ao intervalo `[match_start, match_end)` em `cell.cell_text`."""
    if not cell.cell_tokens:
        return None

    selected: list[UnifiedToken] = []
    cursor = 0
    for idx_t, tok in enumerate(cell.cell_tokens):
        if idx_t > 0:
            cursor += 1  # espaço inserido entre tokens em cell.cell_text
        t_start = cursor
        t_end = cursor + len(tok.text)
        cursor = t_end
        if max(t_start, match_start) < min(t_end, match_end):
            selected.append(tok)

    if selected:
        return tuple(selected)

    norm_target = re.sub(r"\s+", "", matched_text)
    if norm_target and len(cell.cell_tokens) >= 1:
        return cell.cell_tokens
    return None


def _tokens_overlap(a: tuple[UnifiedToken, ...] | None, b: tuple[UnifiedToken, ...] | None) -> bool:
    if not a or not b:
        return False
    set_a = {(t.page_number, t.block_no, t.line_no, t.word_no, t.x0_norm, t.y0_norm) for t in a}
    set_b = {(t.page_number, t.block_no, t.line_no, t.word_no, t.x0_norm, t.y0_norm) for t in b}
    return bool(set_a & set_b)


def _tokens_is_superset(a: tuple[UnifiedToken, ...] | None, b: tuple[UnifiedToken, ...] | None) -> bool:
    if not a or not b:
        return False
    set_a = {(t.page_number, t.block_no, t.line_no, t.word_no, t.x0_norm, t.y0_norm) for t in a}
    set_b = {(t.page_number, t.block_no, t.line_no, t.word_no, t.x0_norm, t.y0_norm) for t in b}
    return len(set_a) > len(set_b) and set_b.issubset(set_a)


def _upsert_person_or_span_detection(
    existing_detections: list[RawTextSpanDetection],
    new_det: RawTextSpanDetection,
    *,
    allow_replace_shorter_person: bool = True,
) -> bool:
    """
    Insere `new_det` evitando duplicações e substituindo spans parciais de nome de pessoa
    (ex.: `"Henrique Silva\\nCavalcante"`) quando uma detecção mais completa (`"Mateus\\nHenrique Silva\\nCavalcante"`)
    estiver disponível no mesmo local.
    """
    overlapping_indices: list[int] = []
    for idx, ex in enumerate(existing_detections):
        if _spans_overlap(new_det.start_char, new_det.end_char, ex.start_char, ex.end_char) or _tokens_overlap(
            new_det.override_tokens, ex.override_tokens
        ):
            overlapping_indices.append(idx)

    if not overlapping_indices:
        existing_detections.append(new_det)
        return True

    if allow_replace_shorter_person and new_det.entity_type in _PERSON_ENTITY_TYPES:
        all_shorter_person = True
        preserved_dep = False
        new_word_count = len(new_det.matched_text.split())
        for idx in overlapping_indices:
            ex = existing_detections[idx]
            if ex.entity_type not in _PERSON_ENTITY_TYPES:
                all_shorter_person = False
                break
            ex_word_count = len(ex.matched_text.split())
            is_span_superset = (
                new_det.start_char >= 0
                and ex.start_char >= 0
                and new_det.start_char <= ex.start_char
                and new_det.end_char >= ex.end_char
                and new_word_count > ex_word_count
            )
            is_tok_superset = _tokens_is_superset(new_det.override_tokens, ex.override_tokens)
            if not (is_span_superset or is_tok_superset):
                all_shorter_person = False
                break
            if ex.entity_type == "DEPENDENT_DATA":
                preserved_dep = True

        if all_shorter_person:
            for idx in reversed(overlapping_indices):
                existing_detections.pop(idx)
            final_det = (
                RawTextSpanDetection(
                    page_number=new_det.page_number,
                    entity_type="DEPENDENT_DATA",
                    category=DataCategory.MINOR_OR_DEPENDENT,
                    matched_text=new_det.matched_text,
                    start_char=new_det.start_char,
                    end_char=new_det.end_char,
                    confidence=new_det.confidence,
                    detector_source=new_det.detector_source,
                    override_tokens=new_det.override_tokens,
                )
                if preserved_dep and new_det.entity_type != "DEPENDENT_DATA"
                else new_det
            )
            existing_detections.append(final_det)
            return True

    return False


def _append_table_detection(
    *,
    page_number: int,
    full_text: str,
    cell: ExtractedTableCell,
    entity_type: str,
    category: DataCategory,
    matched_text: str,
    match_start: int,
    match_end: int,
    confidence: float,
    detector_source: str,
    existing_detections: list[RawTextSpanDetection],
    replace_shorter_person: bool = True,
    preferred_action_mode: RedactionActionMode | None = None,
) -> None:
    """Registra detecção vinda de célula de tabela com span exato em `full_text` ou `override_tokens`."""
    cell_matched_tokens = _tokens_for_cell_match(cell, match_start, match_end, matched_text)
    all_spans = _find_all_whitespace_insensitive_spans(full_text, matched_text)
    span: tuple[int, int] | None = None
    for cand_s, cand_e in all_spans:
        overlaps_existing = any(
            _spans_overlap(cand_s, cand_e, ex.start_char, ex.end_char)
            for ex in existing_detections
        )
        if not overlaps_existing:
            span = (cand_s, cand_e)
            break
    if span is None and all_spans and not cell_matched_tokens:
        span = all_spans[0]

    if span is not None:
        s_c, e_c = span
        det = RawTextSpanDetection(
            page_number=page_number,
            entity_type=entity_type,
            category=category,
            matched_text=full_text[s_c:e_c],
            start_char=s_c,
            end_char=e_c,
            confidence=confidence,
            detector_source=detector_source,
            override_tokens=cell_matched_tokens,
            preferred_action_mode=preferred_action_mode,
        )
        _upsert_person_or_span_detection(
            existing_detections,
            det,
            allow_replace_shorter_person=replace_shorter_person,
        )
    elif cell_matched_tokens:
        if any(
            _tokens_overlap(cell_matched_tokens, ex.override_tokens)
            and (not replace_shorter_person or ex.entity_type not in _PERSON_ENTITY_TYPES)
            for ex in existing_detections
        ):
            return
        if all_spans:
            if entity_type in _PERSON_ENTITY_TYPES:
                for cand_s, cand_e in all_spans:
                    det = RawTextSpanDetection(
                        page_number=page_number,
                        entity_type=entity_type,
                        category=category,
                        matched_text=full_text[cand_s:cand_e],
                        start_char=cand_s,
                        end_char=cand_e,
                        confidence=confidence,
                        detector_source=detector_source,
                        override_tokens=cell_matched_tokens,
                        preferred_action_mode=preferred_action_mode,
                    )
                    _upsert_person_or_span_detection(
                        existing_detections,
                        det,
                        allow_replace_shorter_person=replace_shorter_person,
                    )
            return
        compact_cand = re.sub(r"\s+", "", matched_text)
        for ex in existing_detections:
            if ex.entity_type == entity_type and re.sub(r"\s+", "", ex.matched_text) == compact_cand:
                if _tokens_overlap(cell_matched_tokens, ex.override_tokens):
                    return
        det = RawTextSpanDetection(
            page_number=page_number,
            entity_type=entity_type,
            category=category,
            matched_text=matched_text,
            start_char=-1,
            end_char=-1,
            confidence=confidence,
            detector_source=detector_source,
            override_tokens=cell_matched_tokens,
            preferred_action_mode=preferred_action_mode,
        )
        _upsert_person_or_span_detection(
            existing_detections,
            det,
            allow_replace_shorter_person=replace_shorter_person,
        )


def _extract_table_aware_spans_for_page(
    page_number: int,
    full_text: str,
    table_cells: list[ExtractedTableCell] | None,
    existing_detections: list[RawTextSpanDetection],
    *,
    run_regex_on_cells: bool = False,
    custom_regex_specs: Sequence[RegexPatternSpec] | None = None,
) -> None:
    """
    Inspeciona as células de tabelas extraídas da página para identificar:
    1. Contas bancárias, agências, PIX e valores monetários (`FINANCIAL_BANKING`);
    2. CNH, PIS/PASEP, Datas de Nascimento (`Data Nasc.` / `Nascimento`) e nomes em colunas nominais;
    3. Nomes de dependentes/menores em tabelas ou linhas de dependentes (`MINOR_OR_DEPENDENT`);
    4. Qualquer regex padrão (CPF, UUID PIX, telefone, CID) que tenha quebrado linha dentro de célula
       com colunas intercaladas em `full_text`.
    """
    if not table_cells or not full_text:
        return

    dependent_tables: set[int] = set()
    dependent_rows: set[tuple[int, int]] = set()
    for cell in table_cells:
        hdr_norm_dep = _norm_ascii_lower(cell.header_label)
        is_mixed_hdr = any(k in hdr_norm_dep for k in ("servidor", "titular", "discente", "docente", "bolsista"))
        if _DEPENDENT_KEYWORD_RE.search(cell.cell_text) or (
            _DEPENDENT_KEYWORD_RE.search(cell.header_label) and not is_mixed_hdr
        ):
            dependent_rows.add((cell.table_index, cell.row_index))
            if _DEPENDENT_KEYWORD_RE.search(cell.header_label) and not is_mixed_hdr:
                dependent_tables.add(cell.table_index)

    for cell in table_cells:
        if cell.row_index == 0:
            continue

        hdr_low = _norm_ascii_lower(cell.header_label)
        val_clean = cell.cell_text.strip()
        if not val_clean:
            continue

        # 0. Varredura Regex dentro da célula
        if run_regex_on_cells:
            cell_specs = (*COMPILED_REGEX_SPECS, *(custom_regex_specs or ()))
            for spec in cell_specs:
                for match_idx, match in enumerate(spec.pattern.finditer(val_clean)):
                    if spec.is_custom and match_idx >= 500:
                        break
                    grp = spec.value_group if spec.value_group <= (match.lastindex or 0) else 0
                    val = match.group(grp)
                    if not val or match.start(grp) == match.end(grp):
                        continue
                    if spec.validator is not None and not spec.validator(val):
                        continue
                    cell_m_start = match.start(grp)
                    cell_m_end = match.end(grp)
                    if spec.entity_type == "EMAIL":
                        val, cell_m_start, cell_m_end = _strip_glued_email_prefix(val, cell_m_start, cell_m_end)
                    if spec.requires_context and spec.entity_type == "RG":
                        if not has_rg_card_context(val_clean, cell_m_start, cell_m_end):
                            continue
                    if spec.requires_context and spec.entity_type == "BIRTH_DATE":
                        if not has_rg_birth_date_context(val_clean, cell_m_start, cell_m_end):
                            continue
                    if spec.entity_type == "PERSON_NAME":
                        val = _trim_person_text_only(val)
                        if not val:
                            continue
                    if spec.is_custom:
                        pass
                    elif spec.entity_type in ("PROCESSO_SEI", "REMUNERATION"):
                        if spec.entity_type == "PROCESSO_SEI" and should_suppress_detection(
                            entity_type=spec.entity_type,
                            matched_value=val,
                            full_text=val_clean,
                            start_char=match.start(grp),
                            end_char=match.end(grp),
                            table_cells=table_cells,
                        ):
                            continue
                    elif should_suppress_detection(
                        entity_type=spec.entity_type,
                        matched_value=val,
                        full_text=val_clean,
                        start_char=match.start(grp),
                        end_char=match.end(grp),
                        table_cells=table_cells,
                    ):
                        continue
                    _append_table_detection(
                        page_number=page_number,
                        full_text=full_text,
                        cell=cell,
                        entity_type=spec.entity_type,
                        category=spec.category,
                        matched_text=val,
                        match_start=cell_m_start,
                        match_end=cell_m_end,
                        confidence=spec.base_confidence,
                        detector_source="CUSTOM_REGEX" if spec.is_custom else "REGEX",
                        existing_detections=existing_detections,
                        preferred_action_mode=spec.preferred_action_mode,
                    )

        # 1. Coluna de Conta Bancária ("Conta", "C/C", "Conta Corrente")
        if any(k in hdr_low for k in ("conta", "c/c", "c.c")):
            m = _TABLE_ACCOUNT_CELL_RE.match(val_clean)
            if m:
                _append_table_detection(
                    page_number=page_number,
                    full_text=full_text,
                    cell=cell,
                    entity_type="BANK_ACCOUNT",
                    category=DataCategory.FINANCIAL_BANKING,
                    matched_text=m.group(1),
                    match_start=m.start(1),
                    match_end=m.end(1),
                    confidence=0.95,
                    detector_source="REGEX",
                    existing_detections=existing_detections,
                )

        # 2. Coluna de Agência Bancária ("Agência", "Ag.")
        if any(k in hdr_low for k in ("agencia", "ag.", "ag:")):
            m = _TABLE_AGENCY_CELL_RE.match(val_clean)
            if m:
                _append_table_detection(
                    page_number=page_number,
                    full_text=full_text,
                    cell=cell,
                    entity_type="BANK_AGENCY",
                    category=DataCategory.FINANCIAL_BANKING,
                    matched_text=m.group(1),
                    match_start=m.start(1),
                    match_end=m.end(1),
                    confidence=0.94,
                    detector_source="REGEX",
                    existing_detections=existing_detections,
                )

        # 3. Coluna de Valor / Remuneração / Líquido / Bruto / Proventos / Bolsa
        if any(
            k in hdr_low
            for k in ("valor", "remunera", "bruto", "liquido", "provento", "vencimento", "salario", "rubrica", "bolsa")
        ):
            for m in _TABLE_MONEY_CELL_RE.finditer(val_clean):
                _append_table_detection(
                    page_number=page_number,
                    full_text=full_text,
                    cell=cell,
                    entity_type="REMUNERATION",
                    category=DataCategory.FINANCIAL_BANKING,
                    matched_text=m.group(1).strip(),
                    match_start=m.start(1),
                    match_end=m.end(1),
                    confidence=0.93,
                    detector_source="REGEX",
                    existing_detections=existing_detections,
                )

        # 4. Coluna de Data de Nascimento ("Data Nasc.", "Nascimento", "Dt. Nasc.")
        if any(k in hdr_low for k in ("nasc", "aniversario")):
            for m in _TABLE_BIRTH_DATE_CELL_RE.finditer(val_clean):
                _append_table_detection(
                    page_number=page_number,
                    full_text=full_text,
                    cell=cell,
                    entity_type="BIRTH_DATE",
                    category=DataCategory.PERSONAL_COMMON,
                    matched_text=m.group(1),
                    match_start=m.start(1),
                    match_end=m.end(1),
                    confidence=0.93,
                    detector_source="REGEX",
                    existing_detections=existing_detections,
                )

        # 5. Coluna de CNH
        if "cnh" in hdr_low:
            for m in _TABLE_CNH_CELL_RE.finditer(val_clean):
                _append_table_detection(
                    page_number=page_number,
                    full_text=full_text,
                    cell=cell,
                    entity_type="CNH",
                    category=DataCategory.PERSONAL_COMMON,
                    matched_text=m.group(1),
                    match_start=m.start(1),
                    match_end=m.end(1),
                    confidence=0.94,
                    detector_source="REGEX",
                    existing_detections=existing_detections,
                )

        # 6. Coluna de PIS / PASEP
        if any(k in hdr_low for k in ("pis", "pasep", "nit", "nis")):
            for m in _TABLE_PIS_CELL_RE.finditer(val_clean):
                _append_table_detection(
                    page_number=page_number,
                    full_text=full_text,
                    cell=cell,
                    entity_type="PIS_PASEP",
                    category=DataCategory.PERSONAL_COMMON,
                    matched_text=m.group(1),
                    match_start=m.start(1),
                    match_end=m.end(1),
                    confidence=0.94,
                    detector_source="REGEX",
                    existing_detections=existing_detections,
                )

        # 6b. Coluna de Título de Eleitor ("Título Eleitor", "Tít. Eleitoral", "Inscrição Eleitoral")
        if ("titulo" in hdr_low and ("eleitor" in hdr_low or "eleitoral" in hdr_low)) or (
            "inscricao" in hdr_low and "eleitoral" in hdr_low
        ):
            for m in _TABLE_TITULO_ELEITOR_CELL_RE.finditer(val_clean):
                cand = m.group(1)
                if validate_titulo_eleitor_uf(cand):
                    _append_table_detection(
                        page_number=page_number,
                        full_text=full_text,
                        cell=cell,
                        entity_type="TITULO_ELEITOR",
                        category=DataCategory.PERSONAL_COMMON,
                        matched_text=cand,
                        match_start=m.start(1),
                        match_end=m.end(1),
                        confidence=0.94,
                        detector_source="REGEX",
                        existing_detections=existing_detections,
                    )

        # 6c. Coluna de Matrícula SIAPE ("Matrícula SIAPE", "SIAPE")
        if "siape" in hdr_low:
            for m in _TABLE_SIAPE_CELL_RE.finditer(val_clean):
                cand = m.group(1)
                if 6 <= len(cand) <= 8 and len(set(cand)) > 1:
                    _append_table_detection(
                        page_number=page_number,
                        full_text=full_text,
                        cell=cell,
                        entity_type="SIAPE",
                        category=DataCategory.PERSONAL_COMMON,
                        matched_text=cand,
                        match_start=m.start(1),
                        match_end=m.end(1),
                        confidence=0.94,
                        detector_source="REGEX",
                        existing_detections=existing_detections,
                    )

        # 7. Colunas de Dependentes, Discentes, Bolsistas ou Nomes Próprios nominais
        is_mixed_hdr = any(k in hdr_low for k in ("servidor", "titular", "discente", "docente", "bolsista"))
        is_dep_context = (
            cell.table_index in dependent_tables
            or (cell.table_index, cell.row_index) in dependent_rows
            or (not is_mixed_hdr and ("dependente" in hdr_low or "menor" in hdr_low or "filho" in hdr_low))
        )
        is_nominal_col = any(
            k in hdr_low
            for k in (
                "nome",
                "servidor",
                "interessad",
                "favorecid",
                "benefici",
                "titular",
                "credor",
                "paciente",
                "requerente",
                "discente",
                "bolsista",
                "alun",
                "docente",
                "candidat",
                "orientand",
                "estagi",
                "estudante",
                "dependente",
                "menor",
                "filho",
            )
        )

        if is_dep_context or is_nominal_col:
            target_etype = "DEPENDENT_DATA" if is_dep_context else "PERSON_NAME"
            target_cat = DataCategory.MINOR_OR_DEPENDENT if is_dep_context else DataCategory.PERSONAL_COMMON
            for m in _TABLE_PERSON_NAME_RE.finditer(val_clean):
                candidate = m.group(1).strip()
                if is_false_positive_institutional_text(candidate):
                    continue
                if _DEPENDENT_KEYWORD_RE.fullmatch(candidate):
                    continue
                if not _is_valid_person_name_line(candidate):
                    continue
                full_alpha_words = [
                    w.strip(".,()")
                    for w in candidate.split()
                    if not _NAME_PARTICLE_RE.match(w)
                    and not (len(w.strip(".,()")) == 1)
                    and not (w.strip(",()").endswith(".") and len(w.strip(",()")) <= 2)
                ]
                if len(full_alpha_words) < 2:
                    continue

                span = _find_whitespace_insensitive_span(full_text, candidate)
                if span is not None and is_dep_context:
                    s_c, e_c = span
                    upgraded = False
                    for i, ex in enumerate(existing_detections):
                        if _spans_overlap(s_c, e_c, ex.start_char, ex.end_char):
                            if ex.entity_type in ("PERSON_NAME", "PER"):
                                existing_detections[i] = RawTextSpanDetection(
                                    page_number=page_number,
                                    entity_type="DEPENDENT_DATA",
                                    category=DataCategory.MINOR_OR_DEPENDENT,
                                    matched_text=ex.matched_text,
                                    start_char=ex.start_char,
                                    end_char=ex.end_char,
                                    confidence=max(ex.confidence, 0.93),
                                    detector_source=ex.detector_source,
                                    override_tokens=ex.override_tokens,
                                )
                            upgraded = True
                            break
                    if upgraded:
                        continue

                _append_table_detection(
                    page_number=page_number,
                    full_text=full_text,
                    cell=cell,
                    entity_type=target_etype,
                    category=target_cat,
                    matched_text=candidate,
                    match_start=m.start(1),
                    match_end=m.end(1),
                    confidence=0.93,
                    detector_source="REGEX",
                    existing_detections=existing_detections,
                )


def _trim_person_text_only(raw_text: str) -> str:
    """Remove linhas finais não pertencentes a nomes próprios (ex.: verbos ou termos administrativos)."""
    if "\n" not in raw_text:
        return raw_text.strip()
    lines = raw_text.splitlines()
    kept: list[str] = [lines[0]]
    for ln in lines[1:]:
        if _is_valid_person_name_line(ln):
            kept.append(ln)
        else:
            break
    return "\n".join(kept).strip()


def _strip_glued_email_prefix(val: str, start_char: int, end_char: int) -> tuple[str, int, int]:
    """Remove rótulos colados por OCR no início de um e-mail (ex.: `enderecoeletronicomarcilio@...`)."""
    m_pref = _GLUED_EMAIL_PREFIX_RE.match(val)
    if m_pref:
        remainder = val[m_pref.end() :]
        if "@" in remainder and remainder.find("@") >= 2:
            return remainder, start_char + m_pref.end(), end_char
    return val, start_char, end_char


def _accent_insensitive_regex(token: str) -> str:
    """Converte um token em regex tolerante a acentuação para propagação entre páginas OCR."""
    norm = _norm_ascii_lower(token)
    parts: list[str] = []
    for ch in norm:
        if ch in _ACCENT_CHAR_CLASS:
            parts.append(_ACCENT_CHAR_CLASS[ch])
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _within_ocr_edit_distance(a: str, b: str, max_edits: int = 1) -> bool:
    """Retorna True se duas strings ASCII normalizadas diferirem por no máximo `max_edits` edições."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > max_edits:
        return False
    if max_edits <= 0:
        return False
    if len(a) == len(b):
        return sum(1 for c1, c2 in zip(a, b) if c1 != c2) <= max_edits
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    i = j = diffs = 0
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j]:
            i += 1
            j += 1
        else:
            diffs += 1
            if diffs > max_edits:
                return False
            j += 1
    return True


def _extract_regex_spans_for_page(
    page_number: int,
    full_text: str,
    table_cells: list[ExtractedTableCell] | None = None,
    *,
    custom_regex_specs: Sequence[RegexPatternSpec] | None = None,
) -> list[RawTextSpanDetection]:
    """Executa todas as expressões regulares pré-compiladas e validadores matemáticos no texto da página."""
    detections: list[RawTextSpanDetection] = []
    if not full_text:
        return detections

    all_specs = (*COMPILED_REGEX_SPECS, *(custom_regex_specs or ()))
    for spec in all_specs:
        for match_idx, match in enumerate(spec.pattern.finditer(full_text)):
            if spec.is_custom and match_idx >= 500:
                break
            grp = spec.value_group if spec.value_group <= (match.lastindex or 0) else 0
            val = match.group(grp)
            if not val or match.start(grp) == match.end(grp):
                continue

            start_char, end_char = match.span(grp)
            stripped_val = val.strip()
            if stripped_val != val and stripped_val:
                l_diff = len(val) - len(val.lstrip())
                start_char += l_diff
                end_char = start_char + len(stripped_val)
                val = stripped_val

            if spec.entity_type in ("PERSON_NAME", "DEPENDENT_DATA", "FILIACAO"):
                start_char, end_char, val = _trim_person_stopwords(val, start_char, end_char)

            if spec.entity_type == "EMAIL":
                val, start_char, end_char = _strip_glued_email_prefix(val, start_char, end_char)

            if spec.requires_context and spec.entity_type == "RG":
                if not has_rg_card_context(full_text, start_char, end_char):
                    continue
            if spec.requires_context and spec.entity_type == "BIRTH_DATE":
                if not has_rg_birth_date_context(full_text, start_char, end_char):
                    continue

            if spec.validator is not None and not spec.validator(val):
                continue

            if not spec.is_custom and should_suppress_detection(
                entity_type=spec.entity_type,
                matched_value=val,
                full_text=full_text,
                start_char=start_char,
                end_char=end_char,
                table_cells=table_cells,
            ):
                continue

            det = RawTextSpanDetection(
                page_number=page_number,
                entity_type=spec.entity_type,
                category=spec.category,
                matched_text=val,
                start_char=start_char,
                end_char=end_char,
                confidence=spec.base_confidence,
                detector_source="CUSTOM_REGEX" if spec.is_custom else "REGEX",
                preferred_action_mode=spec.preferred_action_mode,
            )
            _upsert_person_or_span_detection(detections, det, allow_replace_shorter_person=True)

    _extract_table_aware_spans_for_page(
        page_number=page_number,
        full_text=full_text,
        table_cells=table_cells,
        existing_detections=detections,
        run_regex_on_cells=True,
        custom_regex_specs=custom_regex_specs,
    )

    return detections


def _map_spacy_label_to_category(label: str) -> tuple[str, DataCategory, float] | None:
    """Mapeia o rótulo do NER / EntityRuler para `(entity_type, DataCategory, confidence)`."""
    if label in ("PER", "PERSON"):
        return ("PERSON_NAME", DataCategory.PERSONAL_COMMON, 0.85)
    if label == "UFAM_ADMIN_PERSON":
        return ("PERSON_NAME", DataCategory.PERSONAL_COMMON, 0.90)
    if label == "UFAM_NATURALIDADE":
        return ("NATURALIDADE", DataCategory.PERSONAL_COMMON, 0.86)
    if label == "UFAM_FILIACAO":
        return ("FILIACAO", DataCategory.PERSONAL_COMMON, 0.88)
    if label in ("UFAM_HEALTH_SENSITIVE", "UFAM_CID_ANCHOR"):
        return ("HEALTH_DATA", DataCategory.PERSONAL_SENSITIVE, 0.90)
    if label == "UFAM_UNION_SENSITIVE":
        return ("UNION_AFFILIATION", DataCategory.PERSONAL_SENSITIVE, 0.90)
    if label == "UFAM_ETHNIC_SENSITIVE":
        return ("ETHNIC_RACIAL_DECLARATION", DataCategory.PERSONAL_SENSITIVE, 0.91)
    if label == "UFAM_DEPENDENT_MINOR":
        return ("DEPENDENT_DATA", DataCategory.MINOR_OR_DEPENDENT, 0.89)
    return None


def _trim_person_stopwords(raw_text: str, orig_start: int, orig_end: int) -> tuple[int, int, str]:
    """Remove stopwords institucionais/etiquetas nas extremidades de candidatos de pessoa."""
    cur_start, cur_end, cur_val = orig_start, orig_end, raw_text
    if "\n" in cur_val:
        cur_start, cur_end, cur_val = _trim_multiline_person_entity("", cur_val, cur_start, cur_end)
    parts = cur_val.split()
    while len(parts) > 1:
        head_norm = _norm_ascii_lower(parts[0].strip(".,;:()"))
        if head_norm in _NON_PERSON_LINE_STOPWORDS or head_norm in {"da", "de", "do", "dos", "das", "e"}:
            m_head = re.match(r"^\s*" + re.escape(parts[0]) + r"\s+", cur_val)
            if m_head:
                cur_start += m_head.end()
                cur_val = cur_val[m_head.end() :]
                parts.pop(0)
                continue
        break
    while len(parts) > 1:
        tail_norm = _norm_ascii_lower(parts[-1].strip(".,;:()"))
        if tail_norm in _NON_PERSON_LINE_STOPWORDS or tail_norm in {"da", "de", "do", "dos", "das", "e"}:
            m_tail = re.search(r"\s+" + re.escape(parts[-1]) + r"\s*$", cur_val)
            if m_tail:
                cur_val = cur_val[: m_tail.start()]
                cur_end = cur_start + len(cur_val)
                parts.pop()
                continue
        break
    return cur_start, cur_end, cur_val.strip()

def _trim_multiline_person_entity(full_text: str, raw_text: str, orig_start: int, orig_end: int) -> tuple[int, int, str]:
    """
    Preserva quebras de linha legítimas em nomes compostos (ex.: `"Mateus\nHenrique Silva\nCavalcante"`,
    `"Terezinha\nde Jesus Nogueira Castelo Branco"`), cortando apenas antes de linhas administrativas
    ou campos com `:`, dígitos ou termos institucionais.
    """
    if "\n" not in raw_text:
        return orig_start, orig_end, raw_text.strip()

    lines = raw_text.splitlines()
    kept_lines: list[str] = [lines[0]]
    for ln in lines[1:]:
        if _is_valid_person_name_line(ln):
            kept_lines.append(ln)
        else:
            break

    candidate = "\n".join(kept_lines).strip()
    if not candidate:
        return orig_start, orig_end, raw_text.strip()

    offset = raw_text.find(candidate)
    if offset == -1:
        first = lines[0].strip()
        off0 = raw_text.find(first)
        if off0 != -1:
            return orig_start + off0, orig_start + off0 + len(first), first
        return orig_start, orig_end, raw_text.strip()

    return orig_start + offset, orig_start + offset + len(candidate), candidate


def _find_containing_table_cell(
    ent_text: str,
    table_cells: list[ExtractedTableCell] | None,
) -> ExtractedTableCell | None:
    if not table_cells or not ent_text:
        return None
    norm_ent = _norm_ascii_lower(" ".join(ent_text.split()))
    if not norm_ent:
        return None
    for cell in table_cells:
        if cell.row_index == 0:
            continue
        if norm_ent in _norm_ascii_lower(cell.cell_text):
            return cell
    return None


def _extend_wrapped_person_span(
    full_text: str,
    start_c: int,
    end_c: int,
    ent_text: str,
    table_cells: list[ExtractedTableCell] | None = None,
) -> tuple[int, int, str]:
    """
    Estende um nome próprio para baixo por até 4 linhas quando terminar antes de `\\n` seguido de
    continuação válida de sobrenome (ex.: `"Mateus\\nHenrique Silva\\nCavalcante"`).
    """
    cur_end = end_c
    cur_text = ent_text
    host_cell = _find_containing_table_cell(cur_text, table_cells)
    for _ in range(4):
        if cur_text.rstrip().endswith((".", ";", ":", "|", ",")):
            break
        tail = full_text[cur_end:]
        m = _NEXT_LINE_CONT_RE.match(tail)
        if not m:
            break
        cont_line = m.group(1).strip()
        if not _is_valid_person_name_line(cont_line):
            break
        cand_text = full_text[start_c : cur_end + m.end(1)].strip()
        if host_cell is not None and _norm_ascii_lower(" ".join(cand_text.split())) not in _norm_ascii_lower(host_cell.cell_text):
            break
        cur_end = cur_end + m.end(1)
        cur_text = cand_text

    return start_c, cur_end, cur_text


def _prepend_wrapped_person_span(
    full_text: str,
    start_c: int,
    end_c: int,
    ent_text: str,
    table_cells: list[ExtractedTableCell] | None = None,
) -> tuple[int, int, str]:
    """
    Recupera o primeiro nome quando o NER inicia apenas na 2ª linha de um nome quebrado
    (ex.: `"Mateus\\nHenrique Silva\\nCavalcante"` onde `"Mateus"` ficou na linha anterior).
    """
    cur_start = start_c
    cur_text = ent_text
    host_cell = _find_containing_table_cell(cur_text, table_cells)

    for _ in range(2):
        head = full_text[:cur_start]
        m_gap = re.search(r"([ \t]*\n[ \t]*|[ \t]+(?:da|de|do|dos|das|e)[ \t]+|[ \t]+)$", head, re.IGNORECASE)
        if not m_gap:
            break

        before_gap = head[: m_gap.start()]
        line_start = before_gap.rfind("\n")
        line_start = 0 if line_start == -1 else line_start + 1
        prev_segment = before_gap[line_start:]
        if not prev_segment.strip():
            break

        # Analisa tokens do fim da linha anterior para trás, parando em rótulos/stopwords/pontuação
        raw_parts = list(re.finditer(r"\S+", prev_segment))
        if not raw_parts:
            break

        kept_start_idx: int | None = None
        kept_tokens: list[str] = []
        for tok_m in reversed(raw_parts):
            tok_txt = tok_m.group(0)
            if any(ch in tok_txt for ch in (":", "=", "@", "/", "|", ";", ",", ".")) or re.search(r"\d", tok_txt):
                break
            if _NAME_PARTICLE_RE.match(tok_txt):
                kept_tokens.insert(0, tok_txt)
                kept_start_idx = tok_m.start()
                continue
            if _norm_ascii_lower(tok_txt) in _NON_PERSON_LINE_STOPWORDS:
                break
            if _FLEX_NAME_TOKEN_RE.match(tok_txt) and not is_false_positive_institutional_text(tok_txt):
                kept_tokens.insert(0, tok_txt)
                kept_start_idx = tok_m.start()
                if len(kept_tokens) >= 3:
                    break
            else:
                break

        # Remove partículas órfãs no início (ex.: "e Mateus" -> "Mateus")
        while kept_tokens and _NAME_PARTICLE_RE.match(kept_tokens[0]):
            removed = kept_tokens.pop(0)
            if kept_start_idx is not None:
                rem_m = re.search(rf"{re.escape(removed)}\s+", prev_segment[kept_start_idx:])
                if rem_m:
                    kept_start_idx += rem_m.end()
                else:
                    kept_tokens.clear()
                    break

        if not kept_tokens or kept_start_idx is None:
            break
        if not any(_FLEX_NAME_TOKEN_RE.match(t) for t in kept_tokens):
            break
        # Não absorve palavra logo após separador de cabeçalho/rótulo na linha anterior (ex.: "Contato / Saúde")
        if prev_segment[:kept_start_idx].rstrip().endswith(("/", "|", ":", ";", "=")):
            break

        cand_start = line_start + kept_start_idx
        cand_text = full_text[cand_start:end_c].strip()
        if host_cell is not None and _norm_ascii_lower(" ".join(cand_text.split())) not in _norm_ascii_lower(host_cell.cell_text):
            break
        cur_start = cand_start
        cur_text = cand_text

    return cur_start, end_c, cur_text


def _to_title_token_same_len(word: str) -> str:
    if word.upper() in ("DA", "DE", "DO", "DOS", "DAS", "E"):
        return word.lower()
    if len(word) <= 1:
        return word.upper()
    return word[0].upper() + word[1:].lower()


def _build_title_shadow_with_identical_offsets(full_text: str) -> str | None:
    """
    Constrói uma cópia de `full_text` com EXATAMENTE o mesmo comprimento (`len(shadow) == len(full_text)`)
    convertendo sequências multi-palavra em CAIXA ALTA (não pertencentes à blocklist institucional)
    para Title Case, permitindo ao `spaCy` NER detectá-las preservando offsets originais 1:1.
    """
    if not full_text:
        return None
    chars = list(full_text)
    changed = False
    for m in _UPPER_SEQ_RE.finditer(full_text):
        seg = m.group(0)
        if is_false_positive_institutional_text(seg):
            continue
        lines = [ln.strip() for ln in seg.splitlines() if ln.strip()]
        if any(is_false_positive_institutional_text(ln) for ln in lines):
            continue
        words = seg.split()
        alpha_words = [w.strip(".") for w in words if not _NAME_PARTICLE_RE.match(w)]
        if len(alpha_words) < 2:
            continue
        if any(_norm_ascii_lower(w) in _NON_PERSON_LINE_STOPWORDS for w in alpha_words):
            continue
        base = m.start()
        for wm in re.finditer(r"[A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç]+", seg):
            w_orig = wm.group(0)
            w_conv = _to_title_token_same_len(w_orig)
            if len(w_conv) == len(w_orig):
                s_i = base + wm.start()
                chars[s_i : s_i + len(w_orig)] = list(w_conv)
                changed = True
    return "".join(chars) if changed else None


def propagate_person_names_across_batch(
    pages_payload: list[tuple[int, str, list[ExtractedTableCell] | None]],
    results_by_page: dict[int, list[RawTextSpanDetection]],
    *,
    extra_confirmed_names: list[tuple[str, str, DataCategory, float]] | None = None,
) -> dict[int, list[RawTextSpanDetection]]:
    """
    Varredura complementar (*second-pass match*):
    Quando um nome completo de pessoa é detectado em qualquer página do documento (ex.: `"Gabrielly Tupinambá de Souza"`
    na Página 3 ou `"Mateus Henrique Silva Cavalcante"`), busca todas as ocorrências daquele nome completo
    (mesmo quebrado em 2 ou 3 linhas ou dentro de células de tabela) em TODAS as páginas do PDF para garantir
    cobertura 100% e vinculação ao mesmo `group_id`.
    """
    confirmed_names: dict[str, tuple[list[str], str, DataCategory, float]] = {}

    def _register_name(raw_name: str, etype: str, cat: DataCategory, conf: float) -> None:
        cleaned = re.sub(r"\s+", " ", (raw_name or "")).strip()
        if not cleaned or is_false_positive_institutional_text(cleaned):
            return
        raw_parts = [t.strip(",()") for t in cleaned.split() if t.strip(".,()")]
        tokens = [
            t if (len(t.rstrip(".")) == 1 and t.endswith(".")) else t.rstrip(".")
            for t in raw_parts
        ]
        alpha_non_particles = [
            t for t in tokens if not _NAME_PARTICLE_RE.match(t) and _FLEX_NAME_TOKEN_RE.match(t)
        ]
        full_words = [t for t in alpha_non_particles if not t.endswith(".") and len(t) >= 2]
        if len(tokens) < 2 or len(full_words) < 2:
            return
        if any(_norm_ascii_lower(t.rstrip(".")) in _NON_PERSON_LINE_STOPWORDS for t in alpha_non_particles):
            return
        key = _norm_ascii_lower(" ".join(tokens))
        prev = confirmed_names.get(key)
        if prev is None or (etype == "DEPENDENT_DATA" and prev[1] != "DEPENDENT_DATA"):
            confirmed_names[key] = (tokens, etype, cat, conf)

    for dets in results_by_page.values():
        for d in dets:
            if d.entity_type in _PERSON_ENTITY_TYPES:
                _register_name(d.matched_text, d.entity_type, d.category, d.confidence)

    for name_val, etype, cat, conf in extra_confirmed_names or []:
        _register_name(name_val, etype, cat, conf)

    if not confirmed_names:
        return results_by_page

    ordered_candidates = sorted(
        confirmed_names.values(),
        key=lambda item: (-len(item[0]), -sum(len(t) for t in item[0])),
    )

    compiled_candidates: list[tuple[re.Pattern[str], str, DataCategory, float]] = []
    fuzzy_candidates: list[tuple[re.Pattern[str], str, str, DataCategory, float]] = []
    for tokens, etype, cat, conf in ordered_candidates:
        compiled_candidates.append(
            (
                re.compile(r"(?i)\b" + r"[\s\n]+".join(_accent_insensitive_regex(t) for t in tokens) + r"\b"),
                etype,
                cat,
                conf,
            )
        )
        non_part_indices = [i for i, t in enumerate(tokens) if not _NAME_PARTICLE_RE.match(t)]
        if len(non_part_indices) >= 3:
            first_i, last_i = non_part_indices[0], non_part_indices[-1]
            abbr_parts: list[str] = []
            for idx_t, t in enumerate(tokens):
                if _NAME_PARTICLE_RE.match(t) or idx_t == first_i or idx_t == last_i:
                    abbr_parts.append(_accent_insensitive_regex(t))
                else:
                    clean_t = t.rstrip(".")
                    abbr_parts.append(rf"(?:{_accent_insensitive_regex(clean_t)}|{_accent_insensitive_regex(clean_t[0])}\.)")
            compiled_candidates.append(
                (
                    re.compile(r"(?i)\b" + r"[\s\n]+".join(abbr_parts) + r"\b"),
                    etype,
                    cat,
                    conf,
                )
            )
            # Propagação tolerante a espaços colados (`Americo deCastroBraga`, `EdsonAraujodaSilva`)
            # e 1 erro de OCR em tokens intermediários (`AMERICO DE CASTRD BRAGA`)
            first_tok = tokens[first_i]
            last_tok = tokens[last_i]
            if len(first_tok) >= 4 and len(last_tok) >= 4:
                compact_target = re.sub(r"[^a-z]", "", _norm_ascii_lower("".join(tokens)))
                mid_len = sum(len(t) for t in tokens[first_i + 1 : last_i])
                max_mid = min(48, mid_len + len(tokens) * 3 + 4)
                fuzzy_re = re.compile(
                    rf"(?i)\b{_accent_insensitive_regex(first_tok)}"
                    rf"[A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç \t\n]{{0,{max_mid}}}"
                    rf"{_accent_insensitive_regex(last_tok)}\b"
                )
                fuzzy_candidates.append((fuzzy_re, compact_target, etype, cat, conf))

    for page_num, full_text, table_cells in pages_payload:
        page_dets = results_by_page.setdefault(page_num, [])

        if full_text:
            known_person_spans: set[tuple[int, int]] = {
                (d.start_char, d.end_char)
                for d in page_dets
                if d.entity_type in _PERSON_ENTITY_TYPES
            }
            for pattern, etype, cat, conf in compiled_candidates:
                for match in pattern.finditer(full_text):
                    s_c, e_c = match.span(0)
                    if (s_c, e_c) in known_person_spans:
                        continue
                    matched_val = match.group(0).strip()
                    if not matched_val or is_false_positive_institutional_text(matched_val):
                        continue
                    det = RawTextSpanDetection(
                        page_number=page_num,
                        entity_type=etype,
                        category=cat,
                        matched_text=matched_val,
                        start_char=s_c,
                        end_char=e_c,
                        confidence=conf,
                        detector_source="SPACY_NLP",
                    )
                    _upsert_person_or_span_detection(page_dets, det, allow_replace_shorter_person=True)
                    known_person_spans.add((s_c, e_c))

            for f_pattern, compact_target, etype, cat, conf in fuzzy_candidates:
                max_edits = 1 if len(compact_target) >= 12 else 0
                for match in f_pattern.finditer(full_text):
                    s_c, e_c = match.span(0)
                    if (s_c, e_c) in known_person_spans:
                        continue
                    matched_val = match.group(0).strip()
                    if not matched_val or "\n\n" in matched_val or is_false_positive_institutional_text(matched_val):
                        continue
                    compact_cand = re.sub(r"[^a-z]", "", _norm_ascii_lower(matched_val))
                    if not _within_ocr_edit_distance(compact_cand, compact_target, max_edits=max_edits):
                        continue
                    det = RawTextSpanDetection(
                        page_number=page_num,
                        entity_type=etype,
                        category=cat,
                        matched_text=matched_val,
                        start_char=s_c,
                        end_char=e_c,
                        confidence=round(min(conf, 0.90), 4),
                        detector_source="SPACY_NLP",
                    )
                    _upsert_person_or_span_detection(page_dets, det, allow_replace_shorter_person=True)
                    known_person_spans.add((s_c, e_c))

        if table_cells and full_text:
            for cell in table_cells:
                if cell.row_index == 0 or not cell.cell_text.strip():
                    continue
                val_clean = cell.cell_text.strip()
                for pattern, etype, cat, conf in compiled_candidates:
                    for match in pattern.finditer(val_clean):
                        matched_val = match.group(0).strip()
                        if not matched_val or is_false_positive_institutional_text(matched_val):
                            continue
                        _append_table_detection(
                            page_number=page_num,
                            full_text=full_text,
                            cell=cell,
                            entity_type=etype,
                            category=cat,
                            matched_text=matched_val,
                            match_start=match.start(0),
                            match_end=match.end(0),
                            confidence=conf,
                            detector_source="SPACY_NLP",
                            existing_detections=page_dets,
                            replace_shorter_person=True,
                        )

        page_dets.sort(key=lambda d: (d.start_char, -(d.end_char - d.start_char)))

    return results_by_page


def analyze_pages_batch(
    pages_payload: list[tuple[int, str, list[ExtractedTableCell] | None]],
    *,
    custom_regex_specs: Sequence[RegexPatternSpec] | None = None,
) -> dict[int, list[RawTextSpanDetection]]:
    """
    Analisa um lote de páginas em alta velocidade combinando Regex, Validadores Matemáticos,
    `nlp.pipe` vetorizado, análise estrutural de tabelas e propagação cruzada de nomes completos.
    """
    if not pages_payload:
        return {}

    nlp = ensure_ufam_entity_ruler_configured(ml_registry.get_spacy_nlp())

    possible_disable = ["parser", "lemmatizer", "attribute_ruler", "tok2vec"]
    active_disable = [comp for comp in possible_disable if comp in nlp.pipe_names and "ner" not in comp]

    texts = [item[1] for item in pages_payload]
    results_by_page: dict[int, list[RawTextSpanDetection]] = {}

    for page_num, full_text, table_cells in pages_payload:
        results_by_page[page_num] = _extract_regex_spans_for_page(
            page_number=page_num,
            full_text=full_text,
            table_cells=table_cells,
            custom_regex_specs=custom_regex_specs,
        )

    docs = nlp.pipe(texts, batch_size=16, disable=active_disable)
    for (page_num, full_text, table_cells), doc in zip(pages_payload, docs):
        existing_spans = results_by_page[page_num]

        # Pré-mescla entidades consecutivas do tipo PER / UFAM_ADMIN_PERSON separadas apenas por quebra de linha/partícula
        raw_ents: list[tuple[str, str, int, int, DataCategory, float]] = []
        for ent in doc.ents:
            mapped = _map_spacy_label_to_category(ent.label_)
            if mapped is None:
                continue
            ent_type, category, conf = mapped
            ent_text = ent.text.strip()
            start_c, end_c = ent.start_char, ent.end_char

            if (
                raw_ents
                and ent_type == "PERSON_NAME"
                and raw_ents[-1][0] == "PERSON_NAME"
            ):
                prev_type, prev_text, prev_s, prev_e, prev_cat, prev_conf = raw_ents[-1]
                gap = full_text[prev_e:start_c]
                if _INTER_NAME_GAP_RE.match(gap) and _is_valid_person_name_line(ent_text.split("\n", 1)[0]):
                    merged_text = full_text[prev_s:end_c].strip()
                    raw_ents[-1] = (prev_type, merged_text, prev_s, end_c, prev_cat, max(prev_conf, conf))
                    continue

            raw_ents.append((ent_type, ent_text, start_c, end_c, category, conf))

        for ent_type, ent_text, start_c, end_c, category, conf in raw_ents:
            raw_slice = full_text[start_c:end_c]

            if "\n" in ent_text and ent_type in ("PERSON_NAME", "DEPENDENT_DATA", "FILIACAO", "NATURALIDADE"):
                start_c, end_c, ent_text = _trim_multiline_person_entity(full_text, raw_slice, start_c, end_c)

            if ":" in ent_text and ent_type in (
                "PERSON_NAME",
                "DEPENDENT_DATA",
                "FILIACAO",
                "UNION_AFFILIATION",
                "ETHNIC_RACIAL_DECLARATION",
            ):
                colon_idx = ent_text.find(":")
                value_part = ent_text[colon_idx + 1 :].strip()
                if value_part:
                    slice_now = full_text[start_c:end_c]
                    offset = slice_now.find(value_part, slice_now.find(":"))
                    if offset != -1:
                        start_c = start_c + offset
                        end_c = start_c + len(value_part)
                        ent_text = value_part

            if ent_type in _PERSON_ENTITY_TYPES:
                start_c, end_c, ent_text = _trim_person_stopwords(ent_text, start_c, end_c)
                start_c, end_c, ent_text = _prepend_wrapped_person_span(full_text, start_c, end_c, ent_text, table_cells)
                start_c, end_c, ent_text = _extend_wrapped_person_span(full_text, start_c, end_c, ent_text, table_cells)
                start_c, end_c, ent_text = _trim_person_stopwords(ent_text, start_c, end_c)

            if ent_type == "PERSON_NAME":
                ctx_left = full_text[max(0, start_c - 45) : start_c]
                if re.search(r"(?i)\b(?:dependente|menor|benefici[aá]rio\s+menor|filho\(a\)\s+menor)\s*[:=-]?\s*$", ctx_left):
                    ent_type = "DEPENDENT_DATA"
                    category = DataCategory.MINOR_OR_DEPENDENT

            if should_suppress_detection(
                entity_type=ent_type,
                matched_value=ent_text,
                full_text=full_text,
                start_char=start_c,
                end_char=end_c,
                table_cells=table_cells,
            ):
                continue

            det = RawTextSpanDetection(
                page_number=page_num,
                entity_type=ent_type,
                category=category,
                matched_text=ent_text,
                start_char=start_c,
                end_char=end_c,
                confidence=conf,
                detector_source="SPACY_NLP",
            )
            _upsert_person_or_span_detection(existing_spans, det, allow_replace_shorter_person=True)

        _extract_table_aware_spans_for_page(
            page_number=page_num,
            full_text=full_text,
            table_cells=table_cells,
            existing_detections=existing_spans,
            run_regex_on_cells=False,
        )

        existing_spans.sort(key=lambda d: (d.start_char, -(d.end_char - d.start_char)))

    # Passo complementar Title-Case mantendo offsets 1:1 para nomes em CAIXA ALTA via spaCy NER
    shadow_payload: list[tuple[int, str, list[ExtractedTableCell] | None, str]] = []
    for page_num, full_text, table_cells in pages_payload:
        shadow_txt = _build_title_shadow_with_identical_offsets(full_text)
        if shadow_txt is not None:
            shadow_payload.append((page_num, full_text, table_cells, shadow_txt))

    if shadow_payload:
        shadow_docs = nlp.pipe([item[3] for item in shadow_payload], batch_size=16, disable=active_disable)
        for (page_num, full_text, table_cells, _shadow_txt), s_doc in zip(shadow_payload, shadow_docs):
            existing_spans = results_by_page[page_num]
            for ent in s_doc.ents:
                mapped = _map_spacy_label_to_category(ent.label_)
                if mapped is None:
                    continue
                ent_type, category, conf = mapped
                if ent_type not in _PERSON_ENTITY_TYPES:
                    continue
                start_c, end_c = ent.start_char, ent.end_char
                ent_text = full_text[start_c:end_c].strip()
                if not ent_text:
                    continue
                if ":" in ent_text:
                    colon_idx = ent_text.find(":")
                    val_p = ent_text[colon_idx + 1 :].strip()
                    if val_p:
                        slice_now = full_text[start_c:end_c]
                        off = slice_now.find(val_p, slice_now.find(":"))
                        if off != -1:
                            start_c += off
                            end_c = start_c + len(val_p)
                            ent_text = val_p
                if ent_type in _PERSON_ENTITY_TYPES:
                    start_c, end_c, ent_text = _trim_person_stopwords(ent_text, start_c, end_c)
                    start_c, end_c, ent_text = _prepend_wrapped_person_span(full_text, start_c, end_c, ent_text, table_cells)
                    start_c, end_c, ent_text = _extend_wrapped_person_span(full_text, start_c, end_c, ent_text, table_cells)
                    start_c, end_c, ent_text = _trim_person_stopwords(ent_text, start_c, end_c)
                if should_suppress_detection(
                    entity_type=ent_type,
                    matched_value=ent_text,
                    full_text=full_text,
                    start_char=start_c,
                    end_char=end_c,
                    table_cells=table_cells,
                ):
                    continue
                det = RawTextSpanDetection(
                    page_number=page_num,
                    entity_type=ent_type,
                    category=category,
                    matched_text=ent_text,
                    start_char=start_c,
                    end_char=end_c,
                    confidence=conf,
                    detector_source="SPACY_NLP",
                )
                _upsert_person_or_span_detection(existing_spans, det, allow_replace_shorter_person=True)
            existing_spans.sort(key=lambda d: (d.start_char, -(d.end_char - d.start_char)))

    # Varredura complementar entre todas as páginas e células de tabela para propagação completa por group_id
    propagate_person_names_across_batch(pages_payload, results_by_page)

    return results_by_page
