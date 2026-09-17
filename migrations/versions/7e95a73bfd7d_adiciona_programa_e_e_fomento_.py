"""adiciona programa e e_fomento, instituicao_promotora vira array

Revision ID: 7e95a73bfd7d
Revises: f92c4118d4d8
Create Date: 2026-09-16 19:13:51.941449

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '7e95a73bfd7d'
down_revision = 'f92c4118d4d8'
branch_labels = None
depends_on = None


def upgrade():
    # programa: coluna nova, nullable — nenhum registro existente muda de comportamento.
    op.add_column('oportunidades', sa.Column('programa', sa.String(length=150), nullable=True))

    # e_fomento: NOT NULL contra os 342 registros já existentes exige server_default na
    # hora do ADD COLUMN — sem isso o Postgres rejeita a migração. Mantido depois (e não só
    # durante a migração) porque espelha o default=True do modelo: todo INSERT que não passe
    # por SQLAlchemy (ex: SQL direto) continua caindo no valor certo.
    op.add_column(
        'oportunidades',
        sa.Column('e_fomento', sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # Autogenerate não detecta varchar -> varchar[] (mesma limitação já confirmada nas
    # migrações anteriores de instituicao_financiadora/uf/nivel_formacao), escrita à mão.
    # Nullable: sem o CASE WHEN, um registro com instituicao_promotora NULL viraria
    # ARRAY[NULL] ([None], length 1) em vez de continuar NULL.
    op.alter_column(
        'oportunidades', 'instituicao_promotora',
        type_=postgresql.ARRAY(sa.String(length=200)),
        postgresql_using=(
            "CASE WHEN instituicao_promotora IS NULL THEN NULL "
            "ELSE ARRAY[instituicao_promotora]::varchar(200)[] END"
        ),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        'oportunidades', 'instituicao_promotora',
        type_=sa.String(length=200),
        postgresql_using="instituicao_promotora[1]",
        existing_nullable=True,
    )
    op.drop_column('oportunidades', 'e_fomento')
    op.drop_column('oportunidades', 'programa')
