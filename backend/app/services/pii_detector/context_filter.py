"""
Filtro Contextual Anti-Falso Positivo (`context_filter.py`).

Implementa a Seção 2 ("Supressão de Falsos Positivos") da `SPEC.md` e endurecimento
para documentos judiciais, cédulas de identidade (RG/CPF), faturas bancárias e contas:
1. Descarta CNPJ institucional da UFAM (`04.378.626/0001-97` etc.) e outros CNPJs corporativos;
2. Descarta datas de publicação de documentos/portarias/editais/DOU (sem jamais suprimir datas de nascimento);
3. Descarta números de portarias, ofícios, editais, resoluções ou memorandos isolados;
4. Regras estritas para processos SEI (`23105.XXXXXX/XXXX-XX`);
5. Suprime trechos abstratos de citação da Lei nº 13.709/2018 e avisos institucionais de SAC bancário
   (ex.: `"SAC - Deficiência Auditiva ou de Fala: 0800..."`);
6. Suprime falsos positivos de `REMUNERATION` em valores de ações cíveis/faturas não salariais e
   endereços/CEPs corporativos ou de tribunais/fóruns;
7. Blocklist de termos estruturais, judiciais (PROJUDI/TJAM), faturas bancárias e validação rígida
   de nomes próprios contra frases minúsculas/coladas de OCR.
"""

from __future__ import annotations

import re
import unicodedata

from app.services.pdf_extractor.extractor import ExtractedTableCell
from app.services.pii_detector.validators import is_ufam_institutional_cnpj, strip_non_digits

SEI_CONTEXT_WINDOW_CHARS: int = 60
FOOTER_Y0_NORM_THRESHOLD: float = 0.93

_SEI_PUBLIC_EXCLUSION_RE = re.compile(
    r"(?i)\b(?:geral|organizacional|normativ[ao]|refer[eê]ncia|or[cç]amento)\b"
)
_SEI_PERSONAL_FORCE_RE = re.compile(
    r"(?i)\b(?:individual|servidor(?:\(a\)|a)?|nominal|interessad[oa](?:\(a\))?|caso|reservad[oa]|sindic[aâ]ncia|disciplinar)\b"
)

_PERSON_LINK_CONTEXT_RE = re.compile(
    r"(?i)(?:\b(?:servidor\(a\)|interessad[oa]\(a\)|requerente|paciente|discente|docente|alun[oa]\(a\)|"
    r"titular|portador\(a\)|cpf|siape|matr[ií]cula|nome|favorecid[oa]|benefici[aá]ri[oa]|individual|nominal|caso|reservad[oa]|sindic[aâ]ncia|disciplinar)\b|"
    r"\b\d{3}\.\d{3}\.\d{3}-[\s\n]*\d{2}\b|"
    r"(?:[:=-]\s*[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]{2,}\s+(?:da\s+|de\s+|do\s+|dos\s+|das\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]{2,}))"
)

_FINANCIAL_REMUNERATION_CONTEXT_RE = re.compile(
    r"(?i)\b(?:remunera[cç][aã]o|vencimento(?:s)?|proventos|valor[\s\n]+(?:bruto|l[ií]quido|da[\s\n]+bolsa|mensal)|"
    r"rendimento[\s\n]+(?:bruto|l[ií]quido)|sal[aá]rio|rubrica|folha[\s\n]+de[\s\n]+pagamento|contracheque|"
    r"ag[eê]ncia|conta[\s\n]+(?:corrente|poupan[cç]a|sal[aá]rio)|pix|pens[aã]o|aux[ií]lio|"
    r"bolsa|bolsista|gratifica[cç][aã]o|adicional|siape)\b"
)
_CIVIL_LITIGATION_AMOUNT_EXCLUSION_RE = re.compile(
    r"(?i)\b(?:valor\s+da\s+causa|danos\s+morais|danos\s+materiais|restitui[cç][aã]o|"
    r"indeniza[cç][aã]o|pr[oó]tese|condena[cç][aã]o|custas|honor[aá]rios|valor\s+pago|"
    r"parcelad[oa]|fatura|limite\s+de\s+cr[eé]dito|pagamento\s+m[ií]nimo|sal[aá]rios\s+m[ií]nimos)\b"
)
_STRONG_PAYROLL_OVERRIDE_RE = re.compile(
    r"(?i)\b(?:remunera[cç][aã]o|vencimento\s+b[aá]sico|proventos|folha\s+de\s+pagamento|"
    r"valor\s+l[ií]quido|valor\s+bruto|bolsa\s*:|bolsista|contracheque|rubrica)\b"
)

