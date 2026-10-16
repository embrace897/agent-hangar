"""Cadastro da empresa e e-mail corporativo: o admin informa os domínios e pode exigir que todo membro tenha e-mail
deles — em convite e cadastro, primeiro login pelo SSO, SCIM e troca de e-mail. Domínio de e-mail pessoal e de outra
empresa são recusados; o dono não se tranca para fora; quem já era membro fora dos domínios aparece numa lista."""
import pytest

from app import sso
from app.db import SessionLocal
from app.models import Organization

from .conftest import ADMIN
from .test_access import _session
from .test_sso_scim import scim  # noqa: F401


@pytest.fixture()
def company(client, uniq):
    """Domínios únicos para este teste; ao terminar, a empresa principal volta como estava (outros testes a usam)."""
    with SessionLocal() as db:
        o = db.get(Organization, 1)
        before = (o.name, list(o.domains or []), dict(o.access_policy or {}))
    tag = uniq("co").replace("-", "")
    dom = f"{tag}.com.br"
    admin = f"dona@{dom}"
    client.post("/api/users", headers=ADMIN, json={"email": admin, "org_role": "admin"})
    yield {"dom": dom, "tag": tag, "h": _session(admin), "admin": admin}
    with SessionLocal() as db:
        o = db.get(Organization, 1)
        o.name, o.domains, o.access_policy = before
        db.commit()


def _put(client, h, **body):
    return client.put("/api/workspace/company", headers=h, json=body)


def test_first_access_registers_the_company_and_can_skip(client, company):
    me = client.get("/api/me", headers=company["h"]).json()
    assert "setup_done" in me["org"]
    r = _put(client, company["h"], name="ACME Ltda", domains=[f"@{company['dom']}", f"{company['tag']}.com"], enforce=True)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["name"] == "ACME Ltda" and c["domains"] == [company["dom"], f"{company['tag']}.com"] and c["enforce"]
    assert c["setup_done"] and client.get("/api/me", headers=company["h"]).json()["org"]["setup_done"] is True
    # salvar a política de acesso não apaga a regra de e-mail
    client.put("/api/workspace/policy", headers=company["h"], json={"login": {"password": True}})
    assert client.get("/api/workspace/company", headers=company["h"]).json()["enforce"] is True
    # "configurar depois" só marca o primeiro acesso como feito
    assert _put(client, company["h"], skip=True).json()["domains"] == c["domains"]


def test_domains_are_validated(client, company):
    h = company["h"]
    assert "pessoal" in _put(client, h, domains=["@gmail.com"]).json()["detail"]
    assert _put(client, h, domains=["não é domínio"]).status_code == 400
    assert "ao menos um domínio" in _put(client, h, domains=[], enforce=True).json()["detail"]
    # o dono não se tranca para fora: o e-mail dele precisa estar nos domínios para ligar a regra
    r = _put(client, h, domains=[f"outro{company['tag']}.com"], enforce=True)
    assert r.status_code == 400 and "seu e-mail" in r.json()["detail"]
    # só o admin
    member = f"m@{company['dom']}"
    client.post("/api/users", headers=ADMIN, json={"email": member})
    assert _put(client, _session(member), domains=[company["dom"]]).status_code == 403
    # domínio de outra empresa
    other = client.post("/api/orgs", headers=ADMIN, json={"name": f"Outra {company['tag']}", "admin_email": f"a@x{company['tag']}.com",
                                                         "domains": [f"x{company['tag']}.com"]})
    assert other.status_code == 200, other.text
    assert "outra empresa" in _put(client, h, domains=[company["dom"], f"x{company['tag']}.com"]).json()["detail"]


def test_every_way_in_respects_the_rule(client, company, scim):  # noqa: F811
    dom, h, tag = company["dom"], company["h"], company["tag"]
    assert _put(client, h, domains=[dom], enforce=True).status_code == 200
    # cadastro e convite
    r = client.post("/api/users", headers=ADMIN, json={"email": "fulano@gmail.com"})
    assert r.status_code == 400 and f"@{dom}" in r.json()["detail"]
    assert client.post("/api/workspace/invites", headers=h, json={"email": f"x@{tag}.net", "send": False}).status_code == 400
    ok = client.post("/api/workspace/invites", headers=h, json={"email": f"nova@{dom}", "send": False})
    assert ok.status_code == 200, ok.text
    # time: um e-mail novo fora dos domínios não vira pessoa
    team = client.post("/api/teams", headers=ADMIN, json={"name": f"T {tag}"}).json()["slug"]
    assert client.post(f"/api/teams/{team}/members", headers=ADMIN, json={"email": f"y@{tag}.net"}).status_code == 400
    # troca de e-mail
    uid = client.post("/api/users", headers=ADMIN, json={"email": f"troca@{dom}"}).json()["id"]
    assert client.patch(f"/api/users/{uid}", headers=ADMIN, json={"email": f"troca@{tag}.net"}).status_code == 400
    # subdomínios só com a opção
    assert client.post("/api/users", headers=ADMIN, json={"email": f"s@br.{dom}"}).status_code == 400
    _put(client, h, subdomains=True)
    assert client.post("/api/users", headers=ADMIN, json={"email": f"s@br.{dom}"}).status_code == 200
    # SCIM
    r = client.post("/scim/v2/Users", headers=scim, json={"userName": f"z@{tag}.net", "active": True})
    assert r.status_code == 400
    assert client.post("/scim/v2/Users", headers=scim, json={"userName": f"z@{dom}", "active": True}).status_code == 201
    # primeiro login pelo SSO
    with SessionLocal() as db:
        with pytest.raises(sso.AuthError, match="não é um e-mail da empresa"):
            sso.login_identity(db, "google", sso.Identity(subject=f"g-{tag}", email=f"w@{tag}.net", email_verified=True), {})
        u = sso.login_identity(db, "google", sso.Identity(subject=f"g2-{tag}", email=f"w@{dom}", email_verified=True), {})
        assert u.email == f"w@{dom}"


def test_members_outside_the_domains_are_listed_not_removed(client, company):
    old = f"antigo@{company['tag']}.net"
    uid = client.post("/api/users", headers=ADMIN, json={"email": old}).json()["id"]
    c = _put(client, company["h"], domains=[company["dom"]], enforce=True).json()
    out = {u["email"]: u for u in c["outside"]}
    assert old in out and company["admin"] not in out
    assert client.get("/api/users", headers=ADMIN).status_code == 200
    users = {u["email"]: u for u in client.get("/api/users", headers=ADMIN).json()}
    assert users[old]["active"] is True  # nada muda sozinho
    client.patch(f"/api/users/{uid}", headers=ADMIN, json={"active": False})
    assert old not in {u["email"] for u in client.get("/api/workspace/company", headers=company["h"]).json()["outside"]}
