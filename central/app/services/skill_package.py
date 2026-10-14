"""Skills como pacote (o formato das Agent Skills): um SKILL.md com nome e descrição no cabeçalho YAML e, opcionalmente,
outros arquivos (references/, scripts/, assets/…).

- Upload: um .md (com o cabeçalho) ou um .zip/.skill que tenha um SKILL.md; vários de uma vez.
- Verificação de segurança antes de salvar:
  - bloqueia: caminhos fora do pacote, arquivos binários ou grandes demais, segredos no texto (chaves de API, chaves
    privadas, senhas);
  - avisa: instruções que tentam passar por cima das regras ("ignore as instruções anteriores"), envio de dados para
    endereços externos, scripts (os agentes de chat não executam scripts da skill).
- Download: o mesmo formato (pasta <nome>/SKILL.md + arquivos), num .zip.

No agente, o SKILL.md vai inteiro para as instruções; os arquivos de texto de references/ vão junto até um limite, e os
outros são listados (os agentes de chat não executam scripts).
"""
import base64
import io
import json
import posixpath
import re
import zipfile

import yaml

from .common import PlatformError, slugify

MAX_FILE = 256 * 1024
MAX_TOTAL = 2 * 1024 * 1024
MAX_FILES = 100
TEXT_EXT = {".md", ".txt", ".py", ".js", ".ts", ".mjs", ".json", ".yaml", ".yml", ".csv", ".tsv", ".html", ".htm", ".css",
            ".sh", ".sql", ".xml", ".toml", ".ini", ".cfg", ".rst", ".jinja", ".j2", ".tmpl", ".svg", ".r", ".rb", ".go",
            ".java", ".kt", ".cs", ".php", ".ps1", ".bat", ".env.example", ""}
SCRIPT_EXT = {".py", ".js", ".ts", ".mjs", ".sh", ".ps1", ".bat", ".rb", ".go", ".php"}
SECRET = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "chave privada"),
    (re.compile(r"\b(sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,})"), "chave de API (sk-…)"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "chave de acesso da AWS"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "token do GitHub"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "token do Slack"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "chave do Google"),
    (re.compile(r"(?i)\b(password|senha|passwd|secret|api[_-]?key)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"), "senha ou chave escrita no texto"),
]
INJECTION = [
    (re.compile(r"(?i)ignore\s+(all\s+)?(the\s+)?(previous|prior|above)\s+(instructions|rules)|ignore\s+as\s+instru[çc][õo]es\s+anteriores"),
     "pede para ignorar as instruções anteriores"),
    (re.compile(r"(?i)(do not|don't|não)\s+(tell|inform|avise|conte)\s+(the\s+)?(user|usuário)"), "pede para esconder algo do usuário"),
    (re.compile(r"(?i)(send|post|upload|envie|mande)\b[^\n]{0,60}\b(https?://|webhook)"), "manda dados para um endereço externo"),
    (re.compile(r"(?i)system\s+prompt|<\s*/?\s*system\s*>"), "fala do prompt de sistema"),
]


# ------------------------------------------------------------------ ler o pacote
def parse_skill_md(text: str) -> tuple[dict, str]:
    """(cabeçalho YAML, corpo) de um SKILL.md."""
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text or "", re.S)
    if not m:
        raise PlatformError("o SKILL.md precisa começar com o cabeçalho YAML (--- name: … description: … ---)")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise PlatformError(f"cabeçalho YAML inválido: {e}") from None
    if not isinstance(meta, dict) or not meta.get("name") or not meta.get("description"):
        raise PlatformError("o cabeçalho do SKILL.md precisa de name e description")
    return meta, m.group(2).strip()


def _safe_path(p: str) -> str:
    p = p.replace("\\", "/")
    norm = posixpath.normpath(p)
    if p.startswith("/") or norm.startswith("..") or "/../" in f"/{norm}/" or re.match(r"^[A-Za-z]:", p):
        raise PlatformError(f"caminho fora do pacote: {p}")
    return norm


def _ext(path: str) -> str:
    base = posixpath.basename(path).lower()
    return ".env.example" if base.endswith(".env.example") else posixpath.splitext(base)[1]


