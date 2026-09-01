"""Scraper das consultas/chamadas da FUNDECT (Mato Grosso do Sul).

WordPress server-rendered. Existe REST API, mas ela **não serve aqui**: o post type
`lista` está vazio (`X-WP-Total: 0`) e as categorias (`chamadas-abertas`,
`chamadas-em-andamento`, `chamadas-encerradas`) não expõem a coluna **Tramitação** nem o
histórico de documentos, que é justamente o que esta fonte tem de melhor. Por isso o
parsing é da tabela HTML de `https://www.fundect.ms.gov.br/informativos/consultas/`.

Estrutura: `<table class="consultas-table">`, onde **cada chamada ocupa duas linhas**:

    <tr>  <td></td> <td>Nome</td> <td>Assunto</td>
          <td><strong>Leitura:</strong> ... <a>Acessar proposta</a> Em andamento</td> </tr>
    <tr>  <td>*Observação: 14/07/2026 - Resultado Final ... 03/07/2026 - Chamada ...</td> </tr>

A segunda linha é continuação da primeira (histórico cronológico de documentos), não uma
chamada nova — o parser consome as duas juntas.

Diferencial em relação às outras FAPs: a coluna Tramitação dá status explícito, que vira
`status_oficial`. Como esse campo tem prioridade sobre o cálculo por `data_prazo` na
exibição, chamadas finalizadas **não** aparecem como "Aberta" mesmo sem prazo — o problema
que obrigou a FAPEMIG a importar só as abertas não existe aqui.

LIMITAÇÃO CONHECIDA: `data_prazo` não aparece na listagem (só dentro do PDF), como em
FAPES, FACEPE e FAPEG.
"""

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

URL_BASE = "https://www.fundect.ms.gov.br/informativos/consultas/"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

# Trava: a listagem tinha 2 páginas em 2026-08-26; o limite evita laço infinito caso o
# site passe a responder 200 para qualquer /page/N/.
MAX_PAGINAS = 20

PADRAO_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

# "Cancelamento da Chamada", "Chamada cancelada" — as duas formas aparecem no histórico.
PADRAO_CANCELAMENTO = re.compile(r"cancelad[ao]|cancelamento", re.IGNORECASE)


def _url_pagina(numero):
    return URL_BASE if numero == 1 else f"{URL_BASE}page/{numero}/"


def _data_mais_antiga(texto):
    """Devolve a data mais ANTIGA do bloco de observação.

    As entradas vêm da mais recente para a mais antiga ("14/07 Resultado Final ...
    03/07 Chamada ..."), e a publicação original é a última da lista — por isso o mínimo,
    e não a primeira data encontrada.
    """
    encontradas = []
    for dia, mes, ano in PADRAO_DATA.findall(texto or ""):
        try:
            encontradas.append(datetime(int(ano), int(mes), int(dia)).date())
        except ValueError:
            continue
    return min(encontradas) if encontradas else None


def _status_oficial(tramitacao, observacao):
    """Traduz a coluna Tramitação (+ histórico) para o nosso vocabulário.

    O cancelamento tem prioridade: a chamada aparece como "Finalizado" na coluna, mas o
    histórico deixa claro que ela foi cancelada, não que saiu resultado.
    """
    if PADRAO_CANCELAMENTO.search(observacao or ""):
        return "cancelada"
    if "finalizado" in (tramitacao or "").lower():
        # Aproximação: "Finalizado" costuma significar resultado publicado. O curador
        # refina se for outro desfecho.
        return "resultado_divulgado"
    return None  # "Em andamento": deixa o cálculo automático por data agir


def _tipo_instrumento(titulo):
    return "premio" if titulo.strip().lower().startswith("prêmio") else "chamada_publica_edital"


def _extrair_da_tabela(html):
    """Extrai as chamadas de uma página, juntando cada par de linhas."""
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find("table", class_="consultas-table")
    if tabela is None:
        return []

    linhas = tabela.find_all("tr")
    resultados = []
    i = 0
    while i < len(linhas):
        celulas = linhas[i].find_all(["td", "th"])
        if len(celulas) < 4:
            i += 1
            continue

        titulo = celulas[1].get_text(" ", strip=True)
        if not titulo or titulo == "Nome":  # cabeçalho
            i += 1
            continue

        celula_tramitacao = celulas[3]
        link_tag = celula_tramitacao.find("a", href=True)  # "Acessar proposta"
        if not link_tag:
            i += 1
            continue

        # A linha seguinte é o histórico desta mesma chamada, quando existir.
        observacao = ""
        consumidas = 1
        if i + 1 < len(linhas):
            proxima = linhas[i + 1].find_all(["td", "th"])
            if len(proxima) == 1 and proxima[0].get_text(strip=True).startswith("*Observa"):
                observacao = proxima[0].get_text(" ", strip=True)
                consumidas = 2

        resultados.append(
            {
                "titulo": titulo,
                "descricao": celulas[2].get_text(" ", strip=True) or None,
                "link": link_tag["href"],
                "tramitacao": celula_tramitacao.get_text(" ", strip=True),
                "observacao": observacao or None,
                "data_publicacao": _data_mais_antiga(observacao),
            }
        )
        i += consumidas

    return resultados


def coletar_chamadas_fundect(paginas_html=None):
    """Percorre todas as páginas da listagem e devolve dicts prontos para inserção.

    `paginas_html` (lista de HTMLs) permite testar offline, sem bater na rede.
    """
    if paginas_html is None:
        paginas_html = []
        for numero in range(1, MAX_PAGINAS + 1):
            resp = requests.get(
                _url_pagina(numero), timeout=45, headers={"User-Agent": USER_AGENT}
            )
            if resp.status_code == 404:
                break  # fim da paginação
            resp.raise_for_status()
            if not _extrair_da_tabela(resp.content):
                break  # página sem chamadas (tabela vazia ou ausente)
            paginas_html.append(resp.content)

    resultados = []
    for html in paginas_html:
        for bruto in _extrair_da_tabela(html):
            resultados.append(
                {
                    "titulo": bruto["titulo"],
                    "descricao": bruto["descricao"],
                    "link": bruto["link"],
                    "data_publicacao": bruto["data_publicacao"],
                    "status_oficial": _status_oficial(bruto["tramitacao"], bruto["observacao"]),
                    "tipo_instrumento": _tipo_instrumento(bruto["titulo"]),
                    "historico_documentos": bruto["observacao"],
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
        if r["historico_documentos"]:
            dados_extra["historico_documentos"] = r["historico_documentos"]

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
                # status_oficial É monitorado: uma chamada que passe de "Em andamento"
                # para finalizada ou cancelada depois da curadoria reabre para revisão.
                "status_oficial": r["status_oficial"],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": r["data_publicacao"],
                "data_prazo": None,  # só existe dentro do PDF da chamada
                # Parcerias (Fundect/CNPq/CAPES, Fundect/CONFAP) aparecem no título, mas
                # extraí-las automaticamente seria chute — o curador completa a lista.
                "instituicao_financiadora": ["FUNDECT"],
                "instituicao_promotora": "FUNDECT",
                "tipo_instrumento": r["tipo_instrumento"],
                "tipo_parceria": detectar_tipo_parceria(r["titulo"]),
                "uf": ["MS"],
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
        registros = coletar_chamadas_fundect()
        print(f"Coletadas {len(registros)} chamadas da FUNDECT.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
