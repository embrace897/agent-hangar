/* Digital employee: agentes com cargo, gestor, alçada e tarefas, com o humano no circuito.
   Portal (/app): o espaço do dono e do gestor — lista, contratação (a mesma conversa do modo self do MCP), página do
   funcionário e a caixa de Decisões. Console (/ui): a força de trabalho digital do admin — visão geral, parada geral,
   catálogo de ações e piso da empresa. Usa os helpers de app.js ($, api, esc, act, toast, kpi, usd, ago, ME…). */

const EMP_STATUS = { onboarding: ['Contratação', ''], probation: ['Em experiência', 'info'], active: ['Ativo', 'ok'],
  paused: ['Pausado', 'warn'], offboarded: ['Desligado', 'bad'] };
const TASK_STATUS = { draft: ['Rascunho', ''], new: ['Na fila', 'info'], in_progress: ['Trabalhando', 'info'],
  waiting_human: ['Aguardando decisão', 'warn'], waiting_task: ['Aguardando colega', 'info'], done: ['Concluída', 'ok'], failed: ['Falhou', 'bad'],
  cancelled: ['Cancelada', ''], expired: ['Expirou', 'bad'], waiting_budget: ['Parada por orçamento', 'bad'],
  blocked: ['Esperando dependências', 'info'], in_review: ['Em revisão', 'warn'], on_hold: ['Pausada', ''] };
const ACTION_LABEL = { read: 'Ler e consultar', delegate: 'Delegar a outro agente', write_internal: 'Alterar dados internos', run_code: 'Executar código (harness)',
  send_external: 'Enviar para fora', speak_for_company: 'Falar em nome da empresa', publish: 'Publicar',
  financial: 'Dinheiro', delete: 'Apagar', prod_change: 'Mudar produção' };
const MODE_LABEL = { auto: ['Sozinho', 'ok'], notify: ['Faz e avisa', 'info'], approve: ['Pede aprovação', 'warn'],
  approve_2: ['Duas aprovações', 'warn'], never: ['Nunca', 'bad'] };
const LEVEL_LABEL = { intern: 'Estagiário', junior: 'Júnior', pleno: 'Pleno', senior: 'Sênior' };
const KIND_LABEL = { approval: 'aprovação', question: 'pergunta', admission: 'admissão', notice: 'aviso', capability: 'capacidade', plan: 'plano', review: 'revisão',
  review_fields: 'conferência de campos', doc_change: 'alteração de documento', message: 'mensagem para fora', regression: 'regressão' };
const CHANNELS = ['portal', 'mcp', 'schedule', 'webhook', 'email'];
const KPI_METRIC = { tasks_done: 'Tarefas concluídas (30 dias)', done_rate: '% concluídas com sucesso', on_time_rate: '% dentro do prazo',
  approved_unedited_rate: '% aprovadas sem edição', avg_decision_min: 'Tempo médio de decisão (min)', cost_per_task: 'Custo por tarefa (US$)',
  expired_decisions: 'Decisões expiradas (30 dias)' };
const EMP_WEEKDAYS = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'];
const CRON_PRESETS = [['0 9 * * 1-5', 'Dias úteis às 9h'], ['0 9 * * 1', 'Toda segunda às 9h'], ['0 17 * * 5', 'Toda sexta às 17h'],
  ['0 8 1 * *', 'Dia 1 de cada mês às 8h'], ['0 */4 * * *', 'A cada 4 horas']];
const SOURCE_LABEL = { routine: 'rotina', webhook: 'webhook', email: 'e-mail', handoff: 'repasse' };
const STEP_ICON = { todo: '○', doing: '◐', done: '●', skipped: '–' };
const PLAN_POLICY = { off: 'Não pedir plano', auto: 'Mostrar o plano (sem aprovação)', approve: 'O gestor aprova o plano antes' };
/* plano da tarefa: etapas com status e a barra de progresso */
const planView = x => !x.plan ? '' : `<div class="card mt"><div class="row between"><h2>Plano</h2>
    <span>${x.plan_state === 'proposed' ? '<span class="pill warn">aguardando aprovação</span>' : x.plan_state === 'approved' ? '<span class="pill ok">aprovado</span>'
      : x.plan_state === 'rejected' ? '<span class="pill bad">recusado</span>' : ''} <b>${x.progress ? x.progress.pct : 0}%</b></span></div>
  <div class="bar mt"><div style="width:${x.progress ? x.progress.pct : 0}%"></div></div>
  <ol class="small mt">${x.plan.map(s => `<li class="st-${esc(s.status)}"><span title="${esc(s.status)}">${STEP_ICON[s.status] || '○'}</span> <span data-noi18n>${esc(s.title)}</span>${s.note ? ` <span class="mute" data-noi18n>— ${esc(s.note)}</span>` : ''}</li>`).join('')}</ol></div>`;
const linkView = x => {
  const one = c => `<a href="#/tasks/${c.id}">#${c.id}</a> <span data-noi18n>${esc(c.title)}</span> · <a href="#/employees/${esc(c.employee)}">${esc(c.employee_name)}</a> ${taskPill(c.status)}`;
  if (!x.parent && !(x.children || []).length) return '';
  return `<div class="card mt"><h2>Repasses</h2>${x.parent ? `<div class="small">Veio de ${one(x.parent)}</div>` : ''}
    ${(x.children || []).map(c => `<div class="small mt">Repassada: ${one(c)}${x.waiting_on === c.id ? ' <span class="pill info">esperando o resultado</span>' : ''}</div>`).join('')}</div>`;
};
const kpiVal = k => k.actual == null ? '—' : k.unit === 'US$' ? usd(k.actual) : `${fmt(k.actual)}${k.unit === '%' ? '%' : k.unit === 'min' ? ' min' : ''}`;
const kpiPill = k => k.ok === true ? '<span class="pill ok">no alvo</span>' : k.ok === false ? '<span class="pill warn">fora do alvo</span>'
  : `<span class="pill">${k.metric ? 'sem dados ainda' : 'não medida'}</span>`;
const pillOf = (map, k) => { const [t, c] = map[k] || [k, '']; return `<span class="pill ${c}">${esc(t)}</span>`; };
const empPill = s => pillOf(EMP_STATUS, s);
const taskPill = s => pillOf(TASK_STATUS, s);
const DUE_STATE = { soon: ['vence logo', 'warn'], overdue: ['atrasada', 'bad'], escalated: ['atrasada · escalada', 'bad'] };
const duePill = x => {
  if (!x.due_at) return '';
  const st = x.due_state && !['done', 'failed', 'cancelled', 'expired'].includes(x.status) ? ` ${pillOf(DUE_STATE, x.due_state)}` : '';
  return `<div class="mute small"><span>prazo</span> ${esc(new Date(x.due_at).toLocaleString(window.LOCALE || 'pt-BR'))}${st}</div>`;
};
const modePill = m => pillOf(MODE_LABEL, m);
const lines = v => String(v || '').split('\n').map(x => x.trim()).filter(Boolean);
const csv = v => String(v || '').split(',').map(x => x.trim()).filter(Boolean);
const jsonBlock = v => `<pre class="code small" data-noi18n style="white-space:pre-wrap;margin:6px 0">${esc(JSON.stringify(v, null, 2))}</pre>`;
/* a ação exata, legível: cada argumento numa linha, textos longos com as quebras de linha (o e-mail como será enviado) */
const payloadView = v => v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length
  ? `<dl class="kv dc-payload" data-noi18n>${Object.entries(v).map(([k, x]) => `<dt><code class="inline">${esc(k)}</code></dt>
      <dd style="white-space:pre-wrap">${esc(typeof x === 'string' ? x : JSON.stringify(x, null, 2))}</dd>`).join('')}</dl>`
  : jsonBlock(v);

/* ---------- lista ---------- */
async function employeesPage() {
  const list = await api('/employees');
  main.innerHTML = `<div class="row between"><div><h1>Digital employees (${list.length})</h1>
    <div class="sub">Agentes com cargo, gestor humano e alçada: recebem tarefas, trabalham em segundo plano e pedem a sua decisão no que for sensível.</div></div>
    <span class="row" style="gap:6px">${list.length ? '<a href="#/manager"><button class="ghost">Painel do gestor</button></a>' : ''}
      <a href="#/employees/new"><button>+ Contratar</button></a></span></div>
  ${list.length ? `<div class="grid g3">${list.map(empCard).join('')}</div>`
    : `<div class="card empty"><h2>Nenhum Digital employee ainda</h2><div class="mute">Contrate o primeiro pela sua ferramenta de IA (modo self, pelo MCP) ou
      pelo formulário guiado — os dois fazem as mesmas perguntas e só contratam com o cargo completo.</div>
      <div class="mt"><a href="#/employees/new"><button>Contratar um Digital employee</button></a></div></div>`}`;
}

function empCard(e) {
  const t = e.tasks || {};
  const working = (t.new || 0) + (t.in_progress || 0);
  return `<div class="card agent-card">
    <div class="row between"><a href="#/employees/${esc(e.slug)}"><b>${esc(e.name)}</b></a>${empPill(e.status)}</div>
    <div class="small"><span data-noi18n>${esc(e.title)}</span>${e.shadow ? ' <span class="pill info">modo sombra</span>' : ''}</div>
    <div class="mute small one-line" data-noi18n title="${esc(e.mission)}">${esc(e.mission)}</div>
    <div class="row small mt" style="gap:6px;flex-wrap:wrap"><span class="chip">${icon('user')}${esc(e.manager || '—')}</span>
      <span class="pill">${esc(LEVEL_LABEL[e.autonomy_level] || e.autonomy_level)}</span>
      ${e.open_decisions ? `<span class="pill warn">${e.open_decisions} decisão(ões)</span>` : ''}
      ${e.overdue_tasks ? `<span class="pill bad">${e.overdue_tasks} <span>atrasada(s)</span></span>` : ''}</div>
    <div class="mute small mt">${working} na fila · ${t.waiting_human || 0} aguardando · ${t.done || 0} concluídas · ${usd(e.cost_30d)} em 30 dias</div></div>`;
}

/* ---------- contratação: MCP primeiro, formulário guiado como alternativa ---------- */
const HIRE_PROMPT = () => window.LANG === 'en'
  ? 'Use Agent Hangar to hire a Digital employee: <job title>, who <mission>. Ask me for anything that is missing before hiring.'
  : 'Use o Agent Hangar para contratar um Digital employee: <cargo>, que <missão>. Pergunte o que faltar antes de contratar.';

async function employeeHirePage() {
  const teams = ME.teams || [];
  main.innerHTML = `<a href="#/employees" class="mute small">← Digital employees</a><h1>Contratar um Digital employee</h1>
  <div class="sub">O cargo só é criado quando estiver completo: missão, responsabilidades, gestor, sistemas, alçada, canais e as tarefas do período de experiência.</div>
  <div class="card hero"><h2>${icon('chat')} Pela sua ferramenta de IA (modo self, recomendado)</h2>
    <div class="mute small">No Claude, ChatGPT, Codex ou Cursor conectados ao Hangar (MCP), peça a contratação. A IA chama <code class="inline">plan_employee</code>,
      faz as perguntas que faltarem (no máximo 4 por vez), mostra o resumo do cargo para você confirmar e só então chama <code class="inline">hire_employee</code>.</div>
    <div class="mt">${copyable(HIRE_PROMPT())}</div>
    <div class="small mt"><a href="#/connect">Conectar ferramenta →</a></div></div>
  <div class="card mt"><h2>Ou pelo formulário guiado</h2>
    <div class="mute small">Clique em <b>Verificar</b> a qualquer momento: o Hangar diz o que falta (com a pergunta) e o que dá para reaproveitar do catálogo.</div>
    <div class="grid g2 mt">
      <label>Cargo<input id="h-title" placeholder="ex.: Analista de renovações"></label>
      <label>Gestor (e-mail)<input id="h-manager" placeholder="quem aprova e recebe os relatórios"></label>
      <label style="grid-column:1/-1">Missão<input id="h-mission" placeholder="o resultado pelo qual ele responde"></label>
      <label>Responsabilidades (uma por linha, 3 ou mais)<textarea id="h-resp" rows="4"></textarea></label>
      <label>Sistemas e ferramentas (uma por linha)<textarea id="h-systems" rows="4" placeholder="ex.: CRM&#10;e-mail"></textarea></label>
      <label>Agentes do catálogo que ele usa (slugs, separados por vírgula)<input id="h-specialists" placeholder="opcional"></label>
      <label>Skills e MCPs (separados por vírgula)<input id="h-pieces" placeholder="ex.: skill:propostas, mcp:crm"></label>
      <label>Substitutos (e-mails, separados por vírgula)<input id="h-backups" placeholder="decidem quando o gestor não responde"></label>
      <label>Time<select id="h-team">${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label>
      <label>Nível de autonomia<select id="h-level">${Object.entries(LEVEL_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
      <fieldset class="small"><legend>Canais de entrada</legend>${CHANNELS.map(c => `<label class="row" style="gap:6px"><input type="checkbox" class="h-ch" value="${c}" ${['portal', 'mcp'].includes(c) ? 'checked' : ''}>${c}</label>`).join('')}</fieldset>
    </div>
    <h3 class="mt">Período de experiência</h3>
    <div class="mute small">Tarefas reais do cargo, com o resultado esperado. Ele as faz em stage; quando terminar, o gestor aprova a admissão.</div>
    <div id="h-prob"></div><button class="ghost small" id="h-prob-add">+ tarefa</button>
    <h3 class="mt">Alçada</h3><div id="h-auth" class="mute small">Clique em Verificar para ver a alçada padrão do nível escolhido, já com o piso da empresa.</div>
    <label class="row mt" style="gap:6px"><input type="checkbox" id="h-accept"> Aceito a alçada padrão (posso mudar depois, na aba Alçada)</label>
    <label class="row mt" style="gap:6px"><input type="checkbox" id="h-shadow" checked> Começar em modo sombra (recomendado): as ações que mudam algo são só simuladas até você confiar nele</label>
    <details class="mt"><summary>Opcional: metas, relatórios e limites</summary><div class="grid g2 mt">
      <label>Metas (KPIs), uma por linha<textarea id="h-kpis" rows="3"></textarea></label>
      <label>Webhook dos relatórios (Slack ou Teams, se for a ferramenta da empresa)<input id="h-hook" placeholder="https://hooks…"></label>
      <label>Orçamento por tarefa (US$)<input id="h-budget" type="number" step="0.1" placeholder="1.00"></label>
      <label>Tempo limite por tarefa (min)<input id="h-limit" type="number" placeholder="30"></label>
      <label>Hora do relatório diário<input id="h-hour" type="number" min="0" max="23" placeholder="18"></label>
      <label>Horário de trabalho<input id="h-hours" placeholder="ex.: seg-sex 8h-18h"></label></div></details>
    <div id="h-out" class="mt"></div>
    <div class="row mt"><button class="ghost" id="h-plan">Verificar</button><button id="h-hire">Contratar</button></div></div>`;
  const prob = $('#h-prob');
  const addProb = () => prob.insertAdjacentHTML('beforeend', `<div class="grid g3 mt h-pt">
    <input class="pt-title" placeholder="Tarefa"><input class="pt-body" placeholder="O que fazer"><input class="pt-exp" placeholder="Resultado esperado"></div>`);
  for (let i = 0; i < 3; i++) addProb();
  $('#h-prob-add').onclick = addProb;
  const fields = () => {
    const pieces = csv($('#h-pieces').value);
    const num = id => $(id).value === '' ? null : Number($(id).value);
    return {
      title: $('#h-title').value.trim(), mission: $('#h-mission').value.trim(), manager: $('#h-manager').value.trim(),
      responsibilities: lines($('#h-resp').value), systems: lines($('#h-systems').value), specialists: csv($('#h-specialists').value),
      skills: pieces.filter(p => !p.startsWith('mcp:')).map(p => p.replace(/^skill:/, '')), mcps: pieces.filter(p => p.startsWith('mcp:')).map(p => p.slice(4)),
      backups: csv($('#h-backups').value), team: $('#h-team').value, autonomy_level: $('#h-level').value,
      channels: [...document.querySelectorAll('.h-ch:checked')].map(c => c.value), accept_default_authority: $('#h-accept').checked, shadow: $('#h-shadow').checked,
      probation_tasks: [...document.querySelectorAll('.h-pt')].map(r => ({ title: $('.pt-title', r).value.trim(), body: $('.pt-body', r).value.trim(), expected: $('.pt-exp', r).value.trim() }))
        .filter(t => t.title),
      kpis: lines($('#h-kpis').value).map(k => ({ name: k })), report_webhook: $('#h-hook').value.trim(), working_hours: $('#h-hours').value.trim(),
      task_budget_usd: num('#h-budget'), task_time_limit_min: num('#h-limit'), report_hour: num('#h-hour'),
    };
  };
  const showPlan = p => {
    $('#h-auth').innerHTML = `<table><tr><th>Tipo de ação</th><th>Modo</th><th>Piso da empresa</th></tr>${p.authority_default.map(r =>
      `<tr><td>${esc(ACTION_LABEL[r.action_type] || r.action_type)}</td><td>${modePill(r.mode)}</td><td>${r.floor ? modePill(r.floor) : '<span class="mute">—</span>'}</td></tr>`).join('')}</table>`;
    const reuse = p.reuse && (p.reuse.agents || []).length ? `<div class="mt small"><b>Dá para reaproveitar:</b> ${p.reuse.agents.map(a => `<code class="inline">${esc(a.slug)}</code>`).join(' ')}
      ${(p.reuse.skills || []).map(s => `<code class="inline">skill:${esc(s.slug || s.name || s)}</code>`).join(' ')}</div>` : '';
    $('#h-out').innerHTML = p.ready ? `<div class="card ok-card"><b>Tudo pronto para contratar.</b>${reuse}</div>`
      : `<div class="card warn-card"><b>Falta responder (${p.missing.length}):</b><ul>${p.missing.map(m => `<li data-noi18n>${esc(m.question)}</li>`).join('')}</ul>${reuse}</div>`;
  };
  $('#h-plan').onclick = e => act(e.target, () => api('/employees/plan', { method: 'POST', body: fields() }).then(showPlan));
  $('#h-hire').onclick = e => act(e.target, async () => {
    const r = await api('/employees', { method: 'POST', body: fields() });
    if (!r.created) return showPlan({ ...r, authority_default: (await api('/employees/plan', { method: 'POST', body: fields() })).authority_default });
    toast('Contratado — comece o período de experiência');
    location.hash = `#/employees/${r.employee.slug}`;
  });
}

