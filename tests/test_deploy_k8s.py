"""Driver Kubernetes (RUNTIME_BACKEND=kubernetes) contra uma API do Kubernetes simulada: nomes DNS, Deployment +
Service + Secret do agente com o endurecimento de segurança, atualização (409 -> replace), estados, parada, Jobs de
harness/avaliação com o Secret amarrado ao Job, logs e limpeza."""
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import pytest

from app import config
from app import deploy_k8s as k8s


class ApiError(Exception):
    def __init__(self, status):
        self.status = status


class FakeK8s:
    def __init__(self):
        self.objs: dict[tuple[str, str], dict] = {}
        self.calls: list[tuple] = []
        self.pods: list = []

    # ---- helpers
    def _create(self, kind, body):
        key = (kind, body["metadata"]["name"])
        if key in self.objs:
            raise ApiError(409)
        self.objs[key] = body
        self.calls.append(("create", kind, key[1]))
        return NS(metadata=NS(uid="uid-" + key[1], name=key[1]))

    def _replace(self, kind, name, body):
        self.objs[(kind, name)] = body
        self.calls.append(("replace", kind, name))
        return NS(metadata=NS(uid="uid-" + name, name=name))

    def _delete(self, kind, name):
        if (kind, name) not in self.objs:
            raise ApiError(404)
        del self.objs[(kind, name)]
        self.calls.append(("delete", kind, name))

    # ---- CoreV1
    def create_namespaced_secret(self, ns, body): return self._create("Secret", body)
    def replace_namespaced_secret(self, name, ns, body): return self._replace("Secret", name, body)
    def delete_namespaced_secret(self, name, ns): return self._delete("Secret", name)
    def create_namespaced_service(self, ns, body): return self._create("Service", body)
    def delete_namespaced_service(self, name, ns): return self._delete("Service", name)
    def list_namespaced_pod(self, ns, label_selector=""):
        k, v = label_selector.split("=")
        return NS(items=[p for p in self.pods if p.metadata.labels.get(k) == v])
    def read_namespaced_pod_log(self, name, ns, tail_lines=0): return f"log de {name}"

    # ---- AppsV1
    def create_namespaced_deployment(self, ns, body): return self._create("Deployment", body)
    def replace_namespaced_deployment(self, name, ns, body): return self._replace("Deployment", name, body)
    def delete_namespaced_deployment(self, name, ns): return self._delete("Deployment", name)
    def list_namespaced_deployment(self, ns, label_selector=""):
        return NS(items=[NS(metadata=NS(name=n)) for (kind, n) in self.objs if kind == "Deployment"])

    # ---- BatchV1
    def create_namespaced_job(self, ns, body): return self._create("Job", body)
    def delete_namespaced_job(self, name, ns, propagation_policy=""): return self._delete("Job", name)
    def list_namespaced_job(self, ns, label_selector=""):
        k, v = label_selector.split("=")
        old = datetime(2020, 1, 1, tzinfo=UTC)
        return NS(items=[NS(metadata=NS(name=n, creation_timestamp=old)) for (kind, n), body in self.objs.items()
                         if kind == "Job" and body["spec"]["template"]["metadata"]["labels"].get(k) == v])


def pod(name, labels, waiting=None):
    cs = [NS(state=NS(waiting=NS(reason=waiting, message="x") if waiting else None))]
    return NS(metadata=NS(name=name, labels=labels, creation_timestamp=1), status=NS(container_statuses=cs))


@pytest.fixture()
def fk(monkeypatch):
    f = FakeK8s()
    monkeypatch.setattr(k8s, "_apis", (f, f, f))
    monkeypatch.setattr(config, "K8S_NAMESPACE", "hangar")
    k8s.invalidate_states()
    return f


def test_dns_names():
    assert k8s.container_name("assistente-comercial", "stage") == "agent-assistente-comercial-stage"
    long = k8s.container_name("a" * 80, "stage")
    assert len(long) <= 63 and long.startswith("agent-aaaa") and long == k8s.container_name("a" * 80, "stage")
    assert k8s.dns_name("Job_7 X") == "job-7-x"
    assert k8s.internal_url("bot", "prod") == "http://agent-bot-prod:8000"


