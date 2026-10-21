/* Workspace do membro (portal /app): início simples, meus agentes (pessoais e que gerencio), organograma visual com
   arrastar-e-soltar, conectar a minha IA (MCP da plataforma), meus acessos — e, para o admin, a política de acesso
   dos membros. Usa os helpers de app.js ($, api, esc, act, toast, ME…). */

const PERSON_IC = n => `<span class="av">${esc(((n || '?')[0] || '?').toUpperCase())}</span>`;
const workingChip = w => w ? `<a class="chip work-on" href="#/tasks/${w.id}"><i class="dot-live"></i><span>trabalhando em</span> <span data-noi18n>${esc(w.title)}</span></a>` : '';
const modelChip = n => n.model ? `<span class="chip" title="${esc(n.connection || '')}">${icon('chip')}<span data-noi18n>${esc(n.model)}</span></span>` : '';
const harnessChip = h => h ? `<span class="chip">${icon('server')}<span data-noi18n>${esc(h)}</span></span>` : '';

/* Sem provedor de LLM, os agentes respondem com o modelo de teste: o aviso leva quem pode resolver à chave de API. */
function llmBanner(ready) {
  if (ready) return '';
  return ME.is_admin
    ? `<div class="card warn-card"><h2>Adicione a chave de um provedor de LLM</h2>
        <div>Sem um provedor, os agentes respondem com um <b>modelo de teste</b> (respostas fictícias, sem custo). Cadastre a chave de API da OpenAI, Anthropic, Google Gemini, OpenRouter, DeepSeek, Azure OpenAI ou do gateway da empresa (LiteLLM, Portkey…) — a conexão é testada na hora.</div>
        <div class="row mt"><a href="/ui/#/providers"><button>Cadastrar provedor</button></a></div></div>`
    : '<div class="card warn-card">Os agentes estão no <b>modo de teste</b> (respostas fictícias) até o admin cadastrar um provedor de LLM.</div>';
}

/* ---------- início do membro ---------- */
async function memberHome() {
  const [w, dec] = await Promise.all([api('/me/workspace'), api('/decisions').catch(() => [])]);
  const toDecide = dec.filter(d => d.kind !== 'notice');
  const hour = new Date().getHours();
  const hello = window.t(hour < 12 ? 'Bom dia' : hour < 18 ? 'Boa tarde' : 'Boa noite');
  const first = ME.user ? (ME.user.name || ME.user.email).split(/[ @]/)[0] : '';
  const working = (w.agents || []).filter(a => a.working_on);
  const cap = w.personal_cap_usd ? ` / ${usd(w.personal_cap_usd)}` : '';
  main.innerHTML = `<div class="row between"><div><h1>${esc(hello)}${first ? `, <span data-noi18n>${esc(first)}</span>` : ''}</h1>
    <div class="sub"><span>Workspace</span> <b data-noi18n>${esc(w.workspace || '')}</b></div></div>
    <div class="row">${w.personal_allowed ? '<a href="#/employees/new?personal=1"><button>+ Novo agente</button></a>' : ''}<a href="#/connect-ai"><button class="ghost">Conectar minha IA</button></a></div></div>
  ${llmBanner(w.llm_ready !== false)}
  <div class="board">
    ${kpi(fmt(w.to_decide), 'Para você decidir')}
    ${kpi(fmt((w.agents || []).length), 'Meus agentes')}
    ${kpi(fmt(working.length), 'Trabalhando agora')}
    ${kpi(usd(w.month_cost_usd), 'Custo no mês')}
  </div>
  ${w.personal_count || w.personal_cap_usd ? `<div class="mute small mt"><span>Agentes pessoais:</span> ${w.personal_count}/${w.personal_max} · <span>gasto no mês</span> ${usd(w.personal_spent_usd)}${cap}</div>` : ''}
  <div class="grid g2 mt">
    <div class="card ${toDecide.length ? 'warn-card' : ''}"><div class="row between"><h2>Decisões</h2><a href="#/decisions" class="small">Abrir →</a></div>
      ${toDecide.length ? `<ul class="list">${toDecide.slice(0, 6).map(d => `<li><a href="#/decisions"><b data-noi18n>${esc(d.employee_name)}</b> · ${esc(window.t(KIND_LABEL[d.kind] || d.kind))}</a>
        <span class="mute small">${ago(d.created_at)}</span></li>`).join('')}</ul>` : '<div class="mute">Nada esperando por você.</div>'}</div>
    <div class="card"><div class="row between"><h2>Trabalhando agora</h2><a href="#/org" class="small">Organograma →</a></div>
      ${working.length ? `<ul class="list">${working.map(a => `<li><a href="#/employees/${esc(a.slug)}"><b data-noi18n>${esc(a.name)}</b></a> ${workingChip(a.working_on)}</li>`).join('')}</ul>`
        : '<div class="mute">Nenhum dos seus agentes está numa tarefa agora.</div>'}</div>
  </div>
  <div class="grid g2 mt">
    <div class="card"><div class="row between"><h2>Minhas tarefas</h2><a href="#/my-issues" class="small">Minhas issues →</a></div>
      ${(w.my_tasks || []).length ? `<table class="small">${w.my_tasks.map(t => `<tr><td><a href="#/tasks/${t.id}" data-noi18n>${esc(t.title)}</a></td><td>${pill(t.status)}</td><td class="mute">${when(t.created_at)}</td></tr>`).join('')}</table>`
        : '<div class="mute">Você ainda não pediu tarefas aos agentes. Peça pela página do agente ou pela sua IA conectada.</div>'}</div>
    <div class="card"><div class="row between"><h2>Meus agentes</h2><a href="#/mine" class="small">Ver todos →</a></div>
      ${(w.agents || []).length ? `<ul class="list">${w.agents.slice(0, 6).map(a => `<li><a href="#/employees/${esc(a.slug)}"><b data-noi18n>${esc(a.name)}</b></a>
        ${a.personal ? '<span class="pill info">pessoal</span>' : ''} ${empPill(a.status)}</li>`).join('')}</ul>`
        : `<div class="mute">Nenhum agente seu ainda.</div>${w.personal_allowed ? '<a href="#/employees/new?personal=1" class="small">Criar o primeiro →</a>' : ''}`}</div>
  </div>`;
}

