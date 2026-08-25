"""Scraper das chamadas abertas da FAPESC (Santa Catarina), via API REST do WordPress.

Diferente do que a URL `/chamadas-abertas/` sugere à primeira vista (um parser de HTML
seria necessário), o site é WordPress puro e expõe a API REST padrão
(`wp-json/wp/v2/posts`), filtrável por categoria — sem precisar de parsing de HTML.
Confirmado em 2026-08-10 via `curl .../wp-json/wp/v2/posts?per_page=5`. A categoria
"Chamadas abertas" tem id=32 (descoberta via `/wp-json/wp/v2/categories`), com
exatamente 15 chamadas — batendo com a contagem vista manualmente.

    GET https://fapesc.sc.gov.br/wp-json/wp/v2/posts?categories=32&per_page=100

O campo `content.rendered` (HTML) traz um texto corrido com um padrão consistente:
descrição, depois "Prazo para submissão: DD/MM/AAAA a DD/MM/AAAA" (ou, em editais de
fluxo contínuo, "Fluxo contínuo até DD/MM/AAAA"), depois "Contato para dúvidas: ..." e
por fim links de rodapé ("ACESSE O EDITAL COMPLETO" etc). A descrição é tudo antes do
primeiro desses dois marcadores; o prazo é extraído do trecho entre eles. Quando o
trecho tem duas datas, a primeira é o início da submissão (vai para
`dados_extra.inscricao_inicio`, mesmo padrão do scraper do CNPq) e a última é
`data_prazo`; quando só tem uma (ex.: "Fluxo contínuo até..."), só o fim é conhecido.

Um edital sem o marcador "Prazo para submissão" (ex.: "Programa Inova Talentos... em
Fluxo Contínuo", que só lista datas de "RESULTADO" periódicas) fica com
`data_prazo=None` de propósito — não há prazo fixo a extrair, e chutar um a partir das
datas de resultado seria errado.

`title.rendered` vem com entidades HTML (ex.: `&#8211;`) mas sem tags — por isso usa
`html.unescape`, diferente de `content.rendered`, que tem marcação real e passa por
BeautifulSoup.
"""

import html
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import processar_registro

API_BASE = "https://fapesc.sc.gov.br/wp-json/wp/v2/posts"

# Descoberta via GET /wp-json/wp/v2/categories — "Chamadas abertas".
CATEGORIA_CHAMADAS_ABERTAS = 32

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

PADRAO_DATA = re.compile(r"(\d{2}/\d{2}/\d{4})")

# Onde a descrição corrida termina: no início do trecho de prazo, ou (quando não há
# prazo declarado) no início do contato.
PADRAO_MARCADOR_CORTE = re.compile(
    r"Prazo\s+para\s+submiss[ãa]o|Contato\s+para\s+d[úu]vidas", re.IGNORECASE
)

PADRAO_PRAZO = re.compile(
    r"Prazo\s+para\s+submiss[ãa]o\s*:?\s*(.*?)(?=Contato\s+para\s+d[úu]vidas|$)",
    re.IGNORECASE | re.DOTALL,
)

# Trava de segurança: a categoria tem 15 itens hoje (1 página de 100), mas isso evita
# laço infinito caso a API pagine de forma inesperada no futuro.
MAX_PAGINAS = 20


def limpar_html(texto):
    """Remove tags HTML de um campo de texto rico, retornando texto puro."""
    if not texto:
        return None
    return BeautifulSoup(texto, "html.parser").get_text(" ", strip=True) or None


def extrair_descricao_e_prazo(texto_completo):
    """Separa a descrição do trecho de prazo.

    Devolve (descricao, data_prazo, inscricao_inicio).
    """
    corte = PADRAO_MARCADOR_CORTE.search(texto_completo)
    descricao = (texto_completo[: corte.start()] if corte else texto_completo).strip() or None

    match_prazo = PADRAO_PRAZO.search(texto_completo)
    if not match_prazo:
        return descricao, None, None

    datas = PADRAO_DATA.findall(match_prazo.group(1))
    if not datas:
        return descricao, None, None

    data_prazo = datetime.strptime(datas[-1], "%d/%m/%Y").date()
    inscricao_inicio = (
        datetime.strptime(datas[0], "%d/%m/%Y").date() if len(datas) >= 2 else None
    )
    return descricao, data_prazo, inscricao_inicio


def coletar_chamadas_fapesc():
    """Percorre a categoria "Chamadas abertas" via API e devolve dicts prontos para inserção."""
    resultados = []
    page = 1

    while page <= MAX_PAGINAS:
        resp = requests.get(
            API_BASE,
            params={"categories": CATEGORIA_CHAMADAS_ABERTAS, "per_page": 100, "page": page},
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code == 400 and page > 1:
            # WP REST devolve 400 (rest_post_invalid_page_number) ao passar da última página.
            break
        resp.raise_for_status()

        itens = resp.json()
        if not itens:
            break

        for item in itens:
            link = item.get("link")
            titulo = html.unescape((item.get("title") or {}).get("rendered", "")).strip()
            if not link or not titulo:
                continue

            texto_completo = limpar_html((item.get("content") or {}).get("rendered")) or ""
            descricao, data_prazo, inscricao_inicio = extrair_descricao_e_prazo(texto_completo)

            data_publicacao = None
            data_raw = item.get("date")
            if data_raw:
                try:
                    data_publicacao = datetime.fromisoformat(data_raw).date()
                except ValueError:
                    data_publicacao = None

            dados_extra = {}
            if inscricao_inicio:
                dados_extra["inscricao_inicio"] = inscricao_inicio.isoformat()

            resultados.append(
                {
                    "titulo": titulo,
                    "link": link,
                    "descricao": descricao,
                    "data_publicacao": data_publicacao,
                    "data_prazo": data_prazo,
                    "dados_extra": dados_extra or None,
                }
            )

        total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1

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
                "data_prazo": r["data_prazo"],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": r["data_publicacao"],
                "instituicao_financiadora": "FAPESC",
                "tipo_instrumento": "chamada_publica_edital",
                # Placeholder: não é inferível do título/descrição. Ver docs — sempre revisar.
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
                # NOT NULL no schema, mas não extraíveis com confiança da listagem/
                # conteúdo: lista vazia significa "ainda não determinado", em vez de
                # um chute.
                "natureza_recurso": [],
                "publico_alvo": [],
                "uf": "SC",
                "abrangencia": "estadual",
                "origem": "institucional",
                "status": "pendente",
                "dados_extra": r["dados_extra"],
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
        registros = coletar_chamadas_fapesc()
        print(f"Coletadas {len(registros)} chamadas.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
