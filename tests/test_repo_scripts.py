"""Os scripts que a documentação manda rodar com ./ (scripts/install.sh…) precisam do bit de execução no git: num
clone do zero em Linux/macOS, sem ele, ./scripts/install.sh falha com "Permission denied"."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="sem git ou fora do repositório")
def test_shell_scripts_are_executable_in_git():
    out = subprocess.run(["git", "ls-files", "-s", "--", "*.sh"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    modes = {line.split()[3]: line.split()[0] for line in out.splitlines() if line.strip()}
    assert "scripts/install.sh" in modes
    assert not [f for f, m in modes.items() if m != "100755"], modes