/* ---------- meus agentes ---------- */
const ASK_AI = () => window.LANG === 'en'
  ? 'Create a personal agent for me in Agent Hangar (hire_employee with personal=true). Ask me what it should do, which systems it uses and how much autonomy it gets, then show me the summary before hiring.'
  : 'Crie um agente pessoal para mim no Agent Hangar (hire_employee com personal=true). Pergunte o que ele deve fazer, quais sistemas usa e quanta autonomia tem, e me mostre o resumo antes de contratar.';

const BUILT_WHY = { personal: 'pessoal', maintainer: 'você mantém o time', creator: 'você criou' };
const AGENT_STATUS = { draft: ['rascunho', ''], tested: ['testado', 'info'], test_failed: ['testes falharam', 'bad'], stage: ['em stage', 'info'],
  production: ['em produção', 'ok'] };

function builtRow(a) {  // um agente comum em Meus agentes: abre a página do agente (não a de Digital employee)
  const st = AGENT_STATUS[a.status] || [a.status, ''];
  return `<div class="card agent-card mt"><div class="row between"><div><a href="#/agents/${esc(a.slug)}"><b data-noi18n>${esc(a.name)}</b></a>
      <span class="pill ${st[1]}">${esc(window.t(st[0]))}</span> <span class="pill">${esc(window.t(BUILT_WHY[a.why] || a.why))}</span>
      <div class="mute small one-line" data-noi18n style="max-width:620px">${esc(a.objective)}</div></div>
      <a href="#/agents/${esc(a.slug)}"><button class="ghost small">Abrir</button></a></div>
    <div class="row small mt"><span class="chip">v${a.version}</span>${a.team ? `<span class="chip" data-noi18n>${esc(a.team)}</span>` : ''}
      ${a.schedules ? `<span class="chip"><span>agendado</span>${a.next_run_at ? ` · <span>próxima</span> ${esc(new Date(a.next_run_at).toLocaleString(window.LOCALE))}` : ''}</span>` : ''}
      <span class="chip"><span>30 dias:</span> ${usd(a.cost_30d_usd)}</span></div></div>`;
}

async function myAgentsPage() {
  const [w, chart] = await Promise.all([api('/me/workspace'), api('/org-chart?mine=1').catch(() => ({ nodes: [] }))]);
  const card = Object.fromEntries((chart.nodes || []).map(n => [n.slug, n]));
  const list = (w.agents || []).map(a => ({ ...a, ...(card[a.slug] || {}), personal: a.personal, name: a.name }));
  const personal = list.filter(a => a.personal), team = list.filter(a => !a.personal);
  const row = a => `<div class="card agent-card"><div class="row between"><div><a href="#/employees/${esc(a.slug)}"><b data-noi18n>${esc(a.name)}</b></a>
      ${a.personal ? '<span class="pill info">pessoal</span>' : ''} ${empPill(a.status)}
      <div class="mute small" data-noi18n>${esc(a.title || '')}</div></div>
      <div class="row">${a.personal ? `<button class="ghost small mine-promote" data-slug="${esc(a.slug)}">Promover para o time</button>` : ''}
        <a href="#/employees/${esc(a.slug)}"><button class="ghost small">Abrir</button></a></div></div>
    <div class="row small mt">${modelChip(a)}${harnessChip(a.harness)}<span class="chip">${esc(LEVEL_LABEL[a.level || a.autonomy_level] || a.level || a.autonomy_level || '')}</span>
      ${(a.specialists || []).map(s => `<span class="chip" title="${esc(s.harness || '')}">${icon('bot')}<span data-noi18n>${esc(s.name)}</span>${s.harness ? ` <span class="mute" data-noi18n>· ${esc(s.harness)}</span>` : ''}</span>`).join('')}
      ${workingChip(a.working_on)}</div></div>`;
  const left = Math.max(0, (w.personal_max || 0) - (w.personal_count || 0));
  main.innerHTML = `<div class="row between"><div><h1>Meus agentes</h1><div class="sub">Os seus agentes pessoais (só você vê) e os do time que você gerencia — cada um com o modelo e o harness do seu papel.</div></div>
    <div class="row">${w.personal_allowed ? `<a href="#/employees/new?personal=1"><button ${left ? '' : 'disabled'}>+ Novo agente pessoal</button></a>` : ''}</div></div>
  ${w.personal_allowed ? `<div class="card hero"><h2>${icon('chat')} Ou peça para a sua IA</h2>
    <div class="mute small">No Claude, ChatGPT, Codex, Cursor ou Gemini conectados ao Hangar (<a href="#/connect-ai">Conectar minha IA</a>), cole:</div>
    <div class="mt">${copyable(ASK_AI())}</div>
    <div class="mute small mt"><span>Agentes pessoais:</span> ${w.personal_count}/${w.personal_max}${w.personal_cap_usd ? ` · <span>orçamento do mês</span> ${usd(w.personal_spent_usd)} / ${usd(w.personal_cap_usd)}` : ''}</div></div>`
    : '<div class="card warn-card">O workspace não permite agentes pessoais. Fale com o admin, ou crie agentes do time em <a href="#/employees/new">Contratar</a>.</div>'}
  <h2 class="mt"><span>Pessoais</span> (${personal.length})</h2>${personal.map(row).join('') || '<div class="card empty"><div class="mute">Nenhum agente pessoal ainda.</div></div>'}
  <h2 class="mt"><span>Do time, que eu gerencio</span> (${team.length})</h2>${team.map(row).join('') || '<div class="card empty"><div class="mute">Nenhum agente do time sob sua gestão.</div></div>'}
  <h2 class="mt"><span>Agentes que eu criei ou mantenho</span> (${(w.built_agents || []).length})</h2>
  <div class="mute small">Agentes comuns (não são Digital employees): os seus pessoais, os dos times que você mantém e os que você criou — pelo portal ou pela sua IA.</div>
  ${(w.built_agents || []).map(builtRow).join('') || '<div class="card empty mt"><div class="mute">Nenhum agente comum seu ainda.</div></div>'}`;
  main.querySelectorAll('.mine-promote').forEach(b => b.onclick = e => {
    const note = prompt(window.t('Por que o time deve usar este agente? (quem mantém o time decide)'));
    if (note === null) return;
    act(e.target, () => api(`/employees/${b.dataset.slug}/promote-to-team`, { method: 'POST', body: { note } }), 'Pedido enviado ao mantenedor do time');
  });
}

