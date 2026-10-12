"""Aprendizado da organização: artigos e procedimentos propostos a partir das tarefas que deram certo.

Revision ID: 0035_org_learning
Revises: 0034_ticket_connectors
"""
import sqlalchemy as sa
from alembic import op

revision = "0035_org_learning"
down_revision = "0034_ticket_connectors"
branch_labels = None
depends_on = None

S = sa.String
TZ = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("learning_mode", S(4), nullable=False, server_default="on"))
    op.create_table(
        "learned_artifacts", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1"),
        sa.Column("kind", S(12), nullable=False), sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("employee_id", sa.Integer, nullable=True), sa.Column("task_id", sa.Integer, nullable=True),
        sa.Column("title", S(300), nullable=False), sa.Column("content", sa.JSON, nullable=False),
        sa.Column("status", S(10), nullable=False, server_default="proposed"), sa.Column("request_id", sa.Integer, nullable=True),
        sa.Column("target", S(160), nullable=False, server_default=""), sa.Column("uses", sa.Integer, nullable=False, server_default="0"),
        sa.Column("decided_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
        sa.Column("decided_at", TZ, nullable=True),
    )
    for c in ("org_id", "team_id", "employee_id", "task_id"):
        op.create_index(f"ix_learned_artifacts_{c}", "learned_artifacts", [c])


def downgrade():
    op.drop_table("learned_artifacts")
    with op.batch_alter_table("employees") as b:
        b.drop_column("learning_mode")
