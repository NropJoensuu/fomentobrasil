"""Scraper das chamadas abertas da FAPEAL (Alagoas).

Usa a **REST API do WordPress**, não a página `/pesquisador/editais/`: aquela exibe
carrosséis por categoria com sobreposição (a mesma chamada aparece em "Chamadas Abertas",
"Chamadas Internacionais" e "Editais Vigentes"), e traz só título e link.

A API resolve os dois problemas de uma vez — a categoria `chamadas-abertas` dá o conjunto
sem repetição, e cada post vem com data, link e as categorias já classificadas:

    GET /wp-json/wp/v2/categories?slug=chamadas-abertas   -> descobre o id
    GET /wp-json/wp/v2/posts?categories=<id>&per_page=50

O id é descoberto pelo slug em vez de fixado no código (era 61 em 2026-08-29): ids de
categoria do WordPress mudam se alguém recriar a categoria.

SOBRE `descricao`: fica `None` de propósito. O `excerpt`/`content` desses posts **não é
descrição** — é a lista de nomes dos documentos anexos ("Chamada", "Chamada original
Diretrizes da Fapeal", "RESULTADO FINAL ... EDITAL Anexo I"). Gravar isso como descrição
colocaria a palavra "Chamada" como resumo público da oportunidade. O texto vai para
`dados_extra["documentos"]`, onde serve de referência para a curadoria.
"""

import re
from html import unescape

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import processar_registro

API_BASE = "https://www.fapeal.br/wp-json/wp/v2"
SLUG_CATEGORIA = "chamadas-abertas"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

# Cooperação internacional pelo título. Não basta sozinho: a chamada ERC se chama
# "Mobilidade de pesquisadores, para a Europa", sem nenhuma destas palavras — por isso
# as categorias do post também são consultadas (ver CATEGORIAS_INTERNACIONAIS).
PADRAO_INTERNACIONAL = re.compile(
    r"confap|daad|erc\b|msca|mobility|internacional|europa", re.IGNORECASE
)

# Slugs de categoria que indicam cooperação internacional na FAPEAL.
CATEGORIAS_INTERNACIONAIS = {"internacional", "internacional-internacional", "internacional-2", "confap"}


def _limpar(html_texto):
    """Remove tags e desfaz entidades HTML, normalizando espaços."""
    if not html_texto:
        return ""
    texto = BeautifulSoup(html_texto, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", unescape(texto)).strip()


def _tipo_instrumento(titulo):
    return "premio" if "prêmio" in titulo.lower() or "premio" in titulo.lower() else "chamada_publica_edital"


def coletar_chamadas_fapeal(categorias=None, posts=None):
    """Coleta as chamadas abertas via API. `categorias`/`posts` permitem teste offline."""
    cabecalhos = {"User-Agent": USER_AGENT}

    if categorias is None:
        resp = requests.get(
            f"{API_BASE}/categories", params={"per_page": 100}, timeout=45, headers=cabecalhos
        )
        resp.raise_for_status()
        categorias = resp.json()

    por_id = {c["id"]: c.get("slug", "") for c in categorias if isinstance(c, dict)}
    id_abertas = next(
        (c["id"] for c in categorias if isinstance(c, dict) and c.get("slug") == SLUG_CATEGORIA),
        None,
    )
    if id_abertas is None:
        return []

    if posts is None:
        resp = requests.get(
            f"{API_BASE}/posts",
            params={"categories": id_abertas, "per_page": 50},
            timeout=45,
            headers=cabecalhos,
        )
        resp.raise_for_status()
        posts = resp.json()

    resultados = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        titulo = _limpar((post.get("title") or {}).get("rendered", ""))
        link = post.get("link")
        if not titulo or not link:
            continue

        slugs_do_post = {por_id.get(i, "") for i in (post.get("categories") or [])}
        internacional = bool(slugs_do_post & CATEGORIAS_INTERNACIONAIS) or bool(
            PADRAO_INTERNACIONAL.search(titulo)
        )

        resultados.append(
            {
                "titulo": titulo,
                "link": link,
                "data_publicacao": (post.get("date") or "")[:10] or None,
                "documentos": _limpar((post.get("excerpt") or {}).get("rendered", "")) or None,
                "tipo_parceria": "internacional" if internacional else None,
                "tipo_instrumento": _tipo_instrumento(titulo),
            }
        )

    return resultados


def salvar_no_banco(registros):
    """Insere registros novos, atualiza existentes se um campo monitorado mudou
    (ver app.scraper_utils), ou ignora quando nada mudou. Dedup/match por link."""
    from datetime import datetime

    novos = 0
    atualizados = 0
    ja_existentes = 0

    for r in registros:
        try:
            data_publicacao = (
                datetime.strptime(r["data_publicacao"], "%Y-%m-%d").date()
                if r["data_publicacao"]
                else None
            )
        except ValueError:
            data_publicacao = None

        dados_extra = {}
        if r["documentos"]:
            # Não é descrição — é a lista de anexos do post. Ver docstring do módulo.
            dados_extra["documentos"] = r["documentos"]

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
            },
            campos_extras_fixos={
                "descricao": None,  # a fonte não publica descrição (ver docstring)
                "data_publicacao": data_publicacao,
                "data_prazo": None,  # não disponível na listagem
                "instituicao_financiadora": ["FAPEAL"],
                "tipo_instrumento": r["tipo_instrumento"],
                "tipo_parceria": r["tipo_parceria"],
                "uf": ["AL"],
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
        registros = coletar_chamadas_fapeal()
        print(f"Coletadas {len(registros)} chamadas abertas da FAPEAL.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