/* ---------- organograma visual (arrastar para mudar a quem reporta) ---------- */
async function orgVisualPage() {
  const q = qsOf();
  if (q.get('view') === 'tree' && !USER_MODE) return orgChartPage();
  const team = USER_MODE ? '' : (q.get('team') || '');
  // no portal, o organograma é o da pessoa: ela no topo (CEO ou Staff) e, abaixo, os agentes dela
  const d = await api('/org-chart' + (USER_MODE ? '?mine=1' : team ? `?team=${encodeURIComponent(team)}` : ''));
  const by = Object.fromEntries(d.nodes.map(n => [n.slug, n]));
  const kids = {};
  d.nodes.forEach(n => { const p = n.reports_to && by[n.reports_to] ? n.reports_to : ''; (kids[p] = kids[p] || []).push(n); });
  const teams = [...new Set(d.nodes.map(n => n.team).filter(Boolean))];
  const node = n => `<li><div class="oc-card ${n.personal ? 'personal' : ''} st-${esc(n.status)}" draggable="true" data-slug="${esc(n.slug)}" tabindex="0">
      <div class="row between"><a href="#/employees/${esc(n.slug)}"><b data-noi18n>${esc(n.name)}</b></a>${empPill(n.status)}</div>
      <div class="mute small" data-noi18n>${esc(n.title || '')}</div>
      <div class="oc-chips">${modelChip(n)}${harnessChip(n.harness)}<span class="chip">${esc(LEVEL_LABEL[n.level] || n.level)}</span>${n.personal ? '<span class="pill info">pessoal</span>' : ''}</div>
      ${(n.specialists || []).length ? `<div class="oc-spec small">${n.specialists.map(s => `<span title="${esc(s.harness || '')}">${icon('bot')}<span data-noi18n>${esc(s.name)}</span></span>`).join('')}</div>` : ''}
      ${!USER_MODE && n.manager ? `<div class="mute small">${PERSON_IC(n.manager)} <span data-noi18n>${esc(n.manager)}</span></div>` : ''}
      ${workingChip(n.working_on)}</div>
    ${(kids[n.slug] || []).length ? `<ul>${kids[n.slug].map(node).join('')}</ul>` : ''}</li>`;
  const me = d.me;
  const top = (kids[''] || []).map(node).join('');
  const meCard = me ? `<li><div class="oc-card oc-person" id="oc-root" tabindex="0">
      <div class="row" style="gap:10px">${me.avatar_url ? `<img class="oc-person-av" src="${esc(me.avatar_url)}" alt="" referrerpolicy="no-referrer">` : `<span class="oc-person-av">${esc((me.name || '?')[0].toUpperCase())}</span>`}
        <div><b data-noi18n>${esc(me.name)}</b><div class="mute small"><span class="pill ${me.role === 'CEO' ? 'warn' : 'info'}">${esc(me.role)}</span> <span>você</span></div></div></div>
      <div class="mute small">${d.nodes.length} <span>agente(s) seu(s)</span> · <span>solte um agente aqui para ele reportar direto a você</span></div></div>
    ${top ? `<ul>${top}</ul>` : ''}</li>` : '';
  main.innerHTML = `<div class="row between"><div><h1>Organograma</h1><div class="sub">${USER_MODE
      ? 'Você no topo e, abaixo, os seus agentes (os pessoais e os que você gerencia). Arraste um agente para cima de outro para mudar a quem ele reporta.'
      : 'Arraste um agente para cima de outro para mudar a quem ele reporta. Se você não gerencia o agente, vira uma proposta para quem gerencia decidir.'}</div></div>
    ${USER_MODE ? '' : `<div class="row"><select id="oc-team" style="width:auto"><option value="">${window.t('Todos os times')}</option>${teams.map(t => `<option ${t === team ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>
      <a href="#/org?view=tree${team ? `&team=${encodeURIComponent(team)}` : ''}" class="small">Lista</a></div>`}</div>
  ${me ? `<div class="card oc-wrap"><ul class="oc-tree">${meCard}</ul></div>
    ${d.nodes.length ? '' : '<div class="card empty mt"><div class="mute">Você ainda não tem agentes. Crie um em <a href="#/mine">Meus agentes</a> (ou peça para a sua IA): ele aparece aqui, abaixo de você.</div></div>'}`
    : d.nodes.length ? `<div class="oc-root" id="oc-root">Solte aqui para não reportar a ninguém</div>
    <div class="card oc-wrap"><ul class="oc-tree">${top}</ul></div>`
    : '<div class="card empty"><div class="mute">Nenhum Digital employee visível.</div></div>'}`;
  const ts = $('#oc-team');
  if (ts) ts.onchange = () => { location.hash = '#/org' + (ts.value ? `?team=${encodeURIComponent(ts.value)}` : ''); };
  let dragged = '';
  const move = async (slug, to) => {
    const n = by[slug];
    if (!n || slug === to || (n.reports_to || '') === to) return;
    try {
      if (n.can_manage) {
        await api(`/employees/${slug}`, { method: 'PATCH', body: { reports_to: to } });
        toast(window.t('Organograma atualizado'));
      } else {
        const why = prompt(window.t('Você não gerencia este agente: a mudança vira uma proposta. Por quê?'));
        if (why === null) return;
        await api(`/employees/${slug}/propose-reports-to`, { method: 'POST', body: { to, why } });
        toast(window.t('Proposta enviada a quem gerencia o agente'));
      }
      route();
    } catch (e) { toast(e.message, true); }
  };
  main.querySelectorAll('.oc-card').forEach(c => {
    c.ondragstart = e => { dragged = c.dataset.slug; e.dataTransfer.setData('text/plain', dragged); c.classList.add('dragging'); };
    c.ondragend = () => { c.classList.remove('dragging'); main.querySelectorAll('.drop').forEach(x => x.classList.remove('drop')); };
    c.ondragover = e => { if (dragged && dragged !== c.dataset.slug) { e.preventDefault(); c.classList.add('drop'); } };
    c.ondragleave = () => c.classList.remove('drop');
    c.ondrop = e => { e.preventDefault(); c.classList.remove('drop'); move(dragged, c.dataset.slug); };
  });
  const root = $('#oc-root');
  if (root) {
    root.ondragover = e => { if (dragged) { e.preventDefault(); root.classList.add('drop'); } };
    root.ondragleave = () => root.classList.remove('drop');
    root.ondrop = e => { e.preventDefault(); root.classList.remove('drop'); move(dragged, ''); };
  }
}

/* ---------- conectar a minha IA (o MCP da plataforma) ---------- */
const AI_CLIENTS = [
  { id: 'claude-code', name: 'Claude Code', file: '', how: 'Rode no terminal:' },
  { id: 'claude-desktop', name: 'Claude Desktop', file: 'claude_desktop_config.json', how: 'Em Configurações → Desenvolvedor → Editar config, junte em claude_desktop_config.json:' },
  { id: 'codex', name: 'Codex', file: 'config.toml', how: 'Junte em ~/.codex/config.toml:' },
  { id: 'cursor', name: 'Cursor', file: 'mcp.json', how: 'Junte em ~/.cursor/mcp.json (ou .cursor/mcp.json do projeto):' },
  { id: 'gemini', name: 'Gemini CLI', file: 'settings.json', how: 'Junte em ~/.gemini/settings.json:' },
  { id: 'vscode', name: 'VS Code (Copilot)', file: 'mcp.json', how: 'Junte em .vscode/mcp.json (ou rode “MCP: Add Server”):' },
  { id: 'opencode', name: 'OpenCode', file: 'opencode.json', how: 'Junte em opencode.json:' },
  { id: 'web', name: 'Claude.ai e ChatGPT', file: '', how: '' },
];
function aiSnippet(id, url, key) {
  const auth = `Bearer ${key}`, n = 'agent-hangar', j = o => JSON.stringify(o, null, 2);
  return {
    'claude-code': `claude mcp add --transport http ${n} ${url} --header "Authorization: ${auth}"`,
    'claude-desktop': j({ mcpServers: { [n]: { command: 'npx', args: ['-y', 'mcp-remote', url, '--header', `Authorization: ${auth}`].concat(url.startsWith('https://') ? [] : ['--allow-http']) } } }),
    codex: `[mcp_servers.${n}]\nurl = "${url}"\nhttp_headers = { Authorization = "${auth}" }`,
    cursor: j({ mcpServers: { [n]: { url, headers: { Authorization: auth } } } }),
    gemini: j({ mcpServers: { [n]: { httpUrl: url, headers: { Authorization: auth } } } }),
    vscode: j({ servers: { [n]: { type: 'http', url, headers: { Authorization: auth } } } }),
    opencode: j({ $schema: 'https://opencode.ai/config.json', mcp: { [n]: { type: 'remote', url, headers: { Authorization: auth } } } }),
  }[id] || '';
}

async function connectAiPage() {
  const c = await api('/connect');
  let key = '';
  let pick = qsOf().get('client') || 'claude-code';
  const draw = () => {
    const cl = AI_CLIENTS.find(x => x.id === pick) || AI_CLIENTS[0];
    const snip = aiSnippet(cl.id, c.mcp_url, key || '<SUA_CHAVE>');
    $('#ai-body').innerHTML = cl.id === 'web'
      ? `<div>Cole só a URL ${copyable(c.mcp_url)}: o app pede para você entrar aqui no portal e autorizar (OAuth). Não precisa de chave.</div>
        <ul class="small mt"><li><b>Claude.ai</b>: Configurações → Conectores → Adicionar conector personalizado.</li>
          <li><b>ChatGPT</b>: Configurações → Apps e conectores → Criar (modo desenvolvedor), autenticação OAuth.</li></ul>
        ${c.mcp_url.startsWith('https://') ? '' : '<div class="bad-text small">Estes apps exigem um endereço HTTPS público: publique o hangar atrás de um domínio ou túnel.</div>'}`
      : `<div class="small">${esc(window.t(cl.how))}</div><pre id="ai-snip" data-noi18n>${esc(snip)}</pre>
        <div class="row"><button class="ghost small" id="ai-copy">Copiar</button>${cl.file ? `<button class="ghost small" id="ai-dl"><span>Baixar</span> <span data-noi18n>${esc(cl.file)}</span></button>` : ''}</div>
        ${key ? '' : '<div class="mute small mt">Gere a chave acima para a configuração já sair pronta.</div>'}`;
    main.querySelectorAll('.ai-tab').forEach(t => t.classList.toggle('on', t.dataset.id === pick));
    const cp = $('#ai-copy'); if (cp) cp.onclick = () => { navigator.clipboard.writeText(snip); toast(window.t('Copiado')); };
    const dl = $('#ai-dl'); if (dl) dl.onclick = () => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([snip + '\n'], { type: 'text/plain' })); a.download = cl.file; a.click(); URL.revokeObjectURL(a.href);
    };
  };
  main.innerHTML = `<h1>Conectar minha IA</h1><div class="sub">Ligue o Claude, Codex, Cursor, Gemini, VS Code ou OpenCode ao Hangar (MCP) e crie, edite e acompanhe os seus agentes conversando — como você faz hoje.</div>
  <div class="card hero"><h2>1. Sua chave pessoal</h2>
    <div class="mute small">A chave age como você: os seus times, papéis e agentes pessoais. Ela aparece uma vez só; você revoga em <a href="#/keys">Minhas chaves</a>.</div>
    <div class="row mt"><button id="ai-key">Gerar chave para esta ferramenta</button><span id="ai-key-out"></span></div></div>
  <div class="card mt"><h2>2. A ferramenta</h2>
    <div class="tabs ai-tabs">${AI_CLIENTS.map(x => `<a href="#" class="ai-tab" data-id="${x.id}" data-noi18n>${esc(x.name)}</a>`).join('')}</div>
    <div id="ai-body" class="mt"></div></div>
  <div class="card mt"><h2>3. Peça</h2><div class="mute small">Depois de conectar, experimente:</div>
    <ul class="small"><li>${copyable(window.LANG === 'en' ? 'What is waiting for me in Agent Hangar?' : 'O que está esperando por mim no Agent Hangar?')}</li>
      <li>${copyable(ASK_AI())}</li>
      <li>${copyable(window.LANG === 'en' ? 'Show my agents and what each one is doing now.' : 'Mostre os meus agentes e o que cada um está fazendo agora.')}</li></ul></div>
  ${typeof vscodeCard === 'function' ? vscodeCard() : ''}`;
  main.querySelectorAll('.ai-tab').forEach(t => t.onclick = e => { e.preventDefault(); pick = t.dataset.id; draw(); });
  $('#ai-key').onclick = e => act(e.target, async () => {
    const cl = AI_CLIENTS.find(x => x.id === pick) || AI_CLIENTS[0];
    const r = await api('/keys', { method: 'POST', body: { name: `${cl.name} · ${new Date().toISOString().slice(0, 10)}`, scopes: ['user'], agents: [] } });
    key = r.key;
    $('#ai-key-out').innerHTML = `<span class="pill ok">chave criada</span> <span class="mute small">guarde agora: ela não aparece de novo</span>`;
    draw();
  });
  draw();
}

/* ---------- meus acessos ---------- */
const METHOD_LABEL = { password: 'senha', sso: 'SSO', activation: 'código de ativação', key: 'token pessoal', '—': '—' };

async function myAccessPage() {
  const ss = await api('/me/sessions');
  main.innerHTML = `<h1>Meus acessos</h1><div class="sub">Onde a sua conta está aberta. Encerre o que você não reconhece; para entrar em outro navegador sem senha, gere um código de ativação.</div>
  <div class="card"><div class="row between"><h2><span>Navegadores e aparelhos</span> (${ss.length})</h2>${ss.length > 1 ? '<button class="ghost" id="ma-others">Sair de todos os outros</button>' : ''}</div>
    <table><tr><th>Aparelho</th><th>Entrou com</th><th>Último uso</th><th>IP</th><th></th></tr>
    ${ss.map(s => `<tr><td><b data-noi18n>${esc(s.device)}</b> ${s.current ? '<span class="pill ok">este</span>' : ''}</td><td>${esc(window.t(METHOD_LABEL[s.method] || s.method))}</td>
      <td>${when(s.last_seen_at)}</td><td class="mute small" data-noi18n>${esc(s.ip || '')}</td>
      <td>${s.current ? '' : `<button class="ghost small ma-rev" data-id="${s.id}">Encerrar</button>`}</td></tr>`).join('')}</table></div>
  <div class="card mt" id="ma-push"></div>
  <div class="card mt"><h2>Entrar em outro navegador</h2>
    <div class="mute small">Gere um código e digite no outro navegador em <b>Entrar → Entrar com código de ativação</b>. Vale uma vez, por 24 horas.</div>
    <div class="row mt"><button id="ma-code">Gerar código</button><span id="ma-code-out"></span></div></div>`;
  pushCard($('#ma-push')).catch(() => {});
  main.querySelectorAll('.ma-rev').forEach(b => b.onclick = e => act(e.target, () => api(`/me/sessions/${b.dataset.id}`, { method: 'DELETE' }).then(route), 'Acesso encerrado'));
  const o = $('#ma-others');
  if (o) o.onclick = e => confirm(window.t('Sair de todos os outros navegadores e aparelhos?')) && act(e.target, () => api('/me/sessions/revoke-others', { method: 'POST' }).then(route), 'Os outros acessos foram encerrados');
  $('#ma-code').onclick = e => act(e.target, async () => {
    const r = await api('/me/activation-code', { method: 'POST' });
    const link = `${location.origin}/app/#/login?code=${encodeURIComponent(r.code)}`;
    $('#ma-code-out').innerHTML = `<span class="code-big" data-noi18n>${esc(r.code)}</span> <span class="mute small"><span>válido até</span> ${new Date(r.expires_at).toLocaleString(window.LOCALE)}</span>
      <div class="small mt">Ou abra o link no outro navegador: ${copyable(link)}</div>`;
  });
}

