"""Renomeia fonte para instituicao_financiadora, adiciona instituicao_beneficiaria

Revision ID: 4619dcdbb505
Revises: 1560ebd9e92d
Create Date: 2026-07-28 13:17:29.616432

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4619dcdbb505'
down_revision = '1560ebd9e92d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('oportunidades', schema=None) as batch_op:
        batch_op.add_column(sa.Column('instituicao_financiadora', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('instituicao_beneficiaria', sa.String(length=200), nullable=True))

    op.execute("UPDATE oportunidades SET instituicao_financiadora = fonte")

    with op.batch_alter_table('oportunidades', schema=None) as batch_op:
        batch_op.drop_column('fonte')
        batch_op.alter_column('instituicao_financiadora', nullable=False)


def downgrade():
    with op.batch_alter_table('oportunidades', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fonte', sa.String(length=100), nullable=True))

    op.execute("UPDATE oportunidades SET fonte = instituicao_financiadora")

    with op.batch_alter_table('oportunidades', schema=None) as batch_op:
        batch_op.alter_column('fonte', nullable=False)
        batch_op.drop_column('instituicao_financiadora')
        batch_op.drop_column('instituicao_beneficiaria')