_BANK_SAC_ACCESSIBILITY_CONTEXT_RE = re.compile(
    r"(?i)(?:\bsac\b|ouvidoria|0800[\s.-]*\d{3}|fone\s*f[aá]cil|central\s+de\s+atendimento|"
    r"auditiva\s+ou\s+de\s+fala|defici[êe]ncia\s+auditiva\s+ou\s+de\s+fala|"
    r"bradesco|ita[uú]|santander|caixa\s+econ[oô]mica|banco\s+do\s+brasil|nubank)"
)

_RG_CARD_CONTEXT_RE = re.compile(
    r"(?i)(?:registro[\s\n]+geral|carteira[\s\n]+de[\s\n]*identidade|[oó]rg[aã]o[\s\n]+expedidor|"
    r"ssp[/\s-]*(?:am|sp|rj|mg|df|ba|pe|pr|rs|sc|ce|go|pa|ma|pb|rn|al|se|pi|mt|ms|ro|ac|ap|rr|to|es)|"
    r"v[aá]lida[\s\n]*em[\s\n]*todo)"
)
_RG_BIRTH_CONTEXT_RE = re.compile(
    r"(?i)(?:naturalidade|filia[cçg][aã]o|carteira[\s\n]+de[\s\n]*identidade|registro[\s\n]+geral)"
)
_RG_NON_BIRTH_DATE_NEAR_RE = re.compile(
    r"(?i)(?:expedi[cç][aã]o|emiss[aã]o|juntada|distribui[cç][aã]o|vencimento|validade|projudi|assinado)"
)

_INSTITUTIONAL_FALSE_PERSON_RE = re.compile(
    r"(?i)\b(?:universidade\s+federal|estado\s+do\s+amazonas|governo\s+do\s+amazonas|pr[oó]-reitoria|"
    r"minist[eé]rio\s+da\s+educa[cç][aã]o|minist[eé]rio\s+da\s+fazenda|receita\s+federal|"
    r"conselho\s+universit[aá]rio|consuni|consepe|progesp|proplan|"
    r"proeg|proext|protec|proadm|di[aá]rio\s+oficial|boletim\s+de\s+servi[cç]o|portaria\s+n[º°]?|"
    r"edital\s+n[º°]?|of[ií]cio\s+n[º°]?|resolu[cç][aã]o\s+n[º°]?|memorando\s+n[º°]?|"
    r"instituto\s+de\s+computa[cç][aã]o|faculdade\s+de|departamento\s+de|coordena[cç][aã]o\s+de|"
    r"divis[aã]o\s+de|se[cç][aã]o\s+de|diretoria\s+de|cadastro\s+e\s+pagamento|gest[aã]o\s+de\s+pessoas|"
    r"poder\s+executivo|poder\s+judici[aá]rio|tribunal\s+de\s+justi[cç]a|juizado\s+especial|"
    r"comarca\s+de\s+manaus|rep[uú]blica\s+federativa|assunto\s*:|objeto\s*:|ementa\s*:|"
    r"valor\s+l[ií]quido|conta\s+corrente|dados\s+banc[aá]rios|folha\s+de\s+pagamento|"
    r"banco\s+do\s+brasil|caixa\s+econ[oô]mica|itau|bradesco|santander|"
    r"documento\s+f[ií]sico|f[ií]sico\s+digitalizado|"
    r"nome\s+completo\s+do\s+menor|condi[cç][aã]o\s+m[eé]dica|menores\s+de\s+idade|dependentes\s+menores|"
    r"v[aá]lido\s+sint[eé]tico|pens[aã]o\s+aliment[ií]cia|ci[eê]ncia\s+do\s+interessado|assinatura\s+do|"
    r"registro\s+facial\s+id|analista\s+de\s+tecnologia|tecnologia\s+da\s+informa[cç][aã]o|"
    r"t[eé]cnico\s+administrativo|assistente\s+em\s+administra[cç][aã]o|natural\s+de\s+manaus|"
    r"vossa\s+excel[eê]ncia|polegar\s+direito|polegar\s+esquerdo|pessoa\s+jur[ií]dica|"
    r"juntada\s+de\s*peti[cç][aã]o|n[uú]cleo\s+cidade\s+de\s+deus|visa\s+platinum|"
    r"north\s+bio|ind[uú]stria\s+gr[aá]fica|manaus\s+ambiental|[aá]guas\s+de\s+manaus|"
    r"vitoria\s+supermerc|vit[oó]ria\s+supermerc|drogaria|supermercado|farm[aá]cia)\b"
)

