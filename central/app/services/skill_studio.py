"""Skill Studio: editar uma skill, guardar entradas de teste, rodar os testes contra um LLM, ver o histórico de versões e
restaurar uma versão antiga (como uma versão nova).

- Edita um admin ou quem é developer/mantenedor do time dono da skill. Cada mudança sobe a versão e grava a revisão.
- Os casos são os da spec (input + expect_contains / expect_regex / judge). O teste "oficial" da skill (o que entra
  nos testes dos agentes montados com ela) continua em `test`; os casos do estúdio ficam em `cases`.
- Rodar: o conteúdo da skill vira o prompt de sistema e cada caso, a mensagem. Sem conexão de LLM (protocolo openai),
  a rodada é mock e o conteúdo não é avaliado. Cada rodada fica guardada com a versão, para comparar versões.
- Descontinuar marca a skill para não ser usada em agentes novos (aparece no catálogo).
"""
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..models import LlmConnection, Skill, SkillRevision, SkillTestRun, now
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso

MAX_CASES = 20


def _get(db: Session, name: str) -> Skill:
    s = db.scalar(select(Skill).where(Skill.name == name))
    if s is None:
        raise PlatformError(f"skill '{name}' não existe")
    return s


def can_edit(acc: Access, s: Skill) -> bool:
    return acc.p.is_admin or (s.team_id is not None and RANK.get(acc.teams.get(s.team_id, ""), 0) >= RANK["developer"])


def record(db: Session, s: Skill, who: str, note: str = "") -> SkillRevision:
    old = db.scalar(select(SkillRevision).where(SkillRevision.skill_id == s.id, SkillRevision.version == s.version))
    if old is not None:
        return old
    r = SkillRevision(skill_id=s.id, version=s.version, description=s.description, content=s.content,
                      examples=s.examples or "", test=s.test, changed_by=who, note=note[:300], at=now())
    db.add(r)
    db.commit()
    return r


def _last_run(db: Session, s: Skill) -> SkillTestRun | None:
    return db.scalar(select(SkillTestRun).where(SkillTestRun.skill_id == s.id).order_by(SkillTestRun.id.desc()))


def skill_dict(db: Session, s: Skill, acc: Access, detail: bool = False) -> dict:
    last = _last_run(db, s)
    d = {"name": s.name, "description": s.description, "version": s.version, "status": s.status or "active",
         "team_id": s.team_id, "cases": len(s.cases or []) + (1 if s.test else 0), "created_by": s.created_by,
         "last_run": {"version": last.version, "passed": last.passed, "at": iso(last.at)} if last else None,
         "can_edit": can_edit(acc, s)}
    if detail:
        record(db, s, s.created_by or "sistema", "versão inicial")  # skills antigas ganham a revisão atual
        d.update(content=s.content, examples=s.examples or "", test=s.test, case_list=s.cases or [],
                 revisions=[{"version": r.version, "changed_by": r.changed_by, "note": r.note, "at": iso(r.at),
                             "content": r.content, "description": r.description}
                            for r in db.scalars(select(SkillRevision).where(SkillRevision.skill_id == s.id)
                                                .order_by(SkillRevision.version.desc()).limit(30))],
                 runs=[{"id": r.id, "version": r.version, "passed": r.passed, "connection": r.connection, "by": r.by,
                        "at": iso(r.at), "results": r.results}
                       for r in db.scalars(select(SkillTestRun).where(SkillTestRun.skill_id == s.id)
                                           .order_by(SkillTestRun.id.desc()).limit(10))],
                 connections=[c.name for c in db.scalars(select(LlmConnection).where(LlmConnection.protocol == "openai"))])
    return d


def list_skills(db: Session, acc: Access) -> list[dict]:
    return [skill_dict(db, s, acc) for s in db.scalars(select(Skill).order_by(Skill.name))]


def detail(db: Session, acc: Access, name: str) -> dict:
    return skill_dict(db, _get(db, name), acc, True)


def save(db: Session, acc: Access, name: str, description: str, content: str, examples: str = "", test: dict | None = None,
         note: str = "") -> dict:
    from .catalog import upsert_skill
    s = db.scalar(select(Skill).where(Skill.name == name))
    if s is not None and not can_edit(acc, s):
        raise Forbidden("edita a skill um admin ou quem é developer do time dono")
    if s is None and not (acc.p.is_admin or any(RANK.get(r, 0) >= RANK["developer"] for r in acc.teams.values())):
        raise Forbidden("cria skills quem é developer de um time (ou um admin)")
    if not (content or "").strip():
        raise PlatformError("a skill precisa de conteúdo")
    team_id = s.team_id if s is not None else next((t for t, r in acc.teams.items() if RANK.get(r, 0) >= RANK["developer"]), None)
    if s is not None:
        record(db, s, s.created_by or "sistema", "antes da edição")
    s = upsert_skill(db, name, description, content, acc.p.name, examples, test, team_id=team_id)
    record(db, s, acc.p.name, note)
    return skill_dict(db, s, acc, True)


