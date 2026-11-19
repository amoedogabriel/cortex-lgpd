"""
Mapeador de Coordenadas, Recomendação Inteligente (`BLACKOUT` vs. `PARTIAL_MASK`),
Mascaramento Parcial e Agrupamento de Repetições (`entity_mapper.py`).

Requisitos implementados:
1. Constrói o texto linear da página indexando o intervalo exato de caracteres `[char_start, char_end)`
   de cada `UnifiedToken` na página com busca binária `O(log N)`;
2. Mapeia cada detecção (por intervalo `[start_char, end_char)` ou `override_tokens` em tabelas
   multicoluna) para Bounding Boxes normalizadas `[x0_norm, y0_norm, x1_norm, y1_norm]` estritamente
   separadas por linha vertical (`(block_no, line_no)` e `abs(word1.y0 - word2.y0) <= 3.0pt`),
   cortando prefixos/sufixos dentro de tokens de fronteira via largura visual (`fitz.get_text_length`);
3. Mantém coordenadas horizontais e verticais exatas (`padding_x = 0`, `padding_y = 0`) nas caixas
   de revisão para impedir invasão de palavras adjacentes no modo `PARTIAL_MASK` (margem `+2pt`
   aplicada na direita apenas na gravação física `BLACKOUT`);
4. Unifica `group_id` entre nomes completos e suas variações com nomes do meio abreviados
   (ex.: `"ROBERTO CARLOS NOGUEIRA DA SILVA"` e `"Roberto Carlos N. da Silva"`);
5. Ignora textos localizados na região de rodapé/paginação (`y0_norm > 0.93`).
"""

from __future__ import annotations

import bisect
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

import fitz  # PyMuPDF

from app.models.enums import (
    ConfidenceLevel,
    DataCategory,
    DocumentProfile,
    RedactionActionMode,
)
from app.services.pdf_extractor.extractor import (
    PageExtractionResult,
    UnifiedToken,
    VisualElementKind,
)
from app.services.pii_detector.context_filter import is_footer_region
from app.services.pii_detector.nlp_analyzer import (
    RawTextSpanDetection,
    analyze_pages_batch,
    propagate_person_names_across_batch,
)
from app.services.pii_detector.validators import strip_non_digits
from app.services.redactor.pdf_redactor import generate_masked_preview

_SAME_LINE_Y0_TOLERANCE_NORM: float = 3.0 / 842.0
_PERSON_GROUP_TYPES: frozenset[str] = frozenset({"PERSON_NAME", "DEPENDENT_DATA", "FILIACAO"})
_ASCII_PARTICLES: frozenset[str] = frozenset({"DA", "DE", "DO", "DOS", "DAS", "E"})

_NUMERIC_CANONICAL_TYPES: frozenset[str] = frozenset(
    {
        "CPF",
        "CNH",
        "PIS_PASEP",
        "RG",
        "TITULO_ELEITOR",
        "CEP",
        "PHONE",
        "SIAPE",
        "MATRICULA_UFAM",
        "PROCESSO_SEI",
        "BANK_AGENCY",
        "BANK_OPERATION",
        "BANK_ACCOUNT",
    }
)

_PUBLIC_TRANSPARENCY_RE = re.compile(
    r"(?i)\b(?:EDITAL|RESULTADO|HOMOLOGA[CÇ][AÃ]O|CLASSIFICA[CÇ][AÃ]O|LISTA\s+DE\s+APROVADOS|"
    r"CHAMADA\s+P[UÚ]BLICA|BOLSISTAS|EXTRATO\s+DE\s+CONTRATO)\b"
)
_RESTRICTED_PROCESS_RE = re.compile(
    r"(?i)\b(?:DESPACHO|PROCESSO\s+SEI|REQUERIMENTO|LAUDO|ATESTADO|SIASS|FOLHA|DEPENDENT|AVERBA[CÇ][AÃ]O)\b"
)
_MINOR_CONTEXT_KW_RE = re.compile(
    r"(?i)\b(?:menor(?:\s+de\s+idade)?|dependente|crian[çc]a|adolescente|filh[oa]\s+menor|pens[aã]o\s+aliment[ií]cia)\b"
)
_SENSITIVE_MED_KW_RE = re.compile(
    r"(?i)\b(?:cid-?1[01]|laudo\s+m[ée]dico|diagn[óo]stico|patologia|atestado\s+m[ée]dico)\b"
)


@dataclass(frozen=True, slots=True)
class DetectedPIIEntity:
    """Entidade de dado pessoal mapeada para coordenadas normalizadas [0.0, 1.0], agrupada e com modo recomendado."""

    page_number: int
    entity_type: str
    category: DataCategory
    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float
    confidence_score: float
    confidence_level: ConfidenceLevel
    group_id: str
    detector_source: str
    matched_text: str  # Mantido apenas em memória transitória de sessão para revisão HITL; JAMAIS persistido em DB/Logs
    action_mode: RedactionActionMode = RedactionActionMode.BLACKOUT
    recommended_mode: RedactionActionMode = RedactionActionMode.BLACKOUT
    masked_preview: str = "***"
    is_continuation: bool = False
    is_ocr: bool = False
    safe_x0_norm: float | None = None
    safe_x1_norm: float | None = None


