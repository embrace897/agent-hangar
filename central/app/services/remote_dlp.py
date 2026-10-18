"""DLP e medição na ponte com o agente remoto (spec `remote`).

- **DLP:** os dados que a política da empresa mascara (Organização → Governança de dados: CPF, CNPJ, cartão, e-mail,
  telefone, CID) vão ao agente remoto como `[CPF_3f9a1c2b]`; na volta, o app recebe o valor real. A marca sai de um
  HMAC do valor com a chave da instalação: é a mesma em todas as rodadas da conversa (o agente pode citar
  `[CPF_3f9a1c2b]` na resposta de agora sobre a mensagem de antes) e não precisa guardar nada. Com
  `remote.dlp: off` o agente recebe os valores reais (quando as ferramentas dele precisam deles).
- **Medição:** o agente remoto informa o uso num evento `CUSTOM` (`hangar.usage` ou `usage`), um por chamada ao modelo
  ou um por rodada: os valores se somam. Sem `cost_usd` no evento, o custo sai de `remote.prices`. O orçamento passa a
  ver o custo real do agente.
"""
import hashlib
import hmac
import json
import re

from .. import config

DELTA_ENDS = {"TEXT_MESSAGE_CONTENT": "TEXT_MESSAGE_END", "TOOL_CALL_ARGS": "TOOL_CALL_END",
              "REASONING_MESSAGE_CONTENT": "REASONING_MESSAGE_END"}
END_OF = {v: k for k, v in DELTA_ENDS.items()}
KEEP = ("threadId", "runId", "parentRunId", "tools")  # ids e o contrato das ferramentas do app: não são dado de pessoa
TOKEN = re.compile(r"\[[A-Z]+_[0-9a-f]{8}\]")
HOLD = 24  # o maior pedaço que pode ser o começo de uma marca partida entre dois eventos


def kinds_for(db, remote: dict) -> list[str]:
    """O que mascarar para este agente: a política da empresa, a não ser que a spec diga `dlp: off`."""
    if (remote or {}).get("dlp") == "off":
        return []
    from .governance import policy
    return list(policy(db)["dlp"]["mask"])


class Masker:
    def __init__(self, kinds):
        self.kinds = list(kinds or [])
        self.values: dict[str, str] = {}  # marca -> valor real

    def _token(self, kind: str, value: str) -> str:
        key = (config.SECRET_KEY or config.INTERNAL_SECRET or "hangar").encode()
        return f"[{kind.upper()}_{hmac.new(key, f'{kind}:{value}'.encode(), hashlib.sha256).hexdigest()[:8]}]"

    def mask(self, text: str) -> str:
        if not self.kinds or not isinstance(text, str) or not text:
            return text
        from .governance import detect
        for kind, found in detect(text, self.kinds).items():
            for v in sorted(set(found), key=len, reverse=True):
                tok = self._token(kind, v)
                self.values[tok] = v
                text = text.replace(v, tok)
        return text

    def mask_obj(self, obj, skip=()):
        if isinstance(obj, str):
            return self.mask(obj)
        if isinstance(obj, list):
            return [self.mask_obj(x) for x in obj]
        if isinstance(obj, dict):
            return {k: (v if k in skip else self.mask_obj(v)) for k, v in obj.items()}
        return obj

    def run_input(self, body: dict) -> dict:
        return self.mask_obj(body, KEEP) if self.kinds else body

    def unmask(self, text: str) -> str:
        if not self.values or not isinstance(text, str) or "[" not in text:
            return text
        return TOKEN.sub(lambda m: self.values.get(m.group(0), m.group(0)), text)

    def unmask_obj(self, obj):
        if isinstance(obj, str):
            return self.unmask(obj)
        if isinstance(obj, list):
            return [self.unmask_obj(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask_obj(v) for k, v in obj.items()}
        return obj


class Stream:
    """Os eventos que voltam do agente remoto: devolve o valor real no lugar das marcas (inclusive numa marca partida
    entre dois pedaços do texto) e soma o uso informado."""

    def __init__(self, masker: Masker, prices: dict | None = None):
        self.m = masker
        self.prices = prices or {}
        self.held: dict[tuple, str] = {}
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
        self.reported = False
        self.cost_reported = False
        self.failed = False  # RUN_ERROR do agente ou da ponte: a chamada entra na medição como erro

    def _usage(self, value: dict):
        if not isinstance(value, dict):
            return
        pin = int(value.get("prompt_tokens") or value.get("input_tokens") or 0)
        pout = int(value.get("completion_tokens") or value.get("output_tokens") or 0)
        total = int(value.get("total_tokens") or (pin + pout))
        self.usage["prompt_tokens"] += pin
        self.usage["completion_tokens"] += pout
        self.usage["total_tokens"] += total
        if value.get("cost_usd") is not None:
            self.usage["cost_usd"] += float(value["cost_usd"])
            self.cost_reported = True
        else:
            self.usage["cost_usd"] += (pin * float(self.prices.get("input_per_mtok") or 0)
                                       + pout * float(self.prices.get("output_per_mtok") or 0)) / 1e6
        self.reported = True

    def result(self) -> dict | None:
        """O uso da rodada (para a medição), ou None quando o agente não informou nada."""
        if not self.reported:
            return None
        return {**self.usage, "cost_usd": round(self.usage["cost_usd"], 8)}

    def events(self, ev: dict) -> list[dict]:
        """Um evento do agente -> o(s) evento(s) para o app."""
        k = ev.get("type")
        if k == "RUN_ERROR":
            self.failed = True
        if k == "CUSTOM" and ev.get("name") in ("hangar.usage", "usage"):
            self._usage(ev.get("value"))
            return [ev]
        if not self.m.values:
            return [ev]
        if k in DELTA_ENDS:
            key = (k, ev.get("messageId") or ev.get("toolCallId"))
            text = self.held.pop(key, "") + (ev.get("delta") or "")
            cut = text.rfind("[")
            if cut != -1 and "]" not in text[cut:] and len(text) - cut <= HOLD:
                text, self.held[key] = text[:cut], text[cut:]
            return [{**ev, "delta": self.m.unmask(text)}] if text else []
        out = []
        if k in END_OF:
            out += self._flush((END_OF[k], ev.get("messageId") or ev.get("toolCallId")), ev)
        elif k in ("RUN_FINISHED", "RUN_ERROR"):
            for key in list(self.held):
                out += self._flush(key, {})
        return out + [self.m.unmask_obj(ev)]

    def _flush(self, key, ev) -> list[dict]:
        rest = self.held.pop(key, "")
        if not rest:
            return []
        idk = "toolCallId" if key[0] == "TOOL_CALL_ARGS" else "messageId"
        return [{"type": key[0], idk: key[1] or ev.get(idk), "delta": self.m.unmask(rest)}]

    def line(self, raw: str) -> list[str]:
        """Uma linha SSE `data: {...}` -> as linhas para o app."""
        try:
            ev = json.loads(raw[5:].strip())
        except ValueError:
            return [raw]
        if not isinstance(ev, dict):
            return [raw]
        return ["data: " + json.dumps(e, ensure_ascii=False) for e in self.events(ev)]
