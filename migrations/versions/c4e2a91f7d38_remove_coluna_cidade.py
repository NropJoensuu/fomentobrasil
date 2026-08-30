"""Remove a coluna cidade de oportunidades

A coluna nunca foi usada: nenhum scraper a preenche e, nas 340 oportunidades existentes
no momento desta migração, estava NULL em todas. Localização já é coberta por
`abrangencia` + `uf`; cidade só faria sentido para vagas ligadas a uma instituição física,
caso que o projeto não trata hoje.

Revision ID: c4e2a91f7d38
Revises: b3d1c7a4e920
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e2a91f7d38"
down_revision = "b3d1c7a4e920"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("oportunidades", "cidade")


def downgrade():
    op.add_column(
        "oportunidades",
        sa.Column("cidade", sa.String(length=150), nullable=True),
    )
