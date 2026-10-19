"""Pacotes de time: levar a configuração de um time para outro (ou de outra instalação), ou começar de um time pronto.

Um pacote (YAML/JSON, kind: hangar-package) tem:
- skills (as do time usadas pelos funcionários);
- metas do time (em árvore);
- Digital employees: o cargo completo (missão, responsabilidades, metas, sistemas, canais, ferramentas, skills, MCPs,
  plugins, modelo), a alçada, as tarefas de experiência, as rotinas, o chefe e os colegas;
- projetos (com a meta e o funcionário padrão).

Exportar remove os segredos: credenciais e webhooks não saem, e campos com cara de segredo (token, senha, chave,
authorization) nas ferramentas viram vazio. Instalações de plugins e orçamentos não vão no pacote.

Importar sempre começa por uma prévia (o que será criado, o que já existe e será reaproveitado ou renomeado, o que falta)
e cria os funcionários em rascunho: cada um ainda passa pelo período de experiência e pela admissão do gestor. Os
plugins citados precisam estar instalados para o time, e o gestor informado vira o gestor de todos.
"""
import copy
import os
import re

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import Agent, AuthorityRule, Employee, EmployeeRoutine, EmployeeTask, Goal, Plugin, Project, Skill, now
from ..tenancy import unscoped
from .access import RANK, Access, Forbidden, find_team
from .common import PlatformError, audit, iso, spec_of

KIND = "hangar-package"
FORMAT = 1
_SECRETISH = re.compile(r"(secret|token|password|passwd|authorization|api[_-]?key|credential|cookie)", re.I)
PACKAGES_DIR = os.path.join(config.TEMPLATES_DIR, "packages")


