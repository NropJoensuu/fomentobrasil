"""Converte instituicao_financiadora e uf para ARRAY

Revision ID: cfbf9587ffda
Revises: 52ffc42f67d1
Create Date: 2026-08-25 15:12:00.701066

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'cfbf9587ffda'
down_revision = '52ffc42f67d1'
branch_labels = None
depends_on = None


def upgrade():
    # Autogenerate não detecta varchar -> varchar[] (mesma limitação já confirmada na
    # migração anterior de linha_de_fomento), escrita à mão.
    op.alter_column(
        'oportunidades', 'instituicao_financiadora',
        type_=postgresql.ARRAY(sa.String(length=200)),
        postgresql_using="ARRAY[instituicao_financiadora]::varchar(200)[]",
        existing_nullable=False,
    )
    # uf é nullable: sem o CASE WHEN, um registro com uf NULL viraria ARRAY[NULL]
    # ([None], length 1) em vez de continuar NULL.
    op.alter_column(
        'oportunidades', 'uf',
        type_=postgresql.ARRAY(sa.String(length=2)),
        postgresql_using="CASE WHEN uf IS NULL THEN NULL ELSE ARRAY[uf]::varchar(2)[] END",
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        'oportunidades', 'instituicao_financiadora',
        type_=sa.String(length=200),
        postgresql_using="instituicao_financiadora[1]",
        existing_nullable=False,
    )
    op.alter_column(
        'oportunidades', 'uf',
        type_=sa.String(length=2),
        postgresql_using="uf[1]",
        existing_nullable=True,
    )