def set_cases(db: Session, acc: Access, name: str, cases: list[dict]) -> dict:
    from .. import spec as specmod
    s = _get(db, name)
    if not can_edit(acc, s):
        raise Forbidden("edita a skill um admin ou quem é developer do time dono")
    if len(cases or []) > MAX_CASES:
        raise PlatformError(f"no máximo {MAX_CASES} casos")
    clean = []
    for c in cases or []:
        try:
            clean.append(specmod.TestCase(**c).model_dump(exclude_none=True))
        except Exception as e:  # noqa: BLE001
            raise PlatformError(f"caso inválido: {e}") from None
    s.cases = clean
    db.commit()
    audit(db, acc.p.name, "skill.cases", name, f"{len(clean)} caso(s)")
    return skill_dict(db, s, acc, True)


def CALL(base: str, key: str, model: str, system: str, user: str) -> str:  # noqa: N802  (os testes trocam)
    r = httpx.post(f"{base}/chat/completions", timeout=120, headers={"Authorization": f"Bearer {key}"} if key else {},
                   json={"model": model, "temperature": 0,
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"].get("content") or ""


def run_tests(db: Session, acc: Access, name: str, connection: str | None = None) -> dict:
    from .testing import evaluate
    s = _get(db, name)
    if not can_edit(acc, s):
        raise Forbidden("roda os testes quem edita a skill")
    cases = ([{"name": "teste oficial", **s.test}] if s.test else []) + list(s.cases or [])
    if not cases:
        raise PlatformError("guarde pelo menos um caso de teste")
    conn = None
    if connection:
        conn = db.scalar(select(LlmConnection).where(LlmConnection.name == connection, LlmConnection.protocol == "openai"))
        if conn is None:
            raise PlatformError(f"conexão '{connection}' não existe (ou não é protocolo openai)")
    system = s.content + (f"\n\nExemplos:\n{s.examples}" if s.examples else "")
    spec = {"llm": {"connection": conn.name}, "judge": {"connection": conn.name}} if conn else {"llm": {}}
    results = []
    for n, c in enumerate(cases):
        try:
            out = (CALL(conn.base_url, crypto.decrypt(conn.api_key), conn.model_name, system, c["input"]) if conn
                   else f"[mock] {c['input']}")
            ok, why = evaluate(db, spec, c, out)
        except Exception as e:  # noqa: BLE001 — uma conexão fora do ar vira falha do caso, não da rodada
            ok, why, out = False, f"erro ao chamar o LLM: {type(e).__name__}: {str(e)[:200]}", ""
        results.append({"name": c.get("name") or f"caso {n + 1}", "input": c["input"][:500], "passed": ok, "detail": why[:400],
                        "output": out[:1500]})
    run = SkillTestRun(skill_id=s.id, version=s.version, connection=conn.name if conn else "mock", results=results,
                       passed=all(r["passed"] for r in results), by=acc.p.name, at=now())
    db.add(run)
    db.commit()
    audit(db, acc.p.name, "skill.test", name, f"v{s.version} {'ok' if run.passed else 'falhou'}")
    return {"id": run.id, "version": s.version, "passed": run.passed, "connection": run.connection, "results": results,
            "mock": conn is None}


def restore(db: Session, acc: Access, name: str, version: int) -> dict:
    s = _get(db, name)
    r = db.scalar(select(SkillRevision).where(SkillRevision.skill_id == s.id, SkillRevision.version == int(version)))
    if r is None:
        raise PlatformError(f"a versão v{version} não está no histórico")
    return save(db, acc, name, r.description, r.content, r.examples, r.test, f"restaurada a v{r.version}")


def set_status(db: Session, acc: Access, name: str, status: str) -> dict:
    s = _get(db, name)
    if not can_edit(acc, s):
        raise Forbidden("edita a skill um admin ou quem é developer do time dono")
    if status not in ("active", "deprecated"):
        raise PlatformError("status: active | deprecated")
    s.status = status
    db.commit()
    audit(db, acc.p.name, f"skill.{status}", name)
    return skill_dict(db, s, acc, True)