_EXACT_NON_PERSONAL_BLOCKLIST_RE = re.compile(
    r"(?i)^\s*(?:filho|filha|s\s+e\s+menores\s+de\s+idade|dependentes\s+menores|"
    r"nome\s+completo\s+do\s+menor|condi[cç][aã]o\s+m[eé]dica|v[aá]lido\s+sint[eé]tico|"
    r"pens[aã]o\s+aliment[ií]cia|ci[eê]ncia\s+do\s+interessado|assinatura\s+do(?:\s+\w+)*|"
    r"registro\s+facial\s+id:?|sindicato\s+ou\s+a|vossa\s+excel[eê]ncia|polegar\s+direito|"
    r"polegar\s+esquerdo|naturalidade|filia[cç][aã]o|nome|cert|niss|visa|visa\s+platinum)\s*$"
)

_CITY_UF_PATTERN_RE = re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s]{2,24}-[A-Z]{2}$")
_PERSON_FORBIDDEN_CHARS_RE = re.compile(r"[\d/:%@=+×$€£\\\[\]{}|]")
_CAP_ALPHA_WORD_RE = re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç'-]{1,}$")
_PERSON_PARTICLES_LOWER: frozenset[str] = frozenset({"da", "de", "do", "dos", "das", "e"})

_STRUCTURAL_BLOCKLIST_TOKENS: frozenset[str] = frozenset(
    {
        "CENARIO",
        "TESTE",
        "TABELAS",
        "TABELA",
        "DENSAS",
        "DENSA",
        "MULTIPLAS",
        "MULTIPLA",
        "PAGINAS",
        "PAGINA",
        "REPETICOES",
        "REPETICAO",
        "DADOS",
        "ACADEMICOS",
        "ACADEMICO",
        "RELATORIO",
        "QUADRO",
        "FOLHA",
        "PARECER",
        "MINISTERIO",
        "UNIVERSIDADE",
        "PRO-REITORIA",
        "PROREITORIA",
        "PROEG",
        "PROGESP",
        "PROPLAN",
        "PROEXT",
        "PROADM",
        "PROTEC",
        "CONSUNI",
        "CONSEPE",
        "BOLSAS",
        "SAUDE",
        "CONTATO",
        "DOCUMENTOS",
        "BANCARIOS",
        "ATENDIMENTO",
        "PRIORITARIO",
        "INSCRICAO",
        "FISICO",
        "DIGITALIZADO",
        "DIGITALIZADA",
        "DPI",
        "ARQUIVO",
        "PROTOCOLO",
        "FICHA",
        "ASSENTAMENTO",
        "LOTE",
        "SCAN",
        "FOTO",
        "ESCANEADA",
        "ESCANEADO",
        "SECAO",
        "CONDICAO",
        "MEDICA",
        "MENORES",
        "DEPENDENTES",
        "SINTETICO",
        "VALIDO",
        "PENSAO",
        "ALIMENTICIA",
        "CIENCIA",
        "ASSINATURA",
        "ANALISTA",
        "TECNOLOGIA",
        "INFORMACAO",
        "NASC",
        # Termos judiciais / PROJUDI / TJAM
        "JUNTADA",
        "PETICAO",
        "DEPETICAO",
        "DEINICIAL",
        "INICIAL",
        "DISTRIBUICAO",
        "PROJUDI",
        "TJAM",
        "COMARCA",
        "JUIZADO",
        "ESPECIAL",
        "CIVEL",
        "VOSSA",
        "EXCELENCIA",
        "REQUERENTE",
        "REQUERIDO",
        "REQUERIDA",
        "CITACAO",
        "INTIMACAO",
        "AUDIENCIA",
        "CONCILIACAO",
        "MANDADO",
        "CERTIDAO",
        "SENTENCA",
        "DECISAO",
        "EXPEDICAO",
        "APRESENTACAO",
        "PRETENSAO",
        "JUDICIARIO",
        "TRIBUNAL",
        "JUSTICA",
        "ARQ",
        # Termos de RG / Cédula de Identidade
        "POLEGAR",
        "NATURALIDADE",
        "FILIACAO",
        "CERT",
        "NISS",
        "DEANOS",
        "TERRITORIO",
        "NACIONAL",
        "IDENTIDADE",
        "CARTEIRA",
        "GRAFICA",
        "INDUSTRIA",
        # Termos de fatura bancária / cartão / comércio / utilidades
        "VISA",
        "MASTERCARD",
        "PLATINUM",
        "FATURA",
        "FATURAS",
        "ASFATURAS",
        "PARCELAMENTO",
        "PARCOLAMENTO",
        "PARCELADAS",
        "TAXAS",
        "MENSAIS",
        "MENSALS",
        "PERIODO",
        "PARIODO",
        "PAGAMENTO",
        "PAGAMENLO",
        "PAGOMONTO",
        "CONTAS",
        "CANTAS",
        "COBRANCA",
        "COBRANCADEIOF",
        "IOF",
        "CET",
        "ANUAL",
        "DEMAIS",
        "REGIOES",
        "SUPERMERC",
        "SUPERMERCADO",
        "DROGARIA",
        "FARMACIA",
        "NUCLEO",
        "CONCORRA",
        "CONCORRAAVIAGENS",
        "VIAGENS",
        "NAVEGACAO",
        "NAVEGACAOPP",
        "MLT",
        "INPGNT",
        "JRS",
        "IMPONI",
        "VENCIMENTO",
        "ENCARGOS",
        "OUVIDORIA",
        "LTDA",
        "AMBIENTAL",
        "SANEAMENTO",
        "RELACAO",
        "DIRETOR",
        "DIRETORA",
        "DIRETORADE",
        "SECRETARIA",
        "POLO",
        "PASSIVO",
        "ATIVO",
        "POLOPASSIVO",
        "POLOATIVO",
        "AVENIDA",
        "RUA",
        "TRAVESSA",
        "ALAMEDA",
        "ESTRADA",
    }
)

