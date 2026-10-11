"""Fase G: base de conhecimento com permissões herdadas, navegador governado (domínios e procedimentos), governança de
dados (classificação, DLP, retenção, pedidos do titular e barreiras de informação).

Revision ID: 0031_knowledge_browser_gov
Revises: 0030_processes_queues_outside
"""
import sqlalchemy as sa
from alembic import op

revision = "0031_knowledge_browser_gov"
down_revision = "0030_processes_queues_outside"
branch_labels = None
depends_on = None

S = sa.String
TZ = sa.DateTime(timezone=True)


def _org():
    return sa.Column("org_id", sa.Integer, nullable=False, server_default="1")


def _idx(table, *cols):
    for c in ("org_id", *cols):
        op.create_index(f"ix_{table}_{c}", table, [c])


def _cls():
    return sa.Column("classification", S(12), nullable=False, server_default="internal")


def upgrade():
    with op.batch_alter_table("organizations") as b:
        b.add_column(sa.Column("governance", sa.JSON, nullable=True))
    with op.batch_alter_table("projects") as b:
        b.add_column(_cls())
        b.add_column(sa.Column("legal_hold", sa.Boolean, nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("confidential", sa.Boolean, nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("members", sa.JSON, nullable=True))
    with op.batch_alter_table("employee_tasks") as b:
        b.add_column(_cls())
        b.add_column(sa.Column("legal_hold", sa.Boolean, nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("purged_at", TZ, nullable=True))
    with op.batch_alter_table("task_files") as b:
        b.add_column(_cls())
    with op.batch_alter_table("llm_connections") as b:
        b.add_column(sa.Column("max_classification", S(12), nullable=False, server_default="confidential"))
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("browser_domains", sa.JSON, nullable=True))
    op.create_table(
        "knowledge_sources", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", S(80), nullable=False), sa.Column("title", S(200), nullable=False, server_default=""),
        sa.Column("kind", S(12), nullable=False, server_default="upload"), sa.Column("config", sa.JSON, nullable=True),
        sa.Column("acl_mode", S(8), nullable=False, server_default="team"), sa.Column("team_ids", sa.JSON, nullable=True),
        sa.Column("owner_user_id", sa.Integer, nullable=True), sa.Column("review_days", sa.Integer, nullable=False, server_default="365"),
        _cls(), sa.Column("status", S(10), nullable=False, server_default="active"),
        sa.Column("last_sync_at", TZ, nullable=True), sa.Column("last_error", S(300), nullable=False, server_default=""),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
        sa.UniqueConstraint("org_id", "name", name="uq_knowledgesource_org_name"),
    )
    _idx("knowledge_sources")
    op.create_table(
        "knowledge_docs", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", S(300), nullable=False, server_default=""), sa.Column("title", S(300), nullable=False),
        sa.Column("url", S(1000), nullable=False, server_default=""), sa.Column("acl", sa.JSON, nullable=True),
        sa.Column("updated_at", TZ, nullable=True), sa.Column("review_by", S(10), nullable=False, server_default=""),
        sa.Column("content_hash", S(64), nullable=False, server_default=""), sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("meta", sa.JSON, nullable=True),
    )
    _idx("knowledge_docs", "source_id", "external_id")
    op.create_table(
        "knowledge_chunks", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("doc_id", sa.Integer, sa.ForeignKey("knowledge_docs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idx", sa.Integer, nullable=False, server_default="0"), sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("norm", sa.Text, nullable=False, server_default=""),
    )
    _idx("knowledge_chunks", "doc_id")
    op.create_table(
        "knowledge_gaps", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("query", S(500), nullable=False), sa.Column("task_id", sa.Integer, nullable=True),
        sa.Column("asked_by", S(254), nullable=False, server_default=""), sa.Column("best_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()), sa.Column("at", TZ, nullable=True),
    )
    _idx("knowledge_gaps")
    op.create_table(
        "procedures", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", S(80), nullable=False), sa.Column("title", S(200), nullable=False, server_default=""),
        sa.Column("team_id", sa.Integer, nullable=True), sa.Column("start_url", S(1000), nullable=False, server_default=""),
        sa.Column("domains", sa.JSON, nullable=False), sa.Column("steps", sa.JSON, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("updated_at", TZ, nullable=True),
        sa.UniqueConstraint("org_id", "name", name="uq_procedure_org_name"),
    )
    _idx("procedures")
    op.create_table(
        "dlp_tokens", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("employee_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", S(40), nullable=False), sa.Column("value", sa.Text, nullable=False),
    )
    _idx("dlp_tokens", "task_id")
    op.create_table(
        "subject_requests", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("subject", S(300), nullable=False), sa.Column("kind", S(8), nullable=False, server_default="access"),
        sa.Column("status", S(10), nullable=False, server_default="open"), sa.Column("report", sa.JSON, nullable=True),
        sa.Column("requested_by", S(254), nullable=False, server_default=""), sa.Column("approved_by", S(254), nullable=False, server_default=""),
        sa.Column("note", sa.Text, nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
        sa.Column("done_at", TZ, nullable=True),
    )
    _idx("subject_requests")


def downgrade():
    for t in ("subject_requests", "dlp_tokens", "procedures", "knowledge_gaps", "knowledge_chunks", "knowledge_docs",
              "knowledge_sources"):
        op.drop_table(t)
    for table, cols in (("employees", ["browser_domains"]), ("llm_connections", ["max_classification"]),
                        ("task_files", ["classification"]), ("employee_tasks", ["purged_at", "legal_hold", "classification"]),
                        ("projects", ["members", "confidential", "legal_hold", "classification"]), ("organizations", ["governance"])):
        with op.batch_alter_table(table) as b:
            for c in cols:
                b.drop_column(c)