@dataclass(frozen=True, slots=True)
class _IndexedPageText:
    """Texto linearizado da página com índice ordenado para busca binária O(log N) de `UnifiedToken`."""

    text: str
    token_starts: list[int]
    token_ends: list[int]
    tokens: list[UnifiedToken]


@dataclass(frozen=True, slots=True)
class _LineSubBox:
    """Sub-retângulo correspondente a uma única linha vertical de uma entidade."""

    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float
    mean_confidence: float
    line_text: str
    is_ocr: bool = False
    safe_x0_norm: float | None = None
    safe_x1_norm: float | None = None


def _normalize_ascii_upper_space(val: str) -> str:
    nfkd = unicodedata.normalize("NFKD", (val or "").strip())
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_only).strip().upper()


def _person_non_particle_tokens(raw_name: str) -> list[str]:
    norm = _normalize_ascii_upper_space(raw_name)
    out: list[str] = []
    for tok in norm.split():
        clean = tok.strip(",()")
        if not clean:
            continue
        if len(clean.rstrip(".")) == 1 and clean.endswith("."):
            out.append(clean)
            continue
        clean = clean.rstrip(".")
        if clean and clean not in _ASCII_PARTICLES:
            out.append(clean)
    return out


def _tokens_equal_or_ocr_typo(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) < 4 or len(b) < 4 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for ch1, ch2 in zip(a, b) if ch1 != ch2) <= 1
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    i = j = diffs = 0
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j]:
            i += 1
            j += 1
        else:
            diffs += 1
            if diffs > 1:
                return False
            j += 1
    return True


def _person_abbreviated_matches_full(abbr_tokens: list[str], full_tokens: list[str]) -> bool:
    """Verifica se uma forma abreviada (`Roberto Carlos N. da Silva`) corresponde a um nome completo (tolerando 1 erro de OCR no sobrenome)."""
    if len(abbr_tokens) < 2 or len(full_tokens) < 3:
        return False
    if not _tokens_equal_or_ocr_typo(abbr_tokens[0], full_tokens[0]) or not _tokens_equal_or_ocr_typo(abbr_tokens[-1], full_tokens[-1]):
        return False
    if len(abbr_tokens) > len(full_tokens):
        return False

    f_idx = 1
    f_limit = len(full_tokens) - 1
    for a_tok in abbr_tokens[1:-1]:
        is_initial = len(a_tok.rstrip(".")) == 1
        a_init = a_tok.rstrip(".")
        matched = False
        while f_idx < f_limit:
            f_tok = full_tokens[f_idx]
            f_idx += 1
            if (is_initial and f_tok.startswith(a_init)) or (not is_initial and a_tok == f_tok):
                matched = True
                break
        if not matched:
            return False
    return True


def build_person_canonical_map(raw_names: list[str]) -> dict[str, str]:
    """
    Mapeia variações abreviadas de nomes (`Roberto Carlos N. da Silva`) para a forma completa
    correspondente (`ROBERTO CARLOS NOGUEIRA DA SILVA`) quando ambas coexistem no documento.
    """
    unique_norms: dict[str, list[str]] = {}
    for rn in raw_names:
        norm = _normalize_ascii_upper_space(rn)
        if not norm or norm in unique_norms:
            continue
        toks = _person_non_particle_tokens(rn)
        if len(toks) >= 2:
            unique_norms[norm] = toks

    full_candidates = [
        (norm, toks)
        for norm, toks in unique_norms.items()
        if len(toks) >= 3 and all(not t.endswith(".") and len(t) >= 2 for t in toks)
    ]
    full_candidates.sort(key=lambda item: (-len(item[1]), -len(item[0])))

    canonical_map: dict[str, str] = {}
    for norm, toks in unique_norms.items():
        has_abbr = any(t.endswith(".") or len(t) == 1 for t in toks)
        for full_norm, full_toks in full_candidates:
            if full_norm == norm:
                continue
            if (has_abbr or len(toks) < len(full_toks)) and _person_abbreviated_matches_full(toks, full_toks):
                canonical_map[norm] = full_norm
                break
    return canonical_map


