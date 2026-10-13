/* O que ajuda no dia a dia: primeiros passos, avisos da empresa, favoritos e recentes, e o "ao vivo".
   Favoritos e recentes ficam só neste navegador (localStorage); o resto vem da API. Usa os helpers de app.js. */

const UX_KEY = { fav: 'hangar_favs', rec: 'hangar_recent', ann: 'hangar_ann_hidden' };
const UX_KINDS = { agents: 'agente', employees: 'Digital employee', projects: 'projeto', tasks: 'tarefa', skills: 'skill', plugins: 'plugin' };
const uxGet = k => { try { return JSON.parse(localStorage.getItem(k) || '[]'); } catch { return []; } };
const uxSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sem armazenamento: só não lembra */ } };
let uxAnns = [];
let uxLiveTimer = null;

/* a página atual como item (favorito ou recente) */
function uxItem() {
  const [, r, a] = location.hash.split('?')[0].split('/');
  if (!UX_KINDS[r] || !a || ['new', 'catalog'].includes(a)) return null;
  const h1 = main.querySelector('h1');
  if (!h1) return null;
  const title = [...h1.childNodes].filter(n => n.nodeType === 3 || (n.nodeType === 1 && !n.matches('.pill,button,.chip'))).map(n => n.textContent).join(' ')
    .replace(/\s+/g, ' ').trim().slice(0, 80);
  return { href: `#/${r}/${a}`, kind: r, title: title || decodeURIComponent(a) };
}

function uxSide() {
  const nav = document.querySelector('#side nav');
  if (!nav) return;
  const old = $('#ux-favs');
  if (old) old.remove();
  const favs = uxGet(UX_KEY.fav);
  if (favs.length) {
    nav.insertAdjacentHTML('afterbegin', `<div class="nav-group" id="ux-favs"><div class="nav-title">Favoritos</div>
      ${favs.map(f => `<a href="${esc(f.href)}" data-r="${esc(f.href.slice(2))}" data-noi18n>${icon('check')}${esc(f.title)}</a>`).join('')}</div>`);
  }
  api('/announcements').then(a => { uxAnns = a; uxBanner(); }).catch(() => {});
}

function uxBanner() {
  const hidden = uxGet(UX_KEY.ann);
  const show = uxAnns.filter(a => !hidden.includes(a.id));
  const old = $('#ux-ann');
  if (old) old.remove();
  if (!show.length) return;
  main.insertAdjacentHTML('afterbegin', `<div id="ux-ann">${show.map(a => `<div class="card ux-ann ${a.level === 'warn' ? 'warn-card' : ''}" data-id="${a.id}">
    <div class="row between"><span data-noi18n>${esc(a.text)}</span><button class="ghost small ux-ann-x" title="Esconder" aria-label="Esconder">×</button></div></div>`).join('')}</div>`);
  main.querySelectorAll('.ux-ann-x').forEach(b => b.onclick = () => {
    uxSet(UX_KEY.ann, [...uxGet(UX_KEY.ann), Number(b.closest('.ux-ann').dataset.id)].slice(-50));
    uxBanner();
  });
}

