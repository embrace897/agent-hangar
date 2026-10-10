/* Plugins: conectar qualquer sistema à plataforma com um manifesto declarativo (ferramentas HTTP ou um servidor MCP em
   container, credencial do time, tipos de ação para a alçada, configurações, gatilhos e skills). Vitrine interna,
   rascunho (portal ou Creator mode pelo MCP), testes em stage, revisão de quatro olhos (admin) e instalação por time.
   Usa os helpers de app.js e ACTION_LABEL/mdLite de employees.js. */

const PLUGIN_STATUS = { draft: ['Rascunho', ''], pending: ['Em revisão', 'warn'], approved: ['Liberado', 'ok'],
  rejected: ['Recusado', 'bad'], disabled: ['Desligado', 'bad'] };
const AUTH_LABEL = { none: 'sem autenticação', api_key: 'chave de API', bearer: 'token (Bearer)', basic: 'usuário e senha (Basic)', oauth2: 'conta OAuth2' };
const PLUGIN_CATEGORY = { finance: 'Finanças', sales: 'Vendas', support: 'Atendimento', hr: 'Pessoas', it: 'TI',
  operations: 'Operações', data: 'Dados', communication: 'Comunicação', legal: 'Jurídico', other: 'Outros' };
const plStatus = s => { const [t, c] = PLUGIN_STATUS[s] || [s, '']; return `<span class="pill ${c}">${esc(t)}</span>`; };
const plActions = acts => Object.entries(acts || {}).map(([k, n]) =>
  `<span class="pill ${['send_external', 'financial', 'delete', 'publish', 'prod_change', 'speak_for_company', 'run_code'].includes(k) ? 'warn' : ''}"><span>${esc(ACTION_LABEL[k] || k)}</span> · ${n}</span>`).join(' ');
const PLUGIN_EXAMPLE = `name: meu-erp
title: Meu ERP
version: 1.0.0
description: Consulta faturas e clientes do ERP.
publisher: TI
category: finance
tags: [erp, faturas]
readme: |
  # Meu ERP
  Consulta faturas e clientes. Peça ao TI a chave de API de teste para o stage.
base_url: https://erp.exemplo.com/api
auth: {type: api_key, in: header, name: X-API-Key, label: Chave da API}
settings:
  - {key: company_id, title: Código da empresa, required: true}
tools:
  - name: get_invoice
    description: Uma fatura pelo número.
    method: GET
    path: /companies/{{settings.company_id}}/invoices/{number}
    parameters: {type: object, properties: {number: {type: string}}, required: [number]}
  - name: send_invoice
    description: Envia a fatura por e-mail ao cliente.
    method: POST
    path: /invoices/{number}/send
    action: send_external
    parameters: {type: object, properties: {number: {type: string}}, required: [number]}
skills:
  - {name: glossario, content: "Fatura vencida: mais de 30 dias sem pagamento."}
tests:
  - {name: fatura existe, tool: get_invoice, args: {number: "1"}, expect_contains: "number"}
`;

/* ---------- vitrine ---------- */
function plCard(p) {
  return `<div class="card agent-card pl-card" data-cat="${esc(p.category)}" data-q="${esc(`${p.title} ${p.name} ${p.description} ${(p.tags || []).join(' ')}`.toLowerCase())}">
    <div class="row between"><a href="#/plugins/${esc(p.name)}"><b data-noi18n>${esc(p.title)}</b></a>
      <span>${p.featured ? '<span class="pill info">destaque</span> ' : ''}${p.status === 'approved' ? '' : plStatus(p.status)}</span></div>
    <div class="mute small"><span>${esc(PLUGIN_CATEGORY[p.category] || p.category)}</span> · v${esc(p.approved_version || p.version)}${p.publisher ? ` · <span data-noi18n>${esc(p.publisher)}</span>` : ''}</div>
    <div class="small one-line mt" data-noi18n title="${esc(p.description)}">${esc(p.description)}</div>
    <div class="row small mt" style="gap:4px;flex-wrap:wrap">${plActions(p.permissions.actions)}${p.permissions.runtime === 'server' ? ' <span class="pill info">código</span>' : ''}</div>
    ${(p.tags || []).length ? `<div class="small mt" data-noi18n>${p.tags.map(t => `<span class="chip">#${esc(t)}</span>`).join(' ')}</div>` : ''}
    <div class="mute small mt">${p.installs_count ? `<span>${p.installs_count}</span> <span>${window.t(p.installs_count === 1 ? 'time usa' : 'times usam')}</span> · <span>${p.calls}</span> <span>${window.t('chamadas')}</span>` : '<span>Ninguém usa ainda</span>'}
      ${(p.requests || []).length ? ` · <span class="pill warn">${p.requests.length} <span>pedido(s)</span></span>` : ''}</div></div>`;
}

