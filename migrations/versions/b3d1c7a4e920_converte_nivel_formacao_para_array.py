"""Converte nivel_formacao de String para ARRAY

Revision ID: b3d1c7a4e920
Revises: cfbf9587ffda
Create Date: 2026-08-26 23:05:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b3d1c7a4e920'
down_revision = 'cfbf9587ffda'
branch_labels = None
depends_on = None


def upgrade():
    # Mesma limitação da conversão de linha_de_fomento: o autogenerate não detecta
    # varchar -> varchar[], então a migração é manual.
    #
    # Diferença importante em relação àquela: nivel_formacao é NULLABLE, e a maioria
    # dos registros está NULL (116 de 118 hoje). Um `ARRAY[nivel_formacao]` direto
    # transformaria NULL em `{NULL}` — um array de um elemento nulo, que não é vazio
    # nem nulo e apareceria como lixo na tela. O CASE preserva NULL como NULL e só
    # empacota quem tem valor de verdade.
    op.alter_column(
        'oportunidades',
        'nivel_formacao',
        type_=postgresql.ARRAY(sa.String(length=50)),
        postgresql_using=(
            "CASE WHEN nivel_formacao IS NULL THEN NULL "
            "ELSE ARRAY[nivel_formacao] END::varchar(50)[]"
        ),
        existing_nullable=True,
    )


def downgrade():
    # Pega o primeiro elemento — perde os extras de quem tiver mais de um nível,
    # comportamento esperado ao voltar para campo de valor único.
    op.alter_column(
        'oportunidades',
        'nivel_formacao',
        type_=sa.String(length=50),
        postgresql_using="nivel_formacao[1]",
        existing_nullable=True,
    )
