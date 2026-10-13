/* Login, empresa, times, usuários, aprovações, acesso a agentes e SSO/SCIM. Usa os helpers de app.js
   ($, api, esc, toast, act, ME…), que só são chamados depois que os dois scripts carregaram. */

const ROLE_LABEL = { admin: 'Admin', auditor: 'Auditor', member: 'Membro', maintainer: 'Mantenedor', developer: 'Developer', consumer: 'Consumer' };
const ROLE_HELP = {
  maintainer: 'gerencia membros, aprova produção e pedidos de acesso',
  developer: 'cria, edita e testa os agentes do time',
  consumer: 'usa os agentes do time (conectar, playground, uso)',
};
const providerIcon = id => BRAND[id] || icon(id === 'oauth2' ? 'key' : 'login');

/* ---------- boot e login ---------- */
async function boot() {
  if (location.hash.startsWith('#/login')) return showLogin();
  try { ME = await api('/me'); }
  catch (e) { if (!(e instanceof AuthError)) main.innerHTML = `<div class="card"><h2>Erro</h2>${esc(e.message)}</div>`; return; }
  // o console /ui é só de admins: os demais usam o portal /app (o servidor também redireciona)
  if (window.HANGAR_MODE !== 'user' && !ME.is_admin) { location.replace('/app/?v=' + Date.now() + location.hash); return; }
  document.body.classList.remove('login-mode');
  $('#login').hidden = true;
  renderSide();
  route();
}

function renderSide() {
  const ok = need => !need || (need === 'admin' && ME.is_admin) || (need === 'auditor' && ME.is_auditor) || (need === 'builder' && (ME.can_create_agents || ME.is_admin)) || (need === 'instance' && ME.instance_admin);
  document.querySelectorAll('nav a').forEach(a => { a.hidden = !ok(a.dataset.need); });
  const badge = $('#appr-badge'); if (badge) { badge.textContent = ME.pending || ''; badge.hidden = !ME.pending; }
  const dec = $('#dec-badge'); if (dec) { dec.textContent = ME.decisions || ''; dec.hidden = !ME.decisions; }
  const u = ME.user;
  const role = ME.is_admin ? 'Admin' : ME.is_auditor ? 'Auditor' : (ME.teams[0] ? `${ROLE_LABEL[ME.teams[0].role]} · ${ME.teams[0].name}` : 'Membro');
  $('#whoami').innerHTML = `<div class="who">${u && u.avatar_url ? `<img src="${esc(u.avatar_url)}" alt="" referrerpolicy="no-referrer">` : `<span class="av">${esc((u ? (u.name || u.email) : 'A')[0].toUpperCase())}</span>`}
    <div><div class="small"><b>${esc(u ? (u.name || u.email) : 'Sessão de emergência')}</b></div><div class="mute small">${esc(role)}</div>
    ${u && u.has_password ? '<a href="#" id="pw-change" class="small">Trocar senha</a>' : ''}</div></div>
    ${!USER_MODE && ME.instance_admin && (ME.orgs || []).length > 1 ? `<label class="small org-switch">Empresa<select id="org-switch">${ME.orgs.map(o =>
      `<option value="${o.id}" ${o.id === ME.org.id ? 'selected' : ''} data-noi18n>${esc(o.name)}</option>`).join('')}</select></label>` : ''}`;
  uxSide();  // favoritos e avisos
  const sw = $('#org-switch');  // quem administra a instalação age dentro da empresa escolhida (cookie lido pelo servidor)
  if (sw) sw.onchange = () => { document.cookie = `hangar_org=${sw.value}; path=/; SameSite=Lax`; location.hash = '#/'; location.reload(); };
  const pc = $('#pw-change');
  if (pc) pc.onclick = async e => {
    e.preventDefault();
    const current = prompt('Senha atual:'); if (current === null) return;
    const next = prompt('Nova senha (mínimo 8 caracteres):'); if (!next) return;
    if (prompt('Repita a nova senha:') !== next) return toast('As senhas não conferem', true);
    try { await api('/me/password', { method: 'POST', body: { current, new: next } }); toast('Senha alterada'); } catch (err) { toast(err.message, true); }
  };
}

async function showLogin() {
  ME = null;
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  const err = params.get('error');
  const next = location.hash.startsWith('#/login') ? '#/' : (location.hash || '#/');
  const { providers, password_login: pwLogin } = await fetch('/api/auth/providers').then(r => r.json()).catch(() => ({ providers: [], password_login: false }));
  document.body.classList.add('login-mode');
  const box = $('#login');
  box.hidden = false;
  box.innerHTML = `<div class="login-art"><div class="brand">${icon('logo')}<span>Agent Hangar</span></div>
    <p>Os agentes de IA da empresa, do hangar à produção.</p>
    <small>Construa, teste e publique agentes com segurança — cada time com os seus.</small></div>
    <div class="login-side"><div class="login-card">
    <div class="brand">${icon('logo')}<span>Agent Hangar</span></div>
    <h1>Entrar</h1><div class="sub">Use a conta da empresa.</div>
    ${err ? `<div class="card bad-card small">${esc(err)}</div>` : ''}
    ${pwLogin ? `<form id="lg-pw" class="pw-form"><label>Usuário ou e-mail<input id="lg-user" autocomplete="username" autofocus></label>
      <label class="mt">Senha<input id="lg-pass" type="password" autocomplete="current-password"></label>
      <button class="mt" type="submit" style="width:100%">Entrar</button></form>${providers.length ? '<div class="or"><span>ou</span></div>' : ''}` : ''}
    <div class="sso-list">${providers.map(p => `<a class="sso-btn" href="/api/auth/login/${encodeURIComponent(p.id)}?next=${encodeURIComponent(next)}">${providerIcon(p.id)}Entrar com ${esc(p.label)}</a>`).join('')
      || (pwLogin ? '' : '<div class="mute small">Nenhum login corporativo configurado ainda. Entre com o token de admin e configure em <b>SSO e SCIM</b>.</div>')}</div>
    <details class="mt" id="lg-act-box" ${params.get('code') ? 'open' : ''}><summary>Entrar com código de ativação</summary>
      <div class="row mt"><input id="lg-code" placeholder="XXXX-XXXX-XXXX" style="flex:1" autocomplete="one-time-code" value="${esc(params.get('code') || '')}"><button id="lg-act">Entrar</button></div>
      <div class="mute small mt">O código vem do admin do workspace ou de outro navegador seu (Meus acessos). Vale uma vez, por 24 horas.</div></details>
    <details class="mt" ${providers.length || pwLogin ? '' : 'open'}><summary>Entrar com token (emergência, chave admin ou token pessoal)</summary>
      <div class="row mt"><input id="lg-token" type="password" placeholder="ADMIN_TOKEN ou ah_…" style="flex:1" autocomplete="off"><button id="lg-go">Entrar</button></div>
      <div class="mute small mt">O token vira uma sessão (cookie seguro) e não fica salvo no navegador.</div></details></div></div>`;
  const go = async () => {
    const r = await fetch('/api/auth/token', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: $('#lg-token').value.trim() }) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); toast(d.detail || 'Token inválido', true); return; }
    location.hash = next; location.reload();
  };
  $('#lg-go').onclick = go;
  const activate = async () => {
    const r = await fetch('/api/auth/activate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: $('#lg-code').value.trim() }) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); toast(d.detail || 'Código inválido', true); return; }
    location.hash = next; location.reload();
  };
  $('#lg-act').onclick = activate;
  if (params.get('code')) {  // link do convite (ou de Meus acessos): entra direto, e o código sai do endereço
    history.replaceState(null, '', location.pathname + location.search + '#/login');
    activate();
  }
  $('#lg-code').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); activate(); } };
  $('#lg-token').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); go(); } };
  const pf = $('#lg-pw');
  if (pf) pf.onsubmit = async e => {
    e.preventDefault();
    const r = await fetch('/api/auth/password', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('#lg-user').value.trim(), password: $('#lg-pass').value }) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); toast(d.detail || 'Usuário ou senha inválidos', true); $('#lg-pass').value = ''; return; }
    location.hash = next; location.reload();
  };
}

