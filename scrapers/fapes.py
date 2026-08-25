"""Scraper dos editais abertos da FAPES (Espírito Santo).

A FAPES publica os editais abertos em 5 páginas separadas por categoria, todas no
mesmo template server-rendered (Orchard CMS, sem JS necessário). Confirmado em
2026-08-10: não há API JSON (/api/editais e /wp-json/ devolvem a página de erro
padrão do CMS, não JSON) — diferente da FAPEMIG.

Estrutura real de cada página (verificada por amostragem nas 5 categorias):

    <table class="table-downloads">          <!-- um por edital (acordeão) -->
      <tbody>
        <tr>                                  <!-- um por documento/versão -->
          <th class="coluna-1">
            <a href="/Media/.../Edital.pdf" ...>
              <span class="conteudo-value">TÍTULO</span>
            </a>
            <div class="caption"><span class="caption-value">DESCRIÇÃO</span></div>
          </th>
          <th class="coluna-2"><span class="dataatualizacao-value">DD/MM/AAAA</span></th>
          ...
        </tr>
      </tbody>
    </table>

Um mesmo edital pode ter várias linhas (ex.: original + 1ª alteração + 2ª alteração,
cada uma com seu próprio link, título e descrição) — por isso a coleta itera as
linhas de cada tabela, não só a primeira. A categoria "Extensão" pode legitimamente
não ter nenhuma tabela (bloco vazio) quando não há editais abertos no momento.

LIMITAÇÃO CONHECIDA: a coluna "Atualização" é a data de última modificação do
ARQUIVO PDF, não o prazo de submissão da chamada (o prazo só existe dentro do PDF,
que não é extraído nesta fase). Por isso `data_prazo` fica sempre None aqui — guardada
em dados_extra como "documento_atualizado_em" só para referência.
"""

from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import processar_registro

URLS_FAPES = {
    "Carreira Científica": "https://fapes.es.gov.br/edital-aberto-forma%C3%A7%C3%A3o-cient%C3%ADfica",
    "Pesquisa": "https://fapes.es.gov.br/editais-abertos-pesquisa-4",
    "Difusão do Conhecimento": "https://fapes.es.gov.br/difusao-do-conhecimento",
    "Extensão": "https://fapes.es.gov.br/editais-abertos-extensao-2",
    "Inovação": "https://fapes.es.gov.br/inovacao",
}

BASE_URL = "https://fapes.es.gov.br"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"


def parse_data(data_str):
    """Converte "DD/MM/AAAA" em date. Devolve None se não casar o formato."""
    if not data_str:
        return None
    try:
        return datetime.strptime(data_str.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def coletar_editais_categoria(categoria, url, html=None):
    """Coleta os editais de uma categoria e devolve uma lista de dicts.

    `html` permite testar o parsing offline, sem bater na rede.
    """
    if html is None:
        resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        html = resp.content

    soup = BeautifulSoup(html, "html.parser")

    resultados = []

    for tabela in soup.select("table.table-downloads"):
        for linha in tabela.select("tbody tr"):
            link_tag = linha.find("a", href=True)
            if not link_tag or not link_tag["href"].lower().endswith(".pdf"):
                continue

            conteudo = link_tag.select_one("span.conteudo-value")
            titulo = conteudo.get_text(strip=True) if conteudo else link_tag.get_text(strip=True)
            if not titulo:
                continue

            link = urljoin(BASE_URL, link_tag["href"])

            legenda = linha.select_one("div.caption span.caption-value")
            descricao = legenda.get_text(" ", strip=True) if legenda else None

            data_tag = linha.select_one("span.dataatualizacao-value")
            atualizacao = parse_data(data_tag.get_text(strip=True) if data_tag else None)

            resultados.append(
                {
                    "titulo": titulo,
                    "link": link,
                    "descricao": descricao,
                    "categoria": categoria,
                    "documento_atualizado_em": atualizacao,
                }
            )

    return resultados


def coletar_todos_editais_fapes():
    todos = []
    for categoria, url in URLS_FAPES.items():
        try:
            todos.extend(coletar_editais_categoria(categoria, url))
        except requests.RequestException as e:
            print(f"Falha ao coletar '{categoria}': {e}")
    return todos


def salvar_no_banco(registros):
    """Insere registros novos, atualiza existentes se um campo monitorado mudou
    (ver app.scraper_utils), ou ignora quando nada mudou. Dedup/match por link."""
    novos = 0
    atualizados = 0
    ja_existentes = 0

    for r in registros:
        dados_extra = {"categoria_fapes": r["categoria"]}
        if r["documento_atualizado_em"]:
            dados_extra["documento_atualizado_em"] = r["documento_atualizado_em"].isoformat()

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
                # data_prazo não é campo monitorado aqui: nunca vem da listagem (ver
                # docstring do módulo), então nunca há um valor novo real pra comparar.
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_prazo": None,  # não disponível na listagem, requer abrir o PDF
                "instituicao_financiadora": "FAPES",
                "tipo_instrumento": "chamada_publica_edital",
                "uf": "ES",
                "abrangencia": "estadual",
                # Placeholder: a categoria FAPES não mapeia 1:1 para linha_de_fomento;
                # requer revisão manual na curadoria (ver dados_extra["categoria_fapes"]).
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
        registros = coletar_todos_editais_fapes()
        print(f"Coletados {len(registros)} editais (todas as 5 categorias).")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
