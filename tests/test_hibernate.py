"""Hibernação do stage ocioso: só o stage para (o prod nunca), uma queda não vira "hibernado", o uso em outra réplica
segura a hibernação e a próxima chamada acorda o agente."""
import time

import pytest

from app import config, deploy, shared
from app.services import schedules


class Box:
    def __init__(self, name, status="running", code=0):
        self.name, self.status, self.code, self.started, self.stopped = name, status, code, 0, 0

    @property
    def attrs(self):
        return {"Status": f"Exited ({self.code}) 1 minute ago" if self.status == "exited" else "Up 5 minutes"}

    def stop(self, timeout=10):
        self.status, self.code, self.stopped = "exited", 0, self.stopped + 1

    def start(self):
        self.status, self.started = "running", self.started + 1


class FakeDocker:
    def __init__(self, boxes):
        self.boxes = {b.name: b for b in boxes}
        self.containers = self

    def list(self, all=True, filters=None):  # noqa: A002
        return list(self.boxes.values())

    def get(self, name):
        return self.boxes[name]


@pytest.fixture()
def docker(monkeypatch, client):  # client: o app no ar (banco migrado)
    fd = FakeDocker([Box("agent-a-stage"), Box("agent-a-prod"), Box("agent-b-stage", "exited", code=1)])
    monkeypatch.setattr(deploy, "client", lambda: fd)
    # o conftest troca deploy.states por {} (nenhum teste fala com o Docker): aqui, o fake com a mesma regra do real
    monkeypatch.setattr(deploy, "states", lambda max_age=3.0: {c.name: ("hibernated" if deploy._hibernated(c) else c.status)
                                                              for c in fd.list()})
    monkeypatch.setattr(deploy, "wait_healthy", lambda slug, env, timeout=60: None)
    monkeypatch.setattr(config, "STAGE_IDLE_MIN", 30.0)
    deploy._LAST_USE.clear()
    deploy._SHARED_AT.clear()
    for n in ("agent-a-stage", "agent-a-prod"):
        shared.pop(f"stageuse:{n}")
    deploy.invalidate_states()
    yield fd
    deploy.invalidate_states()


def test_idle_stage_hibernates_and_wakes_on_the_next_call(docker):
    assert deploy.hibernate_idle(60) == []  # o primeiro visto conta a partir de agora
    deploy._LAST_USE["agent-a-stage"] = time.monotonic() - 3600
    deploy._LAST_USE["agent-a-prod"] = time.monotonic() - 3600
    assert deploy.hibernate_idle(60) == ["agent-a-stage"]  # o prod nunca hiberna
    st = deploy.states(max_age=0)
    assert st["agent-a-stage"] == "hibernated" and st["agent-a-prod"] == "running"
    assert st["agent-b-stage"] == "exited"  # caiu (código 1): não é hibernação
    assert deploy.ensure_up("a", "stage") is None and docker.boxes["agent-a-stage"].started == 1
    assert deploy.states(max_age=0)["agent-a-stage"] == "running"
    assert deploy.ensure_up("a", "prod") is None and docker.boxes["agent-a-prod"].started == 0  # já no ar: nada a fazer


def test_use_on_another_replica_holds_hibernation(docker):
    deploy._LAST_USE["agent-a-stage"] = time.monotonic() - 3600
    shared.put("stageuse:agent-a-stage", {"at": time.time()}, 600)  # outra réplica usou há pouco
    assert deploy.hibernate_idle(60) == []
    assert docker.boxes["agent-a-stage"].stopped == 0


def test_the_scheduler_sweep_runs_once_per_minute_and_can_be_turned_off(docker, monkeypatch):
    deploy._LAST_USE["agent-a-stage"] = time.monotonic() - 3600
    shared.pop("stage-hibernate")
    assert schedules.hibernate_idle_stages() == ["agent-a-stage"]
    docker.boxes["agent-a-stage"].start()
    deploy._LAST_USE["agent-a-stage"] = time.monotonic() - 3600
    assert schedules.hibernate_idle_stages() == []  # outra volta no mesmo minuto: nada
    monkeypatch.setattr(config, "STAGE_IDLE_MIN", 0.0)
    shared.pop("stage-hibernate")
    assert schedules.hibernate_idle_stages() == []  # desligado


def test_deleting_an_agent_removes_its_data_volumes(client, fake_docker):
    """Excluir o agente tira os dois volumes de dados (stage e prod): antes, cada agente apagado deixava volumes órfãos."""
    from .conftest import ADMIN
    client.post("/api/apply", headers=ADMIN, json={"agents": [{"name": "Vol", "slug": "vol-orfao", "objective": "x",
                                                               "final_output": "x"}]})
    assert client.delete("/api/agents/vol-orfao", headers=ADMIN).status_code == 200
    assert {"agent-vol-orfao-stage", "agent-vol-orfao-prod"} <= set(fake_docker.removed_data)