_UFAM_INSTITUTIONAL_ADDRESS_RE = re.compile(
    r"(?i)(?:\b(?:av(?:enida)?\.?\s+(?:general\s+)?rodrigo\s+oct[aá]vio|"
    r"campus\s+universit[aá]rio\s+senador\s+arthur\s+virg[ií]lio|"
    r"n[uú]cleo\s+cidade\s+de\s+deus|"
    r"rua\s+alexandre\s+amorim)\b)"
)
_PUBLIC_OR_CORP_CEP_SET: frozenset[str] = frozenset({"06029900", "69067005", "69010300"})
_CORP_OR_COURT_ADDRESS_BEFORE_RE = re.compile(
    r"(?i)(?:(?<!cpf/)cnpj\s*[:=-]?|com\s+sede\s+na|poder\s*judici[aá]rio|tribunal\s+de\s+justi[cç]a|"
    r"juizado\s+especial|f[oó]rum\s+|projudi\b|rua\s+alexandre\s+amorim|banco\s+bradesco|n[uú]cleo\s+cidade\s+de\s+deus|"
    r"manaus\s+ambiental|[aá]guas\s+de\s*manaus|\bs/a\b|\bltda\b)"
)
_PERSONAL_ADDRESS_BEFORE_RE = re.compile(
    r"(?i)(?:residente\s+e\s+domiciliad|endere[cç]o\s+residencial|requerente\s*:|titular\s*:|destinat[aá]rio|"
    r"polo\s+ativo|cpf/cnpj\s*[:=-]?\s*\d{3}\.\d{3}\.\d{3}-\d{2}|cpf/cnpj\s*[:=-]?\s*\d{2,3}\*)"
)

