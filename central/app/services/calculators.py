"""Calculadoras certificadas: o número que precisa estar certo (provisão, imposto, comissão, multa, reajuste) sai de uma
fórmula versionada e testada — não da "cabeça" do LLM.

- Passos com fórmulas numa linguagem segura (aritmética em Decimal, comparações, `a if cond else b`, listas e uma
  lista fechada de funções); cada passo vê as entradas e os passos anteriores. Sem eval, sem import, sem atributos.
- Casos de teste (entradas → saídas esperadas, com tolerância). Certificar exige todos os casos passando e outra
  pessoa (quatro olhos): quem editou por último não certifica.
- O Digital employee só usa a versão certificada (`calculate`); editar cria um rascunho e a certificada continua
  valendo até a nova ser certificada. Cada conta fica guardada com a memória de cálculo (entradas, fórmula e valor de
  cada passo, versão) — e a calculadora pode exigir revisão humana da tarefa que a usou.
"""
import ast
import json
import math
import re
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from sqlalchemy import select

from ..models import CalcRun, Calculator, Team, now
from . import task_files
from .access import RANK, Access, Forbidden
from .common import PlatformError, audit, iso, slugify
from .platform_tools import Ctx, tool

MAX_STEPS, MAX_EXPR, MAX_NUM = 80, 2000, Decimal("1e30")


class CalcError(PlatformError):
    pass


# ------------------------------------------------------------------ linguagem segura
def _d(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):  # "1234.56" ou "1234,56" vira número; datas e textos ficam como estão
        s = v.strip()
        return Decimal(s.replace(",", ".")) if re.fullmatch(r"-?\d+(?:[.,]\d+)?", s) else v
    if isinstance(v, list):
        return [_d(x) for x in v]
    if isinstance(v, dict):
        return {k: _d(x) for k, x in v.items()}
    return v


def _round(x, n=0, mode="half_up"):
    q = Decimal(1).scaleb(-int(n))
    return Decimal(x).quantize(q, rounding=ROUND_HALF_EVEN if mode == "half_even" else ROUND_HALF_UP)


def _date(s) -> date:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        raise CalcError(f"data inválida: {s} (use AAAA-MM-DD)") from None


FUNCS = {
    "round": _round, "abs": lambda x: abs(x), "min": lambda *a: min(a[0] if len(a) == 1 else a),
    "max": lambda *a: max(a[0] if len(a) == 1 else a), "sum": lambda xs: sum(xs, Decimal(0)), "len": lambda xs: Decimal(len(xs)),
    "floor": lambda x: Decimal(math.floor(x)), "ceil": lambda x: Decimal(math.ceil(x)),
    "pct": lambda x, p: Decimal(x) * Decimal(p) / Decimal(100),
    "days_between": lambda a, b: Decimal((_date(b) - _date(a)).days),
    "months_between": lambda a, b: Decimal((_date(b).year - _date(a).year) * 12 + _date(b).month - _date(a).month),
    "number": lambda x: Decimal(str(x).replace(",", ".")), "text": lambda x: str(x),
}
BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
       ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b}
CMP = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
       ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
       ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}


def _check(v):
    if isinstance(v, Decimal) and v.is_finite() and abs(v) > MAX_NUM:
        raise CalcError("número grande demais")
    if isinstance(v, str) and len(v) > 10_000:
        raise CalcError("texto grande demais")
    if isinstance(v, list) and len(v) > 10_000:
        raise CalcError("lista grande demais")
    return v


