/* Workspace do membro (portal /app): início simples, meus agentes (pessoais e que gerencio), organograma visual com
   arrastar-e-soltar, conectar a minha IA (MCP da plataforma), meus acessos — e, para o admin, a política de acesso
   dos membros. Usa os helpers de app.js ($, api, esc, act, toast, ME…). */

const ADV_KEY = 'hangar_nav_adv';
const advOn = () => { try { return localStorage.getItem(ADV_KEY) === '1'; } catch (e) { return false; } };

/* ---------- menu: o essencial, e o resto atrás de "Avançado" ---------- */
function memberNavSync(key) {
  const adv = $('#nav-adv'), btn = $('#nav-adv-btn');
  if (!adv || !btn) return;
  const inAdv = [...adv.querySelectorAll('a')].some(a => a.dataset.r === key && key !== '');
  const open = advOn() || inAdv;
  adv.hidden = !open;
  btn.textContent = window.t(open ? 'Menos opções' : 'Avançado');
  btn.setAttribute('aria-expanded', String(open));
  btn.onclick = () => { try { localStorage.setItem(ADV_KEY, advOn() ? '0' : '1'); } catch (e) { /* ok */ } memberNavSync(key); };
}

const PERSON_IC = n => `<span class="av">${esc(((n || '?')[0] || '?').toUpperCase())}</span>`;
const workingChip = w => w ? `<a class="chip work-on" href="#/tasks/${w.id}"><i class="dot-live"></i><span>trabalhando em</span> <span data-noi18n>${esc(w.title)}</span></a>` : '';
const modelChip = n => n.model ? `<span class="chip" title="${esc(n.connection || '')}">${icon('chip')}<span data-noi18n>${esc(n.model)}</span></span>` : '';
const harnessChip = h => h ? `<span class="chip">${icon('server')}<span data-noi18n>${esc(h)}</span></span>` : '';

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
    <div class="card"><div class="row between"><h2>Minhas tarefas</h2><a href="#/work" class="small">Meu trabalho →</a></div>
      ${(w.my_tasks || []).length ? `<table class="small">${w.my_tasks.map(t => `<tr><td><a href="#/tasks/${t.id}" data-noi18n>${esc(t.title)}</a></td><td>${pill(t.status)}</td><td class="mute">${when(t.created_at)}</td></tr>`).join('')}</table>`
        : '<div class="mute">Você ainda não pediu tarefas aos agentes. Peça pela página do agente ou pela sua IA conectada.</div>'}</div>
    <div class="card"><div class="row between"><h2>Meus agentes</h2><a href="#/mine" class="small">Ver todos →</a></div>
      ${(w.agents || []).length ? `<ul class="list">${w.agents.slice(0, 6).map(a => `<li><a href="#/employees/${esc(a.slug)}"><b data-noi18n>${esc(a.name)}</b></a>
        ${a.personal ? '<span class="pill info">pessoal</span>' : ''} ${empPill(a.status)}</li>`).join('')}</ul>`
        : `<div class="mute">Nenhum agente seu ainda.</div>${w.personal_allowed ? '<a href="#/employees/new?personal=1" class="small">Criar o primeiro →</a>' : ''}`}</div>
  </div>
  <div class="mute small mt"><a href="#/overview">Painel completo (agentes do hangar, chamadas e produção) →</a></div>`;
}

/* ---------- meus agentes ---------- */
const ASK_AI = () => window.LANG === 'en'
  ? 'Create a personal agent for me in Agent Hangar (hire_employee with personal=true). Ask me what it should do, which systems it uses and how much autonomy it gets, then show me the summary before hiring.'
  : 'Crie um agente pessoal para mim no Agent Hangar (hire_employee com personal=true). Pergunte o que ele deve fazer, quais sistemas usa e quanta autonomia tem, e me mostre o resumo antes de contratar.';

async function myAgentsPage() {
  const [w, chart] = await Promise.all([api('/me/workspace'), api('/org-chart').catch(() => ({ nodes: [] }))]);
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
  <h2 class="mt"><span>Do time, que eu gerencio</span> (${team.length})</h2>${team.map(row).join('') || '<div class="card empty"><div class="mute">Nenhum agente do time sob sua gestão.</div></div>'}`;
  main.querySelectorAll('.mine-promote').forEach(b => b.onclick = e => {
    const note = prompt(window.t('Por que o time deve usar este agente? (quem mantém o time decide)'));
    if (note === null) return;
    act(e.target, () => api(`/employees/${b.dataset.slug}/promote-to-team`, { method: 'POST', body: { note } }), 'Pedido enviado ao mantenedor do time');
  });
}

