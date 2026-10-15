"""Spec do agente: schema tipado (Pydantic) e atualização por JSON Merge Patch (RFC 7396).

Regras do merge (design_agent): objetos são mesclados recursivamente, listas e escalares são
substituídos, e `null` APAGA o campo. Isso resolve o caso real em que um `llm.model` antigo sobrevivia a
uma troca de `llm.connection` — agora basta mandar `{"llm": {"connection": "x", "model": null}}`.
"""
import copy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

HarnessId = Literal["claude-code", "codex", "hermes", "deepseek-harness"]
BuiltinToolName = Literal["calculator", "current_time", "platform_dashboard"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmEnvOverride(_Strict):
    connection: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)


class LlmSpec(_Strict):
    connection: str = ""
    model: str = ""
    temperature: float = Field(default=0.2, ge=0, le=2)
    # opcionais (ausentes = padrão do runtime), para não mudar a spec de agentes existentes
    max_steps: int | None = Field(default=None, ge=1, le=40,
                                  description="Rodadas máximas de chamadas de tool por mensagem (padrão 6)")
    vision: bool | None = Field(default=None,
                                description="false: imagens anexadas não vão ao LLM, só ao workspace (padrão true)")
    redact_secrets: bool | None = Field(
        default=None, description="false: não mascara segredos (chaves, tokens) nas saídas das ferramentas do cliente "
                                  "antes de irem ao LLM (padrão true)")
    client_tools: bool | None = Field(
        default=None, description="false: ignora as `tools` enviadas pelo cliente (padrão true: o chat do VS Code, "
                                  "Cline, Continue… executam arquivos e terminal localmente e o agente as usa)")
    stage: LlmEnvOverride | None = None
    prod: LlmEnvOverride | None = None


class HarnessEnvOverride(_Strict):
    id: HarnessId | None = None
    connection: str | None = None
    model: str | None = None


class HarnessSpec(_Strict):
    id: HarnessId
    connection: str = ""
    model: str = Field(default="", description="modelo da conexão (vazio = o padrão da conexão)")
    stage: HarnessEnvOverride | None = None
    prod: HarnessEnvOverride | None = None


class ExecutionSpec(_Strict):
    """Como o modelo trabalha em cada chamada. Ausente = automático: o esforço vem do papel do agente e só vai ao
    modelo que tem modo de raciocínio. A central traduz para o runtime de chat e para cada harness."""
    effort: Literal["auto", "minimal", "low", "medium", "high", "max"] | None = Field(
        default=None, description="esforço de raciocínio (auto = pelo papel: planejador e código alto, triagem mínimo)")
    max_output_tokens: int | None = Field(default=None, ge=256, le=128_000, description="limite de tokens de saída")
    parallel_tools: bool | None = Field(
        default=None, description="false: uma ferramenta por vez (padrão: as de servidores diferentes rodam juntas)")
    prompt_cache: bool | None = Field(default=None, description="false: não usa cache do prompt (padrão: usa)")
    max_turns: int | None = Field(default=None, ge=1, le=500, description="harness: rodadas máximas do CLI")


class JudgeSpec(_Strict):
    """Conexão (protocolo openai) usada pelos casos de teste com `judge`. Sem ela, usa a do próprio agente."""
    connection: str
    model: str = ""


class HttpTool(_Strict):
    type: Literal["http"] = "http"
    name: str
    description: str = ""
    url: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})


class BuiltinTool(_Strict):
    type: Literal["builtin"]
    name: BuiltinToolName


Tool = Annotated[HttpTool | BuiltinTool, Field(discriminator="type")]


class InlineSkill(_Strict):
    name: str
    description: str = ""
    content: str


class InlineMcp(_Strict):
    name: str
    url: str
    tool_prefix: str = ""  # só as ferramentas com este prefixo (gateway que agrega vários servidores)


class MemorySpec(_Strict):
    """Memória de longo prazo (profile memory): o agente ganha as tools memory__recall e memory__remember."""
    scope: Literal["agent", "team", "org"] = Field(
        default="agent", description="agent: só este agente; team: compartilhada com o time; org: com a empresa")
    write: bool = Field(default=True, description="false: só consulta (não grava fatos novos)")


