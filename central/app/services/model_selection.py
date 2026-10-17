"""Escolha do modelo por papel: o melhor modelo da empresa para o planejador, o de código, o classificador…

Para cada papel há pesos por área (o planejador pesa raciocínio; o classificador, custo e velocidade). A nota de um
modelo para o papel combina as notas da ficha (model_profiles) com o desempenho real dele aqui dentro:

- testes de stage dos agentes que rodaram com ele (a versão testada diz qual modelo era);
- tarefas de referência (golden) dos Digital employees que usam o modelo;
- chamadas com erro nos últimos 30 dias.

Quanto mais execuções reais, mais elas pesam (até metade da nota). Modelo sem nota na área principal do papel fica
atrás dos que têm, com confiança baixa. Só modelos de texto disponíveis, com os recursos que o agente precisa
(ferramentas, ler imagens) e numa conexão que o agente pode usar (protocolo openai; código só em conexão aprovada
quando a empresa exige).

Nada muda sozinho: a plataforma sugere (tela do agente, caixa de entrada dos admins, MCP) e quem pode editar o agente
aplica — vira uma versão nova, que passa pelos testes antes de ir para produção.
"""
import re
from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import (
    Agent,
    Employee,
    LlmConnection,
    LlmModel,
    Organization,
    RegressionRun,
    TestRun,
    UsageEvent,
    now,
)
from ..tenancy import current_org
from . import model_profiles as mp
from .common import PlatformError, spec_of

ROLES = {
    "planner": {"label": "planejador / orquestrador", "weights": {"reasoning": 0.6, "coding": 0.1, "speed": 0.1, "cost": 0.2}},
    "coder": {"label": "código", "weights": {"coding": 0.6, "reasoning": 0.2, "speed": 0.1, "cost": 0.1}},
    "analyst": {"label": "análise de dados", "weights": {"reasoning": 0.5, "coding": 0.2, "speed": 0.1, "cost": 0.2}},
    "writer": {"label": "redação", "weights": {"reasoning": 0.4, "cost": 0.3, "speed": 0.3}},
    "support": {"label": "atendimento", "weights": {"speed": 0.35, "cost": 0.35, "reasoning": 0.3}},
    "extractor": {"label": "extração de dados", "weights": {"cost": 0.4, "reasoning": 0.3, "speed": 0.3}},
    "classifier": {"label": "classificação / triagem", "weights": {"cost": 0.5, "speed": 0.3, "reasoning": 0.2}},
    "general": {"label": "geral", "weights": {"reasoning": 0.35, "coding": 0.15, "speed": 0.2, "cost": 0.3}},
}
ROLE_WORDS = [  # a ordem importa: o primeiro que casar
    ("coder", r"c[óo]dig|program|desenvolv|engenh(eiro|aria) de software|\bdev\b|bug|refator|pull request|\bapi\b|script"),
    ("classifier", r"classific|triag|rotul|categoriz|encaminh|rote(ar|ia)|prioriz"),
    ("extractor", r"extra(i|ç)|nota fiscal|fatura|ocr|ler (os )?documentos|preench|cadastr"),
    ("analyst", r"an[áa]lis|dados|planilh|m[ée]trica|kpi|financ|relat[óo]rio|dashboard|previs"),
    ("support", r"atend|suporte|cliente|faq|helpdesk|chamado|sac\b"),
    ("writer", r"escrev|redig|redat|escritor|copywrit|e-?mail|proposta|texto|conte[úu]do|post|artigo|resum"),
]
VISION_WORDS = r"imag|foto|print|screenshot|captura de tela|vis[ãa]o|escanead|desenho|diagrama"
MARGIN = 10  # só sugere trocar quando o recomendado é ao menos 10 pontos melhor


def infer_role(spec: dict, name: str = "", objective: str = "") -> str:
    if spec.get("sub_agents"):
        return "planner"
    if any(isinstance(t, dict) and t.get("workspace") for t in spec.get("tests") or []):
        return "coder"
    # o nome diz mais que o texto: "analista-dados" que "extrai" números é analista
    for text in ((name or "").replace("-", " ").lower(),
                 " ".join([objective or "", spec.get("instructions") or ""]).lower()):
        for role, rx in ROLE_WORDS:
            if re.search(rx, text):
                return role
    return "general"