def classify_document_profile(text: str) -> DocumentProfile:
    """
    Classifica o perfil documental a partir do cabeçalho/texto da Página 1:
    - `PUBLIC_TRANSPARENCY` vs `RESTRICTED_PROCESS`.
    """
    if not text:
        return DocumentProfile.RESTRICTED_PROCESS

    header_slice = text[:650]
    has_restricted_header = bool(_RESTRICTED_PROCESS_RE.search(header_slice))
    has_public_header = bool(_PUBLIC_TRANSPARENCY_RE.search(header_slice))

    if has_public_header and not has_restricted_header:
        return DocumentProfile.PUBLIC_TRANSPARENCY

    if has_restricted_header:
        return DocumentProfile.RESTRICTED_PROCESS

    if _PUBLIC_TRANSPARENCY_RE.search(text) and not _RESTRICTED_PROCESS_RE.search(text):
        return DocumentProfile.PUBLIC_TRANSPARENCY

    return DocumentProfile.RESTRICTED_PROCESS


def compute_recommended_action_mode(
    entity_type: str,
    category: DataCategory,
    document_profile: DocumentProfile,
    *,
    linked_to_minor_or_sensitive: bool = False,
) -> RedactionActionMode:
    """Determina o modo recomendado (`BLACKOUT`, `PARTIAL_MASK` ou `KEEP`)."""
    if linked_to_minor_or_sensitive:
        return RedactionActionMode.BLACKOUT
    if category in (DataCategory.PERSONAL_SENSITIVE, DataCategory.MINOR_OR_DEPENDENT):
        return RedactionActionMode.BLACKOUT

    if document_profile == DocumentProfile.PUBLIC_TRANSPARENCY:
        etype = entity_type.upper()
        if etype in ("CPF", "RG"):
            return RedactionActionMode.PARTIAL_MASK
        if etype == "PERSON_NAME":
            return RedactionActionMode.KEEP

    return RedactionActionMode.BLACKOUT


def compute_confidence_level(score: float) -> ConfidenceLevel:
    """Classifica o score numérico em `HIGH` (>= 0.85), `MEDIUM` (0.60 a 0.84) ou `LOW` (< 0.60)."""
    if score >= 0.85:
        return ConfidenceLevel.HIGH
    if score >= 0.60:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


@lru_cache(maxsize=4096)
def compute_entity_group_id(entity_type: str, raw_value: str, session_salt: str = "") -> str:
    """Gera um `group_id` determinístico (SHA-256 curto de 16 hex chars) a partir do valor normalizado."""
    cleaned = raw_value.strip()
    if entity_type in _NUMERIC_CANONICAL_TYPES:
        canonical = strip_non_digits(cleaned) or cleaned.upper()
    else:
        canonical = _normalize_ascii_upper_space(cleaned)

    payload = f"{session_salt}:{entity_type}:{canonical}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _build_indexed_text_from_page(page_result: PageExtractionResult) -> _IndexedPageText:
    """Constrói uma string linear a partir dos tokens da página preservando quebras de linha."""
    if not page_result.tokens:
        return _IndexedPageText(text=page_result.full_text, token_starts=[], token_ends=[], tokens=[])

    parts: list[str] = []
    token_starts: list[int] = []
    token_ends: list[int] = []
    tokens_list: list[UnifiedToken] = []
    cursor = 0
    prev_line_key: tuple[int, int] | None = None
    prev_y0: float | None = None
    prev_x1: float | None = None
    prev_word: str = ""

    for tok in page_result.tokens:
        word = tok.text
        if not word:
            continue

        if parts:
            if tok.source == "NATIVE":
                curr_line_key = (tok.block_no, tok.line_no)
                line_changed = (
                    prev_line_key is not None
                    and (
                        curr_line_key != prev_line_key
                        or (prev_y0 is not None and abs(tok.y0_norm - prev_y0) >= _SAME_LINE_Y0_TOLERANCE_NORM)
                    )
                )
                sep = "\n" if line_changed else " "
                prev_line_key = curr_line_key
                prev_y0 = tok.y0_norm
                prev_x1 = tok.x1_norm
                prev_word = word
            else:
                wide_col_gap = (
                    prev_x1 is not None
                    and (tok.x0_norm - prev_x1) >= 0.08
                    and not prev_word.endswith((":", "-", "="))
                )
                sep = (
                    "\n"
                    if (
                        (prev_y0 is not None and abs(tok.y0_norm - prev_y0) >= _SAME_LINE_Y0_TOLERANCE_NORM)
                        or wide_col_gap
                    )
                    else " "
                )
                prev_y0 = tok.y0_norm
                prev_x1 = tok.x1_norm
                prev_word = word

            parts.append(sep)
            cursor += len(sep)
        else:
            prev_line_key = (tok.block_no, tok.line_no)
            prev_y0 = tok.y0_norm
            prev_x1 = tok.x1_norm
            prev_word = word

        start_idx = cursor
        parts.append(word)
        cursor += len(word)
        end_idx = cursor

        token_starts.append(start_idx)
        token_ends.append(end_idx)
        tokens_list.append(tok)

    return _IndexedPageText(
        text="".join(parts),
        token_starts=token_starts,
        token_ends=token_ends,
        tokens=tokens_list,
    )


def _clamp_norm(val: float) -> float:
    return round(max(0.0, min(1.0, float(val))), 6)