async function pluginsPage() {
  const list = await api('/plugins');
  const live = list.filter(p => p.live), mine = list.filter(p => !p.live);
  const featured = live.length > live.filter(p => p.featured).length ? live.filter(p => p.featured) : [];  // só destaca se houver outros
  const cats = [...new Set(live.map(p => p.category))];
  main.innerHTML = `<div class="row between"><div><h1>Plugins (${live.length})</h1>
    <div class="sub">A vitrine da empresa: sistemas internos (ERP, CRM, faturamento…) prontos para os agentes. Cada plugin passou pela revisão de outra pessoa; o
      mantenedor do time instala com a credencial do time, e o segredo nunca chega ao agente.</div></div>
    ${ME.can_create_agents ? '<a href="#/plugins/new"><button>+ Novo plugin</button></a>' : ''}</div>
  ${live.length ? `<div class="row mt" style="gap:8px;flex-wrap:wrap"><input id="pl-q" placeholder="Buscar plugins…" style="max-width:280px">
      <span class="row" style="gap:4px;flex-wrap:wrap"><button class="ghost small pl-cat on" data-c="">Todos</button>${cats.map(c => `<button class="ghost small pl-cat" data-c="${esc(c)}">${esc(PLUGIN_CATEGORY[c] || c)}</button>`).join('')}</span></div>` : ''}
  ${featured.length ? `<h2 class="mt">Destaques</h2><div class="grid g3">${featured.map(plCard).join('')}</div>` : ''}
  ${live.length ? `<h2 class="mt">${featured.length ? 'Todos' : 'Catálogo'}</h2><div class="grid g3" id="pl-all">${[...live].sort((a, b) => (b.installs_count - a.installs_count) || a.title.localeCompare(b.title)).map(plCard).join('')}</div>` : ''}
  ${mine.length ? `<h2 class="mt">Em construção e revisão (${mine.length})</h2><div class="grid g3">${mine.map(plCard).join('')}</div>` : ''}
  ${!list.length ? `<div class="card empty"><h2>Nenhum plugin ainda</h2><div class="mute">Escreva o manifesto, importe o OpenAPI do sistema ou peça à sua ferramenta de IA (Creator mode pelo MCP). Depois dos testes em stage e da aprovação de um admin, o mantenedor do time instala com a credencial do time e os agentes usam com <code class="inline">plugins: [nome]</code> na spec.</div></div>` : ''}`;
  let cat = '';
  const filter = () => {
    const q = ($('#pl-q') || {}).value ? $('#pl-q').value.trim().toLowerCase() : '';
    main.querySelectorAll('#pl-all .pl-card').forEach(c => { c.hidden = (cat && c.dataset.cat !== cat) || (q && !c.dataset.q.includes(q)); });
  };
  const q = $('#pl-q');
  if (q) q.oninput = filter;
  main.querySelectorAll('.pl-cat').forEach(b => b.onclick = () => {
    cat = b.dataset.c; main.querySelectorAll('.pl-cat').forEach(x => x.classList.toggle('on', x === b)); filter();
  });
}

async function pluginNewPage() {
  const teams = (ME.teams || []).filter(t => ['developer', 'maintainer'].includes(t.role));
  main.innerHTML = `<a href="#/plugins" class="mute small">← Plugins</a><h1>Novo plugin</h1>
  <div class="card hero"><b>Prefere pelo chat?</b> Peça à sua ferramenta de IA conectada ao MCP do hangar <i>“conecte o nosso ERP, a documentação está em …”</i>:
    o Creator mode monta o manifesto, roda os testes em stage e envia para a revisão. A credencial de teste você cadastra aqui, nunca no chat.</div>
  <div class="grid g2 mt"><div class="card"><h2>Importar de um OpenAPI</h2>
      <div class="mute small">Cole o OpenAPI 3 (JSON ou YAML) do sistema: cada operação vira uma ferramenta, com um palpite do tipo de ação. Revise o resultado ao lado antes de salvar.</div>
      <label class="mt">OpenAPI<textarea id="pn-oa" rows="10" class="code" data-noi18n placeholder='{"openapi": "3.0.0", …}'></textarea></label>
      <div class="grid g2"><label>Nome (opcional)<input id="pn-oa-name" data-noi18n placeholder="meu-erp"></label>
        <label>base_url (se o OpenAPI não tiver)<input id="pn-oa-base" data-noi18n placeholder="https://…"></label></div>
      <div class="row mt"><button class="ghost" id="pn-import">Gerar o manifesto</button></div></div>
    <div class="card"><h2>Manifesto</h2>
      <div class="mute small">YAML ou JSON. <code class="inline">base_url</code> é o único host com que o plugin fala; cada ferramenta declara o <code class="inline">action</code> (o tipo que decide a alçada).</div>
      <textarea id="pn-manifest" rows="22" class="code mt" data-noi18n>${esc(PLUGIN_EXAMPLE)}</textarea>
      <label class="mt">Time dono<select id="pn-team"><option value="">— só eu —</option>${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name || t.slug)}</option>`).join('')}</select></label>
      <div class="row mt"><button id="pn-save">Salvar rascunho</button></div></div></div>`;
  let source = 'manual';
  $('#pn-import').onclick = ev => act(ev.target, () => api('/plugins/import-openapi', { method: 'POST', body: {
    document: $('#pn-oa').value, name: $('#pn-oa-name').value.trim(), base_url: $('#pn-oa-base').value.trim() } }).then(r => {
    $('#pn-manifest').value = JSON.stringify(r.manifest, null, 2); source = 'openapi';
    toast(`${r.manifest.tools.length} ${window.t('ferramenta(s) geradas: revise as ações')}`);
  }));
  $('#pn-save').onclick = ev => act(ev.target, () => api('/plugins', { method: 'POST', body: {
    manifest: $('#pn-manifest').value, team: $('#pn-team').value || null, source } }).then(p => { location.hash = `#/plugins/${p.name}`; }), 'Rascunho salvo');
}

/* ---------- instalação (produção ou stage) ---------- */
function plOauth(m, inst) {
  if (m.auth.type !== 'oauth2') return '';
  const o = (inst && inst.oauth) || {};
  return `<div class="mt"><b>Conta OAuth2</b> ${o.connected ? `<span class="pill ok">conectada</span> <span class="mute small">${esc(o.connected_by)}</span>` : '<span class="pill">não conectada</span>'}
    ${o.error ? `<div class="small bad-ic" data-noi18n>${esc(o.error)}</div>` : ''}
    <div class="mute small">Registre um app OAuth no sistema com esta URL de retorno: ${copyable((inst && inst.redirect_uri) || `${location.origin}/api/plugins/oauth/callback`)}
      <span>Escopos:</span> ${(m.auth.scopes || []).map(s => `<code class="inline" data-noi18n>${esc(s)}</code>`).join(' ') || '—'}</div>
    <div class="grid g2 mt"><label>Client ID<input class="pl-oid" data-noi18n value="${esc(o.client_id || '')}"></label>
      <label>Client secret<input class="pl-osec" type="password" autocomplete="off" placeholder="${o.client_secret ? window.t('cadastrado — digite outro para trocar') : ''}"></label></div>
    ${inst ? `<div class="row mt"><button class="ghost pl-connect">${o.connected ? 'Reconectar conta' : 'Conectar conta'}</button></div>` : ''}</div>`;
}

