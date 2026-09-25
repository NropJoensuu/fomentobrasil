"""Lógica compartilhada de salvamento usada por todos os scrapers.

Substitui o padrão antigo ("já existe? pula; não existe? insere") por um que também
detecta mudança num conjunto pequeno de campos monitorados em registros JÁ CURADOS
(aprovados ou não) — por exemplo, quando a fonte adia um prazo depois que o edital já
foi revisado. Nesse caso o registro é atualizado e marcado com `revisao_pendente=True`
para reaparecer em `/moderacao/atualizacoes`, sem regredir `status` (fica visível
normalmente até alguém revisar de novo).
"""

import re
from datetime import datetime, date
from decimal import Decimal
from urllib.parse import urljoin

from app import db
from app.models import Oportunidade

# Cooperação internacional detectada pelo título. Vale para todos os scrapers: o mesmo
# programa CONFAP é operado por várias FAPs, e antes só FAPEAL e FAPESB preenchiam
# `tipo_parceria` — a mesma chamada ficava marcada numa FAP e vazia noutra.
#
# Só marca o que é inequívoco. Nomes de programa (ERC, MSCA, DAAD, CDTI, GCUB, RAMP,
# Water4All, Biodiversa) e países/blocos entram; "confap" sozinho NÃO entra, porque
# há chamada CONFAP puramente nacional. Casos sem nenhuma dessas pistas ficam None e
# são resolvidos na curadoria.
PADRAO_COOPERACAO_INTERNACIONAL = re.compile(
    r"\b("
    r"internacional|international"
    r"|erc\b|msca|sklodowska|curie|daad|cdti|gcub|ramp\b|water4all|biodiversa|horizon|horizonte\s+europa"
    r"|mobility|mobilidade\s+internacional"
    r"|brasillinois|wbi\b|wallonie"
    r"|alemanha|it[áa]lia|espanha|b[ée]lgica|fran[çc]a|portugal|reino\s+unido|europa|exterior"
    r"|jsps|japão|japao|nwo|pa[íi]ses\s+baixos|su[íi][çc]a|noruega|su[ée]cia"
    r")\b",
    re.IGNORECASE,
)


def detectar_tipo_parceria(*textos):
    """Devolve "internacional" se algum dos textos indicar cooperação internacional.

    Recebe vários textos porque a pista nem sempre está no título: a chamada ERC da
    FAPEAL se chama "Mobilidade de pesquisadores, para a Europa", e quem denuncia a
    cooperação são as categorias do post. Passar título + o que mais houver.
    """
    for texto in textos:
        if texto and PADRAO_COOPERACAO_INTERNACIONAL.search(str(texto)):
            return "internacional"
    return None


# Editais que provavelmente NÃO são fomento à pesquisa — contratação de pessoal,
# credenciamento de avaliadores, consultoria. Algumas FAPs publicam isso na mesma seção
# das chamadas (FAPESQ e FAPITEC, hoje).
#
# É SINALIZADOR, não filtro: o registro é coletado do mesmo jeito e recebe
# `dados_extra["possivel_nao_fomento"]` só para o curador priorizar a revisão. Há falso
# positivo legítimo — "PROCESSO SELETIVO DE PESQUISADORES PÓS-GRADUADOS" (FAPESQ) é
# fomento de verdade e casa com a expressão.
#
# E é aplicado POR FONTE, de propósito, não em todos os scrapers: "credenciamento" é
# fomento legítimo na FAPESP e na FAPEMIG ("Edital de credenciamento para incubação de
# startups", "credenciamento de empresas do PIPE"), onde marcá-lo seria ruído.
PADRAO_POSSIVEL_NAO_FOMENTO = re.compile(
    r"processo\s+seletivo|contrata[çc][ãa]o|cadastro\s+de\s+reserva"
    r"|sele[çc][ãa]o\s+de\s+oficineiros|credenciamento|consultoria|\bad\s*hoc\b",
    re.IGNORECASE,
)


def detectar_possivel_nao_fomento(*textos):
    """True se algum texto sugerir que o edital não é fomento à pesquisa."""
    return any(
        texto and PADRAO_POSSIVEL_NAO_FOMENTO.search(str(texto)) for texto in textos
    )


CAMPOS_MONITORADOS = [
    "data_prazo",
    "data_resultado_previsto",
    "orcamento_total_chamada",
    "valor_minimo_proposta",
    "valor_maximo_proposta",
    "status_oficial",
]


def _serializar(valor):
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return str(valor)
    return valor


def _normalizar_para_comparacao(valor):
    """Evita falso-positivo de mudança entre Decimal (vindo do banco, colunas Numeric)
    e float (vindo do scraper): `Decimal('880838.81') != 880838.81` é `True` por causa
    da representação binária do float, mesmo sendo o mesmo valor. Convertendo o float
    via `str()` antes de virar Decimal evita essa comparação furada.
    """
    if isinstance(valor, float):
        return Decimal(str(valor))
    return valor


