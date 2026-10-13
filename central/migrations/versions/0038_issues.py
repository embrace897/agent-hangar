"""Issues (quadro kanban): status por categoria, etiquetas, issues com sub-issues e etapas, relações, comentários
em discussões, atividade e inscrições; a rodada do Digital employee aponta para a issue; rotina que abre issue.

Revision ID: 0038_issues
Revises: 0037_member_workspace
"""
import sqlalchemy as sa
from alembic import op

revision = "0038_issues"
down_revision = "0037_member_workspace"
branch_labels = None
depends_on = None

S = sa.String
TZ = sa.DateTime(timezone=True)


def _org():
    return sa.Column("org_id", sa.Integer, nullable=False, server_default="1")


def upgrade():
    with op.batch_alter_table("organizations") as b:
        b.add_column(sa.Column("issue_prefix", S(10), nullable=False, server_default="HGR"))
    with op.batch_alter_table("employee_tasks") as b:
        b.add_column(sa.Column("issue_id", sa.Integer, nullable=True))
        b.create_index("ix_employee_tasks_issue_id", ["issue_id"])
    with op.batch_alter_table("employee_routines") as b:
        b.add_column(sa.Column("creates_issue", sa.Boolean, nullable=False, server_default=sa.false()))
    op.create_table(
        "issue_statuses", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("key", S(40), nullable=False), sa.Column("name", S(80), nullable=False),
        sa.Column("category", S(12), nullable=False), sa.Column("color", S(9), nullable=False, server_default="#8793A1"),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("builtin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("org_id", "key", name="uq_issue_status_org_key"),
    )
    op.create_table(
        "issue_labels", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("name", S(60), nullable=False), sa.Column("color", S(9), nullable=False, server_default="#5B8DEF"),
        sa.Column("created_by", S(254), nullable=False, server_default=""),
        sa.UniqueConstraint("org_id", "name", name="uq_issue_label_org_name"),
    )
    op.create_table(
        "issues", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("number", sa.Integer, nullable=False), sa.Column("team_id", sa.Integer, nullable=True),
        sa.Column("project_id", sa.Integer, nullable=True), sa.Column("parent_id", sa.Integer, nullable=True),
        sa.Column("stage", sa.Integer, nullable=True), sa.Column("title", S(300), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("status", S(40), nullable=False, server_default="backlog"),
        sa.Column("priority", S(8), nullable=False, server_default="none"),
        sa.Column("assignee_user_id", sa.Integer, nullable=True), sa.Column("assignee_employee_id", sa.Integer, nullable=True),
        sa.Column("creator_user_id", sa.Integer, nullable=True), sa.Column("creator_employee_id", sa.Integer, nullable=True),
        sa.Column("labels", sa.JSON, nullable=True), sa.Column("start_date", TZ, nullable=True), sa.Column("due_date", TZ, nullable=True),
        sa.Column("estimate", sa.Integer, nullable=True), sa.Column("position", sa.Float, nullable=False, server_default="0"),
        sa.Column("routine_id", sa.Integer, nullable=True), sa.Column("created_at", TZ, nullable=True),
        sa.Column("updated_at", TZ, nullable=True), sa.Column("completed_at", TZ, nullable=True),
        sa.UniqueConstraint("org_id", "number", name="uq_issue_org_number"),
    )
    for c in ("org_id", "number", "team_id", "project_id", "parent_id", "status", "assignee_user_id", "assignee_employee_id",
              "updated_at"):
        op.create_index(f"ix_issues_{c}", "issues", [c])
    op.create_table(
        "issue_relations", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("issue_id", sa.Integer, nullable=False), sa.Column("kind", S(10), nullable=False),
        sa.Column("other_id", sa.Integer, nullable=False), sa.Column("created_at", TZ, nullable=True),
    )
    op.create_table(
        "issue_comments", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("issue_id", sa.Integer, nullable=False), sa.Column("parent_id", sa.Integer, nullable=True),
        sa.Column("author_user_id", sa.Integer, nullable=True), sa.Column("author_employee_id", sa.Integer, nullable=True),
        sa.Column("body", sa.Text, nullable=False, server_default=""), sa.Column("reactions", sa.JSON, nullable=True),
        sa.Column("resolved_at", TZ, nullable=True), sa.Column("resolved_by", S(254), nullable=False, server_default=""),
        sa.Column("edited_at", TZ, nullable=True), sa.Column("deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("task_id", sa.Integer, nullable=True), sa.Column("created_at", TZ, nullable=True),
    )
    op.create_table(
        "issue_activity", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("issue_id", sa.Integer, nullable=False), sa.Column("actor", S(254), nullable=False, server_default=""),
        sa.Column("actor_user_id", sa.Integer, nullable=True), sa.Column("kind", S(30), nullable=False),
        sa.Column("data", sa.JSON, nullable=True), sa.Column("created_at", TZ, nullable=True),
    )
    op.create_table(
        "issue_subscriptions", sa.Column("id", sa.Integer, primary_key=True), _org(),
        sa.Column("issue_id", sa.Integer, nullable=False), sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("created_at", TZ, nullable=True),
        sa.UniqueConstraint("issue_id", "user_id", name="uq_issue_sub"),
    )
    for t, cols in (("issue_statuses", ("org_id",)), ("issue_labels", ("org_id",)),
                    ("issue_relations", ("org_id", "issue_id", "other_id")),
                    ("issue_comments", ("org_id", "issue_id", "parent_id")),
                    ("issue_activity", ("org_id", "issue_id", "created_at")),
                    ("issue_subscriptions", ("org_id", "issue_id", "user_id"))):
        for c in cols:
            op.create_index(f"ix_{t}_{c}", t, [c])


def downgrade():
    for t in ("issue_subscriptions", "issue_activity", "issue_comments", "issue_relations", "issues", "issue_labels",
              "issue_statuses"):
        op.drop_table(t)
    with op.batch_alter_table("employee_routines") as b:
        b.drop_column("creates_issue")
    with op.batch_alter_table("employee_tasks") as b:
        b.drop_index("ix_employee_tasks_issue_id")
        b.drop_column("issue_id")
    with op.batch_alter_table("organizations") as b:
        b.drop_column("issue_prefix")