def infer_caps(spec: dict, name: str = "", objective: str = "") -> list[str]:
    caps = []
    if spec.get("mcps") or spec.get("tools") or spec.get("plugins") or spec.get("sub_agents") or spec.get("memory"):
        caps.append("tools")
    if re.search(VISION_WORDS, " ".join([name or "", objective or "", spec.get("instructions") or ""]).lower()):
        caps.append("vision")
    return caps


# ------------------------------------------------------------------ desempenho real
def _model_of(spec: dict, env: str, conns: dict) -> str:
    llm = spec.get("llm") or {}
    o = llm.get(env) if isinstance(llm.get(env), dict) else {}
    c = o.get("connection") or llm.get("connection")
    if not c or c not in conns:
        return ""
    return o.get("model") or llm.get("model") or conns[c].model_name or ""


def real_performance(db: Session, days: int = 90) -> dict[str, dict]:
    """{chave do modelo: {value 0–100, n, tests, golden, ok}} do que aconteceu aqui dentro."""
    conns = {c.name: c for c in db.scalars(select(LlmConnection))}
    since = now() - timedelta(days=days)
    acc: dict[str, dict] = {}

    def add(key, field, value, n=1):
        if not key or key.startswith("mock"):
            return
        a = acc.setdefault(key, {"tests": [0, 0], "golden": [0.0, 0], "ok": [0, 0]})
        a[field][0] += value
        a[field][1] += n
    agents = {a.id: a for a in db.scalars(select(Agent))}
    for t in db.scalars(select(TestRun).where(TestRun.created_at >= since)):
        a = agents.get(t.agent_id)
        if a is None:
            continue
        try:
            spec = spec_of(a, t.version)
        except PlatformError:
            continue
        add(mp.key_of(_model_of(spec, t.env or "stage", conns)), "tests", 1 if t.status == "passed" else 0)
    for r in db.scalars(select(RegressionRun).where(RegressionRun.created_at >= since)):
        e = db.get(Employee, r.employee_id)
        a = agents.get(e.agent_id) if e else None
        if a is None:
            continue
        key = mp.key_of(_model_of(spec_of(a), "prod", conns))
        for x in r.results or []:
            if isinstance(x.get("score"), (int, float)):
                add(key, "golden", (max(1, min(5, x["score"])) - 1) / 4 * 100)
    rows = db.execute(select(UsageEvent.agent_id, UsageEvent.env, func.count(), func.sum(case((UsageEvent.ok, 1), else_=0)))
                      .where(UsageEvent.created_at >= now() - timedelta(days=30))
                      .group_by(UsageEvent.agent_id, UsageEvent.env)).all()
    for agent_id, env, n, ok in rows:
        a = agents.get(agent_id)
        if a is not None:
            add(mp.key_of(_model_of(spec_of(a), env or "prod", conns)), "ok", int(ok or 0), int(n))
    out = {}
    for key, a in acc.items():
        parts, weights = [], []
        if a["tests"][1]:
            parts.append(100 * a["tests"][0] / a["tests"][1])
            weights.append(a["tests"][1])
        if a["golden"][1]:
            parts.append(a["golden"][0] / a["golden"][1])
            weights.append(a["golden"][1])
        if a["ok"][1]:
            parts.append(100 * a["ok"][0] / a["ok"][1])
            weights.append(min(a["ok"][1], 20))  # muitas chamadas não podem abafar testes e tarefas avaliadas
        n = sum(weights)
        if n:
            out[key] = {"value": round(sum(p * w for p, w in zip(parts, weights, strict=True)) / n), "n": n,
                        "tests": a["tests"][1], "golden": a["golden"][1], "calls": a["ok"][1]}
    return out


# ------------------------------------------------------------------ ranking
def _code_policy_approved(db: Session) -> bool:
    org = db.get(Organization, current_org())
    return bool(org and (org.code_policy or "any") == "approved")


def context(db: Session) -> dict:
    """O que o ranking lê uma vez só (fichas, custo, desempenho real e modelos), para avaliar vários agentes."""
    q = (select(LlmModel, LlmConnection).join(LlmConnection, LlmConnection.id == LlmModel.connection_id)
         .where(LlmModel.available.is_(True), LlmModel.kind == "text", LlmConnection.protocol == "openai"))
    return {"profs": mp.profiles_by_key(db), "cost": mp.cost_scores(db), "real": real_performance(db),
            "rows": db.execute(q).all(), "approved": _code_policy_approved(db)}