_STANDALONE_TOPONYM_OR_ORG_SET: frozenset[str] = frozenset(
    {
        "MANAUS",
        "MAUES",
        "AMAZONAS",
        "BRASIL",
        "UFAM",
        "TJAM",
        "PROJUDI",
        "PROGESP",
        "PROEG",
        "PROPLAN",
        "PROEXT",
        "PROADM",
        "PROTEC",
        "CONSUNI",
        "CONSEPE",
        "VISA",
    }
)

_PUBLICATION_DATE_CONTEXT_RE = re.compile(
    r"(?i)\b(?:publicad[oa]\s+em|portaria\s+de|edital\s+de|manaus\s*,\s*|"
    r"data\s+da\s+publica[cç][aã]o|dou\s+de|vig[eê]ncia|assinado\s+eletronicamente\s+em)\b"
)
_BIRTH_ANCHOR_CONTEXT_RE = re.compile(r"(?i)\b(?:nasc|nascimento|nascid[oa]|nasceu|dt\.?\s*nasc)\b")

_NOMINAL_TABLE_HEADER_RE = re.compile(
    r"(?i)\b(?:servidor|interessado|requerente|nome|cpf|siape|matr[ií]cula|discente|bolsista|alun[oa]|benefici[aá]rio|dependente|titular|favorecid[oa])\b"
)

_LEGAL_ABSTRACT_CITATION_RE = re.compile(
    r"(?i)(?:lei\s+n[º°]?\s*13\.709/2018|considera-se\s+dado\s+pessoal\s+sens[íi]vel|cita[cç][aã]o\s+legal\s+abstrata)"
)
_SPECIFIC_SENSITIVE_SIGNAL_RE = re.compile(
    r"(?i)\b(?:adua|sintest|andes|fasubra|sinasefe|ficha\s+sindical|hla-|cid-?1[01]|bio-|#img-|"
    r"adventista|judaica|quilombola|tukano|parda|preto|ind[ií]gena|burnout|radiculopatia|"
    r"ansiedade|depressiv|lombociatalgia|retinopatia|surdez|tea\b|pcd\b|hipertens[aã]o)\b"
)


def _ascii_upper(val: str) -> str:
    nfkd = unicodedata.normalize("NFKD", val or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).upper()


def is_footer_region(y0_norm: float) -> bool:
    """Retorna True se o elemento estiver nos últimos ~6-7% inferiores da página (rodapé/paginação)."""
    return float(y0_norm) > FOOTER_Y0_NORM_THRESHOLD


