"""Scraper dos editais em aberto da FAPEMA (Maranhão).

Usa a **REST API do WordPress**, não o HTML: a categoria `editais-em-aberto` devolve os 42
editais numa requisição só (`per_page=100`, `X-WP-TotalPages: 1`), com título, link, data
de publicação e resumo já estruturados. A página HTML equivalente tem 5 páginas.

Não usar a categoria `editais` (pai): são 43 páginas de histórico completo.

**Diferencial desta fonte:** os resumos trazem o período de submissão com datas reais, então
é a primeira FAP do Nordeste em que `data_prazo` sai da listagem, sem abrir o PDF. Em
2026-08-29: 26 dos 42 com prazo extraído.

EXTRAÇÃO DO PRAZO — o resumo tem formatos variados, todos cobertos pela regra "última data":

    "Período de Submissão online (até às 12h do último dia) 11/08/2026 a 31/08/2026"
    "Período de Submissão das Propostas 13/08/2026 a 09/09/2026 (até às 12h)"
    "– Inscrições até 11/06/2026"
    "Inscrições: 1ª chamada até 12h de 12/03/2026 2ª chamada ... 3ª chamada até 15/06/2026"

Num intervalo "X a Y" a última é o fim; em "até X" a última é a única; em várias chamadas
sucessivas a última é o encerramento final. Nos três casos a última data serve.

EXCEÇÃO — resumos que são CRONOGRAMA (ver PADRAO_CRONOGRAMA): aí a última data pode ser
de divulgação de resultado, não de submissão. Nesses o prazo fica `None` e o registro é
marcado com `dados_extra["prazo_requer_revisao"]`, em vez de arriscar um valor errado.
O texto cru fica sempre em `dados_extra["prazo_bruto"]` para o curador conferir.
"""

import re
from datetime import datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

API_BASE = "https://www.fapema.br/wp-json/wp/v2"
SLUG_CATEGORIA = "editais-em-aberto"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

PADRAO_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

# Marcadores de TABELA de cronograma. Deliberadamente NÃO inclui "divulgação do resultado":
# esse trecho aparece logo depois de um período de submissão válido em vários resumos
# ("Período de submissão online 12/06/2025 a 18/07/2025 ... Divulgação do Resultado"), e
# incluí-lo descartaria prazos que a regra da última data acerta.
PADRAO_CRONOGRAMA = re.compile(
    r"cronograma|atividades\s+datas?|publica[çc][ãa]o\s+do\s+edital|impugna[çc][ãa]o",
    re.IGNORECASE,
)

PADRAO_PREMIO = re.compile(r"pr[êe]mio|concurso", re.IGNORECASE)


def _limpar(html_texto):
    """Remove tags e desfaz entidades HTML, normalizando espaços."""
    if not html_texto:
        return ""
    texto = BeautifulSoup(html_texto, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", unescape(texto)).strip()


def extrair_data_prazo(resumo):
    """Devolve (data_prazo, requer_revisao) a partir do resumo.

    `requer_revisao=True` quando o resumo é um cronograma: há datas, mas não dá para
    saber qual é a de submissão sem ler o edital.
    """
    if not resumo:
        return None, False
    if PADRAO_CRONOGRAMA.search(resumo):
        return None, True

    encontradas = PADRAO_DATA.findall(resumo)
    if not encontradas:
        return None, False

    dia, mes, ano = encontradas[-1]
    try:
        return datetime(int(ano), int(mes), int(dia)).date(), False
    except ValueError:
        return None, False


def _tipo_instrumento(titulo, resumo):
    return "premio" if PADRAO_PREMIO.search(f"{titulo} {resumo}") else "chamada_publica_edital"


def coletar_chamadas_fapema(categorias=None, posts=None):
    """Coleta os editais em aberto. `categorias`/`posts` permitem teste offline."""
    cabecalhos = {"User-Agent": USER_AGENT}

    if categorias is None:
        resp = requests.get(
            f"{API_BASE}/categories", params={"per_page": 100}, timeout=45, headers=cabecalhos
        )
        resp.raise_for_status()
        categorias = resp.json()

    # Descoberto pelo slug, não fixado: ids do WordPress mudam se a categoria for recriada.
    id_categoria = next(
        (c["id"] for c in categorias if isinstance(c, dict) and c.get("slug") == SLUG_CATEGORIA),
        None,
    )
    if id_categoria is None:
        return []

    if posts is None:
        resp = requests.get(
            f"{API_BASE}/posts",
            params={"categories": id_categoria, "per_page": 100},
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

        resumo = _limpar((post.get("excerpt") or {}).get("rendered", ""))
        data_prazo, requer_revisao = extrair_data_prazo(resumo)

        try:
            data_publicacao = (
                datetime.strptime(post["date"][:10], "%Y-%m-%d").date()
                if post.get("date")
                else None
            )
        except (ValueError, KeyError):
            data_publicacao = None

        resultados.append(
            {
                "titulo": titulo,
                "link": link,
                "descricao": resumo or None,
                "data_publicacao": data_publicacao,
                "data_prazo": data_prazo,
                "prazo_requer_revisao": requer_revisao,
                "prazo_bruto": resumo or None,
                "tipo_instrumento": _tipo_instrumento(titulo, resumo),
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
        if r["prazo_bruto"]:
            # Sempre guardado: a variedade de formatos torna a extração best-effort,
            # e o curador precisa do texto original para conferir.
            dados_extra["prazo_bruto"] = r["prazo_bruto"]
        if r["prazo_requer_revisao"]:
            dados_extra["prazo_requer_revisao"] = True

        # Prêmio não é tipo_instrumento (é o que está sendo oferecido, não o
        # procedimento) — vira linha_de_fomento própria, e o instrumento que veicula
        # o prêmio é um edital como qualquer outro.
        e_premio = r["tipo_instrumento"] == "premio"

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
                # data_prazo É monitorado aqui (diferente de FAPES/FACEPE/FAPEG, onde
                # nunca vem da listagem): se a FAPEMA prorrogar um edital já curado,
                # a mudança reabre o registro para revisão.
                "data_prazo": r["data_prazo"],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": r["data_publicacao"],
                "instituicao_financiadora": ["FAPEMA"],
                "instituicao_promotora": "FAPEMA",
                "tipo_instrumento": "chamada_publica_edital" if e_premio else r["tipo_instrumento"],
                "tipo_parceria": detectar_tipo_parceria(r["titulo"], r["descricao"]),
                "uf": ["MA"],
                "abrangencia": "estadual",
                # Placeholder, exceto quando o título já indica prêmio de propósito.
                # Corrigido na curadoria quando o placeholder não se aplicar.
                "linha_de_fomento": ["premiacao"] if e_premio else ["apoio_formacao_capacitacao"],
                "natureza_recurso": [],
                "proponente_elegivel": [],
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
        registros = coletar_chamadas_fapema()
        com_prazo = sum(1 for r in registros if r["data_prazo"])
        revisar = sum(1 for r in registros if r["prazo_requer_revisao"])
        print(
            f"Coletados {len(registros)} editais em aberto da FAPEMA "
            f"({com_prazo} com prazo, {revisar} com cronograma a revisar)."
        )
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
