"""Scraper das chamadas da FAPESPA (Pará).

WordPress padrão, e a **REST API funciona** — então não é preciso parsear o HTML da
listagem (onde a data vem quebrada em duas linhas, "25 ago" / "2026"). A API entrega
`date` em ISO, título, link e excerpt já estruturados.

DUAS DESCOBERTAS QUE MUDAM A EXPECTATIVA DE VOLUME (verificadas em 2026-08-29):

1. **A categoria `editais` está vazia.** `/wp-json/wp/v2/categories` reporta `count: 27`,
   mas `/wp-json/wp/v2/posts?categories=60` devolve `X-WP-Total: 0`, e a página
   `/category/editais/` mostra "Nothing Found". O `count` é metadado desatualizado do
   WordPress — e não é caso isolado neste site: `publicacoes` diz 64, `sustentabilidade`
   diz 5, `videos` diz 4, e **todas devolvem 0 posts**. A única categoria com conteúdo real
   é `chamadas` (11).
2. Por isso o total é **11**, não os ~38 que a soma dos counts sugere.

O scraper consulta as duas categorias mesmo assim: se a FAPESPA repovoar `editais`, os
posts entram sozinhos, sem precisar mexer no código.

**Uma categoria por requisição, de propósito.** A sintaxe `?categories=134,60` numa
requisição só é tentadora, mas não deu para confirmar que ela faz união neste WordPress
(devolveu 11, que é exatamente o total da primeira categoria — indistinguível de "ignorou a
segunda", já que a segunda está vazia). Consultar separadamente e unir é inequívoco.

A distinção editorial entre "chamada" e "edital" não é semântica: a categoria `chamadas`
inclui um "Edital nº 002/2026 Programa Centelha". Ambas são oportunidades de fomento.

LIMITAÇÃO: `data_prazo` não aparece na listagem, e a fonte não separa abertos de
encerrados — o cálculo de aberto/encerrado fica para a curadoria. O conteúdo é recente
(todos de 2026), então o risco de histórico antigo é baixo.
"""

import re
from datetime import datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

API_BASE = "https://www.fapespa.pa.gov.br/wp-json/wp/v2"
SLUGS_CATEGORIAS = ("chamadas", "editais")

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"


def _limpar_excerpt(html_excerpt):
    """Texto do resumo, sem o link "Continue reading" que o WordPress anexa.

    O excerpt vem como `<p>texto...<a class="button">Continue reading <span>TÍTULO</span></a></p>`.
    Remover o `<a>` antes de extrair o texto é mais limpo que tentar cortar a string
    depois — o rótulo muda com o idioma e o título entra no meio dele.
    """
    if not html_excerpt:
        return None
    sopa = BeautifulSoup(html_excerpt, "html.parser")
    for link in sopa.find_all("a"):
        link.decompose()
    texto = re.sub(r"\s+", " ", unescape(sopa.get_text(" ", strip=True))).strip()
    # Sobra um "…"/"..." solto onde o WordPress cortou a frase.
    texto = re.sub(r"\s*(?:…|\.\.\.)\s*$", "", texto)
    return texto or None


def _limpar_titulo(html_titulo):
    if not html_titulo:
        return ""
    texto = BeautifulSoup(html_titulo, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", unescape(texto)).strip()


def _buscar_posts_da_categoria(id_categoria, cabecalhos):
    """Todos os posts de uma categoria, seguindo a paginação da API."""
    posts = []
    pagina = 1
    while pagina <= 20:  # trava contra paginação infinita
        resp = requests.get(
            f"{API_BASE}/posts",
            params={"categories": id_categoria, "per_page": 100, "page": pagina},
            timeout=45,
            headers=cabecalhos,
        )
        if resp.status_code == 400:
            break  # o WP devolve 400 quando a página passa do total
        resp.raise_for_status()
        lote = resp.json()
        if not lote:
            break
        posts.extend(lote)
        total_paginas = int(resp.headers.get("X-WP-TotalPages", 1) or 1)
        if pagina >= total_paginas:
            break
        pagina += 1
    return posts


def coletar_chamadas_fapespa(categorias=None, posts_por_categoria=None):
    """Coleta as chamadas das categorias configuradas.

    `categorias`/`posts_por_categoria` permitem testar offline, sem bater na rede.
    """
    cabecalhos = {"User-Agent": USER_AGENT}

    if categorias is None:
        resp = requests.get(
            f"{API_BASE}/categories", params={"per_page": 100}, timeout=45, headers=cabecalhos
        )
        resp.raise_for_status()
        categorias = resp.json()

    ids = [
        c["id"]
        for c in categorias
        if isinstance(c, dict) and c.get("slug") in SLUGS_CATEGORIAS
    ]
    if not ids:
        return []

    vistos = set()
    resultados = []
    for id_categoria in ids:
        if posts_por_categoria is not None:
            posts = posts_por_categoria.get(id_categoria, [])
        else:
            posts = _buscar_posts_da_categoria(id_categoria, cabecalhos)

        for post in posts:
            if not isinstance(post, dict):
                continue
            titulo = _limpar_titulo((post.get("title") or {}).get("rendered", ""))
            link = post.get("link")
            if not titulo or not link or link in vistos:
                continue
            vistos.add(link)

            try:
                data_publicacao = (
                    datetime.strptime(post["date"][:10], "%Y-%m-%d").date()
                    if post.get("date")
                    else None
                )
            except (ValueError, KeyError):
                data_publicacao = None

            descricao = _limpar_excerpt((post.get("excerpt") or {}).get("rendered", ""))
            resultados.append(
                {
                    "titulo": titulo,
                    "link": link,
                    "descricao": descricao,
                    "data_publicacao": data_publicacao,
                    "tipo_parceria": detectar_tipo_parceria(titulo, descricao),
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
                "data_prazo": None,  # não disponível na listagem
                # Chamadas conjuntas (ex.: 009/2026 FAPESPA/CNPq/CAPES) trazem os
                # parceiros no título, mas extraí-los seria chute — o curador completa
                # a lista, que é ARRAY justamente para isso.
                "instituicao_financiadora": ["FAPESPA"],
                "tipo_instrumento": "chamada_publica_edital",
                "tipo_parceria": r["tipo_parceria"],
                "uf": ["PA"],
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
        registros = coletar_chamadas_fapespa()
        print(f"Coletadas {len(registros)} chamadas da FAPESPA.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
