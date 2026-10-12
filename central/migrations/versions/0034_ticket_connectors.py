"""Sistemas de chamados (Jira, Linear, Asana, ServiceNow) como porta de entrada das filas.

Revision ID: 0034_ticket_connectors
Revises: 0033_desktop_apps
"""
import sqlalchemy as sa
from alembic import op

revision = "0034_ticket_connectors"
down_revision = "0033_desktop_apps"
branch_labels = None
depends_on = None

S = sa.String
TZ = sa.DateTime(timezone=True)


def _org():
    return sa.Column("org_id", sa.Integer, nullable=False, server_default="1")


def upgrade():
    op.create_table(
        "ticket_connectors", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", S(80), nullable=False), sa.Column("title", S(200), nullable=False, server_default=""),
        sa.Column("kind", S(20), nullable=False), sa.Column("base_url", S(300), nullable=False, server_default=""),
        sa.Column("auth", sa.JSON, nullable=False), sa.Column("config", sa.JSON, nullable=False),
        sa.Column("queue_id", sa.Integer, nullable=False), sa.Column("team_id", sa.Integer, nullable=False),
        sa.Column("status", S(10), nullable=False, server_default="draft"),
        sa.Column("poll_minutes", sa.Integer, nullable=False, server_default="5"),
        sa.Column("cursor", S(40), nullable=False, server_default=""),
        sa.Column("next_poll_at", TZ, nullable=True), sa.Column("last_sync_at", TZ, nullable=True),
        sa.Column("last_error", S(500), nullable=False, server_default=""),
        sa.Column("webhook_token_hash", S(64), nullable=False, server_default=""),
        sa.Column("stats", sa.JSON, nullable=False), sa.Column("created_by", S(254), nullable=False, server_default=""),
        sa.Column("approved_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
        sa.UniqueConstraint("org_id", "name", name="uq_ticketconnector_org_name"),
    )
    for c in ("org_id", "queue_id", "team_id"):
        op.create_index(f"ix_ticket_connectors_{c}", "ticket_connectors", [c])
    op.create_table(
        "ticket_links", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("connector_id", sa.Integer, sa.ForeignKey("ticket_connectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", S(120), nullable=False), sa.Column("external_key", S(120), nullable=False, server_default=""),
        sa.Column("url", S(500), nullable=False, server_default=""), sa.Column("work_item_id", sa.Integer, nullable=False),
        sa.Column("pushed", sa.JSON, nullable=False), sa.Column("remote_updated", S(40), nullable=False, server_default=""),
        sa.Column("created_at", TZ, nullable=True),
        sa.UniqueConstraint("connector_id", "external_id", name="uq_ticketlink_connector_ext"),
    )
    for c in ("org_id", "connector_id", "work_item_id"):
        op.create_index(f"ix_ticket_links_{c}", "ticket_links", [c])


def downgrade():
    op.drop_table("ticket_links")
    op.drop_table("ticket_connectors")
