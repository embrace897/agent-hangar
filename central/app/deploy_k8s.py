"""Engine de deploy no Kubernetes (RUNTIME_BACKEND=kubernetes): o mesmo contrato do deploy.py (Docker), com recursos
nativos do cluster no lugar de containers:

- agente (chat): Deployment + Service `agent-<slug>-<env>` (porta 8000) + Secret com os tokens e a chave do LLM;
- job de harness e avaliação de código: Job efêmero (backoffLimit 0, TTL) + Secret do ambiente, apagados no fim.

A central usa a própria ServiceAccount, com uma Role mínima no namespace (deployments, services, secrets, jobs, pods e
logs) — sem acesso ao nó, ao runtime de containers nem a outros namespaces. As NetworkPolicies do chart fazem o papel
das redes Docker: agentes e jobs só falam com a central, o DNS e a internet. Todos os pods rodam sem root, com o
sistema de arquivos só de leitura, sem capabilities e sem o token da API do Kubernetes.
"""
import hashlib
import threading
import time

import httpx

from . import auth, config

LBL = "hangar.dev"
_lock = threading.Lock()
_apis = None
_states_cache: tuple[float, dict] = (0.0, {})
BAD_WAITING = {"ImagePullBackOff", "ErrImagePull", "InvalidImageName", "CrashLoopBackOff", "CreateContainerConfigError"}



def namespace() -> str:
    if config.K8S_NAMESPACE:
        return config.K8S_NAMESPACE
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "default"


def apis():
    """(CoreV1, AppsV1, BatchV1) — config de dentro do cluster (ServiceAccount) ou do kubeconfig local."""
    global _apis
    with _lock:
        if _apis is None:
            from kubernetes import client
            from kubernetes import config as kconf
            try:
                kconf.load_incluster_config()
            except kconf.ConfigException:
                kconf.load_kube_config()
            _apis = (client.CoreV1Api(), client.AppsV1Api(), client.BatchV1Api())
        return _apis


def _gone(e) -> bool:
    return getattr(e, "status", None) == 404


def dns_name(base: str) -> str:
    """Nomes do Kubernetes: até 63 caracteres, minúsculas, números e hífen."""
    base = "".join(c if c.isalnum() or c == "-" else "-" for c in base.lower()).strip("-")
    if len(base) <= 63:
        return base
    return f"{base[:52].rstrip('-')}-{hashlib.sha256(base.encode()).hexdigest()[:10]}"


def container_name(slug: str, env: str) -> str:
    return dns_name(f"agent-{slug}-{env}")


def internal_url(slug: str, env: str) -> str:
    return f"http://{container_name(slug, env)}:8000"


def _quantity_mem(v: str) -> str:
    v = (v or "512m").strip()
    return v[:-1] + "Mi" if v.lower().endswith("m") and not v.endswith("Mi") else (v[:-1] + "Gi" if v.lower().endswith("g") else v)


def _resources(mem: str, cpus: float) -> dict:
    return {"limits": {"memory": _quantity_mem(mem), "cpu": f"{int(cpus * 1000)}m"},
            "requests": {"memory": "128Mi", "cpu": f"{max(50, int(cpus * 250))}m"}}


SECURITY = {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"}}


def _pull_secrets() -> list:
    return [{"name": s} for s in (config.K8S_IMAGE_PULL_SECRET or "").split(",") if s.strip()]


def _upsert_secret(name: str, data: dict, labels: dict, owner: dict | None = None):
    core, _, _ = apis()
    body = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "labels": labels,
            **({"ownerReferences": [owner]} if owner else {})}, "type": "Opaque",
            "stringData": {k: str(v) for k, v in data.items()}}
    try:
        core.create_namespaced_secret(namespace(), body)
    except Exception as e:
        if getattr(e, "status", None) != 409:
            raise
        core.replace_namespaced_secret(name, namespace(), body)