function plTriggers(m, inst, emps) {
  if (!(m.triggers || []).length) return '';
  const cfg = Object.fromEntries(((inst && inst.triggers) || []).map(t => [t.name, t]));
  return `<div class="mt"><b>Gatilhos</b><div class="mute small">Eventos do sistema que viram tarefas de um Digital employee do time. Configure a URL no sistema;
      os assinados (HMAC) usam a configuração secreta do manifesto, os outros usam o token da instalação.</div>
    ${m.triggers.map(t => { const c = cfg[t.name] || {}; return `<div class="li pl-trig" data-t="${esc(t.name)}"><div class="row between"><span><b data-noi18n>${esc(t.title || t.name)}</b>
        ${t.signature ? '<span class="pill info">assinado</span>' : '<span class="pill">token</span>'} ${c.received ? `<span class="mute small"><span>recebidos:</span> ${c.received}</span>` : ''}</span>
        <span class="row small" style="gap:6px"><select class="pl-temp"><option value="">— ninguém —</option>${emps.map(e => `<option value="${esc(e.slug)}" ${e.slug === c.employee ? 'selected' : ''}>${esc(e.name)}</option>`).join('')}</select>
        <label class="row" style="gap:4px"><input type="checkbox" class="pl-ton" ${c.enabled ? 'checked' : ''}> <span>ligado</span></label></span></div>
      ${t.description ? `<div class="mute small" data-noi18n>${esc(t.description)}</div>` : ''}
      ${c.url ? `<div class="small mt">${copyable(c.url)}</div>` : ''}</div>`; }).join('')}
    ${inst && m.triggers.some(t => !t.signature) ? `<div class="row small mt" style="gap:8px"><button class="ghost pl-htok">${inst.hook_token ? 'Trocar o token' : 'Gerar o token'}</button>
      ${inst.hook_token ? `<span class="mute"><span>token atual termina em</span> <code class="inline" data-noi18n>…${esc(inst.hook_token)}</code></span>` : ''}</div><div class="pl-htok-out"></div>` : ''}</div>`;
}

function plServer(inst) {
  const s = inst && inst.server;
  if (!s) return '';
  const pill = s.state === 'running' ? `<span class="pill ok" data-noi18n>${window.t('no ar', 'servidor')}</span>` : s.state === 'missing' ? '<span class="pill">parado</span>' : `<span class="pill bad">${esc(s.state)}</span>`;
  return `<div class="mt"><b>Servidor</b> ${pill} <code class="inline" data-noi18n>${esc(s.name)}</code>
    ${s.started ? `<span class="mute small"><span>desde</span> <span>${ago(s.started)}</span></span>` : ''}
    ${s.error ? `<div class="small bad-ic" data-noi18n>${esc(s.error)}</div>` : ''}
    ${plEgress(inst.egress)}
    <div class="row mt"><button class="ghost pl-logs">Ver logs</button></div><pre class="code small pl-logs-out" data-noi18n hidden style="max-height:320px;overflow:auto"></pre></div>`;
}

function plEgress(e) {
  if (!e) return '';
  if (e.mode !== 'proxy') return '<div class="small mt"><span class="pill warn">saída livre</span> <span class="mute">O servidor sai direto para a internet (PLUGIN_EGRESS=open).</span></div>';
  const ld = e.last_denied;
  return `<div class="small mt"><b>Saída</b> <span class="mute">só pelo proxy da central, para:</span> ${e.hosts.length ? e.hosts.map(h => `<code class="inline" data-noi18n>${esc(h)}</code>`).join(' ') : '<span class="pill">nenhum host</span>'}
    ${e.denied ? `<div class="bad-ic"><span>Recusadas:</span> ${e.denied}${ld ? ` · <span>última:</span> <code class="inline" data-noi18n>${esc(ld.host)}</code> <span>${ago(ld.at)}</span>` : ''}
      <span class="mute">— se o plugin precisa desse host, declare em <code class="inline">server.egress</code> e mande uma versão nova.</span></div>` : ''}</div>`;
}

function plSignature(p) {
  const s = p.signature, sg = p.signing || {};
  if (!s && !sg.configured) return '<span class="mute">não verificada (cosign não configurado)</span>';
  if (!s) return '<span class="mute">verificada na aprovação</span>';
  const pill = s.ok === true ? '<span class="pill ok">assinada</span>' : s.ok === false ? '<span class="pill bad">sem assinatura válida</span>' : '<span class="pill">não verificada</span>';
  return `${pill} <span class="mute small" data-noi18n>${esc(s.detail || '')}</span>${sg.required ? ' <span class="pill info">obrigatória</span>' : ''}`;
}

const CALL_STATUS = { ok: ['ok', 'ok'], error: ['erro', 'bad'], limited: ['limite', 'warn'] };

const POLICY_LABEL = { allow: 'a alçada decide', ask: 'pede aprovação a cada uso', off: 'desligada' };
const IDENTITY_LABEL = { team: 'a conta do time', requester: 'a conta de quem pediu (obrigatória)', prefer_requester: 'a de quem pediu, se conectada; senão a do time' };