/* ---------- console (admin): Membros — pessoas e convites, acessos abertos, como entram ---------- */
const ROLE_TEAM = { consumer: 'Consumer — usa os agentes do time', developer: 'Developer — cria e edita os agentes do time', maintainer: 'Mantenedor — gerencia o time e aprova' };

async function membersPage(tab = 'people') {
  const tabs = { people: 'Pessoas', access: 'Acessos abertos', policy: 'Como entram' };
  const key = tabs[tab] ? tab : 'people';
  main.innerHTML = `<h1>Membros</h1><div class="sub">O workspace é a empresa: aqui você convida as pessoas, define o papel e os times de cada uma, como entram e onde cada conta está aberta. No portal (/app), cada membro vê só o que é da conta dele.</div>
    <div class="tabs">${Object.entries(tabs).map(([k, v]) => `<a href="#/members/${k}" class="${k === key ? 'on' : ''}">${v}</a>`).join('')}</div><div id="tab"></div>`;
  await ({ people: membersPeople, access: membersAccess, policy: membersPolicy })[key]($('#tab'));
}

async function membersPeople(el) {
  const [inv, teams] = await Promise.all([api('/workspace/invites').catch(() => []), api('/teams').catch(() => [])]);
  const admin = ME.is_admin;
  el.innerHTML = `${admin ? `<div class="card hero"><h2>Convidar um membro</h2>
      <div class="mute small">A pessoa recebe um e-mail com o link do portal (/app). O link entra uma vez, sem senha, e vale por 7 dias; depois ela entra com a conta da empresa (SSO) ou com um código gerado em Meus acessos.</div>
      <div class="grid g3 mt"><label>E-mail<input id="iv-email" type="email" placeholder="pessoa@empresa.com" autocomplete="off"></label>
        <label>Nome<input id="iv-name" placeholder="opcional"></label>
        <label>Papel na empresa<select id="iv-org"><option value="member">Membro</option><option value="auditor">Auditor</option><option value="admin">Admin</option></select></label>
        <label>Time<select id="iv-team"><option value="">— sem time por enquanto —</option>${teams.map(t => `<option value="${esc(t.slug)}" data-noi18n>${esc(t.name)}</option>`).join('')}</select></label>
        <label>Papel no time<select id="iv-role">${Object.entries(ROLE_TEAM).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select></label>
        <div class="row" style="align-items:flex-end"><button id="iv-go">Enviar convite</button></div></div>
      <div id="iv-out" class="mt"></div></div>` : ''}
    ${inv.length ? `<div class="card mt"><h2><span>Convites aguardando</span> (${inv.length})</h2><table><tr><th>Pessoa</th><th>Convidado por</th><th>Enviado</th><th>Validade</th><th></th></tr>
      ${inv.map(i => `<tr><td><b data-noi18n>${esc(i.name || i.email)}</b><div class="mute small" data-noi18n>${esc(i.email)}</div></td><td class="small" data-noi18n>${esc(i.invited_by)}</td>
        <td class="small">${when(i.invited_at)}</td><td>${i.expired ? '<span class="pill warn">expirado</span>' : `<span class="small">${new Date(i.expires_at).toLocaleDateString(window.LOCALE)}</span>`}</td>
        <td>${admin ? `<button class="ghost small iv-resend" data-id="${i.user_id}">Reenviar</button>` : ''}</td></tr>`).join('')}</table></div>` : ''}
    <div id="iv-users"></div>`;
  await usersPage($('#iv-users'));
  const show = r => {
    $('#iv-out').innerHTML = r.sent
      ? `<div class="card ok-card"><span>Convite enviado para</span> <b data-noi18n>${esc(r.user)}</b>.</div>`
      : `<div class="card warn-card">${esc(r.error || (r.email_ready ? '' : window.t('O e-mail da instalação não está configurado (SMTP): copie o link e mande para a pessoa.')))}
          <div class="mt">${copyable(r.link)}</div><div class="mute small mt">O link entra uma vez, sem senha. Não publique em lugar aberto.</div></div>`;
  };
  const go = $('#iv-go');
  if (go) go.onclick = e => act(e.target, async () => {
    const r = await api('/workspace/invites', { method: 'POST', body: { email: $('#iv-email').value.trim(), name: $('#iv-name').value.trim(),
      org_role: $('#iv-org').value, team: $('#iv-team').value, role: $('#iv-role').value } });
    await membersPeople(el);
    show(r);
  });
  el.querySelectorAll('.iv-resend').forEach(b => b.onclick = e => act(e.target, async () => {
    const r = await api(`/workspace/invites/${b.dataset.id}/resend`, { method: 'POST' });
    await membersPeople(el);
    show(r);
  }));
}

async function membersAccess(el) {
  const ss = await api('/workspace/sessions');
  const admin = ME.is_admin;
  const byUser = {};
  ss.forEach(s => { (byUser[s.user_id] = byUser[s.user_id] || []).push(s); });
  el.innerHTML = `<div class="card"><h2><span>Acessos abertos</span> (${ss.length})</h2>
    ${ss.length ? `<table><tr><th>Membro</th><th>Aparelho</th><th>Entrou com</th><th>Último uso</th><th></th></tr>
      ${Object.values(byUser).map(list => list.map((s, i) => `<tr><td>${i ? '' : `<b data-noi18n>${esc(s.name)}</b><div class="mute small" data-noi18n>${esc(s.user || '')}</div>`}</td>
        <td data-noi18n>${esc(s.device)}</td><td>${esc(window.t(METHOD_LABEL[s.method] || s.method))}</td><td>${when(s.last_seen_at)}</td>
        <td>${admin ? `<button class="ghost small wp-rev" data-id="${s.id}">Encerrar</button>${i ? '' : ` <button class="ghost small wp-rev-all" data-user="${s.user_id}">Todos deste membro</button>`}` : ''}</td></tr>`).join('')).join('')}</table>`
      : '<div class="mute">Nenhum acesso aberto.</div>'}</div>`;
  el.querySelectorAll('.wp-rev').forEach(b => b.onclick = e => act(e.target, () => api(`/workspace/sessions/${b.dataset.id}`, { method: 'DELETE' }).then(route), 'Acesso encerrado'));
  el.querySelectorAll('.wp-rev-all').forEach(b => b.onclick = e => confirm(window.t('Encerrar todos os acessos deste membro?')) &&
    act(e.target, () => api(`/workspace/users/${b.dataset.user}/sessions/revoke`, { method: 'POST' }).then(route), 'Acessos encerrados'));
}

async function membersPolicy(el) {
  const pol = await api('/workspace/policy');
  const L = pol.login, P = pol.personal_agents, admin = ME.is_admin;
  el.innerHTML = `<div class="grid g2">
    <div class="card"><h2>Como os membros entram</h2>
      ${[['password', 'Usuário e senha'], ['sso', 'SSO (Entra ID, Google, GitHub…)'], ['activation', 'Código de ativação (chave virtual)'], ['key', 'Token pessoal na tela de login']].map(([k, v]) =>
        `<label class="row small" style="gap:6px"><input type="checkbox" class="wp-login" value="${k}" ${L[k] ? 'checked' : ''} ${admin ? '' : 'disabled'} style="width:auto">${esc(window.t(v))}</label>`).join('')}
      <div class="mute small mt">O SSO é configurado em <a href="#/sso">SSO e SCIM</a>. Pelo menos um jeito de entrar fica ligado. Os convites usam o código de ativação; sem ele, o link do convite leva ao login (SSO). O MCP (chave pessoal ou OAuth) continua funcionando.</div></div>
    <div class="card"><h2>Agentes pessoais</h2>
      <label class="row small" style="gap:6px"><input type="checkbox" id="wp-pa" ${P.enabled ? 'checked' : ''} ${admin ? '' : 'disabled'} style="width:auto">Membros podem criar agentes pessoais</label>
      <div class="grid g2 mt">
        <label>Quem cria<select id="wp-who" ${admin ? '' : 'disabled'}><option value="members" ${P.who === 'members' ? 'selected' : ''}>qualquer membro</option><option value="developers" ${P.who === 'developers' ? 'selected' : ''}>developers e mantenedores</option></select></label>
        <label>Máximo por membro<input id="wp-max" type="number" min="0" max="50" value="${P.max_per_member}" ${admin ? '' : 'disabled'}></label>
        <label>Orçamento por membro / mês (US$, 0 = sem teto)<input id="wp-budget" type="number" min="0" step="1" value="${P.monthly_budget_usd}" ${admin ? '' : 'disabled'}></label>
        <label>Conexões de LLM liberadas (vírgula; vazio = todas)<input id="wp-llm" value="${esc((P.llm_connections || []).join(', '))}" ${admin ? '' : 'disabled'} data-noi18n></label></div>
    </div></div>
  ${admin ? '<button id="wp-save" class="mt">Salvar política</button>' : ''}`;
  const save = $('#wp-save');
  if (save) save.onclick = e => act(e.target, () => api('/workspace/policy', { method: 'PUT', body: {
    login: Object.fromEntries([...el.querySelectorAll('.wp-login')].map(c => [c.value, c.checked])),
    personal_agents: { enabled: $('#wp-pa').checked, who: $('#wp-who').value, max_per_member: Number($('#wp-max').value || 0),
      monthly_budget_usd: Number($('#wp-budget').value || 0), llm_connections: $('#wp-llm').value.split(',').map(x => x.trim()).filter(Boolean) } } }).then(route), 'Política salva');
}

/* ---------- cadastro da empresa: primeiro acesso do admin e Configurações → Empresa ---------- */
async function companyPage(first = false) {
  const c = await api('/workspace/company');
  const doms = c.domains.map(d => '@' + d).join('\n');
  main.innerHTML = `${first ? '<div class="mute small">Primeiro acesso</div>' : ''}<h1>${first ? 'Cadastro da empresa' : 'Empresa'}</h1>
    <div class="sub">${first ? 'Antes de começar: o nome da empresa e, se quiser, os domínios de e-mail dela. Dá para mudar depois em Configurações → Empresa.'
      : 'O nome da empresa, os domínios de e-mail e se só entram membros com e-mail corporativo.'}</div>
    <div class="card"><div class="grid g2">
      <label>Nome da empresa<input id="co-name" value="${esc(c.name)}" data-noi18n></label>
      <label>Domínios de e-mail da empresa (um por linha)<textarea id="co-doms" rows="3" placeholder="@empresa.com.br&#10;@empresa.com" data-noi18n>${esc(doms)}</textarea></label></div>
      <label class="row small mt" style="gap:6px"><input type="checkbox" id="co-enforce" style="width:auto" ${c.enforce ? 'checked' : ''}> <b>Só aceitar membros com e-mail desses domínios</b></label>
      <div class="mute small">Vale para todo jeito de entrar: convite e cadastro, primeiro login pelo SSO, SCIM e troca de e-mail. E-mail pessoal (Gmail, Outlook…) não é aceito como domínio da empresa.</div>
      <label class="row small mt" style="gap:6px"><input type="checkbox" id="co-sub" style="width:auto" ${c.subdomains ? 'checked' : ''}> Aceitar também os subdomínios (ex.: @br.empresa.com.br)</label>
      ${c.my_email ? `<div class="mute small mt"><span>O seu e-mail</span> <code class="inline" data-noi18n>${esc(c.my_email)}</code> <span>precisa estar num dos domínios para ligar a regra.</span></div>` : ''}
      <div class="row mt"><button id="co-save">${first ? 'Salvar e continuar' : 'Salvar'}</button>${first ? '<button class="ghost" id="co-skip">Configurar depois</button>' : ''}</div></div>
    ${c.outside.length ? `<div class="card mt warn-card"><h2>Membros fora dos domínios</h2>
      <div class="mute small">${c.enforce ? 'A regra está ligada: estas pessoas entraram antes dela.' : 'Se a regra for ligada, estas pessoas continuam como estão.'} Nada muda sozinho: mantenha, troque o e-mail delas para o corporativo em Membros, ou desative.</div>
      <table class="small mt"><tr><th>E-mail</th><th>Nome</th><th>Papel</th><th></th></tr>${c.outside.map(u => `<tr><td data-noi18n>${esc(u.email)}</td><td data-noi18n>${esc(u.name || '')}</td>
        <td>${esc(window.t(u.org_role))}</td><td>${u.email === c.my_email ? '<span class="mute">você</span>' : `<button class="ghost small co-off" data-id="${u.id}">Desativar</button>`}</td></tr>`).join('')}</table></div>` : ''}`;
  const done = () => { if (ME.org) ME.org.setup_done = true; if (first) location.hash = '#/'; else route(); };
  $('#co-save').onclick = e => act(e.target, async () => {
    const domains = $('#co-doms').value.split(/[\s,;]+/).map(x => x.trim()).filter(Boolean);
    const r = await api('/workspace/company', { method: 'PUT', body: { name: $('#co-name').value.trim(), domains,
      enforce: $('#co-enforce').checked, subdomains: $('#co-sub').checked } });
    if (r.enforce && r.outside.length) toast(`${r.outside.length} ${window.t('membro(s) fora dos domínios: veja a lista abaixo')}`);
    if (first && r.enforce && r.outside.length) { route(); if (ME.org) ME.org.setup_done = true; return; }
    done();
  }, 'Cadastro da empresa salvo');
  const skip = $('#co-skip');
  if (skip) skip.onclick = e => act(e.target, () => api('/workspace/company', { method: 'PUT', body: { skip: true } }).then(done));
  main.querySelectorAll('.co-off').forEach(b => b.onclick = e => confirm(window.t('Desativar este membro? Ele perde o acesso; dá para reativar em Membros.')) &&
    act(e.target, () => api(`/users/${b.dataset.id}`, { method: 'PATCH', body: { active: false } }).then(route), 'Membro desativado'));
}

/* ---------- app instalado (PWA) e notificações do navegador (Web Push) ---------- */
let installEvt = null;
if (window.HANGAR_MODE === 'user' && 'serviceWorker' in navigator) {  // app.js (USER_MODE) carrega depois deste arquivo
  navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).catch(() => {});
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); installEvt = e; const b = $('#pwa-install'); if (b) b.hidden = false; });
}
const pushOk = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
const b64key = s => { const p = '='.repeat((4 - s.length % 4) % 4); const r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from(r, c => c.charCodeAt(0)); };