def from_upload(filename: str, data: bytes) -> dict:
    """Um arquivo enviado -> {name, description, content, files:[{path, content}], skipped:[...]}."""
    low = (filename or "").lower()
    if low.endswith(".md"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise PlatformError(f"{filename}: o arquivo .md precisa ser texto UTF-8") from None
        meta, body = parse_skill_md(text)
        return {"meta": meta, "content": body, "files": [], "skipped": []}
    if not (low.endswith(".zip") or low.endswith(".skill")):
        raise PlatformError(f"{filename}: envie um .md, .zip ou .skill")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PlatformError(f"{filename}: não é um zip válido") from None
    names = [n for n in zf.namelist() if not n.endswith("/") and "__MACOSX" not in n and not posixpath.basename(n).startswith(".")]
    if len(names) > MAX_FILES + 1:
        raise PlatformError(f"{filename}: mais de {MAX_FILES} arquivos")
    total = sum(zf.getinfo(n).file_size for n in names)
    if total > MAX_TOTAL:
        raise PlatformError(f"{filename}: o pacote passa de {MAX_TOTAL // 1024 // 1024} MB descompactado")
    skill_md = sorted((n for n in names if posixpath.basename(n) == "SKILL.md"), key=lambda n: n.count("/"))
    if not skill_md:
        raise PlatformError(f"{filename}: o pacote precisa de um SKILL.md")
    root = posixpath.dirname(skill_md[0])
    meta, body = parse_skill_md(zf.read(skill_md[0]).decode("utf-8", errors="replace"))
    files, skipped = [], []
    for n in names:
        if n == skill_md[0]:
            continue
        rel = n[len(root) + 1:] if root and n.startswith(root + "/") else (n if not root else None)
        if rel is None:
            skipped.append({"path": n, "why": "fora da pasta da skill"})
            continue
        rel = _safe_path(rel)
        info = zf.getinfo(n)
        if _ext(rel) not in TEXT_EXT:
            skipped.append({"path": rel, "why": "arquivo binário (só texto entra na skill)"})
            continue
        if info.file_size > MAX_FILE:
            skipped.append({"path": rel, "why": f"maior que {MAX_FILE // 1024} KB"})
            continue
        try:
            files.append({"path": rel, "content": zf.read(n).decode("utf-8")})
        except UnicodeDecodeError:
            skipped.append({"path": rel, "why": "não é texto UTF-8"})
    return {"meta": meta, "content": body, "files": files, "skipped": skipped}


def clean_files(files: list) -> list[dict]:
    """Arquivos vindos da tela (criar/atualizar): caminhos seguros, só texto, sem o próprio SKILL.md."""
    out, seen, total = [], set(), 0
    for f in files or []:
        if not isinstance(f, dict) or not (f.get("path") or "").strip():
            continue
        p = _safe_path(f["path"].strip())
        if p == "SKILL.md" or p in seen:
            continue
        c = f.get("content") or ""
        if len(c.encode()) > MAX_FILE:
            raise PlatformError(f"{p}: maior que {MAX_FILE // 1024} KB")
        if _ext(p) not in TEXT_EXT:
            raise PlatformError(f"{p}: só arquivos de texto entram na skill")
        total += len(c.encode())
        seen.add(p)
        out.append({"path": p, "content": c})
    if len(out) > MAX_FILES or total > MAX_TOTAL:
        raise PlatformError("arquivos demais ou grandes demais para uma skill")
    return sorted(out, key=lambda f: f["path"])


# ------------------------------------------------------------------ verificação de segurança
def scan(content: str, files: list[dict]) -> dict:
    """{ok, blocked: [...], warnings: [...]}: segredo bloqueia; instrução suspeita e script avisam."""
    blocked, warnings = [], []
    docs = [("SKILL.md", content)] + [(f["path"], f["content"]) for f in files]
    for path, text in docs:
        for rx, what in SECRET:
            if rx.search(text or ""):
                blocked.append(f"{path}: {what} — tire o segredo do arquivo (use o cofre: secret://nome)")
        if _ext(path) in (".md", ".txt", "") or path == "SKILL.md":
            for rx, what in INJECTION:
                if rx.search(text or ""):
                    warnings.append(f"{path}: {what}")
    scripts = [f["path"] for f in files if _ext(f["path"]) in SCRIPT_EXT]
    if scripts:
        warnings.append(f"{len(scripts)} script(s) ({', '.join(scripts[:5])}): ficam no pacote, mas os agentes de chat não os executam")
    return {"ok": not blocked, "blocked": blocked, "warnings": warnings}


# ------------------------------------------------------------------ upload e download
def upload(db, acc, items: list[dict], check_only: bool = False) -> list[dict]:
    """items: [{name, data (base64)}]. Cada arquivo vira uma skill (nova ou versão nova da existente)."""
    from .skill_studio import save
    out = []
    for it in items[:20]:
        fname = it.get("name") or "skill"
        try:
            raw = base64.b64decode(it.get("data") or "", validate=True)
        except ValueError:
            out.append({"file": fname, "ok": False, "error": "arquivo ilegível"})
            continue
        try:
            pkg = from_upload(fname, raw)
            name = slugify(str(pkg["meta"]["name"]))[:100]
            report = scan(pkg["content"], pkg["files"])
            res = {"file": fname, "name": name, "description": str(pkg["meta"]["description"])[:1000], "scan": report,
                   "files": [f["path"] for f in pkg["files"]], "skipped": pkg["skipped"]}
            if not report["ok"]:
                out.append({**res, "ok": False, "error": "a verificação de segurança bloqueou o pacote"})
                continue
            if check_only:
                out.append({**res, "ok": True})
                continue
            s = save(db, acc, name, res["description"], pkg["content"], note=f"upload: {fname}", files=pkg["files"])
            out.append({**res, "ok": True, "version": s["version"]})
        except PlatformError as e:
            out.append({"file": fname, "ok": False, "error": str(e)})
    return out


def skill_md(name: str, description: str, content: str) -> str:
    head = yaml.safe_dump({"name": name, "description": description}, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{head}\n---\n\n{content.strip()}\n"


def to_zip(s, files: list | None = None, version: int | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{s.name}/SKILL.md", skill_md(s.name, s.description or "", s.content or ""))
        for f in (s.files if files is None else files) or []:
            zf.writestr(f"{s.name}/{f['path']}", f["content"])
    return buf.getvalue()


def for_agent(skill_row) -> str:
    """O texto que o agente recebe: o SKILL.md e, até um limite, as referências de texto; o resto só listado."""
    content = skill_row.content or ""
    files = skill_row.files or []
    if not files:
        return content
    budget, parts, listed = 30_000, [content], []
    for f in files:
        if f["path"].startswith("references/") and _ext(f["path"]) in (".md", ".txt", ".csv", ".json", ".yaml", ".yml") \
                and len(f["content"]) <= budget:
            parts.append(f"\n\n### {f['path']}\n{f['content']}")
            budget -= len(f["content"])
        else:
            listed.append(f["path"])
    if listed:
        parts.append("\n\nOutros arquivos do pacote (não incluídos aqui): " + ", ".join(listed[:50]))
    return "".join(parts)


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