def evaluate(expr: str, scope: dict):
    if len(expr) > MAX_EXPR:
        raise CalcError("fórmula longa demais")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"fórmula inválida: {e.msg}") from None

    def ev(n, sc):
        if isinstance(n, ast.Expression):
            return ev(n.body, sc)
        if isinstance(n, ast.Constant):
            if isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
                return Decimal(str(n.value))
            if isinstance(n.value, (str, bool)) or n.value is None:
                return n.value
            raise CalcError("constante não permitida")
        if isinstance(n, ast.Name):
            if n.id in sc:
                return sc[n.id]
            if n.id in ("True", "False", "None"):
                return {"True": True, "False": False, "None": None}[n.id]
            raise CalcError(f"'{n.id}' não existe (entradas e passos anteriores)")
        if isinstance(n, ast.BinOp):
            if isinstance(n.op, ast.Pow):
                base, exp = ev(n.left, sc), ev(n.right, sc)
                if abs(exp) > 100:
                    raise CalcError("expoente grande demais")
                return _check(Decimal(base) ** Decimal(exp))
            op = BIN.get(type(n.op))
            if op is None:
                raise CalcError("operador não permitido")
            try:
                return _check(op(ev(n.left, sc), ev(n.right, sc)))
            except (ZeroDivisionError, InvalidOperation):
                raise CalcError("divisão por zero") from None
            except TypeError as e:
                raise CalcError(f"tipos incompatíveis: {e}") from None
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand, sc)
            if isinstance(n.op, ast.USub):
                return -v
            if isinstance(n.op, ast.UAdd):
                return +v
            if isinstance(n.op, ast.Not):
                return not v
            raise CalcError("operador não permitido")
        if isinstance(n, ast.BoolOp):
            vals = [ev(v, sc) for v in n.values]
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.Compare):
            left = ev(n.left, sc)
            for op, right in zip(n.ops, n.comparators, strict=True):
                r = ev(right, sc)
                f = CMP.get(type(op))
                if f is None or not f(left, r):
                    return False
                left = r
            return True
        if isinstance(n, ast.IfExp):
            return ev(n.body, sc) if ev(n.test, sc) else ev(n.orelse, sc)
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in FUNCS or n.keywords:
                raise CalcError(f"função não permitida (use: {', '.join(sorted(FUNCS))})")
            try:
                return _check(FUNCS[n.func.id](*[ev(a, sc) for a in n.args]))
            except (TypeError, ValueError, InvalidOperation) as e:
                raise CalcError(f"{n.func.id}: {e}") from None
        if isinstance(n, (ast.List, ast.Tuple)):
            return [ev(x, sc) for x in n.elts]
        if isinstance(n, ast.Dict):
            return {ev(k, sc): ev(v, sc) for k, v in zip(n.keys, n.values, strict=True)}
        if isinstance(n, ast.Subscript):
            base, key = ev(n.value, sc), ev(n.slice, sc)
            try:
                return base[int(key)] if isinstance(base, list) else base[key]
            except (KeyError, IndexError, TypeError):
                raise CalcError("índice inexistente") from None
        if isinstance(n, (ast.ListComp, ast.GeneratorExp)):
            if len(n.generators) != 1 or not isinstance(n.generators[0].target, ast.Name):
                raise CalcError("só um 'for' simples por lista")
            g = n.generators[0]
            out = []
            for item in ev(g.iter, sc):
                inner = {**sc, g.target.id: item}
                if all(ev(c, inner) for c in g.ifs):
                    out.append(ev(n.elt, inner))
            return _check(out)
        raise CalcError(f"construção não permitida: {type(n).__name__}")

    with localcontext() as c:
        c.prec = 34
        return ev(tree, dict(scope))


def compute(spec: dict, inputs: dict) -> tuple[dict, list]:
    """Roda os passos com as entradas; (saídas, memória de cálculo)."""
    scope = {}
    for i in spec.get("inputs") or []:
        if i["name"] not in inputs and i.get("required", True):
            raise CalcError(f"falta a entrada '{i['name']}'")
        scope[i["name"]] = _d(inputs.get(i["name"], i.get("default")))
    trace = []
    for st in spec.get("steps") or []:
        v = evaluate(st["expr"], scope)
        scope[st["name"]] = v
        trace.append({"name": st["name"], "label": st.get("label") or st["name"], "expr": st["expr"], "value": _out(v)})
    outs = spec.get("outputs") or ([spec["steps"][-1]["name"]] if spec.get("steps") else [])
    return {o: _out(scope.get(o)) for o in outs}, trace