def has_rg_card_context(full_text: str, start_char: int, end_char: int) -> bool:
    """Verifica se um número no formato `0109678-8` está em contexto de cédula de RG."""
    if not full_text or start_char < 0:
        return False
    win = full_text[max(0, start_char - 280) : min(len(full_text), end_char + 280)]
    if not _RG_CARD_CONTEXT_RE.search(win):
        return False
    # Evita capturar matrículas (`Matr. 154.714-3-B`) ou números bancários
    left_near = full_text[max(0, start_char - 20) : start_char]
    if re.search(r"(?i)(?:matr[íi]cula|matr\.?|inscri[çc][ãa]o|siape|conta|c/c|ag[eê]ncia|ag\.?|processo|cert\.?)\s*[:=-]?\s*$", left_near):
        return False
    return True


def has_rg_birth_date_context(full_text: str, start_char: int, end_char: int) -> bool:
    """Verifica se uma data isolada em verso de RG corresponde à data de nascimento (excluindo expedição/juntada)."""
    if not full_text or start_char < 0:
        return False
    win = full_text[max(0, start_char - 220) : min(len(full_text), end_char + 220)]
    if not _RG_BIRTH_CONTEXT_RE.search(win):
        return False
    near_left = full_text[max(0, start_char - 55) : start_char]
    near_right = full_text[end_char : min(len(full_text), end_char + 65)]
    if _RG_NON_BIRTH_DATE_NEAR_RE.search(near_left) or _RG_NON_BIRTH_DATE_NEAR_RE.search(near_right):
        return False
    # Em verso de RG, a data de nascimento aparece logo após filiação ou logo antes de naturalidade/cidade-UF
    if re.search(r"(?i)(?:filia[cçg][aã]o|naturalidade|[A-Z]{3,}-[A-Z]{2}|maior\s+de)", near_left + "\n" + near_right):
        return True
    return False


def is_false_positive_institutional_text(candidate_text: str) -> bool:
    """Retorna True se o texto candidato for um termo organizacional/estrutural, rodapé CAIXA ALTA ou CNPJ da UFAM."""
    cleaned = re.sub(r"\s+", " ", (candidate_text or "")).strip()
    if not cleaned:
        return True

    if _EXACT_NON_PERSONAL_BLOCKLIST_RE.match(cleaned):
        return True

    digits = strip_non_digits(cleaned)
    if len(digits) == 14 and is_ufam_institutional_cnpj(digits):
        return True

    upper_ascii = _ascii_upper(cleaned)
    if upper_ascii in _STANDALONE_TOPONYM_OR_ORG_SET:
        return True

    if _CITY_UF_PATTERN_RE.match(upper_ascii):
        return True

    if _INSTITUTIONAL_FALSE_PERSON_RE.search(cleaned):
        return True

    if "PRO-REITORIA" in upper_ascii or "PRO REITORIA" in upper_ascii:
        return True

    words = re.findall(r"[A-Z0-9\-]+", upper_ascii)
    if any(w in _STRUCTURAL_BLOCKLIST_TOKENS for w in words):
        return True

    return False


