"""Várias empresas numa instalação: org_id em todas as tabelas de uma empresa (as linhas existentes ficam na empresa 1),
slug, domínios e status na empresa, e os nomes que passam a ser únicos por empresa (skills, conexões de LLM, MCPs,
plugins, o catálogo de ações e o piso da alçada).

Revision ID: 0026_multi_company
Revises: 0025_org_skills_access
"""
import sqlalchemy as sa
from alembic import op

revision = "0026_multi_company"
down_revision = "0025_org_skills_access"
branch_labels = None
depends_on = None

TABLES = ["agent_versions", "test_runs", "deployments", "usage_events", "audit_log", "skills", "mcp_servers",
          "llm_connections", "jobs", "api_keys", "team_members", "access_requests", "promotion_requests", "schedules",
          "schedule_runs", "remote_mcps", "oauth_grants", "agent_lineage", "agent_guides", "employees", "authority_rules",
          "action_catalog", "org_authority_floor", "employee_tasks", "employee_task_events", "employee_task_checkpoints",
          "human_requests", "employee_routines", "employee_reports", "plugins", "plugin_installs", "plugin_calls",
          "task_messages", "budget_incidents", "inbox_states", "task_dependencies", "task_files", "employee_revisions",
          "skill_revisions", "skill_test_runs", "plugin_user_accounts", "secret_proposals"]
# (tabela, coluna, nome do unique por empresa, recriar o índice simples da coluna)
PER_ORG = [("skills", "name", "uq_skill_org_name", False), ("mcp_servers", "name", "uq_mcpserver_org_name", False),
           ("llm_connections", "name", "uq_llmconnection_org_name", False),
           ("action_catalog", "tool_ref", "uq_actioncatalog_org_tool_ref", True),
           ("org_authority_floor", "action_type", "uq_orgauthorityfloor_org_action_type", False),
           ("plugins", "name", "uq_plugin_org_name", True)]


def _per_org_unique(table, col, name, keep_index):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for ix in insp.get_indexes(table):  # unique=True com index=True vira um índice único: sai e volta como índice simples
        if ix["column_names"] == [col] and ix.get("unique") and not ix.get("duplicates_constraint"):  # o da constraint sai com ela
            op.drop_index(ix["name"], table_name=table)
    uqs = [u for u in insp.get_unique_constraints(table) if u["column_names"] == [col]]
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always",
                                  naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s",
                                                     "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}) as b:
            for u in uqs:
                b.drop_constraint(u["name"] or f"uq_{table}_{col}", type_="unique")
            b.create_unique_constraint(name, ["org_id", col])
    else:
        for u in uqs:
            op.drop_constraint(u["name"], table, type_="unique")
        op.create_unique_constraint(name, table, ["org_id", col])
    if keep_index:
        op.create_index(f"ix_{table}_{col}", table, [col])


def upgrade():
    for t in TABLES:
        with op.batch_alter_table(t) as b:
            b.add_column(sa.Column("org_id", sa.Integer, nullable=False, server_default="1"))
        op.create_index(f"ix_{t}_org_id", t, ["org_id"])
    with op.batch_alter_table("organizations") as b:
        b.add_column(sa.Column("slug", sa.String(80), nullable=False, server_default=""))
        b.add_column(sa.Column("domains", sa.JSON, nullable=True))
        b.add_column(sa.Column("status", sa.String(12), nullable=False, server_default="active"))
    op.execute("UPDATE organizations SET slug = 'principal' WHERE id = 1 AND slug = ''")
    for table, col, name, keep_index in PER_ORG:
        _per_org_unique(table, col, name, keep_index)


def downgrade():
    for table, col, name, keep_index in PER_ORG:
        if keep_index:
            op.drop_index(f"ix_{table}_{col}", table_name=table)
        with op.batch_alter_table(table) as b:
            b.drop_constraint(name, type_="unique")
            b.create_unique_constraint(f"uq_{table}_{col}", [col])
    with op.batch_alter_table("organizations") as b:
        for c in ("status", "domains", "slug"):
            b.drop_column(c)
    for t in TABLES:
        op.drop_index(f"ix_{t}_org_id", t)
        with op.batch_alter_table(t) as b:
            b.drop_column("org_id")
