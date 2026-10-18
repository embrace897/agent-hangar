"""`hangar setup` e `hangar doctor`: respostas, .env, profiles, configs das ferramentas de IA, configuração pela API
(contra o app de verdade) e as verificações do diagnóstico."""
import os
import sys

import httpx
import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cli"))
from hangar_cli import doctor as dr  # noqa: E402
from hangar_cli import setup as st  # noqa: E402

from .conftest import ADMIN  # noqa: E402

BASE = {"llm": {"provider": "openrouter", "model": "openai/gpt-4.1-mini", "api_key": "k"}}


def test_validate_defaults_and_errors():
    a = st.validate(BASE)
    assert a["exposure"] == {"mode": "local"} and a["images"] == "published" and a["code_policy"] == "approved"
    assert a["llm"]["base_url"] == "https://openrouter.ai/api/v1" and a["llm"]["name"] == "openrouter"
    bad = [({"exposure": {"mode": "ngrok"}}, "exposure.domain"),
           ({"llm": {"provider": "openai"}}, "llm.model"),
           ({"llm": {"provider": "azure", "model": "gpt"}, }, "llm.base_url"),
           ({"sso": {"provider": "microsoft", "client_id": "a", "client_secret": "b"}}, "sso.tenant"),
           ({"sso": {"provider": "oauth2", "client_id": "a", "client_secret": "b"}}, "sso.authorize_url"),
           ({"admin": {"username": "admin", "password": "curta"}}, "8+"),
           ({"memory": "redis"}, "memory")]
    for answers, needle in bad:
        with pytest.raises(st.SetupError, match=needle.replace("+", r"\+")):
            st.validate(answers)


def test_public_url_profiles_and_env():
    a = st.validate({**BASE, "memory": "falkordb", "extras": ["data-studio", "bogus"],
                     "exposure": {"mode": "ngrok", "domain": "acme.ngrok-free.app", "authtoken": "t"},
                     "sso": {"provider": "microsoft", "client_id": "cid", "client_secret": "cs", "tenant": "acme.onmicrosoft.com",
                             "allowed_domains": "acme.com"}, "admin": {"username": "admin", "email": "ti@acme.com"}})
    assert st.public_url(a) == "https://acme.ngrok-free.app"
    assert st.compose_profiles(a) == ["memory-falkordb", "data-studio", "tunnel-ngrok"]
    u = st.env_updates(a, {}, "0.13.0")
    assert u["PUBLIC_BASE_URL"] == "https://acme.ngrok-free.app" and u["COMPOSE_PROFILES"] == "memory-falkordb,data-studio,tunnel-ngrok"
    assert u["HANGAR_REGISTRY"] == "ghcr.io/valteresj2" and u["HANGAR_VERSION"] == "0.13.0"
    assert u["MEMORY_BACKEND"] == "falkordb" and u["MEMORY_LLM_CONNECTION"] == "openrouter" and u["MEMORY_URL"] == "http://memory:8000"
    assert u["NGROK_DOMAIN"] == "acme.ngrok-free.app" and u["OAUTH_MICROSOFT_TENANT"] == "acme.onmicrosoft.com"
    assert u["OAUTH_MICROSOFT_ALLOWED_DOMAINS"] == "acme.com" and u["BOOTSTRAP_ADMIN_EMAILS"] == "ti@acme.com"
    local = st.validate({"memory": "none", "images": "build",
                         "exposure": {"mode": "tailscale", "hostname": "hangar", "tailnet": "acme.ts.net", "authkey": "x"}})
    lu = st.env_updates(local, {}, "0.13.0")
    keep = st.env_updates(st.validate({"memory": "neo4j"}), {}, "0.13.0")
    assert "MEMORY_LLM_CONNECTION" not in keep  # sem LLM agora: não apaga a conexão que a memória já usa
    assert lu["MEMORY_URL"] == "" and "HANGAR_REGISTRY" not in lu and lu["PUBLIC_BASE_URL"] == "https://hangar.acme.ts.net"
    assert lu["COMPOSE_PROFILES"] == "tunnel-tailscale"
    o2 = st.env_updates(st.validate({"sso": {"provider": "oauth2", "client_id": "a", "client_secret": "b", "label": "Okta",
                                             "authorize_url": "https://o/a", "token_url": "https://o/t", "userinfo_url": "https://o/u"}}), {}, "x")
    assert o2["OAUTH_OAUTH2_AUTHORIZE_URL"] == "https://o/a" and o2["OAUTH_OAUTH2_LABEL"] == "Okta"


