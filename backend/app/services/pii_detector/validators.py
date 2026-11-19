"""
Validadores matemáticos reais de dígitos verificadores (`validators.py`).

Implementa algoritmos oficiais para:
- CPF (11 dígitos, módulo 11 em dois estágios com rejeição de sequências monótonas);
- CNPJ (14 dígitos, módulo 11 com pesos 5..2 / 6..2);
- CNH (11 dígitos, algoritmo oficial DENATRAN/SENATRAN com ajuste dsc);
- PIS/PASEP/NIT (11 dígitos, módulo 11 com pesos [3, 2, 9, 8, 7, 6, 5, 4, 3, 2]);
- Título de Eleitor (12 dígitos, código de UF + dígitos verificadores TSE).
"""

from __future__ import annotations

import re

_NON_DIGIT_RE = re.compile(r"\D+")

# CNPJs institucionais conhecidos da Fundação Universidade do Amazonas (UFAM)
UFAM_INSTITUTIONAL_CNPJS: frozenset[str] = frozenset(
    {
        "04378626000197",  # Fundação Universidade do Amazonas - Matriz
        "04378626000278",  # UFAM Filial / HUUGV
    }
)


def strip_non_digits(value: str) -> str:
    """Remove qualquer caractere não numérico de uma string."""
    return _NON_DIGIT_RE.sub("", value)


def validate_cpf(raw_cpf: str) -> bool:
    """
    Valida matematicamente um CPF brasileiro (11 dígitos) segundo cálculo oficial da Receita Federal.
    Rejeita sequências de dígitos repetidos (ex.: 111.111.111-11, 000.000.000-00).
    """
    digits = strip_non_digits(raw_cpf)
    if len(digits) != 11:
        return False
    if len(set(digits)) == 1:
        return False

    nums = [int(ch) for ch in digits]

    # 1º dígito verificador
    sum_1 = sum(nums[i] * (10 - i) for i in range(9))
    rem_1 = (sum_1 * 10) % 11
    dv_1 = 0 if rem_1 == 10 else rem_1
    if nums[9] != dv_1:
        return False

    # 2º dígito verificador
    sum_2 = sum(nums[i] * (11 - i) for i in range(10))
    rem_2 = (sum_2 * 10) % 11
    dv_2 = 0 if rem_2 == 10 else rem_2
    return nums[10] == dv_2


def validate_cnpj(raw_cnpj: str) -> bool:
    """Valida matematicamente um CNPJ brasileiro (14 dígitos) segundo cálculo oficial da Receita Federal."""
    digits = strip_non_digits(raw_cnpj)
    if len(digits) != 14:
        return False
    if len(set(digits)) == 1:
        return False

    nums = [int(ch) for ch in digits]

    weights_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    sum_1 = sum(nums[i] * weights_1[i] for i in range(12))
    rem_1 = sum_1 % 11
    dv_1 = 0 if rem_1 < 2 else (11 - rem_1)
    if nums[12] != dv_1:
        return False

    weights_2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    sum_2 = sum(nums[i] * weights_2[i] for i in range(13))
    rem_2 = sum_2 % 11
    dv_2 = 0 if rem_2 < 2 else (11 - rem_2)
    return nums[13] == dv_2


def is_ufam_institutional_cnpj(raw_cnpj: str) -> bool:
    """Verifica se um CNPJ pertence à própria UFAM (deve ser suprimido como falso positivo)."""
    digits = strip_non_digits(raw_cnpj)
    return digits in UFAM_INSTITUTIONAL_CNPJS


def validate_cnh(raw_cnh: str) -> bool:
    """
    Valida matematicamente o número de registro da Carteira Nacional de Habilitação (CNH - 11 dígitos)
    segundo o algoritmo oficial do SENATRAN.
    """
    digits = strip_non_digits(raw_cnh)
    if len(digits) != 11:
        return False
    if len(set(digits)) == 1:
        return False

    nums = [int(ch) for ch in digits]

    # 1º dígito verificador: pesos crescentes de 9 a 1
    sum_1 = sum(nums[i] * (9 - i) for i in range(9))
    rem_1 = sum_1 % 11
    if rem_1 >= 10:
        dv_1 = 0
        dsc = 2
    else:
        dv_1 = rem_1
        dsc = 0

    if nums[9] != dv_1:
        return False

    # 2º dígito verificador: pesos crescentes de 1 a 9 com desconto dsc
    sum_2 = sum(nums[i] * (i + 1) for i in range(9))
    rem_2 = sum_2 % 11
    raw_dv2 = rem_2 - dsc
    if raw_dv2 < 0:
        raw_dv2 += 11
    dv_2 = 0 if raw_dv2 >= 10 else raw_dv2

    return nums[10] == dv_2


def validate_pis_pasep_nit(raw_pis: str) -> bool:
    """
    Valida matematicamente um número PIS/PASEP/NIT/NIS (11 dígitos) usando pesos [3,2,9,8,7,6,5,4,3,2].
    """
    digits = strip_non_digits(raw_pis)
    if len(digits) != 11:
        return False
    if len(set(digits)) == 1:
        return False

    nums = [int(ch) for ch in digits]
    weights = (3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    total = sum(nums[i] * weights[i] for i in range(10))
    rem = 11 - (total % 11)
    dv = 0 if rem in (10, 11) else rem
    return nums[10] == dv


def validate_titulo_eleitor_uf(raw_titulo: str) -> bool:
    """
    Valida formato de 12 dígitos e código de UF oficial do TSE (01 a 28) para Título de Eleitor
    sem exigir dígito verificador Módulo 11 (útil para células de coluna explícita com números sintéticos).
    """
    digits = strip_non_digits(raw_titulo)
    if len(digits) != 12 or len(set(digits)) == 1:
        return False
    uf_code = int(digits[8:10])
    return 1 <= uf_code <= 28


def validate_titulo_eleitor(raw_titulo: str) -> bool:
    """Valida matematicamente o Título de Eleitor brasileiro (12 dígitos) segundo regra do TSE."""
    digits = strip_non_digits(raw_titulo)
    if len(digits) != 12:
        return False
    if len(set(digits)) == 1:
        return False

    uf_code = int(digits[8:10])
    if not (1 <= uf_code <= 28):
        return False

    nums = [int(ch) for ch in digits]
    weights_1 = (2, 3, 4, 5, 6, 7, 8, 9)
    sum_1 = sum(nums[i] * weights_1[i] for i in range(8))
    rem_1 = sum_1 % 11
    if rem_1 == 0:
        dv_1 = 1 if uf_code in (1, 2) else 0
    elif rem_1 == 10:
        dv_1 = 0
    else:
        dv_1 = rem_1

    if nums[10] != dv_1:
        return False

    weights_2 = (7, 8, 9)
    sum_2 = nums[8] * weights_2[0] + nums[9] * weights_2[1] + dv_1 * weights_2[2]
    rem_2 = sum_2 % 11
    if rem_2 == 0:
        dv_2 = 1 if uf_code in (1, 2) else 0
    elif rem_2 == 10:
        dv_2 = 0
    else:
        dv_2 = rem_2

    return nums[11] == dv_2
