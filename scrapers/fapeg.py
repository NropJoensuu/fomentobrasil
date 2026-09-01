"""Scraper das chamadas com inscrições abertas da FAPEG (Goiás).

**Não usar o SparkX** (`sparkx.fapeg.go.gov.br`): é a plataforma proprietária "Stela",
que transmite comandos de construção de UI em vez de dados, depende de sessão e só
contém arquivo histórico (2018/2019). Inviável e inútil.

A fonte boa é o site institucional (WordPress + Elementor), e ele expõe **as duas coisas
de que precisamos**, cada uma resolvendo metade do problema:

1. `https://goias.gov.br/fapeg/editais/inscricoes-abertas/` — uma **tabela** curada pela
   própria FAPEG com exatamente as chamadas abertas (7 em 2026-08-26), com colunas
   Nº, Tipo, Origem, Descrição e Link. É o recorte que queremos: a categoria
   `/categoria/editais/` tem 297 posts misturando abertos e encerrados desde 2016.
2. `https://goias.gov.br/fapeg/wp-json/wp/v2/posts?slug=a,b,c` — a REST API do WordPress
   **funciona** e aceita vários slugs de uma vez, devolvendo em UMA requisição a data de
   publicação, o título limpo e o conteúdo de cada chamada.

Por isso o fluxo é híbrido: a tabela diz *quais* chamadas estão abertas, a API diz *os
dados* de cada uma. A tabela sozinha não serve (a data que aparece nela é a da página,
não de cada edital); a API sozinha também não (não sabe quais estão abertas).

O conteúdo do post é a linha de documentos, com as datas em DD/MM/AAAA:

    "FAQ – (17/08/2026) 2ª Retificação – (17/08/2026) 1ª Retificação – (12/08/2026)
     Edital – (03/08/2026)"

LIMITAÇÃO CONHECIDA: `data_prazo` não aparece em lugar nenhum da listagem nem do post —
está dentro do PDF do edital. Fica `None`, como em FAPES e FACEPE.
"""

import re
from datetime import datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

URL_INSCRICOES_ABERTAS = "https://goias.gov.br/fapeg/editais/inscricoes-abertas/"
API_POSTS = "https://goias.gov.br/fapeg/wp-json/wp/v2/posts"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

# Coluna "Nº" da tabela: "18/2026".
PADRAO_NUMERO = re.compile(r"^\s*(\d{1,3}/\d{4})\s*$")


def _limpar(texto):
    """Desfaz entidades HTML e normaliza espaços (a API devolve `&#8211;` etc.)."""
    if not texto:
        return ""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", texto))).strip()