# ---------------------------------------------------------------- agentes
def run_agent(slug: str, env: str, resolved_spec: dict, llm_conf: dict):
    import json

    core, apps, _ = apis()
    ns, name = namespace(), container_name(slug, env)
    labels = {f"{LBL}/role": "agent", f"{LBL}/name": name, f"{LBL}/env": env, f"{LBL}/agent": dns_name(slug)}
    _upsert_secret(name, {"INTERNAL_TOKEN": auth.agent_token(slug), "INTERNAL_ENV_TOKEN": auth.agent_env_token(slug, env),
                          "LLM_API_KEY": llm_conf.get("api_key", "")}, labels)
    plain = {"AGENT_SPEC": json.dumps(resolved_spec), "AGENT_SLUG": slug, "AGENT_ENV": env,
             "PUBLIC_URL": f"{config.PUBLIC_BASE_URL}/{'gw' if env == 'prod' else 'gw-stage'}/{slug}",
             "INTERNAL_BASE_URL": config.INTERNAL_BASE_URL, "LLM_BASE_URL": llm_conf.get("base_url", "")}
    pod = {"metadata": {"labels": {**labels, f"{LBL}/version": str(resolved_spec.get("version", ""))}},
           "spec": {"automountServiceAccountToken": False, "enableServiceLinks": False,
                    "imagePullSecrets": _pull_secrets(),
                    "containers": [{"name": "agent", "image": config.RUNTIME_IMAGE, "imagePullPolicy": config.K8S_PULL_POLICY,
                                    "ports": [{"containerPort": 8000, "name": "http"}],
                                    "env": [{"name": k, "value": v} for k, v in plain.items()],
                                    "envFrom": [{"secretRef": {"name": name}}],
                                    "resources": _resources(config.AGENT_MEM_LIMIT, config.AGENT_CPUS),
                                    "securityContext": SECURITY,
                                    "readinessProbe": {"httpGet": {"path": "/health", "port": 8000}, "periodSeconds": 5},
                                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}, {"name": "data", "mountPath": "/data"}]}],
                    "volumes": [{"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}},
                                {"name": "data", "emptyDir": {"sizeLimit": "1Gi"}}]}}
    dep = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "labels": labels},
           "spec": {"replicas": 1, "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": {f"{LBL}/name": name}}, "template": pod}}
    try:
        res = apps.create_namespaced_deployment(ns, dep)
    except Exception as e:
        if getattr(e, "status", None) != 409:
            raise
        res = apps.replace_namespaced_deployment(name, ns, dep)  # nova versão: rollout do pod
    svc = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "labels": labels},
           "spec": {"selector": {f"{LBL}/name": name}, "ports": [{"port": 8000, "targetPort": 8000, "name": "http"}]}}
    try:
        core.create_namespaced_service(ns, svc)
    except Exception as e:
        if getattr(e, "status", None) != 409:
            raise
    invalidate_states()

    class Ref:
        id = (res.metadata.uid or name).replace("-", "")
    return Ref()


def _pods(selector: str) -> list:
    core, _, _ = apis()
    return core.list_namespaced_pod(namespace(), label_selector=selector).items


def _waiting_reason(pods) -> str:
    for p in pods:
        for cs in (p.status.container_statuses or []):
            w = cs.state.waiting if cs.state else None
            if w and w.reason in BAD_WAITING:
                return f"{w.reason}: {w.message or ''}".strip()
    return ""


def wait_healthy(slug: str, env: str, timeout=120):
    """None se saudável; senão a mensagem de erro (com o motivo do pod e os logs)."""
    deadline = time.time() + timeout
    url = internal_url(slug, env) + "/health"
    sel = f"{LBL}/name={container_name(slug, env)}"
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return None
        except Exception:
            pass
        reason = _waiting_reason(_pods(sel))
        if reason.startswith(("ImagePullBackOff", "ErrImagePull", "InvalidImageName")):
            return (f"imagem '{config.RUNTIME_IMAGE}' não pôde ser baixada ({reason}). Confira HANGAR_REGISTRY/"
                    "HANGAR_VERSION e o imagePullSecret (K8S_IMAGE_PULL_SECRET).")
        time.sleep(2)
    return "Agente não ficou saudável.\n" + logs(slug, env, 30)


def logs(slug: str, env: str, tail=200) -> str:
    return _logs(f"{LBL}/name={container_name(slug, env)}", tail)


def _logs(selector: str, tail: int) -> str:
    core, _, _ = apis()
    try:
        pods = _pods(selector)
        if not pods:
            return "(sem pods)"
        reason = _waiting_reason(pods)
        pod = sorted(pods, key=lambda p: p.metadata.creation_timestamp)[-1]
        try:
            text = core.read_namespaced_pod_log(pod.metadata.name, namespace(), tail_lines=tail)
        except Exception:
            text = ""
        return (f"[{reason}]\n" if reason else "") + (text or "")
    except Exception as e:
        return f"(sem logs: {e})"


