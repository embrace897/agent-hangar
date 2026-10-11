"""Digital employee externo: o trabalho roda num worker seu, que busca as tarefas pelo heartbeat com um token próprio.

Revision ID: 0027_external_employee
Revises: 0026_multi_company
"""
import sqlalchemy as sa
from alembic import op

revision = "0027_external_employee"
down_revision = "0026_multi_company"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("runtime", sa.String(12), nullable=False, server_default="hangar"))
        b.add_column(sa.Column("worker_token_hash", sa.String(64), nullable=False, server_default=""))
        b.add_column(sa.Column("worker_token_hint", sa.String(12), nullable=False, server_default=""))
        b.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("worker_info", sa.JSON, nullable=True))
    op.create_index("ix_employees_worker_token_hash", "employees", ["worker_token_hash"])


def downgrade():
    op.drop_index("ix_employees_worker_token_hash", "employees")
    with op.batch_alter_table("employees") as b:
        for c in ("worker_info", "heartbeat_at", "worker_token_hint", "worker_token_hash", "runtime"):
            b.drop_column(c)