def _slice_token_horizontally(
    tok: UnifiedToken,
    local_start: int,
    local_end: int,
) -> UnifiedToken:
    """
    Ajusta `[x0_norm, x1_norm]` de um token de fronteira quando a entidade começa ou termina
    no meio do token (ex.: `"Filiação:Antônio"` ou `"528.419.630-98,"`) usando largura visual
    ponderada via `fitz.get_text_length(..., fontname="helv", fontsize=10)`.
    """
    tok_text = tok.text
    if not tok_text:
        return tok
    s_i = max(0, min(len(tok_text), local_start))
    e_i = max(s_i + 1, min(len(tok_text), local_end))
    if s_i == 0 and e_i == len(tok_text):
        return tok

    total_pt = float(fitz.get_text_length(tok_text, fontname="helv", fontsize=10))
    if total_pt <= 1e-5:
        total_pt = float(max(1, len(tok_text)))

    pre_pt = float(fitz.get_text_length(tok_text[:s_i], fontname="helv", fontsize=10))
    end_pt = float(fitz.get_text_length(tok_text[:e_i], fontname="helv", fontsize=10))

    rel_s = max(0.0, min(1.0, pre_pt / total_pt))
    rel_e = max(rel_s, min(1.0, end_pt / total_pt))

    width = max(1e-5, tok.x1_norm - tok.x0_norm)
    new_x0 = _clamp_norm(tok.x0_norm + rel_s * width)
    new_x1 = _clamp_norm(tok.x0_norm + rel_e * width)
    if new_x1 <= new_x0:
        new_x1 = _clamp_norm(min(1.0, new_x0 + 1e-4))

    return UnifiedToken(
        text=tok_text[s_i:e_i],
        page_number=tok.page_number,
        x0_norm=new_x0,
        y0_norm=tok.y0_norm,
        x1_norm=new_x1,
        y1_norm=tok.y1_norm,
        confidence=tok.confidence,
        source=tok.source,
        block_no=tok.block_no,
        line_no=tok.line_no,
        word_no=tok.word_no,
    )


def _group_tokens_into_line_subboxes(
    matching_tokens: list[UnifiedToken],
    *,
    all_page_tokens: list[UnifiedToken] | None = None,
    left_sliced: bool = False,
    right_sliced: bool = False,
) -> list[_LineSubBox]:
    """
    Agrupa tokens estritamente por linha (`(block_no, line_no)` quando nativo e `abs(y0_1 - y0_2) <= 3.0pt`)
    gerando 1 sub-retângulo horizontal exato (`padding_x = 0`, `padding_y = 0`) por linha ocupada.
    """
    if not matching_tokens:
        return []

    line_groups: list[list[UnifiedToken]] = []
    for tok in matching_tokens:
        if not line_groups:
            line_groups.append([tok])
            continue

        prev_tok = line_groups[-1][-1]
        same_y_line = abs(tok.y0_norm - prev_tok.y0_norm) <= _SAME_LINE_Y0_TOLERANCE_NORM
        same_native_line = (
            tok.source != "NATIVE"
            or prev_tok.source != "NATIVE"
            or (tok.block_no == prev_tok.block_no and tok.line_no == prev_tok.line_no)
            or same_y_line
        )
        same_x_flow = tok.x0_norm >= (prev_tok.x0_norm - 0.02) and (tok.x0_norm - prev_tok.x1_norm) < 0.18

        if same_y_line and same_native_line and same_x_flow:
            line_groups[-1].append(tok)
        else:
            line_groups.append([tok])

    sub_boxes: list[_LineSubBox] = []
    for g_idx, group in enumerate(line_groups):
        x0 = _clamp_norm(min(t.x0_norm for t in group))
        y0 = _clamp_norm(min(t.y0_norm for t in group))
        x1 = _clamp_norm(max(t.x1_norm for t in group))
        y1 = _clamp_norm(max(t.y1_norm for t in group))

        if x1 <= x0:
            x1 = _clamp_norm(min(1.0, x0 + 1e-4))
        if y1 <= y0:
            y1 = _clamp_norm(min(1.0, y0 + 1e-4))

        mean_conf = sum(t.confidence for t in group) / len(group)
        line_str = " ".join(t.text for t in group if t.text).strip()
        is_ocr_group = any(t.source == "OCR" for t in group)
        safe_x0: float | None = None
        safe_x1: float | None = None
        if is_ocr_group:
            band_tokens = [
                t
                for t in (all_page_tokens or [])
                if abs(t.y0_norm - y0) <= 0.014 or min(t.y1_norm, y1) > max(t.y0_norm, y0)
            ]
            is_first_left_sliced = left_sliced and g_idx == 0
            is_last_right_sliced = right_sliced and g_idx == (len(line_groups) - 1)
            if is_first_left_sliced:
                safe_x0 = x0
            else:
                left_walls = [
                    t.x1_norm + (1.0 / 595.0) for t in band_tokens if t.x1_norm <= x0 + 1e-4
                ]
                safe_x0 = _clamp_norm(max(left_walls)) if left_walls else 0.0
                if safe_x0 > x0:
                    safe_x0 = x0
            if is_last_right_sliced:
                safe_x1 = x1
            else:
                right_walls = [
                    t.x0_norm - (1.0 / 595.0) for t in band_tokens if t.x0_norm >= x1 - 1e-4
                ]
                safe_x1 = _clamp_norm(min(right_walls)) if right_walls else 1.0
                if safe_x1 < x1:
                    safe_x1 = x1
        sub_boxes.append(
            _LineSubBox(
                x0_norm=x0,
                y0_norm=y0,
                x1_norm=x1,
                y1_norm=y1,
                mean_confidence=mean_conf,
                line_text=line_str,
                is_ocr=is_ocr_group,
                safe_x0_norm=safe_x0,
                safe_x1_norm=safe_x1,
            )
        )

    return sub_boxes


