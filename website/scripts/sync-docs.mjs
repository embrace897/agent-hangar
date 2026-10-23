// Gera as páginas do site a partir de ../docs (a fonte continua sendo docs/: nada é editado aqui).
// - o primeiro "# Título" vira o title do frontmatter, e o primeiro parágrafo a description;
// - links entre docs (concepts.md#x) viram rotas do site (/agent-hangar/docs/concepts/#x): a raiz é a página de entrada;
// - links para o resto do repositório (../README.md, ../examples/…) apontam para o GitHub;
// - docs/assets e docs/demo (a demo interativa) vão para public/, no mesmo endereço de antes;
// - os avatares dos agentes (central/app/static/avatars) vão para public/avatars: a cena da página de entrada usa.
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SITE = join(HERE, '..');
const DOCS = join(SITE, '..', 'docs');
const OUT = join(SITE, 'src', 'content', 'docs', 'docs');
const BASE = (process.env.DOCS_BASE ?? '/agent-hangar').replace(/\/$/, '');
const REPO = 'https://github.com/valteresj2/agent-hangar/blob/main';
// fora do site: o plano comercial e o documento do cliente (fora do git), e as traduções (o site é em inglês)
const SKIP = new Set(['MELHORIAS.md']);

const slugOf = (file) => file.replace(/\.md$/, '').toLowerCase();

function plain(md) {
  return md.replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_`>#]/g, '').replace(/\s+/g, ' ').trim();
}

function rewrite(md) {
  const link = (target) => {
    if (/^(https?:|mailto:|#)/.test(target)) return target;
    const [path, hash] = target.split('#');
    const anchor = hash ? `#${hash}` : '';
    if (/^[\w.-]+\.md$/.test(path) && existsSync(join(DOCS, path)) && !SKIP.has(path) && !path.includes('.pt-BR.')) {
      return `${BASE}/docs/${slugOf(path)}/${anchor}`;
    }
    if (path.startsWith('assets/') || path.startsWith('demo/')) return `${BASE}/${path}${anchor}`;
    if (path.startsWith('../')) return `${REPO}/${path.slice(3)}${anchor}`;
    return `${REPO}/docs/${path}${anchor}`;  // outro arquivo de docs/ (uma tradução, um script): no GitHub
  };
  return md
    .replace(/(\]\()([^)\s]+)(\))/g, (_, a, t, b) => a + link(t) + b)
    .replace(/(src|href)="((?:assets|demo)\/[^"]+)"/g, (_, attr, p) => `${attr}="${BASE}/${p}"`);
}

mkdirSync(OUT, { recursive: true });
for (const f of readdirSync(OUT)) if (f.endsWith('.md')) rmSync(join(OUT, f));
let n = 0;
for (const file of readdirSync(DOCS).sort()) {
  if (!file.endsWith('.md') || SKIP.has(file) || file.includes('.pt-BR.')) continue;
  const src = readFileSync(join(DOCS, file), 'utf8').replace(/\r\n/g, '\n');
  const lines = src.split('\n');
  const h1 = lines.findIndex((l) => l.startsWith('# '));
  const title = h1 >= 0 ? lines[h1].slice(2).trim() : slugOf(file);
  const rest = h1 >= 0 ? lines.slice(h1 + 1) : lines;
  const para = [];
  for (const l of rest) {
    if (!l.trim()) { if (para.length) break; continue; }
    if (/^(#|\||```|<|- |\* |\d+\. )/.test(l.trim())) { if (para.length) break; continue; }
    para.push(l.trim());
  }
  let description = plain(para.join(' '));
  if (description.length > 200) description = description.slice(0, 197).replace(/\s+\S*$/, '') + '…';
  const front = ['---', `title: ${JSON.stringify(plain(title))}`,
    ...(description ? [`description: ${JSON.stringify(description)}`] : []),
    `editUrl: ${JSON.stringify(`https://github.com/valteresj2/agent-hangar/edit/main/docs/${file}`)}`, '---', ''];
  writeFileSync(join(OUT, `${slugOf(file)}.md`), front.join('\n') + rewrite(rest.join('\n')).replace(/^\n+/, ''));
  n += 1;
}
for (const dir of ['assets', 'demo']) {
  const from = join(DOCS, dir);
  if (existsSync(from)) {
    rmSync(join(SITE, 'public', dir), { recursive: true, force: true });
    cpSync(from, join(SITE, 'public', dir), { recursive: true });
  }
}
if (existsSync(join(SITE, '..', 'llms.txt'))) cpSync(join(SITE, '..', 'llms.txt'), join(SITE, 'public', 'llms.txt'));
const AV = join(SITE, '..', 'central', 'app', 'static', 'avatars');
if (existsSync(AV)) {
  rmSync(join(SITE, 'public', 'avatars'), { recursive: true, force: true });
  cpSync(AV, join(SITE, 'public', 'avatars'), { recursive: true });
}
console.log(`sync-docs: ${n} páginas de docs/ (base ${BASE})`);
