"""Fase B: dependências entre tarefas e revisão antes de concluir, arquivos das tarefas (anexos e entregáveis, com
versões), rodadas extras ao continuar uma tarefa e o histórico de revisões do cargo.

Revision ID: 0024_deps_files_revisions
Revises: 0023_goals_threads_budgets_inbox
"""
import sqlalchemy as sa
from alembic import op

revision = "0024_deps_files_revisions"
down_revision = "0023_goals_threads_budgets_inbox"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("employee_tasks") as b:
        b.add_column(sa.Column("review_required", sa.Boolean, nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("reviewer_user_id", sa.Integer, nullable=True))
        b.add_column(sa.Column("extra_runs", sa.Integer, nullable=False, server_default="0"))
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("review_policy", sa.String(10), nullable=False, server_default="off"))
    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("employee_tasks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("depends_on_id", sa.Integer, sa.ForeignKey("employee_tasks.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("created_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "depends_on_id"),
    )
    op.create_table(
        "task_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("employee_tasks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("kind", sa.String(8), nullable=False, server_default="input"),
        sa.Column("content_type", sa.String(120), nullable=False, server_default="application/octet-stream"),
        sa.Column("size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("data", sa.LargeBinary, nullable=False),
        sa.Column("uploaded_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "employee_revisions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("employee_id", sa.Integer, sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=False),
        sa.Column("summary", sa.String(300), nullable=False, server_default=""),
        sa.Column("changed_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("employee_id", "version"),
    )


def downgrade():
    for t in ("employee_revisions", "task_files", "task_dependencies"):
        op.drop_table(t)
    with op.batch_alter_table("employees") as b:
        b.drop_column("review_policy")
    with op.batch_alter_table("employee_tasks") as b:
        for c in ("extra_runs", "reviewer_user_id", "review_required"):
            b.drop_column(c)
