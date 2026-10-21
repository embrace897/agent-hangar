"""Quem criou cada versão de agente, pelo id (agent_versions.created_user_id): "Meus agentes" mostra os agentes que a
pessoa criou sem depender do nome, que pode repetir entre membros ou mudar.

Revision ID: 0050_version_creator_id
Revises: 0049_remove_user_subscriptions
"""
import sqlalchemy as sa
from alembic import op

revision = "0050_version_creator_id"
down_revision = "0049_remove_user_subscriptions"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agent_versions") as b:
        b.add_column(sa.Column("created_user_id", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("agent_versions") as b:
        b.drop_column("created_user_id")