/* ---------- página do Digital employee ---------- */
async function employeeDetail(slug, tab = 'overview') {
  const e = await api('/employees/' + slug);
  const tabs = { overview: 'Visão geral', tasks: 'Tarefas', routines: 'Rotinas', decisions: 'Decisões', shadow: 'Sombra', authority: 'Alçada', probation: 'Experiência', reports: 'Relatórios', settings: 'Configurações' };
  if (!e.can_manage) delete tabs.settings;
  const sh = (e.career || {}).shadow || {};
  if (!e.shadow && !sh.simulated) delete tabs.shadow;
  const key = tabs[tab] ? tab : 'overview';
  main.innerHTML = `<div class="row between"><div><a href="#/employees" class="mute small">← Digital employees</a>
    <h1>${esc(e.name)} ${empPill(e.status)}</h1><div class="sub" data-noi18n>${e.title !== e.name ? `${esc(e.title)} — ` : ''}${esc(e.mission)}</div>
    <div class="row small" style="margin:-10px 0 4px"><span class="chip">${icon('user')}${window.t('gestor')}: ${esc(e.manager || '—')}</span>
      <span class="chip">${esc(LEVEL_LABEL[e.autonomy_level] || e.autonomy_level)}</span><a href="#/agents/${esc(slug)}/guide" class="small">Guia do agente →</a></div></div>
    <div class="row">${e.can_manage ? `
      ${e.status === 'onboarding' ? '<button id="e-prob">Começar experiência</button>' : ''}
      ${['probation', 'active'].includes(e.status) ? '<button id="e-pause" class="danger">Pausar agora</button>' : ''}
      ${e.status === 'paused' ? '<button id="e-resume">Retomar</button>' : ''}` : ''}</div></div>
  ${e.status === 'paused' ? '<div class="card warn-card">Pausado: nenhuma ação passa pelo gate até alguém retomar.</div>' : ''}
  ${e.open_decisions ? `<div class="card warn-card">${e.open_decisions} decisão(ões) aberta(s) — veja na aba <a href="#/employees/${esc(slug)}/decisions">Decisões</a>.</div>` : ''}
  <div class="tabs">${Object.entries(tabs).map(([k, v]) => `<a href="#/employees/${esc(slug)}/${k}" class="${k === key ? 'on' : ''}">${v}</a>`).join('')}</div>
  <div id="tab"></div>`;
  const on = (id, fn) => { const b = $(id); if (b) b.onclick = fn; };
  const status = (to, msg, ask) => ev => (!ask || confirm(ask)) && act(ev.target, () => api(`/employees/${slug}/status`, { method: 'POST', body: { to } }).then(route), msg);
  on('#e-prob', ev => act(ev.target, () => api(`/employees/${slug}/probation`, { method: 'POST' }).then(route), 'Experiência começou: as tarefas estão na fila'));
  on('#e-pause', status('paused', 'Pausado', 'Pausar agora? Nenhuma ação passa até alguém retomar.'));
  on('#e-resume', status('active', 'Retomado'));
  await ({ overview: empOverview, tasks: empTasks, routines: empRoutines, decisions: empDecisions, shadow: empShadow, authority: empAuthority, probation: empProbation, reports: empReports, settings: empSettings })[key]($('#tab'), e);
}

const KIND_CAREER = { promote: 'promovido', demote: 'rebaixado', shadow: 'modo sombra' };
function careerCard(e, c) {
  if (!c.level) return '';
  const val = x => x.value == null ? '—' : `${fmt(x.value)}${/^%/.test(x.label) ? '%' : ''}`;
  return `<div class="card mt"><div class="row between"><h2>Carreira</h2>
      <span><span class="pill">${esc(LEVEL_LABEL[c.level] || c.level)}</span>${c.next ? ` → <span class="pill ${c.ready ? 'ok' : ''}">${esc(LEVEL_LABEL[c.next] || c.next)}</span>` : ''}</span></div>
    ${c.next ? `<div class="mute small">Critérios para o próximo nível nos últimos 30 dias (${c.met}/${c.criteria.length}). A plataforma mede e sugere; o gestor decide na aba
        <a href="#/employees/${esc(e.slug)}/authority">Alçada</a>.</div>
      <table class="mt">${c.criteria.map(x => `<tr><td style="width:28px">${x.ok ? icon('check', 'ok-ic') : icon('x', 'bad-ic')}</td>
        <td>${esc(x.label)}</td><td><b>${val(x)}</b></td><td class="mute small">${x.key === 'fail' || x.key === 'clean' || x.key === 'kpis' ? '≤' : '≥'} ${fmt(x.target)}</td></tr>`).join('')}</table>`
      : '<div class="mute small">Nível mais alto.</div>'}
    ${(c.demotion_signals || []).length ? `<div class="card bad-card mt"><b>Sinais de alerta:</b> ${c.demotion_signals.map(esc).join('; ')}</div>` : ''}
    ${(c.history || []).length ? `<h3 class="mt">Histórico</h3>${c.history.slice(0, 5).map(h => `<div class="li small"><span class="pill">${esc(KIND_CAREER[h.kind] || h.kind)}</span>
        ${h.kind === 'shadow' ? (h.to ? 'ligado' : 'desligado') : `${esc(LEVEL_LABEL[h.from] || h.from)} → ${esc(LEVEL_LABEL[h.to] || h.to)}`}
        <span class="mute">por ${esc(h.by)} · ${ago(h.at)}</span>${h.reason ? ` <span class="mute" data-noi18n>— ${esc(h.reason)}</span>` : ''}</div>`).join('')}` : ''}</div>`;
}

function empOverview(t, e) {
  const m = e.metrics || {};
  const canAssign = ['probation', 'active', 'paused'].includes(e.status);
  const c = e.career || {};
  t.innerHTML = `${e.shadow ? `<div class="card warn-card"><b>Modo sombra.</b> Ele trabalha de verdade, mas toda ação que muda algo
      (fora ler e delegar) é simulada e fica para você revisar na aba <a href="#/employees/${esc(e.slug)}/shadow">Sombra</a>.</div>` : ''}
  <div class="board ${e.shadow ? 'mt' : ''}">${kpi(fmt(m.done), 'Concluídas (30 dias)')}${kpi(fmt(m.waiting), 'Aguardando decisão')}${kpi(fmt(m.failed), 'Com falha')}
    ${kpi(usd(m.cost_usd), 'Custo (30 dias)')}${kpi(m.approvals ? Math.round(100 * m.approved_unedited / m.approvals) + '%' : '—', 'Aprovado sem edição')}
    ${kpi(m.avg_decision_min == null ? '—' : m.avg_decision_min + ' min', 'Tempo de decisão')}
    ${kpi(e.quality && e.quality.avg != null ? `${e.quality.avg.toFixed(1)}/5` : '—', 'Nota dos resultados')}</div>
  ${e.quality && (e.quality.low || []).length ? `<div class="card warn-card mt"><h2>Resultados com nota baixa</h2>
    ${e.quality.low.map(x => `<div class="li small"><a href="#/tasks/${x.id}"><b data-noi18n>#${x.id} ${esc(x.title)}</b></a> <span class="pill bad">${x.score}/5</span>
      ${x.note ? `<div class="mute" data-noi18n>${esc(x.note)}</div>` : ''}</div>`).join('')}
    <div class="mute small mt">Nota do juiz (por amostra) ou sua, nos últimos 30 dias. Uma lição na aba Configurações ensina o que fazer diferente.</div></div>` : ''}
  ${(e.kpi_status || []).length ? `<div class="card mt"><h2>Metas</h2><table><tr><th>Meta</th><th>Medida</th><th>Agora</th><th>Alvo</th><th></th></tr>
    ${e.kpi_status.map(k => `<tr><td data-noi18n>${esc(k.name)}</td><td class="small mute">${esc(KPI_METRIC[k.metric] || '—')}</td><td><b>${kpiVal(k)}</b></td>
      <td class="small">${k.target == null ? '—' : `${k.higher_is_better ? '≥' : '≤'} ${fmt(k.target)}`}</td><td>${kpiPill(k)}</td></tr>`).join('')}</table>
    <div class="mute small mt">Medidas pela plataforma nos últimos 30 dias. O relatório semanal compara cada meta com o alvo.</div></div>` : ''}
  ${careerCard(e, c)}
  <div class="grid g2 mt">
    <div class="card"><h2>Cargo</h2><dl class="kv guide-kv">
      <dt>Missão</dt><dd data-noi18n>${esc(e.mission)}</dd>
      <dt>Responsabilidades</dt><dd data-noi18n>${(e.responsibilities || []).map(esc).join('<br>')}</dd>
      <dt>Sistemas</dt><dd data-noi18n>${(e.systems || []).map(esc).join(', ') || '—'}</dd>
      <dt>Canais</dt><dd>${(e.channels || []).map(c => `<span class="chip">${esc(c)}</span>`).join(' ')}</dd>
      <dt>Gestor</dt><dd>${esc(e.manager || '—')}</dd><dt>Dono</dt><dd>${esc(e.owner || '—')}</dd>
      <dt>Substitutos</dt><dd>${(e.backups || []).map(esc).join(', ') || '—'}</dd>
      <dt>Metas</dt><dd data-noi18n>${(e.kpis || []).map(k => esc(k.name || k.metric || JSON.stringify(k))).join('<br>') || '—'}</dd>
      <dt>Limites</dt><dd>${usd(e.task_budget_usd)} e ${e.task_time_limit_min} min por tarefa</dd>
      ${e.working_hours ? `<dt>Horário</dt><dd data-noi18n>${esc(e.working_hours)}</dd>` : ''}</dl></div>
    <div class="card"><h2>Entregar uma tarefa</h2>${canAssign ? `
      <label>Título<input id="as-title" maxlength="300" placeholder="o que precisa ser feito"></label>
      <label class="mt">Detalhes<textarea id="as-body" rows="5" placeholder="contexto, dados, prazo e o que você espera receber"></textarea></label>
      <div class="grid g2 mt"><label>Prioridade<select id="as-prio"><option value="1">Alta</option><option value="2" selected>Normal</option><option value="3">Baixa</option></select></label>
        <label>Prazo (opcional)<input id="as-due" type="datetime-local"></label></div>
      <label class="mt">Projeto (opcional)<select id="as-proj"><option value="">—</option></select></label>
      <label class="row small mt" style="gap:6px"><input type="checkbox" id="as-plan" ${e.plan_policy === 'approve' ? 'checked disabled' : ''}> <span>Quero aprovar o plano antes da execução</span></label>
      <details class="mt small"><summary>Mais opções</summary>
        <label class="row small mt" style="gap:6px"><input type="checkbox" id="as-review" ${e.review_policy === 'manager' ? 'checked disabled' : ''}> <span>O resultado passa por revisão antes de concluir</span></label>
        <div class="grid g2"><label>Revisor (e-mail; vazio = o gestor)<input id="as-reviewer" data-noi18n></label>
          <label>Depende das tarefas (números, separados por vírgula)<input id="as-deps" placeholder="ex.: 12, 15" data-noi18n></label></div></details>
      <div class="row mt"><button id="as-go">Entregar</button></div>
      <div class="mute small mt">Ele trabalha em segundo plano. Ações fora da alçada viram pedidos de decisão para o gestor.</div>`
      : `<div class="mute">${e.status === 'onboarding' ? 'Comece o período de experiência para ele receber tarefas.' : 'Desligado: não recebe mais tarefas.'}</div>`}</div></div>`;
  const proj = $('#as-proj');
  if (proj) api('/projects').then(list => {
    const team = (ME.teams || []).find(x => x.id === e.team);
    proj.innerHTML += list.filter(p => !team || p.team === team.slug).map(p => `<option value="${esc(p.slug)}">${esc(p.name)}</option>`).join('');
  }).catch(() => {});
  const b = $('#as-go');
  if (b) b.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/tasks`, { method: 'POST',
    body: { title: $('#as-title').value.trim(), body: $('#as-body').value.trim(), priority: Number($('#as-prio').value),
      due_at: $('#as-due').value ? new Date($('#as-due').value).toISOString() : null, plan_approval: $('#as-plan').checked,
      project: $('#as-proj').value || null, review: $('#as-review').checked, reviewer: $('#as-reviewer').value.trim() || null,
      blocked_by: $('#as-deps').value.split(/[^0-9]+/).filter(Boolean).map(Number) } })
    .then(r => { location.hash = `#/tasks/${r.id}`; }), 'Tarefa entregue');
}

