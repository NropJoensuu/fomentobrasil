import re
from decimal import Decimal

REGIAO_POR_UF = {
    "AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte", "RO": "Norte", "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste", "PB": "Nordeste",
    "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}


def get_regioes(ufs):
    """Recebe lista de UFs, retorna lista de regiões únicas (ordenada)."""
    if not ufs:
        return []
    return sorted({REGIAO_POR_UF[uf] for uf in ufs if uf in REGIAO_POR_UF})


def get_ufs_por_regiao(regiao):
    return [uf for uf, r in REGIAO_POR_UF.items() if r == regiao]


REGIOES = sorted(set(REGIAO_POR_UF.values()))


def parse_valor_brl(texto):
    """Converte o texto de um campo com máscara de moeda em Decimal.

    Espelha a regra da máscara `.mascara-moeda` (base.html): só os dígitos importam e os
    dois últimos são os centavos. Como a máscara roda tanto ao digitar quanto ao carregar
    a página, o formulário sempre envia um valor já formatado — "R$ 12.345,67" — ou o
    valor cru do banco ("12345.67"), e ambos caem na mesma regra.

    Sem separador decimal nenhum o número é lido como inteiro em reais, para que um valor
    colado à mão ("12345") não vire R$ 123,45.
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    tem_separador_decimal = "," in texto or "." in texto
    digitos = re.sub(r"\D", "", texto)
    if not digitos:
        return None
    if tem_separador_decimal:
        return Decimal(digitos) / 100
    return Decimal(digitos)
