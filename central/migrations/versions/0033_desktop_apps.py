"""Desktop governado (experimental): os apps de desktop que cada Digital employee pode abrir.

Revision ID: 0033_desktop_apps
Revises: 0032_judgement_watchers_value
"""
import sqlalchemy as sa
from alembic import op

revision = "0033_desktop_apps"
down_revision = "0032_judgement_watchers_value"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("desktop_apps", sa.JSON, nullable=True))


def downgrade():
    with op.batch_alter_table("employees") as b:
        b.drop_column("desktop_apps")