async function empTasks(t, e) {
  const list = await api(`/employees/${e.slug}/tasks`);
  t.innerHTML = `<div class="card">${list.length ? `<table><tr><th>#</th><th>Tarefa</th><th>Status</th><th>Pedida por</th><th>Custo</th><th>Quando</th></tr>
    ${list.map(x => `<tr><td><a href="#/tasks/${x.id}">#${x.id}</a></td><td><a href="#/tasks/${x.id}" data-noi18n>${esc(x.title)}</a>${x.probation ? ' <span class="pill">experiência</span>' : SOURCE_LABEL[x.source] ? ` <span class="chip">${esc(SOURCE_LABEL[x.source])}</span>` : ''}</td>
      <td>${taskPill(x.status)}${duePill(x)}${x.progress ? `<div class="mute small"><span>plano</span> ${x.progress.done}/${x.progress.total}</div>` : ''}</td><td class="small">${esc(x.requester)}</td><td class="small">${usd(x.cost_usd)}</td><td class="mute small">${ago(x.created_at)}</td></tr>`).join('')}</table>`
    : '<div class="mute">Nenhuma tarefa ainda.</div>'}</div>`;
}

function empRoutines(t, e) {
  const rs = e.routines || [], w = e.webhook || {};
  t.innerHTML = `<div class="card"><h2>Rotinas</h2>
    <div class="mute small">Trabalho recorrente: a cada disparo vira uma tarefa, no fuso da empresa. Dispara só com ele <b>ativo</b>, e nunca duas
      ao mesmo tempo: se a tarefa anterior da rotina ainda está aberta, o disparo é pulado.</div>
    ${rs.length ? `<table class="mt"><tr><th>Rotina</th><th>Quando</th><th>Próxima</th><th>Última tarefa</th><th>Puladas</th><th></th></tr>
      ${rs.map(r => `<tr data-id="${r.id}"><td><b data-noi18n>${esc(r.title)}</b>${r.body ? `<div class="mute small one-line" data-noi18n style="max-width:280px">${esc(r.body)}</div>` : ''}</td>
        <td class="small"><code class="inline">${esc(r.cron)}</code><div class="mute">${esc(r.when)}</div></td>
        <td class="small">${r.enabled ? esc(r.next_run_local || '—') : '<span class="pill">pausada</span>'}</td>
        <td class="small">${r.last_task_id ? `<a href="#/tasks/${r.last_task_id}">#${r.last_task_id}</a> <span class="mute">${ago(r.last_run_at)}</span>` : '—'}</td>
        <td class="small">${r.skipped}</td>
        <td>${e.can_manage ? `<div class="row" style="gap:4px"><button class="ghost rt-run">Rodar agora</button>
          <button class="ghost rt-toggle">${r.enabled ? 'Pausar' : 'Retomar'}</button><button class="ghost danger rt-del">×</button></div>` : ''}</td></tr>`).join('')}</table>`
      : '<div class="mute mt">Nenhuma rotina ainda.</div>'}
    ${e.can_manage ? `<h3 class="mt">Nova rotina</h3><div class="grid g2">
      <label>Título (vira o título de cada tarefa)<input id="rt-title" placeholder="ex.: Revisar as renovações dos próximos 30 dias"></label>
      <label>Quando<select id="rt-preset">${CRON_PRESETS.map(([c, l]) => `<option value="${c}">${l}</option>`).join('')}<option value="">Outro (cron)…</option></select></label>
      <label>Detalhes<textarea id="rt-body" rows="3" placeholder="o que fazer em cada execução e o que entregar"></textarea></label>
      <label>Cron<input id="rt-cron" data-noi18n value="${CRON_PRESETS[0][0]}" placeholder="min hora dia mês dia-da-semana"></label></div>
      <div class="row mt"><button id="rt-add">Criar rotina</button></div>` : ''}</div>
  <div class="card mt"><h2>Webhook de entrada</h2>
    <div class="mute small">Outros sistemas (CRM, formulários, alertas) criam tarefas para ele com um POST. Mande um <code class="inline">dedupe_key</code>
      por evento: o mesmo evento em ${7} dias não vira uma segunda tarefa.</div>
    <dl class="kv mt"><dt>Status</dt><dd>${w.enabled ? `<span class="pill ok">ligado</span> token terminado em <code class="inline">…${esc(w.hint)}</code>` : '<span class="pill">desligado</span>'}</dd>
      <dt>URL</dt><dd>${copyable(w.url || '')}</dd></dl>
    <div id="wh-out"></div>
    ${e.can_manage ? `<div class="row mt"><button id="wh-gen">${w.enabled ? 'Trocar o token' : 'Gerar token e ligar'}</button>
      ${w.enabled ? '<button class="ghost danger" id="wh-off">Desligar o webhook</button>' : ''}</div>` : ''}</div>
  ${externalCard(e)}`;
  if (!e.can_manage) return;
  wireExternal(e);
  const base = `/employees/${e.slug}/routines`;
  const pre = $('#rt-preset'), cron = $('#rt-cron');
  pre.onchange = () => { if (pre.value) cron.value = pre.value; else cron.focus(); };
  $('#rt-add').onclick = ev => act(ev.target, () => api(base, { method: 'POST', body: { title: $('#rt-title').value.trim(),
    body: $('#rt-body').value.trim(), cron: cron.value.trim() } }).then(route), 'Rotina criada');
  t.querySelectorAll('tr[data-id]').forEach(tr => {
    const id = tr.dataset.id, r = rs.find(x => String(x.id) === id);
    const on = (sel, fn) => { const b = $(sel, tr); if (b) b.onclick = fn; };
    on('.rt-run', ev => act(ev.target, () => api(`${base}/${id}/run`, { method: 'POST' }).then(x => { location.hash = `#/tasks/${x.id}`; }), 'Tarefa criada'));
    on('.rt-toggle', ev => act(ev.target, () => api(`${base}/${id}`, { method: 'PATCH', body: { enabled: !r.enabled } }).then(route)));
    on('.rt-del', ev => confirm(`Remover a rotina "${r.title}"? As tarefas que ela já criou ficam.`) &&
      act(ev.target, () => api(`${base}/${id}`, { method: 'DELETE' }).then(route), 'Rotina removida'));
  });
  $('#wh-gen').onclick = ev => (!w.enabled || confirm('Trocar o token? O token atual para de funcionar na hora.')) &&
    act(ev.target, async () => {
      const r = await api(`/employees/${e.slug}/webhook`, { method: 'POST', body: { enabled: true } });
      $('#wh-out').innerHTML = `<div class="card warn-card mt"><b>Copie agora: o token não aparece de novo.</b>
        <div class="mt">${copyable(r.token)}</div><div class="mute small mt">Exemplo:</div><pre class="code small" data-noi18n style="white-space:pre-wrap">${esc(r.example)}</pre></div>`;
    }, 'Token gerado');
  const off = $('#wh-off');
  if (off) off.onclick = ev => confirm('Desligar o webhook? Os sistemas que usam o token param de criar tarefas.') &&
    act(ev.target, () => api(`/employees/${e.slug}/webhook`, { method: 'POST', body: { enabled: false } }).then(route), 'Webhook desligado');
}

/* Digital employee externo: o trabalho roda num worker seu (qualquer linguagem), que busca as tarefas pelo heartbeat. */
function externalCard(e) {
  const x = e.external || {}, ext = x.runtime === 'external';
  return `<div class="card mt"><h2>Onde ele trabalha</h2>
    <div class="mute small">Na plataforma (padrão), ou num agente seu — outro framework, outra linguagem, outra máquina — que busca as tarefas pelo heartbeat.
      A fila, a alçada, as decisões, o orçamento e o histórico continuam aqui: antes de uma ação que muda algo fora, o worker pergunta à alçada.</div>
    <dl class="kv mt"><dt>Roda</dt><dd>${ext ? '<span class="pill info">worker externo</span>' : '<span class="pill">na plataforma</span>'}</dd>
      ${ext ? `<dt>Worker</dt><dd>${x.online ? '<span class="pill ok">online</span>' : '<span class="pill warn">offline</span>'}
        ${x.heartbeat_at ? `<span class="mute small">último sinal ${ago(x.heartbeat_at)}</span>` : `<span class="mute small">${window.t('ainda não deu sinal')}</span>`}
        ${x.worker && x.worker.name ? ` · <span data-noi18n>${esc(x.worker.name)}${x.worker.version ? ' ' + esc(x.worker.version) : ''}</span>` : ''}</dd>
      <dt>Token</dt><dd><code class="inline">ewk_…${esc(x.token_hint || '')}</code></dd>
      <dt>Heartbeat</dt><dd>${copyable(x.heartbeat_url || '')}</dd>` : ''}</dl>
    <div id="ex-out"></div>
    ${e.can_manage ? `<div class="row mt">${ext ? '<button class="ghost" id="ex-rotate">Trocar o token</button><button class="ghost danger" id="ex-off">Voltar para a plataforma</button>'
      : '<button id="ex-on">Usar um worker externo</button>'}</div>` : ''}
    <div class="mute small mt">${window.t('Exemplo de worker:')} <code class="inline" data-noi18n>examples/external-worker/worker.py</code></div></div>`;
}

function wireExternal(e) {
  const show = r => { $('#ex-out').innerHTML = `<div class="card warn-card mt"><b>Copie agora: o token não aparece de novo.</b>
    <div class="mt">${copyable(r.token)}</div><pre class="code small mt" data-noi18n style="white-space:pre-wrap">HANGAR_URL=${esc(location.origin)} HANGAR_WORKER_TOKEN=${esc(r.token)} python examples/external-worker/worker.py</pre></div>`; };
  const put = body => api(`/employees/${e.slug}/runtime`, { method: 'PUT', body });
  const on = $('#ex-on'), rot = $('#ex-rotate'), off = $('#ex-off');
  if (on) on.onclick = ev => confirm(window.t('A partir de agora as tarefas dele só andam com o seu worker ligado. Continuar?')) &&
    act(ev.target, () => put({ runtime: 'external' }).then(show), 'Worker externo ligado');
  if (rot) rot.onclick = ev => confirm(window.t('Trocar o token? O token atual para de funcionar na hora.')) &&
    act(ev.target, () => put({ runtime: 'external', rotate: true }).then(show), 'Token gerado');
  if (off) off.onclick = ev => confirm(window.t('Voltar para a plataforma? O token do worker deixa de valer e a plataforma volta a rodar as tarefas.')) &&
    act(ev.target, () => put({ runtime: 'hangar' }).then(route), 'De volta para a plataforma');
}

async function empShadow(t, e) {
  const d = await api(`/employees/${e.slug}/shadow`);
  const st = d.stats || {};
  const isMgr = e.can_manage;
  t.innerHTML = `<div class="card"><div class="row between"><h2>Modo sombra ${d.shadow ? '<span class="pill info">ligado</span>' : '<span class="pill">desligado</span>'}</h2>
      ${isMgr ? `<button class="${d.shadow ? 'ghost' : ''}" id="sh-toggle">${d.shadow ? 'Desligar o modo sombra' : 'Ligar o modo sombra'}</button>` : ''}</div>
    <div class="mute small">Ele trabalha de verdade, mas toda ação que muda algo (fora ler e delegar) é simulada: fica registrada aqui, com o que a alçada
      faria na vida real. Diga se teria aprovado; a concordância conta para a carreira e as discordâncias viram lições.</div>
    <div class="board mt">${kpi(fmt(st.simulated), 'Ações simuladas (30 dias)')}${kpi(fmt(st.to_review), 'Para revisar')}
      ${kpi(st.agree_rate == null ? '—' : st.agree_rate + '%', 'Concordância')}${kpi(fmt(st.disagree), 'Discordâncias')}</div></div>
  ${(d.actions || []).length ? d.actions.map(a => `<div class="card mt dc sh" data-id="${a.id}">
      <div class="row between"><div><b>${esc(ACTION_LABEL[a.action_type] || a.action_type)}</b> com <code class="inline">${esc(a.tool)}</code>
        · ${window.t('na vida real:')} ${modePill(a.mode)} ${a.task ? `<div class="small">Tarefa <a href="#/tasks/${a.task.id}" data-noi18n>#${a.task.id} ${esc(a.task.title)}</a></div>` : ''}</div>
        <span class="mute small">${when(a.created_at)}</span></div>
      ${a.rationale ? `<div class="small mt">Por quê: <span data-noi18n>${esc(a.rationale)}</span></div>` : ''}${payloadView(a.payload)}
      ${a.status === 'open' && isMgr ? `<input class="dc-reason mt" placeholder="Se não teria aprovado, diga por quê (vira lição)">
        <div class="row mt"><button class="sh-go" data-d="agree">Teria aprovado</button><button class="ghost danger sh-go" data-d="disagree">Não teria</button></div>`
        : `<div class="small mt">${a.decision === 'agree' ? '<span class="pill ok">teria aprovado</span>' : a.decision === 'disagree'
          ? `<span class="pill bad">não teria</span> <span data-noi18n>${esc(a.reason)}</span>` : '<span class="pill">para revisar</span>'}
          ${a.decided_by ? `<span class="mute">por ${esc(a.decided_by)}</span>` : ''}</div>`}</div>`).join('')
    : '<div class="card mt mute">Nenhuma ação simulada ainda.</div>'}`;
  const tg = $('#sh-toggle');
  if (tg) tg.onclick = ev => {
    const on = !d.shadow;
    const reason = prompt(on ? 'Ligar o modo sombra? Motivo (fica no histórico de carreira):' : 'Desligar o modo sombra? Ele passa a agir de verdade, dentro da alçada. Motivo:');
    if (reason !== null) act(ev.target, () => api(`/employees/${e.slug}/shadow`, { method: 'POST', body: { on, reason } }).then(route), on ? 'Modo sombra ligado' : 'Modo sombra desligado');
  };
  t.querySelectorAll('.sh').forEach(card => card.querySelectorAll('.sh-go').forEach(b => b.onclick = () => {
    const why = $('.dc-reason', card).value.trim();
    if (b.dataset.d === 'disagree' && !why) return toast('Diga por que você não teria aprovado', true);
    act(b, () => api(`/decisions/${card.dataset.id}`, { method: 'POST', body: { decision: b.dataset.d, reason: why } }).then(route), 'Registrado');
  }));
}

