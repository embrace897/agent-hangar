"""Os ids das migrações cabem na coluna alembic_version.version_num (VARCHAR(32) no Postgres): o SQLite dos testes não
confere o tamanho, e um id longo derruba a subida da central em produção."""
import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "central" / "migrations" / "versions"


def test_revision_ids_fit_alembic_version_column():
    ids = {}
    for f in VERSIONS.glob("*.py"):
        m = re.search(r'^revision = "([^"]+)"', f.read_text(encoding="utf-8"), re.M)
        assert m, f"{f.name} sem revision"
        ids[f.name] = m.group(1)
    long = {n: i for n, i in ids.items() if len(i) > 32}
    assert not long, f"ids com mais de 32 caracteres: {long}"
    assert len(set(ids.values())) == len(ids), "ids repetidos"
