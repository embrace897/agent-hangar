/* Issues: o quadro kanban do trabalho (pessoas e Digital employees), no portal (/app) e no console (/ui).
   Quadro, lista, tabela, raias e cronograma; a página da issue com propriedades, sub-issues, relações, rodadas e a
   conversa (discussões, reações, @menções). Usa os helpers de app.js ($, api, esc, act, toast, mdLite, icon…). */

const PRIO = { urgent: ['Urgente', 4], high: ['Alta', 3], medium: ['Média', 2], low: ['Baixa', 1], none: ['Sem prioridade', 0] };
const ISS_VIEWS = { board: 'Quadro', list: 'Lista', table: 'Tabela', swim: 'Raias', timeline: 'Cronograma' };
const ISS_LANES = { assignee: 'Responsável', priority: 'Prioridade', project: 'Projeto' };
const ISS = { settings: null, people: {}, poll: null, drag: null, busy: false };
const issLS = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { /* ok */ } return null; };

async function issSettings(force) { if (!ISS.settings || force) ISS.settings = await api('/issues/settings'); return ISS.settings; }
async function issPeople(team = '') {
  if (!ISS.people[team]) ISS.people[team] = await api('/issues/assignees' + (team ? `?team=${encodeURIComponent(team)}` : '')).catch(() => ({ people: [], employees: [] }));
  return ISS.people[team];
}
const issStatus = key => (ISS.settings.statuses.find(s => s.key === key) || { key, name: key, color: '#8793A1', category: 'unstarted' });
const issPill = key => { const s = issStatus(key); return `<span class="iss-pill" style="--c:${esc(s.color)}"><i></i>${esc(window.t(s.name))}</span>`; };
function prioIcon(p) {
  if (p === 'urgent') return `<span class="prio-ic urgent" aria-hidden="true">!</span>`;
  const n = PRIO[p] ? PRIO[p][1] : 0;
  return `<span class="prio-ic" aria-hidden="true">${[1, 2, 3].map(b => `<i class="${b <= n ? 'on' : ''}" style="height:${3 + b * 3}px"></i>`).join('')}</span>`;
}
const prioChip = p => `<span class="prio prio-${esc(p)}">${prioIcon(p)}<span>${esc(window.t((PRIO[p] || PRIO.none)[0]))}</span></span>`;
function issAvatar(a, big) {
  if (!a) return `<span class="iss-av none${big ? ' big' : ''}" title="${esc(window.t('Sem responsável'))}"></span>`;
  if (a.kind === 'employee') return `<span class="iss-av bot${big ? ' big' : ''}" title="${esc(a.name)}">${icon('bot')}</span>`;
  return `<span class="iss-av${big ? ' big' : ''}" title="${esc(a.name)}">${a.avatar_url ? `<img src="${esc(a.avatar_url)}" alt="" referrerpolicy="no-referrer">` : esc((a.name || '?')[0].toUpperCase())}</span>`;
}
function issRun(r) {
  if (!r) return '';
  if (r.waiting_decision) return `<a class="pill warn" href="#/decisions" title="${esc(window.t('O Digital employee espera uma decisão'))}">decisão</a>`;
  if (r.active) return `<span class="iss-live"><i class="dot-live"></i><span>trabalhando</span></span>`;
  if (r.status === 'failed') return '<span class="pill bad">falhou</span>';
  return '';
}
const issDue = i => {
  if (!i.due_date) return '';
  const late = new Date(i.due_date) < new Date() && !['done', 'closed'].includes(i.category);
  return `<span class="iss-due ${late ? 'late' : ''}" title="${esc(window.t('Prazo'))}">${new Date(i.due_date).toLocaleDateString(window.LOCALE, { day: '2-digit', month: 'short' })}</span>`;
};
const issLabels = ls => (ls || []).map(l => `<span class="iss-label" style="--c:${esc(l.color)}" data-noi18n>${esc(l.name)}</span>`).join('');
const mentionHtml = html => html.replace(/@\[([^\]]+)\]\((employee|user):([^)\s]+)\)/g, (m, n, k) => `<span class="mention ${k}">@${n}</span>`);
const issMd = text => mentionHtml(mdLite(text || ''));

function issCard(i) {
  return `<div class="iss-card" draggable="${i.can_edit}" data-key="${esc(i.key)}" data-status="${esc(i.status)}" data-pos="${i.position}">
    <div class="iss-key" data-noi18n>${esc(i.key)}${i.parent ? ` <span class="mute">↳ ${esc(i.parent.key)}</span>` : ''}</div>
    <a class="iss-title" href="#/issues/${esc(i.key)}" data-noi18n>${esc(i.title)}</a>
    ${i.snippet ? `<div class="iss-snip" data-noi18n>${esc(i.snippet)}</div>` : ''}
    <div class="iss-meta">${issAvatar(i.assignee)}${prioChip(i.priority)}${issLabels(i.labels)}${issDue(i)}
      ${i.sub.total ? `<span class="iss-sub" title="${esc(window.t('Sub-issues'))}">${i.sub.done}/${i.sub.total}</span>` : ''}
      ${i.comments ? `<span class="iss-cm" title="${esc(window.t('Comentários'))}">${icon('chat')}${i.comments}</span>` : ''}${issRun(i.run)}</div></div>`;
}

/* ---------- a página do quadro ---------- */
function issQuery() {
  const q = qsOf();
  return { view: q.get('view') || issLS('iss_view') || 'board', team: q.get('team') || '', project: q.get('project') || '',
    assignee: q.get('assignee') || '', priority: q.get('priority') || '', label: q.get('label') || '', q: q.get('q') || '',
    lane: q.get('lane') || issLS('iss_lane') || 'assignee' };
}
function issGo(base, f) {
  const p = new URLSearchParams();
  Object.entries(f).forEach(([k, v]) => { if (v && !(k === 'view' && v === 'board') && !(k === 'lane' && v === 'assignee')) p.set(k, v); });
  location.hash = base + (p.toString() ? '?' + p.toString() : '');
}

