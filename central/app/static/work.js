/* O trabalho do dia a dia: caixa de entrada, busca, projetos, metas, orçamentos e a conversa da tarefa. */
const GOAL_STATUS = { planned: ['Planejada', ''], active: ['Ativa', 'info'], achieved: ['Atingida', 'ok'], dropped: ['Abandonada', 'bad'] };
const INBOX_VIEWS = { all: 'Tudo', unread: 'Não lidas', decisions: 'Decisões', blocked: 'Travadas', mine: 'Que eu pedi', archived: 'Arquivadas' };
const INBOX_ICON = { decision: 'approve', task: 'briefcase', budget: 'wallet', issue: 'template', llm_model: 'chip', model_suggestion: 'chip', execution_suggestion: 'chip', connector: 'plug' };
const SEARCH_KIND = { agent: 'Agentes', employee: 'Digital employees', task: 'Tarefas', issue: 'Issues', project: 'Projetos', goal: 'Metas', plugin: 'Plugins', skill: 'Skills' };
const BUDGET_SCOPE = { org: 'Empresa', team: 'Time', agent: 'Agente', project: 'Projeto' };
const BUDGET_STATE = { ok: ['ok', 'ok'], warning: ['aviso', 'warn'], stopped: ['parado', 'bad'] };
const goalPill = s => pillOf(GOAL_STATUS, s);
const scopeOf = x => `<span class="mute small">${esc(window.t(BUDGET_SCOPE[x.scope_type] || x.scope_type))}</span>${x.scope ? ` <b data-noi18n>${esc(x.scope)}</b>` : ''}`;
const qsOf = () => new URLSearchParams(location.hash.split('?')[1] || '');
const myTeams = (min = 'developer') => (ME.teams || []).filter(t => ME.is_admin || ({ consumer: 1, developer: 2, maintainer: 3 }[t.role] || 0) >= ({ consumer: 1, developer: 2, maintainer: 3 }[min]));

/* ---------- caixa de entrada ---------- */
async function inboxPage() {
  if (!ME.user) {  // sessão de emergência (token de admin): não há pessoa, então não há caixa de entrada
    main.innerHTML = `<h1>Caixa de entrada</h1><div class="card empty mt"><div class="mute">${window.t('A caixa de entrada é de uma pessoa. Você está na sessão de emergência (token de admin): entre com a sua conta para ver as suas decisões e tarefas. As aprovações da empresa ficam em')} <a href="#/approvals">${window.t('Aprovações')}</a>.</div></div>`;
    return;
  }
  const view = qsOf().get('view') || 'all';
  const box = await api('/inbox?view=' + view);
  main.innerHTML = `<div class="row between"><div><h1>Caixa de entrada</h1>
      <div class="sub">O que precisa de você: decisões, as tarefas que você pediu, o que travou nos Digital employees que você gerencia e os orçamentos parados.
        Arquivar é só seu: não conclui nem cancela nada.</div></div>
    ${box.items.some(i => i.unread) ? '<button class="ghost" id="ib-all">Marcar tudo como lido</button>' : ''}</div>
  <div class="tabs">${Object.entries(INBOX_VIEWS).map(([k, v]) => `<a href="#/inbox?view=${k}" class="${k === view ? 'on' : ''}">${v}${box.counts[k] ? ` (${box.counts[k]})` : ''}</a>`).join('')}</div>
  ${box.items.length ? `<div class="card">${box.items.map(i => `<div class="li row between ib-item ${i.unread ? 'unread' : ''}" data-k="${esc(i.key)}" data-u="${esc(i.url)}">
      <span class="row" style="gap:10px;min-width:0">${icon(INBOX_ICON[i.kind] || 'inbox')}<span style="min-width:0">
        <a href="${esc(i.url)}" class="ib-open">${i.kind === 'budget' ? `<b>${esc(window.t('Orçamento'))}:</b> ${scopeOf(i)}` : `<b data-noi18n>${esc(i.title)}</b>`}</a> ${i.unread ? '<span class="pill info">nova</span>' : ''}
        <div class="mute small">${i.kind === 'task' ? `${taskPill(i.status)} <span data-noi18n>${esc(i.employee_name || '')}</span>${i.requested ? ' · <span>você pediu</span>' : ''}`
          : i.kind === 'decision' ? `<span>Decisão</span> · <span data-noi18n>${esc(i.employee || '')}</span>${i.mine ? ' · <span>com você</span>' : ''}`
          : i.kind === 'issue' ? `<span>Issue</span> · <span data-noi18n>${esc((i.actor || '').replace(/^employee:/, ''))}</span> · <span>${esc(window.t({ comment: 'comentou', mention: 'mencionou você', status: 'mudou o status', run_done: 'terminou uma rodada', run_failed: 'a rodada falhou', run_started: 'começou uma rodada', assignee: 'mudou o responsável', created: 'criou' }[i.activity] || 'atualizou'))}</span>`
          : `<span class="pill ${i.budget_kind === 'hard_stop' ? 'bad' : 'warn'}">${i.budget_kind === 'hard_stop' ? 'parado' : 'aviso'}</span>`} · <span>${when(i.at)}</span></div></span></span>
      <span class="row" style="gap:4px">${i.unread ? '<button class="ghost small ib-read">Lida</button>' : ''}
        <button class="ghost small ib-arch">${i.archived ? 'Desarquivar' : 'Arquivar'}</button></span></div>`).join('')}</div>`
    : `<div class="card empty"><h2>Nada aqui</h2><div class="mute">${view === 'all' ? 'Tudo em dia.' : 'Nada nesta visão.'}</div></div>`}`;
  const mark = (keys, body) => api('/inbox/mark', { method: 'POST', body: { keys, ...body } });
  main.querySelectorAll('.ib-item').forEach(row => {
    const k = row.dataset.k;
    const rb = $('.ib-read', row);
    if (rb) rb.onclick = ev => act(ev.target, () => mark([k], { read: true }).then(inboxPage));
    $('.ib-arch', row).onclick = ev => act(ev.target, () => mark([k], { archived: view !== 'archived' }).then(inboxPage));
    $('.ib-open', row).onclick = () => { mark([k], { read: true }).then(refreshInboxBadge); };
  });
  const all = $('#ib-all');
  if (all) all.onclick = ev => act(ev.target, () => mark(box.items.filter(i => i.unread).map(i => i.key), { read: true }).then(inboxPage));
  refreshInboxBadge();
}

async function refreshInboxBadge() {
  const b = $('#inbox-badge');
  if (!b || !ME || !ME.user) return;
  try {
    const box = await api('/inbox?view=unread');
    b.textContent = box.counts.unread || '';
    b.hidden = !box.counts.unread;
  } catch { /* sem caixa (ex.: chave de API): fica escondido */ }
}

/* ---------- busca ---------- */
async function searchPage() {
  const q = qsOf().get('q') || '';
  main.innerHTML = `<h1>Buscar</h1><div class="sub">${USER_MODE ? 'Agentes, Digital employees, tarefas, issues e projetos — só o que você pode ver.'
    : 'Agentes, Digital employees, tarefas, projetos, metas, plugins e skills — só o que você pode ver.'}</div>
    <div class="row mt" style="gap:8px"><input id="sq" value="${esc(q)}" placeholder="o que você procura?" style="flex:1" autofocus><button id="sq-go">Buscar</button></div><div id="sr" class="mt"></div>`;
  const go = () => { const v = $('#sq').value.trim(); if (v) location.hash = '#/search?q=' + encodeURIComponent(v); };
  $('#sq-go').onclick = go;
  $('#sq').onkeydown = e => { if (e.key === 'Enter') go(); };
  if (q.length < 2) return;
  // no portal, só o que abre nele: os agentes (os dele e os do catálogo que ele vê), os Digital employees, as tarefas,
  // as issues e os projetos; metas, plugins e skills são do console
  const r = await api('/search?q=' + encodeURIComponent(q) + (USER_MODE ? '&kinds=agent,employee,task,issue,project' : ''));
  const groups = {};
  r.items.forEach(x => (groups[x.kind] = groups[x.kind] || []).push(x));
  $('#sr').innerHTML = r.items.length ? Object.entries(groups).map(([k, xs]) => `<div class="card mt"><h2>${esc(SEARCH_KIND[k] || k)} (${xs.length})</h2>
      ${xs.map(x => `<div class="li"><a href="${esc(x.url)}"><b data-noi18n>${esc(x.title)}</b></a> <span class="mute small" data-noi18n>${esc(x.sub || '')}</span>
        ${x.snippet ? `<div class="mute small" data-noi18n>${esc(x.snippet)}</div>` : ''}</div>`).join('')}</div>`).join('')
    : `<div class="card empty"><div class="mute">Nada encontrado para “<span data-noi18n>${esc(q)}</span>”.</div></div>`;
}