def test_secrets_are_generated_once_and_env_is_preserved(tmp_path):
    gen = st.generated_secrets({"ADMIN_TOKEN": "existente"})
    assert "ADMIN_TOKEN" not in gen and len(gen["INTERNAL_SECRET"]) == 64
    Fernet(gen["HANGAR_SECRET_KEY"].encode())  # chave Fernet válida
    env = tmp_path / ".env"
    env.write_text("# comentário\nADMIN_TOKEN=existente\nDEBUG=1\n", encoding="utf-8")
    st.write_env(env, {"ADMIN_TOKEN": "existente", "PUBLIC_BASE_URL": "http://localhost:8090", "DEBUG": "0"})
    text = env.read_text(encoding="utf-8")
    assert len(list(tmp_path.glob(".env.bak-*"))) == 1  # backup antes de alterar
    assert text.startswith("# comentário\nADMIN_TOKEN=existente\nDEBUG=0\n") and "PUBLIC_BASE_URL=http://localhost:8090" in text
    assert st.read_env(env)["DEBUG"] == "0"


def test_public_answers_drop_secrets():
    a = st.validate({**BASE, "exposure": {"mode": "cloudflare", "hostname": "h.acme.com", "token": "SEGREDO"},
                     "admin": {"username": "admin", "password": "senha-forte-1"},
                     "sso": {"provider": "github", "client_id": "cid", "client_secret": "SEGREDO"}})
    pub = st.public_answers(a)
    assert "SEGREDO" not in str(pub) and "senha-forte-1" not in str(pub) and "k" != pub["llm"]["api_key"]
    assert pub["exposure"]["hostname"] == "h.acme.com" and pub["sso"]["client_id"] == "cid"


def test_client_configs():
    files = st.client_configs("http://localhost:8090", "ah_tok", ["claude-code", "claude-desktop", "codex", "vscode"])
    assert set(files) == {"claude-code.sh", "claude_desktop_config.json", "codex.toml", "README.md", "token.txt"}
    assert "http://localhost:8090/mcp" in files["claude-code.sh"] and "Bearer ah_tok" in files["claude-code.sh"]
    assert "--allow-http" in files["claude_desktop_config.json"]  # mcp-remote exige isso para http
    assert "agent-hangar-vscode.vsix" in files["README.md"] and "ChatGPT" in files["README.md"]
    https = st.client_configs("https://h.acme.com", "ah_tok", ["claude-desktop"])
    assert "--allow-http" not in https["claude_desktop_config.json"]


