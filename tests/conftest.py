"""Testes unitários/integração da central: SQLite em arquivo temporário, Docker sempre mockado."""
import os
import sys
import tempfile
import threading

import pytest
from cryptography.fernet import Fernet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_tmp = tempfile.mkdtemp(prefix="hangar-test-")
os.environ.update({
    "DATABASE_URL": f"sqlite:///{os.path.join(_tmp, 'hangar.db')}",
    "ADMIN_TOKEN": "test-admin-token",
    "HANGAR_SECRET_KEY": Fernet.generate_key().decode(),
    "INTERNAL_SECRET": "test-internal-secret",
    "TEMPLATES_DIR": os.path.join(ROOT, "templates"),
    "DEFAULT_MODEL": "mock/echo",
    "SCHEDULER_ENABLED": "0",  # os testes chamam tick()/execute() direto
    "GUIDE_AUTOGEN": "0",  # os testes chamam guides.generate_draft() direto
    "DATA_STUDIO_URL": "http://data-studio.test/mcp",  # os testes trocam analysis.HTTP
    "DATA_STUDIO_FILES_URL": "http://data-studio.test:8001",
    "MODEL_CATALOG_URL": "",  # nada de baixar a tabela pública de modelos nos testes
})
sys.path.insert(0, os.path.join(ROOT, "central"))

from app import db as dbmod  # noqa: E402
from app import deploy  # noqa: E402

dbmod.migrate()


def _no_docker(*a, **k):
    raise AssertionError("teste tentou falar com o Docker real")


# Nada nos testes pode alcançar o Docker de verdade (limparia containers reais de job, por exemplo).
deploy.client = _no_docker
deploy.cleanup_job_containers = lambda *a, **k: None
deploy.states = lambda max_age=3.0: {}


def _no_models_api(url, *a, **k):  # nenhum teste pergunta a lista de modelos a um provedor de verdade
    from app.services.llm_models import ModelsError
    raise ModelsError(f"sem rede nos testes: {url}")


from app.services import llm_models  # noqa: E402

llm_models._get = _no_models_api

ADMIN = {"Authorization": "Bearer test-admin-token"}


class FakeDocker:
    """Substitui as funções de app.deploy que falam com o Docker."""

    def __init__(self):
        self.running: dict[str, str] = {}
        self.jobs: list[dict] = []
        self.removed_data: list[str] = []  # volumes de dados apagados (agente excluído)
        self.job_result = {"status": "passed", "result": "done", "diff": "", "logs": "",
                           "usage": {"input_tokens": 1000, "output_tokens": 500}}

    def run_agent(self, slug, env, spec, llm_conf):
        self.running[deploy.container_name(slug, env)] = "running"

        class C:
            id = "abc123def456789"
        return C()

    def stop_agent(self, slug, env, remove_data=False):
        self.running.pop(deploy.container_name(slug, env), None)
        if remove_data:
            self.removed_data.append(deploy.container_name(slug, env))

    def states(self, max_age=3.0):
        return dict(self.running)

    def run_job_container(self, name, image, environment, mem_limit, cpus):
        """Simula o container do job: responde pelo mesmo caminho do callback HTTP."""
        self.jobs.append({"name": name, "image": image, "env": environment})
        job_id = int(environment["JOB_CALLBACK_URL"].rstrip("/").split("/")[-2])
        r = dict(self.job_result)

        def cb():
            from app import services as svc
            from app.models import Job
            with dbmod.SessionLocal() as s:
                svc.complete_job(s, s.get(Job, job_id), r["status"], r["result"], r["diff"], r["logs"], r["usage"])
        threading.Timer(0.05, cb).start()


@pytest.fixture()
def fake_docker(monkeypatch):
    fd = FakeDocker()
    monkeypatch.setattr(deploy, "run_agent", fd.run_agent)
    monkeypatch.setattr(deploy, "wait_healthy", lambda slug, env, timeout=60: None)
    monkeypatch.setattr(deploy, "stop_agent", fd.stop_agent)
    monkeypatch.setattr(deploy, "states", fd.states)
    monkeypatch.setattr(deploy, "run_job_container", fd.run_job_container)
    monkeypatch.setattr(deploy, "remove_job_container", lambda name: None)
    monkeypatch.setattr(deploy, "job_logs", lambda name, tail=200: "")
    return fd


@pytest.fixture()
def db():
    with dbmod.SessionLocal() as s:
        yield s


@pytest.fixture(scope="session")
def _app_client():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:  # o lifespan (e o session manager do MCP) só pode rodar uma vez
        yield c


@pytest.fixture()
def client(_app_client, fake_docker):
    return _app_client


_n = [0]


@pytest.fixture()
def uniq():
    def make(prefix="agent"):
        _n[0] += 1
        return f"{prefix}-{os.getpid()}-{_n[0]}"
    return make
