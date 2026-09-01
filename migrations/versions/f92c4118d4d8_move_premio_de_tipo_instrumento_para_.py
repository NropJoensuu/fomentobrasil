"""Move premio de tipo_instrumento para linha_de_fomento

Revision ID: f92c4118d4d8
Revises: d7f39c15b204
Create Date: 2026-09-01 13:58:13.227869

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f92c4118d4d8'
down_revision = 'd7f39c15b204'
branch_labels = None
depends_on = None


def upgrade():
    # 1) para todo registro com tipo_instrumento='premio', acrescenta 'premiacao'
    #    à linha_de_fomento (sem remover o que já houver)
    op.execute("""
        UPDATE oportunidades
        SET linha_de_fomento = array_append(linha_de_fomento, 'premiacao')
        WHERE tipo_instrumento = 'premio'
          AND NOT ('premiacao' = ANY(linha_de_fomento))
    """)

    # 2) esses registros precisam de um tipo_instrumento válido.
    #    Prêmios são publicados por edital -> chamada_publica_edital
    op.execute("""
        UPDATE oportunidades
        SET tipo_instrumento = 'chamada_publica_edital'
        WHERE tipo_instrumento = 'premio'
    """)


def downgrade():
    op.execute("""
        UPDATE oportunidades
        SET tipo_instrumento = 'premio'
        WHERE 'premiacao' = ANY(linha_de_fomento)
    """)
    op.execute("""
        UPDATE oportunidades
        SET linha_de_fomento = array_remove(linha_de_fomento, 'premiacao')
        WHERE 'premiacao' = ANY(linha_de_fomento)
    """)
