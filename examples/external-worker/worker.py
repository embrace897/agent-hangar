"""Worker de referência para um Digital employee externo (só a biblioteca padrão do Python).

O Agent Hangar continua dono da fila, da alçada, das decisões, do orçamento e do histórico; este processo só faz o
trabalho. Troque `think()` pelo seu agente (LangGraph, CrewAI, um script, outro serviço…).

    HANGAR_URL=http://localhost:8090 HANGAR_WORKER_TOKEN=ewk_... python worker.py

Opcional: OPENAI_BASE_URL, OPENAI_API_KEY e OPENAI_MODEL para responder com um LLM compatível com a API da OpenAI;
sem eles, o worker devolve um resumo do pedido (útil para testar o circuito).

Ações que mudam algo fora passam pela alçada antes de rodar. Neste exemplo, uma linha no pedido como

    ACTION send_email {"to": "ana@cliente.com", "body": "Olá"}

vira um pedido à alçada: `allowed` executa (aqui, só registra), `waiting` larga a tarefa (ela volta pelo heartbeat
quando alguém decidir — e a mesma ação pedida de novo vem liberada), `denied` não executa, `simulated` (modo sombra)
segue como se tivesse feito.
"""
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("HANGAR_URL", "http://localhost:8090").rstrip("/")
TOKEN = os.environ.get("HANGAR_WORKER_TOKEN", "")
ONCE = "--once" in sys.argv  # uma volta só (testes e CI)
ACTION = re.compile(r"^ACTION\s+([\w.:-]+)\s+(\{.*\})\s*$", re.M)
running = True


def call(path: str, body: dict) -> dict:
    req = urllib.request.Request(f"{URL}/hooks/worker{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def llm(messages: list[dict]) -> tuple[str, float, int]:
    base, key = os.environ.get("OPENAI_BASE_URL"), os.environ.get("OPENAI_API_KEY")
    if not (base and key):
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return f"Recebido e processado pelo worker externo: {last[:300]}", 0.0, 0
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", method="POST",
                                 data=json.dumps({"model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                                                  "messages": messages}).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    usage = data.get("usage") or {}
    return data["choices"][0]["message"]["content"], float(usage.get("cost_usd") or 0), int(usage.get("total_tokens") or 0)


def think(task: dict) -> dict:
    """Uma rodada da tarefa. Devolve {status, result|error, cost_usd, tokens}."""
    tid = task["id"]
    for tool, raw in ACTION.findall("\n".join(m["content"] for m in task["messages"] if m["role"] == "user")):
        g = call(f"/tasks/{tid}/gate", {"tool": tool, "args": json.loads(raw), "rationale": "pedido na tarefa"})
        if g["status"] == "waiting":
            print(f"#{tid}: {tool} espera decisão (pedido #{g.get('request_id')}); a tarefa volta pelo heartbeat")
            return {"status": "waiting"}
        if g["status"] == "denied":
            call(f"/tasks/{tid}/progress", {"note": f"{tool} negada pela alçada: {g.get('message', '')[:200]}"})
            continue
        # allowed (ou simulated, no modo sombra): aqui entraria a chamada real ao sistema
        call(f"/tasks/{tid}/progress", {"note": f"{tool} executada ({g['status']})"})
    text, cost, tokens = llm(task["messages"])
    return {"status": "done", "result": text, "cost_usd": cost, "tokens": tokens}


def main():
    if not TOKEN.startswith("ewk_"):
        sys.exit("defina HANGAR_WORKER_TOKEN (o token ewk_… gerado na tela do Digital employee)")
    signal.signal(signal.SIGINT, lambda *_: globals().update(running=False))
    info = {"name": os.environ.get("WORKER_NAME", "worker-exemplo"), "version": "1.0"}
    while running:
        try:
            hb = call("/heartbeat", {"worker": info})
        except urllib.error.HTTPError as e:
            sys.exit(f"heartbeat recusado ({e.code}): {e.read().decode()[:200]}")
        except OSError as e:
            print(f"sem conexão com {URL}: {e}; tentando de novo")
            time.sleep(10)
            continue
        task = hb.get("task")
        if task:
            print(f"#{task['id']} {task['title']} (rodada {task['run']})")
            try:
                out = think(task)
            except Exception as e:  # noqa: BLE001 — a falha do seu agente vira falha da tarefa, com o motivo
                out = {"status": "failed", "error": str(e)[:1000]}
            if out["status"] != "waiting":
                r = call(f"/tasks/{task['id']}/result", out)
                print(f"#{task['id']} → {r['status']}")
            continue  # pode haver mais na fila
        if ONCE:
            break
        time.sleep(hb.get("next_heartbeat_s", 10))


if __name__ == "__main__":
    main()