def _texto_por_extenso_para_data(valor_iso):
    """Converte o campo `date` da API ("2026-08-12T08:55:50") em date."""
    if not valor_iso:
        return None
    try:
        return datetime.strptime(valor_iso[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _instituicoes_da_origem(origem):
    """Converte a coluna "Origem" numa lista de financiadoras.

    "Fapeg/CNPq/Capes" -> ["Fapeg", "CNPq", "Capes"]. A FAPEG sempre entra, mesmo quando
    a célula vem vazia (acontece: a linha 17/2026 não tem Origem preenchida) — é a
    agência que publica a chamada.
    """
    partes = [p.strip() for p in (origem or "").split("/") if p.strip()]
    if not partes:
        return ["FAPEG"]
    if not any(p.lower() == "fapeg" for p in partes):
        partes.insert(0, "FAPEG")
    return partes


def _ler_tabela_abertas(html):
    """Extrai as linhas da tabela de inscrições abertas."""
    soup = BeautifulSoup(html, "html.parser")
    artigo = soup.find("article")
    tabela = artigo.find("table") if artigo else None
    if tabela is None:
        return []

    linhas = []
    for tr in tabela.find_all("tr"):
        celulas = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(celulas) < 5:
            continue
        if not PADRAO_NUMERO.match(celulas[0]):
            continue  # cabeçalho ("Nº") e a linha vazia que separa o cabeçalho

        link_tag = tr.find("a", href=True)
        if not link_tag or "inscricoes-abertas" in link_tag["href"]:
            continue

        linhas.append(
            {
                "numero": celulas[0].strip(),
                "tipo": celulas[1].strip(),
                "origem": celulas[2].strip(),
                "descricao": celulas[3].strip(),
                "link": link_tag["href"],
            }
        )
    return linhas


def _buscar_posts_por_slug(slugs):
    """Busca os posts na REST API do WordPress. Devolve {slug: post}.

    Uma requisição só: a API aceita `?slug=a,b,c`. Se ela falhar, o scraper ainda
    funciona com o que veio da tabela — só sem data de publicação e sem os documentos.
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
        return {p.get("slug"): p for p in resp.json() if isinstance(p, dict)}
    except (requests.RequestException, ValueError):
        return {}


def coletar_chamadas_fapeg(html=None, posts=None):
    """Coleta as chamadas com inscrições abertas.

    `html`/`posts` permitem testar offline, sem bater na rede.
    """
    if html is None:
        resp = requests.get(
            URL_INSCRICOES_ABERTAS, timeout=45, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        html = resp.content

    linhas = _ler_tabela_abertas(html)
    if not linhas:
        # Devolver vazio é melhor que cair para /categoria/editais/: aquela página
        # mistura 297 editais desde 2016, e importar encerrados poluiria a curadoria
        # justamente com o que este scraper existe para evitar. Zero coletados é um
        # sinal visível no painel; dado errado em massa, não.
        return []

    slugs = [l["link"].rstrip("/").split("/")[-1] for l in linhas]
    if posts is None:
        posts = _buscar_posts_por_slug(slugs)

    resultados = []
    for linha, slug in zip(linhas, slugs):
        post = posts.get(slug) or {}

        titulo_api = _limpar((post.get("title") or {}).get("rendered", ""))
        # Sem a API, monta o título a partir da tabela — pior, mas melhor que nada.
        titulo = titulo_api or f"{linha['tipo']} nº {linha['numero']} – {linha['descricao']}"

        documentos = _limpar((post.get("content") or {}).get("rendered", ""))

        # A FAPEG retifica com muita frequência (vários editais têm 2ª e 3ª retificação).
        # Sinalizar isso poupa o curador de descobrir só ao abrir o PDF.
        retificada = "retifica" in documentos.lower()

        resultados.append(
            {
                "titulo": titulo,
                "link": linha["link"],
                "descricao": linha["descricao"] or None,
                "data_publicacao": _texto_por_extenso_para_data(post.get("date")),
                "numero_edital": linha["numero"],
                "tipo_fapeg": linha["tipo"],
                "documentos": documentos or None,
                "status_oficial": "retificada" if retificada else None,
                "instituicao_financiadora": _instituicoes_da_origem(linha["origem"]),
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
        dados_extra = {"numero_edital": r["numero_edital"], "tipo_fapeg": r["tipo_fapeg"]}
        if r["documentos"]:
            dados_extra["documentos"] = r["documentos"]

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
                # status_oficial É monitorado: uma retificação nova que apareça depois
                # da curadoria deve reabrir o registro para revisão.
                "status_oficial": r["status_oficial"],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": r["data_publicacao"],
                "data_prazo": None,  # só existe dentro do PDF do edital
                "instituicao_financiadora": [i[:200] for i in r["instituicao_financiadora"]],
                "instituicao_promotora": "FAPEG",
                "tipo_instrumento": "chamada_publica_edital",
                "tipo_parceria": detectar_tipo_parceria(r["titulo"]),
                "uf": ["GO"],
                "abrangencia": "estadual",
                # Placeholder: não é inferível do título. Corrigido na curadoria.
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
                "natureza_recurso": [],
                "proponente_elegivel": [],
                "origem": "institucional",
                "status": "pendente",
                "dados_extra": dados_extra,
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
        registros = coletar_chamadas_fapeg()
        print(f"Coletadas {len(registros)} chamadas com inscrições abertas da FAPEG.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
