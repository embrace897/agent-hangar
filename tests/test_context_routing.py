"""Contexto longo e modelo por resposta. A central resolve o orçamento de contexto (pela janela do modelo) e o modelo
leve (o mais capaz entre os baratos da mesma conexão); o runtime tira da conversa os resultados grandes de ferramenta,
resume o histórico antigo quando ainda não cabe, manda o pedido simples ao leve e escala para o principal quando o leve
falha. O custo de cada chamada sai pelo preço do modelo que atendeu."""
import asyncio
import json

import httpx
import pytest

from app import services as svc
from app.models import LlmConnection, LlmModel, ModelProfile

from .conftest import ADMIN
from .test_runtime import rt  # noqa: F401  (fixture do runtime carregado pelo caminho)


@pytest.fixture(autouse=True)
def _cleanup(db):
    """As conexões e fichas daqui saem no fim: a nota de custo e as recomendações comparam com TODOS os modelos da
    instalação, e os preços baratos daqui mudariam os outros testes."""
    _MADE.clear()
    yield
    for name in list(_MADE):
        c = db.query(LlmConnection).filter_by(name=name).one_or_none()
        if c is not None:
            keys = [m.model_id for m in db.query(LlmModel).filter_by(connection_id=c.id)]
            db.query(LlmModel).filter_by(connection_id=c.id).delete()
            db.query(ModelProfile).filter(ModelProfile.key.in_(keys)).delete(synchronize_session=False)
            db.delete(c)
    db.commit()


_MADE: list = []


def _conn(db, uniq, models: dict, provider="openrouter"):
    """{id: (caps, preço entrada, preço saída, janela)}; o primeiro é o padrão."""
    name = uniq(provider)
    _MADE.append(name)
    c = svc.upsert_llm_connection(db, name, "https://example.test/v1", next(iter(models)), "k", "", "t", "openai",
                                  provider=provider)
    for mid, (caps, p_in, p_out, window) in models.items():
        db.add(LlmModel(connection_id=c.id, model_id=mid, name=mid, kind="text", kind_source="provider", caps=caps,
                        price_in_per_mtok=p_in, price_out_per_mtok=p_out, context_length=window))
    db.commit()
    return name


def _agent(db, uniq, conn, model, **execution):
    a = svc.create_agent(db, uniq("Atendente"), "responde dúvidas dos clientes", "resposta")
    patch = {"llm": {"connection": conn, "model": model}, "tools": [{"type": "builtin", "name": "calculator"}]}
    if execution:
        patch["execution"] = execution
    svc.design_agent(db, a.slug, patch)
    return a