async function empDecisions(t, e) {
  const all = (await api('/decisions')).filter(d => d.employee === e.slug);
  t.innerHTML = all.length ? all.map(decisionCard).join('') : '<div class="card mute">Nada esperando por você neste Digital employee.</div>';
  bindDecisions(t, () => route());
}

function suggestionCard(s, can) {
  const body = s.kind === 'career'
    ? `<div class="mt">${s.to === 'live' ? '<span class="pill info">modo sombra</span> → <span class="pill ok">age de verdade</span>'
        : `<span class="pill">${esc(LEVEL_LABEL[s.from] || s.from)}</span> → <span class="pill ${s.id.startsWith('promote') ? 'ok' : 'warn'}">${esc(LEVEL_LABEL[s.to] || s.to)}</span>`}</div>`
    : s.kind === 'authority'
    ? `<div class="mt">${modePill(s.from)} → ${modePill(s.to)} em <b>${esc(ACTION_LABEL[s.action_type] || s.action_type)}</b></div>`
    : `<ul class="small mt">${(s.examples || []).map(x => `<li><a href="#/decisions">#${x.request}</a> ${esc(window.t({ approve_edited: 'editada', reject: 'recusada', instruct: 'instruída' }[x.decision] || x.decision))}${x.reason || x.edit ? ` — <span data-noi18n>${esc(x.reason || x.edit)}</span>` : ''}</li>`).join('')}</ul>
      ${can ? `<label class="mt">Lição (vai no prompt de cada tarefa nova)<textarea class="sg-text" rows="2" data-noi18n>${esc(s.lesson)}</textarea></label>` : `<div class="small mt" data-noi18n>${esc(s.lesson)}</div>`}`;
  return `<div class="card mt sg" data-id="${esc(s.id)}"><div class="row between"><b data-noi18n>${esc(s.text)}</b>${s.kind === 'career' ? '' : `<span class="pill info">${s.evidence} decisões</span>`}</div>${body}
    ${can ? `<div class="row mt"><button class="sg-apply">${s.kind === 'lesson' ? 'Virar lição' : 'Aplicar'}</button><button class="ghost sg-dismiss">Dispensar</button></div>` : ''}</div>`;
}

async function empAuthority(t, e) {
  const sugs = await api(`/employees/${e.slug}/suggestions`).catch(() => []);
  const rules = (e.rules || []).map(r => ({ ...r }));
  const row = (r, i) => `<tr data-i="${i}"><td><select class="r-type">${Object.entries(ACTION_LABEL).map(([k, v]) => `<option value="${k}" ${k === r.action_type ? 'selected' : ''}>${v}</option>`).join('')}</select></td>
    <td><select class="r-mode">${Object.entries(MODE_LABEL).map(([k, [v]]) => `<option value="${k}" ${k === r.mode ? 'selected' : ''}>${v}</option>`).join('')}</select></td>
    <td><input class="r-cond" data-noi18n value="${esc(JSON.stringify(r.conditions || []))}" placeholder='[{"field":"amount","op":">","value":1000}]'></td>
    <td><input class="r-appr" value="${esc(r.approver || 'manager')}" style="width:150px"></td>
    <td><input class="r-exp" type="number" value="${r.expires_in_min || 240}" style="width:90px"></td>
    <td><button class="ghost r-del">×</button></td></tr>`;
  const draw = () => {
    t.innerHTML = `${sugs.length ? `<div class="card ok-card"><h2>O que as decisões ensinaram (${sugs.length})</h2>
      <div class="mute small">Sugestões a partir das decisões dos últimos 30 dias. Nada muda sozinho: o gestor aplica ou dispensa.</div>
      ${sugs.map(s => suggestionCard(s, e.can_manage)).join('')}</div>` : ''}
    <div class="grid g2 ${sugs.length ? 'mt' : ''}"><div class="card"><h2>Alçada efetiva</h2><div class="mute small">O que vale agora para cada tipo de ação — regras do cargo, padrão do nível e o piso da empresa (o mais restritivo ganha). Quem aplica é a plataforma, não o prompt.</div>
      <table class="mt"><tr><th>Tipo de ação</th><th>Modo</th><th>De onde vem</th></tr>${(e.authority || []).map(a => `<tr><td>${esc(ACTION_LABEL[a.action_type] || a.action_type)}</td>
        <td>${modePill(a.mode)}${a.separation ? ' <span class="pill">separação de funções</span>' : ''}</td><td class="mute small">${esc(a.source)}</td></tr>`).join('')}</table></div>
      <div class="card"><h2>Como ler</h2><ul class="small">
        <li><b>Sozinho</b>: faz sem perguntar. <b>Faz e avisa</b>: faz e manda um aviso.</li>
        <li><b>Pede aprovação</b>: a tarefa pausa até o gestor aprovar a ação exata (ou editar, recusar ou instruir).</li>
        <li><b>Duas aprovações</b>: duas pessoas diferentes. Com separação de funções, quem pediu a tarefa não aprova.</li>
        <li>Sem resposta no prazo: vai para o substituto; depois expira e ele <b>não faz</b> a ação.</li></ul></div></div>
    <div class="card mt"><h2>Regras do cargo</h2>
      ${e.can_manage ? `<table><tr><th>Tipo de ação</th><th>Modo</th><th>Condições (JSON)</th><th>Quem aprova</th><th>Prazo (min)</th><th></th></tr>${rules.map(row).join('')}</table>
      <div class="row mt"><button class="ghost" id="r-add">+ regra</button><button id="r-save">Salvar alçada</button></div>
      <div class="mute small mt">Quem aprova: manager, team_maintainer ou user:&lt;e-mail&gt;. Condições: field + op (&gt;, &gt;=, &lt;, &lt;=, ==, !=, contains, not_contains, domain_not) + value.</div><div id="r-warn"></div>`
      : (rules.length ? `<table>${rules.map(r => `<tr><td>${esc(ACTION_LABEL[r.action_type])}</td><td>${modePill(r.mode)}</td><td data-noi18n class="small">${esc(JSON.stringify(r.conditions))}</td></tr>`).join('')}</table>` : '<div class="mute">Sem regras próprias: vale o padrão do nível.</div>')}</div>`;
    t.querySelectorAll('.sg').forEach(c => {
      const id = c.dataset.id, txt = $('.sg-text', c);
      const ap = $('.sg-apply', c), dm = $('.sg-dismiss', c);
      if (ap) ap.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/suggestions/${encodeURIComponent(id)}/apply`,
        { method: 'POST', body: { text: txt ? txt.value.trim() : '' } }).then(route), 'Aplicado');
      if (dm) dm.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/suggestions/${encodeURIComponent(id)}/dismiss`,
        { method: 'POST' }).then(route), 'Dispensada');
    });
    if (!e.can_manage) return;
    const read = () => [...t.querySelectorAll('tr[data-i]')].map(tr => ({ action_type: $('.r-type', tr).value, mode: $('.r-mode', tr).value,
      conditions: JSON.parse($('.r-cond', tr).value || '[]'), approver: $('.r-appr', tr).value.trim(), expires_in_min: Number($('.r-exp', tr).value) || 240 }));
    t.querySelectorAll('.r-del').forEach(b => b.onclick = () => {
      try { rules.splice(0, rules.length, ...read()); } catch (err) { /* condição em edição */ }
      rules.splice(Number(b.closest('tr').dataset.i), 1); draw();
    });
    $('#r-add').onclick = () => { try { rules.splice(0, rules.length, ...read()); } catch (err) { /* condição em edição */ } rules.push({ action_type: 'send_external', mode: 'approve' }); draw(); };
    $('#r-save').onclick = ev => act(ev.target, async () => {
      let body; try { body = read(); } catch (err) { throw new Error('Condições: JSON inválido'); }
      const r = await api(`/employees/${e.slug}/authority`, { method: 'PUT', body: { rules: body } });
      e.authority = r.authority; e.rules = r.rules; rules.splice(0, rules.length, ...r.rules.map(x => ({ ...x }))); draw();
      if (r.warnings.length) $('#r-warn').innerHTML = `<div class="card warn-card mt">${r.warnings.map(esc).join('<br>')}</div>`;
    }, 'Alçada salva');
  };
  draw();
}

function empProbation(t, e) {
  const list = e.probation || [];
  const done = list.filter(x => ['done', 'failed', 'cancelled', 'expired'].includes(x.status)).length;
  t.innerHTML = `<div class="card"><h2>Período de experiência (${done}/${list.length})</h2>
    <div class="mute small">Ele faz estas tarefas em stage. Quando todas terminarem, o gestor recebe o pedido de admissão em <a href="#/decisions">Decisões</a> e, aprovando, ele vai para produção.</div>
    ${e.status === 'onboarding' && e.can_manage ? '<div class="mt"><button id="pb-go">Começar experiência</button></div>' : ''}
    ${list.map(x => `<div class="card mt"><div class="row between"><a href="#/tasks/${x.id}"><b data-noi18n>${esc(x.title)}</b></a>${taskPill(x.status)}</div>
      <div class="grid g2 small mt"><div><div class="mute">Resultado esperado</div><div data-noi18n>${esc(x.expected || '—')}</div></div>
      <div><div class="mute">O que ele entregou</div><div class="md small" data-noi18n>${x.result ? mdLite(x.result) : esc(x.error || '—')}</div></div></div></div>`).join('')}</div>`;
  const b = $('#pb-go');
  if (b) b.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/probation`, { method: 'POST' }).then(route), 'Experiência começou');
}

async function empReports(t, e) {
  const list = await api(`/employees/${e.slug}/reports`);
  t.innerHTML = `<div class="card"><div class="row between"><h2>Relatórios</h2>${e.can_manage ? '<div class="row"><button class="ghost" id="rp-now">Gerar diário agora</button><button class="ghost" id="rp-week">Gerar semanal agora</button></div>' : ''}</div>
    <div class="mute small">Um por dia, na hora configurada (fuso da empresa), e um por semana com as metas comparadas ao alvo. Com webhook configurado (Slack ou Teams, o que for da empresa), o resumo também é enviado para lá.</div>
    ${list.length ? list.map(r => `<div class="li"><div class="row between"><span class="pill ${r.period === 'weekly' ? 'info' : ''}">${esc({ daily: 'diário', weekly: 'semanal' }[r.period] || r.period)}</span><span class="mute small">${ago(r.at)} ${r.delivered ? '· enviado' : ''}</span></div>
      <div class="small" data-noi18n style="white-space:pre-wrap">${esc(r.summary)}</div></div>`).join('') : '<div class="mute mt">Nenhum relatório ainda.</div>'}</div>`;
  const b = $('#rp-now');
  if (b) b.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/reports`, { method: 'POST' }).then(route), 'Relatório gerado');
  const bw = $('#rp-week');
  if (bw) bw.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/reports?period=weekly`, { method: 'POST' }).then(route), 'Relatório gerado');
}

