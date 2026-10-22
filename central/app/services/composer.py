"""Montagem de agentes a partir do catálogo: "reusar antes de construir".

Os agentes existentes são PEÇAS SÓ DE LEITURA. Nada aqui altera outro agente: instruções, versão, testes, memória,
acesso e deploy deles ficam como estão. Toda a construção acontece no agente NOVO.

- plan(): acha no catálogo (só o que a pessoa pode ver, só versões em produção) os agentes, skills, MCPs e templates
  parecidos com o pedido, diz como cada um pode entrar no agente novo e quais habilidades faltam (com perguntas).
- compose(): cria o agente novo a partir do plano: especialistas (sub_agents, chamados como estão), cópia de uma base
  (instruções, skills, ferramentas e testes copiados para o novo), skills e MCPs do catálogo, skills novas e só o
  texto que é realmente novo. Registra de onde veio (agent_lineage).
- lineage(): "construído a partir de" e "usado por". stats(): taxa de reaproveitamento e texto poupado.

Similaridade: embeddings do modelo local da memória (multilíngue; nada sai da rede) quando a memória está ligada,
combinados com similaridade por palavras (TF-IDF de palavras e n-gramas). Sem memória, só por palavras.
"""
import copy
import hashlib
import json
import math
import re
import secrets
import unicodedata
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import shared
from .. import spec as specmod
from ..models import Agent, AgentLineage, AuditLog, McpServer, Skill, UsageEvent, now
from . import memory as memsvc
from .access import Access, Forbidden
from .common import PlatformError, audit, get_agent, iso, spec_of
from .runtime import active_deployment

PLAN_TTL_S = 2 * 3600
EMB_TTL_S = 30 * 86400
MAX_QUESTIONS = 3
# Limiares calibrados com o catálogo real (MiniLM multilíngue): capacidades relevantes ficam em 0,5–0,65 de
# cosseno, agentes sem relação abaixo de ~0,40 — inclusive com pedido em português e agentes em inglês. No modo
# semântico as palavras em comum só somam (bônus), para não penalizar pedidos em outro idioma.
STRONG = {"semantic": 0.60, "lexical": 0.30}
PARTIAL = {"semantic": 0.45, "lexical": 0.12}

_STOP = set("""
a o os as um uma uns umas de do da dos das em no na nos nas por para com sem que e ou se ao aos à às é ser ter
seu sua seus suas meu minha nosso nossa ele ela eles elas isso este esta esse essa como mais menos muito cada
the a an and or of to in on for with without by from is are be this that these those it its as at our your their
my me we you they he she agent agente agentes agents create build crie criar construa construir faz fazer make do
""".split())


# ------------------------------------------------------------------ texto e similaridade
def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", (text or "").lower()).encode("ascii", "ignore").decode()


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _fold(text)) if len(w) > 2 and w not in _STOP]


def _features(text: str) -> Counter:
    c = Counter()
    for w in _words(text):
        c["w:" + w] += 2
        g = f"_{w}_"
        for i in range(len(g) - 3):
            c["g:" + g[i:i + 4]] += 1
    return c


