"""Scraper dos editais abertos da FAPITEC (Sergipe).

WordPress + Elementor, server-rendered. A REST API **não serve**: `/wp-json/wp/v2/posts`
devolve HTTP 401 `rest_not_logged_in` (a API está restrita a usuários autenticados).

A página `/editais-abertos/` é dedicada aos abertos, então não há nada a filtrar. Cada
edital é um `<li class="wp-block-post">` com o link do título e um `<time>`:

    <li class="wp-block-post post-12446 editais-abertos ...">
      <a href="...">EDITAL FAPITEC/SE/FUNTEC Nº 11/2026 – ...</a>
      <time datetime="2026-08-21T11:25:52-03:00">21 de agosto de 2026</time>
    </li>

Ancorar em `li.wp-block-post` importa: a página tem um "Última atualização: 3 de julho de
..." no rodapé que casaria com uma busca solta por data por extenso.

A data sai do atributo `datetime` (ISO), não do texto em português — mais confiável que
mapear nome de mês. O texto fica como plano B, caso o atributo desapareça.

Volume baixo é esperado: 3 editais abertos em 2026-08-29. É uma FAP pequena, não é sinal
de scraper quebrado.

Outras páginas do site, se um dia agregarem: `/editais-em-andamento/`,
`/editais-encerrados/` e `/editais-e-ou-chamadas-de-instituicoes-parceiras/` — esta última
pode trazer chamadas CONFAP que já vêm de outras FAPs, então precisa de análise de
duplicação antes.

LIMITAÇÃO: `descricao` e `data_prazo` não aparecem na listagem.
"""

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import (detectar_possivel_nao_fomento, detectar_tipo_parceria,
                               processar_registro)

URL_EDITAIS_ABERTOS = "https://fapitec.se.gov.br/editais-abertos/"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}
PADRAO_DATA_EXTENSO = re.compile(r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", re.IGNORECASE)


def _data_do_time(time_tag):
    """Extrai a data do <time>, preferindo o atributo `datetime` (ISO) ao texto."""
    if time_tag is None:
        return None

    iso = time_tag.get("datetime")
    if iso:
        try:
            return datetime.strptime(iso[:10], "%Y-%m-%d").date()
        except ValueError:
            pass  # cai para o texto por extenso

    match = PADRAO_DATA_EXTENSO.search(time_tag.get_text(" ", strip=True))
    if not match:
        return None
    dia, mes_nome, ano = match.groups()
    mes = MESES.get(mes_nome.lower())
    if not mes:
        return None
    try:
        return datetime(int(ano), mes, int(dia)).date()
    except ValueError:
        return None


def coletar_chamadas_fapitec(html=None):
    """Coleta os editais abertos. `html` permite testar offline, sem bater na rede."""
    if html is None:
        resp = requests.get(
            URL_EDITAIS_ABERTOS, timeout=45, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        html = resp.content

    soup = BeautifulSoup(html, "html.parser")

    resultados = []
    for item in soup.select("li.wp-block-post"):
        link_tag = item.find("a", href=True)
        if not link_tag:
            continue
        titulo = re.sub(r"\s+", " ", link_tag.get_text(" ", strip=True)).strip()
        if not titulo:
            continue

        resultados.append(
            {
                "titulo": titulo,
                "link": link_tag["href"],
                "data_publicacao": _data_do_time(item.find("time")),
                "possivel_nao_fomento": detectar_possivel_nao_fomento(titulo),
                "tipo_parceria": detectar_tipo_parceria(titulo),
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
                "descricao": None,  # não disponível na listagem
                "data_publicacao": r["data_publicacao"],
                "data_prazo": None,  # só existe dentro do edital
                "instituicao_financiadora": ["FAPITEC"],
                "instituicao_promotora": "FAPITEC",
                "tipo_instrumento": "chamada_publica_edital",
                "tipo_parceria": r["tipo_parceria"],
                "uf": ["SE"],
                "abrangencia": "estadual",
                # Placeholder: não é inferível do título. Corrigido na curadoria.
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
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
        registros = coletar_chamadas_fapitec()
        sinalizados = sum(1 for r in registros if r["possivel_nao_fomento"])
        print(
            f"Coletados {len(registros)} editais abertos da FAPITEC "
            f"({sinalizados} sinalizados como possível não-fomento)."
        )
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
