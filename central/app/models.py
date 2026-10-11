"""Modelo de dados. Mudança de schema = nova migração em central/migrations/versions (Alembic)."""
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class OrgScoped:
    """A empresa dona do registro (várias empresas numa instalação; ver tenancy.py)."""
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text)
    final_output: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="single")  # single | multi
    owner: Mapped[str] = mapped_column(String(200), default="")  # contato (texto livre)
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    # private (só o time) | org (catálogo da empresa, uso sob pedido) | open (qualquer um da empresa usa)
    visibility: Mapped[str] = mapped_column(String(10), default="org")
    expose_spec: Mapped[bool] = mapped_column(Boolean, default=False)  # quem é de fora vê instruções/spec
    # draft | tested | test_failed | stage | production
    status: Mapped[str] = mapped_column(String(20), default="draft")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    versions = relationship("AgentVersion", cascade="all, delete-orphan", order_by="AgentVersion.version.desc()")
    tests = relationship("TestRun", cascade="all, delete-orphan", order_by="TestRun.id.desc()")
    deployments = relationship("Deployment", cascade="all, delete-orphan", order_by="Deployment.id.desc()")
    jobs = relationship("Job", cascade="all, delete-orphan", order_by="Job.id.desc()")


class AgentVersion(OrgScoped, Base):
    __tablename__ = "agent_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TestRun(OrgScoped, Base):
    __test__ = False
    __tablename__ = "test_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    version: Mapped[int] = mapped_column(Integer)
    env: Mapped[str] = mapped_column(String(20), default="stage")
    status: Mapped[str] = mapped_column(String(20))  # passed | failed
    summary: Mapped[str] = mapped_column(String(300), default="")
    results: Mapped[list] = mapped_column(JSON, default=list)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Deployment(OrgScoped, Base):
    __tablename__ = "deployments"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    version: Mapped[int] = mapped_column(Integer)
    env: Mapped[str] = mapped_column(String(20))  # stage | prod
    container_name: Mapped[str] = mapped_column(String(200), default="")
    container_id: Mapped[str] = mapped_column(String(80), default="")
    # running | failed | stopped | replaced | missing | exited
    status: Mapped[str] = mapped_column(String(20), default="starting")
    url: Mapped[str] = mapped_column(String(300), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageEvent(OrgScoped, Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    env: Mapped[str] = mapped_column(String(20), default="prod")
    channel: Mapped[str] = mapped_column(String(50), default="api")
    protocol: Mapped[str] = mapped_column(String(20), default="openai")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # quem consumiu (chave/sessão de usuário)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AuditLog(OrgScoped, Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Skill(OrgScoped, Base):
    """Habilidade reutilizável (peça de montar agentes): procedimento/conhecimento em markdown, com exemplos e um
    teste próprio que entra nos testes de quem a usa. `version` sobe a cada alteração."""
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_skill_org_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    examples: Mapped[str] = mapped_column(Text, default="")
    test: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # um TestCase da spec ({name, input, judge…})
    version: Mapped[int] = mapped_column(Integer, default=1)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | deprecated (não use em agentes novos)
    cases: Mapped[list | None] = mapped_column(JSON, nullable=True)  # entradas de teste salvas no Skill Studio


class McpServer(OrgScoped, Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_mcpserver_org_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    # só as ferramentas com este prefixo (ex.: "duckduckgo__" num gateway que agrega vários servidores)
    tool_prefix: Mapped[str] = mapped_column(String(100), default="")


class LlmConnection(OrgScoped, Base):
    """LLM externo (gateway LiteLLM corporativo, OpenRouter, provedor direto…). A plataforma nunca hospeda
    modelo: guarda url + model_name + chave (criptografada, ver crypto.py) e injeta no agente no deploy."""
    __tablename__ = "llm_connections"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_llmconnection_org_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(300))
    model_name: Mapped[str] = mapped_column(String(150))
    api_key: Mapped[str] = mapped_column(Text, default="")  # "enc:v1:…" — nunca exposta via API
    description: Mapped[str] = mapped_column(Text, default="")
    # openai (chat, codex, hermes) | anthropic (claude-code) | deepseek (deepseek-harness)
    protocol: Mapped[str] = mapped_column(String(20), default="openai")
    # Preço por 1M tokens (US$), para calcular custo por chamada. Opcional.
    price_in_per_mtok: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_out_per_mtok: Mapped[float | None] = mapped_column(Float, nullable=True)
    # aprovada para receber código (arquivos e saídas de terminal dos devs) quando a empresa usa code_policy=approved
    allow_code: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(OrgScoped, Base):
    """Uma execução de agente-com-harness: container efêmero, 1 por job, sem socket do Docker. O container
    roda a tarefa e chama de volta /internal/jobs/<id>/callback; nenhum container de harness fica ocioso."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    env: Mapped[str] = mapped_column(String(20), default="stage")
    harness_id: Mapped[str] = mapped_column(String(50), default="claude-code")
    connection: Mapped[str] = mapped_column(String(100), default="")
    task: Mapped[str] = mapped_column(Text)
    # queued | running | passed | failed | timeout | error | cancelled
    status: Mapped[str] = mapped_column(String(20), default="running")
    result: Mapped[str] = mapped_column(Text, default="")
    diff: Mapped[str] = mapped_column(Text, default="")
    logs: Mapped[str] = mapped_column(Text, default="")
    token: Mapped[str] = mapped_column(String(80), default="")  # segredo do callback — nunca exposto via API
    container_name: Mapped[str] = mapped_column(String(200), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # réplica da central que roda o worker do job (várias réplicas: cada uma só recupera os próprios órfãos)
    worker: Mapped[str] = mapped_column(String(100), default="")


class ApiKey(OrgScoped, Base):
    """Chave de API com escopo. Só o hash é guardado; o valor aparece uma única vez, na criação.
    scopes: ["admin"] (tudo) ou ["invoke"] (só chamar agentes pelo gateway /gw); agents limita o invoke
    a alguns slugs (lista vazia = todos)."""
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(20))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    agents: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # chave de conexão de uma ferramenta a um agente (aba "Conectar"): de qual cliente e em que modo
    client: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "mcp" | "model"
    # dono da chave: ela nunca pode mais do que ele, e morre com ele (desligamento, saída do time)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)


# ------------------------------------------------------------------ empresa, times, usuários e acesso
class Organization(Base):
    """A empresa. Uma por instalação hoje; org_id já existe nas tabelas para virar multiempresa depois."""
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    default_visibility: Mapped[str] = mapped_column(String(10), default="org")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")  # padrão dos agendamentos
    # any: qualquer conexão recebe código (modo agente de código); approved: só as conexões com allow_code
    code_policy: Mapped[str] = mapped_column(String(10), default="any")
    # decisões fora do portal: credenciais do app do Slack (criptografadas), hora do resumo diário, alertas de prazo
    notify: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    slug: Mapped[str] = mapped_column(String(80), default="")
    domains: Mapped[list | None] = mapped_column(JSON, nullable=True)  # e-mails desses domínios entram nesta empresa (SSO)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | suspended
    brand: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # identidade visual dos entregáveis: theme, accent, footer


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # produção exige aprovação de um mantenedor diferente de quem pediu (quatro olhos)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    budget_usd_month: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_enforce: Mapped[bool] = mapped_column(Boolean, default=False)  # estourou: o gateway recusa (429)
    budget_alerted: Mapped[str] = mapped_column(String(7), default="")  # "AAAA-MM" do último alerta
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)  # sempre minúsculo
    name: Mapped[str] = mapped_column(String(200), default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    org_role: Mapped[str] = mapped_column(String(10), default="member")  # admin | auditor | member
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)  # google | microsoft | github | oauth2
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)  # id do usuário no provedor
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)  # externalId do SCIM
    # conta local (usuário e senha), opcional: LOCAL_LOGIN=0 desliga. Só o hash scrypt é guardado.
    username: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # onde a pessoa recebe decisões: {channel: auto|slack|teams|email|off, teams_webhook (cripto), digest, last_digest}
    notify: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TeamMember(OrgScoped, Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(12), default="consumer")  # maintainer | developer | consumer
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | sso:<provedor> | scim
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UserSession(Base):
    """Sessão da UI (cookie HttpOnly). Só o hash do token fica no banco; revogável a qualquer momento.
    user_id nulo = sessão de emergência aberta com o ADMIN_TOKEN ou com uma chave admin."""
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    admin: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccessRequest(OrgScoped, Base):
    """Pedido de uso de um agente de outro time. approved = concessão ativa (revoked a encerra)."""
    __tablename__ = "access_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | approved | rejected | revoked
    decided_by: Mapped[str] = mapped_column(String(254), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromotionRequest(OrgScoped, Base):
    """Pedido de promoção para produção de uma versão testada. Aprovado por um mantenedor (ou admin)
    diferente de quem pediu; a versão fica fixada: se a spec mudar, o pedido perde a validade."""
    __tablename__ = "promotion_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    requested_by: Mapped[str] = mapped_column(String(254))
    requested_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | approved | rejected | superseded
    decided_by: Mapped[str] = mapped_column(String(254), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SsoProvider(Base):
    """Login OAuth2: Google, Microsoft Entra ID, GitHub ou um provedor OAuth2 genérico (Keycloak, Okta…).
    As variáveis OAUTH_<PROVEDOR>_* do ambiente valem como padrão; o que for salvo aqui tem prioridade."""
    __tablename__ = "sso_providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    client_id: Mapped[str] = mapped_column(String(300), default="")
    client_secret: Mapped[str] = mapped_column(Text, default="")  # "enc:v1:…"
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # tenant, allowed_domains, orgs, urls…
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class GroupMapping(Base):
    """Grupo externo -> time. provider: microsoft (id ou nome do grupo), github ("org/time"), oauth2
    (valor do claim de grupos) ou scim (displayName do grupo provisionado)."""
    __tablename__ = "group_mappings"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    external_group: Mapped[str] = mapped_column(String(300))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    role: Mapped[str] = mapped_column(String(12), default="consumer")


class ScimGroup(Base):
    __tablename__ = "scim_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(300))
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    members: Mapped[list] = mapped_column(JSON, default=list)  # ids de usuários
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)



# ------------------------------------------------------------------ agendamentos
class Schedule(OrgScoped, Base):
    """Execução agendada de um agente em produção: recorrente (cron de 5 campos, no fuso `timezone`) ou única
    (`run_at`). Enquanto o agente não está em produção, as execuções são puladas — o agendamento passa a valer
    quando ele sobe."""
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    cron: Mapped[str | None] = mapped_column(String(120), nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    message: Mapped[str] = mapped_column(Text)  # o que o agente recebe a cada disparo
    notify_url: Mapped[str] = mapped_column(String(500), default="")  # webhook (Slack/Teams/HTTP) com o resultado
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScheduleRun(OrgScoped, Base):
    __tablename__ = "schedule_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), index=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="schedule")  # schedule | manual | late
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | passed | failed | skipped
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    notify_status: Mapped[str] = mapped_column(String(200), default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)



class GatewayServer(Base):
    """Servidor do catálogo Docker MCP ativado no gateway (profile mcp-gateway). Os segredos ficam criptografados
    aqui; a central escreve registry.yaml / config.yaml / secrets.env no volume que o gateway observa (--watch)."""
    __tablename__ = "gateway_servers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secrets: Mapped[str] = mapped_column(Text, default="")  # "enc:v1:…" de um JSON {nome_do_segredo: valor}
    enabled_by: Mapped[str] = mapped_column(String(254), default="")
    enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RemoteMcp(OrgScoped, Base):
    """MCP remoto protegido por OAuth (Activepieces, Notion, Linear…): a central faz o OAuth uma vez (DCR + PKCE),
    guarda os tokens criptografados, renova sozinha e expõe o MCP aos agentes por um proxy interno — o agente nunca
    vê a credencial."""
    __tablename__ = "remote_mcps"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)  # também o nome no catálogo de MCPs
    url: Mapped[str] = mapped_column(String(500))  # endpoint MCP (como a central o alcança)
    browser_base: Mapped[str] = mapped_column(String(300), default="")  # origem pública da tela de autorização
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | connected | error
    oauth: Mapped[dict] = mapped_column(JSON, default=dict)  # endpoints descobertos, resource, scope, client_id
    client_secret: Mapped[str] = mapped_column(Text, default="")  # enc
    access_token: Mapped[str] = mapped_column(Text, default="")  # enc
    refresh_token: Mapped[str] = mapped_column(Text, default="")  # enc
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    connected_by: Mapped[str] = mapped_column(String(254), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


# ------------------------------------------------------------------ estado compartilhado entre réplicas
class Ephemeral(Base):
    """Valores curtos com validade (códigos de uso único, sessões de avaliação, tentativas de login). Fica no banco
    para que várias réplicas da central enxerguem o mesmo estado."""
    __tablename__ = "ephemeral"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


# ------------------------------------------------------------------ OAuth do MCP (Claude.ai, ChatGPT e outros)
class OAuthClient(Base):
    """App registrado (registro dinâmico, RFC 7591) que pode pedir acesso ao MCP da plataforma em nome de uma pessoa."""
    __tablename__ = "oauth_clients"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # client_id
    name: Mapped[str] = mapped_column(String(120), default="")
    redirect_uris: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthGrant(OrgScoped, Base):
    """Uma autorização (pessoa x app): token de acesso curto + refresh token rotativo. Só os hashes ficam no banco."""
    __tablename__ = "oauth_grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    access_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    previous_refresh_hash: Mapped[str] = mapped_column(String(64), default="", index=True)  # detecção de reuso
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ------------------------------------------------------------------ montagem a partir do catálogo
class AgentLineage(OrgScoped, Base):
    """De onde um agente montado veio: o plano, a base copiada e os especialistas que ele chama, com as versões em
    produção no momento da montagem. Só registra — os agentes de origem nunca são alterados."""
    __tablename__ = "agent_lineage"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(40), default="")
    mode: Mapped[str] = mapped_column(String(20), default="scratch")  # specialists | base | mixed | parts | scratch
    based_on: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {slug, version}
    specialists: Mapped[list] = mapped_column(JSON, default=list)  # [{slug, version}]
    skills: Mapped[list] = mapped_column(JSON, default=list)  # nomes reutilizados do catálogo
    new_skills: Mapped[list] = mapped_column(JSON, default=list)  # nomes criados na montagem
    tests_copied: Mapped[int] = mapped_column(Integer, default=0)
    reused_chars: Mapped[int] = mapped_column(Integer, default=0)  # texto aproveitado (≈ tokens × 4) que o LLM não gerou
    written_chars: Mapped[int] = mapped_column(Integer, default=0)  # texto novo que veio do pedido
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentGuide(OrgScoped, Base):
    """Guia de um agente para quem vai usá-lo: o que é, o que faz e como usar (Markdown). Fica fora da spec de
    propósito: corrigir a documentação não cria versão nova nem exige testes. Cada texto diz a versão que descreve;
    o rascunho gerado por LLM no ship fica marcado (`reviewed=False`) até um mantenedor revisar."""
    __tablename__ = "agent_guides"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)  # versão do agente que este texto descreve
    text: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="author")  # author | generated
    reviewed: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(254), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ------------------------------------------------------------------ Digital employee (F1)
class Employee(OrgScoped, Base):
    """Digital employee: um agente com cargo, gestor humano, alçada e caixa de tarefas. A alçada é aplicada pela
    plataforma (gate), nunca pelo prompt. Ciclo de vida: draft -> onboarding -> probation -> active <-> paused ->
    offboarded."""
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), unique=True, index=True)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    mission: Mapped[str] = mapped_column(Text, default="")
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)
    kpis: Mapped[list] = mapped_column(JSON, default=list)  # [{name, target, how}]
    systems: Mapped[list] = mapped_column(JSON, default=list)  # sistemas/ferramentas declarados pelo dono
    channels: Mapped[list] = mapped_column(JSON, default=list)  # portal | mcp | schedule | webhook
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manager_user_id: Mapped[int] = mapped_column(Integer)
    backup_user_ids: Mapped[list] = mapped_column(JSON, default=list)
    autonomy_level: Mapped[str] = mapped_column(String(12), default="intern")  # intern | junior | pleno | senior
    status: Mapped[str] = mapped_column(String(12), default="draft")
    working_hours: Mapped[str] = mapped_column(String(60), default="")
    task_budget_usd: Mapped[float] = mapped_column(Float, default=1.0)
    task_time_limit_min: Mapped[int] = mapped_column(Integer, default=30)
    report_webhook: Mapped[str] = mapped_column(String(500), default="")
    report_hour: Mapped[int] = mapped_column(Integer, default=18)
    last_report_on: Mapped[str] = mapped_column(String(10), default="")  # AAAA-MM-DD do último relatório diário
    report_weekday: Mapped[int] = mapped_column(Integer, default=0)  # relatório semanal: 0 = segunda … 6 = domingo; -1 = sem
    last_weekly_on: Mapped[str] = mapped_column(String(10), default="")
    webhook_token_hash: Mapped[str] = mapped_column(String(64), default="")  # sha256 do token do webhook de entrada
    webhook_token_hint: Mapped[str] = mapped_column(String(12), default="")  # fim do token, para reconhecê-lo
    lessons: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{id, text, source, added_by, at}]
    dismissed: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # sugestões dispensadas: {id: AAAA-MM-DD}
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)  # modo sombra: ações externas simuladas, não executadas
    career: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{at, from, to, by, reason}] mudanças de nível/sombra
    colleagues: Mapped[list | None] = mapped_column(JSON, nullable=True)  # slugs para quem ele pode repassar tarefas
    plan_policy: Mapped[str] = mapped_column(String(10), default="auto")  # off | auto (mostra) | approve (gestor aprova)
    recall: Mapped[bool] = mapped_column(Boolean, default=True)  # lembra trabalhos anteriores parecidos ao começar
    # caixa de e-mail (IMAP) como canal de entrada: {host, port, user, password (cripto), folder, allowed, last_check…}
    inbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_sample: Mapped[int] = mapped_column(Integer, default=20)  # % das tarefas concluídas que o juiz avalia (0 = nenhuma)
    review_policy: Mapped[str] = mapped_column(String(10), default="off")  # off | manager: todo resultado passa pelo gestor
    reports_to_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # o Digital employee chefe (organograma)
    # hangar: a plataforma roda o agente | external: um worker seu busca as tarefas pelo heartbeat (token ewk_…)
    runtime: Mapped[str] = mapped_column(String(12), default="hangar")
    worker_token_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    worker_token_hint: Mapped[str] = mapped_column(String(12), default="")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # o que o worker contou de si (nome, versão)
    disabled_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)  # grupos de ferramentas da plataforma desligados
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    hired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    offboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthorityRule(OrgScoped, Base):
    """Alçada do cargo para um tipo de ação: auto | notify | approve | approve_2 | never, com condições opcionais
    sobre os argumentos da ação. O piso da empresa (OrgAuthorityFloor) sempre vence quando é mais restritivo."""
    __tablename__ = "authority_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str] = mapped_column(String(30))
    mode: Mapped[str] = mapped_column(String(12))
    conditions: Mapped[list] = mapped_column(JSON, default=list)  # [{field, op, value}]
    approver: Mapped[str] = mapped_column(String(254), default="manager")  # manager | team_maintainer | user:<email>
    expires_in_min: Mapped[int] = mapped_column(Integer, default=240)
    note: Mapped[str] = mapped_column(Text, default="")


class ActionCatalog(OrgScoped, Base):
    """Classificação de cada ferramenta por tipo de ação e risco. Ferramenta nova ganha uma classificação automática
    (conservadora) marcada para o admin revisar."""
    __tablename__ = "action_catalog"
    __table_args__ = (UniqueConstraint("org_id", "tool_ref", name="uq_actioncatalog_org_tool_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tool_ref: Mapped[str] = mapped_column(String(300), index=True)
    action_type: Mapped[str] = mapped_column(String(30))
    risk: Mapped[int] = mapped_column(Integer, default=3)
    reversible: Mapped[bool] = mapped_column(Boolean, default=False)
    classified_by: Mapped[str] = mapped_column(String(254), default="auto")  # auto = a revisar pelo admin
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrgAuthorityFloor(OrgScoped, Base):
    """Piso da empresa por tipo de ação: nenhum cargo afrouxa. separation=True: quem pediu a tarefa não aprova."""
    __tablename__ = "org_authority_floor"
    __table_args__ = (UniqueConstraint("org_id", "action_type", name="uq_orgauthorityfloor_org_action_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    action_type: Mapped[str] = mapped_column(String(30))
    min_mode: Mapped[str] = mapped_column(String(12))
    separation: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str] = mapped_column(String(254), default="")


class EmployeeTask(OrgScoped, Base):
    """Trabalho entregue a um Digital employee. Roda em etapas com checkpoint; pausa em waiting_human quando uma ação
    precisa de decisão e retoma do checkpoint depois dela."""
    __tablename__ = "employee_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(20), default="portal")
    requester: Mapped[str] = mapped_column(String(254), default="")
    requester_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")  # tarefas de experiência: o resultado esperado
    probation: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 alta, 2 normal, 3 baixa
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_state: Mapped[str] = mapped_column(String(12), default="")  # "" | soon | overdue | escalated
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    worker: Mapped[str] = mapped_column(String(80), default="")
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # espera entre tentativas
    dedupe_key: Mapped[str] = mapped_column(String(200), default="")  # webhook: o mesmo evento não vira duas tarefas
    routine_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # repassada por outra tarefa
    waiting_on: Mapped[int | None] = mapped_column(Integer, nullable=True)  # espera esta tarefa repassada terminar
    plan: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{title, status: todo|doing|done|skipped, note}]
    plan_state: Mapped[str] = mapped_column(String(12), default="")  # "" | proposed | approved | rejected
    plan_approval: Mapped[bool] = mapped_column(Boolean, default=False)  # esta tarefa exige o plano aprovado
    origin: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # pedida pelo chat: {slack: {channel, ts}} | {teams: …}
    quality: Mapped[int | None] = mapped_column(Integer, nullable=True)  # nota 1-5 do resultado (juiz ou gestor)
    quality_note: Mapped[str] = mapped_column(Text, default="")
    quality_by: Mapped[str] = mapped_column(String(254), default="")  # "judge" ou o e-mail de quem avaliou
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # o projeto a que serve
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # meta explícita (senão, a do projeto)
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)  # o resultado passa por revisão antes de concluir
    reviewer_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # vazio = o gestor do funcionário
    extra_runs: Mapped[int] = mapped_column(Integer, default=0)  # rodadas a mais concedidas ao continuar uma tarefa
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskEvent(OrgScoped, Base):
    __tablename__ = "employee_task_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("employee_tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # created | run | tool | gate | human_request | decision | checkpoint | done | error | note
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskCheckpoint(OrgScoped, Base):
    __tablename__ = "employee_task_checkpoints"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("employee_tasks.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class HumanRequest(OrgScoped, Base):
    """Humano no circuito: aprovação de uma ação exata, pergunta, admissão ou aviso. Sem resposta no prazo, escala
    para o substituto; sem resposta de novo, expira em "não fazer"."""
    __tablename__ = "human_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20))  # approval | question | admission | notice
    action_type: Mapped[str] = mapped_column(String(30), default="")
    mode: Mapped[str] = mapped_column(String(12), default="approve")
    tool_ref: Mapped[str] = mapped_column(String(300), default="")
    tool_name: Mapped[str] = mapped_column(String(120), default="")
    action_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[int] = mapped_column(Integer, default=3)
    reversible: Mapped[bool] = mapped_column(Boolean, default=False)
    question: Mapped[str] = mapped_column(Text, default="")
    assigned_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    fallback_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open | decided | expired | cancelled
    approvals: Mapped[list] = mapped_column(JSON, default=list)  # approve_2: [{user_id, by, at}]
    decision: Mapped[str] = mapped_column(String(20), default="")  # approve | approve_edited | reject | instruct
    edited_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(254), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grant_hash: Mapped[str] = mapped_column(String(64), default="")  # a ação exata liberada (uma vez)
    grant_used: Mapped[bool] = mapped_column(Boolean, default=False)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)  # alerta de "vai expirar" já enviado
    chat_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)  # mensagens no Slack: [{channel, ts}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmployeeRoutine(OrgScoped, Base):
    """Trabalho recorrente de um Digital employee: a cada disparo do cron vira uma tarefa (só com ele ativo, e nunca
    duas ao mesmo tempo: se a anterior ainda está aberta, o disparo é pulado)."""
    __tablename__ = "employee_routines"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    cron: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(60), default="UTC")
    priority: Mapped[int] = mapped_column(Integer, default=2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmployeeReport(OrgScoped, Base):
    __tablename__ = "employee_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(Text, default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    sent_to: Mapped[str] = mapped_column(String(500), default="")
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Plugin(OrgScoped, Base):
    """Plugin declarativo: conecta um sistema à plataforma (ferramentas HTTP, credencial, tipos de ação para a alçada,
    configurações e skills). O rascunho (`manifest`) só vale depois de aprovado por outra pessoa (`approved_manifest`):
    editar cria um novo rascunho e as instalações seguem na versão aprovada até a próxima aprovação."""
    __tablename__ = "plugins"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_plugin_org_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(30), default="")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | pending | approved | rejected | disabled
    source: Mapped[str] = mapped_column(String(12), default="manual")  # manual | openapi
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # time dono (quem edita)
    approved_manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved_version: Mapped[str] = mapped_column(String(30), default="")
    approved_by: Mapped[str] = mapped_column(String(254), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    submitted_by: Mapped[str] = mapped_column(String(254), default="")
    test_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # testes em stage do rascunho (preso ao digest)
    category: Mapped[str] = mapped_column(String(40), default="other")  # vitrine
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)  # destaque escolhido por um admin
    history: Mapped[list | None] = mapped_column(JSON, nullable=True)  # versões aprovadas: [{version, by, at, note}]
    requests: Mapped[list | None] = mapped_column(JSON, nullable=True)  # pedidos de instalação: [{team, by, at, note}]
    signature: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # cosign da imagem aprovada: {ok, image, at, detail}
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PluginInstall(OrgScoped, Base):
    """Instalação de um plugin para um time (team_id) ou para a empresa toda (team_id vazio, só admin): configurações,
    segredos criptografados (nunca saem da central) e estatísticas de uso."""
    __tablename__ = "plugin_installs"
    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_id: Mapped[int] = mapped_column(ForeignKey("plugins.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    stage: Mapped[bool] = mapped_column(Boolean, default=False)  # instalação de teste do rascunho (credencial de teste)
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    secret: Mapped[str] = mapped_column(Text, default="")  # JSON criptografado: {credential, settings secretas}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {calls, errors, last_call, last_error}
    last_test: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    triggers: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {gatilho: {employee, enabled, priority, received}}
    hook_token_hash: Mapped[str] = mapped_column(String(64), default="")  # sha256 do token dos webhooks dos gatilhos
    hook_token_hint: Mapped[str] = mapped_column(String(12), default="")
    installed_by: Mapped[str] = mapped_column(String(254), default="")
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    rate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # chamadas por minuto (vazio = o do manifesto)
    tool_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {ferramenta: off | ask | allow}
    identity_mode: Mapped[str] = mapped_column(String(12), default="team")  # team | requester | prefer_requester


class PluginCall(OrgScoped, Base):
    """Uma chamada de ferramenta de plugin: quem (agente), qual ferramenta, quando, quanto tempo e com que resultado. Os
    argumentos e o trecho da resposta ficam com os segredos mascarados."""
    __tablename__ = "plugin_calls"
    __table_args__ = (Index("ix_plugin_calls_plugin_at", "plugin_id", "at"), Index("ix_plugin_calls_at", "at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_id: Mapped[int] = mapped_column(ForeignKey("plugins.id", ondelete="CASCADE"))
    install_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage: Mapped[bool] = mapped_column(Boolean, default=False)
    caller: Mapped[str] = mapped_column(String(120), default="")  # slug do agente, ou "stage-tests:<pessoa>"
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # tarefa do Digital employee, quando houver
    tool: Mapped[str] = mapped_column(String(80), default="")
    action: Mapped[str] = mapped_column(String(30), default="")
    status: Mapped[str] = mapped_column(String(12), default="ok")  # ok | error | limited
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    args: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Goal(Base):
    """Meta da empresa (sem time) ou de um time, em árvore. Projetos e tarefas apontam para a meta a que servem, e o
    agente recebe a cadeia de metas no prompt: sabe o porquê, não só o título."""
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active")  # planned | active | achieved | dropped
    target_date: Mapped[str] = mapped_column(String(10), default="")  # AAAA-MM-DD
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Project(Base):
    """Agrupa tarefas de um time em torno de um resultado: meta, instruções (entram no prompt de cada tarefa) e o
    Digital employee padrão."""
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | archived
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskMessage(OrgScoped, Base):
    """A conversa da tarefa: mensagens de pessoas (na fila enquanto o agente trabalha, entregues na próxima rodada) e
    as respostas do agente."""
    __tablename__ = "task_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("employee_tasks.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(254), default="")  # e-mail de quem escreveu, ou "agent"
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued | delivered | discarded | agent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BudgetPolicy(Base):
    """Orçamento com escopo (empresa, time, agente, Digital employee ou projeto) e janela (dia ou mês): aviso em
    warn_percent e, com hard_stop, o trabalho para até alguém autorizado liberar."""
    __tablename__ = "budget_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    scope_type: Mapped[str] = mapped_column(String(12))  # org | team | agent | project
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window: Mapped[str] = mapped_column(String(8), default="month")  # day | month
    amount_usd: Mapped[float] = mapped_column(Float)
    warn_percent: Mapped[int] = mapped_column(Integer, default=80)
    hard_stop: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BudgetIncident(OrgScoped, Base):
    """Um orçamento cruzou o aviso ou o limite numa janela. A parada fica aberta até alguém liberar (aumentar o
    orçamento ou liberar o resto da janela)."""
    __tablename__ = "budget_incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("budget_policies.id", ondelete="CASCADE"), index=True)
    window_key: Mapped[str] = mapped_column(String(10))  # AAAA-MM ou AAAA-MM-DD
    kind: Mapped[str] = mapped_column(String(10))  # warning | hard_stop
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | resolved
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_by: Mapped[str] = mapped_column(String(254), default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class InboxState(OrgScoped, Base):
    """Por pessoa: quando leu cada item da caixa de entrada (tarefa, decisão, incidente) e se arquivou. Arquivar é
    pessoal: não conclui nem cancela nada."""
    __tablename__ = "inbox_states"
    __table_args__ = (UniqueConstraint("user_id", "item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    item: Mapped[str] = mapped_column(String(40))  # task:12 | decision:5 | budget:3
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class TaskDependency(OrgScoped, Base):
    """A tarefa só começa quando as tarefas de que depende terminarem; os resultados delas chegam no prompt."""
    __tablename__ = "task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("employee_tasks.id", ondelete="CASCADE"), index=True)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("employee_tasks.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskFile(OrgScoped, Base):
    """Arquivo de uma tarefa: o que a pessoa anexou (input) ou o que o Digital employee entregou (output). O mesmo nome
    vira uma versão nova; as anteriores ficam."""
    __tablename__ = "task_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("employee_tasks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(8), default="input")  # input | output
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[bytes] = mapped_column(LargeBinary, default=b"")  # no banco: vale igual com várias réplicas (FILES_MAX_MB)
    uploaded_by: Mapped[str] = mapped_column(String(254), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmployeeRevision(OrgScoped, Base):
    """Cada mudança no cargo, na alçada ou nas configurações de um Digital employee: o retrato depois da mudança, quem
    mudou e quando. Restaurar aplica um retrato antigo como uma revisão nova."""
    __tablename__ = "employee_revisions"
    __table_args__ = (UniqueConstraint("employee_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(String(300), default="")
    changed_by: Mapped[str] = mapped_column(String(254), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SkillRevision(OrgScoped, Base):
    """Cada versão de uma skill (o conteúdo, os exemplos e o teste), para comparar e restaurar."""
    __tablename__ = "skill_revisions"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    examples: Mapped[str] = mapped_column(Text, default="")
    test: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    changed_by: Mapped[str] = mapped_column(String(254), default="")
    note: Mapped[str] = mapped_column(String(300), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SkillTestRun(OrgScoped, Base):
    """Uma rodada dos casos de teste de uma skill numa versão: dá para comparar versões."""
    __tablename__ = "skill_test_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    connection: Mapped[str] = mapped_column(String(100), default="")
    results: Mapped[list] = mapped_column(JSON, default=list)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    by: Mapped[str] = mapped_column(String(254), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PluginUserAccount(OrgScoped, Base):
    """A conta pessoal de uma pessoa num plugin OAuth2: com identity_mode=requester, o Digital employee usa a conta de
    quem pediu a tarefa (e não a do time)."""
    __tablename__ = "plugin_user_accounts"
    __table_args__ = (UniqueConstraint("plugin_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_id: Mapped[int] = mapped_column(ForeignKey("plugins.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    secret: Mapped[str] = mapped_column(Text, default="")  # JSON criptografado: {oauth: {access_token, refresh_token…}}
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Secret(Base):
    """Segredo central (da empresa ou de um time), referenciado como secret://nome. O valor é criptografado e nunca volta
    pela API; quem usa e quando fica registrado."""
    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("team_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(254), default="")
    updated_by: Mapped[str] = mapped_column(String(254), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_by: Mapped[str] = mapped_column(String(200), default="")


class SecretProposal(OrgScoped, Base):
    """Quem não administra segredos propõe um valor novo; quem administra aprova (sem ver o valor antigo)."""
    __tablename__ = "secret_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    proposed_by: Mapped[str] = mapped_column(String(254), default="")
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | approved | rejected
    decided_by: Mapped[str] = mapped_column(String(254), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Announcement(OrgScoped, Base):
    """Aviso da empresa para todo mundo (faixa no topo do portal e do console), com validade opcional."""
    __tablename__ = "announcements"
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(8), default="info")  # info | warn
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ------------------------------------------------------------------ fase E: dados, documentos e números
class ExtractionSchema(OrgScoped, Base):
    """Os campos a extrair de um tipo de documento (contrato, nota fiscal, boleto…), com tipo, regra e o limiar de
    confiança abaixo do qual uma pessoa confere."""
    __tablename__ = "extraction_schemas"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_extractionschema_org_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fields: Mapped[list] = mapped_column(JSON, default=list)  # [{name, label, type, required, pattern, options, hint}]
    min_confidence: Mapped[float] = mapped_column(Float, default=0.8)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | archived
    created_by: Mapped[str] = mapped_column(String(254), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Extraction(OrgScoped, Base):
    """Os campos tirados de um arquivo da tarefa: valor, confiança, o trecho de origem e quem corrigiu."""
    __tablename__ = "extractions"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("employee_tasks.id", ondelete="CASCADE"), index=True)
    schema_id: Mapped[int] = mapped_column(Integer, index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    file_name: Mapped[str] = mapped_column(String(200))
    file_version: Mapped[int] = mapped_column(Integer, default=1)
    values: Mapped[dict] = mapped_column(JSON, default=dict)  # {campo: {value, confidence, evidence, page, valid, error}}
    status: Mapped[str] = mapped_column(String(10), default="auto")  # auto | review | reviewed | rejected
    request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corrections: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Calculator(OrgScoped, Base):
    """Calculadora certificada: entradas, passos com fórmulas (linguagem segura, Decimal) e casos de teste. Só a versão
    certificada — por outra pessoa, com todos os casos passando — é usada pelos Digital employees."""
    __tablename__ = "calculators"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_calculator_org_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inputs: Mapped[list] = mapped_column(JSON, default=list)  # [{name, label, type, unit}]
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{name, expr, label}]
    outputs: Mapped[list] = mapped_column(JSON, default=list)  # nomes dos passos que são resultado
    cases: Mapped[list] = mapped_column(JSON, default=list)  # [{name, inputs, expected: {saida: valor}, tolerance}]
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | certified | deprecated
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)  # a tarefa que usa passa por revisão humana
    edited_by: Mapped[str] = mapped_column(String(254), default="")
    certified: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {version, inputs, steps, outputs, by, at}
    last_test: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {version, passed, failed, at, results}
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CalcRun(OrgScoped, Base):
    """Uma conta feita por uma calculadora certificada: a memória de cálculo que a auditoria pede."""
    __tablename__ = "calc_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    calculator_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    trace: Mapped[list] = mapped_column(JSON, default=list)  # [{name, expr, value}]
    by: Mapped[str] = mapped_column(String(254), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