def states(max_age=3.0) -> dict | None:
    """{nome: running|exited} de todos os agentes; None = API do Kubernetes indisponível agora."""
    global _states_cache
    ts, data = _states_cache
    if time.monotonic() - ts < max_age:
        return data
    try:
        _, apps, _ = apis()
        deps = apps.list_namespaced_deployment(namespace(), label_selector=f"{LBL}/role=agent").items
        pods = _pods(f"{LBL}/role=agent")
    except Exception:
        return None
    by_name: dict[str, list] = {}
    for p in pods:
        by_name.setdefault(p.metadata.labels.get(f"{LBL}/name", ""), []).append(p)
    data = {}
    for d in deps:
        if _replicas(d) == 0:  # hibernado (stage ocioso): acorda na próxima chamada
            data[d.metadata.name] = "hibernated"
            continue
        # o Deployment existe: está "no ar" a menos que o pod esteja em falha (subindo ainda conta como no ar)
        data[d.metadata.name] = "exited" if _waiting_reason(by_name.get(d.metadata.name, [])) else "running"
    _states_cache = (time.monotonic(), data)
    return data


def invalidate_states():
    global _states_cache
    _states_cache = (0.0, {})


def state(slug: str, env: str) -> str:
    s = states()
    if s is None:
        return "unknown"
    return s.get(container_name(slug, env), "missing")


def _replicas(d) -> int:
    r = getattr(getattr(d, "spec", None), "replicas", None)
    return 1 if r is None else int(r)


def _scale(name: str, replicas: int):
    _, apps, _ = apis()
    apps.patch_namespaced_deployment_scale(name, namespace(), {"spec": {"replicas": replicas}})
    invalidate_states()


def ensure_up(slug: str, env: str, timeout=120) -> str | None:
    from .deploy import touch
    name = container_name(slug, env)
    touch(name)
    if (states() or {}).get(name) != "hibernated":
        return None
    try:
        _scale(name, 1)
    except Exception as e:  # noqa: BLE001
        return f"não consegui acordar {name}: {e}"
    return wait_healthy(slug, env, timeout)


def hibernate_idle(idle_s: float) -> list[str]:
    try:
        _, apps, _ = apis()
        deps = apps.list_namespaced_deployment(namespace(), label_selector=f"{LBL}/role=agent,{LBL}/env=stage").items
    except Exception:  # noqa: BLE001
        return []
    from .deploy import idle
    out = []
    for d in deps:
        name = d.metadata.name
        if _replicas(d) == 0 or not idle(name, idle_s):
            continue
        try:
            _scale(name, 0)
            out.append(name)
        except Exception:  # noqa: BLE001
            continue
    return out


def stop_agent(slug: str, env: str, remove_data: bool = False):  # os dados do pod são emptyDir: saem com ele
    core, apps, _ = apis()
    ns, name = namespace(), container_name(slug, env)
    for fn in (lambda: apps.delete_namespaced_deployment(name, ns), lambda: core.delete_namespaced_service(name, ns),
               lambda: core.delete_namespaced_secret(name, ns)):
        try:
            fn()
        except Exception as e:
            if not _gone(e):
                raise
    invalidate_states()


# ---------------------------------------------------------------- jobs (harness e avaliações de código)
def run_job_container(name: str, image: str, environment: dict, mem_limit: str, cpus: float,
                      command: list[str] | None = None, labels: dict | None = None):
    _, _, batch = apis()
    ns, name = namespace(), dns_name(name)
    role = "eval" if (labels or {}).get("central.eval") else "job"
    lbls = {f"{LBL}/role": role, f"{LBL}/name": name}
    _upsert_secret(name, environment, lbls)  # o ambiente tem o token do job: nada em texto no spec do Job
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "labels": lbls},
           "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 900,
                    "activeDeadlineSeconds": config.JOB_MAX_TIMEOUT_S + 120,
                    "template": {"metadata": {"labels": lbls}, "spec": {
                        "restartPolicy": "Never", "automountServiceAccountToken": False, "enableServiceLinks": False,
                        "imagePullSecrets": _pull_secrets(),
                        "containers": [{"name": "job", "image": image, "imagePullPolicy": config.K8S_PULL_POLICY,
                                        **({"command": command} if command else {}),
                                        "envFrom": [{"secretRef": {"name": name}}],
                                        "resources": _resources(mem_limit, cpus),
                                        "securityContext": {**SECURITY, "readOnlyRootFilesystem": False},
                                        "volumeMounts": [{"name": "work", "mountPath": "/workspace"}]}],
                        "volumes": [{"name": "work", "emptyDir": {"sizeLimit": "2Gi"}}]}}}}
    res = batch.create_namespaced_job(ns, job)
    # o Secret some junto com o Job (GC do Kubernetes), mesmo se a central cair no meio
    owner = {"apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": res.metadata.uid, "blockOwnerDeletion": False}
    _upsert_secret(name, environment, lbls, owner)
    return res


