"""Scraper das chamadas da FAPERO (Rondônia).

A página de publicações é um bloco de conteúdo escrito à mão (como a Fundação Araucária),
não uma listagem de posts: cada chamada é um `<h2>` seguido de parágrafos de descrição e
de N links para PDFs (edital, retificações, resultados, cancelamento).

**Os links NÃO são itens separados** — são documentos da mesma chamada. Felizmente cada
chamada está dentro da sua própria `<section>`, o que é âncora melhor que percorrer irmãos
entre `<h2>`s.

ARMADILHA: `find_all("section")` devolve **uma a mais**. Existe uma `<section>` externa que
envolve todas as outras — ela contém os 4 `<h2>` e 259 links (o menu e o rodapé do site
inteiro). Sem filtrar, ela vira um 5º registro com o link errado. Por isso o parser exige
**exatamente um `<h2>` por section**.

ORGANIZAÇÃO POR ANO: `/publicacoes/2026-2/`, `/2025-2/`, `/2024-2/editais/`. Só o ano
corrente é coletado. `ANO` fica no topo, atualizável — mesma convenção da FAPESQ (a FAPEPI
consegue calcular sozinha porque o ano está no título dos itens; aqui está na URL).

AVISO ELEITORAL: o site exibia banner de suspensão de conteúdo por legislação eleitoral
(04/07/2026 a 04/10/2026) quando este scraper foi escrito. A lista pode estar incompleta
nesse período — vale revisitar depois de outubro/2026.

LIMITAÇÃO: `data_publicacao` e `data_prazo` não aparecem na página; as datas só existem
dentro dos PDFs.
"""

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app import db
from app.scraper_utils import detectar_tipo_parceria, processar_registro

# >>> Atualizar quando virar o ano (ver "ORGANIZAÇÃO POR ANO" na docstring). <<<
ANO = 2026

URL_PUBLICACOES = f"https://rondonia.ro.gov.br/fapero/publicacoes/{ANO}-2/"

USER_AGENT = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"

PADRAO_CANCELAMENTO = re.compile(r"cancelament|cancelad[ao]", re.IGNORECASE)
PADRAO_RETIFICACAO = re.compile(r"retifica", re.IGNORECASE)
PADRAO_EDITAL = re.compile(r"edital", re.IGNORECASE)
PADRAO_CHAMAMENTO = re.compile(r"chamamento\s+p[úu]blico|credenciamento", re.IGNORECASE)


def _limpar(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def _status_oficial(rotulos):
    """Deduz o status a partir dos rótulos dos documentos.

    Cancelamento tem prioridade sobre retificação: uma chamada cancelada pode ter sido
    retificada antes, e o que interessa ao usuário é que ela não vale mais.
    """
    texto = " ".join(rotulos)
    if PADRAO_CANCELAMENTO.search(texto):
        return "cancelada"
    if PADRAO_RETIFICACAO.search(texto):
        return "retificada"
    return None


def _escolher_link_principal(documentos):
    """Devolve (url, sem_link_edital).

    Prefere o documento cujo rótulo mencione "Edital". Quando não há nenhum — caso real do
    "Credenciamento de Aceleradoras", que só tem "Resultado Preliminar" —, usa o primeiro
    disponível e sinaliza: é sinal de que o edital saiu de cartaz e só restou o resultado.
    """
    for doc in documentos:
        if PADRAO_EDITAL.search(doc["rotulo"]):
            return doc["url"], False
    return documentos[0]["url"], True


def coletar_chamadas_fapero(html=None):
    """Coleta as chamadas do ano. `html` permite testar offline, sem bater na rede."""
    if html is None:
        resp = requests.get(
            URL_PUBLICACOES, timeout=45, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        html = resp.content

    soup = BeautifulSoup(html, "html.parser")

    resultados = []
    for section in soup.find_all("section"):
        cabecalhos = section.find_all("h2")
        # Exatamente um <h2>: descarta a <section> externa que envolve todas as outras
        # (ver ARMADILHA na docstring do módulo).
        if len(cabecalhos) != 1:
            continue

        titulo = _limpar(cabecalhos[0].get_text(" ", strip=True))
        if not titulo:
            continue

        documentos = []
        for link in section.find_all("a", href=True):
            rotulo = _limpar(link.get_text(" ", strip=True))
            if not rotulo:
                continue
            documentos.append({"rotulo": rotulo, "url": urljoin(URL_PUBLICACOES, link["href"])})

        if not documentos:
            continue  # sem nenhum documento não há o que apontar

        # Descrição: os parágrafos que NÃO contêm link. Os que contêm são as próprias
        # linhas de documento ("📄 Visualizar Edital").
        partes = [
            _limpar(p.get_text(" ", strip=True))
            for p in section.find_all("p")
            if not p.find("a")
        ]
        descricao = " ".join(p for p in partes if p) or None

        link_principal, sem_link_edital = _escolher_link_principal(documentos)
        rotulos = [d["rotulo"] for d in documentos]

        resultados.append(
            {
                "titulo": titulo,
                "link": link_principal,
                "descricao": descricao,
                "documentos": documentos,
                "sem_link_edital": sem_link_edital,
                "status_oficial": _status_oficial(rotulos),
                "tipo_instrumento": (
                    "chamamento_publico"
                    if PADRAO_CHAMAMENTO.search(f"{titulo} {descricao or ''}")
                    else "chamada_publica_edital"
                ),
                "tipo_parceria": detectar_tipo_parceria(titulo, descricao),
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
        dados_extra = {"documentos": r["documentos"]}
        if r["sem_link_edital"]:
            dados_extra["sem_link_edital"] = True

        resultado = processar_registro(
            dados_novos={
                "link": r["link"][:500],
                "titulo": r["titulo"][:300],
                # status_oficial É monitorado: um cancelamento ou retificação que apareça
                # depois da curadoria reabre o registro para revisão.
                "status_oficial": r["status_oficial"],
            },
            campos_extras_fixos={
                "descricao": r["descricao"],
                "data_publicacao": None,  # só existe dentro dos PDFs
                "data_prazo": None,  # idem
                # O Centelha 3 é nacional executado localmente (MCTI, FINEP, CNPq, CONFAP,
                # CERTI + FAPERO), mas cada estado tem edital e prazo próprios. Não tentar
                # deduplicar entre FAPs nem extrair os parceiros: o curador decide.
                "instituicao_financiadora": ["FAPERO"],
                "instituicao_promotora": "FAPERO",
                "tipo_instrumento": r["tipo_instrumento"],
                "tipo_parceria": r["tipo_parceria"],
                "uf": ["RO"],
                "abrangencia": "estadual",
                # Placeholder: não é inferível do título. Corrigido na curadoria.
                "linha_de_fomento": ["apoio_formacao_capacitacao"],
                "natureza_recurso": [],
                "proponente_elegivel": [],
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
        registros = coletar_chamadas_fapero()
        print(f"Coletadas {len(registros)} chamadas da FAPERO de {ANO}.")
        resultado = salvar_no_banco(registros)
        print(
            f"Novos: {resultado['novos']} | "
            f"Atualizados: {resultado['atualizados']} | "
            f"Já existentes (ignorados): {resultado['ja_existentes']}"
        )