async function issuesPage(mine = false) {
  const base = mine ? '#/my-issues' : '#/issues';
  const f = issQuery();
  if (mine) f.assignee = 'me';
  const [st, list, projects] = await Promise.all([issSettings(true), api('/issues?' + new URLSearchParams(
    Object.fromEntries(['team', 'project', 'assignee', 'priority', 'label', 'q'].filter(k => f[k]).map(k => [k, f[k]])))),
    api('/projects').catch(() => [])]);
  const people = await issPeople(f.team);
  const nFilters = ['team', 'project', 'priority', 'label'].filter(k => f[k]).length + (!mine && f.assignee ? 1 : 0);
  main.innerHTML = `<div class="iss-head"><div><h1>${mine ? 'Minhas issues' : 'Issues'}</h1></div>
    <div class="row iss-tools">
      <div class="seg">${Object.entries(ISS_VIEWS).map(([k, v]) => `<button class="ghost small ${f.view === k ? 'on' : ''}" data-view="${k}">${v}</button>`).join('')}</div>
      <details class="iss-pop"><summary class="ghost small btnlike">${icon('target')}<span>Filtro</span>${nFilters ? ` <b>${nFilters}</b>` : ''}</summary><div class="card iss-pop-body">
        <label>Time<select id="if-team"><option value="">${window.t('Todos')}</option>${(ME.teams || []).map(t => `<option value="${esc(t.slug)}" ${f.team === t.slug ? 'selected' : ''} data-noi18n>${esc(t.name)}</option>`).join('')}</select></label>
        <label>Projeto<select id="if-project"><option value="">${window.t('Todos')}</option>${projects.map(p => `<option value="${esc(p.slug)}" ${f.project === p.slug ? 'selected' : ''} data-noi18n>${esc(p.name)}</option>`).join('')}</select></label>
        ${mine ? '' : `<label>Responsável<select id="if-assignee"><option value="">${window.t('Todos')}</option><option value="me" ${f.assignee === 'me' ? 'selected' : ''}>${window.t('Eu')}</option><option value="none" ${f.assignee === 'none' ? 'selected' : ''}>${window.t('Sem responsável')}</option>
          ${issAssigneeOptions(people, f.assignee)}</select></label>`}
        <label>Prioridade<select id="if-priority"><option value="">${window.t('Todas')}</option>${Object.entries(PRIO).map(([k, v]) => `<option value="${k}" ${f.priority === k ? 'selected' : ''}>${window.t(v[0])}</option>`).join('')}</select></label>
        <label>Etiqueta<select id="if-label"><option value="">${window.t('Todas')}</option>${st.labels.map(l => `<option value="${l.id}" ${String(f.label) === String(l.id) ? 'selected' : ''} data-noi18n>${esc(l.name)}</option>`).join('')}</select></label>
        <div class="row mt"><button class="small" id="if-apply">Aplicar</button><button class="ghost small" id="if-clear">Limpar</button></div></div></details>
      ${f.view === 'swim' ? `<label class="small row" style="gap:6px">Raias<select id="if-lane" style="width:auto">${Object.entries(ISS_LANES).map(([k, v]) => `<option value="${k}" ${f.lane === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>` : ''}
      <input id="iss-q" type="search" placeholder="Buscar… ( / )" value="${esc(f.q)}" style="width:180px">
      <span class="mute small"><b>${list.length}</b> <span>issues</span></span>
      ${!USER_MODE && ME.is_admin ? `<a href="#/issues/settings" class="ghost small btnlike" title="${esc(window.t('Configurar o quadro'))}">${icon('userCog')}</a>` : ''}
      <button id="iss-new">${icon('plus')}<span>Nova issue</span></button></div></div>
  <div id="iss-body"></div>`;
  const draw = rows => { $('#iss-body').innerHTML = issRender(f, rows, st); issBind(f, rows, base); };
  draw(list);
  main.querySelectorAll('[data-view]').forEach(b => b.onclick = () => { issLS('iss_view', b.dataset.view); issGo(base, { ...f, view: b.dataset.view }); });
  const val = id => ($(id) || {}).value || '';
  $('#if-apply').onclick = () => issGo(base, { ...f, team: val('#if-team'), project: val('#if-project'), assignee: mine ? '' : val('#if-assignee'),
    priority: val('#if-priority'), label: val('#if-label') });
  $('#if-clear').onclick = () => issGo(base, { view: f.view, lane: f.lane });
  const lane = $('#if-lane'); if (lane) lane.onchange = () => { issLS('iss_lane', lane.value); issGo(base, { ...f, lane: lane.value }); };
  let tq; $('#iss-q').oninput = e => { clearTimeout(tq); tq = setTimeout(() => issGo(base, { ...f, q: e.target.value.trim() }), 400); };
  $('#iss-new').onclick = () => issNew({ team: f.team, project: f.project, assignee: mine ? 'me' : '' });
  // tempo real: a cada 8 s, enquanto ninguém arrasta nem escreve
  clearInterval(ISS.poll);
  const here = location.hash;
  ISS.poll = setInterval(async () => {
    if (location.hash !== here) { clearInterval(ISS.poll); return; }
    if (document.hidden || ISS.drag || ISS.busy || $('.modal-bg') || document.activeElement.matches('input,textarea,select')) return;
    try {
      const fresh = await api('/issues?' + new URLSearchParams(Object.fromEntries(['team', 'project', 'assignee', 'priority', 'label', 'q'].filter(k => f[k]).map(k => [k, f[k]]))));
      draw(fresh);
    } catch (e) { /* a próxima tenta de novo */ }
  }, 8000);
}

function issAssigneeOptions(p, cur) {
  return `${p.employees.length ? `<optgroup label="${esc(window.t('Digital employees'))}">${p.employees.map(e => `<option value="${esc(e.ref)}" ${cur === e.ref ? 'selected' : ''} data-noi18n>🤖 ${esc(e.name)}</option>`).join('')}</optgroup>` : ''}
    ${p.people.length ? `<optgroup label="${esc(window.t('Pessoas'))}">${p.people.map(u => `<option value="${esc(u.ref)}" ${cur === u.ref ? 'selected' : ''} data-noi18n>${esc(u.name)}</option>`).join('')}</optgroup>` : ''}`;
}

function issRender(f, rows, st) {
  const cols = st.statuses.filter(s => !s.archived);
  const byPos = (a, b) => (a.position - b.position) || (b.number - a.number);
  if (f.view === 'list') {
    return cols.map(s => { const r = rows.filter(i => i.status === s.key).sort(byPos); return r.length ? `<div class="card iss-group"><div class="row">${issPill(s.key)}<span class="mute small">${r.length}</span></div>
      <table class="iss-rows">${r.map(i => `<tr><td class="iss-key" data-noi18n>${esc(i.key)}</td><td>${prioIcon(i.priority)}</td><td><a href="#/issues/${esc(i.key)}" data-noi18n>${esc(i.title)}</a> ${issLabels(i.labels)} ${issRun(i.run)}</td>
        <td>${issDue(i)}</td><td>${issAvatar(i.assignee)}</td><td class="mute small">${when(i.updated_at)}</td></tr>`).join('')}</table></div>` : ''; }).join('') || issEmpty();
  }
  if (f.view === 'table') {
    const sortKey = issLS('iss_sort') || 'updated_at';
    const cmp = { priority: (a, b) => PRIO[b.priority][1] - PRIO[a.priority][1], number: (a, b) => b.number - a.number,
      due_date: (a, b) => (a.due_date || '9').localeCompare(b.due_date || '9'), status: (a, b) => cols.findIndex(s => s.key === a.status) - cols.findIndex(s => s.key === b.status),
      updated_at: (a, b) => (b.updated_at || '').localeCompare(a.updated_at || '') }[sortKey] || (() => 0);
    const th = (k, l) => `<th class="sortable ${sortKey === k ? 'on' : ''}" data-sort="${k}">${l}</th>`;
    return rows.length ? `<div class="card scroll"><table class="iss-table"><tr>${th('number', 'Chave')}<th>Título</th>${th('status', 'Status')}${th('priority', 'Prioridade')}<th>Responsável</th><th>Etiquetas</th><th>Projeto</th>${th('due_date', 'Prazo')}${th('updated_at', 'Atualizada')}</tr>
      ${[...rows].sort(cmp).map(i => `<tr><td class="iss-key" data-noi18n>${esc(i.key)}</td><td><a href="#/issues/${esc(i.key)}" data-noi18n>${esc(i.title)}</a> ${issRun(i.run)}</td><td>${issPill(i.status)}</td><td>${prioChip(i.priority)}</td>
        <td>${i.assignee ? `${issAvatar(i.assignee)} <span class="small" data-noi18n>${esc(i.assignee.name)}</span>` : '<span class="mute">—</span>'}</td><td>${issLabels(i.labels)}</td>
        <td class="small" data-noi18n>${esc(i.project ? i.project.name : '')}</td><td>${issDue(i)}</td><td class="mute small">${when(i.updated_at)}</td></tr>`).join('')}</table></div>` : issEmpty();
  }
  if (f.view === 'timeline') return issTimeline(rows);
  if (f.view === 'swim') {
    const laneOf = i => f.lane === 'priority' ? i.priority : f.lane === 'project' ? (i.project ? i.project.slug : '') : (i.assignee ? i.assignee.ref : '');
    const laneName = (k, i) => f.lane === 'priority' ? window.t(PRIO[k][0]) : f.lane === 'project' ? (i.project ? i.project.name : window.t('Sem projeto')) : (i.assignee ? i.assignee.name : window.t('Sem responsável'));
    const lanes = new Map();
    rows.forEach(i => { const k = laneOf(i); if (!lanes.has(k)) lanes.set(k, { name: laneName(k, i), av: f.lane === 'assignee' ? issAvatar(i.assignee) : f.lane === 'priority' ? prioIcon(i.priority) : '', rows: [] }); lanes.get(k).rows.push(i); });
    if (!lanes.size) return issEmpty();
    return `<div class="iss-swim">${[...lanes.entries()].map(([k, l]) => `<div class="iss-lane"><div class="iss-lane-h">${l.av}<b data-noi18n>${esc(l.name)}</b><span class="mute small">${l.rows.length}</span></div>
      <div class="iss-board compact">${cols.map(s => `<section class="iss-col" style="--c:${esc(s.color)}"><div class="iss-col-body" data-status="${esc(s.key)}" data-lane="${esc(k)}">
        ${l.rows.filter(i => i.status === s.key).sort(byPos).map(issCard).join('')}</div></section>`).join('')}</div></div>`).join('')}
      <div class="iss-board compact iss-lane-cols">${cols.map(s => `<div class="iss-col-h">${issPill(s.key)}</div>`).join('')}</div></div>`;
  }
  return `<div class="iss-board">${cols.map(s => { const r = rows.filter(i => i.status === s.key).sort(byPos); return `<section class="iss-col" style="--c:${esc(s.color)}">
    <div class="iss-col-h">${issPill(s.key)}<span class="mute small">${r.length}</span><button class="ghost small iss-col-add" data-status="${esc(s.key)}" title="${esc(window.t('Nova issue neste status'))}">+</button></div>
    <div class="iss-col-body" data-status="${esc(s.key)}">${r.map(issCard).join('')}</div></section>`; }).join('')}</div>`;
}

const issEmpty = () => `<div class="card empty"><div class="mute">Nenhuma issue aqui. Crie a primeira com <b>Nova issue</b> (ou a tecla <kbd>C</kbd>) — atribua a um Digital employee e ele começa a trabalhar.</div></div>`;

function issTimeline(rows) {
  const dated = rows.filter(i => i.start_date || i.due_date);
  const rest = rows.length - dated.length;
  if (!dated.length) return `<div class="card empty"><div class="mute">Nenhuma issue com início ou prazo. Defina as datas na página da issue para vê-las no cronograma.</div></div>`;
  const day = 86400000;
  const s = i => new Date(i.start_date || i.created_at).getTime(), e = i => Math.max(new Date(i.due_date || i.start_date).getTime(), s(i) + day);
  const lo = Math.min(...dated.map(s)) - day, hi = Math.max(...dated.map(e)) + day, span = hi - lo;
  const ticks = []; for (let t = lo; t <= hi; t += Math.max(day, Math.round(span / 10 / day) * day)) ticks.push(t);
  const todayX = (Date.now() - lo) / span * 100;
  return `<div class="card iss-tl"><div class="iss-tl-axis">${ticks.map(t => `<span style="left:${(t - lo) / span * 100}%">${new Date(t).toLocaleDateString(window.LOCALE, { day: '2-digit', month: 'short' })}</span>`).join('')}</div>
    ${dated.sort((a, b) => s(a) - s(b)).map(i => `<div class="iss-tl-row"><a href="#/issues/${esc(i.key)}" class="iss-tl-name"><span class="iss-key" data-noi18n>${esc(i.key)}</span> <span data-noi18n>${esc(i.title)}</span></a>
      <div class="iss-tl-track">${todayX >= 0 && todayX <= 100 ? `<i class="iss-tl-today" style="left:${todayX}%"></i>` : ''}<a href="#/issues/${esc(i.key)}" class="iss-tl-bar" style="left:${(s(i) - lo) / span * 100}%;width:${Math.max(1.5, (e(i) - s(i)) / span * 100)}%;--c:${esc(issStatus(i.status).color)}" title="${esc(issStatus(i.status).name)}"></a></div></div>`).join('')}
    ${rest ? `<div class="mute small mt">${rest} <span>issue(s) sem datas não aparecem aqui.</span></div>` : ''}</div>`;
}

function issBind(f, rows, base) {
  const byKey = Object.fromEntries(rows.map(i => [i.key, i]));
  main.querySelectorAll('.iss-col-add').forEach(b => b.onclick = () => issNew({ team: f.team, project: f.project, status: b.dataset.status }));
  main.querySelectorAll('.sortable').forEach(h => h.onclick = () => { issLS('iss_sort', h.dataset.sort); route(); });
  main.querySelectorAll('.iss-card[draggable="true"]').forEach(c => {
    c.ondragstart = e => { ISS.drag = c.dataset.key; e.dataTransfer.setData('text/plain', c.dataset.key); e.dataTransfer.effectAllowed = 'move'; setTimeout(() => c.classList.add('dragging'), 0); };
    c.ondragend = () => { c.classList.remove('dragging'); ISS.drag = null; main.querySelectorAll('.drop-into').forEach(x => x.classList.remove('drop-into')); main.querySelectorAll('.iss-drop-mark').forEach(x => x.remove()); };
  });
  const after = (body, y) => [...body.querySelectorAll('.iss-card:not(.dragging)')].find(c => { const r = c.getBoundingClientRect(); return y < r.top + r.height / 2; });
  main.querySelectorAll('.iss-col-body').forEach(body => {
    body.ondragover = e => {
      if (!ISS.drag) return;
      e.preventDefault(); body.classList.add('drop-into');
      main.querySelectorAll('.iss-drop-mark').forEach(x => x.remove());
      const mark = document.createElement('div'); mark.className = 'iss-drop-mark';
      const nxt = after(body, e.clientY); nxt ? body.insertBefore(mark, nxt) : body.append(mark);
    };
    body.ondragleave = e => { if (!body.contains(e.relatedTarget)) { body.classList.remove('drop-into'); body.querySelectorAll('.iss-drop-mark').forEach(x => x.remove()); } };
    body.ondrop = async e => {
      e.preventDefault();
      const key = ISS.drag || e.dataTransfer.getData('text/plain'); const i = byKey[key];
      const mark = body.querySelector('.iss-drop-mark');
      const prev = mark && mark.previousElementSibling && mark.previousElementSibling.classList.contains('iss-card') ? mark.previousElementSibling : null;
      const next = mark && mark.nextElementSibling && mark.nextElementSibling.classList.contains('iss-card') ? mark.nextElementSibling : null;
      const p = prev ? Number(prev.dataset.pos) : null, n = next ? Number(next.dataset.pos) : null;
      const pos = p != null && n != null ? (p + n) / 2 : p != null ? p + 1 : n != null ? n - 1 : 0;
      ISS.drag = null; body.classList.remove('drop-into'); main.querySelectorAll('.iss-drop-mark').forEach(x => x.remove());
      if (!i) return;
      const changes = { position: pos };
      if (body.dataset.status !== i.status) changes.status = body.dataset.status;
      if (body.dataset.lane !== undefined) {
        const lane = body.dataset.lane;
        if (f.lane === 'assignee' && lane !== (i.assignee ? i.assignee.ref : '')) changes.assignee = lane;
        if (f.lane === 'priority' && lane !== i.priority) changes.priority = lane;
        if (f.lane === 'project' && lane !== (i.project ? i.project.slug : '')) changes.project = lane;
      }
      const toEmployee = (changes.assignee || (i.assignee && i.assignee.ref) || '').startsWith('employee:');
      const st = issStatus(changes.status || i.status);
      if (toEmployee && !(i.run && i.run.active) && st.key !== 'backlog' && ['unstarted', 'started'].includes(st.category) &&
          ((changes.status && i.status === 'backlog') || changes.assignee)) {
        const ans = await issAsk('Começar agora?', 'O Digital employee responsável começa a trabalhar nesta issue assim que ela sair do backlog (com a alçada e o orçamento dele).',
          [['start', 'Iniciar agora'], ['later', 'Não iniciar ainda']]);
        if (!ans) return;
        changes.start = ans === 'start';
      }
      const card = main.querySelector(`.iss-card[data-key="${CSS.escape(key)}"]`);
      if (card) { mark ? body.insertBefore(card, next) : body.append(card); card.dataset.pos = pos; }  // otimista
      try {
        ISS.busy = true;
        const r = await api(`/issues/${encodeURIComponent(key)}`, { method: 'PATCH', body: changes });
        if (r.run_started) toast(window.t('O Digital employee começou a trabalhar'));
      } catch (err) { toast(err.message, true); } finally { ISS.busy = false; route(); }
    };
  });
}

/* ---------- janelinha de pergunta e a nova issue ---------- */
function issModal(html, onClose) {
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal card" role="dialog" aria-modal="true">${html}</div>`;
  document.body.append(bg);
  const close = v => { bg.remove(); document.removeEventListener('keydown', esc_); if (onClose) onClose(v); };
  const esc_ = e => { if (e.key === 'Escape') close(null); };
  document.addEventListener('keydown', esc_);
  bg.onclick = e => { if (e.target === bg) close(null); };
  return { el: bg.firstElementChild, close };
}
function issAsk(title, text, buttons) {
  return new Promise(res => {
    const m = issModal(`<h2>${esc(window.t(title))}</h2><div class="mute small">${esc(window.t(text))}</div>
      <div class="row mt">${buttons.map(([v, l], k) => `<button class="${k ? 'ghost' : ''}" data-v="${v}">${esc(window.t(l))}</button>`).join('')}<button class="ghost" data-v="">Cancelar</button></div>`, v => res(v));
    m.el.querySelectorAll('[data-v]').forEach(b => b.onclick = () => m.close(b.dataset.v || null));
  });
}

async function issNew(d = {}) {
  const st = await issSettings();
  const teams = ME.teams || [];
  const team = d.team || (teams.length === 1 ? teams[0].slug : '');
  const [people, projects] = await Promise.all([issPeople(team), api('/projects').catch(() => [])]);
  const m = issModal(`<div class="row between"><h2>Nova issue</h2><span class="mute small">Ctrl+Enter cria</span></div>
    <input id="in-title" class="iss-big" placeholder="Título da issue" autocomplete="off">
    <textarea id="in-desc" rows="5" placeholder="Descrição (markdown): objetivo, contexto, critérios de aceite…"></textarea>
    <div class="iss-props mt">
      <label>Status<select id="in-status">${st.statuses.filter(s => !s.archived).map(s => `<option value="${esc(s.key)}" ${(d.status || '') === s.key ? 'selected' : ''}>${esc(window.t(s.name))}</option>`).join('')}</select></label>
      <label>Prioridade<select id="in-prio">${Object.entries(PRIO).map(([k, v]) => `<option value="${k}" ${k === 'none' ? 'selected' : ''}>${window.t(v[0])}</option>`).join('')}</select></label>
      <label>Responsável<select id="in-assignee"><option value="">${window.t('Sem responsável')}</option><option value="me" ${d.assignee === 'me' ? 'selected' : ''}>${window.t('Eu')}</option>${issAssigneeOptions(people, d.assignee)}</select></label>
      <label>Time<select id="in-team"><option value="">${window.t('Sem time (a empresa toda)')}</option>${teams.map(t => `<option value="${esc(t.slug)}" ${team === t.slug ? 'selected' : ''} data-noi18n>${esc(t.name)}</option>`).join('')}</select></label>
      <label>Projeto<select id="in-project"></select></label>
      <label>Prazo<input id="in-due" type="date"></label>
      <label>Etiquetas<select id="in-labels" multiple size="3">${st.labels.map(l => `<option value="${l.id}" data-noi18n>${esc(l.name)}</option>`).join('')}</select></label>
      <label>Issue mãe<input id="in-parent" placeholder="ex.: ${esc(st.prefix)}-12" value="${esc(d.parent || '')}"></label>
    </div>
    <label class="row small mt" id="in-start-box" hidden style="gap:6px"><input type="checkbox" id="in-start" checked style="width:auto"> Começar agora (o Digital employee trabalha nela assim que for criada)</label>
    <div class="row between mt"><label class="row small" style="gap:6px"><input type="checkbox" id="in-more" style="width:auto"> Criar mais uma</label>
      <div class="row"><button class="ghost" id="in-cancel">Cancelar</button><button id="in-go">Criar issue</button></div></div>`);
  const el = m.el;
  $('#in-title', el).focus();
  const sync = () => { $('#in-start-box', el).hidden = !$('#in-assignee', el).value.startsWith('employee:') || $('#in-status', el).value === 'backlog'; };
  $('#in-assignee', el).onchange = sync; $('#in-status', el).onchange = sync; sync();
  const projOpts = () => { const t = $('#in-team', el).value;  // só os projetos do time escolhido (e os da empresa)
    $('#in-project', el).innerHTML = `<option value="">—</option>${projects.filter(p => !p.team || !t || p.team === t).map(p => `<option value="${esc(p.slug)}" ${d.project === p.slug ? 'selected' : ''} data-noi18n>${esc(p.name)}</option>`).join('')}`; };
  projOpts();
  $('#in-team', el).onchange = async () => { const p = await issPeople($('#in-team', el).value); const cur = $('#in-assignee', el).value;
    $('#in-assignee', el).innerHTML = `<option value="">${window.t('Sem responsável')}</option><option value="me">${window.t('Eu')}</option>${issAssigneeOptions(p, cur)}`; sync(); projOpts(); };
  $('#in-cancel', el).onclick = () => m.close(null);
  const go = async () => {
    const body = { title: $('#in-title', el).value.trim(), description: $('#in-desc', el).value, status: $('#in-status', el).value,
      priority: $('#in-prio', el).value, assignee: $('#in-assignee', el).value, team: $('#in-team', el).value, project: $('#in-project', el).value,
      due_date: $('#in-due', el).value || null, labels: [...$('#in-labels', el).selectedOptions].map(o => Number(o.value)),
      parent: $('#in-parent', el).value.trim(), start: $('#in-start', el).checked };
    if (!body.title) { toast(window.t('Dê um título à issue'), true); return; }
    const r = await act($('#in-go', el), () => api('/issues', { method: 'POST', body }));
    if (!r) return;
    toast(`${r.key} ${window.t('criada')}${r.run_started ? ' · ' + window.t('o Digital employee começou') : ''}`);
    if ($('#in-more', el).checked) { $('#in-title', el).value = ''; $('#in-desc', el).value = ''; $('#in-title', el).focus(); }
    else { m.close(r); if (d.parent) route(); else location.hash = `#/issues/${r.key}`; }
  };
  $('#in-go', el).onclick = go;
  el.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); go(); } });
}

