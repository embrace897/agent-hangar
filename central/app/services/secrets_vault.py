"""Cofre central de segredos.

Um segredo é da empresa (sem time) ou de um time e tem um nome. Onde a plataforma aceita uma credencial (por enquanto,
a credencial e as configurações secretas da instalação de um plugin), dá para pôr `secret://nome` em vez do valor:
- o valor é buscado na hora do uso, primeiro no time da instalação e depois na empresa;
- trocar o valor no cofre vale para todos os lugares que o usam (a versão sobe);
- o valor é criptografado e nunca volta pela API; cada uso fica contado (quantas vezes, a última e por quem).

Quem administra: um admin (empresa e qualquer time) e o mantenedor do time (os segredos do time). Quem não administra
propõe um valor (por exemplo, a chave nova de um sistema) e quem administra aprova sem ver o valor antigo.
"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..db import SessionLocal
from ..models import Secret, SecretProposal, Team, now
from .access import RANK, Access, Forbidden, find_team
from .common import PlatformError, audit, iso

_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,79}$")
PREFIX = "secret://"


def _name(ref_or_name: str) -> str:
    n = (ref_or_name or "").strip()
    n = n[len(PREFIX):] if n.startswith(PREFIX) else n
    if not _NAME.match(n):
        raise PlatformError("nome de segredo: letras minúsculas, números, '_', '.', '-' (2 a 80)")
    return n


def _find(db: Session, name: str, team_id: int | None) -> Secret | None:
    if team_id:
        s = db.scalar(select(Secret).where(Secret.team_id == team_id, Secret.name == name))
        if s is not None:
            return s
    return db.scalar(select(Secret).where(Secret.team_id.is_(None), Secret.name == name))


def can_manage(acc: Access, team_id: int | None) -> bool:
    if acc.p.is_admin:
        return True
    return team_id is not None and RANK.get(acc.teams.get(team_id, ""), 0) >= RANK["maintainer"]


def check_ref(db: Session, ref: str, team_id: int | None):
    """Na instalação: a referência precisa existir para o time (ou na empresa)."""
    if _find(db, _name(ref), team_id) is None:
        raise PlatformError(f"o cofre não tem '{_name(ref)}' para este time nem para a empresa")


def resolve(ref: str, team_id: int | None, used_by: str, db: Session | None = None) -> str:
    """O valor de secret://nome (time primeiro, depois empresa). Registra o uso."""
    own = db is None
    db = db or SessionLocal()
    try:
        s = _find(db, _name(ref), team_id)
        if s is None:
            raise PlatformError(f"segredo '{_name(ref)}' não existe no cofre (para este time ou a empresa)")
        s.uses, s.last_used_at, s.last_used_by = (s.uses or 0) + 1, now(), used_by[:200]
        value = crypto.decrypt(s.value)
        db.commit()
        return value
    finally:
        if own:
            db.close()


def secret_dict(db: Session, s: Secret) -> dict:
    t = db.get(Team, s.team_id) if s.team_id else None
    return {"id": s.id, "name": s.name, "ref": PREFIX + s.name, "team": t.slug if t else None, "description": s.description,
            "version": s.version, "updated_by": s.updated_by, "updated_at": iso(s.updated_at), "uses": s.uses or 0,
            "last_used_at": iso(s.last_used_at) if s.last_used_at else None, "last_used_by": s.last_used_by}


def list_secrets(db: Session, acc: Access) -> dict:
    rows = [s for s in db.scalars(select(Secret).order_by(Secret.team_id.is_not(None), Secret.name))
            if acc.p.is_auditor or s.team_id is None or s.team_id in acc.teams]
    props = [p for p in db.scalars(select(SecretProposal).order_by(SecretProposal.id.desc()).limit(100))
             if can_manage(acc, p.team_id) or p.proposed_by == acc.p.name]
    return {"secrets": [{**secret_dict(db, s), "can_manage": can_manage(acc, s.team_id)} for s in rows],
            "proposals": [{"id": p.id, "name": p.name, "team": (db.get(Team, p.team_id).slug if p.team_id and db.get(Team, p.team_id) else None),
                           "note": p.note, "proposed_by": p.proposed_by, "status": p.status, "decided_by": p.decided_by,
                           "created_at": iso(p.created_at), "can_decide": p.status == "open" and can_manage(acc, p.team_id)
                           and p.proposed_by != acc.p.name} for p in props]}


def put(db: Session, acc: Access, name: str, value: str, team: str | None = None, description: str | None = None) -> dict:
    """Cria ou troca o valor (a versão sobe). Vazio não apaga: use delete."""
    n = _name(name)
    team_id = find_team(db, team).id if team else None
    if not can_manage(acc, team_id):
        raise Forbidden("segredos da empresa: admin; do time: o mantenedor")
    if not value:
        raise PlatformError("informe o valor")
    s = db.scalar(select(Secret).where(Secret.name == n, Secret.team_id.is_(None) if team_id is None else Secret.team_id == team_id))
    if s is None:
        s = Secret(name=n, team_id=team_id, version=0, created_by=acc.p.name)
        db.add(s)
    s.value, s.version, s.updated_by, s.updated_at = crypto.encrypt(value), (s.version or 0) + 1, acc.p.name, now()
    if description is not None:
        s.description = description[:1000]
    db.commit()
    audit(db, acc.p.name, "secret.put", f"{team or 'empresa'}/{n}", f"v{s.version}")
    return secret_dict(db, s)


def delete(db: Session, acc: Access, secret_id: int):
    s = db.get(Secret, secret_id)
    if s is None or not can_manage(acc, s.team_id):
        raise Forbidden("segredo não encontrado ou sem permissão")
    db.delete(s)
    db.commit()
    audit(db, acc.p.name, "secret.delete", s.name)


def propose(db: Session, acc: Access, name: str, value: str, team: str | None = None, note: str = "") -> dict:
    n = _name(name)
    team_id = find_team(db, team).id if team else None
    if team_id is not None and team_id not in acc.teams and not acc.p.is_admin:
        raise Forbidden("proponha segredos para um time do qual você faz parte")
    if not value:
        raise PlatformError("informe o valor")
    p = SecretProposal(team_id=team_id, name=n, value=crypto.encrypt(value), note=(note or "")[:1000],
                       proposed_by=acc.p.name, created_at=now())
    db.add(p)
    db.commit()
    audit(db, acc.p.name, "secret.propose", f"{team or 'empresa'}/{n}")
    return {"id": p.id, "status": p.status}


def decide(db: Session, acc: Access, proposal_id: int, approve: bool) -> dict:
    p = db.get(SecretProposal, proposal_id)
    if p is None or not can_manage(acc, p.team_id):
        raise Forbidden("proposta não encontrada ou sem permissão")
    if p.status != "open":
        raise PlatformError("a proposta já foi decidida")
    if p.proposed_by == acc.p.name:
        raise Forbidden("quatro olhos: quem propôs não aprova")
    p.status, p.decided_by, p.decided_at = ("approved" if approve else "rejected"), acc.p.name, now()
    out = {"status": p.status}
    if approve:
        t = db.get(Team, p.team_id) if p.team_id else None
        out["secret"] = put(db, acc, p.name, crypto.decrypt(p.value), t.slug if t else None)
    p.value = ""  # o valor proposto não fica guardado depois da decisão
    db.commit()
    audit(db, acc.p.name, f"secret.proposal.{p.status}", p.name)
    return out