function bindGlobalSearch() {
  const box = $('#gsearch');
  if (!box) return;
  box.onkeydown = e => { if (e.key === 'Enter' && box.value.trim()) { location.hash = '#/search?q=' + encodeURIComponent(box.value.trim()); box.value = ''; box.blur(); } };
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); box.focus(); } });
}

/* ---------- projetos ---------- */
async function projectsPage() {
  const archived = qsOf().get('archived') === '1';
  const [list, goals] = await Promise.all([api('/projects' + (archived ? '?archived=true' : '')), api('/goals')]);
  const teams = myTeams('developer');
  main.innerHTML = `<div class="row between"><div><h1>Projetos (${list.length})</h1>
      <div class="sub">Agrupam as tarefas de um time em torno de um resultado. O Digital employee recebe o projeto, as instruções e a cadeia de metas: sabe por que a tarefa existe.</div></div>
    <a href="#/projects${archived ? '' : '?archived=1'}" class="small">${archived ? 'Esconder arquivados' : 'Mostrar arquivados'}</a></div>
  ${list.length ? `<div class="grid g3">${list.map(p => `<div class="card agent-card"><div class="row between"><a href="#/projects/${esc(p.slug)}"><b data-noi18n>${esc(p.name)}</b></a>
      ${p.status === 'archived' ? '<span class="pill">arquivado</span>' : ''}</div>
      <div class="mute small"><span data-noi18n>${esc(p.team_name || '—')}</span>${p.goal ? ` · ${icon('target')} <span data-noi18n>${esc(p.goal.title)}</span>` : ''}</div>
      ${p.description ? `<div class="small mt one-line" data-noi18n>${esc(p.description)}</div>` : ''}
      <div class="small mt"><span>${p.open}</span> <span class="mute">${window.t('abertas')}</span> · <span>${p.done}</span> <span class="mute">${window.t('concluídas')}</span> · ${usd(p.cost_usd)}</div></div>`).join('')}</div>`
    : '<div class="card empty"><div class="mute">Nenhum projeto ainda.</div></div>'}
  ${teams.length ? `<div class="card mt"><h2>Novo projeto</h2><div class="grid g2">
      <label>Nome<input id="np-name" maxlength="200"></label>
      <label>Time<select id="np-team">${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name || t.slug)}</option>`).join('')}</select></label>
      <label>Meta<select id="np-goal"><option value="">— nenhuma —</option>${goals.map(g => `<option value="${g.id}" data-team="${esc(g.team || '')}">${esc(g.title)}${g.team ? ` (${esc(g.team)})` : ''}</option>`).join('')}</select></label>
      <label>Digital employee padrão<select id="np-emp"><option value="">—</option></select></label>
      <label style="grid-column:1/-1">Descrição<input id="np-desc"></label>
      <label style="grid-column:1/-1">Instruções (entram no prompt de cada tarefa do projeto)<textarea id="np-instr" rows="3"></textarea></label></div>
    <div class="row mt"><button id="np-go">Criar projeto</button></div></div>` : ''}`;
  if (!teams.length) return;
  const emps = await api('/employees').catch(() => []);
  const fill = () => {
    const team = (ME.teams || []).find(t => t.slug === $('#np-team').value);
    $('#np-emp').innerHTML = '<option value="">—</option>' + emps.filter(e => e.status !== 'offboarded' && (!team || e.team === team.id))
      .map(e => `<option value="${esc(e.slug)}">${esc(e.name)}</option>`).join('');
  };
  fill();
  $('#np-team').onchange = fill;
  $('#np-go').onclick = ev => act(ev.target, () => api('/projects', { method: 'POST', body: { name: $('#np-name').value.trim(), team: $('#np-team').value,
    goal_id: Number($('#np-goal').value) || null, default_employee: $('#np-emp').value || null, description: $('#np-desc').value.trim(),
    instructions: $('#np-instr').value } }).then(p => { location.hash = `#/projects/${p.slug}`; }), 'Projeto criado');
}

async function projectDetail(slug) {
  const p = await api('/projects/' + slug);
  const emps = (await api('/employees').catch(() => [])).filter(e => ['probation', 'active', 'paused'].includes(e.status));
  const team = (ME.teams || []).find(t => t.slug === p.team);
  const mine = emps.filter(e => !team || e.team === team.id);
  main.innerHTML = `<a href="#/projects" class="mute small">← Projetos</a>
  <div class="row between"><div><h1 data-noi18n>${esc(p.name)}</h1><div class="sub"><span data-noi18n>${esc(p.team_name || '—')}</span> · ${p.open} <span>${window.t('abertas')}</span> ·
      ${p.done} <span>${window.t('concluídas')}</span> · ${usd(p.cost_usd)}${p.status === 'archived' ? ' · <span class="pill">arquivado</span>' : ''}</div></div>
    ${p.can_edit ? `<button class="ghost" id="pj-arch">${p.status === 'archived' ? 'Reativar' : 'Arquivar'}</button>` : ''}</div>
  ${p.goal_chain.length ? `<div class="card">${icon('target')} <b>Serve a:</b> ${p.goal_chain.map(g => `<span data-noi18n>${esc(g.title)}</span>`).join(' <span class="mute">→</span> ')}</div>` : ''}
  ${p.description ? `<div class="mute mt" data-noi18n>${esc(p.description)}</div>` : ''}
  <div class="grid g2 mt"><div class="card"><h2>Nova tarefa no projeto</h2>${p.status === 'active' && mine.length ? `
      <label>Para<select id="pt-emp">${mine.map(e => `<option value="${esc(e.slug)}" ${e.slug === p.default_employee ? 'selected' : ''}>${esc(e.name)}</option>`).join('')}</select></label>
      <label class="mt">Título<input id="pt-title" maxlength="300"></label><label class="mt">Detalhes<textarea id="pt-body" rows="4"></textarea></label>
      <div class="row mt"><button id="pt-go">Entregar</button></div>` : '<div class="mute">Sem Digital employee trabalhando neste time (ou projeto arquivado).</div>'}</div>
    <div class="card"><h2>Instruções</h2>${p.can_edit ? `<textarea id="pj-instr" rows="6">${esc(p.instructions || '')}</textarea>
      <label class="mt">Meta<select id="pj-goal"><option value="">— nenhuma —</option></select></label>
      <div class="row mt"><button class="ghost" id="pj-save">Salvar</button></div>` : `<div data-noi18n style="white-space:pre-wrap">${esc(p.instructions || '—')}</div>`}</div></div>
  <div class="card mt"><h2>Tarefas (${p.task_list.length})</h2>${p.task_list.length ? `<table><tr><th>Tarefa</th><th>Digital employee</th><th>Status</th><th>Custo</th><th></th></tr>
    ${p.task_list.map(x => `<tr><td><a href="#/tasks/${x.id}"><b data-noi18n>#${x.id} ${esc(x.title)}</b></a></td><td data-noi18n>${esc(x.employee_name)}</td>
      <td>${taskPill(x.status)}</td><td class="small">${usd(x.cost_usd)}</td><td class="mute small"><span>${when(x.created_at)}</span></td></tr>`).join('')}</table>`
    : '<div class="mute">Nenhuma tarefa ainda.</div>'}</div>`;
  const go = $('#pt-go');
  if (go) go.onclick = ev => act(ev.target, () => api(`/employees/${$('#pt-emp').value}/tasks`, { method: 'POST', body: {
    title: $('#pt-title').value.trim(), body: $('#pt-body').value.trim(), project: p.slug } }).then(r => { location.hash = `#/tasks/${r.id}`; }), 'Tarefa entregue');
  if (p.can_edit) {
    const goals = await api('/goals');
    $('#pj-goal').innerHTML += goals.filter(g => !g.team || g.team === p.team).map(g => `<option value="${g.id}" ${p.goal && p.goal.id === g.id ? 'selected' : ''}>${esc(g.title)}</option>`).join('');
    $('#pj-save').onclick = ev => act(ev.target, () => api('/projects/' + slug, { method: 'PATCH', body: { instructions: $('#pj-instr').value,
      goal_id: Number($('#pj-goal').value) || null } }).then(route), 'Salvo');
    $('#pj-arch').onclick = ev => act(ev.target, () => api('/projects/' + slug, { method: 'PATCH', body: { status: p.status === 'archived' ? 'active' : 'archived' } }).then(route));
  }
  projectMaximizer(p, slug);  // modo maximizador e supervisão (analyst.js)
}