def test_run_agent_creates_hardened_deployment(fk, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_IMAGE", "ghcr.io/acme/agent-runtime:0.10.0")
    monkeypatch.setattr(config, "K8S_IMAGE_PULL_SECRET", "regcred")
    k8s.run_agent("bot", "stage", {"version": 3, "llm": {}}, {"base_url": "https://llm/v1", "api_key": "sk-x"})
    dep = fk.objs[("Deployment", "agent-bot-stage")]
    pod_spec = dep["spec"]["template"]["spec"]
    c = pod_spec["containers"][0]
    assert c["image"] == "ghcr.io/acme/agent-runtime:0.10.0" and pod_spec["imagePullSecrets"] == [{"name": "regcred"}]
    sc = c["securityContext"]
    assert sc["runAsNonRoot"] and sc["runAsUser"] == 10001 and sc["readOnlyRootFilesystem"] and sc["capabilities"] == {"drop": ["ALL"]}
    assert pod_spec["automountServiceAccountToken"] is False  # o agente não fala com a API do Kubernetes
    env = {e["name"]: e["value"] for e in c["env"]}
    assert "LLM_API_KEY" not in env and "INTERNAL_TOKEN" not in env  # segredos só no Secret
    secret = fk.objs[("Secret", "agent-bot-stage")]["stringData"]
    assert secret["LLM_API_KEY"] == "sk-x" and secret["INTERNAL_TOKEN"] and secret["INTERNAL_ENV_TOKEN"]
    assert dep["spec"]["template"]["metadata"]["labels"]["hangar.dev/role"] == "agent"
    assert fk.objs[("Service", "agent-bot-stage")]["spec"]["ports"][0]["port"] == 8000
    # nova versão: o Deployment é substituído (rollout), o Service fica
    k8s.run_agent("bot", "stage", {"version": 4}, {})
    assert ("replace", "Deployment", "agent-bot-stage") in fk.calls


def test_states_and_stop(fk):
    k8s.run_agent("ok", "prod", {"version": 1}, {})
    k8s.run_agent("ruim", "prod", {"version": 1}, {})
    fk.pods = [pod("p1", {"hangar.dev/role": "agent", "hangar.dev/name": "agent-ok-prod"}),
               pod("p2", {"hangar.dev/role": "agent", "hangar.dev/name": "agent-ruim-prod"}, waiting="ImagePullBackOff")]
    k8s.invalidate_states()
    assert k8s.states() == {"agent-ok-prod": "running", "agent-ruim-prod": "exited"}
    assert k8s.state("ok", "prod") == "running" and k8s.state("nada", "prod") == "missing"
    k8s.stop_agent("ok", "prod")
    assert ("Deployment", "agent-ok-prod") not in fk.objs and ("Secret", "agent-ok-prod") not in fk.objs
    k8s.stop_agent("ok", "prod")  # idempotente (404 ignorado)


def test_jobs_secret_owned_by_job(fk):
    k8s.run_job_container("eval-bot-abc123", "img/harness-base:1", {"EVAL_URL": "http://c/internal/eval/tok"}, "1g", 1.0,
                          command=["node", "/srv/eval_runner.js"], labels={"central.eval": "bot"})
    job = fk.objs[("Job", "eval-bot-abc123")]
    spec = job["spec"]
    assert spec["backoffLimit"] == 0 and spec["ttlSecondsAfterFinished"] == 900
    c = spec["template"]["spec"]["containers"][0]
    assert c["command"] == ["node", "/srv/eval_runner.js"] and c["envFrom"] == [{"secretRef": {"name": "eval-bot-abc123"}}]
    assert spec["template"]["metadata"]["labels"]["hangar.dev/role"] == "eval"
    owner = fk.objs[("Secret", "eval-bot-abc123")]["metadata"]["ownerReferences"][0]
    assert owner["kind"] == "Job" and owner["uid"] == "uid-eval-bot-abc123"  # o Secret some junto com o Job
    fk.pods = [pod("j1", {"job-name": "eval-bot-abc123"})]
    assert k8s.job_logs("eval-bot-abc123") == "log de j1"
    k8s.remove_job_container("eval-bot-abc123")
    assert ("Job", "eval-bot-abc123") not in fk.objs
    # limpeza no startup: remove o que sobrou, mas preserva jobs ativos de outras réplicas
    for n in ("job-7-aaaaaa", "job-8-bbbbbb"):
        k8s.run_job_container(n, "img/harness-claude-code:1", {"TASK": "x"}, "1g", 1.0)
    k8s.cleanup_job_containers(keep={"job-8-bbbbbb"}, min_age_s=60)
    assert ("Job", "job-7-aaaaaa") not in fk.objs and ("Job", "job-8-bbbbbb") in fk.objs


def test_wait_healthy_reports_image_errors(fk, monkeypatch):
    fk.pods = [pod("p", {"hangar.dev/name": "agent-x-prod"}, waiting="ErrImagePull")]
    monkeypatch.setattr(k8s.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    msg = k8s.wait_healthy("x", "prod", timeout=5)
    assert "não pôde ser baixada" in msg and "K8S_IMAGE_PULL_SECRET" in msg


def test_facade_switches_backend(monkeypatch):
    """Carrega uma CÓPIA do deploy.py com RUNTIME_BACKEND=kubernetes (recarregar o módulo real desfaria as
    proteções do conftest que impedem os testes de tocar no Docker de verdade)."""
    import importlib.util

    from app import deploy
    monkeypatch.setattr(config, "RUNTIME_BACKEND", "kubernetes")
    spec = importlib.util.spec_from_file_location("app._deploy_facade_copy", deploy.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.run_agent is k8s.run_agent and mod.container_name is k8s.container_name
    assert mod.states is k8s.states and mod.run_job_container is k8s.run_job_container


def test_runtime_class_sandboxes_agents_jobs_and_plugins(fk, monkeypatch):
    """K8S_RUNTIME_CLASS (gVisor, Kata…): os pods que rodam código ganham kernel próprio; vazio, nada muda."""
    k8s.run_agent("sem", "prod", {"version": 1}, {})
    assert "runtimeClassName" not in fk.objs[("Deployment", "agent-sem-prod")]["spec"]["template"]["spec"]
    monkeypatch.setattr(config, "K8S_RUNTIME_CLASS", "gvisor")
    k8s.run_agent("com", "prod", {"version": 1}, {})
    k8s.run_job_container("job-x", "img/harness-base:1", {}, "1g", 1.0)
    k8s.run_plugin("plugin-x", "img/plugin:1", {}, "256m", 0.5)
    assert fk.objs[("Deployment", "agent-com-prod")]["spec"]["template"]["spec"]["runtimeClassName"] == "gvisor"
    assert fk.objs[("Job", "job-x")]["spec"]["template"]["spec"]["runtimeClassName"] == "gvisor"
    assert fk.objs[("Deployment", "plugin-x")]["spec"]["template"]["spec"]["runtimeClassName"] == "gvisor"
