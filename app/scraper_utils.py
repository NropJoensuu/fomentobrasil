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

import requests
from bs4 import BeautifulSoup

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
    return classificar_documentos(documentos)


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


# Tipos de documento de uma chamada. A classificação é HEURÍSTICA: ordena a interface e
# alimenta a proposta de leitura, nunca decide sozinha.
#
#   chamada_retificada — retificação SUBSTITUTIVA: texto completo já corrigido. Ler basta.
#   retificacao        — retificação INCREMENTAL: só lista o que mudou. Precisa dela E da
#                        chamada.
#   resultado          — divulgação de resultado, homologação. Não é o edital.
#   anexo              — formulário, declaração, modelo. Apoio, não o texto da chamada.
#   chamada            — o resto: o edital em si.
#
# A distinção entre as duas primeiras muda o que precisa ser lido, e aparece nos dados: a
# chamada FAPEMIG EVENTECH 009/2026 tem as duas ao mesmo tempo, e o tamanho confirma a
# leitura do rótulo — "Chamada Retificada" (947 kB) e "Edital Retificado" (361 kB) contra
# "Ato Retificação" (156 kB) e "Prorrogação do prazo" (57 kB).

PADRAO_DOC_SUBSTITUTIVO = re.compile(
    r"chamada[-_\s]*retificad|edital[-_\s]*retificad|consolidad|nova\s+vers[ãa]o",
    re.IGNORECASE,
)
PADRAO_DOC_INCREMENTAL = re.compile(
    r"retifica|errata|aditivo|prorroga|ato\s+(?:de\s+)?altera|adequa[çc][ãa]o", re.IGNORECASE
)
PADRAO_DOC_ANEXO_NO_INICIO = re.compile(r"anexo\b|ap[êe]ndice\b", re.IGNORECASE)

# Rótulos que dizem explicitamente "este documento é o ATO que altera", e não o texto
# alterado. Vencem o desempate por tamanho: o "Ato Retificação" da EVENTECH tem 156 kB, acima
# do limite, e ainda assim é incremental — o que manda é o que o rótulo declara ser.
PADRAO_DOC_ATO_RETIFICADOR = re.compile(
    r"ato\s+(?:de\s+)?retifica|aviso\s+(?:de\s+)?retifica|prorroga|errata|comunicado"
    r"|extrato\s+de\s+retifica"
    # "Ato DEFA 231/2024: Adequação" — a Fundação Araucária publica seus atos assim, e
    # eles são sempre o documento que altera, nunca o texto alterado.
    r"|\bato\s+\w+\s+\d+/\d{4}",
    re.IGNORECASE,
)

PADRAO_DOC_RESULTADO = re.compile(
    r"resultado|homologa|deferid|indeferid|classificad|selecionad", re.IGNORECASE
)
PADRAO_DOC_ANEXO = re.compile(
    r"\banexo\b|formul[áa]rio|declara[çc][ãa]o|modelo|termo\s+de|carta\s+de"
    r"|planilha|roteiro|\bfaq\b|manual|cartilha|orienta[çc][õo]es|diretrizes",
    re.IGNORECASE,
)

# Abaixo disto, um PDF não carrega um edital inteiro. Usado só como desempate quando o
# rótulo diz "retificação" sem dizer de que tipo.
LIMITE_KB_DOCUMENTO_CURTO = 120


def classificar_documento(rotulo, url="", tamanho_kb=None):
    """"chamada_retificada", "retificacao", "resultado", "anexo" ou "chamada".

    A precedência é deliberada e não é alfabética:

    1. Substitutivo primeiro, porque "Chamada Retificada" casa TAMBÉM com o padrão
       incremental (contém "retifica"). Testar na ordem inversa classificaria toda
       substitutiva como incremental — que é o erro caro, porque faria o sistema ler dois
       documentos quando um bastava, e pior, concatenar texto revogado.
    2. Incremental antes de resultado: "Retificação do resultado preliminar" é retificação.
    3. Resultado antes de anexo: "Anexo — Resultado Final" é resultado.

    `tamanho_kb` desempata APENAS quando o rótulo diz retificação sem dizer de que tipo
    ("Retificação 1"): documento curto é o ato, documento grande é o texto inteiro. Um rótulo
    que se declara ato ("Ato Retificação", "Prorrogação") ignora o tamanho — o da EVENTECH
    tem 156 kB, acima do limite, e é incremental assim mesmo. Sem tamanho e sem rótulo
    explícito, fica incremental: o lado seguro, que exige ler a chamada também.
    """
    alvo = f"{rotulo or ''} {url or ''}"

    if PADRAO_DOC_SUBSTITUTIVO.search(alvo):
        return "chamada_retificada"

    if PADRAO_DOC_INCREMENTAL.search(alvo):
        # Rótulo explícito de ato vence o tamanho; só o rótulo vago é desempatado por ele.
        if PADRAO_DOC_ATO_RETIFICADOR.search(alvo):
            return "retificacao"
        if tamanho_kb is not None and tamanho_kb >= LIMITE_KB_DOCUMENTO_CURTO:
            return "chamada_retificada"
        return "retificacao"

    if PADRAO_DOC_RESULTADO.search(alvo):
        return "resultado"
    # "ANEXO II - EDITAL DE CHAMAMENTO" é anexo, não edital: o rótulo começa dizendo o que
    # o documento é, e o resto é a que edital ele pertence.
    if PADRAO_DOC_ANEXO_NO_INICIO.match((rotulo or "").strip()):
        return "anexo"
    # "Diretrizes" sozinho é documento de apoio ("Diretrizes da Fapeal", que acompanha todas
    # as chamadas daquela fonte). Mas a FAPESB batiza o próprio edital de "DIRETRIZES
    # ESPECÍFICAS DA FAPESB – CHAMADA BIODIVERSA": quando o rótulo também diz chamada ou
    # edital, é a chamada, e o "diretrizes" é só como aquela casa chama seus editais.
    if PADRAO_DOC_ANEXO.search(alvo) and not PADRAO_DOC_EDITAL.search(alvo):
        return "anexo"
    return "chamada"