async function pushState() {
  if (!pushOk()) return { supported: false };
  const reg = await navigator.serviceWorker.getRegistration('/app/');
  const sub = reg ? await reg.pushManager.getSubscription() : null;
  return { supported: true, permission: Notification.permission, sub };
}

async function pushEnable() {
  if (await Notification.requestPermission() !== 'granted') throw new Error(window.t('O navegador bloqueou as notificações deste site: libere nas configurações do site.'));
  const reg = await navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' });
  await navigator.serviceWorker.ready;
  const { public_key: key } = await api('/me/push');
  const sub = await reg.pushManager.getSubscription() || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64key(key) });
  await api('/me/push', { method: 'POST', body: sub.toJSON() });
}

async function pushDisable() {
  const st = await pushState();
  if (st.sub) { await api('/me/push/remove', { method: 'POST', body: { endpoint: st.sub.endpoint } }).catch(() => {}); await st.sub.unsubscribe(); }
}

async function pushCard(el) {
  const st = await pushState();
  const standalone = window.matchMedia('(display-mode: standalone)').matches;
  el.innerHTML = `<h2>App e notificações</h2>
    <div class="mute small">Instale o portal como app (Windows, Mac, Linux, Android ou iPhone) e receba os pedidos de decisão no aparelho, mesmo com o portal fechado. Sem instalar nada da loja.</div>
    <div class="row mt">${standalone ? '<span class="pill ok">instalado como app</span>' : `<button class="ghost" id="pwa-install" ${installEvt ? '' : 'hidden'}>Instalar como app</button>`}
      ${!st.supported ? '<span class="mute small">Este navegador não recebe notificações.</span>'
        : st.sub ? '<span class="pill ok">notificações ligadas</span><button class="ghost small" id="push-test">Enviar um teste</button><button class="ghost small" id="push-off">Desligar</button>'
        : st.permission === 'denied' ? '<span class="pill warn">bloqueadas no navegador</span>' : '<button id="push-on">Ligar notificações</button>'}</div>
    ${standalone || installEvt ? '' : '<div class="mute small mt">Para instalar: no Chrome ou Edge, o ícone de instalar na barra de endereço; no iPhone, Compartilhar → Adicionar à Tela de Início (as notificações no iPhone exigem o app instalado).</div>'}`;
  const on = (id, fn, msg) => { const b = $(id, el); if (b) b.onclick = e => act(e.target, fn, msg).then(() => pushCard(el)); };
  on('#push-on', pushEnable, 'Notificações ligadas');
  on('#push-off', pushDisable, 'Notificações desligadas');
  on('#push-test', () => api('/me/push/test', { method: 'POST' }), 'Teste enviado');
  const ib = $('#pwa-install', el);
  if (ib) ib.onclick = async () => { if (!installEvt) return; installEvt.prompt(); await installEvt.userChoice.catch(() => null); installEvt = null; pushCard(el); };
}

