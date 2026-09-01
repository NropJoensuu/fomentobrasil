"""Scraper das chamadas abertas da FAPERGS (Rio Grande do Sul), via endpoint interno.

Descoberto via DevTools (Network tab, filtro por domínio próprio, mesmo processo usado
para a FAPEMIG): o site expõe um endpoint interno `_service/conteudo/pagedlistfilho`
que devolve JSON com `recordcount`/`pagecount` e um campo `body` contendo um FRAGMENTO
HTML (um `<article class="conteudo-lista__item">` por edital) — um padrão híbrido
diferente de todas as fontes anteriores: nem API estruturada pura (FAPEMIG, FAPESC),
nem HTML de página completa (CNPq, FAPESP, Araucária).

`pageSize=0` já devolve todos os registros de uma vez (confirmado: `recordcount=4`
bateu exatamente com o corpo devolvido, sem necessidade de paginação).

Confirmado em 2026-08-10: só a chamada "CONFAP - Desafios da Amazônia" tem `descricao`
preenchida na resposta; as outras 3 têm `<p class="hidden-xs">` presente mas vazio
(só espaço em branco) — por isso o `get_text(strip=True) or None` é necessário, não
opcional: sem ele, essas 3 gravariam `descricao=""` em vez de `None`.
"""

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

URL_API_FAPERGS = (
    "https://fapergs.rs.gov.br/_service/conteudo/pagedlistfilho"
    "?id=2042&templatename=pagina.listapagina.padrao"
    "&currentPage=1&pageSize=0"
    "&fields%5B%5D=Titulo&fields%5B%5D=TituloCurto&fields%5B%5D=Texto"
    "&form%5Bordem%5D=RECENTES"
)

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"


def coletar_chamadas_fapergs():
    resp = requests.get(URL_API_FAPERGS, timeout=30, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    payload = resp.json()

    html_fragmento = payload.get("body", "")
    soup = BeautifulSoup(html_fragmento, "html.parser")

    resultados = []
    for artigo in soup.select("article.conteudo-lista__item"):
        link_tag = artigo.select_one("h2.conteudo-lista__item__titulo a")
        if not link_tag:
            continue

        titulo = link_tag.get_text(strip=True)
        link = link_tag.get("href")
        if not link:
            continue
        if not link.startswith("http"):
            link = f"https://fapergs.rs.gov.br{link}"

        descricao_tag = artigo.select_one("p.hidden-xs")
        descricao = descricao_tag.get_text(strip=True) if descricao_tag else None
        descricao = descricao or None  # string vazia (ou só espaço) vira None

        resultados.append(
            {
                "titulo": titulo[:300],
                "link": link[:500],
                "descricao": descricao,
            }
        )

    return resultados


def salvar_no_banco(registros):
    """Insere registros novos, atualiza existentes se um campo monitorado mudou
    (ver app.scraper_utils), ou ignora quando nada mudou. Dedup/match por link.

    A FAPERGS não coleta nenhum dos campos monitorados hoje (nem prazo, nem valores),
    então "atualizado" nunca deve acontecer para esta fonte até o parser evoluir."""
    novos = 0
    atualizados = 0
    ja_existentes = 0

    for r in registros:
        resultado = processar_registro(
            dados_novos={
                "link": r["link"],
                "titulo": r["titulo"],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "instituicao_financiadora": ["FAPERGS"],
                "instituicao_promotora": "FAPERGS",
                "tipo_instrumento": "chamada_publica_edital",
                "tipo_parceria": detectar_tipo_parceria(r["titulo"]),
                # Placeholder: não é inferível do título/descrição. Ver docs — sempre revisar.
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
                # NOT NULL no schema, mas não extraíveis com confiança da listagem:
                # lista vazia significa "ainda não determinado", em vez de um chute.
                "natureza_recurso": [],
                "proponente_elegivel": [],
                "uf": ["RS"],
                "abrangencia": "estadual",
                "origem": "institucional",
                "status": "pendente",
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
        registros = coletar_chamadas_fapergs()
        print(f"Coletadas {len(registros)} chamadas.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
