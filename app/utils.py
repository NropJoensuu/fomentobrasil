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