def classificar_documentos(documentos):
    """Acrescenta `tipo` a cada documento da lista, in-place, e devolve a lista.

    Gravado junto com o documento, não em campo separado: quem lê a lista precisa do tipo
    junto, e separar convidaria as duas a saírem de sincronia.
    """
    for doc in documentos:
        doc["tipo"] = classificar_documento(
            doc.get("rotulo"), doc.get("url"), doc.get("tamanho_kb")
        )
    return documentos


TIPOS_DOCUMENTO_RETIFICADOR = ("chamada_retificada", "retificacao")


def escolher_documentos_para_leitura(documentos):
    """Quais documentos precisam ser lidos para saber o que vale HOJE.

    Devolve lista de `{"origem": ..., "rotulo": ..., "url": ...}` na ordem de leitura, em que
    `origem` é o tipo do documento. A ordem importa: quando há retificação incremental, ela
    vem PRIMEIRO, porque prevalece sobre o texto original.

    Três casos, e a diferença entre eles é o ponto de toda a parte 1 deste trabalho:

    1. **Existe retificação substitutiva** (`chamada_retificada`) — o órgão republicou o
       texto completo já corrigido. Um documento basta, e é a mais recente delas. Ler também
       a chamada original seria pior que inútil: colocaria texto revogado na frente do
       modelo.
    2. **Existe retificação incremental** (`retificacao`) — ela só lista o que mudou, e o
       texto da chamada continua valendo no resto. Precisa dos dois, retificações primeiro,
       da mais recente para a mais antiga.
    3. **Só a chamada** — comportamento de sempre.

    "Mais recente" é a última da lista: `coletar_documentos` preserva a ordem da página, que
    é cronológica na prática (a FAPESC publica "RETIFICAÇÃO" e depois "RETIFICAÇÃO II").
    """
    if not documentos:
        return []

    def _do_tipo(tipo):
        return [d for d in documentos if d.get("tipo") == tipo]

    def _como(origem, doc):
        return {"origem": origem, "rotulo": doc.get("rotulo"), "url": doc.get("url")}

    substitutivas = _do_tipo("chamada_retificada")
    if substitutivas:
        return [_como("chamada_retificada", substitutivas[-1])]

    chamadas = _do_tipo("chamada")
    incrementais = _do_tipo("retificacao")

    leitura = [_como("retificacao", d) for d in reversed(incrementais)]
    if chamadas:
        leitura.append(_como("chamada", chamadas[0]))

    # Só anexo e resultado não formam plano: devolver lista vazia faz quem chamou voltar ao
    # `link` do registro, que é uma aposta melhor que ler um formulário de inscrição ou as
    # "Diretrizes da Fapeal" no lugar do edital. Era o comportamento anterior e continua
    # certo quando a fonte não expõe o edital como documento separado.
    return leitura


USER_AGENT_PADRAO = "fomentobrasil-scraper/1.0 (+https://fomentobrasil.com.br)"


def documentos_da_pagina(url, seletor, filtro_href=None, user_agent=USER_AGENT_PADRAO,
                         logger=None, timeout=45):
    """Busca a página do item e coleta os documentos DENTRO do contêiner `seletor`.

    O escopo não é detalhe: a página inteira traz barra lateral e rodapé, e ali moram links
    de outros editais. Na FAPESB isso produzia oito "retificações" que eram erratas de
    chamadas diferentes, rotuladas "clique aqui"; na FAPEG, seis "documentos" que eram leis e
    decretos do menu institucional. Escopado ao corpo do post, sobra o que é da chamada.

    Devolve [] em qualquer falha — de rede, de seletor ausente ou de página sem documento.
    Uma chamada sem documentos coletados é o estado anterior do sistema, não uma regressão;
    derrubar o scraper inteiro por causa de uma página fora do ar seria.
    """
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": user_agent})
        resp.raise_for_status()
    except requests.RequestException as e:
        if logger:
            logger.warning("não consegui abrir %s (%s)", url, type(e).__name__)
        return []

    container = BeautifulSoup(resp.content, "html.parser").select_one(seletor)
    if container is None:
        if logger:
            logger.warning("seletor %r não encontrado em %s", seletor, url)
        return []

    return coletar_documentos(container, url, filtro_href=filtro_href)


def so_pdf(url):
    """Filtro de href para `coletar_documentos`: aceita apenas PDF."""
    return url.lower().split("?")[0].endswith(".pdf")