def _tfidf(docs: list[Counter]) -> list[dict]:
    n = len(docs)
    df = Counter(t for d in docs for t in d)
    out = []
    for d in docs:
        v = {t: (1 + math.log(f)) * math.log(1 + n / df[t]) for t, f in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({t: x / norm for t, x in v.items()})
    return out


def _cos_sparse(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def _embed_cached(texts: list[str]) -> list[list[float]] | None:
    """Vetores com cache no banco (compartilhado entre réplicas). None = sem embeddings (busca só por palavras)."""
    keys = ["emb:" + hashlib.sha1(t.encode()).hexdigest() for t in texts]
    out: list = [None] * len(texts)
    missing = []
    for i, k in enumerate(keys):
        hit = shared.get(k)
        if hit and hit.get("v"):
            out[i] = hit["v"]
        else:
            missing.append(i)
    if missing:
        vecs = memsvc.embed([texts[i] for i in missing])
        if vecs is None:
            return None
        for i, v in zip(missing, vecs, strict=True):
            out[i] = v
            shared.put(keys[i], {"v": v}, EMB_TTL_S)
    return out


class _Scorer:
    """Pontua um conjunto de documentos contra várias consultas (pedido + cada capacidade)."""

    def __init__(self, docs: list[str], queries: list[str]):
        self.n = len(docs)
        vecs = _tfidf([_features(t) for t in docs + queries])
        self.lex_docs, self.lex_q = vecs[:self.n], vecs[self.n:]
        emb = _embed_cached(docs + queries) if docs and queries else None
        self.mode = "semantic" if emb else "lexical"
        self.emb_docs, self.emb_q = (emb[:self.n], emb[self.n:]) if emb else ([], [])

    def score(self, qi: int, di: int) -> float:
        lex = _cos_sparse(self.lex_q[qi], self.lex_docs[di])
        if self.mode == "semantic":
            return _cos(self.emb_q[qi], self.emb_docs[di]) + 0.25 * lex
        return lex

    def level(self, s: float) -> str | None:
        if s >= STRONG[self.mode]:
            return "strong"
        if s >= PARTIAL[self.mode]:
            return "partial"
        return None


def _shared_terms(query: str, doc: str, k: int = 5) -> list[str]:
    q = set(_words(query))
    seen, out = set(), []
    for w in _words(doc):
        if w in q and w not in seen:
            seen.add(w)
            out.append(w)
    return out[:k]


# ------------------------------------------------------------------ catálogo indexável (só leitura)
def _names(items) -> list[str]:
    return [x if isinstance(x, str) else (x.get("name") or x.get("id") or "") for x in items or []]


def _prod_version(a: Agent) -> int | None:
    d = active_deployment(a, "prod")
    return d.version if d else None


def _agent_doc(a: Agent, spec: dict) -> str:
    parts = [a.name, a.objective, a.final_output, (spec.get("instructions") or "")[:1200],
             "skills: " + ", ".join(_names(spec.get("skills"))), "mcps: " + ", ".join(_names(spec.get("mcps"))),
             "tools: " + ", ".join(_names(spec.get("tools"))),
             "tests: " + ", ".join(t.get("name", "") for t in spec.get("tests") or [])]
    return "\n".join(p for p in parts if p)


def _catalog(db: Session, acc: Access) -> list[dict]:
    """Agentes que a pessoa pode ver e que têm versão em produção (a única indexada)."""
    out = []
    uses = dict(db.execute(select(UsageEvent.agent_id, func.count()).group_by(UsageEvent.agent_id)).all())
    for a in acc.visible(db.scalars(select(Agent)).all()):
        v = _prod_version(a)
        if not v:
            continue
        spec = spec_of(a, v)
        last = a.tests[0] if a.tests else None
        out.append({"agent": a, "version": v, "spec": spec, "doc": _agent_doc(a, spec), "uses": uses.get(a.id, 0),
                    "tests_ok": bool(last and last.status == "passed")})
    return out


def _split_capabilities(request: str) -> list[str]:
    parts = re.split(r"(?:\n+|;|\.\s+|\s+(?:and|e|also|além disso|plus)\s+)", request or "")
    caps = [p.strip(" -•*\t") for p in parts if len(_words(p)) >= 2]
    return caps[:8] or ([request.strip()] if request.strip() else [])


WHY_PT = ("objetivo parecido", "termos em comum", "cobre")
WHY_EN = ("similar goal", "shared terms", "covers")


def _lang_pt(text: str) -> bool:
    w = _fold(text).split()
    pt = sum(x in {"de", "que", "para", "com", "uma", "os", "do", "da", "nao", "cliente", "voce"} for x in w)
    en = sum(x in {"the", "and", "for", "with", "to", "of", "you", "customer", "your", "is"} for x in w)
    return pt > en


# ------------------------------------------------------------------ plano
def plan(db: Session, acc: Access, request: str, capabilities: list[str] | None = None, limit: int = 5) -> dict:
    request = (request or "").strip()
    if len(_words(request)) < 2:
        raise PlatformError("descreva o agente que você quer (o que ele faz e para quem)")
    caps = [c.strip() for c in (capabilities or []) if c and c.strip()][:10] or _split_capabilities(request)
    queries = [request] + caps

    cat = _catalog(db, acc)
    skills = db.scalars(select(Skill)).all()
    mcps = db.scalars(select(McpServer)).all()
    from .templates import list_templates
    tpls = list_templates()
    docs = ([c["doc"] for c in cat]
            + [f"{s.name}\n{s.description}\n{(s.content or '')[:800]}\n{(s.examples or '')[:300]}" for s in skills]
            + [f"{m.name}\n{m.description}" for m in mcps]
            + [f"{t['title']}\n{t['description']}\n{' '.join(t.get('tags') or [])}" for t in tpls])
    sc = _Scorer(docs, queries)
    na, ns, nm = len(cat), len(skills), len(mcps)

    def best_cap(di: int) -> list[str]:
        return [caps[qi - 1] for qi in range(1, len(queries)) if sc.level(sc.score(qi, di))]

    agents = []
    for i, c in enumerate(cat):
        a, s = c["agent"], sc.score(0, i)
        s += (0.03 if c["tests_ok"] else 0) + min(0.04, math.log1p(c["uses"]) / 100)
        lvl = sc.level(s)
        covered = best_cap(i)
        if not lvl and not covered:
            continue
        perms = acc.permissions(a)
        access = "ok" if "consume" in perms else ("request_access" if "request_access" in perms else "no")
        modes = []
        if lvl == "strong" and len(covered) >= max(1, math.ceil(0.7 * len(caps))):
            modes.append("use_as_is")
        modes.append("specialist")
        if "view_spec" in perms:
            modes.append("base")
        lbl = WHY_PT if _lang_pt(request) else WHY_EN
        why = [f"{lbl[0]}: {a.objective[:140]}"]
        terms = _shared_terms(" ".join(queries), c["doc"])
        if terms:
            why.append(f"{lbl[1]}: " + ", ".join(terms))
        if covered:
            why.append(f"{lbl[2]}: " + "; ".join(covered[:3]))
        agents.append({"slug": a.slug, "name": a.name, "team": a.team_id, "version": c["version"],
                       "score": round(s, 3), "match": lvl or "partial", "why": why, "covers": covered,
                       "modes": modes, "access": access, "spec_visible": "view_spec" in perms,
                       "tests_ok": c["tests_ok"], "kind": a.kind})
    agents.sort(key=lambda x: -x["score"])
    agents = agents[:limit]

    def ranked(offset: int, rows, label) -> list[dict]:
        out = []
        for j, r in enumerate(rows):
            di = offset + j
            s, covered = sc.score(0, di), best_cap(di)
            lvl = sc.level(s)
            if lvl or covered:
                out.append({**label(r), "score": round(s, 3), "match": lvl or "partial", "covers": covered})
        return sorted(out, key=lambda x: -x["score"])[:limit]

    skill_hits = ranked(na, skills, lambda s: {"name": s.name, "description": s.description, "version": s.version,
                                               "has_test": bool(s.test)})
    mcp_hits = ranked(na + ns, mcps, lambda m: {"name": m.name, "description": m.description})
    tpl_hits = ranked(na + ns + nm, tpls, lambda t: {"id": t["id"], "title": t["title"], "description": t["description"]})

    covered_caps = {c for x in agents + skill_hits + mcp_hits for c in x["covers"]}
    pt = _lang_pt(request)
    gaps = []
    for c in caps:
        if c in covered_caps:
            continue
        near = [x["name"] for x in skill_hits if x["score"] > 0][:2]
        q = (f"Para “{c}”: existe um procedimento, padrão ou documento que o agente deve seguir? Descreva e eu crio uma "
             f"skill nova do time (ou diga que não precisa)." if pt else
             f"For “{c}”: is there a procedure, standard or document the agent must follow? Describe it and I'll create "
             f"a new team skill (or say it isn't needed).")
        gaps.append({"capability": c, "suggested_skills": near, "question": q})

    use_as_is = [x for x in agents if "use_as_is" in x["modes"]]
    specialists = [x for x in agents if x["match"] == "strong" or x["covers"]][:3]
    if use_as_is:
        name = use_as_is[0]["name"]
        rec = (f"'{name}' já faz isso: use-o como está (connect_agent ou request_agent_access) — ou monte um agente NOVO "
               f"a partir dele (base ou especialista). Ele não será alterado." if pt else
               f"'{name}' already does this: use it as is (connect_agent or request_agent_access), or build a NEW agent "
               f"from it (base or specialist). It will not be changed.")
    elif specialists:
        names = ", ".join(f"'{x['name']}'" + ("" if x["access"] == "ok" else
                                              (" (pedir acesso antes)" if pt else " (request access first)"))
                          for x in specialists)
        rec = (f"Monte um agente NOVO que chama {names} como especialista(s); escreva só o que é novo. Os agentes "
               f"existentes não são alterados." if pt else
               f"Build a NEW agent that calls {names} as specialist(s); write only what is new. The existing agents "
               f"are not changed.")
    elif skill_hits or mcp_hits:
        rec = ("Nenhum agente parecido em produção: crie um agente novo reaproveitando as skills/MCPs sugeridos." if pt
               else "No similar agent in production: create a new agent reusing the suggested skills/MCPs.")
    else:
        rec = ("Nada no catálogo cobre o pedido: construa do zero (compose_agent sem peças) e registre as skills novas."
               if pt else "Nothing in the catalog covers the request: build from scratch (compose_agent without "
                          "pieces) and register the new skills.")

    plan_id = "pl_" + secrets.token_urlsafe(9)
    shared.put(f"plan:{plan_id}", {"by": acc.p.name, "request": request[:2000], "candidates": [x["slug"] for x in agents],
                                   "created_at": now().isoformat()}, PLAN_TTL_S)
    audit(db, acc.p.name, "agent.plan", plan_id,
          f"{len(agents)} agente(s), {len(skill_hits)} skill(s), {len(gaps)} lacuna(s) [{sc.mode}] | {request[:200]}")
    return {"plan_id": plan_id, "similarity": sc.mode, "capabilities": caps, "agents": agents, "skills": skill_hits,
            "mcps": mcp_hits, "templates": tpl_hits, "gaps": gaps, "questions": [g["question"] for g in gaps][:MAX_QUESTIONS],
            "recommendation": rec,
            "compose_hint": {"plan_id": plan_id, "specialists": [x["slug"] for x in specialists if x["access"] == "ok"],
                             "skills": [x["name"] for x in skill_hits if x["match"] == "strong"],
                             "mcps": [x["name"] for x in mcp_hits if x["match"] == "strong"]},
            "rules": "Os agentes do catálogo são só leitura: compose_agent cria um agente novo e nunca altera os existentes. "
                     "Pergunte ao usuário no máximo 3 coisas (questions) e só se forem necessárias."}


# ------------------------------------------------------------------ montagem (cria só o agente novo)
def check_sub_agents(db: Session, acc: Access | None, old_spec: dict, new_spec: dict):
    """Colocar um agente como especialista (sub_agent) exige poder USÁ-lo: para agentes de outros times, pedido
    de acesso aprovado (ou visibilidade open)."""
    if acc is None:
        return
    added = set(new_spec.get("sub_agents") or []) - set((old_spec or {}).get("sub_agents") or [])
    for slug in sorted(added):
        a = get_agent(db, slug)
        if not acc.can("consume", a):
            if "view" not in acc.permissions(a):
                raise Forbidden(f"Agente '{slug}' não encontrado ou sem acesso")
            raise Forbidden(f"Para usar '{slug}' como especialista, peça acesso ao time dono "
                            f"(request_agent_access('{slug}')) — o agente dele não muda")


def _dedupe(items) -> list:
    out, seen = [], set()
    for x in items or []:
        k = json.dumps(x, sort_keys=True, ensure_ascii=False) if not isinstance(x, str) else x
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _safe_tool(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"ask_{slug}")[:64]


def _specialists_section(subs: list[Agent], pt: bool) -> str:
    if not subs:
        return ""
    head = ("## Especialistas\nVocê pode delegar a estes agentes (cada um é uma ferramenta). Chame um especialista só "
            "quando a tarefa precisar da especialidade dele; saudações e perguntas simples (ex.: ping) você responde "
            "direto. Passe só os fatos necessários e use as respostas; não invente o que eles não devolveram:" if pt else
            "## Specialists\nYou can delegate to these agents (each one is a tool). Call a specialist only when the task "
            "needs its specialty; answer greetings and simple questions (e.g. ping) directly. Pass only the facts they "
            "need and use their answers; never invent what they did not return:")
    return head + "\n" + "\n".join(f"- {_safe_tool(a.slug)}: {a.name} — {a.objective}" for a in subs)


def compose(db: Session, acc: Access, name: str, objective: str, final_output: str, instructions: str = "",
            team: str = "", visibility: str = "", specialists: list | None = None, base: str = "",
            skills: list | None = None, new_skills: list | None = None, mcps: list | None = None,
            tools: list | None = None, tests: list | None = None, copy_tests_from: list | None = None,
            copy_base_tests: bool = True, include_skill_tests: bool = True, llm: dict | None = None,
            memory: dict | None = None, plan_id: str = "", personal: bool = False, avatar: str | None = None) -> dict:
    from .registry import create_agent, replace_spec  # import tardio: registry importa deste módulo

    t = acc.team_for_new_agent(team or None, any_role=personal)
    pt = _lang_pt(f"{objective} {instructions}")

    # 1) peças: tudo só leitura, conferido antes de criar qualquer coisa
    subs: list[Agent] = []
    for raw in specialists or []:
        a = get_agent(db, str(raw).split("@")[0])
        check_sub_agents(db, acc, {}, {"sub_agents": [a.slug]})
        if not _prod_version(a):
            raise PlatformError(f"'{a.slug}' ainda não está em produção — só agentes em produção entram como peça")
        subs.append(a)
    base_agent, base_spec, base_v = None, {}, None
    if base:
        base_agent = get_agent(db, base.split("@")[0])
        acc.require("view_spec", base_agent)
        base_v = _prod_version(base_agent)
        if not base_v:
            raise PlatformError(f"'{base_agent.slug}' ainda não está em produção — a base precisa ser uma versão aprovada")
        base_spec = copy.deepcopy(specmod.normalize_legacy(spec_of(base_agent, base_v)))
        base_spec.pop("channels", None)  # integrações (Slack etc.) são do agente original, não da cópia
    copied_tests = []
    if base_spec and copy_base_tests:
        copied_tests += [{**x, "name": f"{x.get('name') or 'case'} (from {base_agent.slug})"} for x in base_spec.get("tests") or []]
    for raw in copy_tests_from or []:
        src = get_agent(db, str(raw).split("@")[0])
        acc.require("view_spec", src)
        v = _prod_version(src) or src.current_version
        copied_tests += [{**x, "name": f"{x.get('name') or 'case'} (from {src.slug})"}
                         for x in spec_of(src, v).get("tests") or []]

    skill_names = _dedupe(_names(base_spec.get("skills")) + list(skills or []))
    for n in skill_names:
        if not db.scalar(select(Skill).where(Skill.name == n)):
            raise PlatformError(f"Skill '{n}' não está no catálogo — crie com new_skills")
    created_skills = []
    for ns in new_skills or []:
        n = (ns.get("name") or "").strip()
        if not n or not (ns.get("content") or "").strip():
            raise PlatformError("cada skill nova precisa de name e content")
        if db.scalar(select(Skill).where(Skill.name == n)):
            raise PlatformError(f"Já existe a skill '{n}': use skills=['{n}'] (ela não é alterada) ou escolha outro nome")
        test = ns.get("test")
        if test:
            try:
                test = specmod.TestCase(**test).model_dump(exclude_none=True)
            except Exception as e:
                raise PlatformError(f"teste da skill '{n}' inválido: {e}") from None
        created_skills.append(Skill(name=n, description=ns.get("description", ""), content=ns["content"],
                                    examples=ns.get("examples", ""), test=test, team_id=t.id, created_by=acc.p.name))

    # 2) spec do agente novo
    parts = [base_spec.get("instructions", ""), instructions or "", _specialists_section(subs, pt)]
    instr = "\n\n".join(p.strip() for p in parts if p and p.strip())
    if not instr:
        instr = (f"Você é {name}. Objetivo: {objective}\nEntregue: {final_output}" if pt else
                 f"You are {name}. Goal: {objective}\nDeliver: {final_output}")
    all_skills = skill_names + [s.name for s in created_skills]
    skill_tests = []
    if include_skill_tests:
        rows = {s.name: s for s in db.scalars(select(Skill).where(Skill.name.in_(skill_names)))} if skill_names else {}
        for s in list(rows.values()) + created_skills:
            if s.test:
                skill_tests.append({**s.test, "name": f"skill {s.name}: {s.test.get('name') or 'case'}"})
    all_tests, seen = [], set()
    for x in copied_tests + skill_tests + list(tests or []):
        nm = x.get("name") or x.get("input", "")[:40]
        if nm not in seen:
            seen.add(nm)
            # chamar especialistas (e a memória deles) leva mais que um agente simples: 300 s por caso, se não definido
            all_tests.append({**x, "timeout_s": x.get("timeout_s") or 300} if subs else x)
    spec = {k: v for k, v in base_spec.items() if k not in ("instructions", "tests", "sub_agents", "skills", "mcps", "tools")}
    spec.update(instructions=instr, tests=all_tests[:25],
                sub_agents=_dedupe(list(base_spec.get("sub_agents") or []) + [a.slug for a in subs]),
                skills=all_skills, mcps=_dedupe(list(base_spec.get("mcps") or []) + list(mcps or [])),
                tools=_dedupe(list(base_spec.get("tools") or []) + list(tools or [])))
    if llm:
        spec["llm"] = {**(spec.get("llm") or {}), **llm}
    if memory is not None:
        spec["memory"] = memory
    try:
        specmod.validate(spec)
    except specmod.SpecError as e:
        raise PlatformError(str(e)) from None

    # 3) cria: skills novas, o agente novo e a linhagem. Nenhum agente existente é escrito.
    for s in created_skills:
        db.add(s)
    db.commit()
    for s in created_skills:
        audit(db, acc.p.name, "skill.create", s.name, f"criada na montagem de '{name}'")
    a = create_agent(db, name, objective, final_output, owner="", actor=acc.p.name, team_id=t.id,
                     visibility=visibility or None, avatar=avatar)
    a = replace_spec(db, a.slug, spec, acc.p.name)
    mode = ("mixed" if base_agent and subs else "base" if base_agent else "specialists" if subs
            else "parts" if (skill_names or copied_tests or mcps) else "scratch")
    reused_skill_text = (sum(len(x.content or "") for x in db.scalars(select(Skill).where(Skill.name.in_(skill_names))))
                         if skill_names else 0)
    # o que um LLM teria de escrever para construir isto do zero e que veio pronto: a base copiada, as instruções e os
    # testes de cada especialista (o comportamento dele é reaproveitado inteiro), os testes copiados e as skills
    specialists_text = 0
    for sub in subs:
        ss = spec_of(sub, _prod_version(sub))
        specialists_text += len(ss.get("instructions") or "") + len(json.dumps(ss.get("tests") or [], ensure_ascii=False))
    reused = (len(base_spec.get("instructions", "")) + len(_specialists_section(subs, pt)) + specialists_text
              + len(json.dumps(copied_tests + skill_tests, ensure_ascii=False)) + reused_skill_text)
    written = len(instructions or "") + len(json.dumps(list(tests or []), ensure_ascii=False)) + sum(
        len(s.content) for s in created_skills)
    db.add(AgentLineage(agent_id=a.id, plan_id=plan_id or "", mode=mode,
                        based_on={"slug": base_agent.slug, "version": base_v} if base_agent else None,
                        specialists=[{"slug": s.slug, "version": _prod_version(s)} for s in subs],
                        skills=skill_names, new_skills=[s.name for s in created_skills],
                        tests_copied=len(copied_tests) + len(skill_tests), reused_chars=reused, written_chars=written,
                        created_by=acc.p.name))
    db.commit()
    audit(db, acc.p.name, "agent.compose", a.slug,
          f"modo {mode} | base {base_agent.slug if base_agent else '-'} | especialistas {[s.slug for s in subs]} | "
          f"skills {all_skills} | testes copiados {len(copied_tests) + len(skill_tests)}")
    return {"slug": a.slug, "version": a.current_version, "team": t.slug, "mode": mode, "kind": a.kind,
            "based_on": {"slug": base_agent.slug, "version": base_v} if base_agent else None,
            "specialists": [s.slug for s in subs], "skills": all_skills, "new_skills": [s.name for s in created_skills],
            "tests": len(spec["tests"]), "tests_copied": len(copied_tests) + len(skill_tests),
            "reused_tokens_estimate": reused // 4, "written_tokens_estimate": written // 4,
            "untouched": "os agentes de origem não foram alterados",
            "next": "ship_agent para testar em stage e publicar (ou run_tests)"}


# ------------------------------------------------------------------ linhagem e métricas
def lineage(db: Session, acc: Access, slug: str) -> dict:
    a = get_agent(db, slug)
    acc.require("view", a)
    row = db.scalar(select(AgentLineage).where(AgentLineage.agent_id == a.id))
    built = None
    if row:
        def ref(item):
            src = db.scalar(select(Agent).where(Agent.slug == item["slug"]))
            if not src or "view" not in acc.permissions(src):
                return {"slug": item["slug"], "visible": False}
            cur = _prod_version(src)
            return {"slug": src.slug, "name": src.name, "version_at_composition": item.get("version"),
                    "prod_version_now": cur, "updated_since": bool(cur and item.get("version") and cur != item["version"]),
                    "visible": True}
        built = {"mode": row.mode, "plan_id": row.plan_id, "created_by": row.created_by, "created_at": iso(row.created_at),
                 "based_on": ref(row.based_on) if row.based_on else None,
                 "specialists": [ref(s) for s in row.specialists or []], "skills": row.skills, "new_skills": row.new_skills,
                 "tests_copied": row.tests_copied, "reused_tokens_estimate": row.reused_chars // 4}
    used_by = []
    for other in acc.visible(db.scalars(select(Agent).where(Agent.id != a.id)).all()):
        if slug in (spec_of(other).get("sub_agents") or []):
            used_by.append({"slug": other.slug, "name": other.name, "as": "specialist"})
    for r in db.scalars(select(AgentLineage)):
        if r.based_on and r.based_on.get("slug") == slug:
            other = db.get(Agent, r.agent_id)
            if other and "view" in acc.permissions(other) and not any(u["slug"] == other.slug and u["as"] == "base" for u in used_by):
                used_by.append({"slug": other.slug, "name": other.name, "as": "base"})
    return {"slug": slug, "built_from": built, "used_by": used_by}


def stats(db: Session) -> dict:
    rows = db.scalars(select(AgentLineage)).all()
    by_mode = Counter(r.mode for r in rows)
    plans = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "agent.plan")) or 0
    reused, written = sum(r.reused_chars for r in rows), sum(r.written_chars for r in rows)
    composed = len(rows)
    return {"plans": plans, "composed": composed, "by_mode": dict(by_mode),
            "reuse_rate": round((composed - by_mode.get("scratch", 0)) / composed, 3) if composed else 0.0,
            "reused_tokens_estimate": reused // 4, "written_tokens_estimate": written // 4,
            "reused_share": round(reused / (reused + written), 3) if reused + written else 0.0,
            "tests_copied": sum(r.tests_copied for r in rows),
            "skills_created": sum(len(r.new_skills or []) for r in rows)}
