"""Revisão por um modelo de outro fornecedor: o bloco `review` numa regra de alçada (authority_rules.review) e o
veredito do revisor guardado no pedido de decisão (human_requests.review).

Revision ID: 0047_cross_review
Revises: 0046_task_findings
"""
import sqlalchemy as sa
from alembic import op

revision = "0047_cross_review"
down_revision = "0046_task_findings"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("authority_rules") as b:
        b.add_column(sa.Column("review", sa.JSON(), nullable=True))
    with op.batch_alter_table("human_requests") as b:
        b.add_column(sa.Column("review", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("human_requests") as b:
        b.drop_column("review")
    with op.batch_alter_table("authority_rules") as b:
        b.drop_column("review")