def test_configure_against_the_real_api(client, uniq, monkeypatch):
    """Cadastra a conexão (e testa), aprova para código, política, cria o admin local e o token pessoal."""
    monkeypatch.setattr(st.httpx, "request", lambda method, url, **kw: client.request(
        method, url.replace("http://testserver", ""), headers=kw.get("headers"), json=kw.get("json")))

    def token(base, username, password):
        client.cookies.clear()
        r = client.post("/api/auth/password", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        k = client.post("/api/keys", json={"name": "ferramentas", "scopes": ["user"]},
                        headers={"X-CSRF-Token": client.cookies.get("hangar_csrf")})
        client.cookies.clear()
        return k.json()["key"]
    monkeypatch.setattr(st, "personal_token", token)
    user = uniq("adm").replace("-", "")
    a = st.validate({"llm": {"provider": "custom", "name": uniq("llm"), "base_url": "http://127.0.0.1:9/v1", "model": "m", "api_key": "k"},
                     "admin": {"username": user, "password": "-".join(["Senha", "Forte", "123"])}, "code_policy": "any"})
    res = st.configure(st.Api("http://testserver", "test-admin-token"), a, "http://testserver")
    assert res["client_token"].startswith("ah_")
    conns = {c["name"]: c for c in client.get("/api/catalog", headers=ADMIN).json()["llm_connections"]}
    assert conns[a["llm"]["name"]]["allow_code"] is True
    assert any(u.get("username") == user and u["org_role"] == "admin" for u in client.get("/api/users", headers=ADMIN).json())
    # idempotente: rodar de novo não recria o admin nem quebra
    assert "admin_password" not in st.configure(st.Api("http://testserver", "test-admin-token"), {**a, "clients": []}, "http://testserver")


def test_llm_connection_test_endpoint(client, uniq):
    from app import services as svc
    from app.db import SessionLocal
    name = uniq("llm")
    with SessionLocal() as db:
        svc.upsert_llm_connection(db, name, "http://127.0.0.1:9/v1", "m", api_key="k")
    r = client.post(f"/api/catalog/llm/{name}/test", headers=ADMIN).json()
    assert r["ok"] is False and "não consegui conectar" in r["detail"]


# ------------------------------------------------------------------ doctor
def _fake(routes: dict):
    def handler(req: httpx.Request) -> httpx.Response:
        for (method, path), resp in routes.items():
            if req.method == method and req.url.path == path:
                return resp(req) if callable(resp) else resp
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_doctor_checks(tmp_path):
    (tmp_path / ".env").write_text("ADMIN_TOKEN=t\nCENTRAL_PORT=8090\nPUBLIC_BASE_URL=http://localhost:8090\nMEMORY_URL=http://memory:8000\n",
                                   encoding="utf-8")
    routes = {
        ("GET", "/api/health"): httpx.Response(200, json={"version": "0.13.0"}),
        ("GET", "/api/me"): httpx.Response(200, json={"is_admin": True}),
        ("GET", "/api/catalog"): httpx.Response(200, json={"llm_connections": [{"name": "boa"}, {"name": "ruim"}]}),
        ("POST", "/api/catalog/llm/boa/test"): httpx.Response(200, json={"ok": True, "detail": "m respondeu em 300 ms"}),
        ("POST", "/api/catalog/llm/ruim/test"): httpx.Response(200, json={"ok": False, "detail": "HTTP 401 (chave inválida)"}),
        ("GET", "/api/memory/status"): httpx.Response(200, json={"ready": True, "backend": "neo4j", "llm_model": "m", "processed": 3}),
        ("GET", "/api/auth/providers"): httpx.Response(200, json={"providers": [{"label": "Google"}], "password_login": True}),
        ("POST", "/mcp"): httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}}),
        ("HEAD", "/downloads/agent-hangar-vscode.vsix"): httpx.Response(200),
        ("GET", "/api/overview"): httpx.Response(200, json={"agents_total": 4, "running_prod": 2, "running_stage": 3}),
    }
    checks = {c.name: c for c in dr.diagnose(tmp_path, http=_fake(routes), docker=False)}
    assert checks["Central"].status == dr.OK and "0.13.0" in checks["Central"].detail
    assert checks["LLM boa"].status == dr.OK and checks["LLM ruim"].status == dr.FAIL and checks["LLM ruim"].hint
    assert checks["Memória"].status == dr.OK and "neo4j" in checks["Memória"].detail
    assert checks["Login"].detail == "Google, usuário e senha"
    assert checks["MCP da plataforma"].status == dr.OK and checks["Extensão VS Code"].status == dr.OK
    assert "2 em produção" in checks["Agentes"].detail and checks["Endereço público"].status == dr.OK


def test_doctor_central_down_and_bad_token(tmp_path):
    (tmp_path / ".env").write_text("ADMIN_TOKEN=t\n", encoding="utf-8")
    down = dr.diagnose(tmp_path, http=_fake({}), docker=False)
    assert down[0].name == "Central" and down[0].status == dr.FAIL
    refused = dr.diagnose(tmp_path, http=_fake({("GET", "/api/health"): httpx.Response(200, json={"version": "x"}),
                                                ("GET", "/api/me"): httpx.Response(401)}), docker=False)
    assert refused[-1].name == "Admin" and refused[-1].status == dr.FAIL


def test_doctor_public_url_unreachable():
    checks = dr.check_public(_fake({}), "https://hangar.acme.com", "0.13.0")
    assert checks[0].status == dr.FAIL and "túnel" in checks[0].hint


