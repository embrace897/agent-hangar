"""Fase A: metas e projetos (o porquê da tarefa), a conversa da tarefa (mensagens na fila enquanto o agente trabalha),
orçamentos com escopo e incidentes (aviso e parada), e a caixa de entrada pessoal (lido e arquivado).

Revision ID: 0023_goals_threads_budgets_inbox
Revises: 0022_plugin_calls_quality
"""
import sqlalchemy as sa
from alembic import op

revision = "0023_goals_threads_budgets_inbox"
down_revision = "0022_plugin_calls_quality"
branch_labels = None
depends_on = None


def _ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    op.create_table(
        "goals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1", index=True),
        sa.Column("team_id", sa.Integer, nullable=True, index=True),
        sa.Column("parent_id", sa.Integer, nullable=True, index=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("owner_user_id", sa.Integer, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("target_date", sa.String(10), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(254), nullable=False, server_default=""),
        _ts("created_at"), _ts("updated_at"),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1", index=True),
        sa.Column("team_id", sa.Integer, nullable=True, index=True),
        sa.Column("slug", sa.String(80), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("instructions", sa.Text, nullable=False, server_default=""),
        sa.Column("goal_id", sa.Integer, nullable=True),
        sa.Column("default_employee_id", sa.Integer, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(254), nullable=False, server_default=""),
        _ts("created_at"), _ts("updated_at"),
    )
    with op.batch_alter_table("employee_tasks") as b:
        b.add_column(sa.Column("project_id", sa.Integer, nullable=True))
        b.add_column(sa.Column("goal_id", sa.Integer, nullable=True))
        b.create_index("ix_employee_tasks_project_id", ["project_id"])
    op.create_table(
        "task_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("employee_tasks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("author", sa.String(254), nullable=False, server_default=""),
        sa.Column("user_id", sa.Integer, nullable=True),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        _ts("created_at"), _ts("delivered_at", True),
    )
    op.create_table(
        "budget_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1", index=True),
        sa.Column("scope_type", sa.String(12), nullable=False),
        sa.Column("scope_id", sa.Integer, nullable=True),
        sa.Column("window", sa.String(8), nullable=False, server_default="month"),
        sa.Column("amount_usd", sa.Float, nullable=False),
        sa.Column("warn_percent", sa.Integer, nullable=False, server_default="80"),
        sa.Column("hard_stop", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(254), nullable=False, server_default=""),
        _ts("created_at"),
    )
    op.create_table(
        "budget_incidents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("budget_policies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("window_key", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("spent_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("amount_usd", sa.Float, nullable=False, server_default="0"),
        _ts("opened_at"),
        sa.Column("resolved_by", sa.String(254), nullable=False, server_default=""),
        _ts("resolved_at", True),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    op.create_table(
        "inbox_states",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False, index=True),
        sa.Column("item", sa.String(40), nullable=False),
        _ts("read_at", True),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("user_id", "item"),
    )


def downgrade():
    for t in ("inbox_states", "budget_incidents", "budget_policies", "task_messages"):
        op.drop_table(t)
    with op.batch_alter_table("employee_tasks") as b:
        b.drop_index("ix_employee_tasks_project_id")
        b.drop_column("goal_id")
        b.drop_column("project_id")
    op.drop_table("projects")
    op.drop_table("goals")