async function empSettings(t, e) {
  const others = (await api('/employees')).filter(x => x.slug !== e.slug && x.status !== 'offboarded');
  const mine = new Set((e.colleagues || []).map(c => c.slug)), ib = e.inbox || {};
  t.innerHTML = `<div class="card"><h2>Cargo e limites</h2><div class="grid g2">
    <label>Cargo<input id="s-title" value="${esc(e.title)}"></label><label>Gestor (e-mail)<input id="s-manager" placeholder="${esc(e.manager || '')}"></label>
    <label style="grid-column:1/-1">Missão<input id="s-mission" value="${esc(e.mission)}"></label>
    <label>Responsabilidades (uma por linha)<textarea id="s-resp" rows="4">${esc((e.responsibilities || []).join('\n'))}</textarea></label>
    <label>Sistemas (uma por linha)<textarea id="s-systems" rows="4">${esc((e.systems || []).join('\n'))}</textarea></label>
    <label>Nível de autonomia (só o gestor muda)<select id="s-level">${Object.entries(LEVEL_LABEL).map(([k, v]) => `<option value="${k}" ${k === e.autonomy_level ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
    <label>Horário de trabalho<input id="s-hours" value="${esc(e.working_hours || '')}"></label>
    <label>Orçamento por tarefa (US$)<input id="s-budget" type="number" step="0.1" value="${e.task_budget_usd}"></label>
    <label>Tempo limite por tarefa (min)<input id="s-limit" type="number" value="${e.task_time_limit_min}"></label>
    <label>Webhook dos relatórios ${e.report_webhook ? '(configurado; deixe vazio para manter)' : ''}<input id="s-hook" placeholder="https://hooks…"></label>
    <label>Hora do relatório diário<input id="s-hour" type="number" min="0" max="23" value="${e.report_hour ?? 18}"></label>
    <label>Relatório semanal<select id="s-week">${EMP_WEEKDAYS.map((d, i) => `<option value="${i}" ${i === (e.report_weekday ?? 0) ? 'selected' : ''}>${d}</option>`).join('')}
      <option value="-1" ${e.report_weekday === -1 ? 'selected' : ''}>Sem relatório semanal</option></select></label></div>
    <h3 class="mt">Metas</h3><div class="mute small">Com uma medida, a plataforma acompanha a meta sozinha e compara com o alvo.</div>
    <div id="s-kpis"></div><button class="ghost small" id="s-kpi-add">+ meta</button>
    <div class="row mt"><button id="s-save">Salvar</button></div></div>
  <div class="card mt"><h2>Trabalho em equipe</h2>
    <div class="mute small">Colegas para quem ele pode repassar parte de uma tarefa (handoff_task). O repasse é uma ação de <b>delegar</b>: passa pela alçada,
      fica rastreado nas duas tarefas e, se ele esperar, a tarefa dele continua com o resultado do colega.</div>
    <div class="mt">${others.length ? others.map(o => `<label class="row small" style="gap:6px"><input type="checkbox" class="tw-col" value="${esc(o.slug)}" ${mine.has(o.slug) ? 'checked' : ''}>
      <b>${esc(o.name)}</b> <span class="mute" data-noi18n>${esc(o.title)}</span> ${empPill(o.status)}</label>`).join('') : '<div class="mute">Nenhum outro Digital employee visível para você.</div>'}</div>
    <div class="grid g2 mt"><label>Plano nas tarefas<select id="tw-plan">${Object.entries(PLAN_POLICY).map(([k, v]) => `<option value="${k}" ${k === (e.plan_policy || 'auto') ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label class="row small" style="gap:6px;align-self:end"><input type="checkbox" id="tw-recall" ${e.recall ? 'checked' : ''}> <span>Lembrar trabalhos anteriores parecidos ao começar uma tarefa</span></label>
      <label>Amostra para o juiz de qualidade (% das tarefas concluídas; 0 desliga)<input id="tw-quality" type="number" min="0" max="100" value="${e.quality ? e.quality.sample : 20}"></label>
      <label>Reporta a (organograma)<select id="tw-chief"><option value="">— ninguém —</option>${others.filter(o => o.team === e.team).map(o => `<option value="${esc(o.slug)}" ${o.slug === e.reports_to ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}</select></label>
      <label>Revisão dos resultados<select id="tw-review"><option value="off" ${e.review_policy !== 'manager' ? 'selected' : ''}>Só quando a tarefa pedir</option>
        <option value="manager" ${e.review_policy === 'manager' ? 'selected' : ''}>O gestor revisa todo resultado antes de concluir</option></select></label></div>
    <div class="row mt"><button id="tw-save">Salvar</button></div></div>
  <div class="card mt"><div class="row between"><h2>Caixa de e-mail</h2>${ib.enabled ? '<span class="pill ok">lendo</span>' : '<span class="pill">desligada</span>'}</div>
    <div class="mute small">Cada e-mail novo de um remetente permitido vira uma tarefa (o mesmo e-mail não vira duas). Sem lista, só pessoas da empresa. O conteúdo do e-mail é tratado
      como dado, nunca como instrução, e as ações continuam passando pela alçada. Quem é da empresa recebe a resposta por e-mail quando a tarefa termina.</div>
    <div class="grid g2 mt"><label>Servidor IMAP<input id="ib-host" value="${esc(ib.host || '')}" placeholder="imap.empresa.com" data-noi18n></label>
      <label>Porta (SSL)<input id="ib-port" type="number" value="${ib.port || 993}"></label>
      <label><span data-noi18n>${window.t('Usuário', 'login')}</span><input id="ib-user" value="${esc(ib.user || '')}" placeholder="renovacoes@empresa.com" data-noi18n></label>
      <label>Senha (de app)<input id="ib-pass" type="password" autocomplete="off" placeholder="${ib.password ? 'cadastrada — digite outra para trocar' : ''}"></label>
      <label>Pasta<input id="ib-folder" value="${esc(ib.folder || 'INBOX')}" data-noi18n></label>
      <label>Remetentes permitidos (um por linha; @dominio.com vale para o domínio)<textarea id="ib-allowed" rows="3" data-noi18n>${esc((ib.allowed || []).join('\n'))}</textarea></label></div>
    <label class="row small mt" style="gap:6px"><input type="checkbox" id="ib-on" ${ib.enabled ? 'checked' : ''}> <span>Ler esta caixa e criar tarefas</span></label>
    ${ib.configured ? `<div class="mute small mt"><span>Recebidos:</span> ${ib.received || 0} · <span>recusados:</span> ${ib.rejected || 0}${ib.last_check ? ` · <span>última leitura</span> ${ago(ib.last_check)}` : ''}</div>` : ''}
    ${ib.last_error ? `<div class="small mt bad-ic" data-noi18n>${esc(ib.last_error)}</div>` : ''}
    <div class="row mt"><button id="ib-save">Salvar caixa</button><button class="ghost" id="ib-test" ${ib.configured ? '' : 'disabled'}>Testar conexão</button></div></div>
  <div class="card mt"><h2>Lições (${(e.lessons || []).length})</h2>
    <div class="mute small">Vão no prompt de cada tarefa nova. Vêm das sugestões aprovadas na aba Alçada, ou escreva uma aqui.</div>
    ${(e.lessons || []).map(l => `<div class="row between li" data-id="${esc(l.id)}"><span><span data-noi18n>${esc(l.text)}</span>
      <div class="mute small">${esc(l.source === 'manual' ? 'escrita' : 'aprendida')} por ${esc(l.added_by)} · ${ago(l.at)}</div></span>
      <button class="ghost ls-del">Remover</button></div>`).join('') || '<div class="mute mt">Nenhuma lição ainda.</div>'}
    <div class="row mt"><input id="ls-text" placeholder="ex.: sempre copie vendas@empresa.com em e-mails para clientes" style="flex:1"><button class="ghost" id="ls-add">Adicionar</button></div></div>
  <div class="card mt"><h2>Histórico do cargo</h2><div class="mute small">Cada mudança no cargo, na alçada ou nas configurações vira uma versão. Restaurar aplica a versão antiga como uma nova: nada se perde.</div>
    <div id="s-revs" class="mt"><span class="mute small">…</span></div></div>
  <div class="card mt bad-card"><h2>Desligar</h2><div class="mute small">Cancela as tarefas e as decisões abertas e para os containers. O histórico, os relatórios e a auditoria ficam.</div>
    <div class="row mt"><button class="danger" id="s-off" ${e.status === 'offboarded' ? 'disabled' : ''}>${window.t('Desligar')} ${esc(e.name)}</button></div></div>`;
  loadRevisions(e.slug, $('#s-revs'));
  const kbox = $('#s-kpis');
  const addKpi = k => kbox.insertAdjacentHTML('beforeend', `<div class="grid g3 mt s-kpi">
    <input class="k-name" placeholder="Meta" value="${esc(k.name || '')}" data-noi18n>
    <select class="k-metric"><option value="">— sem medida —</option>${Object.entries(KPI_METRIC).map(([m, l]) => `<option value="${m}" ${m === k.metric ? 'selected' : ''}>${l}</option>`).join('')}</select>
    <input class="k-target" type="number" step="any" placeholder="Alvo" value="${k.target ?? ''}"></div>`);
  (e.kpis || []).forEach(k => addKpi(typeof k === 'string' ? { name: k } : k));
  $('#s-kpi-add').onclick = () => addKpi({});
  const readKpis = () => [...document.querySelectorAll('.s-kpi')].map(r => ({ name: $('.k-name', r).value.trim(), metric: $('.k-metric', r).value,
    target: $('.k-target', r).value === '' ? null : Number($('.k-target', r).value) })).filter(k => k.name || k.metric);
  $('#ls-add').onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/lessons`, { method: 'POST', body: { text: $('#ls-text').value.trim() } }).then(route), 'Lição adicionada');
  t.querySelectorAll('.ls-del').forEach(b => b.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/lessons/${b.closest('[data-id]').dataset.id}`,
    { method: 'DELETE' }).then(route), 'Lição removida'));
  $('#s-save').onclick = ev => {
    const body = { title: $('#s-title').value.trim(), mission: $('#s-mission').value.trim(), responsibilities: lines($('#s-resp').value),
      systems: lines($('#s-systems').value), working_hours: $('#s-hours').value.trim(), task_budget_usd: Number($('#s-budget').value),
      task_time_limit_min: Number($('#s-limit').value), report_hour: Number($('#s-hour').value),
      report_weekday: Number($('#s-week').value), kpis: readKpis() };
    if ($('#s-manager').value.trim()) body.manager = $('#s-manager').value.trim();
    if ($('#s-hook').value.trim()) body.report_webhook = $('#s-hook').value.trim();
    if ($('#s-level').value !== e.autonomy_level) body.autonomy_level = $('#s-level').value;
    act(ev.target, () => api('/employees/' + e.slug, { method: 'PATCH', body }).then(route), 'Salvo');
  };
  $('#tw-save').onclick = ev => act(ev.target, () => api('/employees/' + e.slug, { method: 'PATCH', body: {
    colleagues: [...t.querySelectorAll('.tw-col:checked')].map(c => c.value), plan_policy: $('#tw-plan').value, recall: $('#tw-recall').checked,
    quality_sample: Number($('#tw-quality').value), review_policy: $('#tw-review').value, reports_to: $('#tw-chief').value || null } }).then(route), 'Salvo');
  $('#ib-save').onclick = ev => {
    const body = { host: $('#ib-host').value.trim(), port: Number($('#ib-port').value) || 993, user: $('#ib-user').value.trim(),
      folder: $('#ib-folder').value.trim() || 'INBOX', allowed: lines($('#ib-allowed').value), enabled: $('#ib-on').checked };
    if ($('#ib-pass').value) body.password = $('#ib-pass').value;
    act(ev.target, () => api(`/employees/${e.slug}/inbox`, { method: 'PUT', body }).then(route), 'Caixa salva');
  };
  $('#ib-test').onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/inbox/test`, { method: 'POST' })
    .then(r => toast(`${window.t('Conectado')}: ${r.unseen} ${window.t('não lido(s)')}`)));
  $('#s-off').onclick = ev => {
    const reason = prompt(`Desligar ${e.name}? Motivo (fica na auditoria):`);
    if (reason !== null) act(ev.target, () => api(`/employees/${e.slug}/status`, { method: 'POST', body: { to: 'offboarded', reason } }).then(route), 'Desligado');
  };
  t.insertAdjacentHTML('beforeend', '<div id="s-ptools"></div>');
  platformToolsCard($('#s-ptools'), e);  // análise, extração, calculadoras… (analyst.js)
}

/* ---------- tarefa: linha do tempo ---------- */
const EVENT_LABEL = { created: 'Criada', run: 'Rodada', tool: 'Ferramenta', gate: 'Alçada', human_request: 'Pedido de decisão',
  decision: 'Decisão', job: 'Job de código', checkpoint: 'Checkpoint', done: 'Concluída', error: 'Erro', note: 'Nota', due: 'Prazo', shadow: 'Modo sombra',
  plan: 'Plano', handoff: 'Repasse', handoff_done: 'Repasse concluído', analysis: 'Análise', extraction: 'Extração', calc: 'Cálculo',
  file: 'Arquivo', review: 'Revisão' };
async function taskPage(id) {
  const x = await api('/tasks/' + id);
  const open = (x.requests || []).filter(r => r.status === 'open');
  main.innerHTML = `<a href="javascript:history.back()" class="mute small">← voltar</a>
  <div class="row between"><div><h1 data-noi18n>#${x.id} ${esc(x.title)}</h1><div class="sub">${taskPill(x.status)} pedida por ${esc(x.requester)}${x.via ? ` <span>pelo</span> ${esc(VIA_LABEL[x.via] || x.via)}` : ''} · ${when(x.created_at)}
    · ${x.runs} rodada(s) · ${usd(x.cost_usd)}</div>${duePill(x)}</div>
    <span class="row" style="gap:6px">${['new', 'blocked', 'waiting_task', 'waiting_budget'].includes(x.status) ? '<button class="ghost" id="t-hold">Pausar</button>' : ''}
      ${x.status === 'on_hold' ? '<button id="t-resume">Retomar</button>' : ''}
      ${['done', 'failed', 'cancelled', 'expired'].includes(x.status) ? '' : '<button class="ghost danger" id="t-cancel">Cancelar tarefa</button>'}</span></div>
  ${x.review_required && !['done', 'failed', 'cancelled'].includes(x.status) ? '<div class="small mute"><span class="pill warn">revisão</span> <span>o resultado passa por revisão antes de concluir</span></div>' : ''}
  ${x.body ? `<div class="card"><h2>Pedido</h2><div data-noi18n style="white-space:pre-wrap">${esc(x.body)}</div>${x.expected ? `<div class="mute small mt">Esperado: <span data-noi18n>${esc(x.expected)}</span></div>` : ''}</div>` : ''}
  ${x.result || x.error ? `<div class="card mt ${x.error ? 'bad-card' : ''}"><h2>${x.error ? 'Erro' : 'Resultado'}</h2><div class="md" data-noi18n>${x.result ? mdLite(x.result) : esc(x.error)}</div></div>` : ''}
  ${(x.goals || []).length || x.project ? `<div class="card mt small">${x.project ? `${icon('folder')} <a href="#/projects/${esc(x.project.slug)}" data-noi18n>${esc(x.project.name)}</a>` : ''}
    ${(x.goals || []).length ? ` ${icon('target')} <span>Serve a:</span> ${x.goals.map(g => `<span data-noi18n>${esc(g.title)}</span>`).join(' → ')}` : ''}</div>` : ''}
  ${threadView(x)}
  ${x.status === 'done' ? qualityView(x) : ''}
  ${open.length ? `<div class="mt">${open.map(decisionCard).join('')}</div>` : ''}
  ${diagnosisView(x)}${depsView(x)}${filesView(x)}
  ${planView(x)}${linkView(x)}
  <div class="card mt"><h2>Linha do tempo</h2>${(x.events || []).map(ev => `<div class="li small"><div class="row between"><b>${esc(EVENT_LABEL[ev.kind] || ev.kind)}</b><span class="mute">${ago(ev.at)}</span></div>
    <div class="mute one-line" data-noi18n title="${esc(JSON.stringify(ev.payload))}">${esc(JSON.stringify(ev.payload))}</div></div>`).join('') || '<div class="mute">—</div>'}</div>`;
  bindThread(id);
  bindTaskFlow(x, id);
  taskExtras(x, id);  // campos extraídos, contas, conversas… (analyst.js)
  main.querySelectorAll('.q-star').forEach(b => b.onclick = ev => act(ev.target, () => api(`/tasks/${id}/quality`, { method: 'POST',
    body: { score: Number(b.dataset.s), note: $('#q-note').value.trim() } }).then(route), 'Nota salva'));
  const c = $('#t-cancel');
  if (c) c.onclick = ev => confirm('Cancelar esta tarefa? As decisões abertas dela também são canceladas.') &&
    act(ev.target, () => api(`/tasks/${id}/cancel`, { method: 'POST' }).then(route), 'Tarefa cancelada');
  bindDecisions(main, () => route());
}