/* ---------- organograma visual (arrastar para mudar a quem reporta) ---------- */
async function orgVisualPage() {
  const q = qsOf();
  if (q.get('view') === 'tree') return orgChartPage();
  const team = q.get('team') || '';
  const d = await api('/org-chart' + (team ? `?team=${encodeURIComponent(team)}` : ''));
  const by = Object.fromEntries(d.nodes.map(n => [n.slug, n]));
  const kids = {};
  d.nodes.forEach(n => { const p = n.reports_to && by[n.reports_to] ? n.reports_to : ''; (kids[p] = kids[p] || []).push(n); });
  const teams = [...new Set(d.nodes.map(n => n.team).filter(Boolean))];
  const node = n => `<li><div class="oc-card ${n.personal ? 'personal' : ''} st-${esc(n.status)}" draggable="true" data-slug="${esc(n.slug)}" tabindex="0">
      <div class="row between"><a href="#/employees/${esc(n.slug)}"><b data-noi18n>${esc(n.name)}</b></a>${empPill(n.status)}</div>
      <div class="mute small" data-noi18n>${esc(n.title || '')}</div>
      <div class="oc-chips">${modelChip(n)}${harnessChip(n.harness)}<span class="chip">${esc(LEVEL_LABEL[n.level] || n.level)}</span>${n.personal ? '<span class="pill info">pessoal</span>' : ''}</div>
      ${(n.specialists || []).length ? `<div class="oc-spec small">${n.specialists.map(s => `<span title="${esc(s.harness || '')}">${icon('bot')}<span data-noi18n>${esc(s.name)}</span></span>`).join('')}</div>` : ''}
      <div class="mute small">${n.manager ? `${PERSON_IC(n.manager)} <span data-noi18n>${esc(n.manager)}</span>` : ''}</div>
      ${workingChip(n.working_on)}</div>
    ${(kids[n.slug] || []).length ? `<ul>${kids[n.slug].map(node).join('')}</ul>` : ''}</li>`;
  main.innerHTML = `<div class="row between"><div><h1>Organograma</h1><div class="sub">Arraste um agente para cima de outro para mudar a quem ele reporta. Se você não gerencia o agente, vira uma proposta para quem gerencia decidir.</div></div>
    <div class="row"><select id="oc-team" style="width:auto"><option value="">${window.t('Todos os times')}</option>${teams.map(t => `<option ${t === team ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>
      <a href="#/org?view=tree${team ? `&team=${encodeURIComponent(team)}` : ''}" class="small">Lista</a></div></div>
  ${d.nodes.length ? `<div class="oc-root" id="oc-root">Solte aqui para não reportar a ninguém</div>
    <div class="card oc-wrap"><ul class="oc-tree">${(kids[''] || []).map(node).join('')}</ul></div>`
    : '<div class="card empty"><div class="mute">Nenhum Digital employee visível.</div></div>'}`;
  $('#oc-team').onchange = () => { location.hash = '#/org' + ($('#oc-team').value ? `?team=${encodeURIComponent($('#oc-team').value)}` : ''); };
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
    $('#ma-code-out').innerHTML = `<span class="code-big" data-noi18n>${esc(r.code)}</span> <span class="mute small">válido até ${new Date(r.expires_at).toLocaleString(window.LOCALE)}</span>
      <div class="small mt">Ou abra o link no outro navegador: ${copyable(link)}</div>`;
  });
}

/* ---------- admin: acesso dos membros (política, acessos, códigos) ---------- */
async function workspaceAdminPage() {
  const [pol, ss, users] = await Promise.all([api('/workspace/policy'), api('/workspace/sessions'), api('/users').catch(() => [])]);
  const L = pol.login, P = pol.personal_agents;
  const admin = ME.is_admin;
  const byUser = {};
  ss.forEach(s => { (byUser[s.user_id] = byUser[s.user_id] || []).push(s); });
  main.innerHTML = `<h1>Acesso dos membros</h1><div class="sub">O workspace é a empresa: aqui você define como os membros entram, o que podem criar sozinhos e vê (e encerra) onde cada conta está aberta.</div>
  <div class="grid g2">
    <div class="card"><h2>Como os membros entram</h2>
      ${[['password', 'Usuário e senha'], ['sso', 'SSO (Entra ID, Google, GitHub…)'], ['activation', 'Código de ativação (chave virtual)'], ['key', 'Token pessoal na tela de login']].map(([k, v]) =>
        `<label class="row small" style="gap:6px"><input type="checkbox" class="wp-login" value="${k}" ${L[k] ? 'checked' : ''} ${admin ? '' : 'disabled'} style="width:auto">${esc(window.t(v))}</label>`).join('')}
      <div class="mute small mt">O SSO é configurado em <a href="/ui/#/sso">SSO e SCIM</a>. Pelo menos um jeito de entrar fica ligado. O MCP (chave pessoal ou OAuth) continua funcionando.</div></div>
    <div class="card"><h2>Agentes pessoais</h2>
      <label class="row small" style="gap:6px"><input type="checkbox" id="wp-pa" ${P.enabled ? 'checked' : ''} ${admin ? '' : 'disabled'} style="width:auto">Membros podem criar agentes pessoais</label>
      <div class="grid g2 mt">
        <label>Quem cria<select id="wp-who" ${admin ? '' : 'disabled'}><option value="members" ${P.who === 'members' ? 'selected' : ''}>qualquer membro</option><option value="developers" ${P.who === 'developers' ? 'selected' : ''}>developers e mantenedores</option></select></label>
        <label>Máximo por membro<input id="wp-max" type="number" min="0" max="50" value="${P.max_per_member}" ${admin ? '' : 'disabled'}></label>
        <label>Orçamento por membro / mês (US$, 0 = sem teto)<input id="wp-budget" type="number" min="0" step="1" value="${P.monthly_budget_usd}" ${admin ? '' : 'disabled'}></label>
        <label>Conexões de LLM liberadas (vírgula; vazio = todas)<input id="wp-llm" value="${esc((P.llm_connections || []).join(', '))}" ${admin ? '' : 'disabled'} data-noi18n></label></div>
      ${admin ? '<button id="wp-save" class="mt">Salvar política</button>' : ''}</div>
  </div>
  ${admin ? `<div class="card mt"><h2>Código de ativação para um membro</h2>
    <div class="mute small">Para quem ainda não tem SSO ou senha: o membro digita o código na tela de login. Uso único, 24 horas.</div>
    <div class="row mt"><select id="wp-user" style="flex:1">${users.filter(u => u.active !== false).map(u => `<option value="${esc(u.email)}" data-noi18n>${esc(u.name || u.email)} · ${esc(u.email)}</option>`).join('')}</select>
      <button id="wp-code">Gerar código</button></div><div id="wp-code-out" class="mt"></div></div>` : ''}
  <div class="card mt"><h2><span>Acessos abertos</span> (${ss.length})</h2>
    ${ss.length ? `<table><tr><th>Membro</th><th>Aparelho</th><th>Entrou com</th><th>Último uso</th><th></th></tr>
      ${Object.values(byUser).map(list => list.map((s, i) => `<tr><td>${i ? '' : `<b data-noi18n>${esc(s.name)}</b><div class="mute small" data-noi18n>${esc(s.user || '')}</div>`}</td>
        <td data-noi18n>${esc(s.device)}</td><td>${esc(window.t(METHOD_LABEL[s.method] || s.method))}</td><td>${when(s.last_seen_at)}</td>
        <td>${admin ? `<button class="ghost small wp-rev" data-id="${s.id}">Encerrar</button>${i ? '' : ` <button class="ghost small wp-rev-all" data-user="${s.user_id}">Todos deste membro</button>`}` : ''}</td></tr>`).join('')).join('')}</table>`
      : '<div class="mute">Nenhum acesso aberto.</div>'}</div>`;
  const save = $('#wp-save');
  if (save) save.onclick = e => act(e.target, () => api('/workspace/policy', { method: 'PUT', body: {
    login: Object.fromEntries([...main.querySelectorAll('.wp-login')].map(c => [c.value, c.checked])),
    personal_agents: { enabled: $('#wp-pa').checked, who: $('#wp-who').value, max_per_member: Number($('#wp-max').value || 0),
      monthly_budget_usd: Number($('#wp-budget').value || 0), llm_connections: $('#wp-llm').value.split(',').map(x => x.trim()).filter(Boolean) } } }).then(route), 'Política salva');
  const code = $('#wp-code');
  if (code) code.onclick = e => act(e.target, async () => {
    const r = await api('/workspace/activation-codes', { method: 'POST', body: { email: $('#wp-user').value } });
    $('#wp-code-out').innerHTML = `<span class="code-big" data-noi18n>${esc(r.code)}</span> <span class="mute small"><span>para</span> <span data-noi18n>${esc(r.user)}</span> · <span>válido até</span> ${new Date(r.expires_at).toLocaleString(window.LOCALE)}</span>`;
  });
  main.querySelectorAll('.wp-rev').forEach(b => b.onclick = e => act(e.target, () => api(`/workspace/sessions/${b.dataset.id}`, { method: 'DELETE' }).then(route), 'Acesso encerrado'));
  main.querySelectorAll('.wp-rev-all').forEach(b => b.onclick = e => confirm(window.t('Encerrar todos os acessos deste membro?')) &&
    act(e.target, () => api(`/workspace/users/${b.dataset.user}/sessions/revoke`, { method: 'POST' }).then(route), 'Acessos encerrados'));
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
