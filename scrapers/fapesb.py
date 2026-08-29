"""Scraper dos editais abertos da FAPESB (Bahia).

`https://www.fapesb.ba.gov.br/category/edital/` renderiza **as três abas no mesmo HTML**:
`#tab1` (Abertos), `#tab2` (Fechados) e `#tab3` (Resultados). A troca é só CSS/JS, então
quem raspar a página inteira leva encerrados e resultados junto. O parser lê **somente
`#tab1`** — em 2026-08-29: 15 itens em tab1, tab2 vazia, 3 em tab3.

Cada item é um `div.edital-item` com:

    <ul class="edital-top-bar"><li>LANÇAMENTO: </li>
                               <li>DIVULGAÇÃO DOS RESULTADOS: </li></ul>
    <div class="edital-title"><h3><p><a href="...">TÍTULO -</a></p></h3></div>
    <p>Propósito: ...</p>                     <!-- descrição, boa -->

SOBRE OS RÓTULOS DE DATA VAZIOS: investigado em 2026-08-29 e são **genuinamente vazios no
site** — os `<li>` são literalmente `<li>LANÇAMENTO: </li>`, sem atributo `data-*`, e
nenhum `<script>` da página menciona `edital-top-bar`, `LANÇAMENTO` ou `DIVULGAÇÃO`. Não é
conteúdo carregado por JavaScript; a FAPESB simplesmente não preenche.

A data foi recuperada por outro caminho: cada item aponta para um post do WordPress, e a
REST API devolve os 15 de uma vez por slug (`?slug=a,b,c`), com o campo `date`. Por isso o
scraper é híbrido — `#tab1` diz *quais* estão abertos e traz a descrição, a API dá a data.
`data_resultado_previsto` continua `None`: essa não tem fonte nenhuma.
"""

import re
from datetime import datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import processar_registro

URL_EDITAIS = "https://www.fapesb.ba.gov.br/category/edital/"
API_POSTS = "https://www.fapesb.ba.gov.br/wp-json/wp/v2/posts"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

PADRAO_INTERNACIONAL = re.compile(
    r"confap|erc\b|msca|cdti|daad|gcub|brasillinois|ramp\b|mobility|internacional",
    re.IGNORECASE,
)


def _limpar(texto):
    return re.sub(r"\s+", " ", unescape(texto or "")).strip()


def _tipo_instrumento(titulo):
    return "premio" if "prêmio" in titulo.lower() or "premio" in titulo.lower() else "chamada_publica_edital"


def _ler_tab1(html):
    """Extrai os itens da aba de abertos. Ignora #tab2 e #tab3 de propósito."""
    soup = BeautifulSoup(html, "html.parser")
    aba = soup.find(id="tab1")
    if aba is None:
        return []

    itens = []
    for bloco in aba.find_all("div", class_="edital-item"):
        link_tag = bloco.select_one(".edital-title a[href]")
        if not link_tag:
            continue

        # Todos os títulos terminam com " -" no site; remover para não poluir a listagem.
        titulo = re.sub(r"\s*-\s*$", "", _limpar(link_tag.get_text(" ", strip=True)))
        if not titulo:
            continue

        # A descrição é o primeiro <p> fora do bloco do título.
        descricao = None
        for paragrafo in bloco.find_all("p"):
            if paragrafo.find_parent(class_="edital-title"):
                continue
            texto = _limpar(paragrafo.get_text(" ", strip=True))
            if texto:
                descricao = texto
                break

        itens.append({"titulo": titulo, "link": link_tag["href"], "descricao": descricao})
    return itens


def _buscar_datas_por_slug(slugs):
    """Devolve {slug: 'AAAA-MM-DD'} usando a REST API. Uma requisição só.

    Falha silenciosa de propósito: sem a API o scraper ainda entrega os editais e as
    descrições, só sem data de publicação.
    """
    if not slugs:
        return {}
    try:
        resp = requests.get(
            API_POSTS,
            params={"slug": ",".join(slugs), "per_page": 100},
            timeout=45,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        return {
            p.get("slug"): (p.get("date") or "")[:10]
            for p in resp.json()
            if isinstance(p, dict) and p.get("slug")
        }
    except (requests.RequestException, ValueError):
        return {}


def coletar_chamadas_fapesb(html=None, datas_por_slug=None):
    """Coleta os editais abertos (#tab1). `html`/`datas_por_slug` permitem teste offline."""
    if html is None:
        resp = requests.get(URL_EDITAIS, timeout=45, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        html = resp.content

    itens = _ler_tab1(html)
    if not itens:
        return []

    slugs = [i["link"].rstrip("/").split("/")[-1] for i in itens]
    if datas_por_slug is None:
        datas_por_slug = _buscar_datas_por_slug(slugs)

    resultados = []
    for item, slug in zip(itens, slugs):
        data_texto = datas_por_slug.get(slug)
        try:
            data_publicacao = (
                datetime.strptime(data_texto, "%Y-%m-%d").date() if data_texto else None
            )
        except ValueError:
            data_publicacao = None

        resultados.append(
            {
                "titulo": item["titulo"],
                "link": item["link"],
                "descricao": item["descricao"],
                "data_publicacao": data_publicacao,
                "tipo_parceria": (
                    "internacional" if PADRAO_INTERNACIONAL.search(item["titulo"]) else None
                ),
                "tipo_instrumento": _tipo_instrumento(item["titulo"]),
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
        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": r["data_publicacao"],
                "data_prazo": None,  # não aparece na listagem
                "data_resultado_previsto": None,  # rótulo existe no site, mas vem vazio
                "instituicao_financiadora": ["FAPESB"],
                "tipo_instrumento": r["tipo_instrumento"],
                "tipo_parceria": r["tipo_parceria"],
                "uf": ["BA"],
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
        registros = coletar_chamadas_fapesb()
        print(f"Coletados {len(registros)} editais abertos da FAPESB.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