/* ---------- metas ---------- */
async function goalsPage() {
  const goals = await api('/goals');
  const kids = {};
  goals.forEach(g => (kids[g.parent_id || 0] = kids[g.parent_id || 0] || []).push(g));
  const node = (g, depth) => `<div class="li goal-row" data-id="${g.id}" style="margin-left:${depth * 22}px"><div class="row between">
      <span>${icon('target')} <b data-noi18n>${esc(g.title)}</b> ${goalPill(g.status)} <span class="mute small">${g.team ? `<span data-noi18n>${esc(g.team_name)}</span>` : window.t('empresa')}
        ${g.owner ? ` · <span data-noi18n>${esc(g.owner)}</span>` : ''}${g.target_date ? ` · <span>até</span> ${esc(g.target_date)}` : ''}${g.projects ? ` · ${g.projects} <span>projeto(s)</span>` : ''}</span></span>
      ${g.can_edit ? `<span class="row small" style="gap:4px"><select class="g-st">${Object.entries(GOAL_STATUS).map(([k, v]) => `<option value="${k}" ${k === g.status ? 'selected' : ''}>${v[0]}</option>`).join('')}</select>
        <button class="ghost small g-del">Apagar</button></span>` : ''}</div>
      ${g.description ? `<div class="mute small" data-noi18n>${esc(g.description)}</div>` : ''}</div>
    ${(kids[g.id] || []).map(c => node(c, depth + 1)).join('')}`;
  const canCreate = ME.is_admin || myTeams('maintainer').length;
  main.innerHTML = `<h1>Metas</h1><div class="sub">O porquê do trabalho. Metas da empresa (definidas por um admin) e dos times (pelo mantenedor), em árvore. Projetos e tarefas apontam para elas, e o Digital employee recebe a cadeia de metas no prompt.</div>
  ${goals.length ? `<div class="card">${(kids[0] || []).map(g => node(g, 0)).join('')}</div>` : '<div class="card empty"><div class="mute">Nenhuma meta ainda.</div></div>'}
  ${canCreate ? `<div class="card mt"><h2>Nova meta</h2><div class="grid g2">
      <label>Título<input id="ng-title" maxlength="300"></label>
      <label>De quem<select id="ng-team">${ME.is_admin ? `<option value="">${window.t('Empresa')}</option>` : ''}${myTeams('maintainer').map(t => `<option value="${esc(t.slug)}">${esc(t.name || t.slug)}</option>`).join('')}</select></label>
      <label>Meta-pai<select id="ng-parent"><option value="">— nenhuma —</option>${goals.map(g => `<option value="${g.id}">${esc(g.title)}</option>`).join('')}</select></label>
      <label>Dono (e-mail)<input id="ng-owner" placeholder="${esc(ME.name || '')}" data-noi18n></label>
      <label>Data alvo<input id="ng-date" type="date"></label>
      <label>Descrição<input id="ng-desc"></label></div>
    <div class="row mt"><button id="ng-go">Criar meta</button></div></div>` : ''}`;
  main.querySelectorAll('.goal-row').forEach(r => {
    const id = r.dataset.id, st = $('.g-st', r), del = $('.g-del', r);
    if (st) st.onchange = () => act(st, () => api('/goals/' + id, { method: 'PATCH', body: { status: st.value } }).then(goalsPage), 'Meta atualizada');
    if (del) del.onclick = ev => confirm(window.t('Apagar esta meta?')) && act(ev.target, () => api('/goals/' + id, { method: 'DELETE' }).then(goalsPage));
  });
  const go = $('#ng-go');
  if (go) go.onclick = ev => act(ev.target, () => api('/goals', { method: 'POST', body: { title: $('#ng-title').value.trim(), team: $('#ng-team').value || null,
    parent_id: Number($('#ng-parent').value) || null, owner: $('#ng-owner').value.trim() || null, target_date: $('#ng-date').value,
    description: $('#ng-desc').value.trim() } }).then(goalsPage), 'Meta criada');
}

/* ---------- orçamentos ---------- */
async function budgetsPage() {
  const b = await api('/budgets');
  const canCreate = ME.is_admin || myTeams('maintainer').length;
  const bar = p => `<div class="bar"><span style="width:${Math.min(100, p.percent || 0)}%" class="${p.state === 'stopped' ? 'bad' : p.state === 'warning' ? 'warn' : ''}"></span></div>`;
  main.innerHTML = `<h1>Orçamentos</h1><div class="sub">Limites por empresa, time, agente (inclusive o de um Digital employee) ou projeto, por dia ou por mês. No aviso, quem responde pelo escopo é avisado;
    no limite, o trabalho para (o gateway recusa e as tarefas esperam) até alguém liberar. O custo é o registrado pela plataforma, não a fatura do provedor.</div>
  ${b.incidents.filter(i => i.status === 'open').length ? `<div class="card warn-card"><h2>Incidentes abertos</h2>${b.incidents.filter(i => i.status === 'open').map(i => `<div class="li row between">
      <span><span class="pill ${i.kind === 'hard_stop' ? 'bad' : 'warn'}">${i.kind === 'hard_stop' ? 'parado' : 'aviso'}</span> ${scopeOf(i)}
        <span class="mute small">${usd(i.spent_usd)} / ${usd(i.amount_usd)} · ${esc(i.window_key)} · <span>${when(i.opened_at)}</span></span></span>
      ${i.can_resolve ? `<button class="ghost bi-rel" data-id="${i.id}">${i.kind === 'hard_stop' ? 'Liberar o resto da janela' : 'Dispensar'}</button>` : ''}</div>`).join('')}</div>` : ''}
  <div class="card mt"><h2>Políticas (${b.policies.length})</h2>${b.policies.length ? `<table><tr><th>Escopo</th><th>Janela</th><th>Gasto</th><th></th><th>Limite</th><th>Estado</th><th></th></tr>
    ${b.policies.map(p => `<tr data-id="${p.id}"><td>${scopeOf(p)}</td>
      <td>${p.window === 'day' ? window.t('dia') : window.t('mês')}</td><td>${usd(p.spent_usd)}</td><td style="width:120px">${bar(p)}</td>
      <td>${usd(p.amount_usd)} <span class="mute small">(${window.t('aviso em')} ${p.warn_percent}%${p.hard_stop ? '' : `, ${window.t('sem parada')}`})</span></td>
      <td>${pillOf(BUDGET_STATE, p.state)}</td>
      <td>${p.can_manage ? '<button class="ghost small bp-edit">Mudar limite</button> <button class="ghost small danger bp-del">Remover</button>' : ''}</td></tr>`).join('')}</table>`
    : '<div class="mute">Nenhum orçamento definido.</div>'}</div>
  ${canCreate ? `<div class="card mt"><h2>Novo orçamento</h2><div class="grid g3">
      <label>Escopo<select id="nb-type">${ME.is_admin ? `<option value="org">${window.t('Empresa')}</option>` : ''}<option value="team">${window.t('Time')}</option>
        <option value="agent">${window.t('Agente')}</option><option value="project">${window.t('Projeto')}</option></select></label>
      <label>Qual (slug)<input id="nb-scope" list="nb-opts" data-noi18n><datalist id="nb-opts"></datalist></label>
      <label>Janela<select id="nb-win"><option value="month">${window.t('mês')}</option><option value="day">${window.t('dia')}</option></select></label>
      <label>Limite (US$)<input id="nb-amount" type="number" step="0.01" min="0.01"></label>
      <label>Aviso em (%)<input id="nb-warn" type="number" min="1" max="100" value="80"></label>
      <label class="row small" style="gap:6px;align-self:end"><input type="checkbox" id="nb-stop" checked> <span>Parar o trabalho ao atingir o limite</span></label></div>
    <div class="row mt"><button id="nb-go">Criar orçamento</button></div></div>` : ''}
  ${b.incidents.filter(i => i.status !== 'open').length ? `<div class="card mt"><h2>Histórico</h2>${b.incidents.filter(i => i.status !== 'open').slice(0, 20).map(i => `<div class="li small">
      <span class="pill">${i.kind === 'hard_stop' ? 'parado' : 'aviso'}</span> ${scopeOf(i)} · ${esc(i.window_key)} · ${usd(i.spent_usd)} / ${usd(i.amount_usd)}
      <span class="mute">— <span>liberado por</span> ${esc(i.resolved_by)} ${i.resolved_at ? `<span>${when(i.resolved_at)}</span>` : ''}${i.note ? ` · <span data-noi18n>${esc(i.note)}</span>` : ''}</span></div>`).join('')}</div>` : ''}`;
  main.querySelectorAll('.bi-rel').forEach(btn => btn.onclick = ev => {
    const note = prompt(window.t('Motivo (fica na auditoria):'));
    if (note !== null) act(ev.target, () => api(`/budgets/incidents/${btn.dataset.id}/release`, { method: 'POST', body: { note } }).then(budgetsPage), 'Liberado');
  });
  main.querySelectorAll('tr[data-id]').forEach(tr => {
    const id = tr.dataset.id, ed = $('.bp-edit', tr), del = $('.bp-del', tr);
    if (ed) ed.onclick = ev => { const v = prompt(window.t('Novo limite (US$):')); if (v) act(ev.target, () => api('/budgets/' + id, { method: 'PATCH', body: { amount_usd: Number(v) } }).then(budgetsPage), 'Limite atualizado'); };
    if (del) del.onclick = ev => confirm(window.t('Remover este orçamento?')) && act(ev.target, () => api('/budgets/' + id, { method: 'DELETE' }).then(budgetsPage));
  });
  const go = $('#nb-go');
  if (!go) return;
  const opts = async () => {
    const tp = $('#nb-type').value, dl = $('#nb-opts');
    $('#nb-scope').disabled = tp === 'org';
    const list = tp === 'team' ? myTeams('maintainer').map(t => t.slug) : tp === 'project' ? (await api('/projects')).map(p => p.slug)
      : tp === 'agent' ? (await api('/agents').catch(() => [])).map(a => a.slug) : [];
    dl.innerHTML = list.map(s => `<option value="${esc(s)}">`).join('');
  };
  $('#nb-type').onchange = opts;
  opts();
  go.onclick = ev => act(ev.target, () => api('/budgets', { method: 'POST', body: { scope_type: $('#nb-type').value, scope: $('#nb-scope').value.trim() || null,
    window: $('#nb-win').value, amount_usd: Number($('#nb-amount').value), warn_percent: Number($('#nb-warn').value), hard_stop: $('#nb-stop').checked } }).then(budgetsPage), 'Orçamento criado');
}

