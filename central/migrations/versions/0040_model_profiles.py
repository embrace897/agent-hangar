"""Fichas dos modelos (para que cada um é bom, notas por área e fontes) e a configuração da pesquisa por empresa.

Revision ID: 0040_model_profiles
Revises: 0039_llm_models
"""
import sqlalchemy as sa
from alembic import op

revision = "0040_model_profiles"
down_revision = "0039_llm_models"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("organizations") as b:
        b.add_column(sa.Column("model_research", sa.JSON, nullable=True))
    op.create_table(
        "model_profiles", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1", index=True),
        sa.Column("key", sa.String(200), nullable=False), sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("strengths", sa.JSON, nullable=True), sa.Column("weaknesses", sa.JSON, nullable=True),
        sa.Column("best_for", sa.JSON, nullable=True), sa.Column("scores", sa.JSON, nullable=True),
        sa.Column("metrics", sa.JSON, nullable=True), sa.Column("sources", sa.JSON, nullable=True),
        sa.Column("locked", sa.JSON, nullable=True), sa.Column("structured_at", TZ, nullable=True),
        sa.Column("researched_at", TZ, nullable=True),
        sa.Column("research_status", sa.String(10), nullable=False, server_default=""),
        sa.Column("research_error", sa.Text, nullable=False, server_default=""),
        sa.Column("research_cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", TZ, nullable=True),
        sa.UniqueConstraint("org_id", "key", name="uq_modelprofile_org_key"),
    )


def downgrade():
    op.drop_table("model_profiles")
    with op.batch_alter_table("organizations") as b:
        b.drop_column("model_research")
