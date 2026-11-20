"""
Expressões regulares pré-compiladas no carregamento do módulo (`regex_patterns.py`).

Evita qualquer recompilação de regex por página ou por documento, garantindo latência
sub-milissegundo na varredura léxica de textos administrativos da UFAM e capturando
entidades com quebras de linha (`\\n`), artefatos de OCR (campos colados sem espaço)
e nomes em CAIXA ALTA ou abreviados.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from app.models.enums import DataCategory, RedactionActionMode
from app.services.pii_detector.validators import (
    strip_non_digits,
    validate_cnh,
    validate_cnpj,
    validate_cpf,
    validate_pis_pasep_nit,
    validate_titulo_eleitor,
)

MAX_CUSTOM_REGEX_RULES: int = 20
MAX_CUSTOM_REGEX_LENGTH: int = 300
_REDOS_NESTED_QUANTIFIER_RE = re.compile(r"\((?:[^()\\]|\\.)*[*+?{][^()]*\)\s*(?:[*+?]|{\d)")


@dataclass(frozen=True, slots=True)
class RegexPatternSpec:
    """Especificação de um padrão léxico compilado com metadados de classificação LGPD."""

    entity_type: str
    category: DataCategory
    pattern: re.Pattern[str]
    value_group: int = 0
    base_confidence: float = 0.92
    validator: Callable[[str], bool] | None = None
    requires_context: bool = False
    preferred_action_mode: RedactionActionMode | None = None
    is_custom: bool = False


# ==============================================================================
# Expressões Regulares Pré-Compiladas (Compiladas uma única vez em import time)
# ==============================================================================

# 1. CPF (Formatado 000.000.000-00 ou quebrado após hífen/pontos em células de tabela, ou rotulado)
RE_CPF_FORMATTED = re.compile(r"\b(\d{3}\.?\d{3}\.?\d{3}-[\s\n]*\d{2})\b")
RE_CPF_LABELED = re.compile(
    r"(?i)\bcpf[\s\n]*(?:n[º°o0.]?[\s\n]*)?[:=-]?[\s\n]*(\d{3}\.?\d{3}\.?\d{3}-?[\s\n]*\d{2})\b"
)

# 2. CNPJ (Formatado 00.000.000/0000-00)
RE_CNPJ_FORMATTED = re.compile(r"\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b")

# 3. CNH rotulada (11 dígitos, suportando quebra de linha e ruído de OCR em nº)
RE_CNH_LABELED = re.compile(
    r"(?i)\bcnh[\s\n]*(?:n[º°o0.]?[\s\n]*)?[:=;\-]?[\s\n]*(\d{11}|\d{9}-[\s\n]*\d{2})(?![\d-])"
)

# 4. PIS / PASEP / NIT / NIS formatado ou rotulado (`pis/pasep` antes de `pis` para evitar match parcial)
RE_PIS_PASEP_FORMATTED = re.compile(r"\b(\d{3}\.\d{5}\.\d{2}-[\s\n]*\d)(?!\d)")
RE_PIS_PASEP_LABELED = re.compile(
    r"(?i)\b(?:pis[\s\n]*/[\s\n]*pasep|pis|pasep|nit|nis)[\s\n]*(?:n[º°o0.]?[\s\n]*)?[:=;\-]?[\s\n]*(\d{3}\.?\d{5}\.?\d{2}-?[\s\n]*\d|\d{11})(?!\d)"
)

# 5. RG (Padrões estaduais brasileiros + SSP/AM, SESDEC/AM, UFAM; tolera órgão emissor colado `RG:1096788SSP/AM`)
RE_RG_LABELED = re.compile(
    r"(?i)\b(?:rg|registro[\s\n]+geral|carteira[\s\n]+de[\s\n]*identidade|identidade)[\s\n]*(?:n[º°o0.]?[\s\n]*)?[:=-]?[\s\n]*"
    r"([0-9]{1,2}\.?[0-9]{3}\.?[0-9]{3}(?:-[\s\n]*[0-9Xx])?|[0-9]{6,10}(?:-[\s\n]*[0-9Xx])?)(?![0-9-])"
)
# Número de RG isolado em cédula de identidade (ex.: `0109678-8` acima de `REGISTRO GERAL`); exige contexto via context_filter
RE_RG_CARD_CONTEXTUAL = re.compile(
    r"\b(\d{1,2}\.?\d{3}\.?\d{3}-[0-9Xx]|\d{6,8}-[0-9Xx])\b"
)

# 6. Passaporte brasileiro/internacional
RE_PASSPORT_LABELED = re.compile(
    r"(?i)\bpassaporte[\s\n]*(?:n[º°o0.]?[\s\n]*)?[:=-]?[\s\n]*([A-Z]{2}[ \t]?\d{6,7})\b"
)

# 7. Título de Eleitor (12 dígitos: XXXX XXXX XXXX, XXXX.XXXX.XXXX, XXXXXXXXXX-XX ou contínuo)
RE_TITULO_ELEITOR_LABELED = re.compile(
    r"(?i)\b(?:t[íi]tulo(?:[\s\n]+de)?[\s\n]+eleitor|t[íi]t\.?[\s\n]*eleitoral?|inscri[çc][ãa]o[\s\n]+eleitoral)"
    r"(?:[\s\n]*n[º°o0.]?)?[:=\s\n]*(\d{4}[\s\.\n]*\d{4}[\s\.\n]*\d{4}|\d{10}-[\s\n]*\d{2}|\d{12})\b"
)
RE_TITULO_ELEITOR_STANDALONE = re.compile(
    r"\b(\d{4}[\s\n]+\d{4}[\s\n]+\d{2}[0-2]\d|\d{4}\.\d{4}\.\d{4}|\d{10}-[\s\n]*\d{2})\b"
)

# 8. Endereços Residenciais Completos (executados ANTES de `RE_CEP` para impedir fragmentação pelo CEP final)
RE_ADDRESS_LABELED = re.compile(
    r"(?i)\b(?:endere[cçg]o(?:[ \t]+residencial)?)[ \t]*[:=-][\s\n]*([^\n|]+?(?:CEP[ \t]*[:=-]?[ \t]*\d{2}\.?\d{3}-?\d{3}|\d{5}-\d{3}))"
)
# Impede invasão de colunas vizinhas financeiras/faturas após quebra de linha (ex.: `786,52 +12×167`)
_ADDR_LINE_CHAR = (
    r"(?:[^\n,;|.×+%$]|"
    r"\n(?=\s*(?:n[º°o.]?\s*\d|bairro\b|conjunto\b|conj\.?|nova\s+rep[uú]blica|quadra\b|qd\.?\b|"
    r"bloco\b|bl\.?\b|apto\.?\b|apartamento\b|casa\b|sala\b|lote\b|distrito\b|manaus\b|"
    r"cep\b|japiim\b|aleixo\b|centro\b|cidade\b|parque\b|alvorada\b|adrian[oó]polis\b|"
    r"flores\b|compensa\b|coroado\b|s[aã]o\s+|santa\s+|vila\b|residencial\b)))"
)
RE_RESIDENTIAL_ADDRESS = re.compile(
    rf"(?i)\b((?:Avenida|Av\.?|Rua|Travessa|Tv\.?|Alameda|Estrada)[\s\n]+{_ADDR_LINE_CHAR}{{3,85}}"
    rf"(?:,[\s\n]*(?:n[º°o.]?[\s\n]*)?\d+[A-Za-z0-9/-]*(?:[ \t]+{_ADDR_LINE_CHAR}+)?)?"
    rf"(?:,[\s\n]*(?:Condom[ií]nio|Conjunto|Bloco|Bl\.|Apto\.?|Apartamento|Casa|Sala|Lote|Quadra|Bairro|Centro|Parque|Residencial|Manaus){_ADDR_LINE_CHAR}*)*)"
)

# 9. CEP brasileiro (suporta colagem OCR `CEP69077-798`, sem hífen rotulado `CEP:69068482` e padrão `69067-005`)
RE_CEP_LABELED = re.compile(r"(?i)\bcep[\s\n]*[:=-]?[\s\n]*(\d{2}\.?\d{3}-?[\s\n]*\d{3})(?!\d)")
RE_CEP = re.compile(r"(?i)(?:\bcep[\s\n]*[:=-]?[\s\n]*)?\b(\d{2}\.?\d{3}-[\s\n]*\d{3})\b")

# 10. Telefones fixos, móveis com DDD, rotulados via OCR, E.164 (+5592984310092) e truncados (+55 97 917...)
RE_PHONE_LABELED = re.compile(
    r"(?i)\b(?:telefones?|tels?\.?|cel(?:ular)?|fone|whatsapp)[\s\n]*[:=-]?[\s\n]*(\(?\d{2}\)?[\s\n]*9?[\s\n]*\d{4}[-\s–—.]?\d{4})\b"
)
RE_PHONE_BR = re.compile(
    r"(?:(?<!\w)\+55[\s\n]*\d{2}[\s\n]*9?\d{4}[-\s–—\n]*\d{4}\b"
    r"|(?:(?<!\w)\+55[\s\n]*)?\([1-9][0-9]\)[\s\n]*(?:9[\s\n]?\d{4}|\d{4})[-\s–—\n]*\d{4}\b"
    r"|(?:(?<!\w)\+55[\s\n]+)?\b[1-9][0-9][ \t]+(?:9?\d{4})[-\s–—]\d{4}\b)"
)
RE_PHONE_PARTIAL_INTL = re.compile(
    r"(?<!\w)(\+55[\s\n]*[1-9]\d[\s\n]*9?\d{3,5}\.{2,3})"
)

# 11. E-mail
RE_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

# 12. Matrícula SIAPE (6 a 8 dígitos vinculados estritamente a contexto SIAPE / Matrícula SIAPE)
RE_SIAPE_LABELED = re.compile(
    r"(?i)\b(?:matr[ií]cula[\s\n]+siape|siape|mat\.?[\s\n]*siape)[\s\n]*(?:n[º°o0.]?[\s\n]*)?[:=-]?[\s\n]*(\d{6,8})\b"
)

# 13. Matrícula Acadêmica UFAM, Número de Inscrição e Matrícula de Concessionária (6 a 10 dígitos, com DV opcional `-1`)
RE_MATRICULA_UFAM = re.compile(
    r"(?i)\b(?:matr[ií]cula(?!\s*siape)(?:[\s\n]+(?:acad[eê]mica|ufam|institucional))?|discente[\s\n]+matr[ií]cula|inscri[cç][aã]o(?![\s\n]+eleitoral)|insc\.?)[\s\n]*(?:n[º°o0.]?[\s\n]*)?[:=-]?[\s\n]*(\d{6,10}(?:-[0-9Xx])?)\b"
)
# Matrícula de concessionária/tabela multi-coluna (ex.: `MATRICULA\nREFERENCIA\nEMISSAO\n1905449-1`)
RE_MATRICULA_TABLE_ROW = re.compile(
    r"(?i)\bmatr[ií]cula\b[^\n]{0,40}\n(?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ/\s]{3,35}\n){1,3}(\d{6,10}-[0-9Xx])\b"
)

# 14. Processo Administrativo SEI / SIPAC UFAM
RE_SEI_PROCESS = re.compile(r"\b(23105\.\d{6}\s*/\s*\d{4}-\d{2})\b")

# 15. Dados Financeiros e Bancários (`FINANCIAL_BANKING` - Jamais classificar como PERSONAL_SENSITIVE)
RE_CREDIT_CARD = re.compile(
    r"(?i)(?<!\d)((?:4\d{3}|5[1-5]\d{2}|2[2-7]\d{2}|6(?:011|5\d{2})|3[47]\d{2})[\s-]*(?:[X*•]{3,5}[\s-]*[X*•]{3,5}|[X*•]{6,10}|\d{4}[\s-]*\d{4})[\s-]*\d{4})\b"
)
RE_BANK_AGENCY = re.compile(
    r"(?i)(?:^|[\s|I;,(])(?:ag[eê]ncia(?:[\s\n]+banc[aá]ria)?\b|ag\.?)[:=;\s\n]+(\d{3,5}(?:-[\s\n]*[0-9Xx])?)(?![\d-])"
)
RE_BANK_OPERATION = re.compile(
    r"(?i)\b(?:opera[cç][aã]o\b|op\b\.?)[:=;\s\n]+(\d{2,4})\b"
)
# Captura APENAS o número da conta após "Conta Corrente:" / "Conta:" / "C/C" (inclusive após quebra de linha)
RE_BANK_ACCOUNT = re.compile(
    r"(?i)(?:^|[\s|I;,(])(?:conta(?:[\s\n]+(?:corrente|poupan[cç]a|sal[aá]rio|banc[aá]ria))?|c/c|c\.c\.)[\s\n]*(?:n[º°o0.]?[\s\n]*)?[:=-]?[\s\n]*([\d\.]{3,14}-[\s\n]*[0-9Xx]|\d{5,12})(?![0-9-])"
)
# Captura contas logo após número de agência (ex.: "3563-7 48921-3" ou "3563-7 | 48921-3")
RE_BANK_ACCOUNT_AFTER_AGENCY = re.compile(
    r"\b\d{4}(?:-[0-9Xx])?[ \t|;/,-]+(\d{4,12}-[\s\n]*[0-9Xx])\b"
)
# Chave PIX rotulada ou UUID quebrado em múltiplas linhas (2-3 linhas)
RE_PIX_UUID_STANDALONE = re.compile(
    r"\b([0-9a-fA-F]{8}-[\s\n]*[0-9a-fA-F]{4}-[\s\n]*[0-9a-fA-F]{4}-[\s\n]*[0-9a-fA-F]{4}-[\s\n]*[0-9a-fA-F]{12})\b"
)
RE_PIX_KEY_UUID = re.compile(
    r"(?i)\b(?:chave[\s\n]+pix|pix)[\s\n]*[:=-]?[\s\n]*"
    r"([0-9a-f]{8}-[\s\n]*[0-9a-f]{4}-[\s\n]*[0-9a-f]{4}-[\s\n]*[0-9a-f]{4}-[\s\n]*[0-9a-f]{12}"
    r"|\+55[\s\n]*\d{2}[\s\n]*9?\d{4}[-\s\n]*\d{4}"
    r"|(?=[^\s,;]*[\d@+])[^\s,;]{6,64})\b"
)
# Valores monetários rotulados ou em contexto de folha/bolsa/tabela financeira (com suporte a R$\n1.400,00)
RE_REMUNERATION_VALUE = re.compile(
    r"(?i)\b(?:remunera[cç][aã]o|rendimento[ \t]+bruto|rendimento[ \t]+l[ií]quido|proventos|vencimento(?:[ \t]+b[aá]sico)?|rubrica(?:[ \t]+salarial)?|valor[ \t]+(?:bruto|l[ií]quido|total|da[ \t]+bolsa|mensal)|sal[aá]rio|pens[aã]o|folha|bolsa(?:[\s\n]+(?:acad[eê]mica|aux[ií]lio|pesquisa|extens[aã]o))?)[\s\n]*[:=-]?[\s\n]*(R\$[\s\n]*\d{1,3}(?:\.\d{3})*,\d{2})\b"
)
RE_MONETARY_VALUE_CONTEXTUAL = re.compile(
    r"(R\$[\s\n]*\d{1,3}(?:\.\d{3})*,\d{2})\b"
)

# 16. Patologias e Códigos Médicos CID-10 / CID-11 (`PERSONAL_SENSITIVE` - Art. 5º, II, LGPD)
RE_SENSITIVE_PATHOLOGY_CID = re.compile(
    r"(?i)\b((?:Epis[óo]dio[\s\n]+Depressivo(?:[\s\n]+(?:Moderado|Leve|Grave))?"
    r"|Lombociatalgia[\s\n]+Aguda"
    r"|Dor[\s\n]+Lombar[\s\n]+Baixa[\s\n]+Cr[oô]nica(?:[\s\n]+com[\s\n]+Radiculopatia)?"
    r"|Rea[cç][aã]o[\s\n]+Aguda[\s\n]+ao[\s\n]+Estresse(?:[\s\n]+e[\s\n]+Burnout)?"
    r"|Vis[aã]o[\s\n]+Monocular(?:[\s\n]+e[\s\n]+Retinopatia)?"
    r"|Hipertens[ãa]o(?:[\s\n]+Arterial(?:[\s\n]+Sist[êe]mica)?)?"
    r"|Transtorno[\s\n]+(?:Depressivo|de[\s\n]+Ansiedade)(?:[\s\n]+Generalizada)?"
    r"|Depress[ãa]o(?:[\s\n]+Moderada)?"
    r"|Acompanhamento[\s\n]+TEA"
    r"|Surdez[\s\n]+Bilateral[\s\n]+Neurosensorial)[\s\n]*\((?:CID[\s\n]*-?[\s\n]*1[01]|CID)[:\s=-]*[A-Z0-9]\d{2}(?:\.\d{1,2})?\))"
)
RE_CID_MEDICAL = re.compile(
    r"(?i)\b(?:cid[\s\n]*-[\s\n]*1[01]|cid)[\s\n]*[:=-]?[\s\n]*"
    r"([A-TV-Z]\d{2}(?:\.\d{1,2})?|[A-Z0-9]\d{2}(?:\.\d{1,2})?(?=\)|\s|$|[,;])|(?=[0-9A-HJ-NP-Z]*\d)[0-9A-HJ-NP-Z]{4,6}(?:\.[0-9A-HJ-NP-Z]{1,2})?)\b"
)
RE_CID10_STANDALONE_WITH_LABEL = re.compile(
    r"(?i)\b(?:cid[\s\n]*-[\s\n]*1[01]|cid)[\s\n]*[:=-]?[\s\n]*([A-Z0-9]\d{2}(?:\.\d{1,2})?)\b"
)

# 17. Dados Sensíveis Contextualizados (Art. 5º, II, LGPD: Saúde, Genética, Biometria, Raça/Etnia, Sindicato, Religião)
RE_SENSITIVE_ETHNIC_LABELED = re.compile(
    r"(?i:\b(?:autodeclara[cçg][aã]o[\s\n]+[eé]tnico-racial(?:[\s\n]+homologada[\s\n]+como)?|origem[\s\n]+racial)[\s\n]*[:=-]?[\s\n]*)([^\n|;.]+)"
)
RE_SENSITIVE_ETHNIC_QUOTA = re.compile(
    r"\b("
    r"(?i:autodeclarad[oa](?:\(a\))?)[\s\n]+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç\-]{2,}"
    r"|(?i:povo)[\s\n]+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç\-]{2,}(?:[\s\n]+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç\-]{2,})?"
    r")\b"
)
RE_SENSITIVE_CLINICAL_CONDITION = re.compile(
    r"(?i)\b("
    r"surdez[\s\n]+(?:bilateral|unilateral)?[\s\n]*(?:neurosensorial|neurossensorial|profunda|moderada|severa)?"
    r"|pessoa[\s\n]+com[\s\n]+defici[êe]ncia(?:[\s\n]*\(pcd\))?"
    r"|acompanhamento[\s\n]+(?:tea|cl[ií]nico|psicol[óo]gico|psiqui[aá]trico|m[eé]dico)"
    r"|transtorno[\s\n]+do[\s\n]+espectro[\s\n]+autista(?:[\s\n]*\(tea\))?"
    r"|transtorno[\s\n]+de[\s\n]+ansiedade(?:[\s\n]+generalizada)?"
    r"|defici[êe]ncia[\s\n]+(?:auditiva|visual|f[ií]sica|intelectual|m[uú]ltipla)"
    r"|epis[óo]dio[\s\n]+depressivo(?:[\s\n]+(?:moderado|leve|grave))?"
    r"|lombociatalgia[\s\n]+aguda"
    r"|dor[\s\n]+lombar[\s\n]+baixa[\s\n]+cr[oô]nica(?:[\s\n]+com[\s\n]+radiculopatia)?"
    r"|rea[cç][aã]o[\s\n]+aguda[\s\n]+ao[\s\n]+estresse(?:[\s\n]+e[\s\n]+burnout)?"
    r"|vis[aã]o[\s\n]+monocular(?:[\s\n]+e[\s\n]+retinopatia)?"
    r"|hipertens[ãa]o(?:[\s\n]+arterial(?:[\s\n]+sist[êe]mica)?)?"
    r")(?=\s|$|[,.;:)\]])"
)
RE_GENETIC_MARKER = re.compile(
    r"(?i:\bmarcador[\s\n]+gen[ée]tico(?:[\s\n]+positivo[\s\n]+para)?[\s\n]*[:=-]?[\s\n]*)([A-Z0-9][A-Z0-9\-]{2,24})\b"
)
RE_SENSITIVE_UNION_LABELED = re.compile(
    r"(?i)(?:\b(?:filia[cçg][aã]o|fl[aã]o|desconto|mensalidade)[ \t]+|\b)sindical[ \t]*[:=-][ \t]*([^\n|;]+)"
)
RE_SENSITIVE_UNION = re.compile(
    r"(?i:\bf?iliad[oa][\s\n]+(?:ativ[oa][\s\n]+)?(?:ao|à)[\s\n]+)(?:[^\n,.;|]|\.(?=\d))+(?:\((?:[^\)\n.]|\.(?=\d))+\))?"
    r"|(?i:\bficha[\s\n]+sindical[\s\n]+n[º°o0.]?[\s\n]*(?:\d{1,3}(?:\.\d{3})*|\d+))"
    r"|(?i:\bsindicato[\s\n]*[:=-]?[\s\n]*)(?:ADUA|SINTEST(?:-AM)?|ANDES(?:-SN)?|FASUBRA|SINASEFE|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Z0-9\-/]{2,24})"
    r"|\b(?:SINTEST-AM|ADUA(?:\s*-\s*[^\n,.;|]+)?)\b"
)
RE_RELIGIOUS_BELIEF = re.compile(
    r"(?i)\b(membr[oa](?:[\s\n]+batizad[oa])?[\s\n]+da[\s\n]+Igreja[\s\n]+[^\n\.|;]+"
    r"|membro[\s\n]+da[\s\n]+Comunidade[\s\n]+Judaica[^\n\.|;]+"
    r"|guarda[\s\n]+do[\s\n]+s[áa]bado[^\)\n|;]+)"
)
RE_RELIGIOUS_BELIEF_LABELED = re.compile(
    r"(?i:\b(?:convic[cç][aã]o|cren[cç]a|orienta[cç][aã]o)[\s\n]+religiosa(?:[\s\n]+declarada)?[\s\n]*[:=-][\s\n]*)([^\n\.|;]{3,80})"
)
RE_BIOMETRY_ID = re.compile(
    r"(?i:(?:\b(?:Hash[\s\n]+de[\s\n]+Template[\s\n]+Biom[ée]trico(?:[\s\n]+Digital)?[\s\n]*(?:n[º°o0.]?)?|Biometria[\s\n]*ID|Registro[\s\n]+Facial[\s\n]*ID|Foto)[\s\n]*[:=-][\s\n]*|(?=(?:#(?:IMG|BIO|FACIAL)|BIO-[A-Z0-9\-]{3,}))))(#[A-Z0-9\-]{3,32}|[A-Z0-9][A-Z0-9\-]{3,32})\b"
)
RE_BIOMETRY_PHOTO_PLACEHOLDER = re.compile(
    r"(?i)(\[FOTO[\s\n]+3[xX]4[^\]]*\])"
)

# 18. Menores de Idade e Dependentes (`MINOR_OR_DEPENDENT` - Art. 14, LGPD)
_NAME_WORD = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç'-]+"
_UPPER_NAME_WORD = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}"
_ANY_CASE_NAME_TOKEN = r"(?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç'-]+|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]\.?)"
_NAME_PARTICLE = r"(?:da|de|do|dos|das|DA|DE|DO|DOS|DAS)"
_NAME_SEP = r"(?:[ \t]+|[ \t]*\n[ \t]*)"
_FULL_NAME_PATTERN = (
    rf"({_NAME_WORD}(?:{_NAME_SEP}(?:{_NAME_PARTICLE}|{_NAME_WORD}(?![ \t]*[:=\d]))){{1,6}})"
)
_FLEX_NAME_PATTERN = (
    rf"({_ANY_CASE_NAME_TOKEN}(?:{_NAME_SEP}(?:{_NAME_PARTICLE}|{_ANY_CASE_NAME_TOKEN}(?![ \t]*[:=\d]))){{1,6}})"
)
_SAME_LINE_FLEX_NAME = (
    rf"({_ANY_CASE_NAME_TOKEN}(?:[ \t]+(?:{_NAME_PARTICLE}|{_ANY_CASE_NAME_TOKEN}(?![ \t]*[:=\d]))){{1,6}})"
)

RE_DEPENDENT_NAMED = re.compile(
    rf"(?i:\b(?:dependente(?:[ \t]+funcional|[ \t]+legal)?|menor[ \t]+assistido|benefici[aá]rio[ \t]+de[ \t]+pens[aã]o[ \t]+aliment[ií]cia|filho\(a\)[ \t]+menor)(?:[ \t]*[:=-][ \t\n]*|[ \t]+)){_FLEX_NAME_PATTERN}"
)

# 19. Nomes de Pessoas Naturais (Title Case, CAIXA ALTA, iniciais abreviadas `N.`, tabelas com parentesco)
RE_ADMIN_UPPER_OR_ABBR_PERSON = re.compile(
    r"(?i:\b(?:Nome(?:[ \t]+Completo)?|Servidor(?:\(a\)|a)?|Interessad[oa](?:\(a\))?|Requerente|Titular"
    r"|Candidat[oa](?:\(a\))?(?:[ \t]+(?:Aprovad[oa]|Classificad[oa]|Habilitad[oa]|Convocad[oa]|Selecionad[oa]))?"
    r"|Bolsista|Discente|Docente|Favorecid[oa](?:\(a\))?|Benefici[aá]ri[oa](?:\(a\))?"
    r"|Resp\.?[\s\n]*Legal|Respons[aá]vel[\s\n]+Legal))"
    r"[ \t]*[:=]+[\s\n]*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇa-záéíóúâêôãõç\.\t \n]{5,75}?)"
    r"(?=[ \t]*(?:\||CPF|RG|SIAPE|Matr[íi]cula|Cargo|Data|\(|$|\n))"
)
RE_SERVIDOR_UPPER_PERSON = re.compile(
    rf"(?i:\bservidor(?:\(a\)|a)?(?:[ \t]+p[uú]blic[oa])?[\s\n]+)({_UPPER_NAME_WORD}(?:{_NAME_SEP}(?:DA|DE|DO|DOS|DAS|E|{_UPPER_NAME_WORD}|[A-Z]\.)){{2,6}})"
)
RE_TABLE_KINSHIP_PERSON = re.compile(
    r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+(?:[\s\n]+(?:da|de|do|dos|das|e|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]*|[A-Z]\.?)){1,6})[\s\n]*(?=\((?i:Servidor|Titular|Pensionista))"
)
RE_TABLE_KINSHIP_DEPENDENT = re.compile(
    r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+(?:[\s\n]+(?:da|de|do|dos|das|e|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]*|[A-Z]\.?)){1,6})[\s\n]*(?=\((?i:Filh[oa]|Menor|Dependente))"
)
RE_ADMIN_ANCHORED_PERSON = re.compile(
    rf"(?i:\b(?:servidor\(?a\)?|interessad[oa]\(?a\)?|requerente|declarante|favorecid[oa]\(?a\)?|benefici[aá]ri[oa]\(?a\)?|nome[ \t]+do\(?a\)?[ \t]+servidor\(?a\)?|nome[ \t]+completo|paciente|titular|f[ií]?l[ií]?a[cçg][aã]o|resp\.?[\s\n]*legal|respons[aá]vel[\s\n]+legal|discente|docente|bolsista|alun[oa]\(?a\)?|candidat[oa]\(?a\)?|orientand[oa])[\s\n]*[:=-][\s\n]*){_FULL_NAME_PATTERN}"
)
RE_ROLE_PROSE_PERSON = re.compile(
    rf"(?i:\b(?:o|a)[\s\n]+(?:discente|docente|servidor(?:a|\(a\))?|bolsista|alun[oa]|requerente|interessad[oa]|benefici[aá]ri[oa]|paciente|candidat[oa]|declarante)[\s\n]+){_FULL_NAME_PATTERN}"
)
RE_FILIACAO_FIRST_PARENT = re.compile(
    rf"(?i:\b(?:filho\(a\)[ \t]+de|filho[ \t]+de|filha[ \t]+de)[\s\n]+){_FULL_NAME_PATTERN}"
)
RE_FILIACAO_SECOND_PARENT = re.compile(
    rf"(?i:\b(?:filho\(a\)[ \t]+de|filho[ \t]+de|filha[ \t]+de)[\s\n]+){_NAME_WORD}(?:{_NAME_SEP}(?:{_NAME_PARTICLE}|{_NAME_WORD})){{1,5}}(?i:[ \t]+e(?:[ \t]+de)?[\s\n]+){_FULL_NAME_PATTERN}"
)
RE_FILIACAO_COLON_SECOND_PARENT = re.compile(
    rf"(?i:\bf[ií]?l[ií]?a[cçg][aã]o[\s\n]*:[\s\n]*){_ANY_CASE_NAME_TOKEN}(?:[ \t]+(?:{_NAME_PARTICLE}|{_ANY_CASE_NAME_TOKEN})){{1,5}}(?i:[ \t]+e(?:[ \t]+de)?[\s\n]+){_FLEX_NAME_PATTERN}"
)
# Cédula de RG: nome da mãe na linha logo após `FILIACAO` (sem `:`)
RE_FILIACAO_RG_NEXT_LINE = re.compile(
    rf"(?i:\bf[ií]?l[ií]?a[cçg][aã]o[ \t]*\n[ \t]*){_SAME_LINE_FLEX_NAME}"
)

# 20. Data de Nascimento rotulada (suporta ano 4 dígitos, 2 dígitos em cartão CPF e layout 2 colunas)
RE_BIRTH_DATE = re.compile(
    r"(?i)\b(?:data[\s\n]+(?:d[eo][\s\n]+)?nascimento|dt\.?[\s\n]*nasc\.?|nascimento|nasceu[\s\n]+em|nascid[oa](?:\(a\))?(?:[\s\n]+em)?|nasc\.?)[\s\n]*[:=-]?[\s\n]*((?:0[1-9]|[12]\d|3[01])/(?:0[1-9]|1[0-2])/(?:19\d{2}|20[0-2]\d|\d{2}))\b"
)
RE_BIRTH_DATE_TWO_COL = re.compile(
    r"(?i)\bdata[\s\n]+d[eo][\s\n]+nascimento[ \t]*\n[^\n]{4,24}\n[ \t]*((?:0[1-9]|[12]\d|3[01])/(?:0[1-9]|1[0-2])/(?:19\d{2}|20[0-2]\d|\d{2}))\b"
)
# Data de nascimento em verso de RG sem rótulo legível (ex.: `08/01/1948` antes de `MANAUS-AM` / `NATURALIDADE`)
RE_BIRTH_DATE_RG_CONTEXTUAL = re.compile(
    r"\b((?:0[1-9]|[12]\d|3[01])/(?:0[1-9]|1[0-2])/(?:19\d{2}|20[01]\d))\b"
)


def _validate_siape(raw: str) -> bool:
    digits = strip_non_digits(raw)
    return 6 <= len(digits) <= 8 and len(set(digits)) > 1


def _validate_non_monotone_11(raw: str) -> bool:
    digits = strip_non_digits(raw)
    return len(digits) == 11 and len(set(digits)) > 1


def _validate_non_monotone_12(raw: str) -> bool:
    digits = strip_non_digits(raw)
    return len(digits) == 12 and len(set(digits)) > 1


COMPILED_REGEX_SPECS: tuple[RegexPatternSpec, ...] = (
    RegexPatternSpec(
        entity_type="CPF",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_CPF_FORMATTED,
        value_group=1,
        base_confidence=0.98,
        validator=validate_cpf,
    ),
    RegexPatternSpec(
        entity_type="CPF",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_CPF_LABELED,
        value_group=1,
        base_confidence=0.98,
        validator=validate_cpf,
    ),
    RegexPatternSpec(
        entity_type="CNH",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_CNH_LABELED,
        value_group=1,
        base_confidence=0.95,
        validator=_validate_non_monotone_11,
    ),
    RegexPatternSpec(
        entity_type="PIS_PASEP",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_PIS_PASEP_FORMATTED,
        value_group=1,
        base_confidence=0.95,
        validator=validate_pis_pasep_nit,
    ),
    RegexPatternSpec(
        entity_type="PIS_PASEP",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_PIS_PASEP_LABELED,
        value_group=1,
        base_confidence=0.95,
        validator=_validate_non_monotone_11,
    ),
    RegexPatternSpec(
        entity_type="RG",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_RG_LABELED,
        value_group=1,
        base_confidence=0.90,
    ),
    RegexPatternSpec(
        entity_type="PASSPORT",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_PASSPORT_LABELED,
        value_group=1,
        base_confidence=0.93,
    ),
    RegexPatternSpec(
        entity_type="TITULO_ELEITOR",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_TITULO_ELEITOR_LABELED,
        value_group=1,
        base_confidence=0.96,
        validator=_validate_non_monotone_12,
    ),
    RegexPatternSpec(
        entity_type="TITULO_ELEITOR",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_TITULO_ELEITOR_STANDALONE,
        value_group=1,
        base_confidence=0.94,
        validator=validate_titulo_eleitor,
    ),
    RegexPatternSpec(
        entity_type="ADDRESS",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_ADDRESS_LABELED,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="ADDRESS",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_RESIDENTIAL_ADDRESS,
        value_group=1,
        base_confidence=0.90,
    ),
    RegexPatternSpec(
        entity_type="CEP",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_CEP_LABELED,
        value_group=1,
        base_confidence=0.91,
    ),
    RegexPatternSpec(
        entity_type="CEP",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_CEP,
        value_group=1,
        base_confidence=0.86,
    ),
    RegexPatternSpec(
        entity_type="PHONE",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_PHONE_LABELED,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="PHONE",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_PHONE_BR,
        value_group=0,
        base_confidence=0.90,
    ),
    RegexPatternSpec(
        entity_type="PHONE",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_PHONE_PARTIAL_INTL,
        value_group=1,
        base_confidence=0.88,
    ),
    RegexPatternSpec(
        entity_type="EMAIL",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_EMAIL,
        value_group=1,
        base_confidence=0.96,
    ),
    RegexPatternSpec(
        entity_type="SIAPE",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_SIAPE_LABELED,
        value_group=1,
        base_confidence=0.95,
        validator=_validate_siape,
    ),
    RegexPatternSpec(
        entity_type="MATRICULA_UFAM",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_MATRICULA_UFAM,
        value_group=1,
        base_confidence=0.94,
    ),
    RegexPatternSpec(
        entity_type="MATRICULA_UFAM",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_MATRICULA_TABLE_ROW,
        value_group=1,
        base_confidence=0.91,
    ),
    RegexPatternSpec(
        entity_type="RG",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_RG_CARD_CONTEXTUAL,
        value_group=1,
        base_confidence=0.89,
        requires_context=True,
    ),
    RegexPatternSpec(
        entity_type="BIRTH_DATE",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_BIRTH_DATE,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="BIRTH_DATE",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_BIRTH_DATE_TWO_COL,
        value_group=1,
        base_confidence=0.91,
    ),
    RegexPatternSpec(
        entity_type="BIRTH_DATE",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_BIRTH_DATE_RG_CONTEXTUAL,
        value_group=1,
        base_confidence=0.88,
        requires_context=True,
    ),
    RegexPatternSpec(
        entity_type="DEPENDENT_DATA",
        category=DataCategory.MINOR_OR_DEPENDENT,
        pattern=RE_TABLE_KINSHIP_DEPENDENT,
        value_group=1,
        base_confidence=0.93,
    ),
    RegexPatternSpec(
        entity_type="PERSON_NAME",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_TABLE_KINSHIP_PERSON,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="PERSON_NAME",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_SERVIDOR_UPPER_PERSON,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="PERSON_NAME",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_ADMIN_UPPER_OR_ABBR_PERSON,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="PERSON_NAME",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_ADMIN_ANCHORED_PERSON,
        value_group=1,
        base_confidence=0.91,
    ),
    RegexPatternSpec(
        entity_type="PERSON_NAME",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_ROLE_PROSE_PERSON,
        value_group=1,
        base_confidence=0.91,
    ),
    RegexPatternSpec(
        entity_type="FILIACAO",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_FILIACAO_FIRST_PARENT,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="FILIACAO",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_FILIACAO_SECOND_PARENT,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="FILIACAO",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_FILIACAO_COLON_SECOND_PARENT,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="FILIACAO",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_FILIACAO_RG_NEXT_LINE,
        value_group=1,
        base_confidence=0.91,
    ),
    RegexPatternSpec(
        entity_type="PROCESSO_SEI",
        category=DataCategory.PERSONAL_COMMON,
        pattern=RE_SEI_PROCESS,
        value_group=1,
        base_confidence=0.88,
        requires_context=True,
    ),
    # Categorias Financeiras e Bancárias -> SEMPRE FINANCIAL_BANKING (Nunca PERSONAL_SENSITIVE)
    RegexPatternSpec(
        entity_type="CREDIT_CARD",
        category=DataCategory.FINANCIAL_BANKING,
        pattern=RE_CREDIT_CARD,
        value_group=1,
        base_confidence=0.96,
    ),
    RegexPatternSpec(
        entity_type="BANK_AGENCY",
        category=DataCategory.FINANCIAL_BANKING,
        pattern=RE_BANK_AGENCY,
        value_group=1,
        base_confidence=0.93,
    ),
    RegexPatternSpec(
        entity_type="BANK_OPERATION",
        category=DataCategory.FINANCIAL_BANKING,
        pattern=RE_BANK_OPERATION,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="BANK_ACCOUNT",
        category=DataCategory.FINANCIAL_BANKING,
        pattern=RE_BANK_ACCOUNT,
        value_group=1,
        base_confidence=0.95,
    ),
    RegexPatternSpec(
        entity_type="BANK_ACCOUNT",
        category=DataCategory.FINANCIAL_BANKING,
        pattern=RE_BANK_ACCOUNT_AFTER_AGENCY,
        value_group=1,
        base_confidence=0.94,
    ),
    RegexPatternSpec(
        entity_type="PIX_KEY",
        category=DataCategory.FINANCIAL_BANKING,
        pattern=RE_PIX_UUID_STANDALONE,
        value_group=1,
        base_confidence=0.95,
    ),
    RegexPatternSpec(
        entity_type="PIX_KEY",
        category=DataCategory.FINANCIAL_BANKING,
        pattern=RE_PIX_KEY_UUID,
        value_group=1,
        base_confidence=0.95,
    ),
    RegexPatternSpec(
        entity_type="REMUNERATION",
        category=DataCategory.FINANCIAL_BANKING,
        pattern=RE_REMUNERATION_VALUE,
        value_group=1,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="REMUNERATION",
        category=DataCategory.FINANCIAL_BANKING,
        pattern=RE_MONETARY_VALUE_CONTEXTUAL,
        value_group=1,
        base_confidence=0.90,
        requires_context=True,
    ),
    # Categorias Sensíveis (Art. 5º, II, LGPD) -> PERSONAL_SENSITIVE
    RegexPatternSpec(
        entity_type="HEALTH_DATA",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_SENSITIVE_PATHOLOGY_CID,
        value_group=1,
        base_confidence=0.96,
    ),
    RegexPatternSpec(
        entity_type="CID_MEDICAL",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_CID_MEDICAL,
        value_group=1,
        base_confidence=0.96,
    ),
    RegexPatternSpec(
        entity_type="HEALTH_DATA",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_SENSITIVE_CLINICAL_CONDITION,
        value_group=1,
        base_confidence=0.94,
    ),
    RegexPatternSpec(
        entity_type="GENETIC_DATA",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_GENETIC_MARKER,
        value_group=1,
        base_confidence=0.95,
    ),
    RegexPatternSpec(
        entity_type="ETHNIC_RACIAL_DECLARATION",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_SENSITIVE_ETHNIC_LABELED,
        value_group=1,
        base_confidence=0.94,
    ),
    RegexPatternSpec(
        entity_type="ETHNIC_RACIAL_DECLARATION",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_SENSITIVE_ETHNIC_QUOTA,
        value_group=1,
        base_confidence=0.93,
    ),
    RegexPatternSpec(
        entity_type="UNION_AFFILIATION",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_SENSITIVE_UNION_LABELED,
        value_group=1,
        base_confidence=0.93,
    ),
    RegexPatternSpec(
        entity_type="UNION_AFFILIATION",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_SENSITIVE_UNION,
        value_group=0,
        base_confidence=0.92,
    ),
    RegexPatternSpec(
        entity_type="RELIGIOUS_BELIEF",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_RELIGIOUS_BELIEF,
        value_group=1,
        base_confidence=0.93,
    ),
    RegexPatternSpec(
        entity_type="RELIGIOUS_BELIEF",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_RELIGIOUS_BELIEF_LABELED,
        value_group=1,
        base_confidence=0.93,
    ),
    RegexPatternSpec(
        entity_type="BIOMETRY_ID",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_BIOMETRY_ID,
        value_group=1,
        base_confidence=0.95,
    ),
    RegexPatternSpec(
        entity_type="FACIAL_PHOTO",
        category=DataCategory.PERSONAL_SENSITIVE,
        pattern=RE_BIOMETRY_PHOTO_PLACEHOLDER,
        value_group=1,
        base_confidence=0.95,
    ),
    # Menores e Dependentes (Art. 14, LGPD) -> MINOR_OR_DEPENDENT
    RegexPatternSpec(
        entity_type="DEPENDENT_DATA",
        category=DataCategory.MINOR_OR_DEPENDENT,
        pattern=RE_DEPENDENT_NAMED,
        value_group=1,
        base_confidence=0.93,
    ),
)


def compile_custom_regex_specs(raw_rules: Sequence[Any] | None) -> list[RegexPatternSpec]:
    """
    Valida e compila regras de expressões regulares personalizadas informadas pelo usuário.
    Protege contra ReDoS (quantificadores aninhados, tamanho excessivo) e padrões que casam string vazia.
    """
    if not raw_rules:
        return []
    if len(raw_rules) > MAX_CUSTOM_REGEX_RULES:
        raise ValueError(
            f"Limite máximo de {MAX_CUSTOM_REGEX_RULES} regras regex personalizadas excedido."
        )

    compiled_specs: list[RegexPatternSpec] = []
    for idx, rule in enumerate(raw_rules, start=1):
        if isinstance(rule, dict):
            raw_label = str(rule.get("label", "") or "")
            raw_pattern = str(rule.get("pattern", "") or "")
            raw_cat = rule.get("category", DataCategory.PERSONAL_COMMON)
            raw_mode = rule.get("action_mode", None)
            case_sensitive = bool(rule.get("case_sensitive", False))
        else:
            raw_label = str(getattr(rule, "label", "") or "")
            raw_pattern = str(getattr(rule, "pattern", "") or "")
            raw_cat = getattr(rule, "category", DataCategory.PERSONAL_COMMON)
            raw_mode = getattr(rule, "action_mode", None)
            case_sensitive = bool(getattr(rule, "case_sensitive", False))

        clean_label = " ".join(raw_label.strip().split())[:64] or f"REGRA_{idx}"
        if not raw_pattern or not raw_pattern.strip():
            raise ValueError(f"A regra regex '{clean_label}' possui padrão vazio.")
        if len(raw_pattern) > MAX_CUSTOM_REGEX_LENGTH:
            raise ValueError(
                f"A regra regex '{clean_label}' excede o limite de {MAX_CUSTOM_REGEX_LENGTH} caracteres."
            )
        if _REDOS_NESTED_QUANTIFIER_RE.search(raw_pattern):
            raise ValueError(
                f"A regra regex '{clean_label}' contém quantificadores aninhados potencialmente inseguros (ReDoS)."
            )

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(raw_pattern, flags)
        except re.error as exc:
            raise ValueError(
                f"Sintaxe inválida na regra regex '{clean_label}': {exc}"
            ) from exc

        if compiled.match("") is not None:
            raise ValueError(
                f"A regra regex '{clean_label}' casa texto vazio e geraria marcações inválidas. Torne o padrão mais específico."
            )

        category = raw_cat if isinstance(raw_cat, DataCategory) else DataCategory(str(raw_cat))
        preferred_mode: RedactionActionMode | None = None
        if raw_mode is not None and str(raw_mode).strip():
            preferred_mode = (
                raw_mode if isinstance(raw_mode, RedactionActionMode) else RedactionActionMode(str(raw_mode))
            )

        compiled_specs.append(
            RegexPatternSpec(
                entity_type=f"CUSTOM:{clean_label}",
                category=category,
                pattern=compiled,
                value_group=0,
                base_confidence=0.99,
                preferred_action_mode=preferred_mode,
                is_custom=True,
            )
        )
    return compiled_specs


__all__ = [
    "RegexPatternSpec",
    "COMPILED_REGEX_SPECS",
    "MAX_CUSTOM_REGEX_RULES",
    "MAX_CUSTOM_REGEX_LENGTH",
    "compile_custom_regex_specs",
    "RE_CPF_FORMATTED",
    "RE_CPF_LABELED",
    "RE_CNPJ_FORMATTED",
    "RE_CNH_LABELED",
    "RE_PIS_PASEP_FORMATTED",
    "RE_PIS_PASEP_LABELED",
    "RE_RG_LABELED",
    "RE_RG_CARD_CONTEXTUAL",
    "RE_PASSPORT_LABELED",
    "RE_TITULO_ELEITOR_LABELED",
    "RE_TITULO_ELEITOR_STANDALONE",
    "RE_ADDRESS_LABELED",
    "RE_CEP_LABELED",
    "RE_CEP",
    "RE_PHONE_LABELED",
    "RE_PHONE_BR",
    "RE_PHONE_PARTIAL_INTL",
    "RE_EMAIL",
    "RE_SIAPE_LABELED",
    "RE_MATRICULA_UFAM",
    "RE_SEI_PROCESS",
    "RE_CREDIT_CARD",
    "RE_BANK_AGENCY",
    "RE_BANK_OPERATION",
    "RE_BANK_ACCOUNT",
    "RE_BANK_ACCOUNT_AFTER_AGENCY",
    "RE_PIX_KEY_UUID",
    "RE_PIX_UUID_STANDALONE",
    "RE_REMUNERATION_VALUE",
    "RE_MONETARY_VALUE_CONTEXTUAL",
    "RE_SENSITIVE_PATHOLOGY_CID",
    "RE_CID_MEDICAL",
    "RE_CID10_STANDALONE_WITH_LABEL",
    "RE_SENSITIVE_ETHNIC_LABELED",
    "RE_SENSITIVE_ETHNIC_QUOTA",
    "RE_SENSITIVE_CLINICAL_CONDITION",
    "RE_GENETIC_MARKER",
    "RE_SENSITIVE_UNION_LABELED",
    "RE_SENSITIVE_UNION",
    "RE_RELIGIOUS_BELIEF",
    "RE_BIOMETRY_ID",
    "RE_BIOMETRY_PHOTO_PLACEHOLDER",
    "RE_DEPENDENT_NAMED",
    "RE_ADMIN_UPPER_OR_ABBR_PERSON",
    "RE_SERVIDOR_UPPER_PERSON",
    "RE_TABLE_KINSHIP_PERSON",
    "RE_TABLE_KINSHIP_DEPENDENT",
    "RE_ADMIN_ANCHORED_PERSON",
    "RE_ROLE_PROSE_PERSON",
    "RE_FILIACAO_FIRST_PARENT",
    "RE_FILIACAO_SECOND_PARENT",
    "RE_FILIACAO_COLON_SECOND_PARENT",
    "RE_FILIACAO_RG_NEXT_LINE",
    "RE_RESIDENTIAL_ADDRESS",
    "RE_BIRTH_DATE",
    "RE_BIRTH_DATE_TWO_COL",
    "RE_BIRTH_DATE_RG_CONTEXTUAL",
    "validate_cnh",
    "validate_cnpj",
]
