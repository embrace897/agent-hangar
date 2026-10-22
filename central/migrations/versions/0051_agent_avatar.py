"""O avatar do agente (agents.avatar): um id do catálogo em static/avatars. Vazio nos agentes antigos: a central
escolhe um pelo papel ao mostrar, até o dono trocar.

Revision ID: 0051_agent_avatar
Revises: 0050_version_creator_id
"""
import sqlalchemy as sa
from alembic import op

revision = "0051_agent_avatar"
down_revision = "0050_version_creator_id"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agents") as b:
        b.add_column(sa.Column("avatar", sa.String(40), nullable=True))


def downgrade():
    with op.batch_alter_table("agents") as b:
        b.drop_column("avatar")
