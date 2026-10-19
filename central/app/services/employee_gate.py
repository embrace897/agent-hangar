"""Digital employee: classificação das ações e motor de alçada.

Toda ferramenta que um Digital employee chama passa por aqui (via /internal/gate, chamado pelo runtime do agente
antes de executar). A decisão é da plataforma, não do prompt:

  modo efetivo = o mais restritivo entre (regra do cargo, ou o padrão do nível de autonomia) e o piso da empresa.

auto      executa
notify    executa e avisa o gestor (fica na linha do tempo e no relatório)
approve   para e pede a decisão de uma pessoa (a ação exata, com o porquê)
approve_2 para e exige duas pessoas diferentes
never     não executa nunca

Uma regra auto/notify pode ter um bloco `review`: um modelo de outro fornecedor confere a ação antes; se ele não
concordar, vale o `on_disagree` dela (services/cross_review.py).
"""
import hashlib
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ActionCatalog, AuthorityRule, Employee, OrgAuthorityFloor, now

MODES = ("auto", "notify", "approve", "approve_2", "never")
RANK = {m: i for i, m in enumerate(MODES)}
ACTION_TYPES = ("read", "delegate", "write_internal", "run_code", "send_external", "speak_for_company", "publish",
                "financial", "delete", "prod_change")
# run_code: delegar uma tarefa a um agente com harness (Claude Code, Codex…), que roda um job de código num sandbox
RISK = {"read": 1, "delegate": 2, "write_internal": 3, "run_code": 4, "send_external": 4, "speak_for_company": 4,
        "publish": 4, "financial": 5, "delete": 5, "prod_change": 5}
# padrão de cada nível de autonomia quando o cargo não tem regra para o tipo de ação (o piso ainda vale por cima)
AUTONOMY_DEFAULTS = {
    "intern": {"read": "auto", "delegate": "notify"},
    "junior": {"read": "auto", "delegate": "auto", "write_internal": "notify"},
    "pleno": {"read": "auto", "delegate": "auto", "write_internal": "auto"},
    "senior": {"read": "auto", "delegate": "auto", "write_internal": "auto", "run_code": "notify", "send_external": "notify"},
}
OPS = (">", ">=", "<", "<=", "==", "!=", "contains", "not_contains", "domain_not")

_READ = re.compile(r"(^|_)(get|list|read|search|query|fetch|find|describe|show|lookup|recall|view|count|status)(_|$)")
_SEND = re.compile(r"(^|_)(send|email|mail|message|post_message|notify|reply|tweet|sms|chat_post)(_|$)")
_DELETE = re.compile(r"(^|_)(delete|remove|drop|destroy|purge|erase|archive)(_|$)")
_MONEY = re.compile(r"(^|_)(pay|payment|charge|refund|transfer|invoice|payout|purchase|order)(_|$)")
_PUBLISH = re.compile(r"(^|_)(publish|deploy|release|merge|push)(_|$)")


def auto_classify(ref: str) -> tuple[str, bool]:
    """(tipo de ação, revisar?) para uma ferramenta sem classificação. revisar=True: a classificação é um palpite e vai
    para a fila do admin."""
    kind, _, rest = ref.partition(":")
    if kind == "builtin":
        return "read", False
    if kind == "agent" or ref in ("hangar:handoff_task", "hangar:delegate_issue"):  # repassar trabalho a um colega
        return "delegate", False
    if kind == "http":
        name, _, method = rest.rpartition(":")
        method = method.upper()
        if method in ("GET", "DELETE"):
            return {"GET": "read", "DELETE": "delete"}[method], False
        return _by_name(name.lower(), read=False), True  # POST/PUT/PATCH: o nome diz o risco; o admin revisa
    if ref == "mcp:memory:recall":
        return "read", False
    if ref == "mcp:memory:remember":
        return "write_internal", False
    return _by_name(rest.rsplit(":", 1)[-1].lower()), True


def _by_name(name: str, read: bool = True) -> str:
    rules = [(_MONEY, "financial"), (_DELETE, "delete"), (_PUBLISH, "publish"), (_SEND, "send_external")]
    for rx, t in rules + ([(_READ, "read")] if read else []):
        if rx.search(name):
            return t
    return "write_internal"


def _harness_agent(db: Session, ref: str) -> bool:
    """agent:<slug> de um agente com harness: a chamada vira um job de código, não uma conversa."""
    if not ref.startswith("agent:"):
        return False
    from ..models import Agent
    from .common import spec_of
    a = db.scalar(select(Agent).where(Agent.slug == ref.split(":", 1)[1]))
    return bool(a and spec_of(a).get("harness"))