def recommend(db: Session, role: str = "general", caps: list[str] | None = None, connection: str = "",
              limit: int = 5, ctx: dict | None = None) -> dict:
    """Os melhores modelos da empresa para o papel, com a nota, a confiança e o porquê."""
    if role not in ROLES:
        raise PlatformError(f"role: {' | '.join(ROLES)}")
    caps = caps or []
    bad = [c for c in caps if c not in mp.CAP_NAMES]
    if bad:
        raise PlatformError(f"recursos desconhecidos: {bad}. Use: {', '.join(mp.CAP_NAMES)}")
    weights = ROLES[role]["weights"]
    # a área de qualidade decide a ordem e a confiança: sem ela, só o preço levaria ao modelo mais barato, mesmo para
    # classificar (um modelo grátis desconhecido não é um bom classificador só por ser grátis)
    main = "coding" if role == "coder" else "reasoning"
    ctx = ctx or context(db)
    approved_only = role == "coder" and ctx["approved"]
    profs, cost, real = ctx["profs"], ctx["cost"], ctx["real"]
    best: dict[str, dict] = {}
    for m, c in ctx["rows"]:
        if connection and c.name != connection:
            continue
        if any(cap not in (m.caps or []) for cap in caps) or (approved_only and not c.allow_code):
            continue
        key = mp.key_of(m.model_id)
        pd = mp.profile_dict(profs.get(key), cost, full=False) or {"scores": {}}
        scores = dict(pd.get("scores") or {})
        if "cost" not in scores and key in cost:
            scores["cost"] = {"value": cost[key], "source": "price"}
        have = {a: w for a, w in weights.items() if a in scores}
        if not have:
            continue
        base = sum(scores[a]["value"] * w for a, w in have.items()) / sum(have.values())
        coverage = sum(have.values())
        r = real.get(key)
        wr = min(0.5, r["n"] / 40) if r else 0.0
        final = base * (1 - wr) + (r["value"] * wr if r else 0)
        src = (scores.get(main) or {}).get("source")
        conf = "alta" if src in ("artificial_analysis", "admin") or (r and r["n"] >= 20) else "média" if src == "research" else "baixa"
        reasons = [f"{mp_area(a)} {scores[a]['value']}" + ("" if scores[a]["source"] != "research" else " (estimativa)")
                   for a in sorted(have, key=lambda a: -weights[a])]
        if r:
            reasons.append(f"desempenho real aqui {r['value']} ({r['tests']} teste(s), {r['golden']} tarefa(s) de referência, "
                           f"{r['calls']} chamada(s))")
        if main not in scores:
            reasons.append(f"sem nota de {mp_area(main).lower()}: confiança baixa" if conf == "baixa"
                           else f"sem nota de {mp_area(main).lower()}; a confiança vem do uso real aqui")
        cand = {"connection": c.name, "model": m.model_id, "key": key, "score": round(final),
                "rank_key": (main in scores, round(final, 2), coverage), "confidence": conf, "reasons": reasons,
                "scores": {a: v["value"] for a, v in scores.items()}, "real": r,
                "price_in_per_mtok": m.price_in_per_mtok, "price_out_per_mtok": m.price_out_per_mtok,
                "best_for": pd.get("best_for") or []}
        prev = best.get(key)
        if prev is None or (cand["price_in_per_mtok"] or 9e9) < (prev["price_in_per_mtok"] or 9e9):
            best[key] = cand  # o mesmo modelo em duas conexões: fica a mais barata
    ranked = sorted(best.values(), key=lambda x: x["rank_key"], reverse=True)
    for x in ranked:
        x.pop("rank_key")
    return {"role": role, "role_label": ROLES[role]["label"], "weights": weights, "caps": caps,
            "code_approved_only": approved_only, "candidates": ranked[:limit], "total": len(ranked)}


def mp_area(a: str) -> str:
    return {"reasoning": "Raciocínio", "coding": "Código", "speed": "Velocidade", "cost": "Custo"}.get(a, a)