def test_compose_version_check():
    assert st.compose_ok("v2.29.1") and st.compose_ok("2.20.0") and st.compose_ok("5.5.1") and st.compose_ok("v3.0.0")
    assert not st.compose_ok("1.29.2") and not st.compose_ok("2.19.9") and not st.compose_ok("")


# ------------------------------------------------------------------ alvo Kubernetes
from hangar_cli import kube  # noqa: E402


def test_k8s_validate_and_values():
    with pytest.raises(st.SetupError, match="kubernetes.host"):
        st.validate({"target": "kubernetes", "kubernetes": {"exposure": "ingress"}})
    with pytest.raises(st.SetupError, match="database_url"):
        st.validate({"target": "kubernetes", "kubernetes": {"database": "external"}})
    a = st.validate({"target": "kubernetes", "memory": "falkordb", "extras": ["data-studio"],
                     "llm": {"provider": "openrouter", "model": "m", "api_key": "k"},
                     "kubernetes": {"provider": "aks", "exposure": "ingress", "host": "hangar.acme.com", "issuer": "letsencrypt",
                                    "database": "external", "database_url": "postgresql+psycopg://u:SENHA@db:5432/h"},
                     "sso": {"provider": "microsoft", "client_id": "cid", "client_secret": "CS", "tenant": "acme.onmicrosoft.com"}})
    assert a["extras"] == []  # extras do Docker não existem no chart
    v, s = kube.build_values(a, "0.13.0")
    assert v["publicUrl"] == "https://hangar.acme.com" and v["image"] == {"registry": "ghcr.io/valteresj2", "tag": "0.13.0"}
    assert v["ingress"] == {"enabled": True, "host": "hangar.acme.com", "annotations": {"cert-manager.io/cluster-issuer": "letsencrypt"}}
    assert v["postgresql"] == {"enabled": False} and s["postgresql"]["external"]["url"].endswith("/h")
    assert v["memory"] == {"enabled": True, "backend": "falkordb", "llmConnection": "openrouter"}
    assert v["sso"]["microsoft"] == {"clientId": "cid", "tenant": "acme.onmicrosoft.com"} and s["sso"]["microsoft"]["clientSecret"] == "CS"
    assert "SENHA" not in str(v) and "CS" not in str(v)  # segredos só no arquivo de segredos
    assert v["central"] == {"replicas": 1}
    with pytest.raises(st.SetupError, match="replicas"):
        st.validate({"target": "kubernetes", "kubernetes": {"replicas": 50}})


def test_k8s_secrets_are_kept_between_runs():
    first = kube.merge_secrets({}, {})
    assert len(first["secrets"]["adminToken"]) == 64 and first["postgresql"]["password"]
    Fernet(first["secrets"]["secretKey"].encode())
    again = kube.merge_secrets({"tunnel": {"cloudflare": {"token": "novo"}}}, first)
    assert again["secrets"] == first["secrets"] and again["postgresql"]["password"] == first["postgresql"]["password"]
    assert again["tunnel"]["cloudflare"]["token"] == "novo"


def test_k8s_helm_command_and_names(tmp_path):
    (tmp_path / "charts" / "agent-hangar").mkdir(parents=True)
    (tmp_path / "charts" / "agent-hangar" / "values-gke.yaml").write_text("x: 1")
    a = st.validate({"target": "kubernetes", "kubernetes": {"provider": "gke", "namespace": "ia", "release": "hangar"}})
    cmd = kube.helm_command(tmp_path, a, tmp_path / "v.yaml", tmp_path / "s.yaml")
    assert cmd[:6] == ["helm", "upgrade", "--install", "hangar", str(tmp_path / "charts" / "agent-hangar"), "-n"]
    assert str(tmp_path / "charts" / "agent-hangar" / "values-gke.yaml") in cmd and cmd[-3:] == ["--wait", "--timeout", "10m"]
    assert kube.fullname("agent-hangar") == "agent-hangar" and kube.fullname("hangar") == "hangar-agent-hangar"
    assert kube.public_url({"exposure": "cloudflare", "hostname": "h.acme.com"}) == "https://h.acme.com"
    assert kube.public_url({"exposure": "portforward"}) == "http://localhost:18090"


