"""Converte linha_de_fomento de String para ARRAY

Revision ID: 52ffc42f67d1
Revises: 816ff492e253
Create Date: 2026-08-25 14:10:39.526289

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '52ffc42f67d1'
down_revision = '816ff492e253'
branch_labels = None
depends_on = None


def upgrade():
    # Autogenerate não detecta varchar -> varchar[] (limitação conhecida do Alembic
    # para esse tipo de mudança), então esta migração foi escrita à mão. Postgres não
    # converte varchar->varchar[] automaticamente com um ALTER simples; postgresql_using
    # empacota cada valor existente numa lista de um elemento, preservando os 118
    # registros atuais (inclusive os 3 já curados manualmente com valor diferente do
    # placeholder).
    op.alter_column(
        'oportunidades',
        'linha_de_fomento',
        type_=postgresql.ARRAY(sa.String(length=50)),
        postgresql_using="ARRAY[linha_de_fomento]::varchar(50)[]",
        existing_nullable=False,
    )


def downgrade():
    # Pega o primeiro elemento de volta — perde os extras de quem tiver mais de uma
    # linha, mas é o comportamento esperado ao reverter para um campo de valor único.
    op.alter_column(
        'oportunidades',
        'linha_de_fomento',
        type_=sa.String(length=50),
        postgresql_using="linha_de_fomento[1]",
        existing_nullable=False,
    )