def _scrub(v):
    """Tira valores com cara de segredo (pelo nome da chave) de qualquer estrutura."""
    if isinstance(v, dict):
        return {k: ("" if _SECRETISH.search(str(k)) and isinstance(x, str) else _scrub(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub(x) for x in v]
    return v


def _slug(db: Session, e: Employee | None) -> str | None:
    a = db.get(Agent, e.agent_id) if e else None
    return a.slug if a else None


# ------------------------------------------------------------------ exportar
def export(db: Session, acc: Access, team: str, name: str = "", description: str = "") -> dict:
    t = find_team(db, team)
    if not acc.p.is_admin and RANK.get(acc.teams.get(t.id, ""), 0) < RANK["developer"]:
        raise Forbidden("exporta o pacote quem é developer ou mantenedor do time (ou um admin)")
    emps = list(db.scalars(select(Employee).where(Employee.team_id == t.id, Employee.status != "offboarded")))
    out_emps, skill_names = [], set()
    for e in emps:
        a = db.get(Agent, e.agent_id)
        spec = spec_of(a) if a else {}
        skills = [s if isinstance(s, str) else s.get("name") for s in spec.get("skills") or []]
        skill_names |= {s for s in skills if s}
        probation = [{"title": x.title, "body": x.body, "expected": x.expected} for x in db.scalars(
            select(EmployeeTask).where(EmployeeTask.employee_id == e.id, EmployeeTask.probation.is_(True)).limit(5))]
        out_emps.append(_scrub({
            "slug": a.slug, "name": a.name, "title": e.title, "mission": e.mission, "responsibilities": e.responsibilities,
            "kpis": e.kpis or [], "systems": e.systems or [], "channels": e.channels or ["portal"],
            "working_hours": e.working_hours, "task_budget_usd": e.task_budget_usd, "task_time_limit_min": e.task_time_limit_min,
            "plan_policy": e.plan_policy, "review_policy": e.review_policy, "quality_sample": e.quality_sample,
            "llm": {k: v for k, v in (spec.get("llm") or {}).items() if k in ("model", "connection")},
            "tools": copy.deepcopy(spec.get("tools") or []), "skills": skills, "mcps": spec.get("mcps") or [],
            "plugins": spec.get("plugins") or [],
            "authority": [{k: getattr(r, k) for k in ("action_type", "mode", "conditions", "approver", "expires_in_min", "note", "review")}
                          for r in db.scalars(select(AuthorityRule).where(AuthorityRule.employee_id == e.id))],
            "probation_tasks": probation,
            "routines": [{"title": r.title, "body": r.body, "cron": r.cron, "timezone": r.timezone, "priority": r.priority}
                         for r in db.scalars(select(EmployeeRoutine).where(EmployeeRoutine.employee_id == e.id))],
            "reports_to": _slug(db, db.get(Employee, e.reports_to_id)) if e.reports_to_id else None,
            "colleagues": e.colleagues or []}))
    goals = list(db.scalars(select(Goal).where(Goal.team_id == t.id)))
    gref = {g.id: f"g{n + 1}" for n, g in enumerate(goals)}
    projects = list(db.scalars(select(Project).where(Project.team_id == t.id, Project.status == "active")))
    pkg = {
        "kind": KIND, "format": FORMAT, "name": name or f"Time {t.name}", "description": description,
        "exported_at": iso(now()), "exported_by": acc.p.name,
        "skills": [{"name": s.name, "description": s.description, "content": s.content, "examples": s.examples or "",
                    "test": s.test, "cases": s.cases or []}
                   for s in db.scalars(select(Skill).where(Skill.name.in_(skill_names)))] if skill_names else [],
        "goals": [{"ref": gref[g.id], "title": g.title, "description": g.description, "target_date": g.target_date,
                   "parent": gref.get(g.parent_id)} for g in goals],
        "employees": out_emps,
        "projects": [{"name": p.name, "slug": p.slug, "description": p.description, "instructions": p.instructions,
                      "goal": gref.get(p.goal_id), "default_employee": _slug(db, db.get(Employee, p.default_employee_id))
                      if p.default_employee_id else None} for p in projects]}
    audit(db, acc.p.name, "package.export", t.slug, f"{len(out_emps)} funcionário(s)")
    return pkg


# ------------------------------------------------------------------ importar
def parse(doc) -> dict:
    if isinstance(doc, str):
        try:
            doc = yaml.safe_load(doc)
        except yaml.YAMLError as e:
            raise PlatformError(f"pacote inválido (YAML): {e}") from None
    if not isinstance(doc, dict) or doc.get("kind") != KIND:
        raise PlatformError(f"isto não é um pacote (kind: {KIND})")
    if int(doc.get("format") or 1) > FORMAT:
        raise PlatformError("pacote de uma versão mais nova do Agent Hangar: atualize a plataforma")
    for k in ("skills", "goals", "employees", "projects"):
        if not isinstance(doc.get(k) or [], list):
            raise PlatformError(f"'{k}' precisa ser uma lista")
    return doc


def _free_slug(db: Session, slug: str, taken: set) -> str:
    base, n, s = slug, 2, slug
    while s in taken or db.scalar(unscoped(select(Agent.id).where(Agent.slug == s))):
        s, n = f"{base}-{n}", n + 1
    return s


def preview(db: Session, acc: Access, doc, team: str, manager: str) -> dict:
    from .access import user_by_email
    pkg = parse(doc)
    t = find_team(db, team)
    if not acc.p.is_admin and RANK.get(acc.teams.get(t.id, ""), 0) < RANK["maintainer"]:
        raise Forbidden("instala o pacote o mantenedor do time (ou um admin): ele cria metas, projetos e funcionários")
    problems = []
    if user_by_email(db, manager) is None:
        problems.append(f"gestor '{manager}' não é uma pessoa da plataforma")
    taken, emps = set(), []
    for e in pkg.get("employees") or []:
        want = str(e.get("slug") or e.get("name") or e.get("title") or "employee")
        new = _free_slug(db, want, taken)
        taken.add(new)
        emps.append({"from": want, "slug": new, "renamed": new != want, "title": e.get("title", "")})
        for pl in e.get("plugins") or []:
            p = db.scalar(select(Plugin).where(Plugin.name == pl))
            if p is None or p.status != "approved":
                problems.append(f"{want}: o plugin '{pl}' não está aprovado nesta instalação")
    skills = [{"name": s["name"], "exists": db.scalar(select(Skill.id).where(Skill.name == s["name"])) is not None}
              for s in pkg.get("skills") or []]
    projects = []
    for p in pkg.get("projects") or []:
        projects.append({"name": p.get("name"), "slug_taken": bool(db.scalar(select(Project.id).where(Project.slug == p.get("slug"))))})
    return {"name": pkg.get("name"), "description": pkg.get("description", ""), "team": t.slug, "employees": emps,
            "skills": skills, "goals": [g.get("title") for g in pkg.get("goals") or []], "projects": projects,
            "problems": problems, "ok": not problems}


def install(db: Session, acc: Access, doc, team: str, manager: str) -> dict:
    """Cria tudo como na prévia. Os funcionários nascem em rascunho (experiência e admissão continuam valendo)."""
    from .catalog import upsert_skill
    from .employees import hire, update
    from .goals import create_goal, create_project
    pv = preview(db, acc, doc, team, manager)
    if not pv["ok"]:
        raise PlatformError("resolva antes de instalar: " + "; ".join(pv["problems"]))
    pkg = parse(doc)
    t = find_team(db, team)
    created = {"skills": [], "goals": [], "employees": [], "projects": []}
    for s in pkg.get("skills") or []:
        if not db.scalar(select(Skill.id).where(Skill.name == s["name"])):
            upsert_skill(db, s["name"], s.get("description", ""), s.get("content", ""), acc.p.name, s.get("examples") or "",
                         s.get("test"), team_id=t.id, create_only=True)
            created["skills"].append(s["name"])
    gmap = {}
    for g in pkg.get("goals") or []:  # na ordem do pacote: a meta-pai vem antes
        made = create_goal(db, acc, g["title"], t.slug, gmap.get(g.get("parent")), g.get("description", ""), None,
                           "active", g.get("target_date", ""))
        gmap[g.get("ref")] = made["id"]
        created["goals"].append(g["title"])
    smap = {x["from"]: x["slug"] for x in pv["employees"]}
    for e in pkg.get("employees") or []:
        want = str(e.get("slug") or e.get("name") or e.get("title"))
        fields = {k: v for k, v in e.items() if k not in ("slug", "reports_to", "colleagues", "plan_policy", "review_policy",
                                                          "quality_sample")}
        fields.update(manager=manager, team=t.slug, name=e.get("name") or e.get("title"),
                      accept_default_authority=not e.get("authority"))
        out = hire(db, acc, "", **fields)
        if not out.get("created"):
            raise PlatformError(f"{want}: faltam campos para contratar: {', '.join(out.get('missing') or [])}")
        smap[want] = out["employee"]["slug"]
        if e.get("plugins"):  # os plugins entram na spec do agente (a instalação no time já foi conferida na prévia)
            from .registry import design_agent
            design_agent(db, smap[want], {"plugins": list(e["plugins"])}, acc.p.name, acc=acc)
        created["employees"].append(smap[want])
    for e in pkg.get("employees") or []:  # depois de todos criados: chefe, colegas e políticas
        want = str(e.get("slug") or e.get("name") or e.get("title"))
        changes = {k: e[k] for k in ("plan_policy", "review_policy", "quality_sample") if e.get(k) is not None}
        if e.get("reports_to") in smap:
            changes["reports_to"] = smap[e["reports_to"]]
        cols = [smap[c] for c in e.get("colleagues") or [] if c in smap]
        if cols:
            changes["colleagues"] = cols
        if changes:
            update(db, acc, smap[want], changes)
    for p in pkg.get("projects") or []:
        made = create_project(db, acc, p["name"], t.slug, p.get("description", ""), p.get("instructions", ""),
                              gmap.get(p.get("goal")), smap.get(p.get("default_employee")), p.get("slug") or "")
        created["projects"].append(made["slug"])
    audit(db, acc.p.name, "package.install", t.slug, f"{pkg.get('name')}: {len(created['employees'])} funcionário(s)")
    return {"name": pkg.get("name"), "team": t.slug, "created": created, "slugs": smap,
            "next": "comece o período de experiência de cada Digital employee (na página dele)"}


# ------------------------------------------------------------------ catálogo de times prontos
def catalog() -> list[dict]:
    out = []
    if not os.path.isdir(PACKAGES_DIR):
        return out
    for fn in sorted(os.listdir(PACKAGES_DIR)):
        if not fn.endswith((".yaml", ".yml")):
            continue
        with open(os.path.join(PACKAGES_DIR, fn), encoding="utf-8") as f:
            try:
                pkg = parse(f.read())
            except PlatformError:
                continue
        out.append({"id": fn.rsplit(".", 1)[0], "name": pkg.get("name"), "description": pkg.get("description", ""),
                    "employees": [{"name": e.get("name"), "title": e.get("title"), "reports_to": e.get("reports_to")}
                                  for e in pkg.get("employees") or []],
                    "projects": [p.get("name") for p in pkg.get("projects") or []],
                    "goals": [g.get("title") for g in pkg.get("goals") or []]})
    return out


def catalog_doc(pid: str) -> str:
    if not re.match(r"^[a-z0-9-]{1,60}$", pid or ""):
        raise PlatformError("pacote desconhecido")
    for ext in (".yaml", ".yml"):
        path = os.path.join(PACKAGES_DIR, pid + ext)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
    raise PlatformError("pacote desconhecido")