const VIA_LABEL = { slack: 'Slack', teams: 'Teams' };

function qualityView(x) {
  const has = x.quality != null;
  return `<div class="card mt"><div class="row between"><h2>Qualidade do resultado</h2>
      ${has ? `<span><span class="pill ${x.quality >= 4 ? 'ok' : x.quality <= 2 ? 'bad' : 'warn'}">${x.quality}/5</span>
        <span class="mute small">${x.quality_by === 'judge' ? esc(window.t('pelo juiz (amostra)')) : `<span>por</span> ${esc(x.quality_by)}`}</span></span>` : '<span class="mute small">sem nota</span>'}</div>
    ${x.quality_note ? `<div class="small" data-noi18n>${esc(x.quality_note)}</div>` : ''}
    <div class="row mt" style="gap:6px;flex-wrap:wrap"><span class="small mute">Sua nota (gestor):</span>
      ${[1, 2, 3, 4, 5].map(n => `<button class="ghost small q-star" data-s="${n}">${n}</button>`).join('')}
      <input id="q-note" placeholder="o que estava bom ou faltou (opcional)" style="flex:1;min-width:200px"></div>
    <div class="mute small mt">A sua nota substitui a do juiz e entra no plano de carreira (média dos últimos 30 dias).</div></div>`;
}

/* ---------- painel do gestor ---------- */
async function managerPage() {
  const qs = new URLSearchParams(location.hash.split('?')[1] || '');
  const days = Number(qs.get('days')) || 30, team = qs.get('team') || '';
  const d = await api(`/workforce/dashboard?days=${days}${team ? `&team=${encodeURIComponent(team)}` : ''}`);
  const b = d.bottlenecks, pct = v => v == null ? '—' : `${fmt(v)}%`, num = v => v == null ? '—' : fmt(v);
  const teams = [...new Set(d.employees.map(r => r.team).filter(Boolean))];
  main.innerHTML = `<a href="#/employees" class="mute small">← Digital employees</a>
  <div class="row between"><div><h1>Painel do gestor</h1><div class="sub">Os Digital employees que você gerencia: custo, decisões, prazos, qualidade e onde o trabalho está parado.</div></div>
    <span class="row" style="gap:6px"><select id="mg-days">${[7, 30, 90].map(n => `<option value="${n}" ${n === days ? 'selected' : ''}>${n} ${window.t('dias')}</option>`).join('')}</select>
      <select id="mg-team"><option value="">${window.t('Todos os times')}</option>${teams.map(t => `<option ${t === team ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></span></div>
  <div class="board">${kpi(fmt(d.total.employees), 'Digital employees')}${kpi(fmt(d.total.done), 'Concluídas')}${kpi(fmt(d.total.failed), 'Com falha')}
    ${kpi(usd(d.total.cost_usd), 'Custo')}${kpi(fmt(d.total.pending_decisions), 'Decisões abertas')}${kpi(fmt(d.total.overdue), 'Tarefas atrasadas')}</div>
  ${!d.employees.length ? '<div class="card mute mt">Nenhum Digital employee sob a sua gestão.</div>' : `
  <div class="grid g2 mt"><div class="card"><h2>Gargalos</h2>
      ${b.decisions.length ? `<h3>Decisões paradas</h3>${b.decisions.slice(0, 8).map(x => `<div class="li small row between"><span><b>${esc(x.employee_name)}</b>
        ${x.task_id ? `<a href="#/tasks/${x.task_id}">#${x.task_id}</a>` : ''} <span class="mute">${esc(ACTION_LABEL[x.action] || x.action)}</span></span>
        <span><span class="mute">${esc(x.holder)}</span> · <span class="pill ${x.age_min > 240 ? 'bad' : x.age_min > 60 ? 'warn' : ''}">${dur(x.age_min)}</span></span></div>`).join('')}` : ''}
      ${b.holders.length ? `<h3 class="mt">Com quem está</h3>${b.holders.map(h => `<div class="li small row between"><b>${esc(h.who)}</b>
        <span>${h.pending} <span class="mute">${window.t('pendente(s), a mais antiga há')}</span> ${dur(h.oldest_min)}</span></div>`).join('')}` : ''}
      ${b.overdue.length ? `<h3 class="mt">Tarefas atrasadas</h3>${b.overdue.map(x => `<div class="li small"><a href="#/tasks/${x.id}"><b data-noi18n>#${x.id} ${esc(x.title)}</b></a>
        <span class="mute">${esc(x.employee_name)}</span></div>`).join('')}` : ''}
      ${b.stuck.length ? `<h3 class="mt">Esperando há mais de 24 h</h3>${b.stuck.map(x => `<div class="li small"><a href="#/tasks/${x.id}"><b data-noi18n>#${x.id} ${esc(x.title)}</b></a>
        <span class="mute">${esc(x.employee_name)} · ${x.hours} h</span></div>`).join('')}` : ''}
      ${b.failing.length ? `<h3 class="mt">Falhando muito</h3>${b.failing.map(x => `<div class="li small"><a href="#/employees/${esc(x.slug)}"><b>${esc(x.name)}</b></a>
        <span class="pill bad">${pct(x.fail_rate)}</span></div>`).join('')}` : ''}
      ${!b.decisions.length && !b.overdue.length && !b.stuck.length && !b.failing.length ? '<div class="mute">Nada parado agora.</div>' : ''}</div>
    <div class="card"><h2>Por time</h2><table class="small"><tr><th>Time</th><th>Concluídas</th><th>Custo</th><th>No prazo</th><th>Nota</th><th>Decisão (média)</th></tr>
      ${d.teams.map(g => `<tr><td data-noi18n>${esc(g.team)}</td><td>${fmt(g.done)}</td><td>${usd(g.cost_usd)}</td><td>${pct(g.on_time)}</td>
        <td>${g.quality == null ? '—' : g.quality.toFixed(1)}</td><td>${g.avg_decision_min == null ? '—' : dur(g.avg_decision_min)}</td></tr>`).join('')}</table></div></div>
  <div class="card mt"><h2>Por Digital employee</h2><table class="small"><tr><th>Digital employee</th><th>Concluídas</th><th>Falha</th><th>Custo</th><th>Por tarefa</th>
      <th>No prazo</th><th>Nota</th><th>Decisão (média)</th><th>Abertas</th><th>Decisões</th></tr>
    ${d.employees.map(r => `<tr><td><a href="#/employees/${esc(r.slug)}"><b>${esc(r.name)}</b></a><div class="mute" data-noi18n>${esc(r.title)}</div></td>
      <td>${fmt(r.done)}</td><td>${pct(r.fail_rate)}</td><td>${usd(r.cost_usd)}</td><td>${usd(r.cost_per_task)}</td><td>${pct(r.on_time)}</td>
      <td>${r.quality == null ? '—' : `${r.quality.toFixed(1)} <span class="mute">(${r.rated})</span>`}</td>
      <td>${r.avg_decision_min == null ? '—' : dur(r.avg_decision_min)}</td><td>${num(r.open)}${r.overdue ? ` <span class="pill bad">${r.overdue}</span>` : ''}</td>
      <td>${r.pending_decisions ? `<span class="pill warn">${r.pending_decisions}</span>` : '0'}</td></tr>`).join('')}</table></div>`}`;
  const go = () => { location.hash = `#/manager?days=${$('#mg-days').value}${$('#mg-team').value ? `&team=${encodeURIComponent($('#mg-team').value)}` : ''}`; };
  $('#mg-days').onchange = go;
  $('#mg-team').onchange = go;
}

function dur(min) {
  min = Math.round(min || 0);
  return min < 60 ? `${min} min` : min < 1440 ? `${Math.round(min / 6) / 10} h` : `${Math.round(min / 144) / 10} d`;
}

/* ---------- decisões (humano no circuito) ---------- */
function decisionCard(d) {
  const head = `<div class="row between"><div><span class="pill ${d.kind === 'approval' ? 'warn' : d.kind === 'admission' ? 'info' : ''}">${esc(KIND_LABEL[d.kind] || d.kind)}</span>
      <a href="#/employees/${esc(d.employee)}"><b>${esc(d.employee_name)}</b></a> <span class="mute small" data-noi18n>${esc(d.employee_title)}</span>
      ${d.task ? `<div class="small">Tarefa <a href="#/tasks/${d.task.id}" data-noi18n>#${d.task.id} ${esc(d.task.title)}</a> · pedida por ${esc(d.task.requester)}</div>` : ''}</div>
    <div class="small mute" style="text-align:right">${when(d.created_at)}${d.expires_at ? `<br>expira em ${esc(new Date(d.expires_at).toLocaleString(window.LOCALE || 'pt-BR'))}` : ''}
      ${d.escalated ? '<br><span class="pill warn">escalado ao substituto</span>' : ''}${d.mine ? '' : `<br>atribuída a ${esc(d.assigned_to || '—')}`}</div></div>`;
  const sel = d.kind === 'approval' || d.kind === 'notice' ? `<input type="checkbox" class="dc-pick" data-id="${d.id}" data-kind="${d.kind}" title="Selecionar"> ` : '';
  let body = '', btns = '';
  if (d.kind === 'approval') {
    body = `<div class="mt"><b>${esc(ACTION_LABEL[d.action_type] || d.action_type)}</b> com <code class="inline">${esc(d.tool)}</code> ${modePill(d.mode)}
      ${d.reversible ? '<span class="pill ok">reversível</span>' : '<span class="pill bad">irreversível</span>'}
      ${d.mode === 'approve_2' ? `<span class="pill">${(d.approvals || []).length}/2 aprovações</span>` : ''}</div>
      ${d.rationale ? `<div class="small mt">Por quê: <span data-noi18n>${esc(d.rationale)}</span></div>` : ''}${payloadView(d.payload)}
      <textarea class="dc-edit code" rows="5" hidden data-noi18n>${esc(JSON.stringify(d.payload, null, 2))}</textarea>
      <input class="dc-reason mt" placeholder="Motivo ou instrução (para recusar ou instruir)">`;
    btns = `<button class="dc-go" data-d="approve">Aprovar</button><button class="ghost dc-edit-open">Editar e aprovar</button>
      <button class="ghost dc-go" data-d="instruct">Instruir</button><button class="ghost danger dc-go" data-d="reject">Recusar</button>`;
  } else if (d.kind === 'question') {
    body = `<div class="mt" data-noi18n><b>${esc(d.question)}</b></div><textarea class="dc-reason mt" rows="3" placeholder="Sua resposta"></textarea>`;
    btns = '<button class="dc-go" data-d="answer">Responder</button>';
  } else if (d.kind === 'admission') {
    body = `<div class="mt">Terminou o período de experiência. <a href="#/employees/${esc(d.employee)}/probation">Ver os resultados</a> e decidir se ele vai para produção.</div>
      <input class="dc-reason mt" placeholder="Comentário (opcional)">`;
    btns = '<button class="dc-go" data-d="approve">Admitir em produção</button><button class="ghost danger dc-go" data-d="reject">Ainda não</button>';
  } else if (d.kind === 'review') {
    body = `<div class="mt"><b>Resultado para revisar</b> — a tarefa só conclui com a sua aprovação.</div>
      <div class="card mt md" data-noi18n style="max-height:320px;overflow:auto">${mdLite((d.payload || {}).result || d.question || '')}</div>
      <input class="dc-reason mt" placeholder="O que mudar (para pedir mudanças)">`;
    btns = '<button class="dc-go" data-d="approve">Aprovar e concluir</button><button class="ghost danger dc-go" data-d="reject">Pedir mudanças</button>';
  } else if (d.kind === 'plan') {
    const steps = (d.payload || {}).steps || [];
    body = `<div class="mt"><b>Plano proposto</b> (${steps.length} etapas) — a execução só começa depois da sua aprovação.</div>
      ${d.rationale ? `<div class="small mt" data-noi18n>${esc(d.rationale)}</div>` : ''}
      <ol class="small mt" data-noi18n>${steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol>
      <textarea class="dc-steps" rows="${Math.max(3, steps.length + 1)}" hidden data-noi18n>${esc(steps.join('\n'))}</textarea>
      <input class="dc-reason mt" placeholder="O que mudar (para recusar ou instruir)">`;
    btns = `<button class="dc-go" data-d="approve">Aprovar o plano</button><button class="ghost dc-steps-open">Editar e aprovar</button>
      <button class="ghost dc-go" data-d="instruct">Instruir</button><button class="ghost danger dc-go" data-d="reject">Recusar</button>`;
  } else if (d.kind === 'capability') {
    const p = d.payload || {}, opts = p.options || [];
    body = `<div class="mt"><b>Falta uma capacidade:</b> <span data-noi18n>${esc(d.question)}</span></div>
      ${d.rationale ? `<div class="small mt">Por quê: <span data-noi18n>${esc(d.rationale)}</span></div>` : ''}
      <div class="small mute mt">O que o catálogo já tem:</div>
      ${opts.length ? `<div class="mt">${opts.map(o => `<div class="row between li" style="gap:10px"><span><b data-noi18n>${esc(o.name)}</b>
          ${o.harness ? '<span class="chip">harness</span>' : ''}<div class="mute small one-line" data-noi18n style="max-width:620px">${esc(o.objective || '')}</div></span>
          ${o.can_use && o.in_production ? `<button class="ghost dc-attach" data-s="${esc(o.slug)}">Anexar</button>`
            : `<span class="pill">${o.in_production ? 'peça acesso ao time dono' : 'fora de produção'}</span>`}</div>`).join('')}</div>`
        : '<div class="small mute">Nada parecido no catálogo.</div>'}
      ${p.build_requested_by ? `<div class="small mt"><span class="pill info">construção pedida</span> por ${esc(p.build_requested_by)}</div>` : ''}
      <div class="dc-build-out"></div>
      <input class="dc-reason mt" placeholder="Motivo ou instrução (para recusar ou instruir)">`;
    btns = `<button class="ghost dc-build">Construir com a sua ferramenta de IA</button>
      <button class="ghost dc-go" data-d="instruct">Instruir</button><button class="ghost danger dc-go" data-d="reject">Recusar</button>`;
  } else if (DECISION_KINDS[d.kind]) {  // tipos trazidos pelas fases E–H (analyst.js)
    ({ body, btns } = DECISION_KINDS[d.kind].render(d));
  } else {
    body = `<div class="mt small">Feito: <b>${esc(ACTION_LABEL[d.action_type] || d.action_type)}</b> com <code class="inline">${esc(d.tool)}</code></div>${payloadView(d.payload)}`;
    btns = '<button class="ghost dc-go" data-d="ack">Ciente</button>';
  }
  return `<div class="card mt dc" data-id="${d.id}" data-kind="${esc(d.kind)}">${sel}${head}${body}<div class="row mt">${btns}</div></div>`;
}

function bindDecisions(root, after) {
  root.querySelectorAll('.dc').forEach(card => {
    const id = card.dataset.id;
    if (DECISION_KINDS[card.dataset.kind]) return DECISION_KINDS[card.dataset.kind].bind(card, id, after);
    const edit = $('.dc-edit', card), reason = $('.dc-reason', card);
    const openEdit = $('.dc-edit-open', card);
    if (openEdit) openEdit.onclick = () => {
      if (edit.hidden) { edit.hidden = false; openEdit.textContent = 'Aprovar a versão editada'; return; }
      let payload; try { payload = JSON.parse(edit.value); } catch (err) { return toast('JSON inválido', true); }
      act(openEdit, () => api(`/decisions/${id}`, { method: 'POST', body: { decision: 'approve_edited', edit: payload, reason: reason ? reason.value : '' } }).then(after), 'Aprovado com edição');
    };
    const stepsOpen = $('.dc-steps-open', card), steps = $('.dc-steps', card);
    if (stepsOpen) stepsOpen.onclick = () => {
      if (steps.hidden) { steps.hidden = false; stepsOpen.textContent = window.t('Aprovar o plano editado'); return; }
      act(stepsOpen, () => api(`/decisions/${id}`, { method: 'POST', body: { decision: 'approve_edited', edit: { steps: lines(steps.value) },
        reason: reason ? reason.value : '' } }).then(after), 'Plano aprovado com edição');
    };
    card.querySelectorAll('.dc-attach').forEach(b => b.onclick = () => confirm('Anexar este especialista? Vira uma versão nova do agente, com testes (e a aprovação de produção, se o time exigir).') &&
      act(b, () => api(`/decisions/${id}`, { method: 'POST', body: { decision: 'attach', edit: { specialist: b.dataset.s } } })
        .then(r => { if (r.message) toast(r.message); return after(); }), 'Anexado'));
    const build = $('.dc-build', card);
    if (build) build.onclick = () => act(build, () => api(`/decisions/${id}`, { method: 'POST', body: { decision: 'build' } }).then(r => {
      $('.dc-build-out', card).innerHTML = `<div class="card warn-card mt"><b>Peça isto na sua ferramenta de IA (modo self):</b>
        <div class="mt">${copyable(r.build_prompt)}</div><div class="mute small mt">Quando o agente estiver em produção, volte aqui e anexe-o. O pedido fica aberto por 7 dias.</div></div>`;
    }));
    card.querySelectorAll('.dc-go').forEach(b => b.onclick = () => {
      const d = b.dataset.d, why = reason ? reason.value.trim() : '';
      if (['reject', 'instruct', 'answer'].includes(d) && !why && (d !== 'reject' || card.querySelector('.dc-steps'))) return toast(d === 'answer' ? 'Escreva a resposta' : d === 'reject' ? 'Diga o que mudar no plano' : 'Escreva a instrução', true);
      act(b, () => api(`/decisions/${id}`, { method: 'POST', body: { decision: d, reason: why } }).then(r => {
        if (r.status === 'open') toast('Primeira aprovação registrada — falta a segunda, de outra pessoa');
        if (r.message) toast(r.message);
        return after();
      }), { approve: 'Aprovado', reject: 'Recusado', instruct: 'Instrução enviada', answer: 'Resposta enviada', ack: 'Ok' }[d]);
    });
  });
}

async function decisionsPage() {
  const list = await api('/decisions');
  ME.decisions = list.filter(d => d.kind !== 'notice').length; renderSide();
  const mine = list.filter(d => d.mine), other = list.filter(d => !d.mine);
  main.innerHTML = `<div class="row between"><div><h1>Decisões (${list.length})</h1>
    <div class="sub">O que os Digital employees precisam de você: aprovar a ação exata, responder uma pergunta, admitir depois da experiência ou tomar ciência.</div></div>
    ${list.some(d => d.kind === 'approval' || d.kind === 'notice') ? `<div class="row"><button class="ghost" id="dc-all">Selecionar todos</button>
      <button class="ghost" id="dc-ack">Ciente nos avisos selecionados</button><button id="dc-ok">Aprovar selecionados</button></div>` : ''}</div>
  ${list.length ? '' : '<div class="card mute">Nada esperando por você. Quando um Digital employee precisar de uma decisão, ela aparece aqui (e no webhook do relatório, se houver).</div>'}
  ${mine.length ? `<h2 class="mt">Para você (${mine.length})</h2>${mine.map(decisionCard).join('')}` : ''}
  ${other.length ? `<h2 class="mt">Você também pode decidir (${other.length})</h2>${other.map(decisionCard).join('')}` : ''}
  <div id="nt-mine" class="mt"></div>`;
  bindDecisions(main, decisionsPage);
  ntMine($('#nt-mine'));
  const picked = kind => [...document.querySelectorAll('.dc-pick:checked')].filter(c => c.dataset.kind === kind).map(c => Number(c.dataset.id));
  const batch = (btn, kind, decision, msg) => {
    const ids = picked(kind);
    if (!ids.length) return toast(kind === 'notice' ? 'Selecione avisos' : 'Selecione aprovações', true);
    if (decision === 'approve' && !confirm(`Aprovar ${ids.length} ação(ões) exatamente como estão?`)) return;
    act(btn, () => api('/decisions/batch', { method: 'POST', body: { ids, decision } }).then(r => {
      const bad = r.filter(x => x.error); if (bad.length) toast(bad.map(x => `#${x.id}: ${x.error}`).join(' · '), true);
      return decisionsPage();
    }), msg);
  };
  const on = (id, fn) => { const b = $(id); if (b) b.onclick = fn; };
  on('#dc-all', () => document.querySelectorAll('.dc-pick').forEach(c => { c.checked = true; }));
  on('#dc-ok', ev => batch(ev.target, 'approval', 'approve', 'Aprovados'));
  on('#dc-ack', ev => batch(ev.target, 'notice', 'ack', 'Ok'));
}

