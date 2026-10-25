"""App Hangar: a fila curta de eventos do app (GET /api/app/events, SSE), lida por todas as réplicas.

Revision ID: 0054_app_events
Revises: 0053_app_hangar_models
"""
import sqlalchemy as sa
from alembic import op

revision = "0054_app_events"
down_revision = "0053_app_hangar_models"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("ref", sa.String(60), nullable=False, server_default=""),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_app_events_org_id", "app_events", ["org_id"])
    op.create_index("ix_app_events_user_id", "app_events", ["user_id"])
    op.create_index("ix_app_events_created_at", "app_events", ["created_at"])


def downgrade():
    op.drop_index("ix_app_events_created_at", "app_events")
    op.drop_index("ix_app_events_user_id", "app_events")
    op.drop_index("ix_app_events_org_id", "app_events")
    op.drop_table("app_events")