/* ---------- agente: visão de quem não é do time ---------- */
async function overviewLimited(t, a) {
  const mineReq = a.access === 'viewer' ? ((await api('/approvals').catch(() => ({ my_access: [] }))).my_access || []).find(r => r.agent === a.slug && r.status === 'pending') : null;
  const useCard = a.access === 'viewer'
    ? `<div class="card hero"><h2>Quer usar este agente?</h2><div>Ele é do time <b>${esc(a.team ? a.team.name : '—')}</b>. ${mineReq ? `Seu pedido de ${when(mineReq.created_at)} está <b>aguardando</b> o mantenedor do time.` : 'Peça acesso: um mantenedor do time aprova, e aí você conecta o agente nas suas ferramentas (aba Conectar).'}</div>
       ${mineReq ? '' : '<button class="mt" id="ov-req">Solicitar acesso</button>'}</div>`
    : `<div class="card hero"><h2>Pronto para usar</h2><div>Conecte nas suas ferramentas pela aba <a href="#/agents/${a.slug}/connect">Conectar</a> (Claude, Codex, OpenCode, LibreChat, Open WebUI…) ou teste no <a href="#/agents/${a.slug}/playground">Playground</a>.</div></div>`;
  t.innerHTML = `${useCard}<div class="grid g2 mt">
    <div class="card"><h2>Registro</h2><dl class="kv">
      <dt>Slug</dt><dd>${copyable(a.slug)}</dd><dt>Saída final</dt><dd>${esc(a.final_output)}</dd>
      <dt>Time</dt><dd>${esc(a.team ? a.team.name : '—')}</dd><dt>Contato</dt><dd>${esc(a.owner) || '—'}</dd>
      <dt>Visibilidade</dt><dd>${visPill(a.visibility)}</dd><dt>Versão</dt><dd>v${a.version}</dd>
      <dt>Produção</dt><dd>${a.prod ? '<span class="pill ok">no ar</span>' : '<span class="mute">fora do ar</span>'}</dd>
      <dt>Último teste</dt><dd>${a.last_test ? `${pill(a.last_test.status)} <span class="mute small">${esc(a.last_test.summary)}</span>` : '—'}</dd></dl></div>
    <div class="card"><h2>Como funciona</h2><div class="mute small">Instruções, spec, versões e logs são visíveis só para o time dono${a.expose_spec ? '' : ' (o time pode liberar a spec em Acesso → “expor spec”)'}.</div>
      ${can(a, 'consume') ? `<h3>Endpoints</h3>${Object.entries(a.endpoints || {}).map(([k, v]) => `<div class="small"><b>${k}</b> ${copyable(v)}</div>`).join('')}` : ''}</div></div>`;
  const b = $('#ov-req'); if (b) b.onclick = () => requestAccessDialog(a.slug);
}

