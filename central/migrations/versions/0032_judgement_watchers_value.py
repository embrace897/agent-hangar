"""Fase H: memorando de decisão (revisor crítico e resultado), vigias, modo par e trilhas de aprendizado, linha de base
de valor (ROI) e tarefas de ouro com rodadas de regressão.

Revision ID: 0032_judgement_watchers_value
Revises: 0031_knowledge_browser_gov
"""
import sqlalchemy as sa
from alembic import op

revision = "0032_judgement_watchers_value"
down_revision = "0031_knowledge_browser_gov"
branch_labels = None
depends_on = None

S = sa.String
TZ = sa.DateTime(timezone=True)


def _org():
    return sa.Column("org_id", sa.Integer, nullable=False, server_default="1")


def _idx(table, *cols):
    for c in ("org_id", *cols):
        op.create_index(f"ix_{table}_{c}", table, [c])


def upgrade():
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("critic_mode", S(8), nullable=False, server_default="off"))
        b.add_column(sa.Column("regression_mode", S(8), nullable=False, server_default="auto"))
    with op.batch_alter_table("employee_tasks") as b:
        b.add_column(sa.Column("mode", S(8), nullable=False, server_default="work"))
    op.create_table(
        "decision_memos", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("task_id", sa.Integer, nullable=True), sa.Column("employee_id", sa.Integer, nullable=True),
        sa.Column("question", sa.Text, nullable=False, server_default=""), sa.Column("options", sa.JSON, nullable=False),
        sa.Column("criteria", sa.JSON, nullable=False), sa.Column("scores", sa.JSON, nullable=False), sa.Column("totals", sa.JSON, nullable=False),
        sa.Column("recommendation", S(200), nullable=False, server_default=""), sa.Column("confidence", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("risks", sa.JSON, nullable=False), sa.Column("citations", sa.JSON, nullable=False), sa.Column("critic", sa.JSON, nullable=True),
        sa.Column("revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("decided_option", S(200), nullable=False, server_default=""), sa.Column("decided_by", S(254), nullable=False, server_default=""),
        sa.Column("decided_note", sa.Text, nullable=False, server_default=""), sa.Column("decided_at", TZ, nullable=True),
        sa.Column("follow_up_at", TZ, nullable=True), sa.Column("follow_up_item_id", sa.Integer, nullable=True),
        sa.Column("outcome", S(8), nullable=False, server_default=""), sa.Column("outcome_note", sa.Text, nullable=False, server_default=""),
        sa.Column("outcome_at", TZ, nullable=True), sa.Column("created_at", TZ, nullable=True),
    )
    _idx("decision_memos", "task_id", "employee_id", "follow_up_at")
    op.create_table(
        "watchers", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", S(200), nullable=False), sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("employee_id", sa.Integer, nullable=True), sa.Column("source", sa.JSON, nullable=False),
        sa.Column("condition", sa.JSON, nullable=False), sa.Column("action", sa.JSON, nullable=False),
        sa.Column("schedule", S(60), nullable=False, server_default="0 * * * *"), sa.Column("state", sa.JSON, nullable=True),
        sa.Column("next_run_at", TZ, nullable=True), sa.Column("last_checked_at", TZ, nullable=True), sa.Column("last_fired_at", TZ, nullable=True),
        sa.Column("last_change", sa.Text, nullable=False, server_default=""), sa.Column("last_error", S(300), nullable=False, server_default=""),
        sa.Column("fired", sa.Integer, nullable=False, server_default="0"), sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
    )
    _idx("watchers", "team_id", "next_run_at")
    op.create_table(
        "learning_tracks", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", S(200), nullable=False), sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("source", S(120), nullable=False, server_default=""), sa.Column("questions", sa.JSON, nullable=False),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
    )
    _idx("learning_tracks", "team_id")
    op.create_table(
        "learning_attempts", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("track_id", sa.Integer, sa.ForeignKey("learning_tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, nullable=False), sa.Column("answers", sa.JSON, nullable=False),
        sa.Column("score", sa.Float, nullable=False, server_default="0"), sa.Column("at", TZ, nullable=True),
    )
    _idx("learning_attempts", "track_id", "user_id")
    op.create_table(
        "value_baselines", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("scope", S(160), nullable=False), sa.Column("minutes", sa.Float, nullable=False, server_default="30"),
        sa.Column("hourly_cost", sa.Float, nullable=False, server_default="0"),
        sa.Column("updated_by", S(254), nullable=False, server_default=""), sa.Column("updated_at", TZ, nullable=True),
    )
    _idx("value_baselines", "scope")
    op.create_table(
        "golden_tasks", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("employee_id", sa.Integer, nullable=False), sa.Column("title", S(300), nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""), sa.Column("expected", sa.Text, nullable=False, server_default=""),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
    )
    _idx("golden_tasks", "employee_id")
    op.create_table(
        "regression_runs", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("employee_id", sa.Integer, nullable=False), sa.Column("trigger", S(200), nullable=False, server_default="manual"),
        sa.Column("revision", sa.Integer, nullable=True), sa.Column("status", S(10), nullable=False, server_default="running"),
        sa.Column("results", sa.JSON, nullable=False), sa.Column("request_id", sa.Integer, nullable=True),
        sa.Column("created_at", TZ, nullable=True), sa.Column("finished_at", TZ, nullable=True),
    )
    _idx("regression_runs", "employee_id")


def downgrade():
    for t in ("regression_runs", "golden_tasks", "value_baselines", "learning_attempts", "learning_tracks", "watchers",
              "decision_memos"):
        op.drop_table(t)
    with op.batch_alter_table("employee_tasks") as b:
        b.drop_column("mode")
    with op.batch_alter_table("employees") as b:
        b.drop_column("regression_mode")
        b.drop_column("critic_mode")
