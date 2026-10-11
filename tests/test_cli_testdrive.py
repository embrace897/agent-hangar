# ruff: noqa: F811  (env e rt são fixtures importadas de test_employees)
"""hangar test-drive: time, Digital employee (mock), experiência, admissão e uma tarefa — e apaga tudo no fim."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli"))
from hangar_cli.testdrive import Drive  # noqa: E402

from .conftest import ADMIN  # noqa: E402
from .test_employees import env, rt, work  # noqa: E402, F401


class ClientApi:
    """O Api da CLI, sobre o TestClient (mesmos caminhos /api/…)."""

    def __init__(self, client):
        self.c, self.url = client, "http://testserver"

    def _ok(self, r):
        assert r.status_code < 400, r.text
        return r.json() if r.content else None

    def get(self, path):
        return self._ok(self.c.get("/api" + path, headers=ADMIN))

    def post(self, path, body=None):
        return self._ok(self.c.post("/api" + path, headers=ADMIN, json=body or {}))

    def patch(self, path, body):
        return self._ok(self.c.patch("/api" + path, headers=ADMIN, json=body))

    def delete(self, path):
        return self._ok(self.c.delete("/api" + path, headers=ADMIN))


def test_test_drive_runs_the_whole_path_and_cleans_up(client, env, rt, monkeypatch):
    said = []
    d = Drive(ClientApi(client), say=said.append)

    def wait(fn, secs, what):  # sem o laço em segundo plano: uma passada do executor a cada consulta
        for _ in range(20):
            work()
            got = fn()
            if got:
                return got
        raise TimeoutError(what)
    monkeypatch.setattr(d, "wait", wait)
    res = d.run()
    assert res["result"] and res["task"]
    assert any("admitido" in s for s in said) and any(f"/app/#/tasks/{res['task']}" in s for s in said)
    made = dict(d.made)
    assert set(made) == {"user", "team", "agent"}
    d.cleanup()
    assert client.get(f"/api/employees/{res['employee']}", headers=ADMIN).status_code in (400, 404)
    assert all(t["slug"] != res["team"] for t in client.get("/api/teams", headers=ADMIN).json())