/* ---------- a conversa da tarefa ---------- */
function threadView(x) {
  const msgs = x.messages || [];
  const reopen = ['done', 'failed'].includes(x.status) && !x.probation;
  const closed = ['cancelled', 'expired'].includes(x.status) || (['done', 'failed'].includes(x.status) && x.probation);
  return `<div class="card mt"><h2>Conversa</h2>
    <div class="mute small">Escreva para o Digital employee. Enquanto ele trabalha, a mensagem fica na fila (dá para editar ou descartar) e entra na próxima rodada.
      ${reopen ? '<b>Esta tarefa terminou: uma mensagem a reabre.</b>' : ''}</div>
    <div class="thread mt">${msgs.map(m => m.agent ? `<div class="msg-agent"><div class="mute small">${icon('bot')} <span>Digital employee</span> · <span>${when(m.at)}</span></div>
        <details ${m === msgs[msgs.length - 1] ? 'open' : ''}><summary class="small one-line" data-noi18n>${esc(m.body.slice(0, 140))}</summary><div class="md" data-noi18n>${mdLite(m.body)}</div></details></div>`
      : `<div class="msg-human ${m.status === 'queued' ? 'queued' : ''}" data-id="${m.id}"><div class="mute small"><span data-noi18n>${esc(m.author)}</span> · <span>${when(m.at)}</span>
          ${m.status === 'queued' ? ' · <span class="pill info">na fila</span>' : ''}</div><div class="m-body" data-noi18n style="white-space:pre-wrap">${esc(m.body)}</div>
        ${m.status === 'queued' && m.author === ME.name ? '<div class="row small" style="gap:4px"><button class="ghost small th-edit">Editar</button><button class="ghost small th-drop">Descartar</button></div>' : ''}</div>`).join('')
      || '<div class="mute small">Nenhuma mensagem ainda.</div>'}</div>
    ${closed ? '' : `<div class="row mt" style="gap:8px;align-items:flex-end"><textarea id="th-body" rows="2" placeholder="${esc(window.t(reopen ? 'acompanhamento: o que mais ele deve fazer?' : 'mais contexto, uma correção, uma prioridade…'))}" style="flex:1"></textarea>
      <button id="th-send">${reopen ? 'Reabrir com esta mensagem' : 'Enviar'}</button></div>`}</div>`;
}

function bindThread(id) {
  const send = $('#th-send');
  if (send) send.onclick = ev => act(ev.target, () => api(`/tasks/${id}/messages`, { method: 'POST', body: { body: $('#th-body').value } }).then(route), 'Mensagem enviada');
  main.querySelectorAll('.msg-human[data-id]').forEach(m => {
    const mid = m.dataset.id, ed = $('.th-edit', m), dr = $('.th-drop', m);
    if (ed) ed.onclick = ev => { const v = prompt(window.t('Editar a mensagem:'), $('.m-body', m).textContent); if (v) act(ev.target, () => api(`/tasks/${id}/messages/${mid}`, { method: 'PATCH', body: { body: v } }).then(route)); };
    if (dr) dr.onclick = ev => act(ev.target, () => api(`/tasks/${id}/messages/${mid}`, { method: 'DELETE' }).then(route), 'Mensagem descartada');
  });
}

// work.js carrega antes do app.js ($, api, ME): liga a busca e o contador da caixa depois que a página carregou
window.addEventListener('load', () => {
  bindGlobalSearch();
  setTimeout(refreshInboxBadge, 1500);
  setInterval(() => { if (document.visibilityState === 'visible') refreshInboxBadge(); }, 60000);
});

/* ---------- fase B: por que parou, dependências, arquivos e o histórico do cargo ---------- */
const DIAG_STATE = { failed: 'Falhou', budget: 'Parada por orçamento', decision: 'Esperando uma decisão', colleague: 'Esperando um colega',
  dependency: 'Esperando dependências', hold: 'Pausada', retry: 'Nova tentativa marcada' };
const fmtSize = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;

function diagnosisView(x) {
  const d = x.diagnosis;
  if (!d) return '';
  const act = d.actions || [];
  return `<div class="card mt ${d.state === 'failed' || d.state === 'budget' ? 'bad-card' : 'warn-card'}"><h2>Por que parou</h2>
    <div><b>${esc(DIAG_STATE[d.state] || d.state)}</b> — <span data-noi18n>${esc(d.cause || '')}</span></div>
    ${d.owner ? `<div class="small mt"><span class="mute">Próxima ação com:</span> <span data-noi18n>${esc(d.owner)}</span></div>` : ''}
    ${d.state === 'failed' ? `<div class="small mute mt"><span>Rodadas usadas</span> ${d.runs} / ${d.limit} · <span>tentativas</span> ${d.attempts}</div>` : ''}
    ${(d.side_effects || []).length ? `<div class="small mt"><b>A última rodada pode ter feito algo fora da plataforma:</b> <span data-noi18n>${d.side_effects.map(esc).join(', ')}</span>.
      <span>Confira antes de continuar: o funcionário é avisado para não repetir.</span></div>` : ''}
    <div class="row mt" style="gap:6px">${act.includes('continue') ? `<input id="dg-note" placeholder="${esc(window.t('instrução para continuar (opcional)'))}" style="flex:1">
        <button id="dg-continue">${(d.side_effects || []).length ? 'Confirmar e continuar' : 'Continuar a tarefa'}</button>` : ''}
      ${act.includes('budgets') ? '<a href="#/budgets"><button class="ghost">Abrir orçamentos</button></a>' : ''}
      ${act.includes('decide') ? '<a href="#/decisions"><button class="ghost">Abrir decisões</button></a>' : ''}
      ${act.includes('open_child') && d.child ? `<a href="#/tasks/${d.child}"><button class="ghost">Abrir a tarefa do colega</button></a>` : ''}
      ${act.includes('resume') ? '<button class="ghost" id="dg-resume">Retomar</button>' : ''}</div></div>`;
}

