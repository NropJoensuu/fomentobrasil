"""Lógica compartilhada de salvamento usada por todos os scrapers.

Substitui o padrão antigo ("já existe? pula; não existe? insere") por um que também
detecta mudança num conjunto pequeno de campos monitorados em registros JÁ CURADOS
(aprovados ou não) — por exemplo, quando a fonte adia um prazo depois que o edital já
foi revisado. Nesse caso o registro é atualizado e marcado com `revisao_pendente=True`
para reaparecer em `/moderacao/atualizacoes`, sem regredir `status` (fica visível
normalmente até alguém revisar de novo).
"""

from datetime import datetime, date
from decimal import Decimal

from app import db
from app.models import Oportunidade

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
        dados_extra_atual = existente.dados_extra or {}
        historico = dados_extra_atual.get("mudancas_detectadas", [])
        historico.extend(mudancas)
        dados_extra_atual["mudancas_detectadas"] = historico
        existente.dados_extra = dados_extra_atual
        return "atualizado"

    return "sem_mudanca"