/* ---------- menus por assunto (hubs): cada item do menu junta páginas que já existem, com abas em cima ----------
   As abas apontam para os endereços de sempre (#/queues, #/tests…): links e favoritos seguem valendo. O item do menu
   abre a última aba usada. need: builder | auditor | admin | instance (quem vê a aba). */
const HUBS_CONSOLE = [
  { id: 'painel', tabs: [['', 'Resumo'], ['live', 'Ao vivo']] },
  { id: 'inbox', tabs: [['inbox', 'Tudo'], ['decisions', 'Decisões', null, 'decisions'], ['approvals', 'Aprovações', null, 'pending']] },
  { id: 'issues', tabs: [['issues', 'Quadro'], ['issues/settings', 'Configurar o quadro', 'admin']] },
  { id: 'agentes', tabs: [['agents', 'Todos'], ['deployments', 'Deployments'], ['tests', 'Testes'], ['templates', 'Templates', 'builder']] },
  { id: 'employees', sub: ['workforce'], tabs: [['workforce', 'Visão geral'], ['org', 'Organograma']] },
  { id: 'trabalho', tabs: [['queues', 'Filas'], ['playbooks', 'Playbooks'], ['meetings', 'Reuniões'], ['memos', 'Memorandos de decisão'],
    ['watchers', 'Vigias'], ['tickets', 'Sistemas de chamados'], ['work', 'Comigo']] },
  { id: 'projetos', tabs: [['projects', 'Projetos'], ['goals', 'Metas'], ['files', 'Entregáveis']] },
  { id: 'capacidades', tabs: [['connectors', 'Conectores'], ['connectors/custom', 'Adicionar personalizado', 'admin'], ['catalog', 'Skills'], ['skills', 'Skill Studio'], ['plugins', 'Plugins'], ['extraction', 'Extração de documentos'],
    ['calculators', 'Calculadoras'], ['message-templates', 'Modelos de mensagem'], ['procedures', 'Procedimentos do navegador']] },
  { id: 'conhecimento', tabs: [['knowledge', 'Base de conhecimento'], ['learned', 'O que a empresa aprendeu'], ['learning', 'Trilhas']] },
  { id: 'valor', tabs: [['value', 'Valor e oportunidades'], ['usage', 'Uso e custo'], ['budgets', 'Orçamentos']] },
  { id: 'auditoria', tabs: [['audit', 'Auditoria', 'auditor'], ['governance', 'Governança de dados', 'auditor']] },
  { id: 'pessoas', tabs: [['members', 'Membros', 'admin'], ['teams', 'Times'], ['packages', 'Pacotes de time'], ['sso', 'SSO e SCIM', 'admin']] },
  { id: 'config', tabs: [['company', 'Empresa', 'admin'], ['providers', 'Provedores de LLM', 'admin'], ['secrets', 'Segredos'], ['keys', 'Chaves de API'], ['announcements', 'Avisos', 'admin'],
    ['orgs', 'Empresas', 'instance'], ['connect', 'Integrações']] },
];
const HUBS_PORTAL = [{ id: 'mine', nav: 'my-issues', tabs: [['my-issues', 'Minhas issues'], ['work', 'Meu trabalho']] }];
const HUBS = () => (USER_MODE ? HUBS_PORTAL : HUBS_CONSOLE);
const hubOk = need => !need || (need === 'admin' && ME.is_admin) || (need === 'auditor' && ME.is_auditor)
  || (need === 'builder' && (ME.can_create_agents || ME.is_admin)) || (need === 'instance' && ME.instance_admin);
