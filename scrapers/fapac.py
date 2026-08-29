"""Scraper dos editais da FAPAC (Acre) — fonte de baixa estruturação.

**É a fonte menos estruturada do projeto.** `https://fapac.ac.gov.br/98-2/` é UMA página
escrita à mão, sem estrutura por item: acumula editais de 2021 a 2026 misturados com anexos,
erratas, resultados, comunicados e portarias, tudo como links soltos. Não há título, data
nem descrição por chamada — só o texto do link.

A estratégia é filtragem em cascata sobre todos os links da área de conteúdo. A precisão é
menor que a das outras fontes por construção; o objetivo é capturar os editais principais do
ano e deixar o resto para a curadoria. Por isso todo registro leva
`dados_extra["fonte_baixa_estruturacao"]`.

DOIS PROBLEMAS REAIS DO HTML, ambos tratados (verificados em 2026-08-29):

1. **O título vem quebrado em vários `<a>`.** O edital 002/2026 aparece como dois links
   consecutivos com o MESMO href: um com o texto `"E"` e outro com `"DITAL Nº 002/2026 …"`.
   Filtrar link a link descartaria os dois (nenhum contém "EDITAL"). Por isso o parser
   **agrupa os `<a>` por href e concatena os textos** antes de filtrar.
2. **Há "E DITAL" com espaço** (edital 003/2026), que sobrevive ao agrupamento. Por isso o
   filtro de inclusão usa `E\\s*DITAL`, e não a string literal "EDITAL". Sem isso, o
   003/2026 — MENTES AZUIS seria perdido.

CASO CONHECIDO: o "EDITAL-PDPG-01.2026" foi publicado como **imagem**
(`EDITAL-PDPG-01.2026-pdf-724x1024.jpg`), não PDF. O filtro de extensão o descarta, o que é
aceitável (não haveria como extrair nada dele), mas o scraper emite um aviso para o curador
saber que existe.

SE A PRECISÃO SE MOSTRAR RUIM na prática (muitos falsos positivos/negativos na curadoria),
vale considerar migrar esta fonte para cadastro manual: o volume é baixo (3 a 5 editais por
ano) e não justifica manter um scraper frágil.
"""

import logging
import re
from collections import OrderedDict
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

logger = logging.getLogger(__name__)

# >>> Atualizar quando virar o ano. O WordPress organiza uploads por ano/mês
# (`/wp-content/uploads/2026/07/...`), e é esse caminho que serve de filtro de ano. <<<
ANO = 2026

URL_EDITAIS = "https://fapac.ac.gov.br/98-2/"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

# Tipos de documento que NÃO são o edital em si.
PADRAO_EXCLUI = re.compile(
    r"\bANEXO\b|\bRESULTADO\b|\bERRATA\b|RETIFICA|PRORROGA|\bCOMUNICADO\b"
    r"|COMISS[ÃA]O\s+DE\s+SELE|FICHA\s+DE\s+INSCRI|DECLARA[ÇC][ÃA]O|FORMUL[ÁA]RIO"
    r"|MODELO\s+DE\s+PROJETO|AUTODECLARA",
    re.IGNORECASE,
)

# "E\s*DITAL" tolera o "E DITAL" com espaço que aparece no 003/2026 (ver docstring).
PADRAO_INCLUI = re.compile(r"E\s*DITAL|CHAMADA|CHAMAMENTO", re.IGNORECASE)

PADRAO_ENCERRADO = re.compile(r"ENCERRAD[AO]", re.IGNORECASE)

# "EDITAL Nº 002/2026" -> "002/2026". Serve de chave para deduplicar republicações.
PADRAO_NUMERO = re.compile(r"N[º°o]?\s*(\d{1,3}/\d{4})", re.IGNORECASE)

