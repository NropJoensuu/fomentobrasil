"""Adiciona origem, status e campos de benchmark (nivel_formacao, area_conhecimento, abrangencia, uf, cidade, instituicao_executora)

Revision ID: 1560ebd9e92d
Revises: f77131d8431e
Create Date: 2026-07-28 12:14:58.281548

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '1560ebd9e92d'
down_revision = 'f77131d8431e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('oportunidades', schema=None) as batch_op:
        batch_op.add_column(sa.Column('origem', sa.String(length=30), nullable=False, server_default='institucional'))
        batch_op.add_column(sa.Column('status', sa.String(length=30), nullable=False, server_default='aprovado'))
        batch_op.add_column(sa.Column('instituicao_executora', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('nivel_formacao', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('abrangencia', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('uf', sa.String(length=2), nullable=True))
        batch_op.add_column(sa.Column('cidade', sa.String(length=150), nullable=True))

    # Preserva o dado existente da coluna antiga antes de remover
    op.execute("UPDATE oportunidades SET instituicao_executora = instituicao")

    with op.batch_alter_table('oportunidades', schema=None) as batch_op:
        batch_op.drop_column('instituicao')
        batch_op.alter_column(
            'area_conhecimento',
            type_=postgresql.ARRAY(sa.String(length=150)),
            postgresql_using="string_to_array(area_conhecimento, ',')"
        )


def downgrade():
    with op.batch_alter_table('oportunidades', schema=None) as batch_op:
        batch_op.alter_column(
            'area_conhecimento',
            type_=sa.String(length=150),
            postgresql_using="array_to_string(area_conhecimento, ',')"
        )
        batch_op.add_column(sa.Column('instituicao', sa.VARCHAR(length=150), autoincrement=False, nullable=True))

    op.execute("UPDATE oportunidades SET instituicao = instituicao_executora")

    with op.batch_alter_table('oportunidades', schema=None) as batch_op:
        batch_op.drop_column('cidade')
        batch_op.drop_column('uf')
        batch_op.drop_column('abrangencia')
        batch_op.drop_column('nivel_formacao')
        batch_op.drop_column('instituicao_executora')
        batch_op.drop_column('status')
        batch_op.drop_column('origem')