"""Provedores de LLM com a lista de modelos: a conexão sabe de qual provedor é, e cada modelo que o provedor oferece
fica guardado com tipo de saída, contexto e preço (atualizado a cada hora).

Revision ID: 0039_llm_models
Revises: 0038_issues
"""
import sqlalchemy as sa
from alembic import op

revision = "0039_llm_models"
down_revision = "0038_issues"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)

# conexões que já existiam: o provedor sai do endereço
GUESS = [("openrouter.ai", "openrouter"), ("api.openai.com", "openai"), ("api.anthropic.com", "anthropic"),
         ("generativelanguage.googleapis.com", "gemini"), ("api.deepseek.com", "deepseek"), (":4000", "litellm"),
         (":11434", "ollama"), ("api.groq.com", "groq"), ("api.mistral.ai", "mistral"), ("api.x.ai", "xai")]


def upgrade():
    with op.batch_alter_table("llm_connections") as b:
        b.add_column(sa.Column("provider", sa.String(40), nullable=False, server_default="custom"))
        b.add_column(sa.Column("models_synced_at", TZ, nullable=True))
        b.add_column(sa.Column("models_error", sa.Text, nullable=False, server_default=""))
    conn = op.get_bind()
    for cid, url in conn.execute(sa.text("SELECT id, base_url FROM llm_connections")).fetchall():
        prov = next((p for frag, p in GUESS if frag in (url or "")), "custom")
        conn.execute(sa.text("UPDATE llm_connections SET provider = :p WHERE id = :i"), {"p": prov, "i": cid})
    op.create_table(
        "llm_models", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1", index=True),
        sa.Column("connection_id", sa.Integer, sa.ForeignKey("llm_connections.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("model_id", sa.String(200), nullable=False), sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False, server_default="text"),
        sa.Column("kind_source", sa.String(10), nullable=False, server_default="name"),
        sa.Column("caps", sa.JSON, nullable=True), sa.Column("context_length", sa.Integer, nullable=True),
        sa.Column("price_in_per_mtok", sa.Float, nullable=True), sa.Column("price_out_per_mtok", sa.Float, nullable=True),
        sa.Column("price_source", sa.String(10), nullable=False, server_default=""),
        sa.Column("available", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("manual", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("first_seen", TZ, nullable=True), sa.Column("last_seen", TZ, nullable=True),
        sa.UniqueConstraint("connection_id", "model_id", name="uq_llmmodel_conn_model"),
    )


def downgrade():
    op.drop_table("llm_models")
    with op.batch_alter_table("llm_connections") as b:
        b.drop_column("models_error")
        b.drop_column("models_synced_at")
        b.drop_column("provider")
