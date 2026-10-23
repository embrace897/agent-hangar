// O site de documentação do Agent Hangar (Astro Starlight). As páginas vêm de ../docs (scripts/sync-docs.mjs); aqui
// ficam a navegação, o tema e o endereço (GitHub Pages: https://valteresj2.github.io/agent-hangar/).
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';

const base = process.env.DOCS_BASE ?? '/agent-hangar';

export default defineConfig({
  site: process.env.DOCS_SITE ?? 'https://valteresj2.github.io',
  base,
  trailingSlash: 'always',
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
          { label: 'Overview', link: '/' },
          { slug: 'concepts' }, { slug: 'feature-map' }, { slug: 'comparison', label: 'Comparison' },
        ] },
        { label: 'Install', items: [
          { slug: 'install', label: 'Install with Docker' }, { slug: 'cloud-vm', label: 'Cloud VM (AWS, Azure, GCP)' }, { slug: 'kubernetes', label: 'Kubernetes (GKE, AKS, EKS)' }, { slug: 'backup', label: 'Backup and upgrade' },
        ] },
        { label: 'Build agents', items: [
          { slug: 'spec', label: 'Agent spec' }, { slug: 'templates' }, { slug: 'composer', label: 'Reuse before you build' }, { slug: 'execution' },
          { slug: 'harnesses' }, { slug: 'memory', label: 'Memory' }, { slug: 'guide', label: 'Agent guide' },
        ] },
        { label: 'Connect', items: [
          { slug: 'clients' }, { slug: 'connectors' }, { slug: 'mcp-catalog', label: 'Docker MCP catalog' }, { slug: 'remote-mcp', label: 'Remote MCPs with OAuth' }, { slug: 'plugins' },
        ] },
        { label: 'Put agents to work', items: [
          { slug: 'digital-employee', label: 'Digital employees' }, { slug: 'work', label: 'Day-to-day work' }, { slug: 'issues', label: 'Issues board' }, { slug: 'analyst', label: "The analyst's day" }, { slug: 'workspace', label: 'Member workspace' },
        ] },
        { label: 'Govern and operate', items: [
          { slug: 'access', label: 'Users, teams and access' }, { slug: 'security' }, { slug: 'observability' }, { slug: 'supply-chain', label: 'Supply chain' },
        ] },
        { label: 'Reference', items: [
          { slug: 'api' }, { slug: 'cli' },
          { label: 'Interactive demo', link: '/demo/', attrs: { target: '_blank' } },  // o Starlight já põe o base
          { label: 'Changelog', link: 'https://github.com/valteresj2/agent-hangar/blob/main/CHANGELOG.md', attrs: { target: '_blank' } },
        ] },
      ],
    }),
  ],
});