def _line_bounding_boxes_for_detection(
    indexed: _IndexedPageText,
    det: RawTextSpanDetection,
) -> list[_LineSubBox]:
    """
    Calcula as Bounding Boxes normalizadas por linha a partir de `override_tokens` (quando
    fornecido pelo extrator de tabelas) ou por busca binária `O(log N)` em `indexed`,
    recortando tokens de fronteira para excluir rótulos antes de `:` ou pontuação externa.
    """
    if det.override_tokens:
        sub_boxes = _group_tokens_into_line_subboxes(
            list(det.override_tokens),
            all_page_tokens=indexed.tokens,
        )
        if sub_boxes:
            return sub_boxes

    if not indexed.tokens or det.start_char < 0 or det.end_char <= det.start_char:
        return [
            _LineSubBox(
                x0_norm=0.05,
                y0_norm=0.05,
                x1_norm=0.25,
                y1_norm=0.08,
                mean_confidence=1.0,
                line_text="",
            )
        ]

    first_idx = bisect.bisect_right(indexed.token_ends, det.start_char)
    last_idx = bisect.bisect_left(indexed.token_starts, det.end_char)

    if first_idx >= last_idx:
        return [
            _LineSubBox(
                x0_norm=0.05,
                y0_norm=0.05,
                x1_norm=0.25,
                y1_norm=0.08,
                mean_confidence=1.0,
                line_text="",
            )
        ]

    matched_tokens = list(indexed.tokens[first_idx:last_idx])
    left_sliced = False
    right_sliced = False
    if len(matched_tokens) == 1:
        tok_s = indexed.token_starts[first_idx]
        tok_e = indexed.token_ends[first_idx]
        l_start = max(0, det.start_char - tok_s)
        l_end = min(len(matched_tokens[0].text), det.end_char - tok_s)
        if det.start_char > tok_s or det.end_char < tok_e:
            left_sliced = det.start_char > tok_s
            right_sliced = det.end_char < tok_e
            matched_tokens[0] = _slice_token_horizontally(matched_tokens[0], l_start, l_end)
    else:
        first_tok_s = indexed.token_starts[first_idx]
        if det.start_char > first_tok_s:
            left_sliced = True
            matched_tokens[0] = _slice_token_horizontally(
                matched_tokens[0],
                det.start_char - first_tok_s,
                len(matched_tokens[0].text),
            )
        last_tok_idx = last_idx - 1
        last_tok_s = indexed.token_starts[last_tok_idx]
        last_tok_e = indexed.token_ends[last_tok_idx]
        if det.end_char < last_tok_e:
            right_sliced = True
            matched_tokens[-1] = _slice_token_horizontally(
                matched_tokens[-1],
                0,
                det.end_char - last_tok_s,
            )

    return _group_tokens_into_line_subboxes(
        matched_tokens,
        all_page_tokens=indexed.tokens,
        left_sliced=left_sliced,
        right_sliced=right_sliced,
    )


def _table_rows_for_subboxes(
    sub_boxes: list[_LineSubBox],
    page_result: PageExtractionResult,
) -> set[tuple[int, int]]:
    if not page_result.table_cells or not sub_boxes:
        return set()
    rows: set[tuple[int, int]] = set()
    for cell in page_result.table_cells:
        if not cell.cell_tokens:
            continue
        cx0 = min(t.x0_norm for t in cell.cell_tokens) - 0.004
        cy0 = min(t.y0_norm for t in cell.cell_tokens) - 0.004
        cx1 = max(t.x1_norm for t in cell.cell_tokens) + 0.004
        cy1 = max(t.y1_norm for t in cell.cell_tokens) + 0.004
        for sub in sub_boxes:
            if min(sub.x1_norm, cx1) > max(sub.x0_norm, cx0) and min(sub.y1_norm, cy1) > max(sub.y0_norm, cy0):
                rows.add((cell.table_index, cell.row_index))
                break
    return rows