class Workspace(_Strict):
    """Avaliação de código: o agente recebe este mini-projeto num sandbox efêmero, trabalha nele com ferramentas de
    arquivo e terminal (como no VS Code) e o caso só passa se `check` sair com 0 e nada em `protected` mudar."""
    files: dict[str, str] = Field(description="caminho relativo -> conteúdo")
    check: str = Field(description="comando de verificação, na raiz do projeto (ex.: python3 -m unittest)")
    protected: list[str] = Field(default_factory=list, description="arquivos que o agente não pode alterar (ex.: testes)")
    max_rounds: int = Field(default=30, ge=1, le=60)

    @field_validator("files")
    @classmethod
    def _safe_paths(cls, v):
        if not v:
            raise ValueError("workspace.files não pode ser vazio")
        if sum(len(c) for c in v.values()) > 200_000:
            raise ValueError("workspace.files passa de 200 KB")
        for p in v:
            parts = p.replace("\\", "/").split("/")
            if p.startswith(("/", "~")) or ".." in parts or not p.strip():
                raise ValueError(f"caminho inválido em workspace.files: {p!r} (use caminhos relativos)")
        return v


class TestCase(_Strict):
    name: str = ""
    input: str
    workspace: Workspace | None = None
    expect_contains: str | None = None
    expect_regex: str | None = None
    judge: str | None = Field(default=None, description="Rubrica avaliada por um LLM (LLM-as-judge)")
    timeout_s: int | None = Field(default=None, ge=5, le=900)


class AgentSpec(_Strict):
    llm: LlmSpec = Field(default_factory=LlmSpec)
    harness: HarnessSpec | None = None
    execution: ExecutionSpec | None = None
    judge: JudgeSpec | None = None
    instructions: str = ""
    skills: list[str | InlineSkill] = Field(default_factory=list)
    mcps: list[str | InlineMcp] = Field(default_factory=list)
    tools: list[Tool] = Field(default_factory=list)
    sub_agents: list[str] = Field(default_factory=list)
    tests: list[TestCase] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    memory: MemorySpec | None = None
    plugins: list[str] = Field(default_factory=list, description="plugins aprovados e instalados para o time do agente")

    @field_validator("tools", mode="before")
    @classmethod
    def _default_tool_type(cls, v):
        # tools http antigas vinham sem "type"; o discriminador exige o campo
        return [({"type": "http", **t} if isinstance(t, dict) and "type" not in t else t) for t in (v or [])]

    @model_validator(mode="after")
    def _chat_xor_harness(self):
        if self.harness and self.sub_agents:
            raise ValueError("um agente com harness não pode ter sub_agents (harness não orquestra); use um "
                             "agente de chat como orquestrador e o agente-com-harness como sub_agent dele")
        if self.harness and self.llm.connection:
            raise ValueError("defina `llm` (agente de chat) OU `harness` (agente executor), não os dois — "
                             "para trocar de tipo, mande o outro campo como null")
        return self


SPEC_KEYS = frozenset(AgentSpec.model_fields)
LEGACY_LLM_KEYS = ("provider", "base_url", "api_key_env")


def default_spec() -> dict:
    return AgentSpec().model_dump(exclude_none=True)


def merge_patch(target, patch):
    """RFC 7396: dict mescla recursivamente; None apaga; qualquer outro valor substitui."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            result.pop(k, None)
        else:
            result[k] = merge_patch(result.get(k), v)
    return result


def normalize_legacy(spec: dict) -> dict:
    """Specs gravadas por versões antigas têm campos que o schema atual não aceita."""
    spec = copy.deepcopy(spec or {})
    llm = spec.get("llm")
    if isinstance(llm, dict):
        for k in LEGACY_LLM_KEYS:
            llm.pop(k, None)
    return spec


class SpecError(ValueError):
    pass


def validate(spec: dict) -> dict:
    """Valida e devolve a spec normalizada (sem campos nulos). Erros viram mensagens legíveis."""
    try:
        return AgentSpec.model_validate(normalize_legacy(spec)).model_dump(exclude_none=True)
    except ValidationError as e:
        msgs = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "spec"
            msgs.append(f"{loc}: {err['msg']}")
        raise SpecError("spec inválida — " + "; ".join(msgs)) from None


def json_schema() -> dict:
    return AgentSpec.model_json_schema()
