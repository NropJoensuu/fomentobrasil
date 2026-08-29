"""Scraper das chamadas e editais da FAPEMIG.

Diferente de CNPq e FAPESP, a FAPEMIG expõe uma **API JSON estruturada** (WordPress
headless com namespace REST próprio), então não há parsing de HTML da listagem — é
mais robusto e não quebra com mudança visual do site.

    GET https://api.site.fapemig.br/wp-json/fapemig-chamadas-e-editais/v1/chamadas
        ?publicacao_status=publish&page=N

Resposta paginada: {"data": [...], "total": N, "page": N, "per_page": 20,
"pagination": {"total_pages": N}}.

Em 2026-08-10: 189 chamadas em 10 páginas, mas só **12 com status_chamada="aberta"**
(136 encerradas, 33 com resultado, 8 em análise). Por padrão só as abertas são
importadas — ver `apenas_abertas` em `coletar_chamadas_fapemig`.
"""

from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

API_BASE = "https://api.site.fapemig.br/wp-json/fapemig-chamadas-e-editais/v1/chamadas"

BASE_LINK = "https://fapemig.br/oportunidades/chamadas-e-editais/"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

# Trava de segurança: impede laço infinito caso a API pare de informar total_pages
# corretamente ou passe a devolver sempre a mesma página.
MAX_PAGINAS = 100

# O vocabulário de status da FAPEMIG não é o nosso. Só "resultados" tem equivalente
# em `status_oficial`; "encerrada" não tem (no nosso modelo isso vem de data_prazo).
STATUS_OFICIAL_POR_STATUS_CHAMADA = {"resultados": "resultado_divulgado"}

# A FAPEMIG classifica público-alvo com 5 slugs; 4 são idênticos aos nossos.
# `ambiente-de-inovacao` (incubadora, parque tecnológico, aceleradora) fica de fora
# de propósito: não temos equivalente e NÃO é sinônimo de `startups`. Ele continua
# visível em dados_extra["publico_alvo_fapemig"] para o curador decidir.
MAPA_PUBLICO_ALVO = {
    "pesquisadores": "pesquisadores",
    "empresas": "empresas",
    "governo": "governo",
    "ict": "ict",
}


def _slugs_selecionados(campo):
    """Slugs marcados numa taxonomia da API."""
    if not isinstance(campo, dict):
        return []
    return [
        s["slug"]
        for s in (campo.get("selected") or [])
        if isinstance(s, dict) and s.get("slug")
    ]


def mapear_publico_alvo(campo):
    """Converte o público-alvo da FAPEMIG para o nosso vocabulário.

    Só converte o que tem equivalente exato — o resto é descartado aqui (e preservado
    em dados_extra). Preserva a ordem e não repete valores.
    """
    mapeados = []
    for slug in _slugs_selecionados(campo):
        nosso = MAPA_PUBLICO_ALVO.get(slug)
        if nosso and nosso not in mapeados:
            mapeados.append(nosso)
    return mapeados


def limpar_html(texto):
    """Remove tags HTML de um campo de texto rico, retornando texto puro."""
    if not texto:
        return None
    return BeautifulSoup(texto, "html.parser").get_text(separator=" ", strip=True) or None