def _is_same_paragraph_or_line(
    det: RawTextSpanDetection,
    subs: list[_LineSubBox],
    anchor_det: RawTextSpanDetection,
    anchor_subs: list[_LineSubBox],
    full_text: str,
) -> bool:
    if not subs or not anchor_subs:
        return False
    if any(abs(s1.y0_norm - s2.y0_norm) <= 0.014 for s1 in subs for s2 in anchor_subs):
        return True
    y_dist = min(abs(s1.y0_norm - s2.y0_norm) for s1 in subs for s2 in anchor_subs)
    if y_dist > 0.045:
        return False
    if det.start_char >= 0 and anchor_det.start_char >= 0 and full_text:
        seg_s = min(det.end_char, anchor_det.end_char)
        seg_e = max(det.start_char, anchor_det.start_char)
        if seg_e > seg_s:
            between = full_text[seg_s:seg_e]
            if re.search(r"\n\s*\n", between):
                return False
            if re.search(r"\n\s*(?:\d+[\.\)\-]|[-•*])\s+", between):
                return False
            if re.search(r"(?i)\n\s*(?:candidat[oa]|servidor(?:\(a\))?|nome|interessad[oa]|item)\b", between):
                return False
    return True


def _deduplicate_mapped_text_entities(entities: list[DetectedPIIEntity]) -> list[DetectedPIIEntity]:
    """Remove caixas sobrepostas redundantes geradas simultaneamente por `full_text` e `table_cells`."""
    if len(entities) <= 1:
        return entities

    cat_rank = {
        DataCategory.MINOR_OR_DEPENDENT: 3,
        DataCategory.PERSONAL_SENSITIVE: 2,
        DataCategory.FINANCIAL_BANKING: 1,
        DataCategory.PERSONAL_COMMON: 0,
    }

    kept: list[DetectedPIIEntity] = []
    for ent in entities:
        if ent.is_continuation or ent.entity_type in ("FACIAL_PHOTO", "VISUAL_SIGNATURE"):
            kept.append(ent)
            continue
        ent_area = max(1e-9, (ent.x1_norm - ent.x0_norm) * (ent.y1_norm - ent.y0_norm))
        replaced = False
        duplicate = False
        for idx, prev in enumerate(kept):
            if prev.is_continuation or prev.entity_type in ("FACIAL_PHOTO", "VISUAL_SIGNATURE"):
                continue
            if prev.page_number != ent.page_number:
                continue
            same_type = prev.entity_type == ent.entity_type or (
                prev.entity_type in _PERSON_GROUP_TYPES and ent.entity_type in _PERSON_GROUP_TYPES
            )
            if not same_type:
                continue
            inter_w = max(0.0, min(prev.x1_norm, ent.x1_norm) - max(prev.x0_norm, ent.x0_norm))
            inter_h = max(0.0, min(prev.y1_norm, ent.y1_norm) - max(prev.y0_norm, ent.y0_norm))
            inter_area = inter_w * inter_h
            prev_area = max(1e-9, (prev.x1_norm - prev.x0_norm) * (prev.y1_norm - prev.y0_norm))
            overlap_ratio = inter_area / min(ent_area, prev_area)
            if overlap_ratio >= 0.72:
                prev_score = (
                    cat_rank.get(prev.category, 0),
                    len(prev.matched_text),
                    prev.confidence_score,
                )
                ent_score = (
                    cat_rank.get(ent.category, 0),
                    len(ent.matched_text),
                    ent.confidence_score,
                )
                if ent_score > prev_score:
                    kept[idx] = ent
                    replaced = True
                else:
                    duplicate = True
                break
        if not duplicate and not replaced:
            kept.append(ent)
    return kept


