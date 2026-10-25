"""App Hangar: modelos de token sincronizados com o desktop (sync_desktop e modelos escondidos na conexão), a chave do
proxy de LLM por dispositivo (só o hash) e o uso sem agente em usage_events (time, dispositivo, conexão, cliente e
tokens de cache).

Revision ID: 0053_app_hangar_models
Revises: 0052_app_hangar
"""
import sqlalchemy as sa
from alembic import op

revision = "0053_app_hangar_models"
down_revision = "0052_app_hangar"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("llm_connections") as b:
        b.add_column(sa.Column("sync_desktop", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("desktop_hidden_models", sa.JSON(), nullable=True))
    with op.batch_alter_table("app_devices") as b:
        b.add_column(sa.Column("llm_key_hash", sa.String(64), nullable=True))
        b.add_column(sa.Column("llm_key_prefix", sa.String(16), nullable=False, server_default=""))
        b.add_column(sa.Column("llm_key_created_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_app_devices_llm_key_hash", "app_devices", ["llm_key_hash"], unique=True)
    with op.batch_alter_table("usage_events") as b:
        b.alter_column("agent_id", existing_type=sa.Integer(), nullable=True)
        b.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("device_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("connection_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("client", sa.String(40), nullable=False, server_default=""))
        b.add_column(sa.Column("tokens_cache_read", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("tokens_cache_write", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_usage_events_team_id", "usage_events", ["team_id"])


def downgrade():
    op.drop_index("ix_usage_events_team_id", "usage_events")
    op.execute("DELETE FROM usage_events WHERE agent_id IS NULL")
    with op.batch_alter_table("usage_events") as b:
        b.drop_column("tokens_cache_write")
        b.drop_column("tokens_cache_read")
        b.drop_column("client")
        b.drop_column("connection_id")
        b.drop_column("device_id")
        b.drop_column("team_id")
        b.alter_column("agent_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_app_devices_llm_key_hash", "app_devices")
    with op.batch_alter_table("app_devices") as b:
        b.drop_column("llm_key_created_at")
        b.drop_column("llm_key_prefix")
        b.drop_column("llm_key_hash")
    with op.batch_alter_table("llm_connections") as b:
        b.drop_column("desktop_hidden_models")
        b.drop_column("sync_desktop")