def _prof(client, slug):
    r = client.get(f"/api/agents/{slug}/execution?env=prod", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


MODELS = {"big": (["tools"], 3.0, 15.0, 200_000), "mid": (["tools"], 0.8, 4.0, 128_000),
          "tiny": (["tools"], 0.1, 0.4, 32_000), "notools": ([], 0.05, 0.1, 32_000), "free": (["tools"], 0.0, 0.0, 32_000)}


def test_context_budget_comes_from_the_model_window(client, db, uniq):
    conn = _conn(db, uniq, MODELS)
    p = _prof(client, _agent(db, uniq, conn, "big").slug)
    assert p["profile"]["compact"] is True and p["profile"]["context_budget"] == 120_000
    assert p["sends"]["compact"] is True and p["sends"]["context_budget"] == 120_000
    p = _prof(client, _agent(db, uniq, conn, "big", compact=False, context_budget=190_000).slug)["profile"]
    assert p["compact"] is False and p["context_budget"] == 190_000 and any("90%" in w for w in p["warnings"])


def test_light_model_auto_picks_the_most_capable_cheap_one_with_tools(client, db, uniq):
    u = uniq("m")  # nomes únicos: as fichas são da instalação inteira
    big, mid, tiny = f"{u}-big", f"{u}-mid", f"{u}-tiny"
    conn = _conn(db, uniq, {big: MODELS["big"], mid: MODELS["mid"], tiny: MODELS["tiny"], f"{u}-nt": MODELS["notools"],
                            f"{u}-free": MODELS["free"]})  # o gratuito nunca entra no automático
    a = _agent(db, uniq, conn, big, light_model="auto")
    r = _prof(client, a.slug)
    # até 40% do preço do big e com ferramentas: mid e tiny; sem fichas, o mais capaz (o mais caro) deles
    assert r["profile"]["light_model"] == mid and "sem fichas" in r["profile"]["light_source"]
    assert [c["model"] for c in r["profile"]["light_candidates"]] == [tiny, mid]
    assert r["sends"]["light"] == {"model": mid, "prices": [0.8, 4.0]}
    # com fichas, a melhor nota de raciocínio entre os baratos
    db.add(ModelProfile(key=tiny, name=tiny, scores={"reasoning": {"value": 80, "source": "admin"}}))
    db.add(ModelProfile(key=mid, name=mid, scores={"reasoning": {"value": 60, "source": "admin"}}))
    db.commit()
    assert _prof(client, a.slug)["profile"]["light_model"] == tiny


def test_light_model_explicit_and_when_it_cannot_be_used(client, db, uniq):
    conn = _conn(db, uniq, MODELS)
    assert _prof(client, _agent(db, uniq, conn, "big", light_model="tiny").slug)["sends"]["light"]["model"] == "tiny"
    p = _prof(client, _agent(db, uniq, conn, "big", light_model="notools").slug)  # o agente usa ferramentas
    assert p["sends"]["light"] is None and any("fora" in p["profile"]["light_source"] or "lista" in w
                                               for w in p["profile"]["warnings"])
    p = _prof(client, _agent(db, uniq, conn, "tiny", light_model="auto").slug)  # nada custa até 40% do tiny
    assert p["sends"]["light"] is None and "nenhum modelo mais barato" in p["profile"]["light_source"]


def test_spec_rejects_a_tiny_context_budget(db, uniq):
    a = svc.create_agent(db, uniq("x"), "y", "z")
    try:
        svc.design_agent(db, a.slug, {"execution": {"context_budget": 100}})
        raise AssertionError("aceitou um orçamento de 100 tokens")
    except svc.PlatformError as e:
        assert "context_budget" in str(e)


# ---------------------------------------------------------------- runtime

def _fake(rt, monkeypatch, handler):  # noqa: F811
    real = httpx.AsyncClient
    monkeypatch.setattr(rt.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(rt, "MODEL", "big")
    monkeypatch.setattr(rt, "PRICES", [3.0, 15.0])
    monkeypatch.setattr(rt, "_OPTIONAL_OFF", False)


def _reply(content=None, calls=None, tokens=(1000, 100)):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return {"choices": [{"message": msg}],
            "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "total_tokens": sum(tokens)}}


def _call(name, args, cid="c1"):
    return [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]


def _ask(rt, messages, tools=None):  # noqa: F811
    async def go():
        rt.MODE.set("full")
        return await rt.chat(messages)
    return asyncio.run(go())


def test_big_tool_results_leave_the_conversation_and_can_be_read_back(rt, monkeypatch):  # noqa: F811
    big = "linha de dados " * 2000  # ~30 mil caracteres
    replies = [_reply(calls=_call("dados__consulta", {"q": "tudo"})), _reply("pronto")]
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=replies[min(len(seen) - 1, len(replies) - 1)])
    _fake(rt, monkeypatch, handler)
    monkeypatch.setattr(rt, "CONTEXT_BUDGET", 3000)

    async def consulta(args):
        return big

    async def tools():
        return [rt.Tool("dados__consulta", "", {"type": "object"}, consulta)]
    monkeypatch.setattr(rt, "get_tools", tools)
    monkeypatch.setattr(rt, "TOOL_OUTPUT_LIMIT", 100_000)
    text, usage, _ = _ask(rt, [{"role": "user", "content": "consulte"}])
    assert text == "pronto" and usage["compactions"] == 1
    tool_msg = next(m for m in seen[1]["messages"] if m["role"] == "tool")
    assert tool_msg["content"].startswith("[resultado guardado fora da conversa") and len(tool_msg["content"]) < 1200
    assert "hangar_read_result" in [t["function"]["name"] for t in seen[1]["tools"]]
    rid = tool_msg["content"].split("id='")[1].split("'")[0]
    first = asyncio.run(rt._read_result({"id": rid}))
    rest = asyncio.run(rt._read_result({"id": rid, "offset": 6000}))
    assert first.startswith(big[:6000]) and "offset=6000" in first and rest.startswith(big[6000:12000])


def test_old_history_becomes_a_summary_in_the_system_prompt(rt, monkeypatch):  # noqa: F811
    history = []
    for i in range(20):
        history += [{"role": "user", "content": f"pergunta {i} " + "x" * 800},
                    {"role": "assistant", "content": f"resposta {i} " + "y" * 800}]
    history.append({"role": "user", "content": "e agora?"})
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "Resuma a conversa" in str(body["messages"][0]["content"]):
            return httpx.Response(200, json=_reply("A pessoa fez 20 perguntas sobre vendas.", tokens=(500, 50)))
        return httpx.Response(200, json=_reply("resposta final"))
    _fake(rt, monkeypatch, handler)
    monkeypatch.setattr(rt, "CONTEXT_BUDGET", 4000)

    async def tools():
        return []
    monkeypatch.setattr(rt, "get_tools", tools)
    text, usage, _ = _ask(rt, history)
    assert text == "resposta final" and len(seen) == 2
    assert "<conversa>" in seen[0]["messages"][1]["content"]  # o resumo recebe a conversa como dado
    final = seen[1]["messages"]
    assert "## Conversa anterior (resumo da plataforma)" in final[0]["content"] and "20 perguntas" in final[0]["content"]
    assert final[-1] == {"role": "user", "content": "e agora?"} and len(final) < len(history)
    assert rt._tokens_of(final) <= 4000
    # o resumo também é cobrado: 1500 + 1100 tokens pelo preço do modelo do agente
    assert usage["total_tokens"] == 550 + 1100 and usage["cost_usd"] == round((1500 * 3 + 150 * 15) / 1e6, 6)


def test_a_short_question_goes_to_the_light_model_and_costs_its_price(rt, monkeypatch):  # noqa: F811
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_reply("Abrimos às 9h."))
    _fake(rt, monkeypatch, handler)
    monkeypatch.setattr(rt, "LIGHT_MODEL", "tiny")
    monkeypatch.setattr(rt, "LIGHT_PRICES", [0.1, 0.4])

    async def tools():
        return []
    monkeypatch.setattr(rt, "get_tools", tools)
    text, usage, _ = _ask(rt, [{"role": "user", "content": "Que horas vocês abrem?"}])
    assert seen[0]["model"] == "tiny" and usage["model_used"] == "tiny" and usage["route"].startswith("leve")
    assert usage["cost_usd"] == round((1000 * 0.1 + 100 * 0.4) / 1e6, 6)
    seen.clear()
    _, usage, _ = _ask(rt, [{"role": "user", "content": "Analise o código abaixo e compare as duas versões"}])
    assert seen[0]["model"] == "big" and usage["model_used"] == "big" and "análise" in usage["route"]


def test_the_main_model_takes_over_when_the_light_one_fails(rt, monkeypatch):  # noqa: F811
    seen, plan = [], []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        step = plan.pop(0)
        return step if isinstance(step, httpx.Response) else httpx.Response(200, json=step)
    _fake(rt, monkeypatch, handler)
    monkeypatch.setattr(rt, "LIGHT_MODEL", "tiny")
    monkeypatch.setattr(rt, "LIGHT_PRICES", [0.1, 0.4])

    async def tools():
        return []
    monkeypatch.setattr(rt, "get_tools", tools)
    # resposta vazia do leve: o principal refaz
    plan[:] = [_reply(""), _reply("feito pelo principal")]
    text, usage, _ = _ask(rt, [{"role": "user", "content": "oi"}])
    assert text == "feito pelo principal" and [b["model"] for b in seen] == ["tiny", "big"]
    assert usage["model_used"] == "big" and usage["route"] == "escalado: resposta vazia"
    # erro do provedor no leve: o principal refaz
    seen.clear()
    plan[:] = [httpx.Response(500, json={"error": "fora do ar"}), _reply("ok")]
    text, usage, _ = _ask(rt, [{"role": "user", "content": "oi"}])
    assert text == "ok" and [b["model"] for b in seen] == ["tiny", "big"] and "falhou" in usage["route"]
    # chamada de ferramenta malformada
    seen.clear()
    bad = [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{nao é json"}}]
    plan[:] = [_reply(calls=bad), _reply("ok")]
    _, usage, _ = _ask(rt, [{"role": "user", "content": "oi"}])
    assert [b["model"] for b in seen] == ["tiny", "big"] and "malformada" in usage["route"]


def test_without_a_light_model_nothing_changes(rt, monkeypatch):  # noqa: F811
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_reply("ok"))
    _fake(rt, monkeypatch, handler)
    monkeypatch.setattr(rt, "LIGHT_MODEL", "")

    async def tools():
        return []
    monkeypatch.setattr(rt, "get_tools", tools)
    _, usage, _ = _ask(rt, [{"role": "user", "content": "oi"}])
    assert seen[0]["model"] == "big" and usage["route"] == "" and "compactions" not in usage


def test_nothing_to_compact_is_not_counted(rt, monkeypatch):  # noqa: F811
    """Uma única mensagem enorme, sem resultados de ferramenta nem histórico: não há o que tirar."""
    def handler(request):
        return httpx.Response(200, json=_reply("ok"))
    _fake(rt, monkeypatch, handler)
    monkeypatch.setattr(rt, "CONTEXT_BUDGET", 2000)

    async def tools():
        return []
    monkeypatch.setattr(rt, "get_tools", tools)
    _, usage, _ = _ask(rt, [{"role": "user", "content": "x " * 20000}])
    assert "compactions" not in usage


def test_usage_event_records_the_model_that_answered(db, uniq):
    from app.models import UsageEvent
    from app.services.usage import record_usage
    a = svc.create_agent(db, uniq("x"), "y", "z")
    record_usage(db, a.id, "stage", "api", "openai", 10, True,
                 {"usage": {"prompt_tokens": 5, "completion_tokens": 1, "model_used": "tiny"}},
                 tags={"model": "big", "version": 1})
    record_usage(db, a.id, "stage", "api", "openai", 10, True, {"usage": {"prompt_tokens": 5}},
                 tags={"model": "big", "version": 1})
    assert [e.model for e in db.query(UsageEvent).filter_by(agent_id=a.id).order_by(UsageEvent.id)] == ["tiny", "big"]


def test_the_summary_keeps_the_start_of_the_conversation(rt, monkeypatch):  # noqa: F811
    """O fato do começo (o código do pedido) chega ao resumo mesmo numa conversa longa."""
    msgs = [{"role": "user", "content": "Meu pedido é ZX-4471."}, {"role": "assistant", "content": "Anotado."}]
    for i in range(40):
        msgs += [{"role": "user", "content": f"pergunta {i} " + "x" * 2000},
                 {"role": "assistant", "content": f"resposta {i} " + "y" * 2000}]
    msgs.append({"role": "user", "content": "qual é o meu pedido?"})
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "Resuma a conversa" in str(body["messages"][0]["content"]):
            return httpx.Response(200, json=_reply("Pedido ZX-4471."))
        return httpx.Response(200, json=_reply("ZX-4471"))
    _fake(rt, monkeypatch, handler)
    monkeypatch.setattr(rt, "CONTEXT_BUDGET", 3000)

    async def tools():
        return []
    monkeypatch.setattr(rt, "get_tools", tools)
    _ask(rt, msgs)
    sent = seen[0]["messages"][1]["content"]
    assert "ZX-4471" in sent and len(sent) <= 3000 * 3 + 100
