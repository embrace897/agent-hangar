# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""Fase E: ferramentas da plataforma, análise sobre os arquivos da tarefa (Data Studio), extração com esquema e
conferência campo a campo, lote de documentos e calculadoras certificadas (quatro olhos e memória de cálculo)."""
import base64
import csv
import io
import json

import httpx
import pytest

from app.db import SessionLocal
from app.models import CalcRun, EmployeeTask, Extraction, TaskFile
from app.services import analysis, calculators, platform_tools, task_files
from app.services import employee_tasks as tasksmod

from .conftest import ADMIN
from .test_employees import assign, env, hire, make_active, open_request, rt, task, work  # noqa: F401


@pytest.fixture(autouse=True)
def _close_leftover_tasks():
    """O que o teste deixou aberto (respostas que acordam tarefas, briefings, ações de reunião) não pode ocupar a fila
    do executor nos testes seguintes."""
    with SessionLocal() as db:
        start = db.query(EmployeeTask.id).order_by(EmployeeTask.id.desc()).limit(1).scalar() or 0
    yield
    with SessionLocal() as db:
        for t in db.query(EmployeeTask).filter(EmployeeTask.id > start, EmployeeTask.status.notin_(tasksmod.TERMINAL)):
            t.status = "cancelled"
        db.commit()


def _task_with_file(client, env, slug, body, name, data: bytes, ctype="text/plain"):
    tid = assign(client, env, slug, body)
    with SessionLocal() as db:
        task_files.store(db, db.get(EmployeeTask, tid), name, data, "input", "teste", ctype)
        db.commit()
    return tid


def _files(tid) -> dict:
    with SessionLocal() as db:
        out = {}
        for f in db.query(TaskFile).filter_by(task_id=tid).order_by(TaskFile.version):
            out[f.name] = f
        return out


# ------------------------------------------------------------------ ferramentas da plataforma
def test_platform_tools_listed_per_employee_and_groups_can_be_turned_off(client, env, rt):
    slug = hire(client, env)
    groups = client.get(f"/api/employees/{slug}/platform-tools", headers=env["h"]["own"]).json()
    assert {"extraction", "calculators", "analysis"} <= {g["group"] for g in groups}
    with SessionLocal() as db:
        e = db.get(EmployeeTask, 1) and None
        from app.models import Agent, Employee
        a = db.query(Agent).filter_by(slug=slug).one()
        e = db.query(Employee).filter_by(agent_id=a.id).one()
        names = {t["name"] for t in platform_tools.tools_for(db, e)}
        assert {"extract_fields", "calculate", "list_calculators"} <= names
    assert client.put(f"/api/employees/{slug}/platform-tools", headers=env["h"]["out"], json={"disabled": ["calculators"]}).status_code in (403, 404)
    r = client.put(f"/api/employees/{slug}/platform-tools", headers=env["h"]["own"], json={"disabled": ["calculators"]})
    assert r.status_code == 200 and not next(g for g in r.json() if g["group"] == "calculators")["enabled"]
    assert client.put(f"/api/employees/{slug}/platform-tools", headers=env["h"]["own"], json={"disabled": ["xpto"]}).status_code == 400
    with SessionLocal() as db:
        e = db.get(Employee, e.id)
        assert "calculate" not in {t["name"] for t in platform_tools.tools_for(db, e)}
        assert platform_tools.run(db, a, None, "calculate", {})["status"] == "denied"  # fora de uma tarefa


# ------------------------------------------------------------------ análise (Data Studio falso)
class FakeDataStudio:
    def __init__(self):
        self.calls, self.files = [], {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        if url.endswith("/health"):
            return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))
        name = url.rsplit("/", 1)[-1]
        return httpx.Response(200, content=self.files.get(name, b""), request=httpx.Request("GET", url))

    def post(self, url, json=None, headers=None):
        name, args = json["params"]["name"], json["params"]["arguments"]
        self.calls.append((headers["x-session-id"], name, args))
        if name == "ingest_file":
            self.files[args["filename"]] = base64.b64decode(args["data_base64"])
            res = {"structuredContent": {"path": args["filename"], "new": True}}
        elif name == "run_python":
            self.files["conciliacao.xlsx"] = b"PK-fake-xlsx"
            res = {"structuredContent": {"stdout": "3 divergências", "files": [
                {"file": "conciliacao.xlsx", "url": "http://localhost:8095/f/sid/tok/conciliacao.xlsx"}]}}
        elif name == "create_presentation":
            assert args["accent"] == "#0B5FFF" and args["footer"] == "Acme · interno"
            self.files["resumo.pptx"] = b"PK-fake-pptx"
            res = {"structuredContent": {"files": [{"file": "resumo.pptx", "url": "http://localhost:8095/f/s/t/resumo.pptx"}]}}
        else:
            res = {"content": [{"type": "text", "text": f"{name} ok"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": res}, request=httpx.Request("POST", url))


@pytest.fixture()
def ds(monkeypatch):
    fake = FakeDataStudio()
    monkeypatch.setattr(analysis, "HTTP", lambda timeout=330: fake)
    monkeypatch.setattr(analysis, "_UP", (0.0, False))
    return fake


def test_analysis_runs_over_the_task_files_and_brings_deliverables_back(client, env, rt, ds):
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    assert client.put("/api/brand", headers=env["h"]["own"], json={"accent": "#0B5FFF"}).status_code == 403
    b = client.put("/api/brand", headers=ADMIN, json={"accent": "#0B5FFF", "footer": "Acme · interno"})
    assert b.status_code == 200 and b.json()["data_studio"] is True
    code = "df = pd.read_csv('extrato.csv'); df.to_excel('conciliacao.xlsx')"
    tid = _task_with_file(client, env, slug, f"use analyze_data {json.dumps({'code': code})}", "extrato.csv", b"a,b\n1,2\n")
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and "3 divergências" in t["result"] and "conciliacao.xlsx v1" in t["result"]
    files = _files(tid)
    assert files["conciliacao.xlsx"].kind == "output" and files["conciliacao.xlsx"].data == b"PK-fake-xlsx"
    sid, name, args = ds.calls[0]
    assert name == "ingest_file" and args["filename"] == "extrato.csv" and sid.endswith(f"-task-{tid}")
    assert any(e["kind"] == "analysis" for e in t["events"])
    # entregável formatado com a identidade visual da empresa
    spec = {"kind": "presentation", "title": "Resumo", "slides": [{"type": "bullets", "title": "Achados", "bullets": ["x"]}]}
    tid2 = assign(client, env, slug, f"use create_deliverable {json.dumps(spec)}")
    work()
    assert "resumo.pptx v1" in task(client, env, tid2)["result"]
    client.put("/api/brand", headers=ADMIN, json={"accent": "", "footer": ""})


# ------------------------------------------------------------------ extração com conferência
FIELDS = [{"name": "contratante", "label": "Contratante", "type": "text", "required": True},
          {"name": "cnpj", "label": "CNPJ", "type": "cnpj", "required": True},
          {"name": "valor", "label": "Valor", "type": "money", "required": True},
          {"name": "vigencia", "label": "Vigência", "type": "date"}]


def _schema(client, env, name="contrato"):
    r = client.put("/api/extraction/schemas", headers=env["h"]["own"],
                   json={"name": name, "title": "Contrato", "fields": FIELDS, "min_confidence": 0.8, "team": env["team"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_extraction_with_field_review_and_corrections(client, env, rt):
    s = _schema(client, env)
    assert s["version"] == 1 and [f["type"] for f in s["fields"]] == ["text", "cnpj", "money", "date"]
    assert client.put("/api/extraction/schemas", headers=env["h"]["out"], json={"name": "contrato", "fields": FIELDS}).status_code == 403
    assert client.put("/api/extraction/schemas", headers=env["h"]["own"],
                      json={"name": "x", "team": env["team"], "fields": [{"name": "a", "type": "nope"}]}).status_code == 400
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    # tudo legível e válido: vai direto, sem conferência
    doc = "Contratante: ACME Ltda\nCNPJ: 11.222.333/0001-81\nValor: R$ 12.500,00\nVigência: 31/12/2026\n"
    body = f"use extract_fields {json.dumps({'file': 'contrato.txt', 'schema': 'contrato'})}"
    tid = _task_with_file(client, env, slug, body, "contrato.txt", doc.encode())
    work()
    t = task(client, env, tid)
    assert t["status"] == "done" and '"valor": 12500.0' in t["result"] and '"vigencia": "2026-12-31"' in t["result"]
    out = json.loads(_files(tid)["extracao-contrato.json"].data)
    assert out["fields"]["cnpj"] == "11.222.333/0001-81" and out["status"] == "auto"
    # CNPJ inválido e valor faltando: uma pessoa confere só esses dois
    doc2 = "Contratante: Beta SA\nCNPJ: 11.222.333/0001-00\nVigência: 01/02/2027\n"
    tid2 = _task_with_file(client, env, slug, body.replace("contrato.txt", "beta.txt"), "beta.txt", doc2.encode())
    work()
    assert task(client, env, tid2)["status"] == "waiting_human"
    r = open_request(client, env, tid2)
    assert r["kind"] == "review_fields"
    fields = r["payload"]["fields"]
    assert fields["cnpj"]["error"] == "CNPJ inválido" and fields["valor"]["error"] == "obrigatório"
    assert not fields["contratante"]["review"] and fields["contratante"]["evidence"].startswith("Contratante")
    bad = client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"], json={"decision": "approve", "edit": {"fields": {"cnpj": "123"}}})
    assert bad.status_code == 400  # corrigido ainda inválido
    ok = client.post(f"/api/decisions/{r['id']}", headers=env["h"]["mgr"],
                     json={"decision": "approve", "edit": {"fields": {"cnpj": "11222333000181", "valor": "8.000,00"}}})
    assert ok.status_code == 200, ok.text
    work()
    assert task(client, env, tid2)["status"] == "done"
    with SessionLocal() as db:
        ex = db.query(Extraction).filter_by(task_id=tid2).one()
        assert ex.status == "reviewed" and ex.corrections == 2 and ex.values["valor"]["value"] == 8000.0
        assert ex.values["cnpj"]["corrected_from"] == "11.222.333/0001-00"
    got = client.get(f"/api/tasks/{tid2}/extractions", headers=env["h"]["own"]).json()
    assert got[0]["status"] == "reviewed" and got[0]["schema"] == "contrato"
    assert client.get(f"/api/tasks/{tid2}/extractions", headers=env["h"]["out"]).status_code in (403, 404)


def test_batch_extracts_each_file_then_consolidates(client, env, rt):
    _schema(client, env, "nota")
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    docs = {f"nf{i}.txt": f"Contratante: Cliente {i}\nCNPJ: 11.222.333/0001-81\nValor: {i}00,00\n" for i in (1, 2, 3)}
    files = [{"name": n, "content_base64": base64.b64encode(d.encode()).decode(), "content_type": "text/plain"}
             for n, d in docs.items()]
    r = client.post(f"/api/employees/{slug}/batch", headers=env["h"]["req"], json={"title": "Notas de outubro", "schema": "nota", "files": files})
    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["tasks"]) == 3 and task(client, env, b["parent"])["status"] == "blocked"
    for _ in range(3):
        work()
    assert all(task(client, env, t)["status"] == "done" for t in b["tasks"])
    p = task(client, env, b["parent"])
    assert p["status"] == "done", p["status"]
    rows = list(csv.DictReader(io.StringIO(_files(b["parent"])["nota-consolidado.csv"].data.decode("utf-8-sig"))))
    assert sorted(r["arquivo"] for r in rows) == ["nf1.txt", "nf2.txt", "nf3.txt"] and {r["valor"] for r in rows} == {"100.0", "200.0", "300.0"}


# ------------------------------------------------------------------ calculadoras certificadas
CALC = {"name": "multa_rescisoria", "title": "Multa rescisória", "requires_review": True,
        "inputs": [{"name": "valor_mensal", "type": "number", "unit": "R$"}, {"name": "meses_restantes", "type": "number"},
                   {"name": "percentual", "type": "number"}],
        "steps": [{"name": "saldo", "expr": "valor_mensal * meses_restantes"},
                  {"name": "multa", "expr": "round(pct(saldo, percentual), 2)"},
                  {"name": "teto", "expr": "min(multa, valor_mensal * 3)"}],
        "outputs": ["teto"],
        "cases": [{"name": "abaixo do teto", "inputs": {"valor_mensal": 1000, "meses_restantes": 10, "percentual": 20},
                   "expected": {"teto": "2000.00"}},
                  {"name": "no teto", "inputs": {"valor_mensal": 1000, "meses_restantes": 30, "percentual": 20},
                   "expected": {"teto": 3000}}]}


def test_safe_formula_language():
    ev = calculators.evaluate
    assert str(ev("0.1 + 0.2", {})) == "0.3"  # Decimal, sem erro de ponto flutuante
    assert str(ev("round(2.675, 2)", {})) == "2.68"  # meio para cima (finanças)
    assert ev("sum([x * 2 for x in xs if x > 1])", {"xs": [calculators._d(v) for v in (1, 2, 3)]}) == 10
    assert ev("'alto' if v > 10 else 'baixo'", {"v": calculators._d(11)}) == "alto"
    assert ev("days_between('2026-01-01', '2026-03-01')", {}) == 59
    for bad in ("__import__('os')", "(1).__class__", "open('x')", "2 ** 1000", "[x for x in y for z in y]", "lambda: 1"):
        with pytest.raises(calculators.CalcError):
            ev(bad, {"y": [1]})
    with pytest.raises(calculators.CalcError):
        ev("1 / 0", {})


def test_calculator_four_eyes_certification_and_use_with_calc_memory(client, env, rt):
    r = client.put("/api/calculators", headers=env["h"]["own"], json={**CALC, "team": env["team"]})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["status"] == "draft" and c["last_test"]["passed"] == 2 and c["edited_by"]
    assert client.post("/api/calculators/multa_rescisoria/certify", headers=env["h"]["own"]).status_code == 403  # quatro olhos
    assert client.post("/api/calculators/multa_rescisoria/certify", headers=env["h"]["mgr"]).json()["certified"]["version"] == 1
    sim = client.post("/api/calculators/multa_rescisoria/test", headers=env["h"]["req"],
                      json={"inputs": {"valor_mensal": 500, "meses_restantes": 4, "percentual": 10}}).json()
    assert sim["outputs"] == {"teto": "200.00"} and [s["name"] for s in sim["trace"]] == ["saldo", "multa", "teto"]
    # um caso quebrado impede a certificação da versão nova; a v1 certificada continua valendo
    broken = {**CALC, "steps": [*CALC["steps"][:2], {"name": "teto", "expr": "multa"}]}
    v2 = client.put("/api/calculators", headers=env["h"]["mgr"], json=broken).json()
    assert v2["version"] == 2 and v2["pending_changes"] and v2["last_test"]["failed"] == 1
    assert client.post("/api/calculators/multa_rescisoria/certify", headers=env["h"]["own"]).status_code == 400
    slug = hire(client, env)
    make_active(client, env, slug, rt)
    args = {"calculator": "multa_rescisoria", "inputs": {"valor_mensal": 1000, "meses_restantes": 30, "percentual": 20}}
    tid = assign(client, env, slug, f"use calculate {json.dumps(args)}")
    work()
    t = task(client, env, tid)
    assert t["status"] == "in_review"  # a calculadora exige revisão humana da tarefa
    memo = json.loads(_files(tid)["memoria-de-calculo-multa_rescisoria.json"].data)
    assert memo["version"] == 1 and memo["outputs"] == {"teto": "3000"} and memo["certified_by"]
    assert [s["value"] for s in memo["steps"]] == ["30000", "6000.00", "3000"]
    with SessionLocal() as db:
        assert db.query(CalcRun).filter_by(task_id=tid).one().version == 1
    det = client.get("/api/calculators/multa_rescisoria", headers=env["h"]["own"]).json()
    assert det["runs"][0]["task"] == tid
    with SessionLocal() as db:  # sem certificação, o funcionário não usa
        from app.models import Agent
        agent = db.query(Agent).filter_by(slug=slug).one()
        tid3 = assign(client, env, slug, "outra conta")
        assert client.put("/api/calculators", headers=env["h"]["own"], json={**CALC, "name": "nova_conta", "team": env["team"]}).status_code == 200
        g = tasksmod.gate(db, agent, tid3, "calculate", "platform:calculate", {"calculator": "nova_conta", "inputs": {}}, "", "platform")
        assert g["status"] == "allowed" and "não tem versão certificada" in g["result"]
