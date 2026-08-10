"""Scraper das chamadas de propostas da FAPESP.

Usa a listagem ANUAL (`/2185/chamadas-de-propostas-2026`), não a página `/chamadas/`,
que repete a mesma chamada em várias categorias. Grava como `status="pendente"`,
para curadoria humana antes de aparecer publicamente.

Estrutura real da página (verificada em 2026-08-10): um único `<ul class="list">` com
41 `<li>` diretos, cada um com as linhas separadas por `<br>`:

    <li><a href="https://fapesp.br/18319">Título da chamada</a><br />
    Chamada FAPESP 41/2026<br />
    Prazo final para o envio de propostas: 03/09/2026<br />
    Descrição livre...<br />
    Apoio: FAPESP e JSPS</li>

Atenção: o texto completo da página também aparece duplicado dentro das meta tags
`og:description` e `twitter:description` — por isso o seletor é ancorado em
`ul.list > li`, e não numa busca solta por `<li>`.
"""

import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app import db
from app.models import Oportunidade

URL_FAPESP_CHAMADAS = "https://fapesp.br/2185/chamadas-de-propostas-2026"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

PADRAO_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}
PADRAO_DATA_EXTENSO = re.compile(
    r"(\d{1,2})\s+de\s+([a-zçã]+)\s+de\s+(\d{4})", re.IGNORECASE
)

PADRAO_NUMERO_CHAMADA = re.compile(r"^Chamada\s+FAPESP\s+\d+/\d{4}", re.IGNORECASE)

# Uma linha é de prazo quando COMEÇA com um rótulo de prazo, ou quando é só uma data
# solta (há chamadas em que o rótulo fica numa linha e a data na seguinte).
# Checar o início da linha — e não a presença do termo em qualquer posição — evita
# engolir descrições que mencionam "submissão"/"prazo" no meio do texto corrido.
# O plural importa: há linhas "Datas limite ..." (chamadas com dois ciclos).
PADRAO_ROTULO_PRAZO = re.compile(
    r"^(prazos?\b|datas?[\s\-]?limite|datas?\s+finais?|data\s+final|inscri[çc])",
    re.IGNORECASE,
)

# Exige os dois-pontos: há descrições que começam com "Apoio a projetos bilaterais...",
# que não são a linha de instituições e não podem ser confundidas com ela.
PADRAO_APOIO = re.compile(r"^apoio\s*:\s*", re.IGNORECASE)


def parse_data(texto):
    """Retorna a ÚLTIMA data encontrada no texto.

    A última é a que interessa: quando a chamada tem dois prazos (pré-proposta e
    proposta completa), o prazo final de fato é o segundo. Entende DD/MM/AAAA e
    "D de mês de AAAA". Datas sem ano (ex.: "21/05") ficam de fora de propósito —
    inferir o ano seria chute; o texto cru fica em dados_extra["prazo_bruto"]
    para o curador resolver.
    """
    if not texto:
        return None

    encontradas = []
    for m in PADRAO_DATA.finditer(texto):
        dia, mes, ano = (int(g) for g in m.groups())
        encontradas.append((m.start(), dia, mes, ano))
    for m in PADRAO_DATA_EXTENSO.finditer(texto):
        mes = MESES.get(m.group(2).lower())
        if mes:
            encontradas.append((m.start(), int(m.group(1)), mes, int(m.group(3))))

    if not encontradas:
        return None

    encontradas.sort()
    _, dia, mes, ano = encontradas[-1]
    try:
        return datetime(ano, mes, dia).date()
    except ValueError:
        return None


def coletar_chamadas_fapesp(html=None):
    """Coleta as chamadas e devolve dicts prontos para inserção.

    `html` permite testar o parsing offline, sem bater na rede.
    """
    if html is None:
        resp = requests.get(
            URL_FAPESP_CHAMADAS, timeout=30, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        html = resp.content

    soup = BeautifulSoup(html, "html.parser")

    resultados = []

    for item in soup.select("ul.list > li"):
        link_tag = item.find("a")
        if not link_tag:
            continue

        titulo = link_tag.get_text(strip=True)
        link = link_tag.get("href")
        if not link or not titulo:
            continue
        link = urljoin(URL_FAPESP_CHAMADAS, link)

        linhas = [l.strip() for l in item.get_text("\n", strip=True).split("\n") if l.strip()]

        chamada_numero = None
        prazo_linhas = []
        apoio = None
        candidatas_descricao = []

        for linha in linhas:
            if linha == titulo:
                continue
            if PADRAO_NUMERO_CHAMADA.match(linha):
                chamada_numero = linha
            elif PADRAO_APOIO.match(linha):
                apoio = PADRAO_APOIO.sub("", linha).strip()
            elif PADRAO_ROTULO_PRAZO.match(linha) or PADRAO_DATA.fullmatch(linha):
                prazo_linhas.append(linha)
            else:
                candidatas_descricao.append(linha)

        prazo_bruto = " | ".join(prazo_linhas) if prazo_linhas else None
        descricao = max(candidatas_descricao, key=len) if candidatas_descricao else None

        resultados.append(
            {
                "titulo": titulo,
                "link": link,
                "descricao": descricao,
                "data_prazo": parse_data(prazo_bruto),
                "prazo_bruto": prazo_bruto,
                "chamada_numero": chamada_numero,
                "instituicao_financiadora": apoio or "FAPESP",
            }
        )

    return resultados


def salvar_no_banco(registros):
    """Insere registros novos (dedup por link), como pendentes de curadoria."""
    novos = 0
    ja_existentes = 0

    for r in registros:
        if Oportunidade.query.filter_by(link=r["link"]).first():
            ja_existentes += 1
            continue

        dados_extra = {}
        if r.get("chamada_numero"):
            dados_extra["chamada_numero"] = r["chamada_numero"]
        if r.get("prazo_bruto"):
            dados_extra["prazo_bruto"] = r["prazo_bruto"]

        oportunidade = Oportunidade(
            titulo=r["titulo"][:300],
            link=r["link"][:500],
            descricao=r["descricao"],
            data_prazo=r["data_prazo"],
            instituicao_financiadora=r["instituicao_financiadora"][:200],
            tipo_instrumento="chamada_publica_edital",
            uf="SP",
            abrangencia="estadual",
            # Placeholder: não é inferível do título/descrição. Ver docs — sempre revisar.
            linha_de_fomento="apoio_formacao_capacitacao",
            # NOT NULL no schema, mas não extraíveis da listagem: lista vazia significa
            # "ainda não determinado", em vez de um chute que viraria dado errado.
            natureza_recurso=[],
            publico_alvo=[],
            origem="institucional",
            status="pendente",
            dados_extra=dados_extra or None,
        )
        db.session.add(oportunidade)
        novos += 1

    db.session.commit()
    return {"novos": novos, "ja_existentes": ja_existentes}


if __name__ == "__main__":
    from app import create_app

    app = create_app()
    with app.app_context():
        registros = coletar_chamadas_fapesp()
        print(f"Coletadas {len(registros)} chamadas.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