/* ---------- agente: aba Acesso (mantenedor/admin) ---------- */
async function agentAccess(t, a) {
  const [grants, teamList] = await Promise.all([api(`/agents/${a.slug}/grants`), api('/teams')]);
  const targets = teamList.filter(x => ME.is_admin || x.my_role === 'maintainer');
  t.innerHTML = `<div class="grid g2">
    <div class="card"><h2>Quem pode ver e usar</h2>
      <label>Visibilidade<select id="ac-vis">${Object.entries({ private: 'só o time vê e usa', org: 'aparece no catálogo da empresa; uso sob pedido aprovado', open: 'qualquer pessoa da empresa usa direto' })
        .map(([k, v]) => `<option value="${k}" ${a.visibility === k ? 'selected' : ''}>${VIS[k]} — ${v}</option>`).join('')}</select></label>
      <label class="row small mt"><input type="checkbox" id="ac-spec" style="width:auto" ${a.expose_spec ? 'checked' : ''}> Expor instruções e spec (somente leitura) para quem não é do time</label>
      <label class="mt">Time dono<select id="ac-team">${targets.map(x => `<option value="${esc(x.slug)}" ${a.team && a.team.slug === x.slug ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label>
      <div class="mute small mt">Ao restringir o acesso, as chaves de quem perdeu permissão são revogadas na hora.</div>
      <button id="ac-save" class="mt">Salvar</button></div>
    <div class="card"><h2>Pedidos e acessos concedidos (${grants.length})</h2>
      ${grants.length ? `<table><tr><th>Pessoa</th><th>Motivo</th><th>Status</th><th></th></tr>${grants.map(g => `<tr><td><b>${esc(g.user_name || g.user)}</b><div class="mute small">${esc(g.user)}</div></td>
        <td class="small">${esc(g.reason) || '<span class="mute">—</span>'}</td><td>${g.status === 'pending' ? '<span class="pill warn">pendente</span>' : '<span class="pill ok">liberado</span>'}<div class="mute small">${when(g.created_at)}</div></td>
        <td>${g.status === 'pending' ? `<button class="gr-ok" data-id="${g.id}">Aprovar</button> <button class="ghost gr-no" data-id="${g.id}">Recusar</button>` : `<button class="ghost gr-rev" data-id="${g.id}">Revogar</button>`}</td></tr>`).join('')}</table>`
        : '<div class="mute">Nenhum pedido. Com visibilidade “empresa”, pessoas de outros times pedem acesso pelo catálogo.</div>'}</div></div>`;
  $('#ac-save').onclick = e => act(e.target, () => api(`/agents/${a.slug}/access`, { method: 'PATCH', body: { visibility: $('#ac-vis').value, expose_spec: $('#ac-spec').checked, team: $('#ac-team').value } }).then(route), 'Acesso atualizado');
  const decide = (sel, verb, msg) => t.querySelectorAll(sel).forEach(b => b.onclick = e => act(e.target, () => api(`/access-requests/${b.dataset.id}/${verb}`, { method: 'POST' }).then(route), msg));
  decide('.gr-ok', 'approve', 'Acesso liberado'); decide('.gr-no', 'reject', 'Pedido recusado');
  t.querySelectorAll('.gr-rev').forEach(b => b.onclick = e => confirm('Revogar o acesso? As chaves dessa pessoa para este agente param na hora.') &&
    act(e.target, () => api(`/access-requests/${b.dataset.id}/revoke`, { method: 'POST' }).then(route), 'Acesso revogado'));
}

/* ---------- aprovações ---------- */
const REQ_STATUS = { pending: ['pendente', 'warn'], approved: ['aprovado', 'ok'], rejected: ['recusado', 'bad'], revoked: ['revogado', ''], superseded: ['sem efeito (nova versão)', ''] };
const reqPill = s => { const [t, c] = REQ_STATUS[s] || [s, '']; return `<span class="pill ${c}">${esc(t)}</span>`; };

async function approvalsPage() {
  const r = await api('/approvals');
  ME.pending = r.to_decide.length; renderSide();
  main.innerHTML = `<h1>Aprovações</h1><div class="sub">Promoções para produção (quatro olhos: quem pediu não aprova) e pedidos de uso de agentes dos seus times</div>
  <div class="card"><h2>Para você decidir (${r.to_decide.length})</h2>
    ${r.to_decide.length ? r.to_decide.map(x => x.kind === 'promotion' ? `
      <div class="card mt"><div class="row between"><div><span class="pill info">produção</span> <a href="#/agents/${x.agent}"><b>${esc(x.agent_name)}</b></a> v${x.version}
        <div class="mute small">pedido por ${esc(x.requested_by)} · ${when(x.created_at)}${x.note ? ` · “${esc(x.note)}”` : ''}</div>
        ${x.stale ? '<div class="bad-text small">O agente mudou depois do pedido — aprovar não vale; peça de novo.</div>' : ''}</div>
        <div class="row"><button class="pr-ok" data-id="${x.id}" ${x.stale ? 'disabled' : ''}>Aprovar e publicar</button><button class="ghost pr-no" data-id="${x.id}">Recusar</button></div></div></div>` : `
      <div class="card mt"><div class="row between"><div><span class="pill">acesso</span> <b>${esc(x.user_name || x.user)}</b> <span class="mute small">${esc(x.user)}</span> quer usar <a href="#/agents/${x.agent}"><b>${esc(x.agent_name)}</b></a>
        <div class="mute small">${when(x.created_at)}${x.reason ? ` · “${esc(x.reason)}”` : ''}</div></div>
        <div class="row"><button class="ac-ok" data-id="${x.id}">Liberar</button><button class="ghost ac-no" data-id="${x.id}">Recusar</button></div></div></div>`).join('')
      : '<div class="mute">Nada pendente.</div>'}</div>
  <div class="grid g2 mt">
    <div class="card"><h2>Seus pedidos de acesso</h2>${r.my_access.length ? `<table><tr><th>Agente</th><th>Status</th><th></th></tr>${r.my_access.map(x => `<tr><td><a href="#/agents/${x.agent}">${esc(x.agent_name)}</a><div class="mute small">${when(x.created_at)}</div></td><td>${reqPill(x.status)}</td>
      <td>${x.status === 'approved' ? `<button class="ghost my-rev" data-id="${x.id}">Abrir mão</button>` : ''}</td></tr>`).join('')}</table>` : '<div class="mute">Nenhum. Procure agentes no <a href="#/agents/catalog">catálogo da empresa</a>.</div>'}</div>
    <div class="card"><h2>Suas promoções</h2>${r.my_promotions.length ? `<table><tr><th>Agente</th><th>Versão</th><th>Status</th></tr>${r.my_promotions.map(x => `<tr><td><a href="#/agents/${x.agent}">${esc(x.agent_name)}</a><div class="mute small">${when(x.created_at)}</div></td><td>v${x.version}</td><td>${reqPill(x.status)}${x.decided_by ? `<div class="mute small">por ${esc(x.decided_by)}</div>` : ''}</td></tr>`).join('')}</table>` : '<div class="mute">Nenhuma.</div>'}</div></div>`;
  const bind = (sel, url, msg, ask) => document.querySelectorAll(sel).forEach(b => b.onclick = e => (!ask || confirm(ask)) &&
    act(e.target, () => api(url(b.dataset.id), { method: 'POST', body: {} }).then(approvalsPage), msg));
  bind('.pr-ok', id => `/promotions/${id}/approve`, 'Aprovado — agente publicado em produção', 'Aprovar e publicar esta versão em produção?');
  bind('.pr-no', id => `/promotions/${id}/reject`, 'Promoção recusada');
  bind('.ac-ok', id => `/access-requests/${id}/approve`, 'Acesso liberado');
  bind('.ac-no', id => `/access-requests/${id}/reject`, 'Pedido recusado');
  bind('.my-rev', id => `/access-requests/${id}/revoke`, 'Acesso devolvido', 'Abrir mão do acesso? Suas chaves para este agente param de funcionar.');
}

/* ---------- times ---------- */
const budgetBar = t => {
  if (!t.budget_usd_month) return `<div class="small row between"><span class="mute">Gasto no mês</span><b>${usd(t.spent_month)}</b></div>`;
  const pct = Math.min(100, Math.round(100 * t.spent_month / t.budget_usd_month));
  return `<div class="small row between"><span>Orçamento do mês</span><b>${usd(t.spent_month)} / ${usd(t.budget_usd_month)}</b></div>
    <div class="hbar budget ${t.budget_state}"><i style="width:${pct}%"></i></div>${t.budget_state === 'over' ? `<div class="bad-text small">Estourado${t.budget_enforce ? ' — chamadas bloqueadas' : ''}</div>` : ''}`;
};

async function teamsPage(openForm) {
  const list = await api('/teams');
  main.innerHTML = `<div class="row between"><div><h1>Times (${list.length})</h1><div class="sub">Cada agente pertence a um time. Papéis: <b>mantenedor</b> (${ROLE_HELP.maintainer}), <b>developer</b> (${ROLE_HELP.developer}), <b>consumer</b> (${ROLE_HELP.consumer}).</div></div>
    ${ME.is_admin ? '<button id="tm-open">+ Novo time</button>' : ''}</div>
  ${ME.is_admin ? `<div class="card hero" id="tm-form" ${openForm ? '' : 'hidden'}><h2>Novo time</h2>
    <div class="grid g2"><label>Nome<input id="tm-name" placeholder="ex.: Dados e BI"></label><label>Descrição<input id="tm-desc" placeholder="opcional"></label></div>
    <div class="grid g2 mt"><label>Mantenedor (e-mail ou usuário, opcional)<input id="tm-maint" placeholder="quem vai gerenciar o time"></label>
      <label class="row small" style="align-self:end"><input type="checkbox" id="tm-appr" checked style="width:auto"> Produção exige aprovação de outro mantenedor</label></div>
    <div class="row mt"><button id="tm-go">Criar time</button><button id="tm-cancel" class="ghost">Cancelar</button><span class="mute small">Crie quantos times quiser; depois adicione os membros em cada um.</span></div></div>` : ''}
  <div class="grid g3 mt">${list.map(t => `<div class="card click-card" onclick="location.hash='#/teams/${t.slug}'"><div class="row between"><h2>${esc(t.name)}</h2>${t.my_role ? `<span class="pill ok">${esc(ROLE_LABEL[t.my_role])}</span>` : ''}</div>
    <div class="mute small" data-noi18n>${esc(t.description) || '&nbsp;'}</div><div class="row mt small"><span class="chip">${icon('team')}${t.members} membros</span><span class="chip">${icon('bot')}${t.agents} agentes</span>${t.require_approval ? '<span class="chip">aprovação p/ produção</span>' : ''}</div>
    <div class="mt">${budgetBar(t)}</div></div>`).join('')}</div>`;
  const form = $('#tm-form');
  if (!form) return;
  const show = on => { form.hidden = !on; if (on) $('#tm-name').focus(); };
  $('#tm-open').onclick = () => show(true);
  $('#tm-cancel').onclick = () => show(false);
  if (openForm) show(true);
  $('#tm-go').onclick = e => act(e.target, async () => {
    const name = $('#tm-name').value.trim();
    if (!name) throw new Error('Dê um nome ao time');
    const t = await api('/teams', { method: 'POST', body: { name, description: $('#tm-desc').value, require_approval: $('#tm-appr').checked, maintainer: $('#tm-maint').value.trim() || null } });
    toast(`Time “${t.name}” criado`);
    if (location.hash === '#/teams') teamsPage(); else location.hash = '#/teams';
  });
  $('#tm-name').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); $('#tm-go').click(); } };
}

async function teamDetail(slug) {
  const t = await api('/teams/' + slug);
  const [members, agents] = await Promise.all([api(`/teams/${slug}/members`).catch(() => null), api('/agents')]);
  const mine = agents.filter(a => a.team && a.team.slug === slug);
  const maint = ME.is_admin || t.my_role === 'maintainer';
  const roleSel = (v, id) => `<select class="mb-role" data-id="${id}" style="width:auto" ${maint ? '' : 'disabled'}>${['maintainer', 'developer', 'consumer'].map(r => `<option value="${r}" ${r === v ? 'selected' : ''}>${ROLE_LABEL[r]}</option>`).join('')}</select>`;
  main.innerHTML = `<a href="#/teams" class="mute small">← Times</a><div class="row between"><div><h1>${esc(t.name)} ${t.my_role ? `<span class="pill ok">${esc(ROLE_LABEL[t.my_role])}</span>` : ''}</h1><div class="sub">${esc(t.description) || ''}</div></div>
    <div class="row">${ME.is_admin ? '<a href="#/teams/new"><button class="ghost">+ Novo time</button></a>' : ''}${ME.is_admin && t.id !== 1 ? '<button id="tm-del" class="danger">Excluir time</button>' : ''}</div></div>
  <div class="grid g2">
    <div class="card"><h2>Membros (${members ? members.length : t.members})</h2>
      ${maint ? `<div class="row"><input id="mb-email" placeholder="e-mail da pessoa" style="flex:1"><select id="mb-role" style="width:auto">${['consumer', 'developer', 'maintainer'].map(r => `<option value="${r}">${ROLE_LABEL[r]}</option>`).join('')}</select><button id="mb-add">Adicionar</button></div>
        <div class="mute small">Quem ainda não entrou fica pré-cadastrado e cai no time no primeiro login.</div>` : ''}
      ${members ? `<table class="mt"><tr><th>Pessoa</th><th>Papel</th><th>Origem</th><th></th></tr>${members.map(m => `<tr class="${m.active ? '' : 'mute'}"><td><b>${esc(m.name || m.email)}</b><div class="mute small">${esc(m.email)}${m.active ? '' : ' · desativado'}</div></td>
        <td>${roleSel(m.role, m.user_id)}</td><td><span class="chip">${esc(m.source)}</span></td><td>${maint ? `<button class="ghost mb-rm" data-id="${m.user_id}" data-n="${esc(m.email)}">Remover</button>` : ''}</td></tr>`).join('')}</table>`
        : '<div class="mute">A lista de membros é visível para quem é do time.</div>'}</div>
    <div class="card"><h2>Configuração</h2>
      <label>Nome<input id="ts-name" value="${esc(t.name)}" ${maint ? '' : 'disabled'}></label>
      <label class="mt">Descrição<input id="ts-desc" value="${esc(t.description)}" ${maint ? '' : 'disabled'}></label>
      <label class="row small mt"><input type="checkbox" id="ts-appr" style="width:auto" ${t.require_approval ? 'checked' : ''} ${ME.is_admin ? '' : 'disabled'}> Produção exige aprovação de outro mantenedor (quatro olhos)</label>
      <div class="grid g2 mt"><label>Orçamento mensal (US$)<input id="ts-budget" type="number" min="0" step="1" value="${t.budget_usd_month ?? ''}" placeholder="sem limite" ${ME.is_admin ? '' : 'disabled'}></label>
        <label class="row small" style="align-self:end"><input type="checkbox" id="ts-enf" style="width:auto" ${t.budget_enforce ? 'checked' : ''} ${ME.is_admin ? '' : 'disabled'}> bloquear chamadas ao estourar</label></div>
      <div class="mt">${budgetBar(t)}</div>
      ${maint ? '<button id="ts-save" class="mt">Salvar</button>' : ''}${ME.is_admin ? '' : '<div class="mute small mt">Aprovação e orçamento são definidos por admins.</div>'}</div></div>
  <div class="card mt"><h2>Agentes do time (${mine.length})</h2>${mine.length ? `<table><tr><th>Agente</th><th>Status</th><th>Visibilidade</th><th>Custo 7d</th></tr>${mine.map(a => `<tr class="click" onclick="location.hash='#/agents/${a.slug}'"><td><b>${esc(a.name)}</b></td><td>${pill(a.status)}</td><td>${visPill(a.visibility)}</td><td>${usd(a.cost_7d)}</td></tr>`).join('')}</table>` : '<div class="mute">Nenhum agente.</div>'}</div>`;
  const reload = () => teamDetail(slug);
  const on = (id, fn) => { const b = $(id); if (b) b.onclick = fn; };
  on('#mb-add', e => act(e.target, () => api(`/teams/${slug}/members`, { method: 'POST', body: { email: $('#mb-email').value.trim(), role: $('#mb-role').value } }).then(reload), 'Membro adicionado'));
  document.querySelectorAll('.mb-role').forEach(s => s.onchange = () => api(`/teams/${slug}/members/${s.dataset.id}`, { method: 'PATCH', body: { role: s.value } }).then(() => toast('Papel atualizado')).catch(e => { toast(e.message, true); reload(); }));
  document.querySelectorAll('.mb-rm').forEach(b => b.onclick = e => confirm(`Remover ${b.dataset.n} do time? As chaves dessa pessoa para agentes do time param na hora.`) &&
    act(e.target, () => api(`/teams/${slug}/members/${b.dataset.id}`, { method: 'DELETE' }).then(reload), 'Membro removido'));
  on('#ts-save', e => {
    const body = { name: $('#ts-name').value.trim(), description: $('#ts-desc').value };
    if (ME.is_admin) Object.assign(body, { require_approval: $('#ts-appr').checked, budget_usd_month: num($('#ts-budget').value) ?? 0, budget_enforce: $('#ts-enf').checked });
    return act(e.target, () => api('/teams/' + slug, { method: 'PATCH', body }).then(reload), 'Time atualizado');
  });
  on('#tm-del', e => confirm(`Excluir o time ${t.name}?`) && act(e.target, () => api('/teams/' + slug, { method: 'DELETE' }).then(() => location.hash = '#/teams'), 'Time excluído'));
}

/* ---------- usuários ---------- */
/* A lista de pessoas da empresa: hoje dentro de Membros → Pessoas (console). */
async function usersPage(el = main) {
  const list = await api('/users');
  const render = q => {
    const rows = list.filter(u => (u.email + u.name).toLowerCase().includes(q.toLowerCase()));
    $('#u-rows').innerHTML = rows.map(u => `<tr class="${u.active ? '' : 'mute'}"><td><b>${esc(u.name || u.email)}</b><div class="mute small">${esc(u.email)}</div></td>
      <td>${u.username ? `<span class="chip" title="Conta local">${icon('user')}${esc(u.username)}</span> ` : ''}${u.provider ? `<span class="chip">${esc(u.provider)}</span>` : (u.username ? '' : '<span class="mute small">pré-cadastro</span>')}${u.scim ? ' <span class="chip">scim</span>' : ''}</td>
      <td>${ME.is_admin ? `<select class="u-role" data-id="${u.id}" style="width:auto">${['member', 'auditor', 'admin'].map(r => `<option value="${r}" ${r === u.org_role ? 'selected' : ''}>${ROLE_LABEL[r]}</option>`).join('')}</select>` : esc(ROLE_LABEL[u.org_role])}</td>
      <td>${u.teams.map(t => `<span class="chip" title="${esc(ROLE_LABEL[t.role])}">${esc(t.name)} · ${esc(ROLE_LABEL[t.role])}</span>`).join('') || '<span class="mute small">—</span>'}</td>
      <td class="mute small">${u.last_login_at ? when(u.last_login_at) : 'nunca'}</td>
      <td>${ME.is_admin ? `<button class="ghost u-pw" data-id="${u.id}" data-u="${esc(u.username || '')}" data-n="${esc(u.email)}">${u.username ? 'Redefinir senha' : 'Criar login local'}</button> ` : ''}${ME.is_admin ? `<button class="ghost u-act ${u.active ? '' : 'on'}" data-id="${u.id}" data-a="${u.active ? 0 : 1}" data-n="${esc(u.email)}">${u.active ? 'Desativar' : 'Reativar'}</button>` : (u.active ? '' : '<span class="pill bad">desativado</span>')}</td></tr>`).join('') || '<tr><td colspan="6" class="empty">Nenhum usuário</td></tr>';
    document.querySelectorAll('.u-role').forEach(s => s.onchange = () => api(`/users/${s.dataset.id}`, { method: 'PATCH', body: { org_role: s.value } }).then(() => toast('Papel atualizado')).catch(e => toast(e.message, true)));
    document.querySelectorAll('.u-pw').forEach(b => b.onclick = async () => {
      const username = b.dataset.u || prompt(`Nome de usuário para ${b.dataset.n} (minúsculas, números, . _ -):`);
      if (!username) return;
      const password = prompt(`Nova senha para ${username} (mínimo 8 caracteres). As sessões atuais dessa pessoa serão encerradas.`);
      if (!password) return;
      try { await api(`/users/${b.dataset.id}`, { method: 'PATCH', body: { username, password } }); toast('Senha definida'); route(); } catch (err) { toast(err.message, true); }
    });
    document.querySelectorAll('.u-act').forEach(b => b.onclick = e => (b.dataset.a === '1' || confirm(`Desativar ${b.dataset.n}? Sessões encerradas e todas as chaves revogadas na hora.`)) &&
      act(e.target, () => api(`/users/${b.dataset.id}`, { method: 'PATCH', body: { active: b.dataset.a === '1' } }).then(route), 'Usuário atualizado'));
  };
  el.innerHTML = `<div class="row between mt"><div class="mute small">Criados no primeiro login (SSO), por convite, por SCIM ou pré-cadastrados. <b>Admin</b>: tudo. <b>Auditor</b>: lê tudo (uso, custo, auditoria, specs). <b>Membro</b>: conforme os times.</div>
    <input id="u-q" placeholder="Buscar…" style="max-width:240px"></div>
  ${ME.is_admin ? `<details class="card mt"><summary>Cadastrar sem convite (conta local ou pré-cadastro)</summary><div class="grid g4 mt"><input id="nu-email" placeholder="e-mail (entra por SSO)"><input id="nu-name" placeholder="nome (opcional)">
    <input id="nu-user" placeholder="usuário (conta local, opcional)" autocomplete="off"><input id="nu-pass" type="password" placeholder="senha da conta local" autocomplete="new-password"></div>
    <div class="grid g4 mt"><select id="nu-role"><option value="member">Membro</option><option value="auditor">Auditor</option><option value="admin">Admin</option></select><button id="nu-go">Cadastrar</button></div>
    <div class="mute small mt">Só e-mail: pré-cadastro, a pessoa entra pelo SSO. Com usuário e senha: conta local (login direto na tela de entrada).</div></details>` : ''}
  <div class="card mt scroll"><table><tr><th>Pessoa</th><th>Origem</th><th>Papel na empresa</th><th>Times</th><th>Último login</th><th></th></tr><tbody id="u-rows"></tbody></table></div>`;
  render(''); $('#u-q').oninput = e => render(e.target.value);
  const go = $('#nu-go');
  if (go) go.onclick = e => act(e.target, () => api('/users', { method: 'POST', body: { email: $('#nu-email').value.trim(), name: $('#nu-name').value, org_role: $('#nu-role').value,
    username: $('#nu-user').value.trim() || null, password: $('#nu-pass').value || null } }).then(route), 'Usuário cadastrado');
}

/* ---------- SSO (OAuth2) e SCIM ---------- */
const SSO_FIELDS = {
  google: [['allowed_domains', 'Domínios permitidos (vírgula) — ex.: empresa.com.br']],
  microsoft: [['tenant', 'Tenant (ID ou domínio, ex.: empresa.onmicrosoft.com)'], ['allowed_domains', 'Domínios permitidos (opcional)'], ['fetch_groups', 'Ler grupos (GroupMember.Read.All, consentimento de admin)', 'bool']],
  github: [['orgs', 'Organizações do GitHub exigidas (vírgula)'], ['fetch_groups', 'Ler times do GitHub para mapear', 'bool']],
  oauth2: [['label', 'Nome no botão (ex.: Keycloak)'], ['authorize_url', 'Authorization URL'], ['token_url', 'Token URL'], ['userinfo_url', 'Userinfo URL'],
    ['scopes', 'Escopos (espaço)'], ['groups_field', 'Campo de grupos no userinfo (padrão groups)'], ['allowed_domains', 'Domínios permitidos (opcional)'],
    ['trust_email', 'Confiar no e-mail sem email_verified (só se o provedor garante)', 'bool']],
};
const SSO_HELP = {
  google: 'Google Cloud Console → APIs e serviços → Credenciais → ID do cliente OAuth (Aplicativo da Web). Tela de consentimento “Interno” restringe à sua organização.',
  microsoft: 'Entra ID → Registros de aplicativo → Novo registro (conta só deste diretório) → Certificados e segredos. Permissões delegadas: User.Read (e GroupMember.Read.All para grupos).',
  github: 'GitHub → Settings → Developer settings → OAuth Apps (ou da organização). Escopos pedidos: read:user, user:email, read:org.',
  oauth2: 'Qualquer provedor OAuth2 com endpoint de userinfo: Keycloak, Okta, Auth0, Authentik… Para SAML, use o Keycloak como ponte (identity brokering).',
};

async function ssoPage() {
  const [c, keys, teamList] = await Promise.all([api('/sso'), api('/keys'), api('/teams')]);
  const scimKeys = keys.filter(k => k.scopes.includes('scim') && !k.revoked);
  const val = (p, k) => { const v = p.settings[k]; return Array.isArray(v) ? v.join(', ') : (v ?? ''); };
  main.innerHTML = `<h1>SSO e SCIM</h1><div class="sub">Login corporativo por OAuth2 e provisionamento automático de usuários e grupos. Segredos ficam criptografados; variáveis OAUTH_* do .env valem como padrão.</div>
  <div class="grid g2">${c.providers.map(p => `<div class="card"><div class="row between"><h2><span class="sso-ic">${providerIcon(p.id)}</span> ${esc(p.id === 'oauth2' ? 'OAuth2 genérico' : p.label)}</h2>
      ${p.ready ? '<span class="pill ok">ativo</span>' : p.enabled && p.client_id ? '<span class="pill warn">incompleto</span>' : '<span class="pill">desligado</span>'}</div>
    <div class="mute small">${esc(SSO_HELP[p.id])}</div>
    <div class="small mt">Redirect URI (cadastre no provedor): ${copyable(p.redirect_uri)}</div>
    <label class="row small mt"><input type="checkbox" id="sp-${p.id}-en" style="width:auto" ${p.enabled ? 'checked' : ''}> Ativar login com este provedor</label>
    <div class="grid g2 mt"><label>Client ID<input id="sp-${p.id}-cid" value="${esc(p.client_id)}"></label>
      <label>Client secret<input id="sp-${p.id}-sec" type="password" placeholder="${p.has_secret ? '•••••• (salvo — vazio mantém)' : 'segredo do app'}" autocomplete="new-password"></label></div>
    ${SSO_FIELDS[p.id].map(([k, label, kind]) => kind === 'bool'
      ? `<label class="row small mt"><input type="checkbox" id="sp-${p.id}-${k}" style="width:auto" ${p.settings[k] ? 'checked' : ''}> ${esc(label)}</label>`
      : `<label class="mt">${esc(label)}<input id="sp-${p.id}-${k}" value="${esc(val(p, k))}"></label>`).join('')}
    <button class="mt sp-save" data-p="${p.id}">Salvar</button></div>`).join('')}</div>

  <div class="card mt"><h2>Grupos → times</h2><div class="mute small">Quem está no grupo entra no time com o papel indicado a cada login (OAuth2) ou mudança no diretório (SCIM). Quem foi adicionado à mão não é mexido.
    Microsoft: ID ou nome do grupo · GitHub: <code>org/time</code> · OAuth2: valor do claim de grupos · SCIM: nome do grupo provisionado.</div>
    <table class="mt"><tr><th>Origem</th><th>Grupo externo</th><th>Time</th><th>Papel</th><th></th></tr>
    ${c.mappings.map(m => `<tr><td><span class="chip">${esc(m.provider)}</span></td><td><code class="inline">${esc(m.external_group)}</code></td><td>${esc(m.team)}</td><td>${esc(ROLE_LABEL[m.role])}</td><td><button class="ghost mp-del" data-id="${m.id}">Remover</button></td></tr>`).join('') || '<tr><td colspan="5" class="mute">Nenhum mapeamento</td></tr>'}</table>
    <div class="grid g4 mt"><select id="mp-prov">${['microsoft', 'github', 'oauth2', 'scim'].map(x => `<option>${x}</option>`).join('')}</select><input id="mp-grp" placeholder="grupo externo">
      <select id="mp-team">${teamList.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select>
      <div class="row"><select id="mp-role" style="width:auto">${['consumer', 'developer', 'maintainer'].map(r => `<option value="${r}">${ROLE_LABEL[r]}</option>`).join('')}</select><button id="mp-add">Adicionar</button></div></div></div>

  <div class="grid g2 mt">
    <div class="card"><h2>SCIM 2.0 (provisionamento)</h2><div class="mute small">No Entra ID: Aplicativos empresariais → seu app → Provisionamento → Automático. Okta: app SCIM 2.0. Desligar alguém no diretório encerra as sessões e revoga as chaves dele aqui.</div>
      <div class="small mt">URL do locatário: ${copyable(c.scim_url)}</div>
      <div class="row mt"><input id="sc-name" placeholder="nome do token (ex.: entra-id)" value="entra-id"><button id="sc-go">Gerar token SCIM</button></div><div id="sc-new"></div>
      ${scimKeys.length ? `<table class="mt"><tr><th>Token</th><th>Último uso</th><th></th></tr>${scimKeys.map(k => `<tr><td><b>${esc(k.name)}</b> <code class="inline">${esc(k.prefix)}…</code></td><td class="mute small">${k.last_used_at ? when(k.last_used_at) : 'nunca'}</td><td><button class="ghost sc-rev" data-id="${k.id}">Revogar</button></td></tr>`).join('')}</table>` : ''}</div>
    <div class="card"><h2>Primeiro acesso e emergência</h2><div class="small">E-mails em <code>BOOTSTRAP_ADMIN_EMAILS</code> viram admin no login: ${c.bootstrap_admins.length ? c.bootstrap_admins.map(e => `<span class="chip">${esc(e)}</span>`).join('') : '<span class="mute">nenhum definido</span>'}</div>
      <div class="mute small mt">O <code>ADMIN_TOKEN</code> continua funcionando como acesso de emergência (“Entrar com token”): guarde-o num cofre e use só se o SSO cair.</div></div></div>`;
  document.querySelectorAll('.sp-save').forEach(b => b.onclick = e => {
    const p = b.dataset.p; const settings = {};
    SSO_FIELDS[p].forEach(([k, , kind]) => { const el = $(`#sp-${p}-${k}`); settings[k] = kind === 'bool' ? el.checked : el.value.trim(); });
    return act(e.target, () => api('/sso/' + p, { method: 'PUT', body: { enabled: $(`#sp-${p}-en`).checked, client_id: $(`#sp-${p}-cid`).value.trim(), client_secret: $(`#sp-${p}-sec`).value, settings } }).then(ssoPage), 'Provedor salvo');
  });
  $('#mp-add').onclick = e => act(e.target, () => api('/sso/mappings', { method: 'POST', body: { provider: $('#mp-prov').value, external_group: $('#mp-grp').value.trim(), team: $('#mp-team').value, role: $('#mp-role').value } }).then(ssoPage), 'Mapeamento criado');
  document.querySelectorAll('.mp-del').forEach(b => b.onclick = e => act(e.target, () => api('/sso/mappings/' + b.dataset.id, { method: 'DELETE' }).then(ssoPage), 'Removido'));
  $('#sc-go').onclick = e => act(e.target, async () => {
    const r = await api('/keys', { method: 'POST', body: { name: $('#sc-name').value.trim() || 'scim', scopes: ['scim'] } });
    $('#sc-new').innerHTML = `<div class="card mt warn-card"><b>Copie agora — não será exibido de novo</b> (cole como “Token secreto” no diretório):${copyable(r.key)}</div>`;
  });
  document.querySelectorAll('.sc-rev').forEach(b => b.onclick = e => confirm('Revogar o token SCIM? O diretório para de sincronizar.') && act(e.target, () => api('/keys/' + b.dataset.id, { method: 'DELETE' }).then(ssoPage), 'Token revogado'));
}