def classify(db: Session, ref: str) -> ActionCatalog:
    row = db.scalar(select(ActionCatalog).where(ActionCatalog.tool_ref == ref))
    if row is None:
        t, review = auto_classify(ref)
        if t == "delegate" and _harness_agent(db, ref):
            t = "run_code"
        row = ActionCatalog(tool_ref=ref, action_type=t, risk=RISK[t], reversible=False,
                            classified_by="auto" if review else "auto:rule", updated_at=now())
        db.add(row)
        db.commit()
    elif row.action_type == "delegate" and row.classified_by.startswith("auto") and _harness_agent(db, ref):
        row.action_type, row.risk, row.classified_by, row.updated_at = "run_code", RISK["run_code"], "auto:rule", now()
        db.commit()  # o agente delegado passou a ter harness: a chamada roda código
    return row


def floor_of(db: Session) -> dict[str, OrgAuthorityFloor]:
    return {f.action_type: f for f in db.scalars(select(OrgAuthorityFloor))}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _field(args: dict, path: str):
    cur = args
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def condition_matches(cond: dict, args: dict) -> bool:
    v, op, target = _field(args, cond.get("field", "")), cond.get("op", "=="), cond.get("value")
    if op in (">", ">=", "<", "<="):
        a, b = _num(v), _num(target)
        if a is None or b is None:
            return True  # sem como comparar: na dúvida, a regra (mais restritiva) vale
        return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]
    s, t = "" if v is None else str(v).lower(), "" if target is None else str(target).lower()
    if op == "==":
        return s == t
    if op == "!=":
        return s != t
    if op == "contains":
        return t in s
    if op == "not_contains":
        return t not in s
    if op == "domain_not":  # algum destinatário fora do domínio da empresa
        found = re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", s)
        return not found or any(d != t.lstrip("@") for d in found)
    return True


def validate_rule(r: dict) -> dict:
    t, m = r.get("action_type"), r.get("mode")
    if t not in ACTION_TYPES:
        raise ValueError(f"action_type inválido: {t!r} (use {', '.join(ACTION_TYPES)})")
    if m not in MODES:
        raise ValueError(f"mode inválido: {m!r} (use {', '.join(MODES)})")
    conds = r.get("conditions") or []
    for c in conds:
        if c.get("op") not in OPS or not c.get("field"):
            raise ValueError(f"condição inválida: {c} (field + op em {', '.join(OPS)} + value)")
    approver = r.get("approver") or "manager"
    if approver not in ("manager", "team_maintainer") and not approver.startswith("user:"):
        raise ValueError("approver: manager | team_maintainer | user:<e-mail>")
    from .cross_review import normalize
    return {"action_type": t, "mode": m, "conditions": conds, "approver": approver,
            "expires_in_min": int(r.get("expires_in_min") or 240), "note": r.get("note") or "",
            "review": normalize(r.get("review"), m)}


def evaluate(db: Session, e: Employee, action_type: str, args: dict) -> dict:
    """Decide o modo de uma ação. Devolve {mode, source, approver, expires_in_min, separation, review}. review: o bloco
    da regra (revisão por um modelo de outro fornecedor), só quando o piso não subiu o modo dela."""
    rules = list(db.scalars(select(AuthorityRule).where(AuthorityRule.employee_id == e.id,
                                                        AuthorityRule.action_type == action_type)))
    chosen = None
    for r in rules:  # regra sem condição vale sempre; com condição, só se a condição bater; vale a mais restritiva
        if all(condition_matches(c, args) for c in r.conditions or []):
            if chosen is None or RANK[r.mode] > RANK[chosen.mode]:
                chosen = r
    if chosen is not None:
        mode, source, approver, expires = chosen.mode, "cargo", chosen.approver, chosen.expires_in_min
    else:
        mode = AUTONOMY_DEFAULTS.get(e.autonomy_level, AUTONOMY_DEFAULTS["intern"]).get(action_type, "approve")
        source, approver, expires = f"padrão ({e.autonomy_level})", "manager", None
    review = chosen.review if chosen is not None and chosen.review else None
    fl = floor_of(db).get(action_type)
    separation = bool(fl and fl.separation)
    if fl and RANK[fl.min_mode] > RANK[mode]:
        mode, source, review = fl.min_mode, "piso da empresa", None
    return {"mode": mode, "source": source, "approver": approver, "expires_in_min": expires, "separation": separation,
            "review": review}


def payload_hash(ref: str, args: dict) -> str:
    """A ação exata: ferramenta + argumentos. Uma aprovação libera só esta ação, uma vez."""
    return hashlib.sha256(json.dumps([ref, args], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def summary(db: Session, e: Employee) -> list[dict]:
    """O modo efetivo de cada tipo de ação para este cargo (sem condições), para o prompt do agente e a tela."""
    return [{"action_type": t, **evaluate(db, e, t, {})} for t in ACTION_TYPES]