def test_k8s_configure_only_skips_helm(tmp_path, monkeypatch):
    """Instalação feita por Terraform: só configura pela API, com o token do Secret do cluster, sem rodar o helm."""
    import contextlib
    monkeypatch.setattr(kube, "preflight", lambda: [])
    monkeypatch.setattr(kube, "current_context", lambda: "gke_x")
    monkeypatch.setattr(kube, "admin_token_from_cluster", lambda ns, rel: "tok-do-cluster" if ns == "ia" else "")
    monkeypatch.setattr(kube, "port_forward", contextlib.contextmanager(lambda ns, rel: (yield "http://localhost:1234")))
    monkeypatch.setattr(st, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não pode rodar helm")))
    seen = {}

    def fake_configure(api, a, local):
        seen.update(token=api.h["Authorization"], local=local)
        return {"client_token": "ah_x"}
    monkeypatch.setattr(st, "configure", fake_configure)
    ans = tmp_path / "a.yaml"
    ans.write_text("kubernetes: {namespace: ia, release: agent-hangar, exposure: ingress, host: h.acme.com}\nclients: [claude-code]\n")
    assert st.setup(tmp_path, str(ans), configure_only=True) == 0
    assert seen == {"token": "Bearer tok-do-cluster", "local": "http://localhost:1234"}
    assert "https://h.acme.com/mcp" in (tmp_path / ".hangar" / "clients" / "claude-code.sh").read_text()
    ans.write_text("kubernetes: {namespace: outro}\n")
    assert st.setup(tmp_path, str(ans), configure_only=True) == 1  # Secret não encontrado


def test_doctor_lists_only_orphan_agent_volumes(monkeypatch):
    """Volumes agent-<slug>-<env>-data sem container e cujo agente foi excluído; os dos agentes que existem ficam."""
    class R:
        returncode, stdout = 0, "agent-vivo-prod-data\nagent-apagado-stage-data\nagent-a-b-c-prod-data\noutro-volume\n"
    monkeypatch.setattr(dr.subprocess, "run", lambda *a, **k: R())

    class Http:
        def get(self, url, headers=None, timeout=None):
            return type("Resp", (), {"status_code": 200, "json": lambda self: [{"slug": "vivo"}]})()
    assert dr.orphan_volumes(Http(), "http://x", {}) == ["agent-a-b-c-prod-data", "agent-apagado-stage-data"]


def test_doctor_ignores_build_only_services(monkeypatch, tmp_path):
    """Serviços que só constroem a imagem (runtime, harnesses, mcp-stdio-runner) saem logo: não são problema."""
    rows = [{"Service": s, "State": st_} for s, st_ in (("central", "running"), ("agent-runtime", "exited"),
                                                        ("harness-base", "exited"), ("mcp-stdio-runner", "exited"))]

    class R:
        def __init__(self, out):
            self.returncode, self.stdout = 0, out
    monkeypatch.setattr(dr.subprocess, "run", lambda args, **k: R("\n".join(__import__("json").dumps(x) for x in rows)
                                                                    if "ps" in args else ""))
    checks = dr.check_docker(tmp_path)
    assert [c.status for c in checks if c.name == "Containers"] == [dr.OK]
    rows.append({"Service": "postgres", "State": "exited"})
    assert "postgres" in next(c.detail for c in dr.check_docker(tmp_path) if c.name == "Containers")


def test_preflight_daemon_down_only_blocks_when_starting(monkeypatch, tmp_path):
    """--no-start só gera o .env: o daemon do Docker parado (runner Windows, Docker Desktop fechado) não impede."""
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")

    def fake_run(cmd, **kw):
        out = "2.29.1" if cmd[:3] == ["docker", "compose", "version"] else ""
        return st.subprocess.CompletedProcess(cmd, 1 if cmd[:2] == ["docker", "info"] else 0, stdout=out, stderr="")
    monkeypatch.setattr(st.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(st.subprocess, "run", fake_run)
    assert st.DAEMON_DOWN in st.preflight(tmp_path, 1)
    assert st.preflight(tmp_path, 1, start=False) == []
