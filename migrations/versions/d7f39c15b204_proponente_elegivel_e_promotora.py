"""Renomeia publico_alvo, remove executora/beneficiaria e cria instituicao_promotora

Três mudanças que vêm da mesma constatação e por isso viajam juntas: nos 15 registros
curados à mão, `instituicao_executora` e `instituicao_beneficiaria` foram preenchidas com
TIPOS ("ICT", "IF; IES", "ICT-RO; IES-RO") e não com nomes próprios — de nove valores
distintos, só "PROCON-SC" era uma instituição real. O campo #376 chegou a receber "FAPERO",
que é a financiadora e promotora do edital, não sua executora.

O diagnóstico: executora, beneficiária, outorgada e interveniente são papéis PÓS-CONCESSÃO,
que só existem depois que a proposta é aprovada. Um portal de descoberta trabalha no
vocabulário PRÉ-outorga: promotora, financiadoras, proponente.

- `publico_alvo` -> `proponente_elegivel`: o nome antigo convidava à leitura "quem é
  beneficiado", e o conteúdo sempre foi "quem pode apresentar a proposta".
- `instituicao_promotora`: quem publica a chamada e recebe as propostas. Não é derivável
  das financiadoras — o Amazônia +10 tem aporte de várias FAPs, CONFAP e BNDES, e foi
  publicado pela FAPESP numa edição e pelo CNPq em outra.

ATENÇÃO ao renomear coluna com Alembic: o autogenerate detecta renomeação como
drop_column + add_column, o que APAGARIA os dados. As renomeações aqui são `alter_column`
com `new_column_name`, escritas à mão.

Revision ID: d7f39c15b204
Revises: c4e2a91f7d38
"""

import sqlalchemy as sa
from alembic import op

revision = "d7f39c15b204"
down_revision = "c4e2a91f7d38"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "oportunidades", "publico_alvo", new_column_name="proponente_elegivel"
    )

    # Mesma renomeação dentro das faixas, que vivem em dados_extra (JSONB). Sem isto, as
    # faixas gravadas ficariam com a chave antiga e sumiriam do formulário.
    op.execute("""
        UPDATE oportunidades
        SET dados_extra = jsonb_set(
            dados_extra,
            '{faixas}',
            (
                SELECT jsonb_agg(
                    CASE WHEN faixa ? 'publico_alvo'
                         THEN (faixa - 'publico_alvo')
                              || jsonb_build_object('proponente_elegivel', faixa->'publico_alvo')
                         ELSE faixa
                    END
                )
                FROM jsonb_array_elements(dados_extra->'faixas') AS faixa
            )
        )
        WHERE dados_extra ? 'faixas'
          AND jsonb_typeof(dados_extra->'faixas') = 'array'
          AND jsonb_array_length(dados_extra->'faixas') > 0
    """)

    op.add_column(
        "oportunidades",
        sa.Column("instituicao_promotora", sa.String(length=200), nullable=True),
    )

    # Backfill: o primeiro elemento de instituicao_financiadora é a fonte de onde o scraper
    # coletou — ou seja, o site da própria promotora. Correto na esmagadora maioria dos
    # casos; os conjuntos (Amazônia +10) o curador ajusta.
    op.execute("""
        UPDATE oportunidades
        SET instituicao_promotora = instituicao_financiadora[1]
        WHERE instituicao_financiadora IS NOT NULL
          AND array_length(instituicao_financiadora, 1) >= 1
    """)

    op.drop_column("oportunidades", "instituicao_executora")
    op.drop_column("oportunidades", "instituicao_beneficiaria")


def downgrade():
    op.add_column(
        "oportunidades",
        sa.Column("instituicao_beneficiaria", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "oportunidades",
        sa.Column("instituicao_executora", sa.String(length=200), nullable=True),
    )
    op.drop_column("oportunidades", "instituicao_promotora")

    op.execute("""
        UPDATE oportunidades
        SET dados_extra = jsonb_set(
            dados_extra,
            '{faixas}',
            (
                SELECT jsonb_agg(
                    CASE WHEN faixa ? 'proponente_elegivel'
                         THEN (faixa - 'proponente_elegivel')
                              || jsonb_build_object('publico_alvo', faixa->'proponente_elegivel')
                         ELSE faixa
                    END
                )
                FROM jsonb_array_elements(dados_extra->'faixas') AS faixa
            )
        )
        WHERE dados_extra ? 'faixas'
          AND jsonb_typeof(dados_extra->'faixas') = 'array'
          AND jsonb_array_length(dados_extra->'faixas') > 0
    """)

    op.alter_column(
        "oportunidades", "proponente_elegivel", new_column_name="publico_alvo"
    )
