"""Scraper dos editais abertos da FACEPE (Pernambuco).

WordPress antigo (3.8.x), server-rendered. Confirmado em 2026-08-26: **não há REST
API** — `/wp-json/wp/v2/posts` devolve HTTP 404 com a página de erro em HTML, não
JSON (diferente da FAPESC). Por isso o parsing é do HTML.

Estrutura real de cada edital:

    <div class="edital-conteudo">
      <h5>
        <a href="....pdf"><span>28/2026 - </span></a>   <!-- número (ou vazio) -->
        <a href="....pdf">Lançamento Edital nº28/2026 – ...</a>  <!-- título -->
      </h5>
      <hr>
      Publicação: 25 de agosto de 2026
    </div>

Os dois `<a>` apontam para o MESMO PDF. O que separa um edital de um sub-documento
(errata, prorrogação, resultado) é o primeiro `<a>`: no edital ele traz o número
("28/2026 - "); no sub-documento o `<span>` vem **vazio** e o título é prefixado com
"• " (`&bull;&nbsp;`).

DUAS CAMADAS DE FILTRO, ambas necessárias (ver comentários em PADRAO_NUMERO e
PADRAO_SUBDOCUMENTO): o número sozinho não basta, porque resultados e adendos do
Programa Cientista Arretado repetem o número do edital-mãe no prefixo.

LIMITAÇÃO CONHECIDA: `data_prazo` não aparece na listagem — está dentro do PDF, que
não é extraído nesta fase. Mesma limitação de FAPES e FUNDECT.
"""

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import processar_registro

# `?c=aberto` é o filtro de editais abertos. Sem ele a página devolve o mesmo
# conjunto (é a visão padrão), mas explicitar protege contra mudança de default —
# `?c=encerrados` traz 1003 blocos e `?c=resultados` 878, que poluiriam a curadoria.
URL_FACEPE = "https://www.facepe.br/editais/todos/?c=aberto"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

# Primeira camada: o primeiro <a> precisa começar com o número do edital ("28/2026 - ").
# Sub-documentos têm esse span vazio, então caem fora aqui.
PADRAO_NUMERO = re.compile(r"^\s*(\d{1,3}/\d{4})\s*-")

# Segunda camada, indispensável: há sub-documentos que TAMBÉM trazem o número do
# edital-mãe no prefixo e passariam pela primeira camada — 16 dos 65 blocos numerados
# em 2026-08-26, quase todos do Programa Cientista Arretado (nº 40/2024), do tipo
# "Resultado Preliminar 6.ª rodada", "Homologação de Resultado 13ª rodada",
# "Prorrogação ... Adendo nº 9". Sem este filtro eles virariam registros próprios.
# A lista cobre só tipos de documento inequívocos, para não descartar edital de verdade.
PADRAO_SUBDOCUMENTO = re.compile(
    r"\b(resultado|homologa\w*|enquadramento|adendo|prorroga\w*|errata|retifica\w*"
    r"|cronograma|comunicado|convoca\w*|classifica\w*)\b"
    r"|^\s*(lista|rela[çc][ãa]o)\s+de\b"
    r"|\b\d+[ªa]\s*(fase|rodada)\b",
    re.IGNORECASE,
)

# "Publicação: 25 de agosto de 2026" — a data fica fora do <h5>, solta no mesmo div.
PADRAO_PUBLICACAO = re.compile(
    r"Publica[çc][ãa]o:\s*(\d{1,2})\s+de\s+([a-zçã]+)\s+de\s+(\d{4})", re.IGNORECASE
)

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


def parse_data_extenso(dia, mes_nome, ano):
    """Converte ("25", "agosto", "2026") em date. None se o mês não for reconhecido."""
    mes = MESES.get((mes_nome or "").strip().lower())
    if not mes:
        return None
    try:
        return datetime(int(ano), mes, int(dia)).date()
    except ValueError:
        return None


def inferir_tipo_instrumento(titulo):
    return "premio" if "prêmio" in titulo.lower() or "premio" in titulo.lower() else "chamada_publica_edital"


def coletar_editais_facepe(html=None):
    """Coleta os editais abertos e devolve dicts prontos para inserção.

    `html` permite testar o parsing offline, sem bater na rede.
    """
    if html is None:
        resp = requests.get(URL_FACEPE, timeout=45, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        html = resp.content

    soup = BeautifulSoup(html, "html.parser")

    resultados = []

    for bloco in soup.select("div.edital-conteudo"):
        links = bloco.find_all("a")
        if len(links) < 2:
            continue

        numero_match = PADRAO_NUMERO.match(links[0].get_text(" ", strip=True))
        if not numero_match:
            continue  # sub-documento: primeiro <a> vem vazio

        titulo = links[1].get_text(" ", strip=True)
        link = links[0].get("href")
        if not titulo or not link:
            continue

        if PADRAO_SUBDOCUMENTO.search(titulo):
            continue  # resultado/adendo/prorrogação que repete o número do edital-mãe

        data_match = PADRAO_PUBLICACAO.search(bloco.get_text(" ", strip=True))
        data_publicacao = parse_data_extenso(*data_match.groups()) if data_match else None

        # A âncora #fluxo-continuo fica DENTRO do div do edital (não delimita uma
        # seção), então marca o próprio registro. O título às vezes também diz.
        fluxo_continuo = bool(bloco.find("span", id="fluxo-continuo")) or (
            "fluxo cont" in titulo.lower()
        )

        resultados.append(
            {
                "titulo": titulo,
                "link": link,
                "data_publicacao": data_publicacao,
                "numero_edital": numero_match.group(1),
                "fluxo_continuo": fluxo_continuo,
                "tipo_instrumento": inferir_tipo_instrumento(titulo),
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
        dados_extra = {"numero_edital": r["numero_edital"]}
        if r["fluxo_continuo"]:
            # Sinaliza ao curador que não há data-limite a procurar no PDF.
            dados_extra["fluxo_continuo"] = True

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
                # data_prazo não entra como campo monitorado: nunca vem da listagem
                # (ver docstring), então nunca haveria valor novo para comparar.
            },
            campos_extras_fixos={
                "data_publicacao": r["data_publicacao"],
                "data_prazo": None,  # só existe dentro do PDF
                "instituicao_financiadora": ["FACEPE"],
                "tipo_instrumento": r["tipo_instrumento"],
                "uf": ["PE"],
                "abrangencia": "estadual",
                # Placeholder: não é inferível do título. Corrigido na curadoria.
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
                "natureza_recurso": [],
                "publico_alvo": [],
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
        registros = coletar_editais_facepe()
        print(f"Coletados {len(registros)} editais abertos da FACEPE.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