def _out(v):
    if isinstance(v, Decimal):  # como saiu da conta: round(x, 2) mostra as duas casas
        return format(v, "f")
    if isinstance(v, list):
        return [_out(x) for x in v]
    if isinstance(v, dict):
        return {k: _out(x) for k, x in v.items()}
    return v


def _same(got, want, tol) -> bool:
    try:
        return abs(Decimal(str(got)) - Decimal(str(want))) <= Decimal(str(tol or 0))
    except (InvalidOperation, TypeError, ValueError):
        return str(got) == str(want)


def run_cases(spec: dict, cases: list) -> dict:
    results = []
    for c in cases or []:
        try:
            outs, _ = compute(spec, c.get("inputs") or {})
            bad = {k: {"want": v, "got": outs.get(k)} for k, v in (c.get("expected") or {}).items()
                   if not _same(outs.get(k), v, c.get("tolerance"))}
            results.append({"name": c.get("name") or "", "passed": not bad, "diff": bad, "outputs": outs})
        except CalcError as e:
            results.append({"name": c.get("name") or "", "passed": False, "error": str(e)})
    return {"passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results), "results": results}


# ------------------------------------------------------------------ gestão
def _spec(c: Calculator) -> dict:
    return {"inputs": c.inputs, "steps": c.steps, "outputs": c.outputs}


def calc_dict(c: Calculator, acc: Access | None = None) -> dict:
    cert = c.certified or {}
    return {"id": c.id, "name": c.name, "title": c.title or c.name, "description": c.description, "team": c.team_id,
            "inputs": c.inputs, "steps": c.steps, "outputs": c.outputs, "cases": c.cases, "version": c.version,
            "status": c.status, "requires_review": c.requires_review, "edited_by": c.edited_by,
            "certified": {"version": cert.get("version"), "by": cert.get("by"), "at": cert.get("at")} if cert else None,
            "pending_changes": bool(cert) and cert.get("version") != c.version, "last_test": c.last_test,
            "can_edit": bool(acc and _can_edit(acc, c.team_id)), "updated_at": iso(c.updated_at) if c.updated_at else None}


def _can_edit(acc: Access, team_id: int | None) -> bool:
    return acc.p.is_admin or (team_id is not None and RANK.get(acc.teams.get(team_id, ""), 0) >= RANK["developer"])


def _get(db, name: str) -> Calculator:
    c = db.scalar(select(Calculator).where(Calculator.name == slugify(name or "").replace("-", "_")))
    if c is None:
        raise PlatformError(f"calculadora '{name}' não existe")
    return c


def list_calcs(db, acc: Access) -> list[dict]:
    return [calc_dict(c, acc) for c in db.scalars(select(Calculator).order_by(Calculator.name))]


def detail(db, acc: Access, name: str) -> dict:
    c = _get(db, name)
    d = calc_dict(c, acc)
    d["runs"] = [{"id": r.id, "version": r.version, "task": r.task_id, "inputs": r.inputs, "outputs": r.outputs,
                  "by": r.by, "at": iso(r.at)} for r in db.scalars(select(CalcRun).where(CalcRun.calculator_id == c.id)
                                                                   .order_by(CalcRun.id.desc()).limit(30))]
    return d


def _clean_spec(inputs, steps, outputs) -> tuple[list, list, list]:
    ins, names = [], set()
    for i in inputs or []:
        n = slugify(str(i.get("name") or "")).replace("-", "_")
        if not n or n in names or n in FUNCS:
            raise PlatformError(f"entrada sem nome, repetida ou reservada: {i}")
        names.add(n)
        ins.append({"name": n, "label": str(i.get("label") or n)[:120], "type": i.get("type") or "number",
                    "unit": str(i.get("unit") or "")[:20], "required": i.get("required", True) is not False,
                    "default": i.get("default")})
    sts = []
    for s in steps or []:
        n = slugify(str(s.get("name") or "")).replace("-", "_")
        if not n or n in names or n in FUNCS:
            raise PlatformError(f"passo sem nome, repetido ou reservado: {s}")
        expr = str(s.get("expr") or "").strip()
        if not expr:
            raise PlatformError(f"o passo {n} precisa de uma fórmula")
        try:
            ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise PlatformError(f"fórmula do passo {n}: {e.msg}") from None
        names.add(n)
        sts.append({"name": n, "expr": expr[:MAX_EXPR], "label": str(s.get("label") or n)[:160]})
    if not sts or len(sts) > MAX_STEPS:
        raise PlatformError(f"entre 1 e {MAX_STEPS} passos")
    outs = [slugify(str(o)).replace("-", "_") for o in (outputs or [sts[-1]["name"]])]
    missing = [o for o in outs if o not in {s["name"] for s in sts}]
    if missing:
        raise PlatformError(f"saídas que não são passos: {', '.join(missing)}")
    return ins, sts, outs


def save(db, acc: Access, name: str, title: str = "", description: str = "", inputs=None, steps=None, outputs=None,
         cases=None, requires_review: bool | None = None, team: str = "") -> dict:
    key = slugify(name or title).replace("-", "_")[:80]
    if not key:
        raise PlatformError("dê um nome à calculadora")
    team_id = None
    if team:
        t = db.scalar(select(Team).where(Team.slug == team))
        if t is None:
            raise PlatformError(f"time '{team}' não encontrado")
        team_id = t.id
    c = db.scalar(select(Calculator).where(Calculator.name == key))
    if not _can_edit(acc, team_id if c is None else c.team_id):
        raise Forbidden("só admins e quem desenvolve no time editam calculadoras")
    ins, sts, outs = _clean_spec(inputs, steps, outputs)
    clean_cases = [{"name": str(x.get("name") or f"caso {i + 1}")[:120], "inputs": x.get("inputs") or {},
                    "expected": x.get("expected") or {}, "tolerance": x.get("tolerance") or 0}
                   for i, x in enumerate(cases or [])]
    if c is None:
        c = Calculator(name=key, team_id=team_id, version=0, status="draft")
        db.add(c)
    changed = (c.inputs, c.steps, c.outputs, c.cases) != (ins, sts, outs, clean_cases) or c.version == 0
    c.title, c.description = (title or key)[:200], (description or "")[:4000]
    c.inputs, c.steps, c.outputs, c.cases = ins, sts, outs, clean_cases
    if requires_review is not None:
        c.requires_review = bool(requires_review)
    if changed:
        c.version, c.edited_by = (c.version or 0) + 1, acc.p.name
        c.status = "draft" if not c.certified else "certified"  # a certificada continua valendo até a nova ser certificada
    c.updated_at = now()
    c.last_test = {"version": c.version, **run_cases(_spec(c), c.cases), "at": now().isoformat()}
    db.commit()
    audit(db, acc.p.name, "calculator.save", key, f"v{c.version}")
    return calc_dict(c, acc)


def test(db, acc: Access, name: str, inputs: dict | None = None) -> dict:
    c = _get(db, name)
    if inputs is not None:  # simulação livre na tela (sem guardar)
        try:
            outs, trace = compute(_spec(c), inputs)
        except CalcError as e:
            return {"error": str(e)}
        return {"outputs": outs, "trace": trace}
    c.last_test = {"version": c.version, **run_cases(_spec(c), c.cases), "at": now().isoformat()}
    db.commit()
    return c.last_test


def certify(db, acc: Access, name: str) -> dict:
    c = _get(db, name)
    if not _can_edit(acc, c.team_id):
        raise Forbidden("só admins e quem desenvolve no time certificam calculadoras")
    if acc.p.name == c.edited_by:
        raise Forbidden("quatro olhos: quem editou por último não certifica — peça a outra pessoa")
    if not c.cases:
        raise PlatformError("cadastre pelo menos um caso de teste antes de certificar")
    res = run_cases(_spec(c), c.cases)
    c.last_test = {"version": c.version, **res, "at": now().isoformat()}
    if res["failed"]:
        db.commit()
        raise PlatformError(f"{res['failed']} caso(s) falhando: corrija antes de certificar")
    c.certified = {"version": c.version, **_spec(c), "by": acc.p.name, "at": now().isoformat()}
    c.status = "certified"
    db.commit()
    audit(db, acc.p.name, "calculator.certify", c.name, f"v{c.version}")
    return calc_dict(c, acc)


def deprecate(db, acc: Access, name: str) -> dict:
    c = _get(db, name)
    if not _can_edit(acc, c.team_id):
        raise Forbidden("sem permissão para esta calculadora")
    c.status = "deprecated"
    db.commit()
    return calc_dict(c, acc)


# ------------------------------------------------------------------ uso pelo Digital employee
@tool("list_calculators", "As calculadoras certificadas da empresa (provisões, impostos, comissões, reajustes…), com as "
      "entradas de cada uma. Para qualquer número que uma delas cobre, use calculate — nunca calcule de cabeça.",
      {"properties": {}}, "calculators")
def list_calculators(ctx: Ctx, args: dict):
    return [{"name": c.name, "title": c.title, "description": c.description[:300],
             "inputs": [f"{i['name']} ({i['type']}{', ' + i['unit'] if i.get('unit') else ''})" for i in (c.certified or {}).get("inputs", [])],
             "outputs": (c.certified or {}).get("outputs", []), "version": (c.certified or {}).get("version")}
            for c in ctx.db.scalars(select(Calculator).where(Calculator.status == "certified").order_by(Calculator.name))
            if c.certified]


@tool("calculate", "Faz a conta com uma calculadora certificada e devolve as saídas e a memória de cálculo (cada passo, "
      "a fórmula e o valor). Cite a calculadora e a versão no resultado.",
      {"properties": {"calculator": {"type": "string"}, "inputs": {"type": "object"}}, "required": ["calculator", "inputs"]},
      "calculators")
def calculate(ctx: Ctx, args: dict):
    c = _get(ctx.db, str(args.get("calculator") or ""))
    if c.status == "deprecated" or not c.certified:
        raise PlatformError(f"'{c.name}' não tem versão certificada em uso")
    cert = c.certified
    try:
        outs, trace = compute(cert, args.get("inputs") or {})
    except CalcError as e:
        raise PlatformError(f"{c.name} v{cert['version']}: {e}") from None
    run = CalcRun(calculator_id=c.id, version=cert["version"], task_id=ctx.task.id, inputs=args.get("inputs") or {},
                  outputs=outs, trace=trace, by=f"agent:{ctx.agent.slug}", at=now())
    ctx.db.add(run)
    ctx.db.flush()
    memo = {"calculator": c.name, "title": c.title, "version": cert["version"], "certified_by": cert.get("by"),
            "run": run.id, "inputs": run.inputs, "outputs": outs, "steps": trace}
    task_files.store(ctx.db, ctx.task, f"memoria-de-calculo-{c.name}.json",
                     json.dumps(memo, ensure_ascii=False, indent=2).encode(), "output", f"agent:{ctx.agent.slug}",
                     "application/json", f"{c.title} v{cert['version']} — conta #{run.id}")
    from . import employee_tasks as tasksmod
    tasksmod.event(ctx.db, ctx.task, "calc", {"calculator": c.name, "version": cert["version"], "run": run.id,
                                              "outputs": outs, "review": c.requires_review})
    if c.requires_review and not ctx.task.review_required:  # número sensível: uma pessoa revisa antes de concluir
        ctx.task.review_required = True
    ctx.db.commit()
    return memo
