"""DOCX sem dependências: ler os parágrafos e gerar uma versão com controle de alterações (redline).

A versão gerada traz os parágrafos do documento; os que mudaram aparecem como exclusão (w:del) seguida de inclusão
(w:ins) assinadas pelo revisor — quem abre no Word vê as marcas de revisão e pode aceitar ou recusar cada uma. A
formatação original (estilos, tabelas, imagens) não é preservada nesta versão.
"""
import io
import re
import zipfile
from datetime import UTC, datetime
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def paragraphs(data: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("não é um DOCX válido") from None
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        out.append(_unescape("".join(re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", p, re.S))))
    return out


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _run(text: str, deleted: bool = False) -> str:
    tag = "w:delText" if deleted else "w:t"
    return f'<w:r><{tag} xml:space="preserve">{escape(text)}</{tag}></w:r>'


def build(rows: list[tuple[str, str | None]], author: str) -> bytes:
    """rows: (antes, depois). depois=None: parágrafo igual; antes="" e depois: inclusão; antes e depois="": exclusão."""
    when = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    a = escape(author)[:120]
    body, rid = [], 1
    for before, after in rows:
        if after is None:
            body.append(f"<w:p>{_run(before)}</w:p>")
            continue
        parts = []
        if before:
            parts.append(f'<w:del w:id="{rid}" w:author="{a}" w:date="{when}">{_run(before, True)}</w:del>')
            rid += 1
        if after:
            parts.append(f'<w:ins w:id="{rid}" w:author="{a}" w:date="{when}">{_run(after)}</w:ins>')
            rid += 1
        body.append(f"<w:p>{''.join(parts)}</w:p>")
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>'
           + "".join(body) + "<w:sectPr/></w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   "</Relationships>")
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def plain(paras: list[str]) -> bytes:
    return build([(p, None) for p in paras], "Agent Hangar")