def _convert_raw_detections_to_mapped_entities(
    page_result: PageExtractionResult,
    indexed: _IndexedPageText,
    raw_detections: list[RawTextSpanDetection],
    *,
    session_salt: str = "",
    document_profile: DocumentProfile = DocumentProfile.RESTRICTED_PROCESS,
    person_canonical_map: dict[str, str] | None = None,
) -> list[DetectedPIIEntity]:
    """
    Converte as detecções textuais e visuais da página em objetos `DetectedPIIEntity`.
    Ignora elementos na região de rodapé (`y0_norm > 0.93`).
    """
    mapped: list[DetectedPIIEntity] = []
    canon_map = person_canonical_map or {}

    det_entries: list[tuple[RawTextSpanDetection, list[_LineSubBox], set[tuple[int, int]]]] = []
    for det in raw_detections:
        sub_boxes = _line_bounding_boxes_for_detection(indexed=indexed, det=det)
        non_footer_subs = [s for s in sub_boxes if not is_footer_region(s.y0_norm)]
        if not non_footer_subs:
            continue
        t_rows = _table_rows_for_subboxes(non_footer_subs, page_result) if document_profile == DocumentProfile.PUBLIC_TRANSPARENCY else set()
        det_entries.append((det, non_footer_subs, t_rows))

    sensitive_minor_anchors: list[tuple[RawTextSpanDetection, list[_LineSubBox], set[tuple[int, int]]]] = []
    sensitive_minor_table_rows: set[tuple[int, int]] = set()
    if document_profile == DocumentProfile.PUBLIC_TRANSPARENCY:
        for det, subs, t_rows in det_entries:
            if det.category in (DataCategory.PERSONAL_SENSITIVE, DataCategory.MINOR_OR_DEPENDENT):
                sensitive_minor_anchors.append((det, subs, t_rows))
                sensitive_minor_table_rows.update(t_rows)
        for cell in page_result.table_cells:
            if cell.row_index > 0 and (_MINOR_CONTEXT_KW_RE.search(cell.cell_text) or _SENSITIVE_MED_KW_RE.search(cell.cell_text)):
                sensitive_minor_table_rows.add((cell.table_index, cell.row_index))

    for det, non_footer_subs, t_rows in det_entries:
        linked_to_minor_or_sensitive = False
        if (
            document_profile == DocumentProfile.PUBLIC_TRANSPARENCY
            and det.category not in (DataCategory.PERSONAL_SENSITIVE, DataCategory.MINOR_OR_DEPENDENT)
        ):
            if t_rows and (t_rows & sensitive_minor_table_rows):
                linked_to_minor_or_sensitive = True
            elif any(
                _is_same_paragraph_or_line(det, non_footer_subs, a_det, a_subs, indexed.text)
                for a_det, a_subs, _ in sensitive_minor_anchors
            ):
                linked_to_minor_or_sensitive = True
            elif det.start_char >= 0 and indexed.text:
                line_s = indexed.text.rfind("\n", 0, det.start_char)
                line_s = 0 if line_s == -1 else line_s + 1
                line_e = indexed.text.find("\n", det.end_char)
                line_e = len(indexed.text) if line_e == -1 else line_e
                same_line_slice = indexed.text[line_s:line_e]
                if _MINOR_CONTEXT_KW_RE.search(same_line_slice) or _SENSITIVE_MED_KW_RE.search(same_line_slice):
                    linked_to_minor_or_sensitive = True

        group_seed_text = det.matched_text
        if det.entity_type in _PERSON_GROUP_TYPES:
            norm_person = _normalize_ascii_upper_space(det.matched_text)
            group_seed_text = canon_map.get(norm_person, det.matched_text)

        grp_id = compute_entity_group_id(det.entity_type, group_seed_text, session_salt=session_salt)
        rec_mode = compute_recommended_action_mode(
            entity_type=det.entity_type,
            category=det.category,
            document_profile=document_profile,
            linked_to_minor_or_sensitive=linked_to_minor_or_sensitive,
        )
        full_masked = generate_masked_preview(det.entity_type, det.matched_text)

        for sub_idx, sub in enumerate(non_footer_subs):
            combined_score = round(min(1.0, max(0.0, det.confidence * sub.mean_confidence)), 4)
            conf_level = compute_confidence_level(combined_score)
            matched_line_text = sub.line_text if (len(non_footer_subs) > 1 and sub.line_text) else det.matched_text
            is_cont = sub_idx > 0
            sub_masked = "" if is_cont else full_masked

            mapped.append(
                DetectedPIIEntity(
                    page_number=page_result.page_number,
                    entity_type=det.entity_type,
                    category=det.category,
                    x0_norm=sub.x0_norm,
                    y0_norm=sub.y0_norm,
                    x1_norm=sub.x1_norm,
                    y1_norm=sub.y1_norm,
                    confidence_score=combined_score,
                    confidence_level=conf_level,
                    group_id=grp_id,
                    detector_source=det.detector_source,
                    matched_text=matched_line_text,
                    action_mode=rec_mode,
                    recommended_mode=rec_mode,
                    masked_preview=sub_masked,
                    is_continuation=is_cont,
                    is_ocr=sub.is_ocr,
                    safe_x0_norm=sub.safe_x0_norm,
                    safe_x1_norm=sub.safe_x1_norm,
                )
            )

    mapped = _deduplicate_mapped_text_entities(mapped)

    for vis_idx, vis in enumerate(page_result.visual_elements):
        if is_footer_region(vis.y0_norm):
            continue
        is_photo = vis.kind == VisualElementKind.FACIAL_PHOTO
        ent_type = "FACIAL_PHOTO" if is_photo else "VISUAL_SIGNATURE"
        vis_category = DataCategory.PERSONAL_SENSITIVE if is_photo else DataCategory.PERSONAL_COMMON
        vis_masked = "[FOTO PROTEGIDA]" if is_photo else "[PROTEGIDO]"
        score = round(float(vis.confidence), 4)
        grp_id = compute_entity_group_id(
            ent_type,
            f"PAGE_{page_result.page_number}_{vis_idx}_{vis.x0_norm:.2f}_{vis.y0_norm:.2f}",
            session_salt=session_salt,
        )
        mapped.append(
            DetectedPIIEntity(
                page_number=page_result.page_number,
                entity_type=ent_type,
                category=vis_category,
                x0_norm=vis.x0_norm,
                y0_norm=vis.y0_norm,
                x1_norm=vis.x1_norm,
                y1_norm=vis.y1_norm,
                confidence_score=score,
                confidence_level=compute_confidence_level(score),
                group_id=grp_id,
                detector_source=vis.detection_method,
                matched_text=f"[{ent_type}]",
                action_mode=RedactionActionMode.BLACKOUT,
                recommended_mode=RedactionActionMode.BLACKOUT,
                masked_preview=vis_masked,
                is_continuation=False,
            )
        )

    return mapped