/* ---------- agente: aba Agendamentos ---------- */
const SCH_STATUS = { passed: ['concluído', 'ok'], failed: ['falhou', 'bad'], skipped: ['pulado', 'warn'], running: ['rodando', 'info'] };
const schPill = s => { if (!s) return '<span class="mute small">ainda não rodou</span>'; const [t, c] = SCH_STATUS[s] || [s, '']; return `<span class="pill ${c}">${esc(t)}</span>`; };
const WEEKDAYS = [['1', 'segunda'], ['2', 'terça'], ['3', 'quarta'], ['4', 'quinta'], ['5', 'sexta'], ['6', 'sábado'], ['0', 'domingo']];

async function agentSchedules(t, a) {
  const list = await api(`/agents/${a.slug}/schedules`);
  const edit = can(a, 'edit');
  const tz = (ME.org && ME.org.timezone) || 'UTC';
  t.innerHTML = `${a.prod ? '' : '<div class="card warn-card">O agente ainda não está em produção: os agendamentos ficam aguardando e começam a disparar quando ele subir.</div>'}
  <div class="card mt"><h2>Agendamentos (${list.length})</h2>
    ${list.length ? `<div class="scroll"><table><tr><th>Quando</th><th>Mensagem</th><th>Próximo disparo</th><th>Última execução</th><th></th></tr>
      ${list.map(s => `<tr><td><b>${esc(s.name)}</b><div class="mute small">${esc(s.when)} (${esc(s.timezone)})${s.notify_url ? ' · webhook' : ''}</div></td>
        <td class="small one-line" style="max-width:280px" title="${esc(s.message)}">${esc(s.message)}</td>
        <td>${s.enabled ? (s.next_run_local ? esc(s.next_run_local) : '—') : '<span class="pill">pausado</span>'}</td>
        <td>${schPill(s.last_status)}${s.last_run_at ? `<div class="mute small">${when(s.last_run_at)}</div>` : ''}</td>
        <td class="row" style="flex-wrap:nowrap;justify-content:flex-end">
          <button class="ghost sc-hist" data-id="${s.id}">Histórico</button>
          ${edit ? `<button class="ghost sc-run" data-id="${s.id}">Rodar agora</button>
          <button class="ghost sc-tog" data-id="${s.id}" data-on="${s.enabled ? 1 : 0}">${s.enabled ? 'Pausar' : 'Retomar'}</button>
          <button class="danger sc-del" data-id="${s.id}">Excluir</button>` : ''}</td></tr>
        <tr class="sc-runs" id="sc-runs-${s.id}" hidden><td colspan="5"></td></tr>`).join('')}</table></div>`
      : '<div class="mute">Nenhum agendamento. O agente roda sozinho no dia e hora escolhidos, com a mensagem que você definir.</div>'}</div>
  ${edit ? `<div class="card mt"><h2>Novo agendamento</h2>
    <label>O que o agente deve fazer a cada disparo<textarea id="sc-msg" rows="3" placeholder="ex.: Gere o resumo de vendas da semana anterior com os 5 principais clientes"></textarea></label>
    <div class="grid g4 mt">
      <label>Frequência<select id="sc-freq"><option value="daily">Todo dia</option><option value="weekdays">Dias úteis (seg a sex)</option>
        <option value="weekly">Toda semana</option><option value="monthly">Todo mês</option><option value="once">Uma vez</option><option value="cron">Cron personalizado</option></select></label>
      <label id="sc-l-time">Hora<input id="sc-time" type="time" value="09:00"></label>
      <label id="sc-l-dow" hidden>Dia da semana<select id="sc-dow">${WEEKDAYS.map(([v, n]) => `<option value="${v}">${n}</option>`).join('')}</select></label>
      <label id="sc-l-dom" hidden>Dia do mês<input id="sc-dom" type="number" min="1" max="28" value="1"></label>
      <label id="sc-l-once" hidden>Data e hora<input id="sc-once" type="datetime-local"></label>
      <label id="sc-l-cron" hidden>Cron (5 campos)<input id="sc-cron" placeholder="0 9 * * 1-5"></label>
      <label>Fuso horário<input id="sc-tz" value="${esc(tz)}"></label></div>
    <div class="grid g2 mt"><label>Nome (opcional)<input id="sc-name" placeholder="ex.: Resumo semanal"></label>
      <label>Enviar o resultado para (webhook, opcional)<input id="sc-hook" placeholder="https://hooks.slack.com/services/…"></label></div>
    <div class="row mt"><button id="sc-go">Criar agendamento</button><span class="mute small">Sem webhook, o resultado fica no histórico desta aba.</span></div></div>` : ''}`;

  const freq = $('#sc-freq');
  if (freq) {
    const sync = () => {
      const f = freq.value;
      $('#sc-l-time').hidden = ['once', 'cron'].includes(f); $('#sc-l-dow').hidden = f !== 'weekly';
      $('#sc-l-dom').hidden = f !== 'monthly'; $('#sc-l-once').hidden = f !== 'once'; $('#sc-l-cron').hidden = f !== 'cron';
    };
    freq.onchange = sync; sync();
    $('#sc-go').onclick = e => act(e.target, async () => {
      const f = freq.value, [hh, mm] = ($('#sc-time').value || '09:00').split(':').map(Number);
      const body = { message: $('#sc-msg').value.trim(), timezone: $('#sc-tz').value.trim(), name: $('#sc-name').value.trim(), notify_url: $('#sc-hook').value.trim() };
      if (!body.message) throw new Error('Descreva o que o agente deve fazer');
      if (f === 'once') { if (!$('#sc-once').value) throw new Error('Escolha data e hora'); body.run_at = $('#sc-once').value; }
      else body.cron = f === 'cron' ? $('#sc-cron').value.trim()
        : `${mm} ${hh} ${f === 'monthly' ? Number($('#sc-dom').value || 1) : '*'} * ${f === 'weekdays' ? '1-5' : f === 'weekly' ? $('#sc-dow').value : '*'}`;
      const s = await api(`/agents/${a.slug}/schedules`, { method: 'POST', body });
      toast(`Agendado: ${s.when}${s.next_run_local ? ` — próximo em ${s.next_run_local}` : ''}`);
      agentSchedules(t, a);
    });
  }
  t.querySelectorAll('.sc-hist').forEach(b => b.onclick = async () => {
    const row = $(`#sc-runs-${b.dataset.id}`);
    if (!row.hidden) { row.hidden = true; return; }
    const runs = await api(`/schedules/${b.dataset.id}/runs?limit=10`);
    row.firstElementChild.innerHTML = runs.length ? runs.map(r => `<div class="card mt"><div class="row between"><div>${schPill(r.status)} <span class="mute small">${when(r.started_at)}${r.trigger !== 'schedule' ? ` (${r.trigger === 'manual' ? 'manual' : 'atrasado'})` : ''}${r.version ? `, v${r.version}` : ''}</span></div>
      <span class="mute small">${fmt(r.tokens)} tokens, ${usd(r.cost_usd)}${r.notify_status ? `, webhook ${esc(r.notify_status)}` : ''}</span></div>
      ${r.output ? `<div class="msg a md run-out">${mdLite(r.output)}</div>` : ''}${r.error ? `<div class="bad-text small mt">${esc(r.error)}</div>` : ''}</div>`).join('') : '<div class="mute small">Nenhuma execução ainda.</div>';
    row.hidden = false;
  });
  t.querySelectorAll('.sc-run').forEach(b => b.onclick = e => act(e.target, async () => {
    const r = await api(`/schedules/${b.dataset.id}/run`, { method: 'POST' });
    toast(r.status === 'passed' ? 'Execução concluída — veja o histórico' : `Execução: ${r.status}${r.error ? ` (${r.error})` : ''}`, r.status !== 'passed');
    agentSchedules(t, a);
  }));
  t.querySelectorAll('.sc-tog').forEach(b => b.onclick = e => act(e.target, () => api(`/schedules/${b.dataset.id}`, { method: 'PATCH', body: { enabled: b.dataset.on !== '1' } }).then(() => agentSchedules(t, a))));
  t.querySelectorAll('.sc-del').forEach(b => b.onclick = e => confirm('Excluir o agendamento e o histórico dele?') &&
    act(e.target, () => api(`/schedules/${b.dataset.id}`, { method: 'DELETE' }).then(() => agentSchedules(t, a)), 'Agendamento excluído'));
}