def extrair_data(valor):
    """Extrai a parte de data de um datetime 'YYYY-MM-DD HH:MM:SS' ou 'YYYY-MM-DD'."""
    if not valor:
        return None
    try:
        return datetime.strptime(valor[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _rotulos_selecionados(campo):
    """Extrai os rótulos de uma taxonomia da API.

    A API devolve {'validate': {slug: bool}, 'selected': [{'slug':..,'label':..}]}.
    Guardamos os rótulos legíveis para o curador classificar depois.
    """
    if not isinstance(campo, dict):
        return []
    return [
        s.get("label") or s.get("slug")
        for s in (campo.get("selected") or [])
        if isinstance(s, dict) and (s.get("label") or s.get("slug"))
    ]


def coletar_chamadas_fapemig(apenas_abertas=True):
    """Percorre todas as páginas da API e devolve dicts prontos para inserção.

    `apenas_abertas=True` (padrão) traz só `status_chamada == "aberta"`, alinhado com
    os scrapers de CNPq e FAPESP, que também listam apenas chamadas vigentes.
    Importar as encerradas traria um problema concreto: 113 delas não têm
    `data_fim_submissao`, e o site calcula "aberta" quando `data_prazo` é nulo —
    ou seja, apareceriam como "Chamada aberta" sem estarem.
    """
    resultados = []
    page = 1
    total_informado = None

    while page <= MAX_PAGINAS:
        resp = requests.get(
            API_BASE,
            params={"publicacao_status": "publish", "page": page},
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        payload = resp.json()

        if total_informado is None:
            total_informado = payload.get("total")

        itens = payload.get("data") or []
        if not itens:
            break

        for item in itens:
            slug = item.get("slug")
            titulo = item.get("titulo")
            # titulo e link são NOT NULL no schema: sem um dos dois, o registro é inútil.
            if not slug or not titulo:
                continue

            if apenas_abertas and item.get("status_chamada") != "aberta":
                continue

            valor_raw = item.get("valor")
            try:
                orcamento = float(valor_raw) if valor_raw else None
            except (TypeError, ValueError):
                orcamento = None

            resultado_info = item.get("data_divulgacao_resultado") or {}
            resultado_previsto = extrair_data(
                resultado_info.get("data") if isinstance(resultado_info, dict) else None
            )

            status_chamada = item.get("status_chamada")

            resultados.append(
                {
                    "titulo": titulo,
                    "link": f"{BASE_LINK}{slug}",
                    "descricao": limpar_html(item.get("descricao_chamada")),
                    "data_publicacao": extrair_data(item.get("data_publicacao")),
                    "data_prazo": extrair_data(item.get("data_fim_submissao")),
                    "data_resultado_previsto": resultado_previsto,
                    "orcamento_total_chamada": orcamento,
                    "status_oficial": STATUS_OFICIAL_POR_STATUS_CHAMADA.get(status_chamada),
                    "publico_alvo": mapear_publico_alvo(item.get("publico_alvo")),
                    "dados_extra": {
                        "numero_chamada": item.get("numero"),
                        "status_chamada_fapemig": status_chamada,
                        "fluxo_tipo": item.get("fluxo_tipo"),
                        "data_inicio_submissao": item.get("data_inicio_submissao"),
                        "quem_pode_participar": limpar_html(item.get("quem_pode_participar")),
                        "o_que_pode_ser_financiado": limpar_html(
                            item.get("o_que_pode_ser_financiado")
                        ),
                        # Taxonomias que a própria FAPEMIG já classifica. Não são gravadas
                        # direto em linha_de_fomento/publico_alvo porque o vocabulário não
                        # é idêntico ao nosso (e linha_de_fomento é valor único, enquanto a
                        # FAPEMIG marca vários). Ficam aqui para orientar a curadoria.
                        "linhas_fomento_fapemig": _rotulos_selecionados(
                            item.get("linhas_fomento")
                        ),
                        "publico_alvo_fapemig": _rotulos_selecionados(item.get("publico_alvo")),
                        "areas_conhecimento_fapemig": _rotulos_selecionados(
                            item.get("areas_conhecimento")
                        ),
                    },
                }
            )

        total_pages = (payload.get("pagination") or {}).get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    return resultados, total_informado


def salvar_no_banco(registros):
    """Insere registros novos, atualiza existentes se um campo monitorado mudou
    (ver app.scraper_utils), ou ignora quando nada mudou. Dedup/match por link."""
    novos = 0
    atualizados = 0
    ja_existentes = 0

    for r in registros:
        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
                "data_prazo": r["data_prazo"],
                "data_resultado_previsto": r["data_resultado_previsto"],
                "orcamento_total_chamada": r["orcamento_total_chamada"],
                "status_oficial": r["status_oficial"],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": r["data_publicacao"],
                "instituicao_financiadora": ["FAPEMIG"],
                "tipo_instrumento": "chamada_publica_edital",
                "tipo_parceria": detectar_tipo_parceria(r["titulo"]),
                "uf": ["MG"],
                "abrangencia": "estadual",
                # Placeholder: a FAPEMIG marca várias linhas de fomento por chamada, e o
                # nosso campo é de valor único. Ver dados_extra["linhas_fomento_fapemig"].
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
                # natureza_recurso a API não informa; publico_alvo vem da taxonomia da
                # FAPEMIG (só os valores com equivalente exato — ver MAPA_PUBLICO_ALVO).
                "natureza_recurso": [],
                "publico_alvo": r["publico_alvo"],
                "origem": "institucional",
                "status": "pendente",
                "dados_extra": r["dados_extra"],
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
        registros, total_api = coletar_chamadas_fapemig()
        print(
            f"API informa {total_api} chamadas publicadas; "
            f"{len(registros)} abertas selecionadas para importação."
        )
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
