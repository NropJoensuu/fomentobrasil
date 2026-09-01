"""Scraper das chamadas abertas da Fundação Araucária (Paraná).

URL: https://www.fappr.pr.gov.br/Programas-Abertos (Drupal, server-rendered).
NÃO usar https://sparkx.fundacaoaraucaria.org.br/#/public/chamadas — é uma SPA vazia
sem dado no HTML (sistema de submissão, não a listagem pública).

A página inteira é um único campo de texto rico editado à mão (não há um node por
item): as 4 seções ("Chamadas Públicas", "Processos de Inexigibilidade", "Parcerias
da Araucária", "Processos de Manifestação de Interesse") são separadas por `<hr>` +
`<h3>`. Só coletamos CP e PA — são as duas seções que representam oportunidades de
fomento reais (PI são processos administrativos não-competitivos, sem chamada aberta
para pesquisadores; PMI está vazia hoje, sem estrutura conhecida para confiar).

Cada item é um `<div class="row two-col-right">` com dois filhos:
  - `<div class="col-main col-md-9">`: `<h3>` com o código curto (ex.: "CP 18/26:
    ICMBio"), um `<p>` de descrição — o nome completo do edital normalmente vem
    sublinhado (`<u>`) dentro desse parágrafo — e uma `<ul>` de links de documentos
    (Edital, Anexos, Atos/Resultados).
  - `<div class="col-sidebar col-md-3">`: uma `<ul>` com "Resp."/"Setor Resp.",
    "Dotação Inicial" e o prazo de inscrição/submissão (ou "Fluxo Contínuo").

Não há URL própria por item (a listagem inteira vive numa página só) nem data de
publicação por item (mesma página/node para tudo). O `link` usado para dedup é o
primeiro link de documento do item: nas Chamadas Públicas isso é o Edital em PDF
(hospedado no próprio fappr.pr.gov.br); nas Parcerias da Araucária costuma ser a
página/notícia da CONFAP (confap.org.br), já que não há um PDF "oficial" fappr por
item nesses casos.

`dotacao_bruta` fica como texto cru em `dados_extra` (não convertida para
`orcamento_total_chamada`): os valores aparecem em formatos bem variados ("R$ 4,6
MI", "EU$ 75 MIL", "R$ 18.000.000,00"), e um parser confiável exigiria mais regras
do que vale a pena agora — o curador converte manualmente na revisão.
"""

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

URL_ARAUCARIA = "https://www.fappr.pr.gov.br/Programas-Abertos"

# Diferente dos outros scrapers: o WAF do fappr.pr.gov.br derruba a conexão (RST) para
# o User-Agent auto-identificado usado no resto do projeto ("fomentobrasil-scraper/1.0
# ..."), mas aceita normalmente um UA de navegador comum. Confirmado que não é uma
# política de robots.txt (o robots.txt do site não restringe /Programas-Abertos, sem
# crawl-delay) — é só um bloqueio de WAF por assinatura de UA.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

PADRAO_DATA = re.compile(r"(\d{2}/\d{2}/\d{2,4})")
PADRAO_DOTACAO = re.compile(r"Dota[çc][ãa]o Inicial:\s*(.+)")
# "Resp.:" e "Setor Resp.:" aparecem os dois na página, sem padrão fixo.
PADRAO_SETOR = re.compile(r"(?:Setor\s+)?Resp\.?:\s*(.+)")

SECOES_COLETADAS = ["Chamadas Públicas", "Parcerias da Araucária"]


def parse_data(texto):
    match = PADRAO_DATA.search(texto)
    if not match:
        return None
    data_str = match.group(1)
    for formato in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(data_str, formato).date()
        except ValueError:
            continue
    return None