def propagate_person_names_across_pages(
    pages: list[PageExtractionResult],
    confirmed_names: list[tuple[str, str, DataCategory, float]],
    *,
    session_salt: str = "",
    document_profile: DocumentProfile = DocumentProfile.RESTRICTED_PROCESS,
) -> list[DetectedPIIEntity]:
    """
    Executa varredura complementar de nomes completos confirmados em outras páginas do mesmo
    documento sobre `pages` (suporta pipeline progressivo First-Page-First).
    """
    if not pages or not confirmed_names:
        return []

    indexed_by_page: dict[int, _IndexedPageText] = {}
    batch_payload: list[tuple[int, str, list]] = []
    empty_results: dict[int, list[RawTextSpanDetection]] = {}

    for p in pages:
        indexed = _build_indexed_text_from_page(p)
        indexed_by_page[p.page_number] = indexed
        batch_payload.append((p.page_number, indexed.text, p.table_cells))
        empty_results[p.page_number] = []

    raw_by_page = propagate_person_names_across_batch(
        batch_payload,
        empty_results,
        extra_confirmed_names=confirmed_names,
    )

    all_person_names = [n[0] for n in confirmed_names]
    for dets in raw_by_page.values():
        for d in dets:
            if d.entity_type in _PERSON_GROUP_TYPES:
                all_person_names.append(d.matched_text)
    canonical_map = build_person_canonical_map(all_person_names)

    all_entities: list[DetectedPIIEntity] = []
    for p in pages:
        raw_dets = raw_by_page.get(p.page_number, [])
        if not raw_dets:
            continue
        indexed = indexed_by_page[p.page_number]
        page_entities = _convert_raw_detections_to_mapped_entities(
            page_result=p,
            indexed=indexed,
            raw_detections=raw_dets,
            session_salt=session_salt,
            document_profile=document_profile,
            person_canonical_map=canonical_map,
        )
        all_entities.extend(page_entities)

    return all_entities


def detect_pii_in_pages(
    pages: list[PageExtractionResult],
    *,
    session_salt: str = "",
    document_profile: DocumentProfile | None = None,
) -> list[DetectedPIIEntity]:
    """
    Executa a detecção de PII em lote sobre uma lista de `PageExtractionResult`.
    """
    if not pages:
        return []

    indexed_by_page: dict[int, _IndexedPageText] = {}
    batch_payload: list[tuple[int, str, list]] = []

    for p in pages:
        indexed = _build_indexed_text_from_page(p)
        indexed_by_page[p.page_number] = indexed
        batch_payload.append((p.page_number, indexed.text, p.table_cells))

    resolved_profile = document_profile
    if resolved_profile is None:
        first_page_text = indexed_by_page[pages[0].page_number].text
        resolved_profile = classify_document_profile(first_page_text)

    raw_by_page = analyze_pages_batch(batch_payload)

    all_person_names = [
        d.matched_text
        for dets in raw_by_page.values()
        for d in dets
        if d.entity_type in _PERSON_GROUP_TYPES
    ]
    canonical_map = build_person_canonical_map(all_person_names)

    all_entities: list[DetectedPIIEntity] = []
    for p in pages:
        raw_dets = raw_by_page.get(p.page_number, [])
        indexed = indexed_by_page[p.page_number]
        page_entities = _convert_raw_detections_to_mapped_entities(
            page_result=p,
            indexed=indexed,
            raw_detections=raw_dets,
            session_salt=session_salt,
            document_profile=resolved_profile,
            person_canonical_map=canonical_map,
        )
        all_entities.extend(page_entities)

    return all_entities


def detect_pii_in_single_page(
    page_result: PageExtractionResult,
    *,
    session_salt: str = "",
    document_profile: DocumentProfile | None = None,
) -> list[DetectedPIIEntity]:
    """Atalho para detectar e mapear PII de uma única página extraída (suporte a streaming página a página)."""
    return detect_pii_in_pages(
        [page_result],
        session_salt=session_salt,
        document_profile=document_profile,
    )