/* ---------- a issue ---------- */
const ISS_ACT = {
  created: () => 'criou a issue', description: () => 'editou a descrição',
  title: d => `<span>mudou o título para</span> “<span data-noi18n>${esc(d.to)}</span>”`,
  status: d => `<span>mudou o status de</span> ${issPill(d.from)} <span>para</span> ${issPill(d.to)}`,
  priority: d => `<span>mudou a prioridade para</span> ${prioChip(d.to)}`,
  assignee: d => d.to ? `<span>atribuiu a</span> <b data-noi18n>${esc(d.to.replace(/^(employee|user):/, ''))}</b>` : 'tirou o responsável',
  labels: () => 'mudou as etiquetas',
  project: d => d.to ? `<span>moveu para o projeto</span> <b data-noi18n>${esc(d.to)}</b>` : 'tirou do projeto',
  team: d => d.to ? `<span>moveu para o time</span> <b data-noi18n>${esc(d.to)}</b>` : 'tirou do time',
  parent: d => d.to ? `<span>virou sub-issue de</span> <b data-noi18n>${esc(d.to)}</b>` : 'deixou de ser sub-issue',
  stage: d => `<span>definiu a etapa</span> ${esc(d.to ?? '—')}`, estimate: d => `<span>estimou em pontos</span> ${esc(d.to ?? '—')}`,
  start_date: d => `<span>definiu o início</span> ${esc(d.to || '—')}`, due_date: d => `<span>definiu o prazo</span> ${esc(d.to || '—')}`,
  relation: d => `<span>relacionou com</span> <b data-noi18n>${esc(d.other)}</b> (<span>${esc(window.t({ blocks: 'bloqueia', blocked_by: 'bloqueada por', related: 'relacionada', duplicate: 'duplicada de' }[d.kind] || d.kind))}</span>)`,
  unrelation: () => 'removeu uma relação',
  mention: d => `<span>mencionou</span> <b data-noi18n>${esc(d.user)}</b>`, comment_deleted: () => 'apagou um comentário',
  run_started: d => `<span>pediu a</span> <b data-noi18n>${esc(d.employee_name || d.employee)}</b> — <a href="#/tasks/${d.task}"><span>rodada</span> #${d.task}</a>`,
  run_done: d => `<span>terminou a</span> <a href="#/tasks/${d.task}"><span>rodada</span> #${d.task}</a>`,
  run_failed: d => `<span class="bad-text"><a href="#/tasks/${d.task}"><span>rodada</span> #${d.task}</a> <span>falhou</span></span>`,
  run_cancelled: d => `<a href="#/tasks/${d.task}"><span>rodada</span> #${d.task}</a> <span>foi cancelada</span>`,
  stage_done: d => `<span>terminou a etapa das sub-issues</span> ${esc(d.stage || '')}${d.next ? '' : ' <span>(todas)</span>'}`,
};
const issActor = a => a.startsWith('employee:') ? `${icon('bot')}<b data-noi18n>${esc(a.slice(9))}</b>` : `<b data-noi18n>${esc(a)}</b>`;