/* ---------- Catálogo Docker MCP (gateway) ---------- */
async function gatewayCatalog(box, q = '') {
  const [st, cat] = await Promise.all([api('/mcp-gateway/status').catch(e => ({ online: false, error: e.message })),
    api('/mcp-gateway/catalog?limit=24&q=' + encodeURIComponent(q)).catch(e => ({ error: e.message, servers: [] }))]);
  if (!box.isConnected) return;  // a pessoa já saiu da tela enquanto o catálogo carregava
  const tools = st.tools || {};
  box.innerHTML = `<div class="card"><div class="row between"><h2>Catálogo Docker MCP ${cat.total ? `(${cat.total} servidores)` : ''}</h2>
      ${st.online ? `<span class="pill ok">gateway no ar, ${st.total_tools} ferramentas</span>` : '<span class="pill warn">gateway fora do ar</span>'}</div>
    <div class="mute small">Servidores MCP prontos, mantidos no catálogo oficial da Docker e rodando cada um num container isolado com imagem assinada.
      Ao ativar, o servidor vira o item <code>docker:&lt;nome&gt;</code> deste catálogo e os agentes passam a usá-lo pela spec (<code>mcps</code>).
      ${st.online ? '' : `<br>Para ligar o gateway: <code>docker compose --profile mcp-gateway up -d</code>${st.error ? ` <span class="bad-text">(${esc(st.error)})</span>` : ''}`}</div>
    ${cat.error ? `<div class="bad-text small mt">${esc(cat.error)}</div>` : ''}
    <div class="row mt"><input id="gw-q" placeholder="Buscar: github, postgres, slack, notion, busca…" value="${esc(q)}" style="flex:1"><button id="gw-go" class="ghost">Buscar</button></div>
    <div class="mute small mt">${cat.matches ?? 0} resultado(s)${cat.matches > 24 ? ' (mostrando 24 — refine a busca)' : ''}${cat.enabled && cat.enabled.length ? `, ${cat.enabled.length} ativo(s)` : ''}.</div>
    <div class="grid g3 mt">${(cat.servers || []).map(s => `<div class="card gw-item">
      <div class="row between"><b>${esc(s.title)}</b>${s.enabled ? '<span class="pill ok">ativo</span>' : ''}</div>
      <div class="mute small"><code>${esc(s.name)}</code>${s.secrets.length ? ` · precisa de ${s.secrets.length} segredo(s)` : ''}</div>
      <div class="small mt one-line" data-noi18n style="white-space:normal;max-height:4.5em" title="${esc(s.description)}">${esc(s.description)}</div>
      ${s.enabled && tools[s.name] ? `<div class="mute small mt">${tools[s.name].length} ferramentas: ${esc(tools[s.name].slice(0, 6).join(', '))}${tools[s.name].length > 6 ? '…' : ''}</div>` : ''}
      <div class="gw-form" data-n="${esc(s.name)}" hidden>${s.secrets.map(x => `<label class="mt">${esc(x.name)}<input type="password" data-secret="${esc(x.name)}" placeholder="${s.enabled ? '•••••• (salvo — vazio mantém)' : esc(x.example || 'valor')}" autocomplete="new-password"></label>`).join('')}
        ${s.config.map(x => `<label class="mt">${esc(x.name)}<input data-config="${esc(x.name)}" placeholder="${esc(x.description || x.type)}"></label>`).join('')}</div>
      <div class="row mt">${s.enabled
        ? `<button class="ghost gw-off" data-n="${esc(s.name)}">Desativar</button>${s.secrets.length || s.config.length ? `<button class="ghost gw-on" data-n="${esc(s.name)}" data-f="${s.secrets.length + s.config.length}">Atualizar</button>` : ''}`
        : `<button class="gw-on" data-n="${esc(s.name)}" data-f="${s.secrets.length + s.config.length}">Ativar</button>`}</div></div>`).join('')}</div></div>`;
  const reload = () => gatewayCatalog(box, box.querySelector('#gw-q').value.trim());
  box.querySelector('#gw-go').onclick = reload;
  box.querySelector('#gw-q').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); reload(); } };
  box.querySelectorAll('.gw-on').forEach(b => b.onclick = e => {
    const form = box.querySelector(`.gw-form[data-n="${b.dataset.n}"]`);
    if (+b.dataset.f && form.hidden) { form.hidden = false; b.textContent = 'Confirmar'; return; }  // pede os segredos antes
    const secrets = {}, config = {};
    form.querySelectorAll('[data-secret]').forEach(i => { if (i.value) secrets[i.dataset.secret] = i.value; });
    form.querySelectorAll('[data-config]').forEach(i => { if (i.value) config[i.dataset.config] = i.value; });
    act(e.target, () => api('/mcp-gateway/servers/' + encodeURIComponent(b.dataset.n), { method: 'PUT', body: { secrets, config } })
      .then(r => { toast(`${b.dataset.n} ativo — use "${r.catalog_mcp}" nos agentes`); setTimeout(reload, 1500); }));
  });
  box.querySelectorAll('.gw-off').forEach(b => b.onclick = e => confirm(`Desativar ${b.dataset.n}? Agentes que o usam perdem essas ferramentas.`) &&
    act(e.target, () => api('/mcp-gateway/servers/' + encodeURIComponent(b.dataset.n), { method: 'DELETE' }).then(reload), 'Desativado'));
}

