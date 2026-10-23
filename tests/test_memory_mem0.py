"""Serviço de memória com MEMORY_BACKEND=mem0 (Mem0 sobre pgvector), sem banco nem LLM: um Memory falso confere que
o grupo imposto pela central vira o filtro do Mem0, que a gravação não manda o timestamp (o Mem0 aberto recusa), que
fato vencido sai como "não atual" e que apagar o grupo apaga só ele."""
import asyncio
import importlib.util
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def srv(monkeypatch):
    monkeypatch.setenv("MEMORY_BACKEND", "mem0")
    monkeypatch.setenv("MEMORY_TOKEN", "t")
    spec = importlib.util.spec_from_file_location("memory_server_mem0",
                                                  os.path.join(ROOT, "mcp-servers", "memory", "app", "server.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["memory_server_mem0"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("memory_server_mem0", None)


class FakeMem0:
    def __init__(self):
        self.calls = []
        future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        self.rows = {"agent-x": [
            {"id": "1", "memory": "A ACME está no plano Enterprise.", "created_at": "2026-09-01T00:00:00Z"},
            {"id": "2", "memory": "Teste grátis até o fim do mês.", "created_at": "2026-10-01T00:00:00Z",
             "expiration_date": future},
            {"id": "3", "memory": "Promoção de agosto.", "created_at": "2026-08-01T00:00:00Z", "expiration_date": past},
            {"id": "1b", "memory": "A ACME está no plano Enterprise.", "created_at": "2026-09-01T00:00:00Z"},
        ], "team-y": [{"id": "9", "memory": "O time usa o CRM da casa.", "created_at": "2026-07-01T00:00:00Z"}]}

    def search(self, query, *, filters, top_k, show_expired):
        self.calls.append(("search", filters, top_k, show_expired))
        rows = self.rows.get(filters["user_id"], [])
        return {"results": rows if show_expired else [r for r in rows if r["id"] != "3"]}

    def add(self, content, **kw):
        self.calls.append(("add", content, kw))

    def get_all(self, *, filters, top_k, show_expired):
        self.calls.append(("get_all", filters, top_k, show_expired))
        return {"results": self.rows.get(filters["user_id"], [])}

    def delete_all(self, user_id):
        self.calls.append(("delete_all", user_id))


def test_recall_filters_by_the_groups_the_central_imposes(srv):
    m = FakeMem0()
    out = asyncio.run(srv._mem0_search(m, "plano", ["agent-x", "team-y"], 5, False))
    assert [c[1] for c in m.calls] == [{"user_id": "agent-x"}, {"user_id": "team-y"}]
    facts = {f["id"]: f for f in out["facts"]}
    assert "1b" not in facts  # o mesmo fato duas vezes aparece uma vez
    assert facts["1"]["current"] and facts["1"]["group"] == "agent-x" and facts["9"]["group"] == "team-y"
    assert facts["2"]["current"] and facts["2"]["invalid_at"]  # vale até uma data futura: ainda vale hoje
    out = asyncio.run(srv._mem0_search(m, "plano", ["agent-x"], 5, True))
    assert {f["id"]: f["current"] for f in out["facts"]}["3"] is False  # vencido: só no histórico


def test_remember_writes_to_the_write_group_without_timestamp(srv):
    m = FakeMem0()
    srv.S.graphiti = m
    at = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    async def run():
        srv._enqueue({"name": "x", "content": "A ACME trocou para o Enterprise.", "source": "agente x", "at": at,
                      "group": "agent-x__stage"})
        await srv.S.queue.join()
        srv.S.worker.cancel()
    asyncio.run(run())
    kind, content, kw = m.calls[0]
    assert kind == "add" and content == "A ACME trocou para o Enterprise." and kw["user_id"] == "agent-x__stage"
    assert "timestamp" not in kw and kw["metadata"] == {"source": "agente x", "at": at.isoformat()}
    assert srv.S.processed == 1 and srv.S.failed == 0


def test_admin_clear_deletes_only_that_group(srv):
    m = FakeMem0()
    srv.S.graphiti = m
    from starlette.testclient import TestClient
    c = TestClient(srv.TokenGuard(srv.mcp.streamable_http_app()))
    assert c.delete("/admin/groups/agent-x").status_code == 401  # sem o MEMORY_TOKEN
    r = c.delete("/admin/groups/agent-x", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200 and m.calls == [("delete_all", "agent-x")]
    r = c.get("/admin/facts?group=agent-x", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200 and m.calls[-1] == ("get_all", {"user_id": "agent-x"}, 50, True)
    assert [f["id"] for f in r.json()["facts"]] == ["2", "1", "3"]  # mais recentes primeiro, sem o repetido
