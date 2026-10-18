"""CORS por agente: os sites listados em spec.cors.origins chamam o agente pelo gateway direto do navegador. Vale a
versão que roda em cada ambiente; sem curinga e sem cookies."""
import httpx
import pytest

from app import config
from app.services import remote_agui
from app.spec import SpecError, validate

from .conftest import ADMIN
from .test_agui_remote import URL, FakeRemote

APP = "https://app.acme.com"
RUN = {"threadId": "t", "runId": "r", "messages": [{"id": "1", "role": "user", "content": "oi"}], "tools": [],
       "context": [], "state": {}, "forwardedProps": {}}


@pytest.fixture()
def remote_agent(monkeypatch):
    fake = FakeRemote()
    transport = httpx.MockTransport(fake.handler)
    monkeypatch.setattr(remote_agui, "HTTP", lambda timeout: httpx.Client(transport=transport))
    monkeypatch.setattr(remote_agui, "AHTTP", lambda timeout: httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(config, "PLUGIN_ALLOW_PRIVATE", True)
    return fake


def _staged(client, uniq, origins) -> str:
    """Um agente (remoto, sem container) com essas origens, testado e no ar em stage."""
    slug = client.post("/api/agents", headers=ADMIN, json={"name": uniq("Cors"), "objective": "x", "final_output": "y"}).json()["slug"]
    r = client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"remote": {"url": URL}, "cors": {"origins": origins}})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/agents/{slug}/test", headers=ADMIN).json()["status"] == "passed"
    assert client.post(f"/api/agents/{slug}/deploy", headers=ADMIN, json={"env": "stage"}).status_code == 200
    return slug


def _preflight(client, path, origin):
    return client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                         "Access-Control-Request-Headers": "authorization, content-type"})


def test_spec_accepts_exact_origins_only():
    assert validate({"cors": {"origins": ["https://App.Acme.com/", "http://localhost:3000", "https://app.acme.com"]}})[
        "cors"]["origins"] == ["https://app.acme.com", "http://localhost:3000"]
    for bad, msg in [("*", "curinga"), ("https://*.acme.com", "curinga"), ("http://acme.com", "localhost"),
                     ("https://acme.com/app", "sem caminho"), ("acme.com", "esquema")]:
        with pytest.raises(SpecError, match=msg):
            validate({"cors": {"origins": [bad]}})


def test_listed_origin_calls_the_agent_from_the_browser(client, uniq, remote_agent):
    slug = _staged(client, uniq, [APP])
    gw = f"/gw-stage/{slug}/agui"
    p = _preflight(client, gw, APP)
    assert p.status_code == 204, p.text
    assert p.headers["access-control-allow-origin"] == APP and "authorization" in p.headers["access-control-allow-headers"]
    assert "access-control-allow-credentials" not in p.headers and "Origin" in p.headers["vary"]
    r = client.post(gw, headers={**ADMIN, "Origin": APP}, json=RUN)
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == APP and "RUN_FINISHED" in r.text
    # os erros também chegam legíveis para a página (sem chave: 401)
    r = client.post(gw, headers={"Origin": APP}, json=RUN)
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == APP
    # qualquer protocolo do agente
    assert _preflight(client, f"/gw-stage/{slug}/v1/chat/completions", APP).status_code == 204


def test_other_origins_and_paths_get_nothing(client, uniq, remote_agent):
    slug = _staged(client, uniq, [APP])
    p = _preflight(client, f"/gw-stage/{slug}/agui", "https://evil.example.com")
    assert p.status_code == 403 and "access-control-allow-origin" not in p.headers and "spec.cors.origins" in p.text
    r = client.post(f"/gw-stage/{slug}/agui", headers={**ADMIN, "Origin": "https://evil.example.com"}, json=RUN)
    assert "access-control-allow-origin" not in r.headers
    # produção não tem versão rodando: nada liberado lá
    assert _preflight(client, f"/gw/{slug}/agui", APP).status_code == 403
    # a API da plataforma não ganha CORS por causa do agente
    assert "access-control-allow-origin" not in client.get("/api/agents", headers={**ADMIN, "Origin": APP}).headers


def test_origins_follow_the_version_running_in_each_env(client, uniq, remote_agent):
    slug = _staged(client, uniq, [APP])
    new = "http://localhost:3000"
    assert client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"cors": {"origins": [new]}}).status_code == 200
    c = client.get(f"/api/agents/{slug}/cors", headers=ADMIN).json()
    assert c["draft"] == [new] and c["stage"]["origins"] == [APP] and c["prod"] == {"version": None, "origins": []}
    assert _preflight(client, f"/gw-stage/{slug}/agui", new).status_code == 403  # versão nova ainda não está em stage
    assert client.post(f"/api/agents/{slug}/test", headers=ADMIN).json()["status"] == "passed"
    assert client.post(f"/api/agents/{slug}/deploy", headers=ADMIN, json={"env": "stage"}).status_code == 200
    assert _preflight(client, f"/gw-stage/{slug}/agui", new).status_code == 204  # vale já, sem esperar o cache
    assert _preflight(client, f"/gw-stage/{slug}/agui", APP).status_code == 403
    assert client.patch(f"/api/agents/{slug}", headers=ADMIN, json={"cors": None}).status_code == 200
    assert client.get(f"/api/agents/{slug}/cors", headers=ADMIN).json()["draft"] == []