function depsView(x) {
  const dp = x.dependencies || { depends_on: [], blocks: [] };
  const canAdd = ['new', 'blocked', 'on_hold'].includes(x.status);
  if (!dp.depends_on.length && !dp.blocks.length && !canAdd) return '';
  const row = (t, rm) => `<div class="li row between small"><span><a href="#/tasks/${t.id}" data-noi18n>#${t.id} ${esc(t.title)}</a> ${taskPill(t.status)}</span>
    ${rm && canAdd ? `<button class="ghost small dp-rm" data-on="${t.id}">Remover</button>` : ''}</div>`;
  return `<div class="card mt"><h2>Dependências</h2>
    ${dp.depends_on.length ? `<div class="mute small">Esta tarefa começa depois de:</div>${dp.depends_on.map(t => row(t, true)).join('')}` : ''}
    ${dp.blocks.length ? `<div class="mute small mt">Estas esperam por ela:</div>${dp.blocks.map(t => row(t, false)).join('')}` : ''}
    ${canAdd ? `<div class="row mt" style="gap:6px"><input id="dp-on" placeholder="${esc(window.t('número da tarefa'))}" style="max-width:180px" data-noi18n>
      <button class="ghost" id="dp-add">Adicionar dependência</button></div>` : ''}</div>`;
}

function filesView(x) {
  const fs = x.files || [];
  return `<div class="card mt"><h2>Arquivos</h2>
    <div class="mute small">Anexe o que o Digital employee precisa ler; os entregáveis que ele salva aparecem aqui. O mesmo nome vira uma versão nova.</div>
    ${fs.length ? `<table class="small mt"><tr><th>Arquivo</th><th>Tipo</th><th>Versão</th><th>Tamanho</th><th>Por</th><th></th></tr>
      ${fs.map(f => `<tr data-id="${f.id}"><td><b data-noi18n>${esc(f.name)}</b>${f.note ? `<div class="mute" data-noi18n>${esc(f.note)}</div>` : ''}</td>
        <td>${f.kind === 'output' ? '<span class="pill ok">entregável</span>' : '<span class="pill">anexo</span>'}</td>
        <td>v${f.version}${(f.history || []).length ? ` <details class="inline"><summary class="mute">${f.history.length} <span>antiga(s)</span></summary>
          ${f.history.map(h => `<div><a href="/api/files/${h.id}" download>v${h.version}</a> <span class="mute"><span>${when(h.at)}</span></span></div>`).join('')}</details>` : ''}</td>
        <td>${fmtSize(f.size)}</td><td class="mute" data-noi18n>${esc(f.uploaded_by)}</td>
        <td style="white-space:nowrap">${f.inline ? `<a href="/api/files/${f.id}?inline=1" target="_blank" rel="noopener">Ver</a> · ` : ''}<a href="/api/files/${f.id}" download>Baixar</a>
          · <a href="javascript:void 0" class="fl-del bad-ic">Apagar</a></td></tr>`).join('')}</table>` : '<div class="mute small mt">Nenhum arquivo ainda.</div>'}
    ${['cancelled', 'expired'].includes(x.status) ? '' : `<div class="row mt" style="gap:6px"><input type="file" id="fl-up"><input id="fl-note" placeholder="${esc(window.t('nota (opcional)'))}" style="flex:1">
      <button class="ghost" id="fl-go">Anexar</button></div>`}</div>`;
}

function bindTaskFlow(x, id) {
  const on = (sel, fn) => { const b = $(sel); if (b) b.onclick = fn; };
  on('#t-hold', ev => act(ev.target, () => api(`/tasks/${id}/hold`, { method: 'POST', body: { on: true } }).then(route), 'Tarefa pausada'));
  const resume = ev => act(ev.target, () => api(`/tasks/${id}/hold`, { method: 'POST', body: { on: false } }).then(route), 'Tarefa retomada');
  on('#t-resume', resume);
  on('#dg-resume', resume);
  on('#dg-continue', ev => act(ev.target, () => api(`/tasks/${id}/continue`, { method: 'POST', body: {
    confirm: ((x.diagnosis || {}).side_effects || []).length > 0, note: $('#dg-note').value } }).then(route), 'A tarefa voltou para a fila'));
  on('#dp-add', ev => act(ev.target, () => api(`/tasks/${id}/dependencies`, { method: 'POST', body: { on: Number(($('#dp-on').value.match(/\d+/) || [0])[0]) } }).then(route), 'Dependência adicionada'));
  main.querySelectorAll('.dp-rm').forEach(b => b.onclick = ev => act(ev.target, () => api(`/tasks/${id}/dependencies/${b.dataset.on}`, { method: 'DELETE' }).then(route)));
  main.querySelectorAll('tr[data-id] .fl-del').forEach(b => b.onclick = ev => confirm(window.t('Apagar este arquivo (esta versão)?')) &&
    act(ev.target, () => api(`/files/${b.closest('tr').dataset.id}`, { method: 'DELETE' }).then(route), 'Arquivo apagado'));
  on('#fl-go', ev => {
    const f = $('#fl-up').files[0];
    if (!f) return toast(window.t('Escolha um arquivo'), true);
    const q = `?name=${encodeURIComponent(f.name)}&note=${encodeURIComponent($('#fl-note').value)}`;
    act(ev.target, async () => {
      const r = await fetch(`/api/tasks/${id}/files${q}`, { method: 'POST', body: f, credentials: 'same-origin',
        headers: { 'Content-Type': f.type || 'application/octet-stream', 'X-CSRF-Token': (document.cookie.match(/hangar_csrf=([^;]+)/) || [])[1] || '' } });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      return route();
    }, 'Arquivo anexado');
  });
}

async function loadRevisions(slug, box) {
  const revs = await api(`/employees/${slug}/revisions`).catch(() => []);
  box.innerHTML = revs.length ? revs.slice(0, 15).map(r => `<div class="li row between small"><span><b>v${r.version}</b> <span data-noi18n>${esc(r.summary)}</span>
      <span class="mute">· <span data-noi18n>${esc(r.changed_by)}</span> · <span>${when(r.at)}</span>${r.changed.length ? ` · <span>mudou:</span> <span data-noi18n>${esc(r.changed.join(', '))}</span>` : ''}</span></span>
      ${r.current ? '<span class="pill ok">atual</span>' : `<button class="ghost small rv-go" data-v="${r.version}">Restaurar</button>`}</div>`).join('')
    : '<span class="mute small">Sem histórico ainda.</span>';
  box.querySelectorAll('.rv-go').forEach(b => b.onclick = ev => confirm(`${window.t('Restaurar a versão')} v${b.dataset.v}?`) &&
    act(ev.target, () => api(`/employees/${slug}/revisions/${b.dataset.v}/restore`, { method: 'POST' }).then(route), 'Versão restaurada'));
}

async function filesPage() {
  const qs = qsOf(), q = qs.get('q') || '', kind = qs.get('kind') || 'output';
  const list = await api(`/files?kind=${kind}${q ? `&q=${encodeURIComponent(q)}` : ''}`);
  main.innerHTML = `<h1>Entregáveis</h1><div class="sub">O que os Digital employees entregaram (e os anexos das tarefas), de todas as tarefas que você pode ver. A última versão de cada arquivo.</div>
    <div class="row mt" style="gap:8px"><input id="fq" value="${esc(q)}" placeholder="${esc(window.t('nome do arquivo'))}" style="max-width:280px" data-noi18n>
      <select id="fk" style="width:auto"><option value="output" ${kind === 'output' ? 'selected' : ''}>Entregáveis</option><option value="input" ${kind === 'input' ? 'selected' : ''}>Anexos</option></select>
      <button class="ghost" id="fgo">Filtrar</button></div>
    ${list.length ? `<div class="card mt"><table class="small"><tr><th>Arquivo</th><th>Tarefa</th><th>Digital employee</th><th>Versão</th><th>Tamanho</th><th></th></tr>
      ${list.map(f => `<tr><td><b data-noi18n>${esc(f.name)}</b></td><td><a href="#/tasks/${f.task_id}" data-noi18n>#${f.task_id} ${esc(f.task_title)}</a></td>
        <td data-noi18n>${esc(f.employee_name)}</td><td>v${f.version} <span class="mute"><span>${when(f.at)}</span></span></td><td>${fmtSize(f.size)}</td>
        <td>${f.inline ? `<a href="/api/files/${f.id}?inline=1" target="_blank" rel="noopener">Ver</a> · ` : ''}<a href="/api/files/${f.id}" download>Baixar</a></td></tr>`).join('')}</table></div>`
      : '<div class="card empty mt"><div class="mute">Nada encontrado.</div></div>'}`;
  const go = () => { location.hash = `#/files?kind=${$('#fk').value}${$('#fq').value.trim() ? `&q=${encodeURIComponent($('#fq').value.trim())}` : ''}`; };
  $('#fgo').onclick = go;
  $('#fq').onkeydown = e => { if (e.key === 'Enter') go(); };
}