def processar_registro(dados_novos, campos_extras_fixos, dados_extra_sempre=None):
    """Insere um registro novo, OU atualiza um existente se algum campo monitorado mudou.

    `dados_extra_sempre` são chaves de REFERÊNCIA — hoje a lista de `documentos` — que se
    atualizam a cada passada do scraper mesmo quando nenhum campo monitorado mudou, e que
    NÃO marcam `revisao_pendente`. A distinção importa: a lista de documentos é material de
    apoio à curadoria, não um campo curado. Sem esse canal, um registro já existente nunca
    receberia seus documentos — só os criados a partir de agora teriam —, porque a função só
    grava quando detecta mudança monitorada. O que de fato exige revisão é o `status_oficial`
    virar "retificada", e esse já está em CAMPOS_MONITORADOS.

    Retorna: "novo", "atualizado" ou "sem_mudanca".
    """
    existente = Oportunidade.query.filter_by(link=dados_novos["link"]).first()

    if not existente:
        extras = dict(campos_extras_fixos)
        if dados_extra_sempre:
            extras["dados_extra"] = {**(extras.get("dados_extra") or {}), **dados_extra_sempre}
        oportunidade = Oportunidade(**dados_novos, **extras)
        db.session.add(oportunidade)
        return "novo"

    if dados_extra_sempre:
        atual = dict(existente.dados_extra or {})
        if any(atual.get(k) != v for k, v in dados_extra_sempre.items()):
            atual.update(dados_extra_sempre)
            # Reatribuição, não mutação: ver o comentário longo mais abaixo sobre JSONB.
            existente.dados_extra = atual

    mudancas = []
    for campo in CAMPOS_MONITORADOS:
        valor_novo = dados_novos.get(campo)
        valor_atual = getattr(existente, campo)
        if valor_novo is None:
            continue
        if _normalizar_para_comparacao(valor_novo) == _normalizar_para_comparacao(valor_atual):
            continue

        mudancas.append(
            {
                "campo": campo,
                "valor_anterior": _serializar(valor_atual),
                "valor_novo": _serializar(valor_novo),
                "detectado_em": datetime.utcnow().isoformat(),
            }
        )
        setattr(existente, campo, valor_novo)

    if mudancas:
        existente.revisao_pendente = True
        # dict(...) novo de propósito, não mutação in-place do dict existente: sem
        # isso, "valor antigo" e "valor novo" que o SQLAlchemy compara na hora do
        # flush seriam o MESMO objeto (JSONB não rastreia mutação in-place por
        # padrão, só reassignment) — o UPDATE simplesmente não incluiria a coluna
        # dados_extra, e a mudança sumiria silenciosamente no commit. Confirmado na
        # prática: sem a cópia, data_prazo/revisao_pendente persistiam mas
        # dados_extra voltava ao valor de antes depois do commit.
        dados_extra_atual = dict(existente.dados_extra or {})
        historico = list(dados_extra_atual.get("mudancas_detectadas", []))
        historico.extend(mudancas)
        dados_extra_atual["mudancas_detectadas"] = historico
        existente.dados_extra = dados_extra_atual
        return "atualizado"

    return "sem_mudanca"


# ---------------------------------------------------------------------------
# Documentos de uma chamada (edital, retificações, anexos, resultados)
#
# Uma chamada quase nunca é UM documento. Guardar só o link principal fez o sistema ler o
# edital ORIGINAL em 3 dos 15 registros curados (#21 CNPq, #82 FAPEMIG, #118 FAPESC), mesmo
# havendo retificação publicada — e no #82 isso produziu um prazo de submissão já vencido
# com toda a aparência de correto. É o pior modo de falha do projeto: silencioso e no campo
# mais consultado.
#
# A lógica abaixo é a da FAPERO, movida para cá sem reescrita porque já foi validada contra
# dados reais. Ver `scrapers/fapero.py` para o caso que a originou.
# ---------------------------------------------------------------------------

PADRAO_DOC_CANCELAMENTO = re.compile(r"cancelament|cancelad[ao]", re.IGNORECASE)
# "prorroga" entra aqui porque prorrogação de prazo É retificação para o que importa:
# muda a data de submissão, que é o campo mais consultado e o que mais estraga quando
# fica desatualizado. A FAPEMIG concorda — marca prorrogação com retificacao=true.
PADRAO_DOC_RETIFICACAO = re.compile(
    r"retifica|errata|alterad[ao]|prorroga", re.IGNORECASE
)
PADRAO_DOC_EDITAL = re.compile(r"edital|chamada", re.IGNORECASE)


