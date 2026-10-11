"""Avisos da empresa (faixa no topo do portal e do console), com validade opcional.

Revision ID: 0028_announcements
Revises: 0027_external_employee
"""
import sqlalchemy as sa
from alembic import op

revision = "0028_announcements"
down_revision = "0027_external_employee"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1"),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("level", sa.String(8), nullable=False, server_default="info"),
        sa.Column("created_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_announcements_org_id", "announcements", ["org_id"])


def downgrade():
    op.drop_index("ix_announcements_org_id", "announcements")
    op.drop_table("announcements")