def for_agent(db: Session, agent: Agent, role: str = "", env: str = "prod", ctx: dict | None = None) -> dict:
    """O papel do agente (deduzido ou informado), o modelo atual e a sugestão, se houver uma melhor."""
    spec = spec_of(agent)
    if spec.get("remote"):
        return {"agent": agent.slug, "supported": False,
                "why": "um agente remoto escolhe o próprio modelo, fora do hangar"}
    if spec.get("harness"):
        return {"agent": agent.slug, "supported": False,
                "why": "a recomendação por papel é para agentes de chat; num agente com harness, escolha o modelo em harness.model"}
    role = role or infer_role(spec, agent.name, agent.objective or "")
    caps = infer_caps(spec, agent.name, agent.objective or "")
    conns = {c.name: c for c in db.scalars(select(LlmConnection))}
    current = _model_of(spec, env, conns)
    llm = spec.get("llm") or {}
    current_conn = ((llm.get(env) or {}) if isinstance(llm.get(env), dict) else {}).get("connection") or llm.get("connection") or ""
    ctx = ctx or context(db)
    rec = recommend(db, role, caps, limit=5, ctx=ctx)
    cur_key = mp.key_of(current) if current else ""
    cur = next((c for c in rec["candidates"] if c["key"] == cur_key), None)
    if cur is None and cur_key:
        full = recommend(db, role, caps, limit=1000, ctx=ctx)["candidates"]
        cur = next((c for c in full if c["key"] == cur_key), None)
    top = rec["candidates"][0] if rec["candidates"] else None
    suggest, note = None, ""
    main = "coding" if role == "coder" else "reasoning"
    cur_blind = bool(cur) and cur["confidence"] == "baixa"
    if cur_blind and current:
        # o atual não tem nota na área principal: a nota dele é só preço e uso, não dá para comparar com quem tem
        note = (f"O modelo atual não tem nota de {mp_area(main).lower()} para comparar. Pesquise a ficha dele "
                "(Configurações → Provedores de LLM → o modelo → Pesquisar agora).")
    # sem nota na área principal do papel (confiança baixa) não há sugestão: só o preço levaria todo mundo ao mais barato
    elif top and top["confidence"] != "baixa" and top["key"] != cur_key and (cur is None or top["score"] - cur["score"] >= MARGIN):
        suggest = {"connection": top["connection"], "model": top["model"], "score": top["score"],
                   "gain": top["score"] - (cur["score"] if cur else 0) if cur else None,
                   "patch": {"llm": {"connection": top["connection"], "model": top["model"]}}}
    return {"agent": agent.slug, "name": agent.name, "supported": True, "role": role, "role_label": ROLES[role]["label"],
            "caps": caps, "current": {"connection": current_conn, "model": current, "score": cur["score"] if cur else None,
                                      "confidence": cur["confidence"] if cur else None,
                                      "reasons": cur["reasons"] if cur else []},
            "suggestion": suggest, "note": note, "candidates": rec["candidates"]}


def for_team(db: Session, agent: Agent) -> list[dict]:
    """Um multiagente: o orquestrador e cada especialista, cada um com o papel e a sugestão."""
    from .common import get_agent
    ctx = context(db)
    out = [for_agent(db, agent, ctx=ctx)]
    for slug in spec_of(agent).get("sub_agents") or []:
        try:
            out.append(for_agent(db, get_agent(db, slug), ctx=ctx))
        except PlatformError:
            continue
    return out


def suggestions(db: Session) -> list[dict]:
    """Os agentes em que existe um modelo bem melhor para o papel (a caixa de entrada dos admins mostra)."""
    out, ctx = [], context(db)
    for a in db.scalars(select(Agent)):
        try:
            r = for_agent(db, a, ctx=ctx)
        except PlatformError:
            continue
        if r.get("supported") and r.get("suggestion") and r["current"]["model"]:
            out.append({**r, "at": now()})
    return out


_SUGGEST_CACHE: dict[int, tuple[float, list]] = {}


def cached_suggestions(db: Session, ttl: float = 600) -> list[dict]:
    """As sugestões da empresa, recalculadas no máximo a cada 10 minutos (a caixa de entrada abre muitas vezes)."""
    import time
    org = current_org()
    hit = _SUGGEST_CACHE.get(org)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = suggestions(db)
    _SUGGEST_CACHE[org] = (time.time(), val)
    return val