def is_sei_process_linked_to_natural_person(
    full_text: str,
    start_char: int,
    end_char: int,
    *,
    table_cells: list[ExtractedTableCell] | None = None,
    window_chars: int = SEI_CONTEXT_WINDOW_CHARS,
) -> bool:
    """
    Avalia se um número de processo SEI (`23105.XXXXXX/XXXX-XX`) está vinculado a uma pessoa
    natural identificável.
    """
    if start_char >= 0 and end_char > start_char:
        ctx_before_raw = full_text[max(0, start_char - window_chars) : start_char]
        ctx_before_norm = re.sub(r"\s+", " ", ctx_before_raw)

        pub_matches = list(_SEI_PUBLIC_EXCLUSION_RE.finditer(ctx_before_norm))
        pers_matches = list(_SEI_PERSONAL_FORCE_RE.finditer(ctx_before_norm))
        if pub_matches and pers_matches:
            return pers_matches[-1].end() > pub_matches[-1].end()
        if pub_matches:
            return False
        if pers_matches:
            return True

        wider_before = re.sub(r"\s+", " ", full_text[max(0, start_char - (window_chars * 2)) : start_char])
        w_pub = list(_SEI_PUBLIC_EXCLUSION_RE.finditer(wider_before))
        w_pers = list(_SEI_PERSONAL_FORCE_RE.finditer(wider_before))
        if w_pers and (not w_pub or w_pers[-1].end() > w_pub[-1].end()):
            return True
        if w_pub:
            return False

        line_start = full_text.rfind("\n", 0, start_char)
        line_start = 0 if line_start == -1 else line_start + 1

        line_end = full_text.find("\n", end_char)
        line_end = len(full_text) if line_end == -1 else line_end

        win_start = max(line_start, start_char - window_chars)
        win_end = min(line_end, end_char + window_chars)
        context_slice = re.sub(r"\s+", " ", full_text[win_start:win_end])

        if _SEI_PUBLIC_EXCLUSION_RE.search(context_slice):
            return False

        if _PERSON_LINK_CONTEXT_RE.search(context_slice):
            return True

        matched_process = full_text[start_char:end_char].strip()
    else:
        matched_process = ""

    if table_cells and matched_process:
        target_rows: set[tuple[int, int]] = set()
        for cell in table_cells:
            if matched_process in cell.cell_text:
                pub_m = list(_SEI_PUBLIC_EXCLUSION_RE.finditer(cell.cell_text))
                pers_m = list(_SEI_PERSONAL_FORCE_RE.finditer(cell.cell_text))
                if pub_m and pers_m:
                    return pers_m[-1].end() > pub_m[-1].end()
                if pub_m:
                    return False
                if pers_m:
                    return True
                target_rows.add((cell.table_index, cell.row_index))

        if target_rows:
            for cell in table_cells:
                if (cell.table_index, cell.row_index) in target_rows:
                    if _NOMINAL_TABLE_HEADER_RE.search(cell.header_label) or _PERSON_LINK_CONTEXT_RE.search(
                        cell.cell_text
                    ):
                        return True

    return False


def _is_corporate_or_court_location_context(full_text: str, start_char: int) -> bool:
    """Retorna True se o endereço/CEP estiver imediatamente sob cabeçalho de tribunal, banco ou empresa."""
    if not full_text or start_char <= 0:
        return False
    ctx_before = full_text[max(0, start_char - 125) : start_char]
    corp_matches = list(_CORP_OR_COURT_ADDRESS_BEFORE_RE.finditer(ctx_before))
    if not corp_matches:
        return False
    pers_matches = list(_PERSONAL_ADDRESS_BEFORE_RE.finditer(ctx_before))
    if pers_matches and pers_matches[-1].end() > corp_matches[-1].end():
        return False
    return True


