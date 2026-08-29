"""Scraper dos editais da FAPESQ (Paraíba).

CMS Plone — o primeiro entre as fontes. **Não há API JSON**: `Accept: application/json`
devolve HTML (é Plone Classic, sem `plone.restapi`), e `/RSS`, `/atom.xml`,
`?format=rss` e `/@@search?format=json` também devolvem HTML. Parsing de HTML, então.

Usar a **coleção**, não a página resumida: `/editais/2026/editais-2026` mostra só 5 itens,
enquanto `/editais/2026/colecao-de-editais-2026` traz os 62 (em 2026-08-29).

Estrutura de cada item — mais rica do que a listagem aparenta:

    <article class="tileItem ...">
      <h2 class="tileHeadline"><a class="summary url" href="...">TÍTULO</a></h2>
      <p class="tileBody"><span class="description">DESCRIÇÃO</span></p>   <!-- quase sempre -->
      <span class="documentByLine">... <i class="icon-day"></i> 11/08/2026 ...</span>
    </article>

Ou seja, dá para extrair `data_publicacao` (62/62) e `descricao` (60/62) — só `data_prazo`
fica indisponível, esse só existe dentro do edital.

MANUTENÇÃO ANUAL: a coleção é organizada por ano e `ANO` abaixo precisa ser atualizado
quando virar 2027 (a URL passa a ser `/editais/2027/colecao-de-editais-2027`). Anos
anteriores são histórico encerrado e não devem ser coletados.
"""

import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

# >>> Atualizar quando virar o ano (ver "MANUTENÇÃO ANUAL" na docstring). <<<
ANO = 2026

URL_COLECAO = f"https://fapesq.rpp.br/editais/{ANO}/colecao-de-editais-{ANO}"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

# O Plone pagina por offset: ?b_start:int=0, 30, 60...
ITENS_POR_PAGINA = 30
MAX_PAGINAS = 30  # trava contra laço infinito se a paginação passar a repetir a página

PADRAO_DATA = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")

# A FAPESQ publica na mesma seção editais que NÃO são fomento à pesquisa: contratação de
# pessoal, seleção de oficineiros, cadastro de reserva. São coletados assim mesmo — quem
# decide é o curador —, mas ficam marcados para ele priorizar a revisão.
# ATENÇÃO: é só um sinalizador, não um filtro. Há "PROCESSO SELETIVO DE PESQUISADORES
# PÓS-GRADUADOS" e "PROCESSO SELETIVO DE ESTUDANTES PARA AÇÕES AFIRMATIVAS" que são
# fomento legítimo e caem na mesma expressão.
PADRAO_POSSIVEL_NAO_FOMENTO = re.compile(
    r"processo\s+seletivo|contrata[çc][ãa]o|cadastro\s+de\s+reserva"
    r"|sele[çc][ãa]o\s+de\s+oficineiros",
    re.IGNORECASE,
)


def _limpar(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def _extrair_data(article):
    """Pega a data do `documentByLine` (ex.: "publicado 11/08/2026 15h59 Notícias")."""
    byline = article.select_one(".documentByLine")
    if not byline:
        return None
    match = PADRAO_DATA.search(byline.get_text(" ", strip=True))
    if not match:
        return None
    dia, mes, ano = match.groups()
    try:
        return datetime(int(ano), int(mes), int(dia)).date()
    except ValueError:
        return None


def _extrair_pagina(html):
    """Extrai os itens de uma página da coleção."""
    soup = BeautifulSoup(html, "html.parser")
    itens = []
    for article in soup.find_all("article"):
        link_tag = article.select_one("h2.tileHeadline a[href]")
        if not link_tag:
            continue
        titulo = _limpar(link_tag.get_text(" ", strip=True))
        if not titulo:
            continue

        descricao_tag = article.select_one("p.tileBody span.description")
        itens.append(
            {
                "titulo": titulo,
                "link": urljoin(URL_COLECAO, link_tag["href"]),
                "descricao": _limpar(descricao_tag.get_text(" ", strip=True))
                if descricao_tag
                else None,
                "data_publicacao": _extrair_data(article),
            }
        )
    return itens


def coletar_chamadas_fapesq(paginas_html=None):
    """Percorre a coleção do ano e devolve dicts prontos para inserção.

    `paginas_html` (lista de HTMLs) permite testar offline, sem bater na rede.
    """
    if paginas_html is None:
        paginas_html = []
        for numero in range(MAX_PAGINAS):
            inicio = numero * ITENS_POR_PAGINA
            url = URL_COLECAO if inicio == 0 else f"{URL_COLECAO}?b_start:int={inicio}"
            resp = requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            if not _extrair_pagina(resp.content):
                break  # página vazia: fim da coleção
            paginas_html.append(resp.content)

    vistos = set()
    resultados = []
    for html in paginas_html:
        for item in _extrair_pagina(html):
            # A paginação por offset pode repetir itens se a coleção mudar durante a
            # coleta; dedup por link já aqui evita processar o mesmo duas vezes.
            if item["link"] in vistos:
                continue
            vistos.add(item["link"])

            resultados.append(
                {
                    **item,
                    "possivel_nao_fomento": bool(
                        PADRAO_POSSIVEL_NAO_FOMENTO.search(item["titulo"])
                    ),
                    "tipo_parceria": detectar_tipo_parceria(
                        item["titulo"], item["descricao"]
                    ),
                }
            )
    return resultados


def salvar_no_banco(registros):
    """Insere registros novos, atualiza existentes se um campo monitorado mudou
    (ver app.scraper_utils), ou ignora quando nada mudou. Dedup/match por link."""
    novos = 0
    atualizados = 0
    ja_existentes = 0

    for r in registros:
        dados_extra = {}
        if r["possivel_nao_fomento"]:
            dados_extra["possivel_nao_fomento"] = True

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": r["data_publicacao"],
                "data_prazo": None,  # só existe dentro do edital
                "instituicao_financiadora": ["FAPESQ"],
                "tipo_instrumento": "chamada_publica_edital",
                "tipo_parceria": r["tipo_parceria"],
                "uf": ["PB"],
                "abrangencia": "estadual",
                # Placeholder: não é inferível do título. Corrigido na curadoria.
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
                "natureza_recurso": [],
                "publico_alvo": [],
                "origem": "institucional",
                "status": "pendente",
                "dados_extra": dados_extra or None,
            },
        )
        if resultado == "novo":
            novos += 1
        elif resultado == "atualizado":
            atualizados += 1
        else:
            ja_existentes += 1

    db.session.commit()
    return {"novos": novos, "atualizados": atualizados, "ja_existentes": ja_existentes}


if __name__ == "__main__":
    from app import create_app

    app = create_app()
    with app.app_context():
        registros = coletar_chamadas_fapesq()
        sinalizados = sum(1 for r in registros if r["possivel_nao_fomento"])
        print(
            f"Coletados {len(registros)} editais da FAPESQ {ANO} "
            f"({sinalizados} sinalizados como possível não-fomento)."
        )
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
