"""Fase F: calendário útil da empresa, filas do time e itens para pessoas (com anexos), playbooks e execuções,
modelos de mensagem, conversas com gente de fora e reuniões.

Revision ID: 0030_processes_queues_outside
Revises: 0029_data_docs_numbers
"""
import sqlalchemy as sa
from alembic import op

revision = "0030_processes_queues_outside"
down_revision = "0029_data_docs_numbers"
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
    with op.batch_alter_table("organizations") as b:
        b.add_column(sa.Column("calendar", sa.JSON, nullable=True))
    op.create_table(
        "work_queues", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("slug", S(80), nullable=False), sa.Column("name", S(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""), sa.Column("team_id", sa.Integer, nullable=False),
        sa.Column("categories", sa.JSON, nullable=False), sa.Column("default_category", S(80), nullable=False, server_default=""),
        sa.Column("intake_token_hash", S(64), nullable=False, server_default=""),
        sa.Column("intake_token_hint", S(12), nullable=False, server_default=""),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
        sa.UniqueConstraint("org_id", "slug", name="uq_workqueue_org_slug"),
    )
    _idx("work_queues", "team_id")
    op.create_table(
        "work_items", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("queue_id", sa.Integer, nullable=True), sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("title", S(300), nullable=False), sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("category", S(80), nullable=False, server_default=""), sa.Column("priority", sa.Integer, nullable=False, server_default="2"),
        sa.Column("status", S(12), nullable=False, server_default="open"),
        sa.Column("assignee_user_id", sa.Integer, nullable=True), sa.Column("employee_task_id", sa.Integer, nullable=True),
        sa.Column("prepared", sa.Text, nullable=False, server_default=""), sa.Column("handled_by", S(12), nullable=False, server_default=""),
        sa.Column("source", S(20), nullable=False, server_default="portal"), sa.Column("source_ref", S(120), nullable=False, server_default=""),
        sa.Column("requester", S(254), nullable=False, server_default=""), sa.Column("requester_user_id", sa.Integer, nullable=True),
        sa.Column("due_at", TZ, nullable=True), sa.Column("sla_due_at", TZ, nullable=True),
        sa.Column("sla_breached", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("evidence", S(10), nullable=False, server_default="none"), sa.Column("result", sa.Text, nullable=False, server_default=""),
        sa.Column("approved_by", S(254), nullable=False, server_default=""),
        sa.Column("playbook_run_id", sa.Integer, nullable=True), sa.Column("step_key", S(80), nullable=False, server_default=""),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
        sa.Column("started_at", TZ, nullable=True), sa.Column("done_at", TZ, nullable=True),
    )
    _idx("work_items", "queue_id", "team_id", "status", "assignee_user_id", "employee_task_id", "playbook_run_id")
    op.create_table(
        "work_item_files", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("item_id", sa.Integer, sa.ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", S(200), nullable=False), sa.Column("content_type", S(120), nullable=False, server_default="application/octet-stream"),
        sa.Column("size", sa.Integer, nullable=False, server_default="0"), sa.Column("data", sa.LargeBinary, nullable=False),
        sa.Column("uploaded_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
    )
    _idx("work_item_files", "item_id")
    op.create_table(
        "playbooks", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", S(80), nullable=False), sa.Column("title", S(200), nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""), sa.Column("team_id", sa.Integer, nullable=False),
        sa.Column("steps", sa.JSON, nullable=False), sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", S(10), nullable=False, server_default="active"), sa.Column("schedule", S(60), nullable=False, server_default=""),
        sa.Column("run_title", S(200), nullable=False, server_default=""), sa.Column("next_run_at", TZ, nullable=True),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("updated_at", TZ, nullable=True),
        sa.UniqueConstraint("org_id", "name", name="uq_playbook_org_name"),
    )
    _idx("playbooks", "team_id", "next_run_at")
    op.create_table(
        "playbook_runs", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("playbook_id", sa.Integer, nullable=False), sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("title", S(300), nullable=False), sa.Column("anchor", S(10), nullable=False),
        sa.Column("status", S(10), nullable=False, server_default="running"), sa.Column("steps", sa.JSON, nullable=False),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
        sa.Column("finished_at", TZ, nullable=True),
    )
    _idx("playbook_runs", "playbook_id")
    op.create_table(
        "message_templates", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", S(80), nullable=False), sa.Column("channel", S(10), nullable=False, server_default="any"),
        sa.Column("subject", S(300), nullable=False, server_default=""), sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("wa_template", S(120), nullable=False, server_default=""), sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("status", S(10), nullable=False, server_default="draft"), sa.Column("approved_by", S(254), nullable=False, server_default=""),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("updated_at", TZ, nullable=True),
        sa.UniqueConstraint("org_id", "name", name="uq_messagetemplate_org_name"),
    )
    _idx("message_templates")
    op.create_table(
        "external_threads", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("employee_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.Integer, nullable=False), sa.Column("channel", S(10), nullable=False, server_default="email"),
        sa.Column("contact_name", S(200), nullable=False, server_default=""), sa.Column("contact", S(254), nullable=False),
        sa.Column("subject", S(300), nullable=False, server_default=""), sa.Column("token", S(16), nullable=False),
        sa.Column("status", S(10), nullable=False, server_default="open"),
        sa.Column("wait_reply", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("follow_up_days", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_follow_ups", sa.Integer, nullable=False, server_default="2"),
        sa.Column("follow_ups_sent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_follow_up_at", TZ, nullable=True), sa.Column("pending_request_id", sa.Integer, nullable=True),
        sa.Column("promises", sa.JSON, nullable=True), sa.Column("created_at", TZ, nullable=True),
        sa.Column("last_out_at", TZ, nullable=True), sa.Column("last_in_at", TZ, nullable=True),
    )
    _idx("external_threads", "task_id", "employee_id", "contact", "token", "next_follow_up_at")
    op.create_table(
        "external_messages", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("thread_id", sa.Integer, sa.ForeignKey("external_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", S(4), nullable=False), sa.Column("subject", S(300), nullable=False, server_default=""),
        sa.Column("body", sa.Text, nullable=False, server_default=""), sa.Column("template", S(80), nullable=False, server_default=""),
        sa.Column("status", S(10), nullable=False, server_default="sent"), sa.Column("error", S(300), nullable=False, server_default=""),
        sa.Column("external_id", S(200), nullable=False, server_default=""), sa.Column("by", S(254), nullable=False, server_default=""),
        sa.Column("at", TZ, nullable=True),
    )
    _idx("external_messages", "thread_id")
    op.create_table(
        "meetings", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("title", S(300), nullable=False), sa.Column("series", S(120), nullable=False, server_default=""),
        sa.Column("starts_at", TZ, nullable=True), sa.Column("ends_at", TZ, nullable=True),
        sa.Column("organizer", S(254), nullable=False, server_default=""), sa.Column("attendees", sa.JSON, nullable=False),
        sa.Column("agenda", sa.Text, nullable=False, server_default=""), sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("employee_id", sa.Integer, nullable=True), sa.Column("prep_hours", sa.Integer, nullable=False, server_default="0"),
        sa.Column("prep_task_id", sa.Integer, nullable=True), sa.Column("provider", S(12), nullable=False, server_default=""),
        sa.Column("external_id", S(300), nullable=False, server_default=""), sa.Column("transcript", sa.Text, nullable=False, server_default=""),
        sa.Column("minutes", sa.Text, nullable=False, server_default=""), sa.Column("decisions", sa.JSON, nullable=True),
        sa.Column("minutes_task_id", sa.Integer, nullable=True), sa.Column("status", S(12), nullable=False, server_default="scheduled"),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("created_at", TZ, nullable=True),
    )
    _idx("meetings", "series", "team_id")


def downgrade():
    for t in ("meetings", "external_messages", "external_threads", "message_templates", "playbook_runs", "playbooks",
              "work_item_files", "work_items", "work_queues"):
        op.drop_table(t)
    with op.batch_alter_table("organizations") as b:
        b.drop_column("calendar")