/* ---------- fase C: organograma, Skill Studio, pacotes de time e cofre de segredos ---------- */
async function orgChartPage() {
  const team = qsOf().get('team') || '';
  const d = await api('/org-chart' + (team ? `?team=${encodeURIComponent(team)}` : ''));
  const kids = {}, slugs = new Set(d.nodes.map(n => n.slug));
  d.nodes.forEach(n => { const p = n.reports_to && slugs.has(n.reports_to) ? n.reports_to : ''; (kids[p] = kids[p] || []).push(n); });
  const teams = [...new Set(d.nodes.map(n => n.team).filter(Boolean))];
  const node = n => `<li><div class="org-node"><a href="#/employees/${esc(n.slug)}"><b>${esc(n.name)}</b></a> ${empPill(n.status)}
      <div class="mute small"><span data-noi18n>${esc(n.title)}</span> · <span>${esc(LEVEL_LABEL[n.level] || n.level)}</span>
        ${n.manager ? ` · <span>gestor:</span> <span data-noi18n>${esc(n.manager)}</span>` : ''}</div></div>
    ${(kids[n.slug] || []).length ? `<ul>${kids[n.slug].map(node).join('')}</ul>` : ''}</li>`;
  main.innerHTML = `<div class="row between"><div><h1>Organograma</h1><div class="sub">Quem reporta a quem entre os Digital employees. O chefe delega para quem reporta a ele, e quem reporta sobe para o chefe o que não é seu (sempre pela alçada). O gestor de cada um continua sendo uma pessoa.</div></div>
    <select id="oc-team" style="width:auto"><option value="">${window.t('Todos os times')}</option>${teams.map(t => `<option ${t === team ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></div>
  ${d.nodes.length ? `<div class="card org"><ul>${(kids[''] || []).map(node).join('')}</ul></div>
    <div class="mute small mt">Defina o chefe em <b>Configurações → Trabalho em equipe</b> de cada Digital employee.</div>`
    : '<div class="card empty"><div class="mute">Nenhum Digital employee visível.</div></div>'}`;
  $('#oc-team').onchange = () => { location.hash = '#/org' + ($('#oc-team').value ? `?team=${encodeURIComponent($('#oc-team').value)}` : ''); };
}

async function skillsPage() {
  const list = await api('/skills');
  const canCreate = ME.is_admin || myTeams('developer').length;
  main.innerHTML = `<h1>Skill Studio</h1><div class="sub">As skills da empresa: procedimentos e conhecimento que os agentes usam. Edite, teste com entradas salvas, compare versões e restaure a anterior quando uma mudança piorar o resultado.</div>
  ${list.length ? `<div class="card"><table class="small"><tr><th>Skill</th><th>Versão</th><th>Casos</th><th>Último teste</th><th>Status</th></tr>
    ${list.map(s => `<tr><td><a href="#/skills/${encodeURIComponent(s.name)}"><b data-noi18n>${esc(s.name)}</b></a><div class="mute" data-noi18n>${esc(s.description || '')}</div></td>
      <td>v${s.version}</td><td>${s.cases}</td>
      <td>${s.last_run ? `${s.last_run.passed ? '<span class="pill ok">passou</span>' : '<span class="pill bad">falhou</span>'} <span class="mute">v${s.last_run.version} · <span>${when(s.last_run.at)}</span></span>` : '<span class="mute">—</span>'}</td>
      <td>${s.status === 'deprecated' ? '<span class="pill warn">descontinuada</span>' : '<span class="pill ok">ativa</span>'}</td></tr>`).join('')}</table></div>`
    : '<div class="card empty"><div class="mute">Nenhuma skill ainda.</div></div>'}
  ${canCreate ? `<div class="card mt row between"><div><h2 style="margin:0">Nova skill</h2><div class="mute small">Faça upload de um SKILL.md ou de um pacote .zip/.skill, ou crie uma do zero.</div></div>
    <div class="cx-addw"><button id="sk-add" class="sk-dark">${icon('plus')} Adicionar</button></div></div>` : ''}`;
  const add = $('#sk-add');
  if (add) add.onclick = e => { e.stopPropagation(); skAddMenu(add); };
}

async function skillDetail(name) {
  const s = await api('/skills/' + encodeURIComponent(name));
  const ed = s.can_edit;
  const casesTxt = JSON.stringify(s.case_list || [], null, 1);
  main.innerHTML = `<a href="#/skills" class="mute small">← Skill Studio</a>
  <div class="row between"><div><h1 data-noi18n>${esc(s.name)}</h1><div class="sub">v${s.version} · ${s.status === 'deprecated' ? '<span class="pill warn">descontinuada</span>' : '<span class="pill ok">ativa</span>'}
    <span data-noi18n>${esc(s.description || '')}</span></div></div>
    ${ed ? `<button class="ghost" id="sk-dep">${s.status === 'deprecated' ? 'Reativar' : 'Descontinuar'}</button>` : ''}</div>
  <div class="grid g2"><div class="card"><h2>Conteúdo</h2>
      <label>Descrição<input id="sk-desc" value="${esc(s.description || '')}" ${ed ? '' : 'disabled'}></label>
      <label class="mt">Conteúdo<textarea id="sk-content" rows="12" class="code" data-noi18n ${ed ? '' : 'disabled'}>${esc(s.content)}</textarea></label>
      <label class="mt">Exemplos<textarea id="sk-ex" rows="4" class="code" data-noi18n ${ed ? '' : 'disabled'}>${esc(s.examples || '')}</textarea></label>
      ${ed ? `<div class="row mt" style="gap:6px"><input id="sk-note" placeholder="${esc(window.t('o que mudou (opcional)'))}" style="flex:1"><button id="sk-save">Salvar versão nova</button></div>` : ''}</div>
    <div class="card"><h2>Testes</h2><div class="mute small">Cada caso: <code class="inline">input</code> e o que esperar (<code class="inline">expect_contains</code>, <code class="inline">expect_regex</code> ou <code class="inline">judge</code>).
        Sem conexão de LLM, a rodada é mock e o conteúdo não é avaliado.</div>
      <textarea id="sk-cases" rows="8" class="code mt" data-noi18n ${ed ? '' : 'disabled'}>${esc(casesTxt)}</textarea>
      ${ed ? `<div class="row mt" style="gap:6px"><button class="ghost" id="sk-cases-save">Salvar casos</button>
        <select id="sk-conn" style="width:auto"><option value="">${window.t('mock (sem LLM)')}</option>${(s.connections || []).map(c => `<option>${esc(c)}</option>`).join('')}</select>
        <button id="sk-run">Rodar os testes</button></div>` : ''}<div id="sk-out"></div>
      ${(s.runs || []).length ? `<h3 class="mt">Rodadas</h3>${s.runs.map(r => `<div class="li small">${r.passed ? '<span class="pill ok">passou</span>' : '<span class="pill bad">falhou</span>'}
        v${r.version} · <span data-noi18n>${esc(r.connection)}</span> · <span data-noi18n>${esc(r.by)}</span> · <span>${when(r.at)}</span>
        <span class="mute">(${r.results.filter(x => x.passed).length}/${r.results.length})</span></div>`).join('')}` : ''}</div></div>
  <div class="card mt"><h2>Versões</h2>${(s.revisions || []).map(r => `<div class="li row between small"><span><b>v${r.version}</b> <span class="mute"><span data-noi18n>${esc(r.changed_by)}</span> · <span>${when(r.at)}</span>
      ${r.note ? ` · <span data-noi18n>${esc(r.note)}</span>` : ''}</span><details><summary class="mute">${window.t('ver conteúdo')}</summary><pre class="code small" data-noi18n>${esc(r.content)}</pre></details></span>
      ${ed && r.version !== s.version ? `<button class="ghost small sk-restore" data-v="${r.version}">Restaurar</button>` : r.version === s.version ? '<span class="pill ok">atual</span>' : ''}</div>`).join('')}</div>`;
  if (!ed) return;
  const n = encodeURIComponent(name);
  $('#sk-save').onclick = ev => act(ev.target, () => api('/skills/' + n, { method: 'PUT', body: { description: $('#sk-desc').value, content: $('#sk-content').value,
    examples: $('#sk-ex').value, test: s.test, note: $('#sk-note').value } }).then(route), 'Versão salva');
  $('#sk-cases-save').onclick = ev => {
    let cases;
    try { cases = JSON.parse($('#sk-cases').value || '[]'); } catch { return toast(window.t('Os casos precisam ser JSON (uma lista)'), true); }
    act(ev.target, () => api(`/skills/${n}/cases`, { method: 'PUT', body: { cases } }).then(route), 'Casos salvos');
  };
  $('#sk-run').onclick = ev => act(ev.target, () => api(`/skills/${n}/test`, { method: 'POST', body: { connection: $('#sk-conn').value || null } }).then(r => {
    $('#sk-out').innerHTML = `<div class="card mt small">${r.passed ? '<span class="pill ok">passou</span>' : '<span class="pill bad">falhou</span>'}${r.mock ? ' <span class="pill">mock</span>' : ''}
      ${r.results.map(x => `<div class="li">${x.passed ? icon('check', 'ok-ic') : icon('x', 'bad-ic')} <b data-noi18n>${esc(x.name)}</b><div class="mute" data-noi18n>${esc(x.detail)}</div></div>`).join('')}</div>`;
  }));
  $('#sk-dep').onclick = ev => act(ev.target, () => api(`/skills/${n}/status`, { method: 'POST', body: { status: s.status === 'deprecated' ? 'active' : 'deprecated' } }).then(route));
  main.querySelectorAll('.sk-restore').forEach(b => b.onclick = ev => confirm(`${window.t('Restaurar a versão')} v${b.dataset.v}?`) &&
    act(ev.target, () => api(`/skills/${n}/restore/${b.dataset.v}`, { method: 'POST' }).then(route), 'Versão restaurada'));
}

async function packagesPage() {
  const cat = await api('/packages/catalog');
  const mteams = myTeams('maintainer'), dteams = myTeams('developer');
  const teamSel = (id, teams) => `<select id="${id}" style="width:auto">${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name || t.slug)}</option>`).join('')}</select>`;
  main.innerHTML = `<h1>Pacotes de time</h1><div class="sub">Leve a configuração de um time para outro, ou comece de um time pronto: cargos, alçada, rotinas, organograma, skills, metas e projetos.
    Segredos, instalações de plugins e orçamentos não vão no pacote. Os Digital employees chegam em rascunho e passam pela experiência e pela admissão.</div>
  <h2 class="mt">Times prontos</h2>${cat.length ? `<div class="grid g2">${cat.map(c => `<div class="card"><b data-noi18n>${esc(c.name)}</b><div class="mute small" data-noi18n>${esc(c.description || '')}</div>
      <div class="small mt">${c.employees.map(e => `<div>${icon('bot')} <span data-noi18n>${esc(e.name)}</span> <span class="mute" data-noi18n>— ${esc(e.title)}${e.reports_to ? ` (→ ${esc(e.reports_to)})` : ''}</span></div>`).join('')}</div>
      ${c.projects.length ? `<div class="small mute mt">${icon('folder')} <span data-noi18n>${c.projects.map(esc).join(', ')}</span></div>` : ''}
      ${mteams.length ? `<div class="row mt" style="gap:6px"><button class="ghost pk-cat" data-id="${esc(c.id)}">Instalar</button></div>` : ''}</div>`).join('')}</div>`
    : '<div class="card mute">Nenhum time pronto nesta instalação.</div>'}
  ${mteams.length ? `<div class="card mt"><h2>Importar um pacote</h2><div class="mute small">Cole o pacote (JSON ou YAML) ou escolha o arquivo. Primeiro vem a prévia; nada é criado sem confirmar.</div>
      <textarea id="pk-doc" rows="6" class="code mt" data-noi18n placeholder="kind: hangar-package"></textarea>
      <div class="row mt" style="gap:6px"><input type="file" id="pk-file" accept=".json,.yaml,.yml"></div>
      <div class="grid g2 mt"><label>Para o time${teamSel('pk-team', mteams)}</label><label>Gestor dos Digital employees (e-mail)<input id="pk-mgr" value="${esc(ME.name || '')}" data-noi18n></label></div>
      <div class="row mt"><button id="pk-preview">Ver a prévia</button></div><div id="pk-out"></div></div>` : ''}
  ${dteams.length ? `<div class="card mt"><h2>Exportar um time</h2><div class="row" style="gap:6px">${teamSel('px-team', dteams)}<button class="ghost" id="px-go">Baixar o pacote</button></div></div>` : ''}`;
  let source = null;
  const preview = (body) => act($('#pk-preview') || document.body, () => api('/packages/preview', { method: 'POST', body }).then(pv => {
    $('#pk-out').innerHTML = `<div class="card mt ${pv.ok ? '' : 'bad-card'}"><b data-noi18n>${esc(pv.name || '')}</b> → <span data-noi18n>${esc(pv.team)}</span>
      <div class="small mt">${pv.employees.map(e => `<div>${icon('bot')} <span data-noi18n>${esc(e.slug)}</span> <span class="mute" data-noi18n>${esc(e.title)}</span>${e.renamed ? ` <span class="pill">${window.t('renomeado')}</span>` : ''}</div>`).join('')}</div>
      ${pv.skills.length ? `<div class="small mt"><span class="mute">Skills:</span> ${pv.skills.map(s => `<span data-noi18n>${esc(s.name)}</span>${s.exists ? ` <span class="mute">(${window.t('já existe: reaproveitada')})</span>` : ''}`).join(', ')}</div>` : ''}
      ${pv.goals.length ? `<div class="small"><span class="mute">Metas:</span> <span data-noi18n>${pv.goals.map(esc).join(', ')}</span></div>` : ''}
      ${pv.problems.length ? `<div class="small mt bad-ic">${pv.problems.map(esc).join('<br>')}</div>` : ''}
      ${pv.ok ? '<div class="row mt"><button id="pk-install">Instalar</button></div>' : ''}</div>`;
    const ins = $('#pk-install');
    if (ins) ins.onclick = ev => act(ev.target, () => api('/packages/install', { method: 'POST', body }).then(r => {
      toast(`${r.created.employees.length} ${window.t('Digital employee(s) criados em rascunho')}`);
      location.hash = '#/employees';
    }));
  }));
  main.querySelectorAll('.pk-cat').forEach(b => b.onclick = () => {
    source = { catalog: b.dataset.id };
    if (!$('#pk-out')) return;
    $('#pk-doc').value = '';
    preview({ ...source, team: $('#pk-team').value, manager: $('#pk-mgr').value.trim() });
    $('#pk-out').scrollIntoView({ behavior: 'smooth' });
  });
  const file = $('#pk-file');
  if (file) file.onchange = async () => { const f = file.files[0]; if (f) $('#pk-doc').value = await f.text(); };
  const pv = $('#pk-preview');
  if (pv) pv.onclick = () => {
    const doc = $('#pk-doc').value.trim();
    source = doc ? { package: doc } : source;
    if (!source) return toast(window.t('Cole um pacote ou escolha um time pronto'), true);
    preview({ ...source, team: $('#pk-team').value, manager: $('#pk-mgr').value.trim() });
  };
  const px = $('#px-go');
  if (px) px.onclick = ev => act(ev.target, () => api('/packages/export', { method: 'POST', body: { team: $('#px-team').value } }).then(pkg => {
    const blob = new Blob([JSON.stringify(pkg, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${$('#px-team').value}.hangar-package.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }), 'Pacote exportado');
}

async function secretsPage() {
  const d = await api('/secrets');
  const mteams = myTeams('maintainer');
  const canManage = ME.is_admin || mteams.length;
  main.innerHTML = `<h1>Segredos</h1><div class="sub">Credenciais guardadas num lugar só e referenciadas como <code class="inline">secret://nome</code> (por enquanto, na credencial e nas configurações secretas das instalações de plugins).
    O valor é criptografado e nunca volta: trocar aqui vale na hora em todo lugar que usa. Cada uso fica contado.</div>
  <div class="card"><h2>Cofre (${d.secrets.length})</h2>${d.secrets.length ? `<table class="small"><tr><th>Nome</th><th>Escopo</th><th>Versão</th><th>Usos</th><th>Último uso</th><th></th></tr>
    ${d.secrets.map(s => `<tr data-id="${s.id}" data-name="${esc(s.name)}" data-team="${esc(s.team || '')}"><td><code class="inline" data-noi18n>${esc(s.ref)}</code>${s.description ? `<div class="mute" data-noi18n>${esc(s.description)}</div>` : ''}</td>
      <td>${s.team ? `<span data-noi18n>${esc(s.team)}</span>` : window.t('empresa')}</td><td>v${s.version} <span class="mute"><span data-noi18n>${esc(s.updated_by)}</span></span></td>
      <td>${s.uses}</td><td class="mute">${s.last_used_at ? `<span>${when(s.last_used_at)}</span> · <span data-noi18n>${esc(s.last_used_by)}</span>` : '—'}</td>
      <td>${s.can_manage ? '<button class="ghost small sc-rot">Trocar valor</button> <button class="ghost small danger sc-del">Apagar</button>' : ''}</td></tr>`).join('')}</table>`
    : '<div class="mute">Nenhum segredo ainda.</div>'}</div>
  <div class="grid g2 mt">${canManage ? `<div class="card"><h2>Novo segredo</h2>
      <label>Nome<input id="sc-name" placeholder="ex.: erp-api-key" data-noi18n></label>
      <label class="mt">De quem<select id="sc-team">${ME.is_admin ? `<option value="">${window.t('Empresa')}</option>` : ''}${mteams.map(t => `<option value="${esc(t.slug)}">${esc(t.name || t.slug)}</option>`).join('')}</select></label>
      <label class="mt">Valor<input id="sc-value" type="password" autocomplete="off"></label><label class="mt">Descrição<input id="sc-desc"></label>
      <div class="row mt"><button id="sc-go">Guardar</button></div></div>` : ''}
    <div class="card"><h2>Propor um valor</h2><div class="mute small">Sem permissão para o cofre? Proponha (por exemplo, a chave nova de um sistema): quem administra aprova sem ver o valor antigo.</div>
      <label class="mt">Nome<input id="sp-name" data-noi18n></label>
      <label class="mt">Time<select id="sp-team"><option value="">${window.t('Empresa')}</option>${(ME.teams || []).map(t => `<option value="${esc(t.slug)}">${esc(t.name || t.slug)}</option>`).join('')}</select></label>
      <label class="mt">Valor<input id="sp-value" type="password" autocomplete="off"></label><label class="mt">Nota<input id="sp-note"></label>
      <div class="row mt"><button class="ghost" id="sp-go">Propor</button></div></div></div>
  ${d.proposals.length ? `<div class="card mt"><h2>Propostas</h2>${d.proposals.map(p => `<div class="li row between small" data-id="${p.id}"><span><code class="inline" data-noi18n>${esc(p.name)}</code>
      ${p.team ? `<span data-noi18n>${esc(p.team)}</span>` : window.t('empresa')} · <span data-noi18n>${esc(p.proposed_by)}</span> · <span>${when(p.created_at)}</span>
      ${p.note ? ` · <span data-noi18n>${esc(p.note)}</span>` : ''} <span class="pill ${p.status === 'approved' ? 'ok' : p.status === 'rejected' ? 'bad' : 'warn'}">${esc(window.t(p.status === 'approved' ? 'aprovada' : p.status === 'rejected' ? 'recusada' : 'aberta'))}</span></span>
      ${p.can_decide ? '<span class="row" style="gap:4px"><button class="small sp-yes">Aprovar</button><button class="ghost small danger sp-no">Recusar</button></span>' : ''}</div>`).join('')}</div>` : ''}`;
  const go = $('#sc-go');
  if (go) go.onclick = ev => act(ev.target, () => api('/secrets', { method: 'PUT', body: { name: $('#sc-name').value.trim(), team: $('#sc-team').value || null,
    value: $('#sc-value').value, description: $('#sc-desc').value } }).then(secretsPage), 'Segredo guardado');
  $('#sp-go').onclick = ev => act(ev.target, () => api('/secrets/proposals', { method: 'POST', body: { name: $('#sp-name').value.trim(), team: $('#sp-team').value || null,
    value: $('#sp-value').value, note: $('#sp-note').value } }).then(secretsPage), 'Proposta enviada');
  main.querySelectorAll('tr[data-id]').forEach(tr => {
    const rot = $('.sc-rot', tr), del = $('.sc-del', tr);
    if (rot) rot.onclick = ev => { const v = prompt(window.t('Novo valor:')); if (v) act(ev.target, () => api('/secrets', { method: 'PUT', body: { name: tr.dataset.name, team: tr.dataset.team || null, value: v } }).then(secretsPage), 'Valor trocado'); };
    if (del) del.onclick = ev => confirm(window.t('Apagar este segredo? O que o usa deixa de funcionar.')) && act(ev.target, () => api('/secrets/' + tr.dataset.id, { method: 'DELETE' }).then(secretsPage));
  });
  main.querySelectorAll('.li[data-id]').forEach(li => {
    const y = $('.sp-yes', li), n = $('.sp-no', li);
    if (y) y.onclick = ev => act(ev.target, () => api(`/secrets/proposals/${li.dataset.id}/decide`, { method: 'POST', body: { approve: true } }).then(secretsPage), 'Proposta aprovada');
    if (n) n.onclick = ev => act(ev.target, () => api(`/secrets/proposals/${li.dataset.id}/decide`, { method: 'POST', body: { approve: false } }).then(secretsPage));
  });
}

/* ---------- empresas (multiempresa: quem administra a instalação) ---------- */
async function orgsPage() {
  const list = await api('/orgs');
  main.innerHTML = `<h1>Empresas</h1><div class="sub">Cada empresa tem as próprias pessoas, times, agentes, Digital employees, plugins, segredos e auditoria — ninguém de uma vê nada da outra. Para agir dentro de uma empresa, escolha-a no seletor da barra lateral.</div>
  <div class="card"><table class="small"><tr><th>Empresa</th><th>Domínios (SSO)</th><th>Pessoas</th><th>Times</th><th>Agentes</th><th>Digital employees</th><th>Status</th><th></th></tr>
  ${list.map(o => `<tr data-id="${o.id}" data-status="${esc(o.status)}"><td><b data-noi18n>${esc(o.name)}</b><div class="mute" data-noi18n>${esc(o.slug)}${o.id === ME.org.id ? ` · ${window.t('você está aqui')}` : ''}</div></td>
    <td><input class="og-doms" value="${esc((o.domains || []).join(', '))}" placeholder="ex.: acme.com" data-noi18n></td>
    <td>${o.users}</td><td>${o.teams}</td><td>${o.agents}</td><td>${o.employees}</td>
    <td><span class="pill ${o.status === 'suspended' ? 'bad' : 'ok'}">${esc(window.t(o.status === 'suspended' ? 'suspensa' : 'ativa'))}</span></td>
    <td class="row" style="gap:4px"><button class="ghost small og-save">Salvar domínios</button>${o.id === 1 ? '' : `<button class="ghost small ${o.status === 'suspended' ? '' : 'danger'} og-status">${o.status === 'suspended' ? 'Reativar' : 'Suspender'}</button>`}</td></tr>`).join('')}</table></div>
  <div class="card mt"><h2>Nova empresa</h2><div class="mute small">Nasce com um time “Geral” e a primeira pessoa admin. Ela entra pelo SSO (as pessoas dos domínios informados entram nesta empresa) ou com usuário e senha locais, se informados.</div>
    <div class="grid g2 mt"><label>Nome<input id="og-name"></label><label>Slug (opcional)<input id="og-slug" data-noi18n></label>
      <label>E-mail da pessoa admin<input id="og-email" type="email" data-noi18n></label><label>Nome da pessoa admin<input id="og-aname"></label>
      <label>Usuário local (opcional)<input id="og-user" autocomplete="off" data-noi18n></label><label>Senha local (opcional)<input id="og-pw" type="password" autocomplete="new-password"></label>
      <label>Domínios de e-mail (separados por vírgula)<input id="og-doms" placeholder="ex.: acme.com" data-noi18n></label></div>
    <div class="row mt"><button id="og-go">Criar empresa</button></div></div>`;
  const doms = v => v.split(',').map(x => x.trim()).filter(Boolean);
  const reload = async () => { ME = await api('/me'); renderSide(); return orgsPage(); };
  $('#og-go').onclick = ev => act(ev.target, () => api('/orgs', { method: 'POST', body: { name: $('#og-name').value.trim(), slug: $('#og-slug').value.trim(),
    admin_email: $('#og-email').value.trim(), admin_name: $('#og-aname').value.trim(), admin_username: $('#og-user').value.trim(),
    admin_password: $('#og-pw').value, domains: doms($('#og-doms').value) } }).then(reload), 'Empresa criada');
  main.querySelectorAll('tr[data-id]').forEach(tr => {
    const id = tr.dataset.id, st = $('.og-status', tr);
    $('.og-save', tr).onclick = ev => act(ev.target, () => api('/orgs/' + id, { method: 'PATCH', body: { domains: doms($('.og-doms', tr).value) } }).then(orgsPage), 'Domínios salvos');
    if (st) st.onclick = ev => {
      const suspend = tr.dataset.status !== 'suspended';
      if (suspend && !confirm(window.t('Suspender esta empresa? As pessoas dela deixam de entrar até você reativar.'))) return;
      act(ev.target, () => api('/orgs/' + id, { method: 'PATCH', body: { status: suspend ? 'suspended' : 'active' } }).then(orgsPage));
    };
  });
}
