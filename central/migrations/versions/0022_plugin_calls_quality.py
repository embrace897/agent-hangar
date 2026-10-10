"""Plugins: auditoria por chamada, limite de chamadas por instalação e a verificação da assinatura da imagem.
Digital employees: de onde veio a tarefa (Slack, Teams) para responder lá, e a nota de qualidade do resultado.

Revision ID: 0022_plugin_calls_quality
Revises: 0021_plugin_creator_gallery
"""
import sqlalchemy as sa
from alembic import op

revision = "0022_plugin_calls_quality"
down_revision = "0021_plugin_creator_gallery"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_calls",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plugin_id", sa.Integer, sa.ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("install_id", sa.Integer, nullable=True),
        sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("stage", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("caller", sa.String(120), nullable=False, server_default=""),
        sa.Column("task_id", sa.Integer, nullable=True),
        sa.Column("tool", sa.String(80), nullable=False, server_default=""),
        sa.Column("action", sa.String(30), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="ok"),
        sa.Column("http_status", sa.Integer, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("args", sa.Text, nullable=False, server_default=""),
        sa.Column("result", sa.Text, nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_plugin_calls_plugin_at", "plugin_calls", ["plugin_id", "at"])
    op.create_index("ix_plugin_calls_at", "plugin_calls", ["at"])
    with op.batch_alter_table("plugin_installs") as b:
        b.add_column(sa.Column("rate_limit", sa.Integer, nullable=True))
    with op.batch_alter_table("plugins") as b:
        b.add_column(sa.Column("signature", sa.JSON, nullable=True))
    with op.batch_alter_table("employee_tasks") as b:
        b.add_column(sa.Column("origin", sa.JSON, nullable=True))
        b.add_column(sa.Column("quality", sa.Integer, nullable=True))
        b.add_column(sa.Column("quality_note", sa.Text, nullable=False, server_default=""))
        b.add_column(sa.Column("quality_by", sa.String(254), nullable=False, server_default=""))
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("quality_sample", sa.Integer, nullable=False, server_default="20"))


def downgrade():
    with op.batch_alter_table("employees") as b:
        b.drop_column("quality_sample")
    with op.batch_alter_table("employee_tasks") as b:
        for c in ("quality_by", "quality_note", "quality", "origin"):
            b.drop_column(c)
    with op.batch_alter_table("plugins") as b:
        b.drop_column("signature")
    with op.batch_alter_table("plugin_installs") as b:
        b.drop_column("rate_limit")
    op.drop_index("ix_plugin_calls_at", "plugin_calls")
    op.drop_index("ix_plugin_calls_plugin_at", "plugin_calls")
    op.drop_table("plugin_calls")
