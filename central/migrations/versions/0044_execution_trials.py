"""Medição por perfil de execução: cada chamada guarda versão, modelo e esforço; comparações de perfis em stage.

Revision ID: 0044_execution_trials
Revises: 0043_connector_stdio
"""
import sqlalchemy as sa
from alembic import op

revision = "0044_execution_trials"
down_revision = "0043_connector_stdio"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("usage_events") as b:
        b.add_column(sa.Column("version", sa.Integer, nullable=True))
        b.add_column(sa.Column("model", sa.String(200), nullable=False, server_default=""))
        b.add_column(sa.Column("effort", sa.String(12), nullable=False, server_default=""))
    op.create_table(
        "execution_trials", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1", index=True),
        sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("model", sa.String(200), nullable=False, server_default=""),
        sa.Column("baseline", sa.String(12), nullable=False, server_default=""),
        sa.Column("levels", sa.JSON, nullable=True), sa.Column("results", sa.JSON, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="running"),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("created_at", TZ, nullable=True), sa.Column("finished_at", TZ, nullable=True),
    )


def downgrade():
    op.drop_table("execution_trials")
    with op.batch_alter_table("usage_events") as b:
        for c in ("effort", "model", "version"):
            b.drop_column(c)
