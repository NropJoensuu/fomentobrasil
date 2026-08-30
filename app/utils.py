import re
from decimal import Decimal, InvalidOperation

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


def parse_faixas(form):
    """Lê os campos indexados `faixa-<i>-<campo>` do formulário e devolve a lista de faixas.

    Os índices não são contínuos — o JS que remove uma linha deixa buracos de propósito
    (ver comentário em templates/_faixas.html) — então a varredura parte das chaves
    presentes no formulário, não de um `range()`.

    Faixas sem nome e sem valor nenhum são descartadas: é o que sobra quando o curador
    clica em "Adicionar faixa" e desiste sem preencher.

    Os valores viram string decimal com DUAS casas ("50000.00") porque o destino é
    `dados_extra`, que é JSONB — `Decimal` não é serializável em JSON, e `float` perderia
    precisão em dinheiro. As duas casas não são cosméticas: a máscara do formulário lê o
    valor como centavos, então gravar "50000" faria a faixa reaparecer como R$ 500,00 na
    próxima edição. É a mesma forma que a coluna `Numeric(14, 2)` já produz.
    """
    def em_centavos(campo):
        valor = parse_valor_brl(form.get(campo))
        return str(valor.quantize(Decimal("0.01"))) if valor is not None else None

    indices = sorted(
        {int(m.group(1)) for m in (re.match(r"faixa-(\d+)-", chave) for chave in form) if m}
    )

    faixas = []
    for i in indices:
        faixa = {
            "nome": (form.get(f"faixa-{i}-nome") or "").strip() or None,
            "descricao": (form.get(f"faixa-{i}-descricao") or "").strip() or None,
            "valor_minimo": em_centavos(f"faixa-{i}-valor_minimo"),
            "valor_maximo": em_centavos(f"faixa-{i}-valor_maximo"),
            "publico_alvo": form.getlist(f"faixa-{i}-publico_alvo") or None,
            "area_principal": form.get(f"faixa-{i}-area_principal") or None,
        }
        if any(faixa.values()):
            faixas.append(faixa)

    return faixas


def aplicar_faixas(dados_extra, faixas):
    """Grava `faixas` em `dados_extra` sem perder o que o scraper escreveu ali.

    `dados_extra` é compartilhado com os scrapers (numero_edital, documentos,
    fonte_baixa_estruturacao...). Devolve um dicionário novo — reatribuir a coluna é o que
    faz o SQLAlchemy detectar a mudança num JSONB.
    """
    novo = dict(dados_extra or {})
    if faixas:
        novo["faixas"] = faixas
    else:
        novo.pop("faixas", None)
    return novo or None


def formatar_moeda(valor):
    """Formata um valor monetário em pt-BR: 236000 -> "R$ 236.000,00".

    Aceita `Decimal` (as colunas `Numeric`) e `str` (as faixas, que vivem em JSONB).
    Registrado como filtro Jinja `moeda` em `create_app`.
    """
    if valor in (None, ""):
        return ""
    try:
        numero = Decimal(str(valor))
    except InvalidOperation:
        return str(valor)
    # Formata em en-US (1,234.56) e troca os separadores: evita depender de locale
    # instalado no sistema, que é o modo clássico de isso quebrar em produção.
    texto = f"{numero:,.2f}"
    return "R$ " + texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
