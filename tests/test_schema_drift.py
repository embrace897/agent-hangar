"""Os modelos e as migrações descrevem o mesmo banco: o que o Alembic gera a partir dos modelos, comparado com o banco
migrado dos testes, não pode ter diferença (uma coluna, tabela ou unique esquecido numa migração derruba produção)."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.db import engine
from app.models import Base

IGNORE = {"alembic_version"}


def _relevant(diff) -> bool:
    if isinstance(diff, list):
        return any(_relevant(d) for d in diff)
    kind = diff[0]
    if kind in ("modify_type", "modify_nullable", "modify_default", "modify_comment"):
        return False  # tipos e defaults variam entre SQLite e Postgres
    if kind in ("add_index", "remove_index"):
        return False  # índices não entram: os nomes variam por dialeto
    table = getattr(diff[1], "name", None) or getattr(getattr(diff[1], "table", None), "name", None)
    if kind == "add_fk" and table == "skills":
        return False  # skills.team_id nunca teve a FK no banco (desde o baseline); criar agora quebraria times órfãos
    return table not in IGNORE


def test_models_match_migrations():
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        diffs = [d for d in compare_metadata(ctx, Base.metadata) if _relevant(d)]
    assert not diffs, "modelos e migrações divergem:\n" + "\n".join(repr(d)[:300] for d in diffs)