/* ---------- MCPs remotos com OAuth (Activepieces, Notion, Linear…) ---------- */
const RM_STATUS = { connected: ['conectado', 'ok'], pending: ['aguardando autorização', 'warn'], error: ['erro — reconecte', 'bad'] };
async function remoteMcps(box) {
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  if (q.get('remote_ok')) toast(`MCP remoto "${q.get('remote_ok')}" conectado — use-o nos agentes pelo nome`);
  if (q.get('remote_error')) toast(`Falha ao conectar: ${q.get('remote_error')}`, true);
  if (q.get('remote_ok') || q.get('remote_error')) history.replaceState(null, '', '#/catalog');
  const list = await api('/remote-mcps').catch(() => []);
  box.innerHTML = `<div class="card"><h2>MCPs remotos com OAuth (${list.length})</h2>
    <div class="mute small">Servidores MCP que exigem login (Activepieces, Notion, Linear, Atlassian…). Você autoriza uma vez; a central guarda os tokens criptografados,
      renova sozinha e entrega o MCP aos agentes por um proxy interno — o agente nunca vê a credencial. Use o <b>nome</b> no <code>mcps</code> do agente.</div>
    ${list.length ? `<table class="mt"><tr><th>MCP</th><th>Status</th><th>Token</th><th></th></tr>${list.map(r => `<tr>
      <td><b>${esc(r.name)}</b><div class="mute small">${esc(r.url)}</div>${r.last_error ? `<div class="bad-text small">${esc(r.last_error)}</div>` : ''}</td>
      <td>${(([t, c]) => `<span class="pill ${c}">${t}</span>`)(RM_STATUS[r.status] || [r.status, ''])}<div class="mute small">${r.connected_by ? 'por ' + esc(r.connected_by) : ''}</div></td>
      <td class="mute small">${r.expires_at ? 'renova sozinho' + (r.has_refresh_token ? '' : ' (sem refresh!)') : '—'}</td>
      <td class="row" style="flex-wrap:nowrap;justify-content:flex-end"><button class="ghost rm-test" data-n="${esc(r.name)}">Testar</button>
        <button class="ghost rm-re" data-n="${esc(r.name)}" data-u="${esc(r.url)}" data-b="${esc(r.browser_base)}">Reconectar</button>
        <button class="danger rm-del" data-n="${esc(r.name)}">Remover</button></td></tr>`).join('')}</table>` : ''}
    <details class="mt" ${list.length ? '' : 'open'}><summary>+ Conectar MCP remoto</summary>
      <div class="grid g2 mt"><label>Nome (vira o nome no catálogo)<input id="rm-name" placeholder="ex.: activepieces"></label>
        <label>URL do MCP<input id="rm-url" placeholder="https://servidor/mcp"></label></div>
      <div class="grid g2 mt"><label>Descrição (opcional)<input id="rm-desc" placeholder="o que os agentes ganham com ele"></label>
        <label>Origem pública para o navegador (opcional)<input id="rm-browser" placeholder="ex.: http://localhost:8098 — quando a central usa um endereço interno"></label></div>
      <div class="row mt"><button id="rm-go">Conectar (abre a tela de autorização)</button></div></details></div>`;
  const connect = async (body, btn) => act(btn, async () => {
    const r = await api('/remote-mcps', { method: 'POST', body });
    location.href = r.authorize_url;  // autoriza no provedor; ele volta para /api/remote-mcps/callback
  });
  $('#rm-go').onclick = e => connect({ name: $('#rm-name').value.trim(), url: $('#rm-url').value.trim(), description: $('#rm-desc').value, browser_base: $('#rm-browser').value.trim() }, e.target);
  box.querySelectorAll('.rm-re').forEach(b => b.onclick = e => connect({ name: b.dataset.n, url: b.dataset.u, browser_base: b.dataset.b }, e.target));
  box.querySelectorAll('.rm-test').forEach(b => b.onclick = e => act(e.target, async () => {
    const r = await api(`/remote-mcps/${encodeURIComponent(b.dataset.n)}/test`, { method: 'POST' });
    toast(`${r.name}: ${r.count} ferramentas (${r.tools.slice(0, 5).join(', ')}${r.count > 5 ? '…' : ''})`);
  }));
  box.querySelectorAll('.rm-del').forEach(b => b.onclick = e => confirm(`Remover o MCP remoto ${b.dataset.n}? Os tokens são revogados e os agentes perdem essas ferramentas.`) &&
    act(e.target, () => api(`/remote-mcps/${encodeURIComponent(b.dataset.n)}`, { method: 'DELETE' }).then(() => remoteMcps(box)), 'Removido'));
}

