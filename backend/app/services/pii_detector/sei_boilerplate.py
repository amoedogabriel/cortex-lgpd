"""
Detecção estrutural de blocos padrão do SEI/UFAM (`sei_boilerplate.py`).

Identifica com precisão geométrica e textual os três blocos padronizados de documentos
emitidos no Sistema Eletrônico de Informações (SEI/UFAM) que, quando a regra configurável
`ignore_sei_standard_blocks` está ativa (padrão), não devem ser tarjados/anonimizados:

1. **Cabeçalho padrão do SEI (`SEI_HEADER`)**:
   - Faixa superior de impressão/continuação (`SEI/UFAM - ...`, `Referência: Processo nº ...`,
     `SEI nº XXXXXXX`, URL `sei.ufam.edu.br`);
   - Timbre institucional (brasão da República, `Ministério da Educação`,
     `Universidade Federal do Amazonas`, unidade/setor/pró-reitoria/escritório e eventuais
     contatos institucionais contíguos), encerrando estritamente ANTES de `Interessado:`,
     `Assunto:`, `Requerente:` ou `DESPACHO`.

2. **Rodapé padrão do SEI (`SEI_FOOTER`)**:
   - Endereço institucional da UFAM (`Av. General Rodrigo Octávio, 6200`,
     `Campus Universitário Senador Arthur Virgílio Filho`), telefone institucional,
     CEP institucional (`69080-900`, `69077-000`, `69067-005`), e-mail `@ufam.edu.br`;
   - Referência e paginação SEI (`Referência: Processo nº ...`, `SEI nº ...`,
     `Despacho XXXXXXX SEI 23105.XXXXXX/XXXX-XX / pg. N`);
   - Bloco de autenticidade, código verificador, código CRC, linha `Criado por ...`
     e QR Code de conferência.

3. **Assinatura final de despacho (`DESPACHO_SIGNATURE`)**:
   - **Despacho pré-assinatura eletrônica**: local e data centralizados (`Manaus, DD de <mês> de AAAA.`)
     ou fecho administrativo (`Atenciosamente,`, `Respeitosamente,`, `Cordialmente,`,
     `Encaminhe-se`, `Cumpra-se`, `Publique-se`, `De acordo`) seguidos do nome do signatário,
     cargo/função institucional (`Encarregado pelo Tratamento de Dados Pessoais`, `Pró-Reitor`,
     `Diretor`, `Coordenador`, `Chefe`, etc.) e ato opcional (`Portaria NNN/AAAA (XXXXXXX)`,
     `SIAPE`, `(Assinado eletronicamente)`);
   - **Despacho pós-assinatura eletrônica SEI**: bloco `Documento assinado eletronicamente por ...`
     até o Decreto nº 8.539/2015, bloco de autenticidade, CRC e QR Code adjacente.

Fail-Safe LGPD:
- Jamais suprime `CPF`, `CNH`, `PIS_PASEP`, `TITULO_ELEITOR`, dados bancários/PIX/cartão,
  dados sensíveis de saúde/filiação sindical/crença/raça/genética, dados de menores/dependentes,
  regras `CUSTOM_REGEX` definidas pelo usuário ou e-mails pessoais não institucionais.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.models.enums import DataCategory
from app.services.pdf_extractor.extractor import (
    DetectedVisualElement,
    PageExtractionResult,
    UnifiedLine,
)

SeiZoneKind = Literal["SEI_HEADER", "SEI_FOOTER", "DESPACHO_SIGNATURE"]

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")

# --- Âncoras de Cabeçalho Padrão do SEI (`SEI_HEADER`) ---
_SEI_TOP_BANNER_RE = re.compile(
    r"(?i)(?:^\s*SEI\s*/\s*UFAM\b|"
    r"https?://sei\.ufam\.edu\.br|"
    r"Sistema\s+Eletr[oô]nico\s+de\s+Informa[cç][oõ]es|"
    r"^\s*Refer[eê]ncia\s*:\s*Processo\s+n[º°]?|"
    r"^\s*SEI\s+n[º°]?\s*\d{6,9}\b|"
    r"^\s*\d{2}/\d{2}/\d{4}\s*,\s*\d{2}:\d{2}\b)"
)

_SEI_TIMBRE_ANCHOR_RE = re.compile(
    r"(?i)(?:Minist[eé]rio\s+da\s+Educa[cç][aã]o|"
    r"Universidade\s+Federal\s+do\s+Amazonas|"
    r"Servi[cç]o\s+P[uú]blico\s+Federal|"
    r"Rep[uú]blica\s+Federativa\s+do\s+Brasil)"
)

_SEI_TIMBRE_STOP_RE = re.compile(
    r"(?i)(?:^\s*(?:Interessad[oa](?:\(a\))?|Assunto|Requerente|Servidor(?:\(a\))?|Nome|CPF|Objeto|Ementa)\s*:|"
    r"\bProcesso\s+(?:Individual|Reservad[oa]|Nominal|Disciplinar)\b|"
    r"^\s*(?:DESPACHO|OF[IÍ]CIO|MEMORANDO|PARECER|PORTARIA|CERTID[AÃ]O|NOTA\s+T[EÉ]CNICA|DECLARA[CÇ][AÃ]O|DECIS[AÃ]O|TERMO|RELAT[OÓ]RIO|ATA)\b)"
)

_SEI_TIMBRE_CONTINUATION_RE = re.compile(
    r"(?i)(?:Minist[eé]rio|Universidade|Poder\s+Executivo|Servi[cç]o\s+P[uú]blico|"
    r"Pr[oó]-Reitoria|Reitoria|Gabinete|Escrit[oó]rio|Instituto|Faculdade|Departamento|"
    r"Coordena[cç][aã]o|Diretoria|Divis[aã]o|Se[cç][aã]o|Superintend[eê]ncia|Secretaria|"
    r"Comiss[aã]o|Conselho|Setor|Centro|Hospital|N[uú]cleo|Ouvidoria|Corregedoria|Auditoria|"
    r"Procuradoria|Biblioteca|Prefeitura\s+do\s+Campus|UFAM\b|"
    r"Av(?:enida)?\.?\s+(?:General\s+)?Rodrigo\s+Oct[aá]vio|Campus\s+Universit[aá]rio|"
    r"CEP\s*:?\s*(?:69\.?080-?900|69\.?077-?000|69\.?067-?005)|"
    r"Telefone|Fone|@ufam\.edu\.br|ufam\.edu\.br|Manaus\s*/\s*AM|CNPJ)"
)

# --- Âncoras de Rodapé Padrão do SEI (`SEI_FOOTER`) ---
_SEI_FOOTER_ANCHOR_RE = re.compile(
    r"(?i)(?:Av(?:enida)?\.?\s+(?:General\s+)?Rodrigo\s+Oct[aá]vio|"
    r"Campus\s+Universit[aá]rio\s+Senador\s+Arthur\s+Virg[ií]lio|"
    r"CEP\s*:?\s*(?:69\.?080-?900|69\.?077-?000|69\.?067-?005)|"
    r"^\s*Refer[eê]ncia\s*:\s*Processo\s+n[º°]?|"
    r"^\s*SEI\s+n[º°]?\s*\d{6,9}\b|"
    r"\b(?:Despacho|Of[ií]cio|Memorando|Parecer|Portaria|Nota\s+T[eé]cnica|Certid[aã]o|"
    r"Informa[cç][aã]o|Termo|Ata|Decis[aã]o|Relat[oó]rio|Declara[cç][aã]o|Anexo|Planilha)\s+\d{5,9}\s+SEI\s+\d{5}\.\d{6}/\d{4}-\d{2}|"
    r"\bSEI\s+\d{5}\.\d{6}/\d{4}-\d{2}\s*/\s*pg\.?\s*\d+|"
    r"A\s+autenticidade\s+deste\s+documento\s+pode\s+ser\s+conferida|"
    r"sei\.ufam\.edu\.br|controlador_externo\.php|"
    r"c[oó]digo\s+verificador\s+\d{5,9}|c[oó]digo\s+CRC\s+[0-9A-Fa-f]{6,12}|"
    r"^\s*Criado\s+por\s+.+,\s*vers[aã]o\s+\d+)"
)

_SEI_FOOTER_INSTITUTIONAL_CONTACT_RE = re.compile(
    r"(?i)(?:@ufam\.edu\.br|www\.ufam\.edu\.br)"
)

# --- Âncoras de Assinatura Final de Despacho (`DESPACHO_SIGNATURE`) ---
_SEI_ELECTRONIC_SIG_START_RE = re.compile(
    r"(?i)(?:Documento\s+assinado\s+eletronicamente\s+por\b|Assinado\s+eletronicamente\s+por\b)"
)

_SEI_ELECTRONIC_SIG_NAME_RE = re.compile(
    r"(?i)assinado\s+eletronicamente\s+por\s+([^,\n]{4,80}?)(?:,|\s+em\s+\d{2}/\d{2}/\d{4}|$)"
)

_SEI_ELECTRONIC_SIG_CONT_RE = re.compile(
    r"(?i)(?:Documento\s+assinado\s+eletronicamente|Assinado\s+eletronicamente|"
    r"hor[aá]rio\s+oficial\s+de\s+Bras[ií]lia|Decreto\s+n[º°]?\s*8\.?539|"
    r"art\.?\s*6[º°]|8\s+de\s+outubro\s+de\s+2015|"
    r"A\s+autenticidade\s+deste\s+documento|pode\s+ser\s+conferida|"
    r"sei\.ufam\.edu\.br|controlador_externo\.php|"
    r"c[oó]digo\s+verificador|c[oó]digo\s+CRC|"
    r"informando\s+o\s+c[oó]digo|^\s*Criado\s+por\s+)"
)

_DESPACHO_LOCAL_DATE_RE = re.compile(
    r"(?i)^\s*(?:Manaus|Humait[aá]|Parintins|Coari|Itacoatiara|Benjamin\s+Constant|"
    r"Tef[eé]|S[aã]o\s+Gabriel(?:\s+da\s+Cachoeira)?|Tabatinga|Bras[ií]lia)"
    r"(?:\s*/\s*[A-Z]{2})?\s*,\s*\d{1,2}\s+de\s+"
    r"(?:janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
    r"\s+de\s+\d{4}\.?\s*$"
)

_DESPACHO_SALUTATION_RE = re.compile(
    r"(?i)^\s*(?:Atenciosamente|Respeitosamente|Cordialmente|"
    r"Sem\s+mais\s+para\s+o\s+momento|[ÀA]\s+considera[cç][aã]o\s+superior|"
    r"De\s+acordo|Ciente\s+e\s+de\s+acordo|Encaminhe-se|Cumpra-se|Publique-se|"
    r"Registre-se|Arquive-se|Defiro|Indefiro)[,.:!]?\s*$"
)

_ASSINADO_TAG_RE = re.compile(
    r"(?i)^\s*\(?\s*Assinado\s+(?:eletronicamente|digitalmente)\s*\)?\s*$"
)

_SIGNATORY_ROLE_OR_ACT_RE = re.compile(
    r"(?i)(?:\b(?:Encarregad[oa](?:\s+pelo\s+Tratamento\s+de\s+Dados(?:\s+Pessoais)?)?|"
    r"Pr[oó]-Reitor(?:a)?|Vice-Reitor(?:a)?|Reitor(?:a)?|"
    r"Diretor(?:a)?(?:\s+(?:de|da|do|Geral|Executivo|Adjunto|Substituto))?|"
    r"Coordenador(?:a)?(?:\s+(?:de|da|do|Geral|Substituto))?|"
    r"Chefe(?:\s+(?:de|da|do|de\s+Gabinete|Substituto))?|"
    r"Subchefe|Secret[aá]ri[oa](?:\s+(?:Executivo|Geral|de|da|do))?|"
    r"Presidente(?:\s+(?:da|de|do))?|Assessor(?:a)?(?:\s+(?:T[eé]cnico|Jur[ií]dico|Especial|de|da|do))?|"
    r"Gerente(?:\s+(?:de|da|do))?|Supervisor(?:a)?|Ouvidor(?:a)?|Corregedor(?:a)?|"
    r"Procurador(?:a)?|Auditor(?:a)?|Superintendente|"
    r"Assistente\s+em\s+Administra[cç][aã]o|T[eé]cnico\s+Administrativo|"
    r"Analista\s+de\s+Tecnologia\s+da\s+Informa[cç][aã]o|"
    r"T[eé]cnico\s+de\s+Tecnologia\s+da\s+Informa[cç][aã]o|"
    r"Administrador(?:a)?|Contador(?:a)?|Engenheir[oa]|Bibliotec[aá]ri[oa]|"
    r"Professor(?:a)?(?:\s+(?:Titular|Associado|Adjunto|Assistente|Auxiliar|do\s+Magist[eé]rio))?|"
    r"Servidor(?:a)?\s+Respons[aá]vel|Respons[aá]vel\s+pelo\s+Setor|"
    r"Escrit[oó]rio\s+de\s+Prote[cç][aã]o\s+de\s+Dados|"
    r"Portaria\s+(?:n[º°]?\s*)?\d+/\d{4}|"
    r"SIAPE\s*[:nº°]?\s*\d{6,8}|Matr[ií]cula\s+SIAPE)\b|"
    r"@ufam\.edu\.br|"
    r"^\s*\(?\s*Assinado\s+(?:eletronicamente|digitalmente)\s*\)?\s*$)"
)

_SIGNATURE_BLOCK_STOP_RE = re.compile(
    r"(?i)(?:\b(?:CPF|RG|CNPJ|Ag[eê]ncia|Conta\s+Corrente|Conta\s+Poupan[cç]a|PIX|CID-?1[01]|"
    r"Interessad[oa]\s*:|Requerente\s*:|Processo\s+Individual|residente\s+e\s+domiciliad)\b|"
    r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b)"
)

_UNDERSCORE_LINE_RE = re.compile(r"^\s*_{5,}\s*$")
_NAME_PARTICLES = frozenset({"da", "de", "do", "dos", "das", "e"})
_CAP_TOKEN_RE = re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç'.-]{0,}$")

_INSTITUTIONAL_EMAIL_DOMAINS = (
    "@ufam.edu.br",
    ".ufam.edu.br",
    "@mec.gov.br",
    ".gov.br",
    ".edu.br",
)

# Fail-Safe LGPD: tipos que NUNCA são suprimidos automaticamente por zonas SEI
_NEVER_IGNORE_ENTITY_TYPES: frozenset[str] = frozenset(
    {
        "CPF",
        "CNH",
        "PIS_PASEP",
        "TITULO_ELEITOR",
        "CREDIT_CARD",
        "BANK_AGENCY",
        "BANK_OPERATION",
        "BANK_ACCOUNT",
        "PIX_KEY",
        "HEALTH_DATA",
        "UNION_AFFILIATION",
        "ETHNIC_RACIAL_DECLARATION",
        "RELIGIOUS_BELIEF",
        "GENETIC_DATA",
        "POLITICAL_OPINION",
        "BIOMETRIC_DATA",
        "DEPENDENT_DATA",
        "FILIACAO",
        "REMUNERATION",
    }
)


@dataclass(frozen=True, slots=True)
class SeiIgnoredZone:
    """Faixa geométrica normalizada [0.0, 1.0] de bloco padrão SEI ignorável."""

    kind: SeiZoneKind
    y0_norm: float
    y1_norm: float
    x0_norm: float = 0.0
    x1_norm: float = 1.0


@dataclass(frozen=True, slots=True)
class SeiPageBoilerplateAnalysis:
    """Resultado da inspeção de cabeçalho, rodapé e assinatura de despacho SEI em uma página."""

    zones: tuple[SeiIgnoredZone, ...] = ()
    ignored_signatory_names: frozenset[str] = frozenset()
    has_sei_header: bool = False
    has_sei_footer: bool = False
    has_despacho_signature: bool = False


def _clean_line_text(text: str) -> str:
    cleaned = _ZERO_WIDTH_RE.sub("", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _normalize_ascii_upper(val: str) -> str:
    nfkd = unicodedata.normalize("NFKD", _clean_line_text(val))
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_only).strip().upper()


def _clamp(val: float) -> float:
    return round(max(0.0, min(1.0, float(val))), 6)


def _is_centered_line(line: UnifiedLine) -> bool:
    """Retorna True se a linha estiver aproximadamente centralizada na página."""
    center_x = (line.x0_norm + line.x1_norm) / 2.0
    width = line.x1_norm - line.x0_norm
    return line.x0_norm >= 0.14 and width <= 0.78 and abs(center_x - 0.50) <= 0.18


def _looks_like_signatory_name_line(text: str) -> bool:
    """
    Verifica se uma linha curta representa o nome de um signatário de despacho
    (ex.: `MARIA CLARA DE OLIVEIRA`, `Prof. Dr. João Pedro da Silva`), rejeitando
    frases de corpo de texto, rótulos com `:` ou strings com dígitos.
    """
    cleaned = _clean_line_text(text)
    if not cleaned or len(cleaned) < 5 or len(cleaned) > 78:
        return False
    if any(ch in cleaned for ch in (":", ";", "@", "/", "%", "$")):
        return False
    if re.search(r"\d", cleaned):
        return False
    if _SIGNATURE_BLOCK_STOP_RE.search(cleaned):
        return False
    if _DESPACHO_LOCAL_DATE_RE.match(cleaned) or _DESPACHO_SALUTATION_RE.match(cleaned):
        return False
    if _ASSINADO_TAG_RE.match(cleaned):
        return False

    # Remove títulos acadêmicos/honoríficos iniciais comuns em assinaturas universitárias
    stripped = re.sub(
        r"(?i)^(?:(?:Prof\.?(?:a\.?)?|Dr\.?(?:a\.?)?|Me\.?|Ma\.?|Eng\.?(?:a\.?)?|Sr\.?(?:a\.?)?)\s+)+",
        "",
        cleaned,
    ).strip()
    if not stripped:
        return False

    tokens = [t.strip(".,()") for t in stripped.split() if t.strip(".,()")]
    if len(tokens) < 2 or len(tokens) > 8:
        return False

    cap_non_particles = 0
    lower_non_particles = 0
    for tok in tokens:
        if tok.lower() in _NAME_PARTICLES:
            continue
        if _CAP_TOKEN_RE.match(tok):
            cap_non_particles += 1
        else:
            lower_non_particles += 1

    if cap_non_particles < 2 or lower_non_particles > 0:
        return False

    # Evita confundir cargo isolado como se fosse nome próprio
    if _SIGNATORY_ROLE_OR_ACT_RE.search(stripped):
        return False

    return True


def _extract_sorted_visual_lines(page_result: PageExtractionResult) -> list[UnifiedLine]:
    """Obtém linhas visuais ordenadas de cima para baixo (usando `lines` ou reconstruindo via `tokens`)."""
    raw_lines: list[UnifiedLine] = []
    if page_result.lines:
        for ln in page_result.lines:
            txt = _clean_line_text(ln.text)
            if txt:
                raw_lines.append(
                    UnifiedLine(
                        text=txt,
                        page_number=ln.page_number,
                        x0_norm=ln.x0_norm,
                        y0_norm=ln.y0_norm,
                        x1_norm=ln.x1_norm,
                        y1_norm=ln.y1_norm,
                        confidence=ln.confidence,
                        source=ln.source,
                    )
                )
    elif page_result.tokens:
        sorted_tokens = sorted(
            (t for t in page_result.tokens if _clean_line_text(t.text)),
            key=lambda t: (round(t.y0_norm, 3), t.x0_norm),
        )
        groups: list[list] = []
        for tok in sorted_tokens:
            if not groups or abs(tok.y0_norm - groups[-1][0].y0_norm) > 0.007:
                groups.append([tok])
            else:
                groups[-1].append(tok)
        for grp in groups:
            grp.sort(key=lambda t: t.x0_norm)
            txt = _clean_line_text(" ".join(t.text for t in grp))
            if not txt:
                continue
            raw_lines.append(
                UnifiedLine(
                    text=txt,
                    page_number=page_result.page_number,
                    x0_norm=min(t.x0_norm for t in grp),
                    y0_norm=min(t.y0_norm for t in grp),
                    x1_norm=max(t.x1_norm for t in grp),
                    y1_norm=max(t.y1_norm for t in grp),
                    confidence=sum(t.confidence for t in grp) / len(grp),
                    source=grp[0].source,
                )
            )

    raw_lines.sort(key=lambda ln: (round(ln.y0_norm, 3), ln.x0_norm))
    return raw_lines


def analyze_sei_boilerplate_zones(page_result: PageExtractionResult) -> SeiPageBoilerplateAnalysis:
    """
    Inspeciona as linhas da página e retorna todas as zonas estruturais de cabeçalho SEI,
    rodapé SEI e assinatura final de despacho detectadas com alta precisão.
    """
    lines = _extract_sorted_visual_lines(page_result)
    if not lines:
        return SeiPageBoilerplateAnalysis()

    zones: list[SeiIgnoredZone] = []
    ignored_names: set[str] = set()
    has_header = False
    has_footer = False
    has_signature = False

    # =========================================================================
    # 1. CABEÇALHO PADRÃO DO SEI (`SEI_HEADER`)
    # =========================================================================
    # 1A. Faixa superior de impressão/continuação (`y0_norm <= 0.10`)
    for ln in lines:
        if ln.y0_norm > 0.10:
            break
        if _SEI_TOP_BANNER_RE.search(ln.text):
            zones.append(
                SeiIgnoredZone(
                    kind="SEI_HEADER",
                    y0_norm=_clamp(ln.y0_norm - 0.005),
                    y1_norm=_clamp(ln.y1_norm + 0.005),
                )
            )
            has_header = True

    # 1B. Timbre institucional MEC/UFAM (`y0_norm <= 0.28`)
    timbre_start_idx: int | None = None
    for idx, ln in enumerate(lines):
        if ln.y0_norm > 0.22:
            break
        if _SEI_TIMBRE_ANCHOR_RE.search(ln.text):
            # Evita confundir títulos corridos à esquerda (ex.: "UFAM - Portaria N 1/2026 - Processo ...")
            # com timbre institucional padronizado do SEI.
            if not _is_centered_line(ln) and (len(ln.text) > 60 or re.search(r"(?i)\b(?:Portaria|Processo|Assentamento|CPF|Servidor)\b", ln.text)):
                continue
            timbre_start_idx = idx
            break

    if timbre_start_idx is not None:
        timbre_end_y1 = lines[timbre_start_idx].y1_norm
        prev_y1 = timbre_end_y1
        for idx in range(timbre_start_idx + 1, len(lines)):
            ln = lines[idx]
            if ln.y0_norm > 0.28:
                break
            if (ln.y0_norm - prev_y1) > 0.045:
                break
            if _SEI_TIMBRE_STOP_RE.search(ln.text):
                break
            if _SIGNATURE_BLOCK_STOP_RE.search(ln.text):
                break
            if _SEI_TIMBRE_CONTINUATION_RE.search(ln.text) or (
                _is_centered_line(ln)
                and len(ln.text) <= 85
                and not re.match(r"(?i)^\s*Processo\s+n[º°]?", ln.text)
            ):
                timbre_end_y1 = max(timbre_end_y1, ln.y1_norm)
                prev_y1 = ln.y1_norm
            else:
                break

        # Cobre de y=0.0 até o fim do timbre (inclui brasão da República acima do texto)
        zones.append(
            SeiIgnoredZone(
                kind="SEI_HEADER",
                y0_norm=0.0,
                y1_norm=_clamp(timbre_end_y1 + 0.005),
            )
        )
        has_header = True

    # =========================================================================
    # 2. RODAPÉ PADRÃO DO SEI (`SEI_FOOTER`)
    # =========================================================================
    footer_anchor_y0: float | None = None
    for ln in lines:
        if ln.y0_norm < 0.74:
            continue
        if _SEI_FOOTER_ANCHOR_RE.search(ln.text):
            footer_anchor_y0 = ln.y0_norm
            break
        if ln.y0_norm >= 0.84 and _SEI_FOOTER_INSTITUTIONAL_CONTACT_RE.search(ln.text):
            if re.search(r"(?i)(?:CEP|Manaus|Telefone|Fone|Av\.|Avenida|Campus)", ln.text):
                footer_anchor_y0 = ln.y0_norm
                break

    if footer_anchor_y0 is not None:
        zones.append(
            SeiIgnoredZone(
                kind="SEI_FOOTER",
                y0_norm=_clamp(footer_anchor_y0 - 0.005),
                y1_norm=1.0,
            )
        )
        has_footer = True

    # =========================================================================
    # 3. ASSINATURA FINAL DE DESPACHO (`DESPACHO_SIGNATURE`)
    # =========================================================================
    # 3A. Bloco pós-assinatura eletrônica do SEI
    idx = 0
    while idx < len(lines):
        ln = lines[idx]
        if _SEI_ELECTRONIC_SIG_START_RE.search(ln.text):
            sig_y0 = ln.y0_norm
            sig_y1 = ln.y1_norm
            m_name = _SEI_ELECTRONIC_SIG_NAME_RE.search(ln.text)
            if m_name:
                ignored_names.add(_normalize_ascii_upper(m_name.group(1)))

            j = idx + 1
            prev_y1 = sig_y1
            while j < len(lines):
                nxt = lines[j]
                if (nxt.y0_norm - prev_y1) > 0.065:
                    break
                if _SIGNATURE_BLOCK_STOP_RE.search(nxt.text):
                    break
                if _SEI_ELECTRONIC_SIG_CONT_RE.search(nxt.text) or (
                    (nxt.y0_norm - prev_y1) <= 0.020
                    and re.search(
                        r"(?i)(?:conforme|fundamento|Bras[ií]lia|Decreto|outubro|verificador|CRC|autenticidade|sei\.ufam|vers[aã]o|por\s+)",
                        nxt.text,
                    )
                ):
                    sig_y1 = max(sig_y1, nxt.y1_norm)
                    prev_y1 = nxt.y1_norm
                    m_sub = _SEI_ELECTRONIC_SIG_NAME_RE.search(nxt.text)
                    if m_sub:
                        ignored_names.add(_normalize_ascii_upper(m_sub.group(1)))
                    j += 1
                else:
                    break

            zones.append(
                SeiIgnoredZone(
                    kind="DESPACHO_SIGNATURE",
                    y0_norm=_clamp(sig_y0 - 0.010),
                    y1_norm=_clamp(sig_y1 + 0.015),
                )
            )
            has_signature = True
            idx = j
            continue
        idx += 1

    # 3B. Bloco pré-assinatura / fecho formal de despacho (Local+Data ou Saudação + Signatário + Cargo/Portaria)
    footer_cutoff = footer_anchor_y0 if footer_anchor_y0 is not None else 0.96
    for i, ln in enumerate(lines):
        if ln.y0_norm >= footer_cutoff:
            break
        is_local_date = bool(_DESPACHO_LOCAL_DATE_RE.match(ln.text))
        is_salutation = bool(_DESPACHO_SALUTATION_RE.match(ln.text))
        is_signed_tag = bool(_ASSINADO_TAG_RE.match(ln.text))
        if not (is_local_date or is_salutation or is_signed_tag):
            continue

        collected_indices: list[int] = [i]
        has_name = False
        has_role = False
        candidate_names: list[str] = []
        prev_y1 = ln.y1_norm

        for j in range(i + 1, min(len(lines), i + 8)):
            nxt = lines[j]
            if nxt.y0_norm >= footer_cutoff - 0.004:
                break
            if _SEI_FOOTER_ANCHOR_RE.search(nxt.text) or _SEI_ELECTRONIC_SIG_START_RE.search(nxt.text):
                break
            max_gap = 0.14 if len(collected_indices) == 1 else 0.055
            if (nxt.y0_norm - prev_y1) > max_gap:
                break
            if _SIGNATURE_BLOCK_STOP_RE.search(nxt.text):
                break
            if _UNDERSCORE_LINE_RE.match(nxt.text) or _ASSINADO_TAG_RE.match(nxt.text):
                collected_indices.append(j)
                prev_y1 = nxt.y1_norm
                continue
            if _SIGNATORY_ROLE_OR_ACT_RE.search(nxt.text) and len(nxt.text) <= 95:
                has_role = True
                collected_indices.append(j)
                prev_y1 = nxt.y1_norm
                continue
            if not has_name and _looks_like_signatory_name_line(nxt.text):
                has_name = True
                candidate_names.append(nxt.text)
                collected_indices.append(j)
                prev_y1 = nxt.y1_norm
                continue
            # Linha curta de unidade institucional logo abaixo do cargo (ex.: `UFAM`, `PROGESP`, `Escritório de ...`)
            if (
                (has_name or has_role)
                and len(nxt.text) <= 75
                and (_SEI_TIMBRE_CONTINUATION_RE.search(nxt.text) or _is_centered_line(nxt))
                and not re.search(r"[.?!]\s+[A-ZÁÉÍÓÚ]", nxt.text)
            ):
                collected_indices.append(j)
                prev_y1 = nxt.y1_norm
                continue
            break

        # Valida se há de fato um bloco de fechamento/assinatura
        centered_date_and_name = (
            is_local_date
            and has_name
            and _is_centered_line(ln)
            and any(_is_centered_line(lines[k]) for k in collected_indices[1:])
        )
        valid_sig_block = (has_name and has_role) or (
            has_name and (is_salutation or is_signed_tag or centered_date_and_name)
        )
        if valid_sig_block:
            top_y0 = min(lines[k].y0_norm for k in collected_indices)
            bot_y1 = max(lines[k].y1_norm for k in collected_indices)
            zones.append(
                SeiIgnoredZone(
                    kind="DESPACHO_SIGNATURE",
                    y0_norm=_clamp(top_y0 - 0.005),
                    y1_norm=_clamp(bot_y1 + 0.005),
                )
            )
            for cname in candidate_names:
                ignored_names.add(_normalize_ascii_upper(cname))
            has_signature = True

    # 3C. Bloco centralizado Signatário + Cargo/Portaria sem saudação explícita acima
    for k, ln in enumerate(lines):
        if ln.y0_norm < 0.28 or ln.y0_norm >= footer_cutoff:
            continue
        if k == 0:
            continue
        if not _is_centered_line(ln) and not _UNDERSCORE_LINE_RE.match(lines[k - 1].text):
            continue
        if len(ln.text) > 90 or _SIGNATURE_BLOCK_STOP_RE.search(ln.text):
            continue
        if not _SIGNATORY_ROLE_OR_ACT_RE.search(ln.text):
            continue

        # Verifica se a linha imediatamente acima é um nome de signatário curto e centralizado
        name_idx: int | None = None
        for back in (k - 1, k - 2):
            if back < 0:
                continue
            cand = lines[back]
            if (ln.y0_norm - cand.y1_norm) > 0.055:
                break
            if _UNDERSCORE_LINE_RE.match(cand.text) or _ASSINADO_TAG_RE.match(cand.text):
                continue
            if _looks_like_signatory_name_line(cand.text):
                name_idx = back
                break
            break

        if name_idx is None:
            continue

        name_ln = lines[name_idx]
        # Exige centralização de ambos (nome e cargo) ou linha de assinatura `______` logo acima
        has_underscore_above = (
            name_idx > 0
            and _UNDERSCORE_LINE_RE.match(lines[name_idx - 1].text) is not None
            and (name_ln.y0_norm - lines[name_idx - 1].y1_norm) <= 0.035
        )
        if not ((_is_centered_line(name_ln) and _is_centered_line(ln)) or has_underscore_above):
            continue

        block_indices = [name_idx, k]
        if has_underscore_above:
            block_indices.insert(0, name_idx - 1)
        prev_y1 = ln.y1_norm
        for fwd in range(k + 1, min(len(lines), k + 4)):
            nxt = lines[fwd]
            if nxt.y0_norm >= footer_cutoff - 0.004 or (nxt.y0_norm - prev_y1) > 0.038:
                break
            if _SIGNATURE_BLOCK_STOP_RE.search(nxt.text):
                break
            if (
                _SIGNATORY_ROLE_OR_ACT_RE.search(nxt.text)
                or _ASSINADO_TAG_RE.match(nxt.text)
                or (_is_centered_line(nxt) and _SEI_TIMBRE_CONTINUATION_RE.search(nxt.text))
            ):
                block_indices.append(fwd)
                prev_y1 = nxt.y1_norm
            else:
                break

        zones.append(
            SeiIgnoredZone(
                kind="DESPACHO_SIGNATURE",
                y0_norm=_clamp(min(lines[idx_b].y0_norm for idx_b in block_indices) - 0.005),
                y1_norm=_clamp(max(lines[idx_b].y1_norm for idx_b in block_indices) + 0.005),
            )
        )
        ignored_names.add(_normalize_ascii_upper(name_ln.text))
        has_signature = True

    return SeiPageBoilerplateAnalysis(
        zones=tuple(zones),
        ignored_signatory_names=frozenset(n for n in ignored_names if n),
        has_sei_header=has_header,
        has_sei_footer=has_footer,
        has_despacho_signature=has_signature,
    )


def _find_matching_sei_zone(
    x0_norm: float,
    y0_norm: float,
    x1_norm: float,
    y1_norm: float,
    analysis: SeiPageBoilerplateAnalysis,
) -> SeiIgnoredZone | None:
    """Retorna a primeira zona SEI que cobre o centro ou >= 50% da altura do retângulo."""
    if not analysis.zones:
        return None
    cy = (y0_norm + y1_norm) / 2.0
    cx = (x0_norm + x1_norm) / 2.0
    box_h = max(1e-6, y1_norm - y0_norm)

    for zone in analysis.zones:
        if not (zone.x0_norm <= cx <= zone.x1_norm):
            continue
        if zone.y0_norm <= cy <= zone.y1_norm:
            return zone
        inter_h = max(0.0, min(y1_norm, zone.y1_norm) - max(y0_norm, zone.y0_norm))
        if (inter_h / box_h) >= 0.50:
            return zone
    return None


def _is_institutional_email(matched_text: str) -> bool:
    low = _clean_line_text(matched_text).lower()
    return any(dom in low for dom in _INSTITUTIONAL_EMAIL_DOMAINS)


def should_ignore_entity_in_sei_zone(
    *,
    entity_type: str,
    category: DataCategory,
    matched_text: str,
    detector_source: str,
    x0_norm: float,
    y0_norm: float,
    x1_norm: float,
    y1_norm: float,
    analysis: SeiPageBoilerplateAnalysis,
) -> bool:
    """
    Avalia se uma entidade textual deve ser ignorada por pertencer a uma zona padrão do SEI
    (`SEI_HEADER`, `SEI_FOOTER` ou `DESPACHO_SIGNATURE`), preservando o Fail-Safe LGPD.
    """
    if not analysis.zones:
        return False

    # Regras personalizadas definidas explicitamente pelo usuário nunca são silenciadas
    if (detector_source or "").upper() == "CUSTOM_REGEX":
        return False

    etype = (entity_type or "").upper()

    # Fail-Safe LGPD: jamais suprime identificadores críticos ou dados sensíveis/financeiros/menores
    if etype in _NEVER_IGNORE_ENTITY_TYPES:
        return False
    if category in (
        DataCategory.PERSONAL_SENSITIVE,
        DataCategory.MINOR_OR_DEPENDENT,
        DataCategory.FINANCIAL_BANKING,
    ):
        return False

    zone = _find_matching_sei_zone(x0_norm, y0_norm, x1_norm, y1_norm, analysis)
    if zone is None:
        return False

    if etype == "EMAIL":
        return _is_institutional_email(matched_text)

    if zone.kind in ("SEI_HEADER", "SEI_FOOTER"):
        return etype in {
            "PERSON_NAME",
            "PROCESSO_SEI",
            "ADDRESS",
            "CEP",
            "PHONE",
            "SIAPE",
            "MATRICULA_UFAM",
            "RG",
            "BIRTH_DATE",
        }

    # zone.kind == "DESPACHO_SIGNATURE"
    if etype in {
        "PERSON_NAME",
        "PROCESSO_SEI",
        "BIRTH_DATE",
        "SIAPE",
        "MATRICULA_UFAM",
        "PHONE",
        "RG",
    }:
        return True

    if etype in ("ADDRESS", "CEP"):
        return bool(
            re.search(
                r"(?i)(?:rodrigo\s+oct[aá]vio|campus\s+universit[aá]rio|69\.?080-?900|69\.?077-?000|69\.?067-?005|ufam)",
                matched_text or "",
            )
        )

    return False


def is_visual_in_sei_zone(
    vis: DetectedVisualElement,
    analysis: SeiPageBoilerplateAnalysis,
) -> bool:
    """
    Retorna True quando um elemento visual (ex.: brasão da República no cabeçalho SEI ou
    QR Code / selo eletrônico no rodapé/assinatura SEI) está dentro de uma zona SEI ignorada.
    """
    if not analysis.zones:
        return False
    return (
        _find_matching_sei_zone(
            vis.x0_norm,
            vis.y0_norm,
            vis.x1_norm,
            vis.y1_norm,
            analysis,
        )
        is not None
    )