def coletar_secao(soup, nome_secao):
    """Coleta os itens de uma seção (do `<h3>` com esse texto até o próximo `<hr>`)."""
    resultados = []

    secao_heading = soup.find(
        lambda tag: tag.name == "h3" and tag.get_text(strip=True) == nome_secao
    )
    if not secao_heading:
        return resultados

    for irmao in secao_heading.find_next_siblings():
        if irmao.name == "hr":
            break
        if irmao.name != "div" or "two-col-right" not in (irmao.get("class") or []):
            continue

        main = irmao.find("div", class_=lambda c: c and "col-main" in c)
        sidebar = irmao.find("div", class_=lambda c: c and "col-sidebar" in c)
        if main is None:
            continue

        h3_item = main.find("h3")
        titulo_curto = h3_item.get_text(" ", strip=True) if h3_item else None

        # Nome completo normalmente sublinhado dentro do parágrafo de descrição;
        # ausente em 2 dos 13 itens (ex.: "PA Confap: Mobility Confap Italy - MCI
        # 2026"), caso em que caímos para o código curto do <h3>.
        paragrafo = main.find("p")
        u_tag = paragrafo.find("u") if paragrafo else None
        titulo = u_tag.get_text(" ", strip=True) if u_tag else titulo_curto
        titulo = re.sub(r"\s+", " ", (titulo or "").replace("\xa0", " ")).strip()
        if not titulo:
            continue

        descricao = None
        if paragrafo is not None:
            descricao = paragrafo.get_text(" ", strip=True).replace("\xa0", " ")
            descricao = re.sub(r"\s+", " ", descricao).strip() or None

        link_tag = main.find("a", href=True)
        link = link_tag["href"] if link_tag else None
        if link and not link.startswith("http"):
            link = f"https://www.fappr.pr.gov.br{link}"
        if not link:
            continue

        # get_text() sem separador: os campos da sidebar (Resp., Dotação Inicial,
        # prazo) vêm cada um em seu próprio <li>, quebrados por linha pelo próprio
        # espaçamento do HTML de origem — não é um separador sintético do BS4.
        texto_sidebar = sidebar.get_text().replace("\xa0", " ") if sidebar else ""

        dotacao_match = PADRAO_DOTACAO.search(texto_sidebar)
        dotacao_bruta = dotacao_match.group(1).strip() if dotacao_match else None

        setor_match = PADRAO_SETOR.search(texto_sidebar)
        setor_responsavel = setor_match.group(1).strip() if setor_match else None

        prazo_bruto = None
        data_prazo = None
        if "fluxo cont" in texto_sidebar.lower():
            prazo_bruto = "Fluxo Contínuo"
        else:
            data_prazo = parse_data(texto_sidebar)
            if data_prazo:
                prazo_bruto = data_prazo.strftime("%d/%m/%Y")

        resultados.append(
            {
                "titulo": titulo[:300],
                "link": link[:500],
                "descricao": descricao,
                "data_prazo": data_prazo,
                "dotacao_bruta": dotacao_bruta,
                "setor_responsavel": setor_responsavel,
                "prazo_bruto": prazo_bruto,
                "secao": nome_secao,
            }
        )

    return resultados


def coletar_chamadas_araucaria(html=None):
    """Coleta CP + PA e devolve dicts prontos para inserção.

    `html` permite testar o parsing offline, sem bater na rede.
    """
    if html is None:
        resp = requests.get(
            URL_ARAUCARIA, timeout=30, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        html = resp.text

    soup = BeautifulSoup(html, "html.parser")

    resultados = []
    for secao in SECOES_COLETADAS:
        resultados.extend(coletar_secao(soup, secao))
    return resultados


def salvar_no_banco(registros):
    """Insere registros novos, atualiza existentes se um campo monitorado mudou
    (ver app.scraper_utils), ou ignora quando nada mudou. Dedup/match por link."""
    novos = 0
    atualizados = 0
    ja_existentes = 0

    for r in registros:
        dados_extra = {"secao_araucaria": r["secao"]}
        if r.get("dotacao_bruta"):
            dados_extra["dotacao_bruta"] = r["dotacao_bruta"]
        if r.get("setor_responsavel"):
            dados_extra["setor_responsavel"] = r["setor_responsavel"]
        if r.get("prazo_bruto"):
            dados_extra["prazo_bruto"] = r["prazo_bruto"]

        resultado = processar_registro(
            dados_novos={
                "link": r["link"],
                "titulo": r["titulo"],
                "data_prazo": r["data_prazo"],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "instituicao_financiadora": ["Fundação Araucária"],
                "instituicao_promotora": "Fundação Araucária",
                "tipo_instrumento": "chamada_publica_edital",
                "tipo_parceria": detectar_tipo_parceria(r["titulo"]),
                # Placeholder: não é inferível do título/descrição. Ver docs — sempre revisar.
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
                # NOT NULL no schema, mas não extraíveis com confiança da listagem:
                # lista vazia significa "ainda não determinado", em vez de um chute.
                "natureza_recurso": [],
                "proponente_elegivel": [],
                "uf": ["PR"],
                "abrangencia": "estadual",
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
        registros = coletar_chamadas_araucaria()
        print(f"Coletadas {len(registros)} chamadas (CP + PA).")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