/* ---------- agente: aba Memória (Graphiti) ---------- */
const MEM_SCOPE = { agent: 'só este agente', team: 'compartilhada com o time', org: 'compartilhada com a empresa' };
const memDate = iso => iso ? new Date(iso).toLocaleDateString(window.LOCALE || 'pt-BR') : '—';

async function agentMemory(t, a, env = 'prod', q = '') {
  const mem = a.spec && a.spec.memory;
  if (!mem) {
    t.innerHTML = `<div class="card mt"><h2>Memória de longo prazo</h2>
      <div class="mute">Este agente não guarda memória entre conversas. Com memória, ele ganha as ferramentas <code>memory__recall</code> e
        <code>memory__remember</code>: grava fatos (clientes, decisões, preferências) num grafo com histórico — um fato que muda não é apagado,
        fica marcado como substituído a partir da data da mudança.</div>
      ${can(a, 'edit') ? `<div class="grid g2 mt"><label>Escopo<select id="mem-scope">${Object.entries(MEM_SCOPE).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label></div>
        <div class="row mt"><button id="mem-on">Ligar memória</button><span class="mute small">Cria uma nova versão da spec; faça deploy em stage para testar.</span></div>` : ''}</div>`;
    const b = $('#mem-on');
    if (b) b.onclick = e => act(e.target, () => api(`/agents/${a.slug}`, { method: 'PATCH', body: { memory: { scope: $('#mem-scope').value } } })
      .then(() => { toast('Memória ligada — faça o deploy em stage'); route(); }));
    return;
  }
  t.innerHTML = '<div class="card mt mute">Carregando memória…</div>';
  let r;
  try { r = await api(`/agents/${a.slug}/memory?env=${env}&q=${encodeURIComponent(q)}&limit=100`); }
  catch (e) { t.innerHTML = `<div class="card mt warn-card">${esc(e.message)}</div>`; return; }
  const facts = r.facts || [];
  t.innerHTML = `<div class="card mt"><div class="row between"><h2>Memória (${facts.length})</h2>
      <div class="row"><select id="mem-env"><option value="prod">Produção</option><option value="stage">Stage</option></select>
        ${can(a, 'manage') ? '<button id="mem-clear" class="danger">Apagar memória</button>' : ''}</div></div>
    <div class="mute small">Escopo: <b>${MEM_SCOPE[r.scope] || esc(r.scope)}</b>${r.write ? '' : ' · só leitura'} · grupo <code>${esc(r.group)}</code>.
      Stage lê a memória de produção, mas grava à parte — testar não suja a produção.</div>
    <div class="row mt"><input id="mem-q" placeholder="Buscar na memória (ex.: plano da ACME)" value="${esc(q)}" style="flex:1"><button id="mem-go" class="ghost">Buscar</button></div>
    ${facts.length ? `<div class="scroll mt"><table><tr><th>Fato</th><th>Vale desde</th><th>Situação</th></tr>
      ${facts.map(f => `<tr><td>${esc(f.fact)}</td><td class="small">${memDate(f.valid_at)}</td>
        <td>${f.current ? `<span class="pill ok">atual</span>${f.invalid_at ? `<div class="mute small">até ${memDate(f.invalid_at)}</div>` : ''}` : `<span class="pill">substituído</span><div class="mute small">em ${memDate(f.invalid_at)}</div>`}</td></tr>`).join('')}</table></div>`
      : `<div class="mute mt">${q ? 'Nada encontrado.' : 'Nenhum fato ainda. O agente grava com memory__remember durante as conversas.'}</div>`}</div>`;
  $('#mem-env').value = env;
  $('#mem-env').onchange = () => agentMemory(t, a, $('#mem-env').value, $('#mem-q').value.trim());
  const search = () => agentMemory(t, a, env, $('#mem-q').value.trim());
  $('#mem-go').onclick = search;
  $('#mem-q').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); search(); } };
  const c = $('#mem-clear');
  if (c) c.onclick = e => confirm(`Apagar toda a memória de ${env === 'prod' ? 'produção' : 'stage'} (grupo ${r.group})? Não dá para desfazer.`) &&
    act(e.target, () => api(`/agents/${a.slug}/memory?env=${env}`, { method: 'DELETE' }).then(() => agentMemory(t, a, env)), 'Memória apagada');
}

