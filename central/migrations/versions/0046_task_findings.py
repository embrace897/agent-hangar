"""Tarefas com resultado "sem achados": o laço que não encontrou nada fecha em silêncio (finding = false); um achado
traz um resumo curto (finding_summary). Nulo = tarefa comum, que não declarou nada.

Revision ID: 0046_task_findings
Revises: 0045_issue_removed
"""
import sqlalchemy as sa
from alembic import op

revision = "0046_task_findings"
down_revision = "0045_issue_removed"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("employee_tasks") as b:
        b.add_column(sa.Column("finding", sa.Boolean(), nullable=True))
        b.add_column(sa.Column("finding_summary", sa.Text(), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("employee_tasks") as b:
        b.drop_column("finding_summary")
        b.drop_column("finding")
