"""Scraper dos editais da FAPEPI (Piauí).

WordPress + Elementor com post type customizado do plugin Pods. A **API não serve**: o
post type `edital` não é exposto na REST API — não aparece em `/wp-json/wp/v2/types` e
`/wp-json/wp/v2/edital` devolve 404 `rest_no_route`. Parsing de HTML, então, e por isso
`data_publicacao` fica `None` (a listagem não mostra data em lugar nenhum).

Estrutura de cada item em `https://www.fapepi.pi.gov.br/editais/`:

    <article class="post">
      <h2 class="entry-title"><a href="/edital/slug/">TÍTULO</a></h2>
      <p>O Governo do Estado do Piauí, por meio da Fundação ... […]</p>
    </article>

FILTRO POR ANO — a fonte **não separa abertos de encerrados** e a paginação vai fundo no
histórico (2022 já aparece na página 3). Como não há status para filtrar, o corte é pelo
ano no título: só entram os do ano corrente, calculado com `datetime.now().year` para não
exigir edição do código todo janeiro.

Consequência assumida: chamadas de anos anteriores que continuem abertas ficam de fora
(ex.: "Chamada Pública nº 07/2025 – NIT"). É o preço de a fonte não ter filtro de status, e
é preferível a inundar a curadoria com anos de histórico encerrado.

A lista vem do mais recente para o mais antigo, então a paginação para na primeira página
sem nenhum item do ano corrente. Em 2026-08-29: 9 itens de 2026, todos na primeira página.
"""

import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

URL_EDITAIS = "https://www.fapepi.pi.gov.br/editais/"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

MAX_PAGINAS = 20  # trava contra laço infinito se a paginação passar a repetir a página

# O WordPress corta o resumo com "[…]"; sem isso a descrição termina com um símbolo solto.
PADRAO_RETICENCIAS = re.compile(r"\s*\[(?:…|\.\.\.)\]\s*$")


def _url_pagina(numero):
    return URL_EDITAIS if numero == 1 else f"{URL_EDITAIS}page/{numero}/"


def _e_do_ano(titulo, ano):
    """True se o título indicar o ano corrente.

    Aceita "/2026" (formato normal: "EDITAL Nº 004/2026") e "2026/" (para casos como
    "Chamada 2026/2027", que a primeira forma não pegaria).
    """
    return bool(re.search(rf"(?:/{ano}\b|\b{ano}/)", titulo))


def _tipo_instrumento(titulo):
    maiusculo = titulo.upper()
    if "PRÊMIO" in maiusculo or "PREMIO" in maiusculo:
        return "premio"
    if "CHAMAMENTO PÚBLICO" in maiusculo or "CHAMAMENTO PUBLICO" in maiusculo:
        return "chamamento_publico"
    return "chamada_publica_edital"


def _extrair_pagina(html, ano):
    """Extrai os itens do ano corrente de uma página.

    Devolve (itens_do_ano, total_de_itens): o total serve para a paginação saber se a
    página tinha conteúdo mas nenhum do ano, ou se acabou a lista.
    """
    soup = BeautifulSoup(html, "html.parser")
    itens = []
    total = 0

    for article in soup.find_all("article"):
        link_tag = article.select_one("h2.entry-title a[href]")
        if not link_tag:
            continue
        titulo = re.sub(r"\s+", " ", link_tag.get_text(" ", strip=True)).strip()
        if not titulo:
            continue
        total += 1

        if not _e_do_ano(titulo, ano):
            continue

        paragrafo = article.find("p")
        descricao = None
        if paragrafo:
            texto = re.sub(r"\s+", " ", paragrafo.get_text(" ", strip=True)).strip()
            descricao = PADRAO_RETICENCIAS.sub("", texto) or None

        itens.append(
            {
                "titulo": titulo,
                "link": urljoin(URL_EDITAIS, link_tag["href"]),
                "descricao": descricao,
                "tipo_instrumento": _tipo_instrumento(titulo),
                "tipo_parceria": detectar_tipo_parceria(titulo, descricao),
            }
        )

    return itens, total


def coletar_chamadas_fapepi(ano=None, paginas_html=None):
    """Coleta os editais do ano corrente.

    `ano`/`paginas_html` permitem testar offline, sem bater na rede.
    """
    ano = ano or datetime.now().year

    if paginas_html is not None:
        resultados = []
        for html in paginas_html:
            itens, _ = _extrair_pagina(html, ano)
            resultados.extend(itens)
        return resultados

    resultados = []
    for numero in range(1, MAX_PAGINAS + 1):
        resp = requests.get(
            _url_pagina(numero), timeout=45, headers={"User-Agent": USER_AGENT}
        )
        if resp.status_code == 404:
            break
        resp.raise_for_status()

        itens, total = _extrair_pagina(resp.content, ano)
        if total == 0:
            break  # acabou a lista
        if not itens:
            # A lista é ordenada do mais recente para o mais antigo: uma página inteira
            # sem item do ano corrente significa que já passamos do ponto.
            break
        resultados.extend(itens)

    return resultados


def salvar_no_banco(registros):
    """Insere registros novos, atualiza existentes se um campo monitorado mudou
    (ver app.scraper_utils), ou ignora quando nada mudou. Dedup/match por link."""
    novos = 0
    atualizados = 0
    ja_existentes = 0

    for r in registros:
        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": None,  # a listagem não mostra data em lugar nenhum
                "data_prazo": None,  # só existe dentro do edital
                "instituicao_financiadora": ["FAPEPI"],
                "tipo_instrumento": r["tipo_instrumento"],
                "tipo_parceria": r["tipo_parceria"],
                "uf": ["PI"],
                "abrangencia": "estadual",
                # Placeholder: não é inferível do título. Corrigido na curadoria.
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
                "natureza_recurso": [],
                "publico_alvo": [],
                "origem": "institucional",
                "status": "pendente",
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
        ano = datetime.now().year
        registros = coletar_chamadas_fapepi()
        print(f"Coletados {len(registros)} editais da FAPEPI de {ano}.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
