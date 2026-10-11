"""Fase E: ferramentas da plataforma por funcionário, identidade visual dos entregáveis, esquemas de extração e
extrações conferidas, calculadoras certificadas e a memória de cada conta.

Revision ID: 0029_data_docs_numbers
Revises: 0028_announcements
"""
import sqlalchemy as sa
from alembic import op

revision = "0029_data_docs_numbers"
down_revision = "0028_announcements"
branch_labels = None
depends_on = None


def _org():
    return sa.Column("org_id", sa.Integer, nullable=False, server_default="1")


def upgrade():
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("disabled_tools", sa.JSON, nullable=True))
    with op.batch_alter_table("organizations") as b:
        b.add_column(sa.Column("brand", sa.JSON, nullable=True))
    op.create_table(
        "extraction_schemas",
        sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("fields", sa.JSON, nullable=False),
        sa.Column("min_confidence", sa.Float, nullable=False, server_default="0.8"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("org_id", "name", name="uq_extractionschema_org_name"),
    )
    op.create_index("ix_extraction_schemas_org_id", "extraction_schemas", ["org_id"])
    op.create_table(
        "extractions",
        sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("employee_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_id", sa.Integer, nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("file_name", sa.String(200), nullable=False),
        sa.Column("file_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("values", sa.JSON, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("request_id", sa.Integer, nullable=True),
        sa.Column("corrections", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reviewed_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    for c in ("org_id", "task_id", "schema_id"):
        op.create_index(f"ix_extractions_{c}", "extractions", [c])
    op.create_table(
        "calculators",
        sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("inputs", sa.JSON, nullable=False),
        sa.Column("steps", sa.JSON, nullable=False),
        sa.Column("outputs", sa.JSON, nullable=False),
        sa.Column("cases", sa.JSON, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("requires_review", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("edited_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("certified", sa.JSON, nullable=True),
        sa.Column("last_test", sa.JSON, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("org_id", "name", name="uq_calculator_org_name"),
    )
    op.create_index("ix_calculators_org_id", "calculators", ["org_id"])
    op.create_table(
        "calc_runs",
        sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("calculator_id", sa.Integer, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("task_id", sa.Integer, nullable=True),
        sa.Column("inputs", sa.JSON, nullable=False),
        sa.Column("outputs", sa.JSON, nullable=False),
        sa.Column("trace", sa.JSON, nullable=False),
        sa.Column("by", sa.String(254), nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=True),
    )
    for c in ("org_id", "calculator_id", "task_id"):
        op.create_index(f"ix_calc_runs_{c}", "calc_runs", [c])


def downgrade():
    for t in ("calc_runs", "calculators", "extractions", "extraction_schemas"):
        op.drop_table(t)
    with op.batch_alter_table("organizations") as b:
        b.drop_column("brand")
    with op.batch_alter_table("employees") as b:
        b.drop_column("disabled_tools")