function plPolicy(m, inst) {
  const pol = inst.tool_policy || {};
  return `<details class="mt"><summary><b>Ações e identidade</b> <span class="mute small">${Object.keys(pol).length ? `${Object.keys(pol).length} ${window.t('com política própria')}` : ''}</span></summary>
    <div class="mute small">Por ação: desligada some para os agentes; “pede aprovação” passa pela decisão de uma pessoa a cada uso (só Digital employees usam).</div>
    <table class="small mt">${m.tools.map(t => `<tr><td><code class="inline" data-noi18n>${esc(t.name)}</code> <span class="mute">${esc(ACTION_LABEL[t.action] || t.action)}</span></td>
      <td><select class="pl-pol" data-t="${esc(t.name)}" style="width:auto">${Object.entries(POLICY_LABEL).map(([k, v]) => `<option value="${k}" ${(pol[t.name] || 'allow') === k ? 'selected' : ''}>${v}</option>`).join('')}</select></td></tr>`).join('')}</table>
    ${m.auth.type === 'oauth2' ? `<label class="mt">Conta usada<select class="pl-ident" style="width:auto">${Object.entries(IDENTITY_LABEL).map(([k, v]) => `<option value="${k}" ${(inst.identity_mode || 'team') === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <div class="mute small"><span>Contas pessoais conectadas:</span> ${inst.personal_accounts || 0}</div>` : ''}
    <div class="row mt"><button class="ghost pl-pol-save">Salvar ações</button></div>
    <h4 class="mt">Testar uma ação</h4><div class="mute small">Executa de verdade, com a credencial do time, e fica na auditoria.</div>
    <div class="row mt" style="gap:6px"><select class="pl-try-tool" style="width:auto">${m.tools.map(t => `<option>${esc(t.name)}</option>`).join('')}</select>
      <input class="pl-try-args code" value="{}" style="flex:1" data-noi18n><button class="ghost pl-try">Executar</button></div><pre class="code small pl-try-out" data-noi18n hidden></pre></details>`;
}

function plCalls(r) {
  if (!r.items.length) return '<div class="mute small mt">Nenhuma chamada registrada.</div>';
  return `<table class="small mt"><tr><th>Quando</th><th>Quem</th><th>Ferramenta</th><th>Resultado</th><th>Tempo</th></tr>
    ${r.items.map(c => { const st = CALL_STATUS[c.status] || [c.status, '']; return `<tr><td>${ago(c.at)}</td>
      <td><code class="inline" data-noi18n>${esc(c.caller)}</code>${c.task_id ? ` <a href="#/tasks/${c.task_id}">#${c.task_id}</a>` : ''}</td>
      <td><code class="inline" data-noi18n title="${esc(c.args)}">${esc(c.tool)}</code></td>
      <td><span class="pill ${st[1]}">${esc(window.t(st[0]))}</span>${c.http_status ? ` <span class="mute">HTTP ${c.http_status}</span>` : ''}
        ${c.status !== 'ok' ? `<div class="mute one-line" data-noi18n title="${esc(c.result)}">${esc(c.result.slice(0, 120))}</div>` : ''}</td>
      <td class="mute">${c.duration_ms} ms</td></tr>`; }).join('')}</table>
    <div class="mute small mt"><span>Guardadas por</span> ${r.retention_days} <span>dias. Argumentos ao passar o mouse na ferramenta, com os segredos mascarados.</span></div>`;
}

function plStageTriggers(m, inst) {
  if (!(m.triggers || []).length) return '';
  return `<div class="mt"><b>Gatilhos (teste)</b><div class="mute small">Veja a tarefa que um evento geraria, sem criar nada. Os webhooks de teste do sistema também podem ir para a URL de stage: a autenticação é conferida e a resposta traz só a prévia.</div>
    ${m.triggers.map(t => { const c = ((inst && inst.triggers) || []).find(x => x.name === t.name) || {}; return `<div class="li pl-strig" data-t="${esc(t.name)}">
      <div class="row between"><b data-noi18n>${esc(t.title || t.name)}</b>${c.received ? `<span class="mute small"><span>recebidos:</span> ${c.received}</span>` : ''}</div>
      ${c.url ? `<div class="small">${copyable(c.url)}</div>` : ''}
      <textarea class="code small pl-ev" rows="4" data-noi18n placeholder='{"id": "…"}'>${esc(JSON.stringify(t.example || {}, null, 1))}</textarea>
      <div class="row mt"><button class="ghost pl-ev-go">Testar o gatilho</button></div><div class="pl-ev-out"></div></div>`; }).join('')}
    ${inst && m.triggers.some(t => !t.signature) ? `<div class="row small mt" style="gap:8px"><button class="ghost pl-htok">${inst.hook_token ? 'Trocar o token de stage' : 'Gerar o token de stage'}</button></div><div class="pl-htok-out"></div>` : ''}</div>`;
}

function plInstallForm(p, m, inst, team, emps, stage = false) {
  const id = team || 'org';
  const settings = m.settings || [];
  return `<div class="card mt pl-inst" data-team="${esc(team || '')}" data-stage="${stage ? 1 : 0}"><div class="row between">
      <h3>${stage ? '<span>Instalação de stage</span>' : team ? `<span>Time</span> <span data-noi18n>${esc(team)}</span>` : 'Empresa toda'}</h3>
      ${inst ? (inst.enabled ? `<span class="pill ok">${stage ? 'stage' : 'instalado'}</span>` : '<span class="pill">pausado</span>') : '<span class="pill">não instalado</span>'}</div>
    ${stage ? '<div class="mute small">Credencial de TESTE (sandbox do sistema), usada só nos testes do rascunho. Nunca serve os agentes.</div>' : ''}
    <div class="grid g2">${settings.map(s => `<label>${esc(s.title || s.key)}${s.required ? ' *' : ''}${s.description ? `<div class="mute small" data-noi18n>${esc(s.description)}</div>` : ''}
        <input class="pl-set" data-k="${esc(s.key)}" data-secret="${s.secret ? 1 : 0}" ${s.secret ? 'type="password" autocomplete="off"' : ''} data-noi18n
          value="${s.secret ? '' : esc((inst && inst.settings[s.key]) ?? s.default ?? '')}" placeholder="${s.secret && inst && inst.secrets[s.key] ? window.t('cadastrado — digite outro para trocar') : ''}"></label>`).join('')}
      ${!['none', 'oauth2'].includes(m.auth.type) ? `<label>${esc(m.auth.label || AUTH_LABEL[m.auth.type])}${m.auth.type === 'basic' ? ' <span class="mute small">(usuário:senha)</span>' : ''}
        <input class="pl-cred" type="password" autocomplete="off" placeholder="${inst && inst.credential ? window.t('cadastrada — digite outra para trocar') : ''}"></label>` : ''}</div>
    ${!stage ? `<div class="grid g2 mt"><label>Limite (chamadas por minuto)<input class="pl-rate" type="number" min="0" max="6000"
        value="${inst && inst.rate_limit ? inst.rate_limit : ''}" placeholder="${inst ? inst.rate_limit_effective || window.t('sem limite') : ''}"></label>
        <div class="mute small" style="align-self:end">${window.t('Vazio ou 0: o padrão do manifesto ou da empresa. Acima disso, a chamada volta como erro para o agente.')}</div></div>` : ''}
    ${!stage && inst ? plPolicy(m, inst) : ''}
    ${plOauth(m, inst)}${stage ? plStageTriggers(m, inst) : plTriggers(m, inst, emps)}${plServer(inst)}
    ${inst && !stage ? `<div class="mute small mt"><span>Chamadas:</span> ${inst.stats.calls || 0} · <span>erros:</span> ${inst.stats.errors || 0}${inst.stats.last_call ? ` · <span>última</span> <span>${ago(inst.stats.last_call)}</span>` : ''}
      ${inst.last_test ? ` · <span>teste:</span> ${inst.last_test.ok ? '<span class="pill ok">ok</span>' : `<span class="pill bad">falhou</span> <span data-noi18n>${esc(inst.last_test.sample || '')}</span>`}` : ''}</div>` : ''}
    <div class="row mt"><button class="pl-save" data-id="${esc(id)}">${inst ? 'Salvar' : (stage ? 'Salvar a credencial de teste' : 'Instalar')}</button>
      ${inst ? `${stage ? '' : '<button class="ghost pl-test">Testar conexão</button>'}<button class="ghost pl-toggle">${inst.enabled ? 'Pausar' : 'Retomar'}</button>
        ${p.can_audit ? '<button class="ghost pl-calls">Chamadas</button>' : ''}
        <button class="ghost danger pl-del">${stage ? 'Remover' : 'Desinstalar'}</button>` : ''}</div><div class="pl-calls-out"></div></div>`;
}

function plReport(p) {
  const r = p.test_report;
  if (!r) return `<div class="mute small mt">${p.tests_declared ? 'Os testes ainda não rodaram nesta versão.' : 'Sem testes declarados (`tests` no manifesto).'}</div>`;
  const current = p.tests_ok || (r.passed === false && r.version === p.version);
  return `<div class="mt"><div class="row between"><b>${r.passed ? '<span class="pill ok">testes passaram</span>' : '<span class="pill bad">testes falharam</span>'}
      <span class="mute small"><span>v</span>${esc(r.version)} · ${esc(r.by)} · <span>${ago(r.at)}</span></span></b>
      ${!current || (r.passed && !p.tests_ok) ? '<span class="pill warn">desatualizado: o rascunho mudou</span>' : ''}</div>
    <table class="small mt">${r.results.map(x => `<tr><td>${x.ok ? icon('check', 'ok-ic') : icon('x', 'bad-ic')}</td><td data-noi18n>${esc(x.name)}</td>
      <td><code class="inline" data-noi18n>${esc(x.tool || x.trigger)}</code></td><td class="mute" data-noi18n>${esc(x.why || (x.status ? `HTTP ${x.status}` : 'ok'))}</td></tr>`).join('')}</table></div>`;
}

/* ---------- detalhe ---------- */
async function pluginDetail(name) {
  const p = await api('/plugins/' + name);
  const qs = new URLSearchParams(location.hash.split('?')[1] || '');
  if (qs.get('oauth_ok')) toast(window.t('Conta conectada'));
  if (qs.get('oauth_error')) toast(qs.get('oauth_error'), true);
  const allEmps = (p.approved_manifest || {}).triggers ? await api('/employees').catch(() => []) : [];
  const teamId = slug => ((ME.teams || []).find(t => t.slug === slug) || {}).id;
  const empsFor = team => allEmps.filter(e => e.status !== 'offboarded' && (!team || e.team === teamId(team)));
  const m = p.approved_manifest || p.manifest, perm = p.permissions;
  const draftDiffers = p.approved_manifest && JSON.stringify(p.approved_manifest) !== JSON.stringify(p.manifest);
  const prodInstalls = (p.installs || []).filter(i => !i.stage), stageInst = (p.installs || []).find(i => i.stage);
  const installs = Object.fromEntries(prodInstalls.map(i => [i.team || '', i]));
  const targets = [...(p.install_teams || []).map(t => t.slug), ...(ME.is_admin ? [''] : [])];
  const askable = (p.my_teams || []).filter(t => !p.installed_for.includes(t.slug));
  const submitBlocked = p.tests_declared && !p.tests_ok;
  main.innerHTML = `<a href="#/plugins" class="mute small">← Plugins</a>
  <div class="row between"><div><h1 data-noi18n>${esc(p.title)}</h1><div class="sub"><code class="inline" data-noi18n>${esc(p.name)}</code> ${plStatus(p.status)}
    ${p.featured ? '<span class="pill info">destaque</span>' : ''} <span>${esc(PLUGIN_CATEGORY[p.category] || p.category)}</span>
    ${p.approved_version ? ` · <span>em uso:</span> v${esc(p.approved_version)}` : ''}${draftDiffers ? ` · <span>rascunho</span> v${esc(p.version)}` : ''}
    ${p.team ? ` · <span>time</span> <span data-noi18n>${esc(p.team)}</span>` : ''}</div></div>
    <div class="row" style="gap:6px">${p.live ? `<a class="ghost-link" href="/api/plugins/${esc(p.name)}/export" download><button class="ghost">Exportar</button></a>` : ''}
      ${ME.is_admin && p.live ? `<button class="ghost" id="pl-feat">${p.featured ? 'Tirar do destaque' : 'Destacar'}</button>` : ''}
      ${ME.is_admin && p.approved_version ? `<button class="ghost ${p.status === 'disabled' ? '' : 'danger'}" id="pl-off">${p.status === 'disabled' ? 'Religar' : 'Desligar'}</button>` : ''}</div></div>
  <div class="mute" data-noi18n>${esc(p.description)}</div>
  ${(p.tags || []).length ? `<div class="small mt" data-noi18n>${p.tags.map(t => `<span class="chip">#${esc(t)}</span>`).join(' ')}</div>` : ''}
  ${p.status === 'rejected' && p.review_note ? `<div class="card bad-card mt"><b>Recusado:</b> <span data-noi18n>${esc(p.review_note)}</span></div>` : ''}
  ${p.can_review ? `<div class="card warn-card mt"><h2>Revisão</h2><div class="small">Enviado por ${esc(p.submitted_by)}. Confira o host, a autenticação e o tipo de cada ação: é o tipo que decide se o agente faz sozinho ou pede aprovação.</div>
    ${plReport(p)}
    <input id="pl-note" class="mt" placeholder="Comentário (obrigatório para recusar)"><div class="row mt"><button id="pl-approve" ${submitBlocked ? 'disabled' : ''}>Aprovar v${esc(p.version)}</button>
    <button class="ghost danger" id="pl-reject">Recusar</button></div></div>` : ''}
  ${(p.requests || []).length ? `<div class="card warn-card mt"><h2>Pedidos de instalação</h2>${p.requests.map(r => `<div class="row between li"><span><b data-noi18n>${esc(r.team)}</b>
      <span class="mute small">${esc(r.by)} · <span>${ago(r.at)}</span></span>${r.note ? `<div class="small" data-noi18n>${esc(r.note)}</div>` : ''}</span>
      ${targets.includes(r.team) ? `<button class="ghost pl-req-x" data-t="${esc(r.team)}">Dispensar</button>` : '<span class="mute small">aguardando o mantenedor</span>'}</div>`).join('')}</div>` : ''}
  ${p.readme ? `<div class="card mt md" data-noi18n>${mdLite(p.readme)}</div>` : ''}
  ${p.live && m.auth.type === 'oauth2' && prodInstalls.some(i => i.identity_mode && i.identity_mode !== 'team') ? `<div class="card mt"><h2>Minha conta</h2>
    <div class="mute small">Este plugin usa a conta de quem pede a tarefa: conecte a sua para os Digital employees agirem em seu nome quando você pedir.</div>
    <div class="row mt" style="gap:6px">${p.my_account ? '<span class="pill ok">conectada</span><button class="ghost danger" id="pl-my-off">Desconectar</button>'
      : `<button id="pl-my-on">Conectar a minha conta</button>`}</div></div>` : ''}
  <div class="grid g2 mt"><div class="card"><h2>Permissões</h2><dl class="kv">
      ${perm.runtime === 'server' ? `<dt>Código</dt><dd><span class="pill info">servidor em container</span>
        <div class="small mt"><code class="inline" data-noi18n>${esc(perm.image)}</code> ${perm.pinned ? '<span class="pill ok">fixada por digest</span>' : '<span class="pill warn">sem digest (desenvolvimento)</span>'}</div></dd>
      <dt>Assinatura</dt><dd>${plSignature(p)}</dd>
      <dt>Saída declarada</dt><dd>${perm.egress.length ? perm.egress.map(h => `<code class="inline" data-noi18n>${esc(h)}</code>`).join(' ') : '—'}</dd>`
      : `<dt>Fala com</dt><dd><code class="inline" data-noi18n>${esc(perm.host)}</code>${perm.templated_host ? ' <span class="mute small">(definido na instalação)</span>' : ''}</dd>`}
      <dt>Autenticação</dt><dd>${esc(AUTH_LABEL[perm.auth] || perm.auth)}</dd>
      <dt>Ações</dt><dd>${plActions(perm.actions)}</dd>
      <dt>Segredos</dt><dd>${perm.secrets.length ? perm.secrets.map(s => `<code class="inline" data-noi18n>${esc(s)}</code>`).join(' ') : '—'}${perm.auth !== 'none' ? ` <span class="mute small">+ ${esc(window.t('credencial'))}</span>` : ''}</dd>
      <dt>Uso</dt><dd>${p.installs_count ? `${p.installed_for.map(t => t === 'org' ? window.t('empresa') : esc(t)).join(', ')} · <span>${p.calls}</span> <span>${window.t('chamadas')}</span>` : '<span>Ninguém usa ainda</span>'}</dd></dl>
      ${perm.risky.length ? `<div class="small mt warn-card card"><span>Ações que mudam algo fora da empresa ou têm risco alto:</span> ${perm.risky.map(a => `<b>${esc(ACTION_LABEL[a] || a)}</b>`).join(', ')}. <span>A alçada de cada agente decide (por padrão, pedem aprovação).</span></div>` : ''}</div>
    <div class="card"><h2>Ferramentas (${m.tools.length})</h2><table class="small">${m.tools.map(t => `<tr><td><code class="inline" data-noi18n>${esc(t.name)}</code>
      <div class="mute" data-noi18n>${esc(t.description || '')}</div></td><td>${t.path ? `<code class="inline" data-noi18n>${esc(t.method)} ${esc(t.path)}</code>` : '<span class="mute">MCP</span>'}</td>
      <td>${esc(ACTION_LABEL[t.action] || t.action)}</td></tr>`).join('')}</table>
      ${(m.skills || []).length ? `<div class="mute small mt"><span>Skills:</span> ${m.skills.map(s => `<code class="inline" data-noi18n>${esc(s.name)}</code>`).join(' ')}</div>` : ''}</div></div>
  ${p.live ? `<h2 class="mt">Instalação</h2>
    <div class="mute small">Cada time instala com a própria credencial (o mantenedor do time; um admin também instala para a empresa toda). Os agentes do time usam com
      <code class="inline">plugins: [${esc(p.name)}]</code> na spec, e as ferramentas chegam como <code class="inline">plugin-${esc(p.name)}__…</code>.</div>
    ${targets.length ? targets.map(t => plInstallForm(p, p.approved_manifest, installs[t], t, empsFor(t))).join('') : '<div class="card mute mt">Só o mantenedor de um time (ou um admin) instala.</div>'}
    ${askable.length && !targets.length ? `<div class="card mt"><h3>Pedir para o meu time</h3><div class="mute small">O mantenedor do time recebe o pedido (no canal de decisões dele) e instala com a credencial do time.</div>
      <div class="row mt" style="gap:8px"><select id="pl-req-team">${askable.map(t => `<option value="${esc(t.slug)}">${esc(t.slug)}</option>`).join('')}</select>
      <input id="pl-req-note" placeholder="Para quê? (opcional)" style="flex:1"><button id="pl-req">Pedir</button></div></div>` : ''}` : ''}
  ${(p.history || []).length ? `<div class="card mt"><h2>Versões</h2>${p.history.map(h => `<div class="li small"><b>v${esc(h.version)}</b> <span class="mute">${esc(h.by)} · <span>${ago(h.at)}</span></span>
      ${h.tests ? ' <span class="pill ok">testada em stage</span>' : ''}${h.note ? ` <span data-noi18n>— ${esc(h.note)}</span>` : ''}</div>`).join('')}</div>` : ''}
  ${p.can_edit ? `<h2 class="mt">Construção</h2>
    <div class="card mt"><div class="row between"><h2>Testes em stage</h2><button class="ghost" id="pl-run-tests" ${stageInst ? '' : 'disabled'}>Rodar os testes</button></div>
      <div class="mute small">Os casos de <code class="inline">tests</code> rodam contra o rascunho, com a credencial de teste da instalação de stage. O resultado vale só para esta versão do manifesto;
        enviar e aprovar exigem os testes passando.</div>${plReport(p)}</div>
    ${plInstallForm(p, p.manifest, stageInst, stageInst ? stageInst.team : p.team, [], true)}
    <div class="card mt"><div class="row between"><h2>Manifesto ${p.status === 'approved' ? '' : `<span class="mute small">(${esc(PLUGIN_STATUS[p.status] ? window.t(PLUGIN_STATUS[p.status][0]) : p.status)})</span>`}</h2>
      ${['draft', 'rejected'].includes(p.status) ? `<button id="pl-submit" ${submitBlocked ? 'disabled title="rode os testes em stage desta versão"' : ''}>Enviar para revisão</button>` : ''}</div>
    <div class="mute small">Para mudar um plugin aprovado, suba a <code class="inline">version</code>: a versão nova passa pelos testes e pela revisão, e as instalações seguem na aprovada até lá.</div>
    <textarea id="pl-yaml" rows="18" class="code mt" data-noi18n>${esc(p.yaml)}</textarea>
    <div class="row mt"><button class="ghost" id="pl-update">Salvar rascunho</button>${!p.approved_version || ME.is_admin ? '<button class="ghost danger" id="pl-delete">Apagar</button>' : ''}</div></div>` : ''}`;
  const on = (sel, fn) => { const b = $(sel); if (b) b.onclick = fn; };
  on('#pl-my-on', ev => act(ev.target, () => api(`/plugins/${name}/my-account/connect`, { method: 'POST', body: { team: (prodInstalls.find(i => i.identity_mode !== 'team') || {}).team || null } })
    .then(r => { location.href = r.authorize_url; })));
  on('#pl-my-off', ev => act(ev.target, () => api(`/plugins/${name}/my-account`, { method: 'DELETE' }).then(route), 'Conta desconectada'));
  on('#pl-submit', ev => act(ev.target, () => api(`/plugins/${name}/submit`, { method: 'POST' }).then(route), 'Enviado para revisão'));
  on('#pl-update', ev => act(ev.target, () => api(`/plugins/${name}`, { method: 'PUT', body: { manifest: $('#pl-yaml').value } }).then(route), 'Rascunho salvo'));
  on('#pl-delete', ev => confirm(`${window.t('Apagar o plugin')} ${name}?`) && act(ev.target, () => api(`/plugins/${name}`, { method: 'DELETE' }).then(() => { location.hash = '#/plugins'; })));
  on('#pl-approve', ev => act(ev.target, () => api(`/plugins/${name}/review`, { method: 'POST', body: { decision: 'approve', note: $('#pl-note').value } }).then(route), 'Aprovado'));
  on('#pl-reject', ev => act(ev.target, () => api(`/plugins/${name}/review`, { method: 'POST', body: { decision: 'reject', note: $('#pl-note').value } }).then(route), 'Recusado'));
  on('#pl-off', ev => act(ev.target, () => api(`/plugins/${name}/${p.status === 'disabled' ? 'enable' : 'disable'}`, { method: 'POST' }).then(route)));
  on('#pl-feat', ev => act(ev.target, () => api(`/plugins/${name}/feature`, { method: 'POST', body: { on: !p.featured } }).then(route)));
  on('#pl-run-tests', ev => act(ev.target, () => api(`/plugins/${name}/tests`, { method: 'POST', body: { team: stageInst ? stageInst.team : null } })
    .then(r => { toast(window.t(r.passed ? 'Testes passaram' : 'Testes falharam'), !r.passed); return route(); })));
  on('#pl-req', ev => act(ev.target, () => api(`/plugins/${name}/requests`, { method: 'POST', body: { team: $('#pl-req-team').value, note: $('#pl-req-note').value } })
    .then(route), 'Pedido enviado ao mantenedor'));
  main.querySelectorAll('.pl-req-x').forEach(b => b.onclick = ev => act(ev.target, () => api(`/plugins/${name}/requests?team=${encodeURIComponent(b.dataset.t)}`, { method: 'DELETE' }).then(route)));
  main.querySelectorAll('.pl-inst').forEach(card => {
    const team = card.dataset.team || null, stage = card.dataset.stage === '1';
    const cur = stage ? stageInst : installs[team || ''];
    const q = (team ? `?team=${encodeURIComponent(team)}` : '') + (stage ? `${team ? '&' : '?'}stage=true` : '');
    $('.pl-save', card).onclick = ev => {
      const body = { team, settings: {}, secrets: {}, stage };
      card.querySelectorAll('.pl-set').forEach(i => { if (i.dataset.secret === '1') { if (i.value) body.secrets[i.dataset.k] = i.value; } else body.settings[i.dataset.k] = i.value; });
      const cred = $('.pl-cred', card);
      if (cred && cred.value) body.credential = cred.value;
      const oid = $('.pl-oid', card), osec = $('.pl-osec', card);
      if (oid) body.oauth_client_id = oid.value.trim();
      if (osec && osec.value) body.oauth_client_secret = osec.value;
      const trig = [...card.querySelectorAll('.pl-trig')];
      if (trig.length) body.triggers = Object.fromEntries(trig.map(r => [r.dataset.t, { employee: $('.pl-temp', r).value, enabled: $('.pl-ton', r).checked }]));
      const rate = $('.pl-rate', card);
      if (rate && rate.value !== '') body.rate_limit = parseInt(rate.value, 10) || 0;
      else if (rate && cur && cur.rate_limit) body.rate_limit = 0;
      act(ev.target, () => api(`/plugins/${name}/install`, { method: 'PUT', body }).then(route), stage ? 'Credencial de teste salva' : 'Instalado');
    };
    const con = $('.pl-connect', card);
    if (con) con.onclick = ev => act(ev.target, () => api(`/plugins/${name}/install/connect`, { method: 'POST', body: { team, stage } })
      .then(r => { location.href = r.authorize_url; }));
    const htok = $('.pl-htok', card);
    if (htok) htok.onclick = ev => (!cur.hook_token || confirm(window.t('Trocar o token? O antigo para de valer na hora.'))) &&
      act(ev.target, () => api(`/plugins/${name}/install/hook-token`, { method: 'POST', body: { team, stage } }).then(r => {
        $('.pl-htok-out', card).innerHTML = `<div class="card warn-card mt"><b>${esc(window.t('Copie agora: o token não aparece de novo.'))}</b><div class="mt">${copyable(r.token)}</div>
          <div class="mute small mt">${esc(window.t('Mande no header Authorization: Bearer <token> (ou X-Hangar-Token, ou ?token= na URL).'))}</div></div>`;
      }));
    card.querySelectorAll('.pl-strig').forEach(row => {
      $('.pl-ev-go', row).onclick = ev => {
        let event;
        try { event = JSON.parse($('.pl-ev', row).value || '{}'); } catch { return toast(window.t('O evento precisa ser JSON'), true); }
        act(ev.target, () => api(`/plugins/${name}/triggers/${row.dataset.t}/test`, { method: 'POST', body: { event, team } }).then(r => {
          $('.pl-ev-out', row).innerHTML = `<div class="card mt small"><div><span class="mute">${esc(window.t('Título'))}:</span> <b data-noi18n>${esc(r.title)}</b></div>
            ${r.dedupe_key ? `<div class="mute"><span>${esc(window.t('Deduplicação'))}:</span> <code class="inline" data-noi18n>${esc(r.dedupe_key)}</code></div>` : ''}
            ${r.signature_note ? `<div class="mute">${esc(window.t(r.signature_note))}</div>` : ''}
            <pre class="code small mt" data-noi18n style="max-height:220px;overflow:auto">${esc(r.body)}</pre></div>`;
        }));
      };
    });
    const ps = $('.pl-pol-save', card);
    if (ps) ps.onclick = ev => {
      const body = { team, tool_policy: Object.fromEntries([...card.querySelectorAll('.pl-pol')].map(s => [s.dataset.t, s.value])) };
      const idm = $('.pl-ident', card);
      if (idm) body.identity_mode = idm.value;
      act(ev.target, () => api(`/plugins/${name}/install`, { method: 'PUT', body }).then(route), 'Ações salvas');
    };
    const tr = $('.pl-try', card);
    if (tr) tr.onclick = ev => {
      let args;
      try { args = JSON.parse($('.pl-try-args', card).value || '{}'); } catch { return toast(window.t('Os argumentos precisam ser JSON'), true); }
      const tool = $('.pl-try-tool', card).value;
      if (!confirm(`${window.t('Executar de verdade')} ${tool}?`)) return;
      act(ev.target, () => api(`/plugins/${name}/install/try`, { method: 'POST', body: { team, tool, args } }).then(r => {
        const out = $('.pl-try-out', card); out.hidden = false; out.textContent = `${r.ok ? 'ok' : 'erro'}${r.status ? ` · HTTP ${r.status}` : ''}\n${r.result}`;
      }));
    };
    const calls = $('.pl-calls', card);
    if (calls) calls.onclick = ev => act(ev.target, () => api(`/plugins/${name}/calls${q}`).then(r => { $('.pl-calls-out', card).innerHTML = plCalls(r); }));
    const lg = $('.pl-logs', card);
    if (lg) lg.onclick = ev => act(ev.target, () => api(`/plugins/${name}/install/logs${q}`)
      .then(r => { const out = $('.pl-logs-out', card); out.hidden = false; out.textContent = r.logs || '(vazio)'; }));
    const tst = $('.pl-test', card);
    if (tst) tst.onclick = ev => act(ev.target, () => api(`/plugins/${name}/install/test`, { method: 'POST', body: { team, stage } })
      .then(r => { toast(r.ok ? window.t('Conexão ok') : `${window.t('Falhou')}: ${r.sample}`, !r.ok); return route(); }));
    const tog = $('.pl-toggle', card);
    if (tog) tog.onclick = ev => act(ev.target, () => api(`/plugins/${name}/install`, { method: 'PUT', body: { team, stage, enabled: !cur.enabled } }).then(route));
    const del = $('.pl-del', card);
    if (del) del.onclick = ev => confirm(window.t(stage ? 'Remover a instalação de stage?' : 'Desinstalar? Os agentes do time deixam de usar este plugin.')) &&
      act(ev.target, () => api(`/plugins/${name}/install${q}`, { method: 'DELETE' }).then(route), stage ? 'Removido' : 'Desinstalado');
  });
}
