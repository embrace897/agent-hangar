import json

import httpx

from app import services as svc
from app.services import testing


def test_meeting_notes_mock_lifecycle(db, fake_docker, monkeypatch):
    """Exercise template apply/test/ship with a deterministic fake runtime, not an LLM quality evaluation."""
    svc.apply_template(db, "meeting-notes")

    def get(url, **kwargs):
        payload = {"status": "ok"} if url.endswith("/health") else {
            "name": "Meeting Notes", "url": "http://mock", "skills": [{"id": "chat"}]}
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    def post(url, **kwargs):
        if url.endswith("/agui"):
            events = [{"type": "RUN_STARTED"}, {"type": "TEXT_MESSAGE_CONTENT", "delta": "mock"},
                      {"type": "RUN_FINISHED"}]
            return httpx.Response(200, text="\n".join("data: " + json.dumps(event) for event in events))
        payload = {"choices": [{"message": {"content": "mock"}}]}
        if url.endswith("/a2a"):
            payload = {"result": {"parts": [{"text": "mock"}]}}
        elif url.endswith("/acp/runs"):
            payload = {"status": "completed", "output": [{"parts": [{"content": "mock"}]}]}
        elif url.endswith("/mcp"):
            payload = {"result": {"tools": [{"name": "chat"}]}}
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(testing.httpx, "get", get)
    monkeypatch.setattr(testing.httpx, "post", post)
    steps = svc.ship(db, "meeting-notes")
    assert steps[-1]["step"] == "prod"
    assert "mock" in steps[0]["summary"]
