# ruff: noqa: F811  (env é fixture importada de test_employees)
"""Avatar do agente: escolhido na criação (pela IA, ou pelo papel quando ela não escolhe) e trocado depois por quem
edita o agente; os agentes antigos (sem avatar gravado) ganham o do papel ao serem mostrados."""
from pathlib import Path

from app.db import SessionLocal
from app.models import Agent
from app.services import avatars

from .conftest import ADMIN
from .test_employees import env  # noqa: F401

STATIC = Path(avatars.__file__).resolve().parent.parent / "static" / "avatars"


def _new(client, h, team, **over):
    body = {"name": "Radar de Notícias de IA", "objective": "busca as notícias de IA do dia e manda um boletim",
            "final_output": "e-mail", "team": team, **over}
    r = client.post("/api/agents", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_catalog_has_an_image_for_every_avatar(client):
    cat = client.get("/api/avatars", headers=ADMIN).json()
    assert len(cat) == len(avatars.IDS) == len({c["id"] for c in cat})
    assert all((STATIC / f"{c['id']}.webp").is_file() and c["url"] == f"/ui/avatars/{c['id']}.webp" for c in cat)
    assert client.get(cat[0]["url"]).status_code == 200  # servido sem login, como o resto da UI


def test_picked_by_role_and_chosen_at_creation(client, env):
    a = _new(client, env["h"]["own"], env["team"])
    assert a["avatar"] == "jornalista" and a["avatar_url"] == "/ui/avatars/jornalista.webp"  # pelo papel
    b = _new(client, env["h"]["own"], env["team"], avatar="pirata")  # a IA escolheu
    assert b["avatar"] == "pirata"
    assert client.post("/api/agents", headers=env["h"]["own"], json={
        "name": "x", "objective": "y", "final_output": "z", "team": env["team"], "avatar": "unicornio"}).status_code == 400
    # sem palavra do papel: um avatar comum (nunca um personagem de fantasia), sempre o mesmo para o mesmo nome
    assert avatars.pick("Agente X", "faz algo") in avatars.GENERIC
    assert avatars.pick("Agente X", "faz algo") == avatars.pick("Agente X", "outra coisa")


def test_owner_swaps_it_and_others_cannot(client, env):
    slug = _new(client, env["h"]["own"], env["team"])["slug"]
    r = client.put(f"/api/agents/{slug}/avatar", headers=env["h"]["own"], json={"avatar": "maestro"})
    assert r.status_code == 200 and r.json()["avatar"] == "maestro"
    assert client.get(f"/api/agents/{slug}", headers=env["h"]["own"]).json()["avatar"] == "maestro"
    assert client.put(f"/api/agents/{slug}/avatar", headers=env["h"]["out"], json={"avatar": "rei"}).status_code == 403
    assert client.put(f"/api/agents/{slug}/avatar", headers=env["h"]["own"], json={"avatar": "nada"}).status_code == 400
    mine = {a["slug"]: a for a in client.get("/api/me/workspace", headers=env["h"]["own"]).json()["built_agents"]}
    assert mine[slug]["avatar_url"] == "/ui/avatars/maestro.webp"  # Meus agentes e o organograma mostram o mesmo


def test_old_agents_get_one_by_role(client, env):
    slug = _new(client, env["h"]["own"], env["team"])["slug"]
    with SessionLocal() as db:  # um agente de antes do avatar existir
        a = db.query(Agent).filter_by(slug=slug).one()
        a.avatar = None
        db.commit()
    assert client.get(f"/api/agents/{slug}", headers=env["h"]["own"]).json()["avatar"] == "jornalista"
