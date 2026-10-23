// O site do Hangar AI Labs: a página de entrada (src/pages/index.astro) e a documentação do Agent Hangar em /docs/
// (Astro Starlight; as páginas vêm de ../docs por scripts/sync-docs.mjs). GitHub Pages: valteresj2.github.io/agent-hangar/
import { readdirSync } from 'node:fs';
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';

const base = process.env.DOCS_BASE ?? '/agent-hangar';
// a raiz agora é a página de entrada do Hangar AI Labs e a documentação mora em /docs/: os endereços antigos
// (/agent-hangar/install/, já compartilhados) redirecionam para os novos
const redirects = Object.fromEntries(readdirSync(new URL('../docs/', import.meta.url))
  .filter((f) => f.endsWith('.md') && !f.includes('.pt-BR.') && f !== 'MELHORIAS.md')
  .map((f) => [`/${f.replace(/\.md$/, '').toLowerCase()}/`, `${base}/docs/${f.replace(/\.md$/, '').toLowerCase()}/`]));

export default defineConfig({
  site: process.env.DOCS_SITE ?? 'https://valteresj2.github.io',
  base,
  trailingSlash: 'always',
  redirects,
  integrations: [
    starlight({
      title: 'Agent Hangar',
      description: 'One home for every AI agent your company builds, whatever tool built it.',
      logo: { src: './src/assets/logo.svg', alt: 'Agent Hangar' },
      favicon: '/favicon.svg',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/valteresj2/agent-hangar' }],
      customCss: ['./src/styles/custom.css'],
      head: [
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.googleapis.com' } },
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: true } },
        { tag: 'link', attrs: { rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap' } },
      ],
      lastUpdated: false,
      pagination: true,
      sidebar: [
        { label: 'Get started', items: [
          { label: 'Overview', link: '/docs/' },
          { slug: 'docs/concepts' }, { slug: 'docs/feature-map' }, { slug: 'docs/comparison', label: 'Comparison' },
        ] },
        { label: 'Install', items: [
          { slug: 'docs/install', label: 'Install with Docker' }, { slug: 'docs/cloud-vm', label: 'Cloud VM (AWS, Azure, GCP)' }, { slug: 'docs/kubernetes', label: 'Kubernetes (GKE, AKS, EKS)' }, { slug: 'docs/backup', label: 'Backup and upgrade' },
        ] },
        { label: 'Build agents', items: [
          { slug: 'docs/spec', label: 'Agent spec' }, { slug: 'docs/templates' }, { slug: 'docs/composer', label: 'Reuse before you build' }, { slug: 'docs/execution' },
          { slug: 'docs/harnesses' }, { slug: 'docs/memory', label: 'Memory' }, { slug: 'docs/guide', label: 'Agent guide' },
        ] },
        { label: 'Connect', items: [
          { slug: 'docs/clients' }, { slug: 'docs/connectors' }, { slug: 'docs/mcp-catalog', label: 'Docker MCP catalog' }, { slug: 'docs/remote-mcp', label: 'Remote MCPs with OAuth' }, { slug: 'docs/plugins' },
        ] },
        { label: 'Put agents to work', items: [
          { slug: 'docs/digital-employee', label: 'Digital employees' }, { slug: 'docs/work', label: 'Day-to-day work' }, { slug: 'docs/issues', label: 'Issues board' }, { slug: 'docs/analyst', label: "The analyst's day" }, { slug: 'docs/workspace', label: 'Member workspace' },
        ] },
        { label: 'Govern and operate', items: [
          { slug: 'docs/access', label: 'Users, teams and access' }, { slug: 'docs/security' }, { slug: 'docs/observability' }, { slug: 'docs/supply-chain', label: 'Supply chain' },
        ] },
        { label: 'Reference', items: [
          { slug: 'docs/api' }, { slug: 'docs/cli' },
          { label: 'Interactive demo', link: '/demo/', attrs: { target: '_blank' } },  // o Starlight já põe o base
          { label: 'Changelog', link: 'https://github.com/valteresj2/agent-hangar/blob/main/CHANGELOG.md', attrs: { target: '_blank' } },
        ] },
      ],
    }),
  ],
});
