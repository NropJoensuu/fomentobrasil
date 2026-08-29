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


def processar_registro(dados_novos, campos_extras_fixos):
    """Insere um registro novo, OU atualiza um existente se algum campo monitorado mudou.

    Retorna: "novo", "atualizado" ou "sem_mudanca".
    """
    existente = Oportunidade.query.filter_by(link=dados_novos["link"]).first()

    if not existente:
        oportunidade = Oportunidade(**dados_novos, **campos_extras_fixos)
        db.session.add(oportunidade)
        return "novo"

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