# "DOE Nº 14.298, 01 de Julho de 2026 – Republicado por Incorreção"
PADRAO_DOE = re.compile(r"DOE\s*N[º°o]?\s*[\d.]+[^)\n]{0,80}", re.IGNORECASE)
PADRAO_DATA_DOE = re.compile(r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", re.IGNORECASE)

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


def _limpar(texto):
    """Normaliza espaços, tira asteriscos residuais de negrito e junta o "E DITAL"."""
    texto = re.sub(r"\s+", " ", (texto or "").replace("*", "")).strip()
    return re.sub(r"\bE\s+DITAL\b", "EDITAL", texto, flags=re.IGNORECASE)


def _data_do_doe(texto_doe):
    """Extrai a data de uma referência ao Diário Oficial, quando houver."""
    if not texto_doe:
        return None
    match = PADRAO_DATA_DOE.search(texto_doe)
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


def coletar_chamadas_fapac(html=None):
    """Coleta os editais do ano. `html` permite testar offline, sem bater na rede."""
    if html is None:
        resp = requests.get(URL_EDITAIS, timeout=45, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        html = resp.content

    soup = BeautifulSoup(html, "html.parser")
    conteudo = soup.find("div", class_="czr-wp-the-content")
    if conteudo is None:
        return []

    # Agrupa por href preservando a ordem da página: repara os títulos quebrados em
    # vários <a> e, de quebra, dá a ordem necessária para "manter a última republicação".
    por_href = OrderedDict()
    for link in conteudo.find_all("a", href=True):
        href = link["href"]
        if f"/{ANO}/" not in href:
            continue  # filtro de ano pelo caminho do upload
        pai = link.find_parent(["p", "li", "div"])
        contexto = re.sub(r"\s+", " ", pai.get_text(" ", strip=True)) if pai else ""
        registro = por_href.setdefault(href, {"textos": [], "contexto": contexto})
        registro["textos"].append(link.get_text(" ", strip=True))

    candidatos = []
    for href, dados in por_href.items():
        titulo = _limpar(" ".join(dados["textos"]))
        caminho = href.lower().split("?")[0]

        if not caminho.endswith(".pdf"):
            if PADRAO_INCLUI.search(titulo) and not PADRAO_EXCLUI.search(titulo):
                # Ex.: "EDITAL-PDPG-01.2026" publicado como imagem. Não dá para aproveitar,
                # mas o curador precisa saber que existe.
                logger.warning(
                    "FAPAC: edital ignorado por não ser PDF (%s): %s", caminho.split("/")[-1], titulo
                )
            continue
        if PADRAO_EXCLUI.search(titulo):
            continue
        if not PADRAO_INCLUI.search(titulo):
            continue
        if PADRAO_ENCERRADO.search(f"{titulo} {dados['contexto']}"):
            continue

        candidatos.append((titulo, href, dados["contexto"]))

    # Dedup por número do edital, mantendo a ÚLTIMA ocorrência: a página lista a versão
    # original e, abaixo, a republicação por incorreção — a de baixo é a que vale.
    por_numero = OrderedDict()
    for titulo, href, contexto in candidatos:
        numero = PADRAO_NUMERO.search(titulo)
        chave = numero.group(1) if numero else href
        republicado = chave in por_numero
        por_numero[chave] = {
            "titulo": titulo,
            "link": urljoin(URL_EDITAIS, href),
            "numero_edital": numero.group(1) if numero else None,
            "contexto": contexto,
            "republicado": republicado,
        }

    resultados = []
    for dados in por_numero.values():
        doe = PADRAO_DOE.search(dados["contexto"] or "")
        texto_doe = doe.group(0).strip() if doe else None
        resultados.append(
            {
                "titulo": dados["titulo"],
                "link": dados["link"],
                "numero_edital": dados["numero_edital"],
                "republicado": dados["republicado"],
                "diario_oficial": texto_doe,
                "data_publicacao": _data_do_doe(texto_doe),
                "tipo_instrumento": (
                    "chamamento_publico"
                    if re.search(r"CHAMAMENTO", dados["titulo"], re.IGNORECASE)
                    else "chamada_publica_edital"
                ),
                "tipo_parceria": detectar_tipo_parceria(dados["titulo"]),
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
        # Sinaliza ao curador que esta fonte exige revisão mais atenta que as demais.
        dados_extra = {"fonte_baixa_estruturacao": True}
        if r["numero_edital"]:
            dados_extra["numero_edital"] = r["numero_edital"]
        if r["republicado"]:
            dados_extra["republicado"] = True
        if r["diario_oficial"]:
            dados_extra["diario_oficial"] = r["diario_oficial"]

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
            },
            campos_extras_fixos={
                "descricao": None,  # a página não traz descrição por edital
                "data_publicacao": r["data_publicacao"],
                "data_prazo": None,  # só existe dentro do PDF
                "instituicao_financiadora": ["FAPAC"],
                "tipo_instrumento": r["tipo_instrumento"],
                "tipo_parceria": r["tipo_parceria"],
                "uf": ["AC"],
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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    from app import create_app

    app = create_app()
    with app.app_context():
        registros = coletar_chamadas_fapac()
        print(f"Coletados {len(registros)} editais da FAPAC de {ANO}.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