async function issueDetail(key) {
  const [st, d] = await Promise.all([issSettings(), api(`/issues/${encodeURIComponent(key)}`)]);
  const [people, projects] = await Promise.all([issPeople(d.team ? d.team.slug : ''), api('/projects').catch(() => [])]);
  ISS.cur = d;
  const ed = d.can_edit;
  const opt = (v, cur, l) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${l}</option>`;
  const assigneeRef = d.assignee ? d.assignee.ref : '';
  main.innerHTML = `<div class="iss-crumb small"><a href="#/issues">Issues</a> ›${d.parent ? ` <a href="#/issues/${esc(d.parent.key)}" data-noi18n>${esc(d.parent.key)}</a> ›` : ''} <span data-noi18n>${esc(d.key)}</span></div>
  <div class="iss-page">
    <div class="iss-main">
      <input id="id-title" class="iss-big iss-title-in" value="${esc(d.title)}" ${ed ? '' : 'readonly'} data-noi18n>
      <div class="card iss-desc"><div class="row between"><span class="mute small">Descrição</span>${ed ? '<button class="ghost small" id="id-desc-edit">Editar</button>' : ''}</div>
        <div id="id-desc" class="md">${d.description ? issMd(d.description) : '<span class="mute">Sem descrição.</span>'}</div></div>
      <div class="card mt"><div class="row between"><h2><span>Sub-issues</span> ${d.sub.total ? `<span class="mute small">${d.sub.done}/${d.sub.total}</span>` : ''}</h2>${ed ? '<button class="ghost small" id="id-sub-add">+ Sub-issue</button>' : ''}</div>
        ${d.sub_issues.length ? `<table class="iss-rows">${d.sub_issues.map(i => `<tr><td class="iss-key" data-noi18n>${esc(i.key)}</td><td>${i.stage ? `<span class="chip">${window.t('etapa')} ${i.stage}</span>` : ''}</td>
          <td><a href="#/issues/${esc(i.key)}" data-noi18n>${esc(i.title)}</a> ${issRun(i.run)}</td><td>${issPill(i.status)}</td><td>${issAvatar(i.assignee)}</td></tr>`).join('')}</table>`
          : '<div class="mute small">Divida o trabalho em sub-issues — com etapas, a próxima só começa quando a anterior termina.</div>'}
        <div id="id-sub-form" hidden class="row mt"><input id="id-sub-title" placeholder="Título da sub-issue" style="flex:1"><input id="id-sub-stage" type="number" min="1" placeholder="etapa" style="width:80px">
          <select id="id-sub-assignee" style="width:auto"><option value="">${window.t('Sem responsável')}</option>${issAssigneeOptions(people, '')}</select><button id="id-sub-go">Criar</button></div></div>
      <div class="card mt"><h2>Atividade</h2><div id="id-tl"></div>
        ${ed ? `<div class="iss-composer mt"><textarea id="id-cm" rows="3" placeholder="Comente… @ menciona uma pessoa ou um Digital employee (ele começa a trabalhar no pedido)"></textarea>
          <div class="row between"><span class="mute small">Markdown · Ctrl+Enter envia</span><button id="id-cm-go">Comentar</button></div></div>` : ''}</div>
    </div>
    <aside class="iss-side card">
      <label>Status<select id="ip-status" ${ed ? '' : 'disabled'}>${st.statuses.filter(s => !s.archived || s.key === d.status).map(s => opt(s.key, d.status, esc(window.t(s.name)))).join('')}</select></label>
      <label>Prioridade<select id="ip-priority" ${ed ? '' : 'disabled'}>${Object.entries(PRIO).map(([k, v]) => opt(k, d.priority, window.t(v[0]))).join('')}</select></label>
      <label>Responsável<select id="ip-assignee" ${ed ? '' : 'disabled'}><option value="">${window.t('Sem responsável')}</option>${issAssigneeOptions(people, assigneeRef)}
        ${assigneeRef && ![...people.people, ...people.employees].some(x => x.ref === assigneeRef) ? `<option value="${esc(assigneeRef)}" selected data-noi18n>${esc(d.assignee.name)}</option>` : ''}</select></label>
      ${d.assignee && d.assignee.kind === 'employee' && ed && !(d.run && d.run.active) && !['done', 'closed'].includes(d.category) ? `<button class="small" id="ip-run">${icon('bot')}<span>Pedir para trabalhar agora</span></button>` : ''}
      ${d.run ? `<div class="small">${issRun(d.run)} <a href="#/tasks/${d.run.task_id}">${window.t('rodada')} #${d.run.task_id}</a></div>` : ''}
      <label>Etiquetas<div class="iss-labels-edit">${issLabels(d.labels)}${ed ? `<select id="ip-label-add" style="width:auto"><option value="">+</option>${st.labels.filter(l => !d.labels.some(x => x.id === l.id)).map(l => `<option value="${l.id}" data-noi18n>${esc(l.name)}</option>`).join('')}<option value="new">${window.t('Nova etiqueta…')}</option></select>` : ''}
        ${d.labels.length && ed ? `<button class="ghost small" id="ip-label-clear" title="${esc(window.t('Tirar as etiquetas'))}">×</button>` : ''}</div></label>
      <label>Projeto<select id="ip-project" ${ed ? '' : 'disabled'}><option value="">—</option>${projects.filter(p => !p.team || !d.team || p.team === d.team.slug || (d.project && d.project.slug === p.slug)).map(p => opt(p.slug, d.project ? d.project.slug : '', esc(p.name))).join('')}</select></label>
      <label>Time<select id="ip-team" ${ed ? '' : 'disabled'}><option value="">${window.t('Sem time (a empresa toda)')}</option>${(ME.teams || []).map(t => opt(t.slug, d.team ? d.team.slug : '', esc(t.name))).join('')}
        ${d.team && !(ME.teams || []).some(t => t.slug === d.team.slug) ? `<option value="${esc(d.team.slug)}" selected data-noi18n>${esc(d.team.name)}</option>` : ''}</select></label>
      <div class="grid g2"><label>Início<input id="ip-start" type="date" value="${d.start_date ? d.start_date.slice(0, 10) : ''}" ${ed ? '' : 'disabled'}></label>
        <label>Prazo<input id="ip-due" type="date" value="${d.due_date ? d.due_date.slice(0, 10) : ''}" ${ed ? '' : 'disabled'}></label></div>
      <div class="grid g2"><label>Estimativa<input id="ip-estimate" type="number" min="0" value="${d.estimate ?? ''}" ${ed ? '' : 'disabled'}></label>
        <label>Etapa<input id="ip-stage" type="number" min="1" value="${d.stage ?? ''}" ${ed ? '' : 'disabled'}></label></div>
      <label>Issue mãe<input id="ip-parent" value="${esc(d.parent ? d.parent.key : '')}" placeholder="${esc(st.prefix)}-12" ${ed ? '' : 'disabled'}></label>
      <div class="iss-side-sec"><div class="mute small">Relações</div>
        ${d.relations.map(r => `<div class="row small" style="gap:6px"><span class="mute">${esc(window.t({ blocks: 'bloqueia', blocked_by: 'bloqueada por', related: 'relacionada', duplicate: 'duplicada de' }[r.kind] || r.kind))}</span>
          <a href="#/issues/${esc(r.key)}" data-noi18n>${esc(r.key)}</a>${issPill(r.status)}${ed ? `<button class="ghost small ip-unrel" data-id="${r.id}">×</button>` : ''}</div>`).join('') || '<div class="mute small">—</div>'}
        ${ed ? `<div class="row small mt" style="gap:4px"><select id="ip-rel-kind" style="width:auto"><option value="blocks">${window.t('bloqueia')}</option><option value="blocked_by">${window.t('bloqueada por')}</option><option value="related">${window.t('relacionada')}</option><option value="duplicate">${window.t('duplicada de')}</option></select>
          <input id="ip-rel-key" placeholder="${esc(st.prefix)}-12" style="width:90px"><button class="ghost small" id="ip-rel-go">+</button></div>` : ''}</div>
      <div class="iss-side-sec"><div class="mute small">Rodadas dos Digital employees</div>
        ${d.runs.map(r => `<div class="small"><a href="#/tasks/${r.task_id}">#${r.task_id}</a> <span data-noi18n>${esc(r.employee_name)}</span> ${pill(r.status)} <span class="mute">${usd(r.cost_usd)}</span></div>`).join('') || '<div class="mute small">—</div>'}</div>
      <div class="iss-side-sec"><button class="ghost small" id="ip-sub">${d.subscribed ? 'Cancelar inscrição' : 'Inscrever-se'}</button>
        <div class="mute small">${d.subscribers.length} <span>inscrito(s): veem a atividade na caixa de entrada.</span></div></div>
      <div class="mute small"><span>Criada</span> ${when(d.created_at)}${d.creator ? ` <span>por</span> <span data-noi18n>${esc(d.creator.replace(/^employee:/, ''))}</span>` : ''}</div>
      ${d.can_delete ? '<button class="ghost danger small" id="ip-del">Apagar issue</button>' : ''}
    </aside></div>`;
  issTimelineRender(d);
  if (!ed) return;
  const save = async (changes, msg) => {
    try { ISS.busy = true; const r = await api(`/issues/${encodeURIComponent(d.key)}`, { method: 'PATCH', body: changes }); if (r.run_started) toast(window.t('O Digital employee começou a trabalhar')); else if (msg) toast(window.t(msg)); }
    catch (e) { toast(e.message, true); } finally { ISS.busy = false; route(); }
  };
  const t = $('#id-title');
  t.onkeydown = e => { if (e.key === 'Enter') t.blur(); if (e.key === 'Escape') { t.value = d.title; t.blur(); } };
  t.onblur = () => { if (t.value.trim() && t.value.trim() !== d.title) save({ title: t.value.trim() }); };
  $('#id-desc-edit').onclick = () => {
    $('#id-desc').innerHTML = `<textarea id="id-desc-in" rows="10">${esc(d.description)}</textarea><div class="row mt"><button class="small" id="id-desc-save">Salvar</button><button class="ghost small" id="id-desc-cancel">Cancelar</button></div>`;
    issMentions($('#id-desc-in'), people);
    $('#id-desc-in').focus();
    $('#id-desc-save').onclick = () => save({ description: $('#id-desc-in').value });
    $('#id-desc-cancel').onclick = () => route();
  };
  const startAsk = async (changes, emp) => {
    if (emp && !(d.run && d.run.active)) {
      const ans = await issAsk('Começar agora?', 'O Digital employee começa a trabalhar nesta issue agora (com a alçada e o orçamento dele), ou fica atribuído para começar depois.',
        [['start', 'Iniciar agora'], ['later', 'Não iniciar ainda']]);
      if (!ans) { route(); return; }
      changes.start = ans === 'start';
    }
    save(changes);
  };
  $('#ip-status').onchange = e => {
    const s = issStatus(e.target.value);
    const emp = d.assignee && d.assignee.kind === 'employee' && d.status === 'backlog' && s.key !== 'backlog' && ['unstarted', 'started'].includes(s.category);
    startAsk({ status: e.target.value }, emp);
  };
  $('#ip-priority').onchange = e => save({ priority: e.target.value });
  $('#ip-assignee').onchange = e => startAsk({ assignee: e.target.value }, e.target.value.startsWith('employee:') && d.status !== 'backlog' && ['unstarted', 'started'].includes(d.category));
  const run = $('#ip-run'); if (run) run.onclick = ev => act(ev.target, () => api(`/issues/${encodeURIComponent(d.key)}/run`, { method: 'POST', body: {} }).then(route), 'O Digital employee começou a trabalhar');
  $('#ip-project').onchange = e => save({ project: e.target.value });
  $('#ip-team').onchange = e => save({ team: e.target.value });
  [['#ip-start', 'start_date'], ['#ip-due', 'due_date'], ['#ip-estimate', 'estimate'], ['#ip-stage', 'stage']].forEach(([id, k]) => { $(id).onchange = e => save({ [k]: e.target.value || null }); });
  $('#ip-parent').onchange = e => save({ parent: e.target.value.trim() });
  const la = $('#ip-label-add');
  if (la) la.onchange = async () => {
    let id = la.value;
    if (id === 'new') { const name = prompt(window.t('Nome da nova etiqueta:')); if (!name) { la.value = ''; return; } id = (await api('/issues/labels', { method: 'POST', body: { name } })).id; ISS.settings = null; }
    if (id) save({ labels: [...d.labels.map(l => l.id), Number(id)] });
  };
  const lc = $('#ip-label-clear'); if (lc) lc.onclick = () => save({ labels: [] });
  main.querySelectorAll('.ip-unrel').forEach(b => b.onclick = ev => act(ev.target, () => api(`/issues/${encodeURIComponent(d.key)}/relations/${b.dataset.id}`, { method: 'DELETE' }).then(route)));
  $('#ip-rel-go').onclick = ev => act(ev.target, () => api(`/issues/${encodeURIComponent(d.key)}/relations`, { method: 'POST', body: { kind: $('#ip-rel-kind').value, other: $('#ip-rel-key').value.trim() } }).then(route), 'Relação criada');
  $('#ip-sub').onclick = ev => act(ev.target, () => api(`/issues/${encodeURIComponent(d.key)}/subscription`, { method: 'POST', body: { subscribed: !d.subscribed } }).then(route));
  const del = $('#ip-del');
  if (del) del.onclick = ev => confirm(window.t('Apagar a issue de vez? Comentários e histórico vão junto e as rodadas em andamento são canceladas. Para guardar o histórico, mude o status para Cancelada.')) &&
    act(ev.target, () => api(`/issues/${encodeURIComponent(d.key)}`, { method: 'DELETE' }).then(() => { location.hash = '#/issues'; }), 'Issue apagada');
  $('#id-sub-add').onclick = () => { $('#id-sub-form').hidden = false; $('#id-sub-title').focus(); };
  $('#id-sub-go').onclick = ev => act(ev.target, async () => {
    const r = await api('/issues', { method: 'POST', body: { title: $('#id-sub-title').value.trim(), parent: d.key, stage: Number($('#id-sub-stage').value) || null,
      assignee: $('#id-sub-assignee').value, team: d.team ? d.team.slug : '', project: d.project ? d.project.slug : '', status: $('#id-sub-assignee').value ? 'todo' : 'backlog' } });
    route(); return r;
  }, 'Sub-issue criada');
  const cm = $('#id-cm');
  issMentions(cm, people);
  const send = ev => { if (!cm.value.trim()) return; act($('#id-cm-go'), () => api(`/issues/${encodeURIComponent(d.key)}/comments`, { method: 'POST', body: { body: cm.value } })
    .then(r => { cm.value = ''; if (r.runs_started && r.runs_started.length) toast(window.t('Digital employee acionado') + ': ' + r.runs_started.join(', ')); route(); })); };
  $('#id-cm-go').onclick = send;
  cm.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
  // tempo real: a conversa e as rodadas atualizam enquanto ninguém escreve
  clearInterval(ISS.poll);
  const here = location.hash;
  ISS.poll = setInterval(async () => {
    if (location.hash !== here) { clearInterval(ISS.poll); return; }
    if (document.hidden || ISS.busy || $('.modal-bg') || document.activeElement.matches('input,textarea,select') || (cm && cm.value.trim()) || $('#id-desc-in')) return;
    try { const fresh = await api(`/issues/${encodeURIComponent(d.key)}`); if (fresh.updated_at !== d.updated_at || JSON.stringify(fresh.run) !== JSON.stringify(d.run)) route(); } catch (e) { /* ok */ }
  }, 6000);
}

function issTimelineRender(d) {
  const items = d.timeline;
  const replies = {};
  items.filter(x => x.type === 'comment' && x.parent_id).forEach(c => (replies[c.parent_id] = replies[c.parent_id] || []).push(c));
  const comment = (c, reply) => `<div class="iss-c ${reply ? 'reply' : ''} ${c.resolved ? 'resolved' : ''}" data-id="${c.id}">
    <div class="iss-c-h">${c.author.kind === 'employee' ? `<span class="iss-av bot">${icon('bot')}</span>` : issAvatar({ kind: 'user', name: c.author.name, avatar_url: c.author.avatar_url })}
      <b data-noi18n>${esc(c.author.name)}</b><span class="mute small">${when(c.created_at)}${c.edited ? ` · ${window.t('editado')}` : ''}</span>
      ${c.task_id ? `<a class="small" href="#/tasks/${c.task_id}">${window.t('rodada')} #${c.task_id}</a>` : ''}
      ${!reply && c.resolved ? `<span class="pill ok">${window.t('resolvida')}</span>` : ''}</div>
    <div class="md iss-c-b">${c.deleted ? `<span class="mute">${window.t('Comentário apagado.')}</span>` : issMd(c.body)}</div>
    ${c.deleted ? '' : `<div class="iss-c-a">${Object.entries(c.reactions).map(([e, n]) => `<button class="ghost small iss-react ${c.my_reactions.includes(e) ? 'on' : ''}" data-e="${e}">${e} ${n}</button>`).join('')}
      ${d.can_edit ? `<details class="iss-react-pick"><summary class="ghost small btnlike">☺</summary><span>${['👍', '❤️', '🎉', '👀', '🚀', '😕'].map(e => `<button class="ghost small iss-react" data-e="${e}">${e}</button>`).join('')}</span></details>` : ''}
      ${!reply && d.can_edit ? `<button class="ghost small iss-reply">${window.t('Responder')}</button><button class="ghost small iss-resolve">${window.t(c.resolved ? 'Reabrir' : 'Resolver')}</button>` : ''}
      ${c.can_edit ? `<button class="ghost small iss-edit">${window.t('Editar')}</button>` : ''}${c.can_delete ? `<button class="ghost small iss-delc">${window.t('Apagar')}</button>` : ''}</div>`}
    ${reply ? '' : (replies[c.id] || []).map(r => comment(r, true)).join('')}</div>`;
  $('#id-tl').innerHTML = items.filter(x => !(x.type === 'comment' && x.parent_id)).map(x => x.type === 'comment' ? comment(x, false)
    : `<div class="iss-act small">${issActor(x.actor)} <span>${(ISS_ACT[x.kind] || (() => esc(x.kind)))(x.data || {})}</span> <span class="mute">${when(x.created_at)}</span></div>`).join('')
    || '<div class="mute small">Nada ainda.</div>';
  const at = (b, fn) => b.onclick = ev => { const id = b.closest('.iss-c').dataset.id; fn(id, ev); };
  const base = `/issues/${encodeURIComponent(d.key)}/comments`;
  $('#id-tl').querySelectorAll('.iss-react').forEach(b => at(b, id => api(`${base}/${id}/react`, { method: 'POST', body: { emoji: b.dataset.e } }).then(route).catch(e => toast(e.message, true))));
  $('#id-tl').querySelectorAll('.iss-resolve').forEach(b => at(b, id => api(`${base}/${id}/resolve`, { method: 'POST', body: { resolved: !b.closest('.iss-c').classList.contains('resolved') } }).then(route).catch(e => toast(e.message, true))));
  $('#id-tl').querySelectorAll('.iss-delc').forEach(b => at(b, id => confirm(window.t('Apagar este comentário?')) && api(`${base}/${id}`, { method: 'DELETE' }).then(route).catch(e => toast(e.message, true))));
  $('#id-tl').querySelectorAll('.iss-edit').forEach(b => at(b, id => {
    const c = d.timeline.find(x => x.type === 'comment' && String(x.id) === id);
    const box = b.closest('.iss-c').querySelector('.iss-c-b');
    box.innerHTML = `<textarea rows="4">${esc(c.body)}</textarea><div class="row"><button class="small">${window.t('Salvar')}</button><button class="ghost small">${window.t('Cancelar')}</button></div>`;
    const [sv, cc] = box.querySelectorAll('button');
    sv.onclick = () => api(`${base}/${id}`, { method: 'PATCH', body: { body: box.querySelector('textarea').value } }).then(route).catch(e => toast(e.message, true));
    cc.onclick = () => route();
  }));
  $('#id-tl').querySelectorAll('.iss-reply').forEach(b => at(b, id => {
    const host = b.closest('.iss-c');
    if (host.querySelector('.iss-reply-box')) return;
    host.insertAdjacentHTML('beforeend', `<div class="iss-reply-box"><textarea rows="2" placeholder="${esc(window.t('Responder…'))}"></textarea><div class="row"><button class="small">${window.t('Responder')}</button></div></div>`);
    const ta = host.querySelector('.iss-reply-box textarea');
    issPeople(d.team ? d.team.slug : '').then(p => issMentions(ta, p));
    ta.focus();
    host.querySelector('.iss-reply-box button').onclick = ev => act(ev.target, () => api(base, { method: 'POST', body: { body: ta.value, parent_id: Number(id) } }).then(route));
  }));
}

/* @menções: ao digitar @, a lista de pessoas e Digital employees; escolher insere @[Nome](employee:slug) */
function issMentions(ta, people) {
  if (!ta) return;
  const all = [...people.employees.map(e => ({ ref: e.ref, name: e.name, sub: window.t('Digital employee'), bot: true })),
    ...people.people.map(u => ({ ref: u.ref, name: u.name, sub: u.email }))];
  let box = null, sel = 0, items = [];
  const close = () => { if (box) box.remove(); box = null; };
  const word = () => { const m = ta.value.slice(0, ta.selectionStart).match(/(^|\s)@([^\s@\[]{0,30})$/); return m ? m[2] : null; };
  const pick = it => {
    const pos = ta.selectionStart, before = ta.value.slice(0, pos).replace(/@([^\s@\[]{0,30})$/, ''), after = ta.value.slice(pos);
    const ins = `@[${it.name}](${it.ref}) `;
    ta.value = before + ins + after; ta.focus(); ta.selectionStart = ta.selectionEnd = before.length + ins.length; close();
  };
  const draw = () => {
    const w = word();
    if (w === null) { close(); return; }
    items = all.filter(x => x.name.toLowerCase().includes(w.toLowerCase())).slice(0, 8);
    if (!items.length) { close(); return; }
    if (!box) { box = document.createElement('div'); box.className = 'iss-mention-box card'; ta.parentElement.style.position = 'relative'; ta.insertAdjacentElement('afterend', box); }
    sel = Math.min(sel, items.length - 1);
    box.innerHTML = items.map((x, k) => `<div class="iss-mention-it ${k === sel ? 'on' : ''}" data-k="${k}">${x.bot ? icon('bot') : icon('user')}<b data-noi18n>${esc(x.name)}</b> <span class="mute small" data-noi18n>${esc(x.sub)}</span></div>`).join('');
    box.querySelectorAll('.iss-mention-it').forEach(el => el.onmousedown = e => { e.preventDefault(); pick(items[Number(el.dataset.k)]); });
  };
  ta.addEventListener('input', () => { sel = 0; draw(); });
  ta.addEventListener('keydown', e => {
    if (!box) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = (sel + 1) % items.length; draw(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = (sel - 1 + items.length) % items.length; draw(); }
    else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(items[sel]); }
    else if (e.key === 'Escape') { e.stopPropagation(); close(); }
  });
  ta.addEventListener('blur', () => setTimeout(close, 150));
}

/* ---------- configurar o quadro ---------- */
async function issueSettingsPage() {
  const st = await issSettings(true);
  const adm = st.can_admin;
  main.innerHTML = `<a href="#/issues" class="mute small">← Issues</a><h1>Configurar o quadro</h1>
  <div class="sub">Os status (colunas) e as etiquetas das issues. Os status embutidos não mudam; cada status novo pertence a uma categoria, que decide o comportamento: <b>não iniciada</b>, <b>em andamento</b>, <b>concluída</b> ou <b>encerrada</b>.</div>
  <div class="grid g2">
    <div class="card"><h2>Status</h2><table><tr><th></th><th>Nome</th><th>Chave</th><th>Categoria</th><th></th></tr>
      ${st.statuses.map(s => `<tr class="${s.archived ? 'mute' : ''}"><td><span class="iss-swatch" style="background:${esc(s.color)}"></span></td><td>${esc(window.t(s.name))}</td><td><code class="inline" data-noi18n>${esc(s.key)}</code></td>
        <td class="small">${esc(window.t({ unstarted: 'não iniciada', started: 'em andamento', done: 'concluída', closed: 'encerrada' }[s.category]))}</td>
        <td>${s.builtin ? '<span class="mute small">embutido</span>' : adm ? `<button class="ghost small is-arch" data-k="${esc(s.key)}" data-a="${s.archived ? 0 : 1}">${s.archived ? 'Reativar' : 'Arquivar'}</button>` : ''}</td></tr>`).join('')}</table>
      ${adm ? `<div class="row mt"><input id="is-name" placeholder="Novo status (ex.: Code review)" style="flex:1"><select id="is-cat" style="width:auto"><option value="unstarted">não iniciada</option><option value="started" selected>em andamento</option><option value="done">concluída</option><option value="closed">encerrada</option></select>
        <input id="is-color" type="color" value="#7C5CFF" style="width:44px;padding:2px"><button id="is-add">Criar</button></div>` : ''}</div>
    <div class="card"><h2>Etiquetas</h2><div class="iss-labels-edit">${st.labels.map(l => `<span class="iss-label" style="--c:${esc(l.color)}" data-noi18n>${esc(l.name)}${adm ? ` <button class="ghost small il-del" data-id="${l.id}">×</button>` : ''}</span>`).join('') || '<span class="mute small">Nenhuma ainda.</span>'}</div>
      <div class="row mt"><input id="il-name" placeholder="Nova etiqueta" style="flex:1"><input id="il-color" type="color" value="#5B8DEF" style="width:44px;padding:2px"><button id="il-add">Criar</button></div>
      <h2 class="mt">Prefixo das issues</h2><div class="row"><input id="ip-prefix" value="${esc(st.prefix)}" ${adm ? '' : 'disabled'} style="width:120px" data-noi18n>${adm ? '<button class="ghost" id="ip-prefix-go">Salvar</button>' : ''}</div>
      <div class="mute small">As chaves ficam ${esc(st.prefix)}-1, ${esc(st.prefix)}-2… (mudar o prefixo muda como todas aparecem).</div></div></div>`;
  const add = $('#is-add');
  if (add) add.onclick = e => act(e.target, () => api('/issues/statuses', { method: 'POST', body: { name: $('#is-name').value, category: $('#is-cat').value, color: $('#is-color').value } }).then(route), 'Status criado');
  main.querySelectorAll('.is-arch').forEach(b => b.onclick = e => act(e.target, () => api(`/issues/statuses/${b.dataset.k}`, { method: 'PATCH', body: { archived: b.dataset.a === '1' } }).then(route)));
  $('#il-add').onclick = e => act(e.target, () => api('/issues/labels', { method: 'POST', body: { name: $('#il-name').value, color: $('#il-color').value } }).then(route), 'Etiqueta criada');
  main.querySelectorAll('.il-del').forEach(b => b.onclick = e => confirm(window.t('Apagar a etiqueta? Ela sai de todas as issues.')) && act(e.target, () => api(`/issues/labels/${b.dataset.id}`, { method: 'DELETE' }).then(route)));
  const pg = $('#ip-prefix-go'); if (pg) pg.onclick = e => act(e.target, () => api('/issues/settings/prefix', { method: 'PUT', body: { prefix: $('#ip-prefix').value } }).then(route), 'Prefixo salvo');
}

/* atalhos: C cria uma issue, / busca (fora de campos de texto) */
document.addEventListener('keydown', e => {
  if (!ME || e.ctrlKey || e.metaKey || e.altKey || document.activeElement.matches('input,textarea,select,[contenteditable]') || $('.modal-bg')) return;
  const r = location.hash.split('?')[0];
  if (!/^#\/(issues|my-issues)/.test(r)) return;
  if (e.key === 'c' || e.key === 'C') { e.preventDefault(); issNew({}); }
  else if (e.key === '/' && $('#iss-q')) { e.preventDefault(); $('#iss-q').focus(); }
});
