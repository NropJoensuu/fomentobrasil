"""Scraper das chamadas abertas do CNPq.

Coleta a página server-rendered de chamadas abertas para submissão e grava os
registros como `status="pendente"`, aguardando curadoria humana antes de
aparecerem na listagem pública.

Estrutura real da página (verificada em 2026-08-09) — cada oportunidade é um bloco:

    <h2 class="headline"><a class="summary url" href="...">Título</a></h2>
    <div class="documentByLine">... <span class="documentPublished">
        <span class="value">DD/MM/AAAA HHhMM</span></span> ...</div>
    <div><div id="parent-fieldname-text">
        <p>Descrição...</p>
        <ul><li><strong>Inscrições:</strong> DD/MM/AAAA a DD/MM/AAAA</li></ul>
    </div></div>

Ou seja: a descrição e as datas NÃO são irmãs diretas do <h2> — ficam aninhadas
dentro de <div>s irmãos. Por isso o parsing localiza os contêineres por seletor,
em vez de procurar um <p> irmão.
"""

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import processar_registro

URL_CHAMADAS_ABERTAS = "https://www.gov.br/cnpq/pt-br/chamadas/abertas-para-submissao"

# User-Agent explícito: identifica o robô e evita bloqueio do UA padrão do requests.
USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

# Datas do tipo "DD/MM/AAAA a DD/MM/AAAA", tolerando espaços dentro da própria data.
# A página tem casos reais como "18/09 /2026" (espaço antes do ano), que quebravam
# um padrão \d{2}/\d{2}/\d{2,4} rígido. O rótulo que antecede varia bastante
# ("Inscrições:", "INSCRIÇÕES:", "Recebimento das propostas:", "Inscrições 2ª Rodada:"),
# por isso casamos só o par de datas, sem depender do rótulo.
PADRAO_DATAS = re.compile(
    r"(\d{2}\s*/\s*\d{2}\s*/\s*\d{2,4})\s*(?:a|à)\s*(\d{2}\s*/\s*\d{2}\s*/\s*\d{2,4})"
)


def parse_data(data_str):
    """Converte "DD/MM/AAAA" ou "DD/MM/AA" em date, tolerando espaços internos."""
    if not data_str:
        return None
    data_str = re.sub(r"\s+", "", data_str)
    for formato in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(data_str, formato).date()
        except ValueError:
            continue
    return None


def extrair_instituicao_financiadora(titulo):
    """Extrai o consórcio de instituições do início do título.

    Ex.: 'Chamada Pública CNPq/CAPES/MRE N° 16/2026 - ...' -> 'CNPq/CAPES/MRE'
    """
    match = re.search(
        r"(?:Chamada|Chamamento)(?:\s+Pública|\s+Público)?\s+([A-Za-z][\w/\.\-]*)", titulo
    )
    return match.group(1) if match else "CNPq"


def inferir_tipo_instrumento(titulo):
    if "chamamento" in titulo.lower():
        return "chamamento_publico"
    return "chamada_publica_edital"


def _texto_normalizado(elemento):
    """get_text com nbsp convertido em espaço comum (a página usa \\xa0 no meio das datas)."""
    return elemento.get_text(" ", strip=True).replace("\xa0", " ")


def coletar_chamadas_cnpq(html=None):
    """Coleta as chamadas abertas e devolve uma lista de dicts prontos para inserção.

    `html` permite testar o parsing offline, sem bater na rede.
    """
    if html is None:
        resp = requests.get(
            URL_CHAMADAS_ABERTAS, timeout=30, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        html = resp.content  # bytes: deixa o bs4 detectar o encoding pela meta tag

    soup = BeautifulSoup(html, "html.parser")

    resultados = []

    for h2 in soup.select("h2.headline"):
        link_tag = h2.find("a")
        if not link_tag:
            continue

        titulo = link_tag.get_text(strip=True)
        link = link_tag.get("href")
        if not link or not titulo:
            continue

        # Percorre os irmãos do <h2> até o próximo <h2>, procurando os contêineres
        # do corpo do texto e da data de publicação (que estão aninhados, não soltos).
        corpo = None
        publicado_em = None

        for irmao in h2.find_next_siblings():
            if irmao.name == "h2":
                break
            if corpo is None:
                corpo = irmao.select_one("#parent-fieldname-text")
            if publicado_em is None:
                marcador = irmao.select_one(".documentPublished .value")
                if marcador:
                    publicado_em = marcador.get_text(strip=True)

        descricao = None
        inscricao_inicio = None
        data_prazo = None

        if corpo is not None:
            for paragrafo in corpo.find_all("p"):
                texto = _texto_normalizado(paragrafo)
                if len(texto) > 40:
                    descricao = texto
                    break

            match_datas = PADRAO_DATAS.search(_texto_normalizado(corpo))
            if match_datas:
                inscricao_inicio = parse_data(match_datas.group(1))
                data_prazo = parse_data(match_datas.group(2))

        # data_publicacao vem do "Publicado em" da própria página — NÃO do início do
        # período de inscrição. As duas datas costumam coincidir, mas não sempre
        # (ex.: chamada CNPq/ERC 21/2026 — publicada 04/08, inscrições desde 03/08).
        data_publicacao = parse_data((publicado_em or "").split()[0] if publicado_em else None)

        resultados.append(
            {
                "titulo": titulo,
                "link": link,
                "descricao": descricao,
                "data_publicacao": data_publicacao,
                "data_prazo": data_prazo,
                "inscricao_inicio": inscricao_inicio,
                "instituicao_financiadora": extrair_instituicao_financiadora(titulo),
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
        # O início do período de inscrição não tem coluna própria no modelo; fica em
        # dados_extra para não se perder até que exista (ou seja descartado na curadoria).
        dados_extra = None
        if r["inscricao_inicio"]:
            dados_extra = {"inscricao_inicio": r["inscricao_inicio"].isoformat()}

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
                "data_prazo": r["data_prazo"],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": r["data_publicacao"],
                "instituicao_financiadora": r["instituicao_financiadora"][:200],
                "tipo_instrumento": r["tipo_instrumento"],
                "abrangencia": "nacional",
                # Placeholder: não dá para inferir a linha de fomento do título/descrição
                # com confiança. Um humano corrige na curadoria, junto com area_principal.
                "linha_de_fomento": "apoio_formacao_capacitacao",
                # natureza_recurso e publico_alvo são NOT NULL no schema, mas não são
                # extraíveis da página de listagem. Ficam como lista vazia — "ainda não
                # determinado" — em vez de um chute que viraria dado errado no banco.
                "natureza_recurso": [],
                "publico_alvo": [],
                "dados_extra": dados_extra,
                "origem": "institucional",
                "status": "pendente",  # aguarda curadoria antes de aparecer publicamente
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
        registros = coletar_chamadas_cnpq()
        print(f"Coletadas {len(registros)} chamadas da página.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
