"""Fase C: organograma (chefe de cada Digital employee), Skill Studio (revisões, casos de teste e rodadas), política por
ação e identidade de quem pede nos plugins (contas pessoais), e o cofre central de segredos com propostas.

Revision ID: 0025_org_skills_access
Revises: 0024_deps_files_revisions
"""
import sqlalchemy as sa
from alembic import op

revision = "0025_org_skills_access"
down_revision = "0024_deps_files_revisions"
branch_labels = None
depends_on = None


def _ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("reports_to_id", sa.Integer, nullable=True))
    with op.batch_alter_table("skills") as b:
        b.add_column(sa.Column("status", sa.String(12), nullable=False, server_default="active"))
        b.add_column(sa.Column("cases", sa.JSON, nullable=True))
    with op.batch_alter_table("plugin_installs") as b:
        b.add_column(sa.Column("tool_policy", sa.JSON, nullable=True))
        b.add_column(sa.Column("identity_mode", sa.String(12), nullable=False, server_default="team"))
    op.create_table(
        "skill_revisions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("skill_id", sa.Integer, sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("examples", sa.Text, nullable=False, server_default=""),
        sa.Column("test", sa.JSON, nullable=True),
        sa.Column("changed_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        _ts("at"),
        sa.UniqueConstraint("skill_id", "version"),
    )
    op.create_table(
        "skill_test_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("skill_id", sa.Integer, sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("connection", sa.String(100), nullable=False, server_default=""),
        sa.Column("results", sa.JSON, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("by", sa.String(254), nullable=False, server_default=""),
        _ts("at"),
    )
    op.create_table(
        "plugin_user_accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plugin_id", sa.Integer, sa.ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.Integer, nullable=False, index=True),
        sa.Column("secret", sa.Text, nullable=False, server_default=""),
        _ts("connected_at"),
        sa.UniqueConstraint("plugin_id", "user_id"),
    )
    op.create_table(
        "secrets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1", index=True),
        sa.Column("team_id", sa.Integer, nullable=True, index=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(254), nullable=False, server_default=""),
        _ts("updated_at"),
        sa.Column("uses", sa.Integer, nullable=False, server_default="0"),
        _ts("last_used_at", True),
        sa.Column("last_used_by", sa.String(200), nullable=False, server_default=""),
        sa.UniqueConstraint("team_id", "name"),
    )
    op.create_table(
        "secret_proposals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("proposed_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("decided_by", sa.String(254), nullable=False, server_default=""),
        _ts("decided_at", True),
        _ts("created_at"),
    )


def downgrade():
    for t in ("secret_proposals", "secrets", "plugin_user_accounts", "skill_test_runs", "skill_revisions"):
        op.drop_table(t)
    with op.batch_alter_table("plugin_installs") as b:
        b.drop_column("identity_mode")
        b.drop_column("tool_policy")
    with op.batch_alter_table("skills") as b:
        b.drop_column("cases")
        b.drop_column("status")
    with op.batch_alter_table("employees") as b:
        b.drop_column("reports_to_id")