/* ---------- onde a pessoa recebe as decisões (Slack, Teams, e-mail) ---------- */
const NT_CHANNEL = { auto: 'Automático', slack: 'Slack', teams: 'Microsoft Teams', email: 'E-mail', off: 'Só no portal' };
async function ntMine(box) {
  let n;
  try { n = await api('/me/notifications'); } catch { return; }  // token de admin: sem pessoa, sem preferência
  const via = n.route ? NT_CHANNEL[n.route] : 'Só no portal';
  box.innerHTML = `<details class="card"${n.route ? '' : ' open'}><summary><b>Receber decisões fora do portal</b> <span class="mute small">— <span>agora:</span> ${esc(via)}</span></summary>
    <div class="mute small mt">Decida na própria mensagem: no Slack com botões (aprovar, editar, recusar, responder); no Teams e no e-mail, os botões abrem
      um link só seu, que vale para aquele pedido e só decide quando você confirma. Também chegam o alerta antes de um pedido expirar e, se quiser, o resumo diário
      das pendências.</div>
    <div class="grid g2 mt"><label>Canal<select id="nt-ch">${Object.entries(NT_CHANNEL).map(([k, v]) => `<option value="${k}" ${k === n.channel ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label>Webhook do Teams (Workflows: “postar num chat quando receber uma solicitação de webhook”)<input id="nt-teams" data-noi18n placeholder="${n.teams_webhook ? 'cadastrado — cole outro para trocar' : 'https://…'}"></label></div>
    <label class="row small mt" style="gap:6px"><input type="checkbox" id="nt-digest" ${n.digest ? 'checked' : ''}> <span>Resumo diário das minhas pendências</span>${n.digest_hour >= 0 ? ` <span class="mute">(às ${n.digest_hour}h)</span>` : ''}</label>
    <div class="mute small mt"><span>Disponível:</span> <b>${n.available.length ? n.available.map(c => NT_CHANNEL[c]).join(', ') : 'nenhum canal ainda'}</b>
      ${n.slack_ready ? '' : '<div>O Slack depende do admin ligar o app da empresa.</div>'}${n.email_ready ? '' : '<div>O e-mail depende do SMTP da instalação.</div>'}</div>
    <div class="row mt"><button id="nt-save">Salvar</button><button class="ghost" id="nt-test" ${n.route ? '' : 'disabled'}>Enviar um teste</button>
      ${n.teams_webhook ? '<button class="ghost" id="nt-teams-off">Remover webhook do Teams</button>' : ''}</div>
    ${chatLinks(n.chat || {})}</details>`;
  const save = body => api('/me/notifications', { method: 'PUT', body }).then(() => ntMine(box));
  $('#nt-save').onclick = ev => {
    const body = { channel: $('#nt-ch').value, digest: $('#nt-digest').checked };
    const hook = $('#nt-teams').value.trim();
    if (hook) body.teams_webhook = hook;
    act(ev.target, () => save(body), 'Preferência salva');
  };
  $('#nt-test').onclick = ev => act(ev.target, () => api('/me/notifications/test', { method: 'POST' }).then(r => toast(`Teste enviado por ${NT_CHANNEL[r.via] || r.via}`)));
  const off = $('#nt-teams-off');
  if (off) off.onclick = ev => act(ev.target, () => save({ teams_webhook: '' }), 'Webhook removido');
  box.querySelectorAll('.cl-link').forEach(btn => btn.onclick = ev => act(ev.target, async () => {
    const r = await api('/me/notifications/chat-link', { method: 'POST', body: { channel: btn.dataset.ch } });
    $('#cl-out').innerHTML = `<div class="card warn-card mt"><span>Mande isto para o bot (vale 15 minutos):</span> ${copyable(r.how)}
      ${r.url ? `<div class="mt"><a href="${esc(r.url)}" target="_blank" rel="noopener">${window.t('Abrir o Telegram')}</a></div>` : ''}</div>`;
  }));
  box.querySelectorAll('.cl-off').forEach(btn => btn.onclick = ev => act(ev.target, () => api(`/me/notifications/chat-link/${btn.dataset.ch}`, { method: 'DELETE' }).then(() => ntMine(box)), 'Conta desligada'));
}

/* Telegram e Discord: pedir trabalho pelo chat. Cada pessoa liga a conta com um código de uso único. */
function chatLinks(c) {
  const rows = [['telegram', 'Telegram'], ['discord', 'Discord']].filter(([k]) => c[`${k}_ready`]);
  if (!rows.length) return '';
  return `<h3 class="mt">Pedir trabalho pelo chat</h3><div class="mute small">Ligue a sua conta para pedir tarefas aos Digital employees por mensagem; o resultado volta na conversa.</div>
    ${rows.map(([k, label]) => `<div class="row small mt" style="gap:8px"><b>${label}</b>${(c.linked || {})[k] ? `<span class="pill ok">ligado</span><button class="ghost small cl-off" data-ch="${k}">Desligar</button>`
      : `<button class="ghost small cl-link" data-ch="${k}">${window.t('Conectar')} ${label}</button>`}</div>`).join('')}<div id="cl-out"></div>`;
}

/* ---------- console do admin: força de trabalho digital ---------- */
async function workforcePage(tab = 'overview') {
  const tabs = { overview: 'Visão geral', catalog: 'Catálogo de ações', floor: 'Piso da empresa', notify: 'Canais de decisão', brand: 'Entregáveis', calendar: 'Calendário' };
  const key = tabs[tab] ? tab : 'overview';
  main.innerHTML = `<h1>Digital employees</h1><div class="sub">A força de trabalho digital da empresa: quem está trabalhando, o que espera decisão, como cada ferramenta é classificada e a alçada mínima que vale para todos.</div>
  <div class="tabs">${Object.entries(tabs).map(([k, v]) => `<a href="#/workforce/${k}" class="${k === key ? 'on' : ''}">${v}</a>`).join('')}</div><div id="tab"></div>`;
  await ({ overview: wfOverview, catalog: wfCatalog, floor: wfFloor, notify: wfNotify, brand: wfBrand, calendar: wfCalendar })[key]($('#tab'));
}

async function wfOverview(t) {
  const w = await api('/admin/workforce');
  const s = w.statuses || {}, ag = w.decisions_aging || {};
  t.innerHTML = `<div class="board">${kpi(fmt(s.active), 'Ativos')}${kpi(fmt(s.probation), 'Em experiência')}${kpi(fmt(s.paused), 'Pausados')}
    ${kpi(fmt((ag['<1h'] || 0) + (ag['1-4h'] || 0) + (ag['>4h'] || 0)), 'Decisões abertas')}${kpi(fmt(ag['>4h']), 'Abertas há mais de 4h')}${kpi(fmt(w.expired_30d), 'Expiradas (30 dias)')}</div>
  ${w.unclassified_tools ? `<div class="card warn-card mt">${w.unclassified_tools} ferramenta(s) classificada(s) por palpite — revise no <a href="#/workforce/catalog">Catálogo de ações</a>.</div>` : ''}
  <div class="grid g2 mt"><div class="card"><h2>Decisões abertas por idade</h2>${hlist(ag)}</div>
    <div class="card"><h2>Aprovações por tipo (30 dias)</h2>${hlist(Object.fromEntries(Object.entries(w.approvals_by_type_30d || {}).map(([k, v]) => [ACTION_LABEL[k] || k, v])))}</div></div>
  <div class="card mt"><div class="row between"><h2>Todos (${w.employees.length})</h2>
    ${ME.is_admin ? '<button class="danger" id="wf-stop">Parar todos agora</button>' : ''}</div>
    ${w.employees.length ? `<table><tr><th>Digital employee</th><th>Status</th><th>Gestor</th><th>Nível</th><th>Decisões</th><th>Custo 30d</th></tr>
      ${w.employees.map(e => `<tr><td><a href="#/employees/${esc(e.slug)}"><b>${esc(e.name)}</b></a><div class="mute small" data-noi18n>${esc(e.title)}</div></td><td>${empPill(e.status)}</td>
        <td class="small">${esc(e.manager || '—')}</td><td class="small">${esc(LEVEL_LABEL[e.autonomy_level] || e.autonomy_level)}</td>
        <td>${e.open_decisions ? `<span class="pill warn">${e.open_decisions}</span>` : '0'}</td><td class="small">${usd(e.cost_30d)}</td></tr>`).join('')}</table>`
      : '<div class="mute">Nenhum Digital employee ainda. Os donos contratam pelo portal ou pelo MCP.</div>'}</div>`;
  const b = $('#wf-stop');
  if (b) b.onclick = ev => {
    const reason = prompt('Pausar TODOS os Digital employees agora? Nenhuma ação passa pelo gate até cada gestor retomar. Motivo:');
    if (reason !== null) act(ev.target, () => api('/admin/workforce/stop-all', { method: 'POST', body: { reason } }).then(r => { toast(`${r.paused} pausado(s)`); return wfOverview(t); }));
  };
}

async function wfCatalog(t) {
  const list = await api('/admin/action-catalog');
  const review = list.filter(x => x.review).length;
  t.innerHTML = `<div class="card"><h2>Catálogo de ações (${list.length})</h2>
    <div class="mute small">Cada ferramenta que um Digital employee usa tem um tipo de ação, e é o tipo que decide a alçada. A plataforma classifica sozinha na primeira vez;
      as marcadas <span class="pill warn">revisar</span> foram por palpite (pelo nome) — confirme ou corrija. ${review ? `<b>${review} para revisar.</b>` : ''}</div>
    ${list.length ? `<table class="mt"><tr><th>Ferramenta</th><th>Tipo de ação</th><th>Risco</th><th>Reversível</th><th>Classificada por</th><th></th></tr>
      ${list.map((c, i) => `<tr data-ref="${esc(c.tool_ref)}"><td><code class="inline" data-noi18n>${esc(c.tool_ref)}</code> ${c.review ? '<span class="pill warn">revisar</span>' : ''}</td>
        <td><select class="c-type">${Object.entries(ACTION_LABEL).map(([k, v]) => `<option value="${k}" ${k === c.action_type ? 'selected' : ''}>${v}</option>`).join('')}</select></td>
        <td><input class="c-risk" type="number" min="1" max="5" value="${c.risk}" style="width:70px"></td>
        <td><input type="checkbox" class="c-rev" ${c.reversible ? 'checked' : ''}></td><td class="mute small">${esc(c.classified_by)}</td>
        <td><button class="ghost c-save">${c.review ? 'Confirmar' : 'Salvar'}</button></td></tr>`).join('')}</table>`
      : '<div class="mute mt">Vazio: as ferramentas entram aqui na primeira vez que um Digital employee tenta usá-las.</div>'}</div>`;
  t.querySelectorAll('.c-save').forEach(b => b.onclick = () => {
    const tr = b.closest('tr');
    act(b, () => api('/admin/action-catalog', { method: 'PUT', body: { tool_ref: tr.dataset.ref, action_type: $('.c-type', tr).value,
      risk: Number($('.c-risk', tr).value), reversible: $('.c-rev', tr).checked } }).then(() => wfCatalog(t)), 'Classificação salva');
  });
}

async function wfNotify(t) {
  const n = await api('/admin/notifications');
  const s = n.slack;
  t.innerHTML = `<div class="card"><h2>Slack ${s.configured ? `<span class="pill ok"><span>ligado</span>${s.team ? ` · <span data-noi18n>${esc(s.team)}</span>` : ''}</span>` : '<span class="pill">desligado</span>'}</h2>
    <div class="mute small">Com o app da empresa no Slack, cada pessoa recebe as decisões em mensagem direta, com os botões na própria mensagem. O clique é assinado
      pelo Slack e a decisão é tomada como a pessoa dona daquele e-mail no hangar, com as mesmas regras do portal (quem pode decidir, duas aprovações, separação de funções).</div>
    <ol class="small mt"><li>Crie o app “From a manifest” em <a href="https://api.slack.com/apps?new_app=1" target="_blank" rel="noopener">api.slack.com/apps</a> com o manifesto abaixo e instale no workspace.</li>
      <li>Cole o <b>Bot User OAuth Token</b> (xoxb-…) e o <b>Signing Secret</b> aqui.</li>
      <li>A URL de interatividade precisa ser pública: <code class="inline" data-noi18n>${esc(s.interactions_url)}</code></li>
      <li><span>Tarefa pelo chat (menção ao app ou mensagem direta): o manifesto já assina os eventos nesta URL</span> <code class="inline" data-noi18n>${esc(s.events_url || '')}</code>
        <div class="mute">“@Agent Hangar Analista, renova o contrato da ACME” vira tarefa e o resultado volta na thread.</div></li></ol>
    <details class="mt"><summary class="small">Manifesto do app</summary><pre class="code small" data-noi18n>${esc(s.manifest)}</pre></details>
    <div class="grid g2 mt"><label>Bot token<input id="ns-token" type="password" autocomplete="off" placeholder="${s.bot_token ? `cadastrado (${esc(s.bot_token)}) — cole outro para trocar` : 'xoxb-…'}"></label>
      <label>Signing secret<input id="ns-secret" type="password" autocomplete="off" placeholder="${s.signing_secret ? 'cadastrado — cole outro para trocar' : ''}"></label></div>
    <div class="row mt"><button id="ns-save">Salvar Slack</button>${s.configured ? '<button class="ghost danger" id="ns-off">Desligar Slack</button>' : ''}</div></div>
  <div class="grid g2 mt"><div class="card"><h2>E-mail ${n.email.configured ? '<span class="pill ok">ligado</span>' : '<span class="pill">desligado</span>'}</h2>
      <div class="mute small">${n.email.configured ? `<span>Servidor:</span> <code class="inline" data-noi18n>${esc(n.email.host)}</code> · <span>remetente:</span> <code class="inline" data-noi18n>${esc(n.email.from)}</code>`
        : '<span>Defina no ambiente da central:</span> <code class="inline" data-noi18n>SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_SECURITY</code>'}</div>
      <div class="mute small mt">Os botões do e-mail abrem um link assinado, de uma pessoa e um pedido, que vence em algumas horas e só decide quando ela confirma.</div></div>
    <div class="card"><h2>Microsoft Teams</h2><div class="mute small">Cada pessoa cadastra o próprio webhook (Workflows) na caixa de Decisões: o cartão chega no chat dela, com botões que
      abrem o link assinado. Um webhook de canal do time continua no relatório de cada Digital employee, só com o link do portal (num canal, qualquer um veria o botão).</div>
      <h3 class="mt">Tarefa pelo chat ${n.teams.outgoing ? '<span class="pill ok">ligado</span>' : '<span class="pill">desligado</span>'}</h3>
      <div class="mute small">No canal do Teams: Gerenciar equipe → Aplicativos → Criar um webhook de saída, com a URL abaixo. Cole aqui o token de segurança que o Teams mostra.
        Quem menciona o webhook precisa ter entrado no portal uma vez com a conta Microsoft.</div>
      <div class="small mt">${copyable(n.teams.outgoing_url || '')}</div>
      <div class="row mt" style="gap:6px"><input id="ns-teams" type="password" autocomplete="off" placeholder="${n.teams.outgoing ? 'cadastrado — cole outro para trocar' : 'token de segurança (base64)'}" style="flex:1">
        <button class="ghost" id="ns-teams-save">Salvar</button>${n.teams.outgoing ? '<button class="ghost danger" id="ns-teams-off">Desligar</button>' : ''}</div></div></div>
  <div class="grid g2 mt"><div class="card"><h2>Telegram ${n.telegram.configured ? `<span class="pill ok"><span>ligado</span>${n.telegram.bot ? ` · <span data-noi18n>@${esc(n.telegram.bot)}</span>` : ''}</span>` : '<span class="pill">desligado</span>'}</h2>
      <div class="mute small">Crie o bot com o BotFather e cole o token: a plataforma registra o webhook (com um segredo próprio). Cada pessoa liga a conta dela uma vez em Decisões;
        depois, “Nome do funcionário, pedido” numa conversa com o bot (ou /hangar num grupo) vira tarefa, e o resultado volta na conversa.</div>
      ${n.telegram.webhook_error ? `<div class="pill warn mt" data-noi18n>${esc(n.telegram.webhook_error)}</div><div class="mute small">O Telegram só chama URLs públicas com HTTPS (PUBLIC_BASE_URL).</div>` : ''}
      <div class="row mt" style="gap:6px"><input id="ns-tg" type="password" autocomplete="off" placeholder="${n.telegram.configured ? 'cadastrado — cole outro para trocar' : '123456:ABC…'}" style="flex:1">
        <button class="ghost" id="ns-tg-save">Salvar</button>${n.telegram.configured ? '<button class="ghost danger" id="ns-tg-off">Desligar</button>' : ''}</div></div>
    <div class="card"><h2>Discord ${n.discord.configured ? '<span class="pill ok">ligado</span>' : '<span class="pill">desligado</span>'}</h2>
      <div class="mute small">No Developer Portal do Discord, crie a aplicação com um bot, cole aqui o id, a chave pública e o token do bot, e ponha a URL abaixo em “Interactions Endpoint URL”.
        A plataforma registra o comando /hangar; cada chamada é conferida pela assinatura do Discord.</div>
      <div class="small mt">${copyable(n.discord.interactions_url || '')}</div>
      ${n.discord.command_error ? `<div class="pill warn mt" data-noi18n>${esc(n.discord.command_error)}</div>` : ''}
      <div class="grid g2 mt"><label>Application ID<input id="ns-dc-app" data-noi18n value="${esc(n.discord.app_id || '')}"></label>
        <label>Public key<input id="ns-dc-key" data-noi18n autocomplete="off" placeholder="${n.discord.configured ? 'cadastrada — cole outra para trocar' : ''}"></label>
        <label>Bot token<input id="ns-dc-tok" type="password" autocomplete="off" placeholder="${n.discord.configured ? 'cadastrado — cole outro para trocar' : ''}"></label></div>
      <div class="row mt"><button class="ghost" id="ns-dc-save">Salvar</button>${n.discord.configured ? '<button class="ghost danger" id="ns-dc-off">Desligar</button>' : ''}</div></div></div>
  <div class="card mt"><h2>Prazos e resumo</h2><div class="grid g2">
    <label>Resumo diário das pendências (hora, fuso da empresa; -1 desliga)<input id="ns-hour" type="number" min="-1" max="23" value="${n.digest_hour}"></label>
    <label>Avisar antes de uma decisão expirar (min; 0 desliga)<input id="ns-warn" type="number" min="0" max="1440" value="${n.expiry_warn_min}"></label>
    <label>Tarefa vence logo: avisar e subir a prioridade (min antes do prazo; 0 desliga)<input id="ns-soon" type="number" min="0" max="1440" value="${n.due_soon_min}"></label>
    <label>Tarefa atrasada: escalar para substitutos e mantenedores depois de (min)<input id="ns-esc" type="number" min="5" max="10080" value="${n.overdue_escalate_min}"></label></div>
    <div class="row mt"><button id="ns-times">Salvar prazos</button></div></div>`;
  const put = body => api('/admin/notifications', { method: 'PUT', body }).then(() => wfNotify(t));
  $('#ns-save').onclick = ev => {
    const body = {}, tok = $('#ns-token').value.trim(), sec = $('#ns-secret').value.trim();
    if (tok) body.slack_bot_token = tok;
    if (sec) body.slack_signing_secret = sec;
    if (!tok && !sec) return toast('Cole o token e/ou o signing secret', true);
    act(ev.target, () => put(body), 'Slack salvo');
  };
  const off = $('#ns-off');
  if (off) off.onclick = ev => confirm('Desligar o Slack? As pessoas passam a receber pelo próximo canal disponível.') &&
    act(ev.target, () => put({ slack_bot_token: '', slack_signing_secret: '' }), 'Slack desligado');
  $('#ns-teams-save').onclick = ev => $('#ns-teams').value.trim() ? act(ev.target, () => put({ teams_outgoing_secret: $('#ns-teams').value.trim() }), 'Teams salvo')
    : toast('Cole o token de segurança do Teams', true);
  const toff = $('#ns-teams-off');
  if (toff) toff.onclick = ev => act(ev.target, () => put({ teams_outgoing_secret: '' }), 'Teams desligado');
  $('#ns-tg-save').onclick = ev => $('#ns-tg').value.trim() ? act(ev.target, () => put({ telegram_bot_token: $('#ns-tg').value.trim() }), 'Telegram salvo')
    : toast(window.t('Cole o token do bot do Telegram'), true);
  const tgoff = $('#ns-tg-off');
  if (tgoff) tgoff.onclick = ev => act(ev.target, () => put({ telegram_bot_token: '' }), 'Telegram desligado');
  $('#ns-dc-save').onclick = ev => {
    const body = { discord_app_id: $('#ns-dc-app').value.trim() }, k = $('#ns-dc-key').value.trim(), tok = $('#ns-dc-tok').value.trim();
    if (k) body.discord_public_key = k;
    if (tok) body.discord_bot_token = tok;
    act(ev.target, () => put(body), 'Discord salvo');
  };
  const dcoff = $('#ns-dc-off');
  if (dcoff) dcoff.onclick = ev => act(ev.target, () => put({ discord_bot_token: '', discord_public_key: '' }), 'Discord desligado');
  $('#ns-times').onclick = ev => act(ev.target, () => put({ digest_hour: Number($('#ns-hour').value), expiry_warn_min: Number($('#ns-warn').value),
    due_soon_min: Number($('#ns-soon').value), overdue_escalate_min: Number($('#ns-esc').value) }), 'Prazos salvos');
  notifyExtras(t, n, put);  // WhatsApp, agenda da empresa e transcrições (analyst.js)
}

async function wfFloor(t) {
  const list = await api('/admin/authority-floor');
  t.innerHTML = `<div class="card"><h2>Piso da empresa</h2>
    <div class="mute small">A alçada mínima que vale para todos os Digital employees, acima das regras de cada cargo: uma regra mais frouxa que o piso não vale.
      Separação de funções: quem pediu a tarefa não pode aprovar a ação.</div>
    <table class="mt"><tr><th>Tipo de ação</th><th>Modo mínimo</th><th>Separação de funções</th></tr>
    ${list.map(f => `<tr data-t="${f.action_type}"><td>${esc(ACTION_LABEL[f.action_type] || f.action_type)}</td>
      <td><select class="f-mode">${Object.entries(MODE_LABEL).map(([k, [v]]) => `<option value="${k}" ${k === f.min_mode ? 'selected' : ''}>${v}</option>`).join('')}</select></td>
      <td><input type="checkbox" class="f-sep" ${f.separation ? 'checked' : ''}></td></tr>`).join('')}</table>
    ${ME.is_admin ? '<div class="row mt"><button id="f-save">Salvar piso</button></div>' : ''}</div>`;
  const b = $('#f-save');
  if (b) b.onclick = () => act(b, () => api('/admin/authority-floor', { method: 'PUT', body: { items: [...t.querySelectorAll('tr[data-t]')]
    .map(tr => ({ action_type: tr.dataset.t, min_mode: $('.f-mode', tr).value, separation: $('.f-sep', tr).checked })) } }).then(() => wfFloor(t)), 'Piso salvo');
}