const hubTabs = h => h.tabs.filter(t => hubOk(t[2]));
const hubKey = (r, a) => (r === 'issues' && a === 'settings' ? 'issues/settings' : r === 'connectors' && a === 'custom' ? 'connectors/custom' : r);
function hubOf(r, a) { const k = hubKey(r, a); return HUBS().find(h => h.tabs.some(t => t[0] === k)); }

function hubGo(id) {  // #/hub/<id>: a última aba usada (ou a primeira)
  const h = HUBS().find(x => x.id === id);
  if (!h) { location.replace('#/'); return; }
  const tabs = hubTabs(h).map(t => t[0]);
  let last = null;
  try { last = localStorage.getItem('hub_' + id); } catch (e) { /* ok */ }
  location.replace('#/' + (tabs.includes(last) ? last : tabs[0] || ''));
}

function hubNav(r, a) {  // o item do menu do assunto fica aceso em qualquer página dele
  const h = hubOf(r, a);
  if (!h) return;
  const link = document.querySelector(`nav a[data-r="${h.nav || 'hub-' + h.id}"]`);
  if (link) link.classList.add('on');
}

function hubBar(r, a) {  // as abas do assunto em cima da página (só nas páginas de lista, não nos detalhes)
  const h = hubOf(r, a);
  const k = hubKey(r, a);
  if (!h || (a && k !== 'issues/settings' && !(h.sub || []).includes(r))) return;
  const tabs = hubTabs(h);
  if (tabs.length < 2) return;
  try { localStorage.setItem('hub_' + h.id, k); } catch (e) { /* ok */ }
  const count = c => (c && ME[c] ? ` <span class="badge">${ME[c]}</span>` : '');
  main.insertAdjacentHTML('afterbegin', `<div class="hub-bar" role="navigation" aria-label="${esc(window.t('Seções'))}">${tabs.map(t =>
    `<a href="#/${t[0]}" class="${t[0] === k ? 'on' : ''}">${esc(window.t(t[1]))}${count(t[3])}</a>`).join('')}</div>`);
}