/* ---------- extensão Agent Hangar do VS Code: card de download e autorização do login (PKCE) ---------- */
function vscodeCard(a) {
  const base = location.origin;
  const deep = `vscode://agent-hangar.agent-hangar/signin?url=${encodeURIComponent(base)}`;
  return `<div class="card mt"><div class="row between" style="align-items:flex-start"><div><h2>VS Code — extensão Agent Hangar</h2>
    <div class="mute small">${a ? `Com a extensão, <b>${esc(a.name)}</b> e os outros agentes que você pode usar aparecem no seletor de modelos do chat do VS Code.`
      : 'Com a extensão, os agentes que você pode usar aparecem no seletor de modelos do chat do VS Code.'}
      No modo <b>Agent</b>, o agente lê e edita o seu projeto e roda os testes no terminal, com a sua aprovação. A extensão também traz o MCP da plataforma
      (criar e editar agentes sem sair do editor). O login é pelo portal: nada de copiar chave.</div>
    <ol class="small mt"><li>Baixe e instale a extensão (<code>code --install-extension agent-hangar-vscode.vsix</code> ou Extensions → … → Install from VSIX).</li>
      <li>Clique em <b>Abrir no VS Code</b>, confirme aqui no portal e pronto.</li></ol></div>
    <div class="row" style="flex-wrap:nowrap"><a href="/downloads/agent-hangar-vscode.vsix"><button class="ghost">Baixar extensão (.vsix)</button></a>
      <a href="${esc(deep)}"><button>Abrir no VS Code</button></a></div></div></div>`;
}

/* ---------- OAuth do MCP: tela de consentimento (Claude.ai, ChatGPT e outros apps) ---------- */
async function oauthConsentPage() {
  const params = Object.fromEntries(new URLSearchParams(location.hash.split('?')[1] || ''));
  const box = body => { main.innerHTML = `<div class="card" style="max-width:640px;margin:40px auto"><h2>Conectar um app</h2>${body}</div>`; };
  let info;
  try { info = await api('/oauth/consent?' + new URLSearchParams(params)); }
  catch (e) { return box(`<div class="bad-text">${esc(e.message)}</div><p class="small">Comece de novo pelo app.</p>`); }
  if (!info.user) return box('<div class="bad-text">Esta sessão não tem um usuário (sessão de emergência). Entre com a sua conta para conectar o app.</div>');
  box(`<p><b>${esc(info.client_name)}</b> quer usar o Agent Hangar como <b>${esc(info.user.name || info.user.email)}</b>.</p>
    <ul class="small"><li>O app poderá usar o MCP da plataforma com os seus times e papéis: listar, criar, testar e publicar agentes que você pode mexer.</li>
      <li>Depois de autorizar, você volta para <b>${esc(info.redirect_host)}</b>. Se não reconhece este endereço, <b>não autorize</b>.</li>
      <li>O acesso aparece em <a href="#/keys">Minhas chaves → Apps conectados</a>; revogue quando quiser.</li></ul>
    <div class="row mt"><button id="oa-ok">Autorizar</button><button class="ghost" id="oa-no">Recusar</button></div><div id="oa-done" class="mt"></div>`);
  const go = approve => async () => {
    const r = await api('/oauth/consent', { method: 'POST', body: { params, approve } });
    history.replaceState(null, '', location.pathname + '#/');  // o pedido não fica no histórico do navegador
    $('#oa-done').innerHTML = `<span class="small">Voltando para ${esc(info.redirect_host)}…</span>`;
    location.href = r.redirect;
  };
  $('#oa-ok').onclick = e => act(e.target, go(true));
  $('#oa-no').onclick = e => act(e.target, go(false));
}

async function vscodeAuthorizePage() {
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  const challenge = q.get('challenge') || '', state = q.get('state') || '', device = q.get('device') || 'VS Code';
  const who = ME.user ? (ME.user.name || ME.user.email) : null;
  main.innerHTML = `<div class="card" style="max-width:640px;margin:40px auto"><h2>Conectar o VS Code</h2>
    ${!challenge || !state ? '<div class="bad-text">Pedido incompleto. Comece pelo VS Code: comando <b>Agent Hangar: Entrar pelo portal</b>.</div>'
      : !who ? '<div class="bad-text">Esta sessão não tem um usuário (sessão de emergência). Entre com a sua conta para conectar o VS Code.</div>'
      : `<p><b>${esc(device)}</b> quer usar o Agent Hangar como <b>${esc(who)}</b>.</p>
      <ul class="small"><li>Os agentes que você pode usar aparecem no chat do VS Code; o MCP da plataforma age com os seus times e papéis.</li>
        <li>É criada uma chave pessoal (em <a href="#/keys">Minhas chaves</a>), revogada quando você sair da extensão ou perder o acesso.</li></ul>
      <div class="row mt"><button id="vs-ok">Autorizar e voltar ao VS Code</button><a href="#/"><button class="ghost">Cancelar</button></a></div>
      <div id="vs-done" class="mt"></div>`}</div>`;
  const ok = $('#vs-ok');
  if (!ok) return;
  ok.onclick = e => act(e.target, async () => {
    const r = await api('/vscode/authorize', { method: 'POST', body: { challenge, state, device } });
    const url = `${r.redirect}?${new URLSearchParams({ code: r.code, state: r.state })}`;
    history.replaceState(null, '', location.pathname + '#/');  // o código não fica no histórico do navegador
    location.href = url;
    $('#vs-done').innerHTML = `<div class="pill ok">Autorizado</div> <span class="small">Se o VS Code não abriu, <a href="${esc(url)}">clique aqui</a>. Pode fechar esta aba.</span>`;
  });
}