def coletar_documentos(bloco_html, url_base, filtro_href=None):
    """Lista os documentos de uma chamada, na ordem em que aparecem na página.

    A ordem importa e é preservada de propósito: em geral é cronológica, e quando há mais de
    uma retificação é ela que diz qual é a última.

    `bloco_html` é o elemento BeautifulSoup que contém os links — a `<section>` da chamada, a
    `<div>` do post, o que a página oferecer. `filtro_href` permite restringir a links de
    documento (ex.: só `.pdf`) em páginas que misturam navegação com anexos.
    """
    documentos = []
    vistos = set()
    for link in bloco_html.find_all("a", href=True):
        href = link["href"].strip()
        # Âncora interna, JavaScript e mailto nunca são documento. Sem isto, os links de
        # acessibilidade do gov.br ("Ir para o Conteúdo", href="#content") entravam na lista:
        # o urljoin os resolve para a própria página e eles passavam em qualquer filtro.
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(url_base, href)
        if filtro_href and not filtro_href(url):
            continue
        rotulo = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
        if not rotulo or url in vistos:
            continue
        vistos.add(url)
        documentos.append({"rotulo": rotulo, "url": url})
    return documentos


def escolher_link_principal(documentos):
    """Devolve (url, sem_link_edital).

    Prefere o documento cujo rótulo mencione "Edital" ou "Chamada". Quando não há nenhum —
    caso real do "Credenciamento de Aceleradoras" da FAPERO, que só tem "Resultado
    Preliminar" —, usa o primeiro e sinaliza: é sinal de que o edital saiu de cartaz e só
    restou o resultado.
    """
    if not documentos:
        return None, True
    for doc in documentos:
        if PADRAO_DOC_EDITAL.search(doc["rotulo"]):
            return doc["url"], False
    return documentos[0]["url"], True


def deduzir_status_oficial(documentos):
    """Deduz `status_oficial` a partir dos rótulos dos documentos.

    Cancelamento tem prioridade sobre retificação: uma chamada cancelada pode ter sido
    retificada antes, e o que interessa a quem procura é que ela não vale mais.
    """
    texto = " ".join(d["rotulo"] for d in documentos)
    if PADRAO_DOC_CANCELAMENTO.search(texto):
        return "cancelada"
    if PADRAO_DOC_RETIFICACAO.search(texto):
        return "retificada"
    return None


def documentos_retificadores(documentos):
    """Os documentos cujo rótulo indica retificação, na ordem da página.

    Separado de `deduzir_status_oficial` porque as duas perguntas são diferentes: uma é "esta
    chamada mudou?", a outra é "QUAIS documentos preciso ler para saber o que vale hoje?".
    """
    return [d for d in documentos if PADRAO_DOC_RETIFICACAO.search(d["rotulo"])]


# Um documento de retificação pode ser de dois tipos, e a diferença muda o que precisa ser
# lido (ver o briefing de retificações em docs/):
#
#   substitutiva — o órgão republica o texto COMPLETO já corrigido. Ler esse basta.
#   incremental  — documento curto que lista só o que mudou. Precisa dele E do edital.
#
# O rótulo separa os dois na prática. Numa mesma chamada da FAPEMIG (EVENTECH 009/2026)
# convivem "Chamada Retificada" e "Edital Retificado" (substitutivos, particípio descrevendo
# o próprio documento) com "Ato Retificação" e "Prorrogação do prazo" (incrementais, o ato
# que altera). O tamanho confirma quando disponível: texto completo é grande, ato é curto.
PADRAO_RETIFICACAO_SUBSTITUTIVA = re.compile(
    r"retificad[ao]|consolidad[ao]|atualizad[ao]|nova\s+vers[ãa]o", re.IGNORECASE
)
PADRAO_RETIFICACAO_INCREMENTAL = re.compile(
    r"ato\s+(?:de\s+)?retifica|aviso\s+de\s+retifica|prorroga|errata|comunicado", re.IGNORECASE
)

# Abaixo disto, um PDF não carrega um edital inteiro. Usado só como desempate.
LIMITE_KB_DOCUMENTO_CURTO = 120


def classificar_retificacao(documento):
    """"substitutiva", "incremental" ou None (não é retificação).

    Proposta, não veredito: só o conteúdo decide de fato, e por isso a curadoria confirma.
    """
    rotulo = documento.get("rotulo") or ""
    if not PADRAO_DOC_RETIFICACAO.search(rotulo):
        return None

    if PADRAO_RETIFICACAO_INCREMENTAL.search(rotulo):
        return "incremental"
    if PADRAO_RETIFICACAO_SUBSTITUTIVA.search(rotulo):
        return "substitutiva"

    tamanho = documento.get("tamanho_kb")
    if tamanho is not None:
        return "incremental" if tamanho < LIMITE_KB_DOCUMENTO_CURTO else "substitutiva"
    return "incremental"  # na dúvida, exige ler os dois — errar para o lado seguro