def job_logs(name: str, tail=200) -> str:
    return _logs(f"job-name={dns_name(name)}", tail)


def remove_job_container(name: str):
    core, _, batch = apis()
    ns, name = namespace(), dns_name(name)
    for fn in (lambda: batch.delete_namespaced_job(name, ns, propagation_policy="Background"),
               lambda: core.delete_namespaced_secret(name, ns)):
        try:
            fn()
        except Exception:
            pass


def cleanup_job_containers(keep: set[str] | frozenset = frozenset(), min_age_s: float = 0):
    """Jobs e avaliações que sobraram (a central reiniciou no meio). Preserva `keep` (jobs ativos de outras réplicas)
    e, com `min_age_s`, as avaliações recentes."""
    from datetime import UTC, datetime
    keep_dns = {dns_name(k) for k in keep}
    try:
        _, _, batch = apis()
        for role in ("job", "eval"):
            for j in batch.list_namespaced_job(namespace(), label_selector=f"{LBL}/role={role}").items:
                name = j.metadata.name
                if name in keep_dns:
                    continue
                created = j.metadata.creation_timestamp
                if role == "eval" and min_age_s and created and (datetime.now(UTC) - created).total_seconds() < min_age_s:
                    continue
                remove_job_container(name)
    except Exception:
        pass


# ---------------------------------------------------------------- plugins com código (Deployment + Service + Secret)
def plugin_name(plugin: str, install_id: int) -> str:
    return dns_name(f"plugin-{plugin}-{install_id}")


def plugin_url(name: str, port: int, path: str) -> str:
    return f"http://{dns_name(name)}:{port}{path}"


def run_plugin(name: str, image: str, environment: dict, mem_limit: str, cpus: float, command: list[str] | None = None,
               labels: dict | None = None, port: int = 8000):
    """Rótulo hangar.dev/role=plugin: a NetworkPolicy do chart só deixa a central chegar nele e corta a saída para as
    redes privadas (banco, agentes, memória)."""
    core, apps, _ = apis()
    ns, name = namespace(), dns_name(name)
    lbls = {f"{LBL}/role": "plugin", f"{LBL}/name": name}
    _upsert_secret(name, environment, lbls)
    pod = {"metadata": {"labels": lbls},
           "spec": {"automountServiceAccountToken": False, "enableServiceLinks": False, "imagePullSecrets": _pull_secrets(),
                    "containers": [{"name": "plugin", "image": image, "imagePullPolicy": config.K8S_PULL_POLICY,
                                    **({"command": command} if command else {}),
                                    "ports": [{"containerPort": port, "name": "mcp"}],
                                    "envFrom": [{"secretRef": {"name": name}}],
                                    "resources": _resources(mem_limit, cpus), "securityContext": SECURITY,
                                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}]}],
                    "volumes": [{"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}]}}
    dep = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "labels": lbls},
           "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {f"{LBL}/name": name}},
                    "template": pod}}
    try:
        apps.create_namespaced_deployment(ns, dep)
    except Exception as e:
        if getattr(e, "status", None) != 409:
            raise
        apps.replace_namespaced_deployment(name, ns, dep)
    svc = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "labels": lbls},
           "spec": {"selector": {f"{LBL}/name": name}, "ports": [{"port": port, "targetPort": port, "name": "mcp"}]}}
    try:
        core.create_namespaced_service(ns, svc)
    except Exception as e:
        if getattr(e, "status", None) != 409:
            raise


def plugin_state(name: str) -> str:
    try:
        _, apps, _ = apis()
        apps.read_namespaced_deployment(dns_name(name), namespace())
    except Exception as e:
        return "missing" if _gone(e) else "unknown"
    return "exited" if _waiting_reason(_pods(f"{LBL}/name={dns_name(name)}")) else "running"


def plugin_logs(name: str, tail=100) -> str:
    return _logs(f"{LBL}/name={dns_name(name)}", tail)


def stop_plugin(name: str):
    core, apps, _ = apis()
    ns, name = namespace(), dns_name(name)
    for fn in (lambda: apps.delete_namespaced_deployment(name, ns), lambda: core.delete_namespaced_service(name, ns),
               lambda: core.delete_namespaced_secret(name, ns)):
        try:
            fn()
        except Exception as e:
            if not _gone(e):
                raise


def client():  # o driver Docker expõe o cliente; aqui não existe
    raise RuntimeError("RUNTIME_BACKEND=kubernetes: não há cliente Docker")
