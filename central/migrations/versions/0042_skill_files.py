"""Skills como pacote: arquivos além do SKILL.md (references, scripts, assets) e a data da última atualização.

Revision ID: 0042_skill_files
Revises: 0041_connectors
"""
import sqlalchemy as sa
from alembic import op

revision = "0042_skill_files"
down_revision = "0041_connectors"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("skills") as b:
        b.add_column(sa.Column("files", sa.JSON, nullable=True))
        b.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("skill_revisions") as b:
        b.add_column(sa.Column("files", sa.JSON, nullable=True))


def downgrade():
    with op.batch_alter_table("skill_revisions") as b:
        b.drop_column("files")
    with op.batch_alter_table("skills") as b:
        b.drop_column("updated_at")
        b.drop_column("files")
