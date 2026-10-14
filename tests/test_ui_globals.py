"""Os scripts do portal e do console rodam no mesmo escopo global: um `const` ou `function` declarado em dois deles
derruba a interface ("Identifier has already been declared"). Este teste pega isso antes do navegador."""
import os
import re
from collections import defaultdict

STATIC = os.path.join(os.path.dirname(__file__), "..", "central", "app", "static")
SCRIPTS = ["icons.js", "org.js", "employees.js", "plugins.js", "home.js", "work.js", "ux.js", "analyst.js", "member.js",
           "issues.js", "llm.js", "connectors.js", "app.js"]  # i18n.js é uma IIFE
DECL = re.compile(r"^(?:const|let|var|class|async function|function)\s+([A-Za-z_$][\w$]*)", re.M)


def test_scripts_do_not_redeclare_globals():
    seen = defaultdict(list)
    for name in SCRIPTS:
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            for ident in DECL.findall(f.read()):
                seen[ident].append(name)
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dups, f"nomes globais repetidos entre scripts: {dups}"
