"""Tipos de pedido de decisão trazidos pelos módulos (revisão de campos extraídos, alteração de documento, regressão…).

Cada módulo registra o seu tratador com `@handler("kind")`; `employee_tasks._decide` o chama depois de conferir quem
pode decidir e se o pedido está aberto. O tratador fecha o pedido, retoma a tarefa (se houver) e devolve o
`request_dict`.
"""
from collections.abc import Callable

from ..models import HumanRequest, now

HANDLERS: dict[str, Callable] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def close(r: HumanRequest, decision: str, reason: str, who: str):
    r.status, r.decision, r.reason, r.decided_by, r.decided_at = "decided", decision, reason, who, now()