async function uxOnboarding() {
  const o = await api('/onboarding' + (USER_MODE ? '?portal=1' : '')).catch(() => null);
  if (!o || o.hidden || o.complete || !location.hash.match(/^#?\/?$/)) return;
  const card = document.createElement('div');
  card.className = 'card ux-onb';
  card.innerHTML = `<div class="row between"><h2>Primeiros passos <span class="mute small">${o.done}/${o.total}</span></h2><button class="ghost small" id="onb-hide">Esconder</button></div>
    <div class="meter ok"><i style="width:${Math.round(100 * o.done / o.total)}%"></i></div>
    <ul class="list mt">${o.steps.map(s => `<li><span class="${s.done ? 'ok-ic' : 'mute'}">${icon(s.done ? 'check' : 'target')}</span>
      ${s.done ? `<span class="mute">${esc(window.t(s.title))}</span>` : `<a href="${esc(s.href)}">${esc(window.t(s.title))}</a>`}</li>`).join('')}</ul>`;
  const ann = $('#ux-ann');
  if (ann) ann.after(card); else main.prepend(card);
  $('#onb-hide').onclick = ev => act(ev.target, () => api('/onboarding', { method: 'PUT', body: { hidden: true } }).then(() => card.remove()));
}

/* depois de cada página: recentes, a estrela de favorito, os avisos e (no início) os primeiros passos */
function uxAfterRoute(r) {
  if (r !== 'live' && uxLiveTimer) { clearInterval(uxLiveTimer); uxLiveTimer = null; }
  uxBanner();
  if (r === '') { uxOnboarding(); main.insertAdjacentHTML('beforeend', uxRecentCard()); }
  const it = uxItem();
  if (!it) return;
  uxSet(UX_KEY.rec, [it, ...uxGet(UX_KEY.rec).filter(x => x.href !== it.href)].slice(0, 10));
  const h1 = main.querySelector('h1');
  if (h1.querySelector('.ux-star')) return;
  const on = uxGet(UX_KEY.fav).some(f => f.href === it.href);
  h1.insertAdjacentHTML('beforeend', ` <button class="ghost small ux-star ${on ? 'on' : ''}" title="${window.t(on ? 'Tirar dos favoritos' : 'Favoritar')}" aria-pressed="${on}">${on ? '★' : '☆'}</button>`);
  h1.querySelector('.ux-star').onclick = () => {
    const favs = uxGet(UX_KEY.fav);
    uxSet(UX_KEY.fav, favs.some(f => f.href === it.href) ? favs.filter(f => f.href !== it.href) : [...favs, it].slice(-15));
    h1.querySelector('.ux-star').remove();
    uxSide();
    uxAfterRoute(r);
  };
}

/* ---------- ao vivo ---------- */
const LV_KIND = { created: 'criada', run: 'rodada', tool: 'ferramenta', gate: 'alçada', human_request: 'pediu decisão', decision: 'decisão',
  done: 'concluída', error: 'erro', note: 'nota', budget: 'orçamento', job: 'job', shadow: 'sombra', review: 'revisão' };
const LV_PILL = { done: 'ok', error: 'bad', human_request: 'warn', gate: 'info', budget: 'warn' };
const LV_STATUS = { in_progress: 'trabalhando', waiting_human: 'esperando decisão', in_review: 'em revisão' };

async function livePage() {
  main.innerHTML = `<h1>Ao vivo</h1><div class="sub">O que os Digital employees estão fazendo agora, nas tarefas que você pode ver. Atualiza sozinho.</div>
    <div class="grid g2"><div class="card"><h2>Em andamento <span id="lv-n" class="mute small"></span></h2><div id="lv-run"></div></div>
      <div class="card"><h2>Linha do tempo</h2><ul class="list" id="lv-ev"></ul><div id="lv-empty" class="mute small">Nada aconteceu ainda nas tarefas que você pode ver.</div></div></div>`;
  let last = 0;
  const events = [];
  const tick = async first => {
    if (!location.hash.startsWith('#/live')) { clearInterval(uxLiveTimer); uxLiveTimer = null; return; }
    if (document.hidden && !first) return;  // aba escondida: não pergunta à toa
    const d = await api(`/live?since=${last}`).catch(() => null);
    if (!d || !$('#lv-run')) return;
    last = d.last;
    events.unshift(...d.events);
    events.splice(100);
    $('#lv-n').textContent = d.running.length ? `(${d.running.length})` : '';
    $('#lv-run').innerHTML = d.running.length ? d.running.map(t => `<div class="row between li"><span><a href="#/tasks/${t.task}"><b data-noi18n>#${t.task} ${esc(t.title)}</b></a>
        <div class="mute small"><span data-noi18n>${esc(t.name)}</span>${t.external ? ' · <span class="chip">worker externo</span>' : ''}</div></span>
        <span class="small"><span class="pill ${t.status === 'in_progress' ? 'info' : 'warn'}">${esc(LV_STATUS[t.status] || t.status)}</span> ${t.since ? `<span class="mute">${ago(t.since)}</span>` : ''}</span></div>`).join('')
      : '<div class="mute small">Nenhuma tarefa em andamento agora.</div>';
    $('#lv-ev').innerHTML = events.map(e => `<li class="row between"><span><span class="pill ${LV_PILL[e.kind] || ''}">${esc(LV_KIND[e.kind] || e.kind)}</span>
        <a href="#/tasks/${e.task}"><span data-noi18n>${esc(e.name)} · #${e.task} ${esc(e.title)}</span></a>${e.detail ? ` <code class="inline" data-noi18n>${esc(e.detail)}</code>` : ''}</span>
        <span class="mute small">${ago(e.at)}</span></li>`).join('');
    $('#lv-empty').hidden = events.length > 0;
  };
  clearInterval(uxLiveTimer);
  await tick(true);
  uxLiveTimer = setInterval(tick, 4000);
}

/* ---------- avisos (admin) ---------- */
async function announcementsPage() {
  const list = await api('/announcements?all=true');
  const now = Date.now();
  main.innerHTML = `<h1>Avisos</h1><div class="sub">Uma faixa no topo do portal e do console, para todo mundo da empresa (cada pessoa pode esconder a dela).</div>
    <div class="card"><h2>Novo aviso</h2><label>Texto<textarea id="an-text" rows="2" placeholder="ex.: Manutenção do ERP no sábado, das 8h às 12h"></textarea></label>
      <div class="grid g2 mt"><label>Tipo<select id="an-level"><option value="info">Informação</option><option value="warn">Atenção</option></select></label>
        <label>Validade (dias; vazio = até remover)<input id="an-days" type="number" min="1" max="365"></label></div>
      <div class="row mt"><button id="an-go">Publicar</button></div></div>
    <div class="card mt"><h2>Publicados</h2>${list.length ? `<table class="small"><tr><th>Aviso</th><th>Por</th><th>Validade</th><th></th></tr>
      ${list.map(a => `<tr data-id="${a.id}"><td data-noi18n>${esc(a.text)}</td><td data-noi18n>${esc(a.created_by)}</td>
        <td>${a.expires_at ? (Date.parse(a.expires_at) < now ? `<span class="pill">${window.t('vencido')}</span>` : when(a.expires_at)) : '—'}</td>
        <td><button class="ghost small danger an-del">Remover</button></td></tr>`).join('')}</table>` : '<div class="mute small">Nenhum aviso.</div>'}</div>`;
  $('#an-go').onclick = ev => act(ev.target, () => api('/announcements', { method: 'POST', body: { text: $('#an-text').value, level: $('#an-level').value,
    days: $('#an-days').value ? Number($('#an-days').value) : null } }).then(() => { uxSide(); return announcementsPage(); }), 'Aviso publicado');
  main.querySelectorAll('.an-del').forEach(b => b.onclick = ev => act(ev.target, () => api('/announcements/' + b.closest('tr').dataset.id, { method: 'DELETE' })
    .then(() => { uxSide(); return announcementsPage(); }), 'Aviso removido'));
}

/* recentes no início do portal (e no painel do console) */
function uxRecentCard() {
  const rec = uxGet(UX_KEY.rec);
  if (!rec.length) return '';
  return `<div class="card mt"><h2>Vistos recentemente</h2><div class="row wrap" style="gap:6px">${rec.map(x =>
    `<a class="chip" href="${esc(x.href)}"><span>${esc(window.t(UX_KINDS[x.kind] || x.kind))}</span> · <span data-noi18n>${esc(x.title)}</span></a>`).join('')}</div></div>`;
}