def should_suppress_detection(
    entity_type: str,
    matched_value: str,
    full_text: str,
    start_char: int,
    end_char: int,
    *,
    table_cells: list[ExtractedTableCell] | None = None,
) -> bool:
    """
    Aplica todas as regras de supressão de falsos positivos (SPEC.md Seção 2):
    Retorna `True` quando a detecção deve ser DESCARTADA.
    """
    if not matched_value or not matched_value.strip():
        return True

    cleaned_norm = re.sub(r"\s+", " ", matched_value).strip()
    if _EXACT_NON_PERSONAL_BLOCKLIST_RE.match(cleaned_norm):
        return True

    if entity_type == "CNPJ" or is_ufam_institutional_cnpj(matched_value):
        return True

    if entity_type == "PROCESSO_SEI":
        return not is_sei_process_linked_to_natural_person(
            full_text=full_text,
            start_char=start_char,
            end_char=end_char,
            table_cells=table_cells,
            window_chars=SEI_CONTEXT_WINDOW_CHARS,
        )

    if entity_type in ("PERSON_NAME", "PER", "DEPENDENT_DATA", "FILIACAO", "NATURALIDADE"):
        if is_false_positive_institutional_text(matched_value):
            return True
        if _PERSON_FORBIDDEN_CHARS_RE.search(cleaned_norm):
            return True
        if ".arq" in cleaned_norm.lower():
            return True

        raw_tokens = [tok.strip(".,;:()") for tok in cleaned_norm.split() if tok.strip(".,;:()")]
        # Rejeita colagens anômalas de OCR não-hifenizadas (ex.: `dificuldadestantoem`, `opagamentotenha`)
        if any(("-" not in tok and len(tok) >= 19) for tok in raw_tokens):
            return True

        alpha_tokens = [tok for tok in raw_tokens if len(tok) >= 2]
        if len(alpha_tokens) < 2:
            return True

        # Exige pelo menos 2 palavras iniciadas em maiúscula (descarta frases comuns minúsculas do spaCy)
        cap_non_particles = [
            tok
            for tok in raw_tokens
            if tok.lower() not in _PERSON_PARTICLES_LOWER and _CAP_ALPHA_WORD_RE.match(tok)
        ]
        if len(cap_non_particles) < 2:
            return True

    if entity_type in (
        "UNION_AFFILIATION",
        "ETHNIC_RACIAL_DECLARATION",
        "RELIGIOUS_BELIEF",
        "GENETIC_DATA",
        "HEALTH_DATA",
    ):
        if start_char >= 0 and full_text:
            para_slice = full_text[max(0, start_char - 240) : min(len(full_text), max(end_char, start_char) + 240)]
            if _LEGAL_ABSTRACT_CITATION_RE.search(para_slice) and not _SPECIFIC_SENSITIVE_SIGNAL_RE.search(cleaned_norm):
                return True
            if entity_type == "HEALTH_DATA" and re.search(r"(?i)defici[êe]ncia\s+(?:auditiva|de\s+fala)", cleaned_norm):
                sac_slice = full_text[max(0, start_char - 150) : min(len(full_text), max(end_char, start_char) + 150)]
                if _BANK_SAC_ACCESSIBILITY_CONTEXT_RE.search(sac_slice):
                    return True

    if entity_type == "ADDRESS":
        if _UFAM_INSTITUTIONAL_ADDRESS_RE.search(cleaned_norm):
            return True
        if _is_corporate_or_court_location_context(full_text, start_char):
            return True

    if entity_type == "CEP":
        cep_digits = strip_non_digits(cleaned_norm)
        if cep_digits in _PUBLIC_OR_CORP_CEP_SET:
            return True
        if _is_corporate_or_court_location_context(full_text, start_char):
            return True

    if entity_type == "PHONE":
        if start_char > 0 and full_text:
            phone_before = full_text[max(0, start_char - 125) : start_char]
            if re.search(
                r"(?i)(?:rua\s+alexandre\s+amorim|69\.?010-300|juizado\s+especial|poder\s*judici[aá]rio|ouvidoria|central\s+de\s+atendimento)",
                phone_before,
            ) and not _PERSONAL_ADDRESS_BEFORE_RE.search(phone_before):
                return True

    if entity_type == "REMUNERATION":
        if start_char >= 0 and end_char > start_char:
            win_slice = full_text[max(0, start_char - 160) : min(len(full_text), end_char + 160)]
            if not _FINANCIAL_REMUNERATION_CONTEXT_RE.search(win_slice):
                return True
            if _CIVIL_LITIGATION_AMOUNT_EXCLUSION_RE.search(win_slice) and not _STRONG_PAYROLL_OVERRIDE_RE.search(
                full_text[max(0, start_char - 80) : start_char]
            ):
                return True

    if entity_type == "BIRTH_DATE":
        if start_char >= 0:
            ctx_left = full_text[max(0, start_char - 45) : start_char]
            if not _BIRTH_ANCHOR_CONTEXT_RE.search(ctx_left) and _PUBLICATION_DATE_CONTEXT_RE.search(ctx_left):
                return True

    return False
