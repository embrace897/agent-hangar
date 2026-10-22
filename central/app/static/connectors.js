/* ---------- Conectores (MCP): catálogo no estilo da página de plugins do ChatGPT, detalhe e cadastro personalizado ---------- */
let CX = null;  // o catálogo carregado (para a busca e os detalhes sem ir de novo ao servidor)
const CX_STATUS = { ok: ['conectado', 'ok'], auth: ['precisa de autenticação', 'warn'], error: ['erro', 'bad'], '': ['não testado', ''] };
const CX_STATE = { approved: ['aprovada', 'ok'], pending: ['nova: esperando aprovação', 'warn'], changed: ['mudou: esperando aprovação', 'warn'], disabled: ['desligada', ''] };
const CX_ACTION = { read: 'leitura', write_internal: 'escrita interna', send_external: 'envia para fora', publish: 'publica',
  delete: 'exclui', financial: 'financeira' };
const CX_KIND = { curated: 'oficial do fornecedor', remote: 'remoto (catálogo Docker)', docker: 'container (catálogo Docker)' };
const cxIcon = (url, size = 32, name = '') => url
  ? `<img class="cx-ic" src="${esc(url)}" alt="" width="${size}" height="${size}" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'cx-ic cx-ini',textContent:${esc(JSON.stringify((name || '?').slice(0, 1).toUpperCase()))}}))">`
  : `<span class="cx-ic cx-ini" style="width:${size}px;height:${size}px">${esc((name || '?').slice(0, 1).toUpperCase())}</span>`;

async function connectorsPage() {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  const focus = params.get('cat') || '', view = params.get('view') || 'catalog';
  if (params.get('remote_error')) toast(params.get('remote_error'), true);
  CX = await api('/connectors');
  const installed = CX.installed;
  const row = x => `<a class="cx-row" href="#/connectors/${x.installed && installed.some(i => i.name === x.id) ? encodeURIComponent(x.id) : 'item/' + encodeURIComponent(x.key)}">
      ${cxIcon(x.icon, 32, x.name)}<span class="cx-txt"><b data-noi18n>${esc(x.name)}</b><span class="mute small" data-noi18n>${esc(x.description || '')}</span></span>
      <span class="cx-plus" title="${x.installed ? 'Instalado' : 'Conectar'}">${x.installed ? icon('check') : icon('plus')}</span></a>`;
  const section = ([id, title], list, all) => list.length ? `<section class="cx-sec"><h2><a href="#/connectors?cat=${id}">${esc(title)} <span class="mute">›</span></a></h2>
      <div class="cx-grid">${(all ? list : list.slice(0, 6)).map(row).join('')}</div></section>` : '';
  const items = CX.items;
  const bySec = id => id === 'popular' ? items.filter(x => x.popular).sort((a, b) => (a.kind === 'curated' ? 0 : 1) - (b.kind === 'curated' ? 0 : 1))
    : items.filter(x => x.section === id && !(x.popular && x.kind === 'curated'));
  const custom = installed.filter(m => ['custom', 'curated', 'remote'].includes(m.source));
  main.innerHTML = `<div class="cx-head"><div><h1>Conectores</h1><div class="sub">Conecte servidores MCP para os agentes trabalharem com as ferramentas da empresa</div></div>
    <div class="cx-addw"><button id="cx-add">${icon('plus')} Adicionar</button>
      <div class="cx-menu card" hidden><a href="#/connectors/custom">${icon('plug')} Servidor MCP personalizado</a><a href="#/connectors/import">${icon('file')} Importar JSON (mcpServers)</a></div></div></div>
  <input id="cx-q" class="cx-search" placeholder="Procurar conectores" autocomplete="off">
  <div id="cx-body">
    <section class="cx-sec"><h2>Instalados <span class="mute">›</span></h2>
      <div class="cx-installed">${installed.map(m => `<a href="#/connectors/${encodeURIComponent(m.name)}" class="cx-tile" title="${esc(m.name)}">${cxIcon(m.icon, 28, m.name)}${m.status && m.status !== 'ok' ? '<i class="cx-dot"></i>' : ''}</a>`).join('') || '<span class="mute small">Nenhum ainda</span>'}</div></section>
    <div class="seg mt" role="tablist"><button class="ghost small ${view === 'catalog' ? 'on' : ''}" data-v="catalog">Catálogo</button><button class="ghost small ${view === 'custom' ? 'on' : ''}" data-v="custom">Personalizados</button></div>
    ${view === 'custom'
      ? `<section class="cx-sec"><div class="cx-grid">${custom.map(m => `<a class="cx-row" href="#/connectors/${encodeURIComponent(m.name)}">${cxIcon(m.icon, 32, m.name)}
          <span class="cx-txt"><b data-noi18n>${esc(m.name)}</b><span class="mute small" data-noi18n>${esc(m.description || m.url)}</span></span>
          <span class="pill ${(CX_STATUS[m.status] || CX_STATUS[''])[1]}">${esc((CX_STATUS[m.status] || CX_STATUS[''])[0])}</span></a>`).join('')
          || '<div class="mute">Nenhum conector personalizado. Use <a href="#/connectors/custom">Adicionar → Servidor MCP personalizado</a>.</div>'}</div></section>`
      : focus ? section(CX.sections.find(s => s[0] === focus) || [focus, focus], bySec(focus), true) + '<div class="mt"><a href="#/connectors">← Todos os conectores</a></div>'
        : CX.sections.map(s => section(s, bySec(s[0]), false)).join('')}
  </div>`;
  const menu = main.querySelector('.cx-menu');
  $('#cx-add').onclick = e => { e.stopPropagation(); menu.hidden = !menu.hidden; };
  document.addEventListener('click', () => { menu.hidden = true; }, { once: true });
  main.querySelectorAll('.seg button').forEach(b => b.onclick = () => { location.hash = '#/connectors' + (b.dataset.v === 'custom' ? '?view=custom' : ''); });
  const body = $('#cx-body'), base = body.innerHTML;
  $('#cx-q').oninput = e => {
    const q = e.target.value.trim().toLowerCase();
    if (!q) { body.innerHTML = base; return; }
    const hit = items.filter(x => `${x.name} ${x.description} ${x.id}`.toLowerCase().includes(q)).slice(0, 60);
    body.innerHTML = `<section class="cx-sec"><h2>Resultados</h2><div class="cx-grid">${hit.map(row).join('') || '<div class="mute">Nada encontrado. Não está no catálogo? <a href="#/connectors/custom">Adicione um servidor MCP personalizado</a>.</div>'}</div></section>`;
  };
}

/* um item do catálogo ainda não instalado: o que é e como conectar */
async function connectorItemPage(key) {
  CX = CX || await api('/connectors');
  const x = CX.items.find(i => i.key === key);
  if (!x) { main.innerHTML = '<div class="card">Conector não encontrado. <a href="#/connectors">Voltar</a></div>'; return; }
  main.innerHTML = `<a href="#/connectors" class="mute small">← Conectores</a>
  <div class="row mt" style="gap:16px">${cxIcon(x.icon, 56, x.name)}<div><h1 style="margin:0" data-noi18n>${esc(x.name)}</h1>
    <div class="mute small"><span>${esc(CX_KIND[x.kind] || x.kind)}</span>${x.tools_count ? ` · ${x.tools_count} <span>ferramentas</span>` : ''}</div></div></div>
  <div class="sub mt" data-noi18n>${esc(x.description || '')}</div>
  <div class="card mt" id="cx-connect"></div>`;
  const box = $('#cx-connect');
  if (x.kind === 'docker') {
    box.innerHTML = `${x.installed ? `<div class="row between"><h2>Ativo <span class="pill ok">ativo</span></h2><a href="#/connectors/${encodeURIComponent(x.key)}">Ver o conector</a></div>
      <div class="mute small">Já está ativo. Preencha abaixo só para trocar a senha ou a configuração salvas (campo vazio mantém o valor atual).</div>`
      : '<h2>Ativar</h2>'}<div class="mute small">Roda num container isolado pelo gateway do catálogo Docker MCP (perfil mcp-gateway). Segredos ficam criptografados na central.</div>
      ${(x.secrets || []).map(s => `<label class="mt">${esc(s.name)}<input type="password" class="cx-sec-in" data-n="${esc(s.name)}" placeholder="${esc(s.description || s.env || '')}" autocomplete="off"></label>`).join('')}
      ${(x.config || []).map(c => `<label class="mt">${esc(c.name)}<input class="cx-cfg-in" data-n="${esc(c.name)}" placeholder="${esc(c.description || c.type)}"></label>`).join('')}
      <button id="cx-go" class="mt">${x.installed ? 'Salvar alterações' : 'Ativar conector'}</button>`;
    $('#cx-go').onclick = e => act(e.target, async () => {
      const secrets = {}, config = {};
      box.querySelectorAll('.cx-sec-in').forEach(i => { if (i.value) secrets[i.dataset.n] = i.value; });
      box.querySelectorAll('.cx-cfg-in').forEach(i => { if (i.value) config[i.dataset.n] = i.value; });
      await api('/mcp-gateway/servers/' + encodeURIComponent(x.id), { method: 'PUT', body: { secrets, config } });
      location.hash = '#/connectors';
    }, 'Conector ativado');
    return;
  }
  box.innerHTML = `<h2>Conectar</h2>
    <div class="mute small">${x.kind === 'curated' ? 'Servidor MCP oficial, no endereço publicado pelo fornecedor.' : 'Servidor MCP remoto do catálogo Docker MCP.'}
      Os agentes falam com ele pela central: a credencial fica no cofre e só passam as ferramentas que você aprovar.</div>
    <dl class="kv mt"><dt>Endereço</dt><dd>${copyable(x.url)}</dd><dt>Autenticação</dt><dd><span>${x.auth === 'oauth' ? 'OAuth (você autoriza no site do fornecedor)' : x.auth === 'bearer' ? 'token / chave do fornecedor' : 'nenhuma'}</span></dd></dl>
    ${x.auth === 'bearer' ? '<label class="mt">Token ou chave<input id="cx-tok" type="password" autocomplete="off" placeholder="vai direto para o cofre"></label>' : ''}
    ${scopeFields()}
    ${riskBox()}
    <button id="cx-go" class="mt" disabled>${x.auth === 'oauth' ? 'Conectar com OAuth' : 'Conectar'}</button>`;
  wireRisk(box);
  $('#cx-go').onclick = e => act(e.target, async () => {
    const r = await api('/connectors', { method: 'POST', body: { name: x.id, url: x.url, transport: x.transport || 'auto', auth: x.auth,
      token: ($('#cx-tok') || {}).value || '', source: x.kind, icon: x.key, description: x.description, accept_risk: true, ...scopeBody() } });
    if (r.authorize_url) { location.href = r.authorize_url; return; }
    location.hash = '#/connectors/' + encodeURIComponent(r.name);
  });
}

function scopeFields() {
  return `<div class="grid g2 mt"><label>Quem pode usar<select id="cx-scope">${ME.is_admin ? '<option value="org">toda a empresa</option>' : ''}<option value="team">um time</option><option value="personal">só eu (meus agentes pessoais)</option></select></label>
    <label id="cx-team-l">Time<input id="cx-team" placeholder="slug do time"></label></div>`;
}
function scopeBody() {
  const s = ($('#cx-scope') || {}).value || 'org';
  return { scope: s, team: s === 'team' ? $('#cx-team').value.trim() : '' };
}
function riskBox() {
  return `<div class="card warn-card mt"><b>Servidores MCP de terceiros apresentam riscos.</b>
      <div class="small">Conecte apenas servidores em que você confia. Um servidor não confiável pode receber dados que os agentes enviarem, ou enganar o agente para usar ferramentas de jeitos não previstos, inclusive alterando ou excluindo dados. A central bloqueia ferramentas novas ou alteradas até um admin aprovar.</div>
      <label class="row small mt" style="gap:8px"><input type="checkbox" id="cx-risk" style="width:auto"> <span>Entendo e quero continuar</span></label></div>`;
}
function wireRisk(box) {
  const cb = box.querySelector('#cx-risk'), go = box.querySelector('#cx-go');
  cb.onchange = () => { go.disabled = !cb.checked; };
  const sc = box.querySelector('#cx-scope'), tl = box.querySelector('#cx-team-l');
  if (sc) { const f = () => { tl.hidden = sc.value !== 'team'; }; sc.onchange = f; f(); }
}

/* sub-aba: servidor MCP personalizado (como o "Adicionar servidor MCP personalizado" do ChatGPT) */
async function connectorCustomPage() {
  let iconData = '', tested = null;
  main.innerHTML = `<a href="#/connectors" class="mute small">← Conectores</a><h1>Adicionar servidor MCP personalizado</h1>
  <div class="card" id="cc-form" style="max-width:720px">
    <div class="row" style="gap:14px;align-items:center"><label class="cx-upload" title="Ícone (opcional)">${icon('plus')}<input type="file" id="cc-icon" accept="image/png,image/jpeg,image/webp,image/svg+xml" hidden></label>
      <div><b>Ícone (opcional)</b><div class="mute small">PNG, JPEG, WebP ou SVG. Melhor com 256 × 256 px ou mais. Até 64 KB.</div></div></div>
    <label class="mt">Nome<input id="cc-name" placeholder="ferramenta-personalizada"></label>
    <label class="mt">Descrição (opcional)<input id="cc-desc" placeholder="Explique em poucas palavras o que ele faz"></label>
    <div class="row between mt"><b>Conexão</b><div class="seg"><button class="ghost small on" data-c="url">URL do servidor</button>${ME.is_admin ? '<button class="ghost small" data-c="stdio" title="Servidor local (npx ou uvx) num container isolado">Túnel (stdio)</button>' : ''}</div></div>
    <div id="cc-url-box"><input id="cc-url" class="mt" placeholder="https://example.com/mcp">
      <label class="mt">Transporte<select id="cc-tr"><option value="auto">Automático (Streamable HTTP; se não houver, SSE legado)</option><option value="http">Streamable HTTP</option><option value="sse">SSE (legado)</option></select></label></div>
    <div id="cc-stdio-box" hidden>
      <div class="mute small mt">O servidor roda num container isolado só dele: sem acesso à máquina da central, disco só de leitura, sem privilégios, e a internet só pelo proxy da central (o registro do pacote e os domínios que você liberar). Versão fixa é obrigatória.</div>
      <div class="grid g2 mt"><label>Executor<select id="cs-runner"><option value="npx">npx (Node)</option><option value="uvx">uvx (Python)</option></select></label>
        <label>Pacote com versão<input id="cs-pkg" placeholder="@modelcontextprotocol/server-filesystem@2025.8.21" data-noi18n></label></div>
      <label class="mt" id="cs-entry-l" hidden>Comando do pacote (uvx, opcional)<input id="cs-entry" placeholder="o nome do pacote" data-noi18n></label>
      <label class="mt">Argumentos (um por linha)<textarea id="cs-args" rows="3" placeholder="/tmp" data-noi18n></textarea></label>
      <div class="mt"><b class="small">Variáveis de ambiente</b><div id="cs-env"></div><button class="ghost small mt" id="cs-eadd">${icon('plus')} <span>Adicionar variável</span></button></div>
      <label class="mt">Domínios que o servidor pode acessar (um por linha; o registro do pacote já entra)<textarea id="cs-egress" rows="2" placeholder="api.example.com" data-noi18n></textarea></label></div>
    <label class="mt">Autenticação<select id="cc-auth"><option value="none">Nenhuma</option><option value="bearer">API key / Bearer token</option><option value="headers">Headers personalizados</option><option value="oauth">OAuth</option></select></label>
    <div id="cc-auth-box"></div>
    <details class="mt"><summary class="small">Avançado</summary>
      <label class="mt">Tempo máximo por chamada (segundos)<input id="cc-timeout" type="number" min="5" max="600" value="60"></label>
      ${scopeFields()}</details>
    <div class="row mt"><button id="cc-test" class="ghost">Testar conexão</button><span class="mute small">testa sem salvar nada</span></div>
    <div id="cc-result"></div>
    ${riskBox()}
    <div class="row between mt"><a href="#/connectors/import" class="small">Importar de um JSON (mcpServers)</a>
      <div class="row"><a class="btn ghost" href="#/connectors">Cancelar</a><button id="cx-go" disabled>Criar conector</button></div></div>
  </div>`;
  const box = $('#cc-form');
  wireRisk(box);
  let mode = 'url';
  box.querySelectorAll('.seg button').forEach(b => b.onclick = () => {
    box.querySelectorAll('.seg button').forEach(o => o.classList.toggle('on', o === b));
    mode = b.dataset.c;
    $('#cc-url-box').hidden = mode !== 'url'; $('#cc-stdio-box').hidden = mode === 'url';
    $('#cc-auth').closest('label').hidden = mode !== 'url'; $('#cc-auth-box').hidden = mode !== 'url';
  });
  const envRow = (n = '', v = '', s = false) => $('#cs-env').insertAdjacentHTML('beforeend', `<div class="grid g3 mt cs-e"><input class="cs-en" placeholder="NOME" value="${esc(n)}" data-noi18n>
    <input class="cs-ev" type="password" placeholder="Valor" value="${esc(v)}"><label class="row small"><input type="checkbox" class="cs-es" style="width:auto" ${s ? 'checked' : ''}> <span>é segredo (vai para o cofre)</span></label></div>`);
  $('#cs-eadd').onclick = e => { e.preventDefault(); envRow('', '', true); };
  $('#cs-runner').onchange = () => { const py = $('#cs-runner').value === 'uvx'; $('#cs-entry-l').hidden = !py; $('#cs-pkg').placeholder = py ? 'mcp-server-fetch==2025.4.7' : '@modelcontextprotocol/server-filesystem@2025.8.21'; };
  const stdioCfg = () => ({ runner: $('#cs-runner').value, package: $('#cs-pkg').value.trim(), entry: $('#cs-entry').value.trim(),
    args: $('#cs-args').value.split('\n').map(s => s.trim()).filter(Boolean), egress: $('#cs-egress').value.split('\n').map(s => s.trim()).filter(Boolean),
    env: [...box.querySelectorAll('.cs-e')].map(r => ({ name: r.querySelector('.cs-en').value.trim(), value: r.querySelector('.cs-ev').value, secret: r.querySelector('.cs-es').checked })).filter(x => x.name) });
  $('#cc-icon').onchange = e => {
    const f = e.target.files[0]; if (!f) return;
    if (f.size > 65536) { toast('Ícone: até 64 KB', true); return; }
    const rd = new FileReader();
    rd.onload = () => { iconData = rd.result; box.querySelector('.cx-upload').innerHTML = `<img src="${iconData}" alt="" width="44" height="44" style="border-radius:10px">`; };
    rd.readAsDataURL(f);
  };
  const authBox = () => {
    const a = $('#cc-auth').value;
    $('#cc-auth-box').innerHTML = a === 'bearer' ? `<label class="mt">Token ou chave<input id="cc-token" type="password" autocomplete="off" placeholder="enviado como Authorization: Bearer …; guardado no cofre"></label>`
      : a === 'headers' ? `<div class="mt" id="cc-hdrs"></div><button class="ghost mt" id="cc-hadd">${icon('plus')} Adicionar header</button>`
        : a === 'oauth' ? '<div class="mute small mt">A central descobre o servidor de autorização, se registra como cliente (registro dinâmico) e abre a tela de autorização do fornecedor. Os tokens ficam criptografados e são renovados sozinhos.</div>' : '';
    if (a === 'headers') {
      const add = (n = '', v = '', s = false) => $('#cc-hdrs').insertAdjacentHTML('beforeend', `<div class="grid g3 mt cc-h"><input class="cc-hn" placeholder="Nome (ex.: X-API-Key)" value="${esc(n)}">
        <input class="cc-hv" type="password" placeholder="Valor" value="${esc(v)}"><label class="row small"><input type="checkbox" class="cc-hs" style="width:auto" ${s ? 'checked' : ''}> <span>é segredo (vai para o cofre)</span></label></div>`);
      add('', '', true);
      $('#cc-hadd').onclick = e => { e.preventDefault(); add(); };
    }
  };
  $('#cc-auth').onchange = authBox; authBox();
  const cfg = () => mode === 'stdio' ? { url: '', stdio: stdioCfg(), timeout_s: Number($('#cc-timeout').value || 60) } : ({ url: $('#cc-url').value.trim(), transport: $('#cc-tr').value, auth: $('#cc-auth').value,
    token: ($('#cc-token') || {}).value || '', timeout_s: Number($('#cc-timeout').value || 60),
    headers: [...box.querySelectorAll('.cc-h')].map(r => ({ name: r.querySelector('.cc-hn').value.trim(), value: r.querySelector('.cc-hv').value, secret: r.querySelector('.cc-hs').checked })).filter(h => h.name) });
  $('#cc-test').onclick = e => act(e.target, async () => {
    if (mode === 'stdio') $('#cc-result').innerHTML = `<div class="card mt mute small">${esc(window.t('Subindo um container de teste e instalando o pacote (pode levar até 2 minutos)…'))}</div>`;
    tested = await api('/connectors/test', { method: 'POST', body: cfg() });
    renderTest(tested);
  });
  const renderTest = r => {
    const res = $('#cc-result');
    if (!r.ok) { res.innerHTML = `<div class="card mt"><span class="pill ${r.status === 'auth' ? 'warn' : 'bad'}">${r.status === 'auth' ? 'precisa de autenticação' : 'falhou'}</span> <span data-noi18n>${esc(r.error)}</span></div>`; return; }
    if (r.oauth) { res.innerHTML = `<div class="card mt"><span class="pill ok">OAuth encontrado</span><dl class="kv mt"><dt>Servidor de autorização</dt><dd data-noi18n>${esc(r.oauth.issuer || '—')}</dd><dt>Escopos</dt><dd data-noi18n>${esc(r.oauth.scope || '—')}</dd><dt>Registro dinâmico</dt><dd>${r.oauth.registration ? 'sim' : 'não'}</dd></dl></div>`; return; }
    res.innerHTML = `<div class="card mt"><div class="row between"><div><span class="pill ok">conectou</span> <span class="mute small" data-noi18n>${esc(r.server.name)} ${esc(r.server.version || '')} · MCP ${esc(r.protocol_version)} · ${r.transport === 'sse' ? 'SSE legado' : r.transport === 'stdio' ? 'stdio (container isolado)' : 'Streamable HTTP'}</span></div></div>
      <div class="mt">${Object.entries(r.capabilities).filter(([, v]) => v).map(([k]) => `<span class="chip">${esc(k)}</span>`).join(' ')}
        <span class="mute small"> · ${r.tools.length} <span>ferramentas</span> · ${r.resources} resources · ${r.prompts} prompts</span></div>
      ${r.tools.length ? `<div class="mt small"><b>Ferramentas liberadas para os agentes</b> <span class="mute">(as desmarcadas ficam desligadas)</span></div>
        <div class="cc-tools">${r.tools.map(t => `<label class="row small cc-t" style="gap:8px;align-items:flex-start"><input type="checkbox" value="${esc(t.name)}" ${t.destructive ? '' : 'checked'} style="width:auto;margin-top:3px">
          <span><code class="inline" data-noi18n>${esc(t.name)}</code>${t.destructive ? ' <span class="pill bad">destrutiva</span>' : t.read_only ? ' <span class="pill">só leitura</span>' : ''}<br><span class="mute" data-noi18n>${esc(t.description)}</span></span></label>`).join('')}</div>` : ''}</div>`;
  };
  $('#cx-go').onclick = e => act(e.target, async () => {
    const c = cfg();
    if (mode === 'url' && !c.url) throw new Error('Informe a URL do servidor');
    if (mode === 'stdio') toast(window.t('Instalando o pacote no container do conector (pode levar até 2 minutos)…'));
    const tools = $('#cc-result').querySelector('.cc-tools') ? [...$('#cc-result').querySelectorAll('.cc-t input:checked')].map(i => i.value) : undefined;
    const r = await api('/connectors', { method: 'POST', body: { ...c, name: $('#cc-name').value.trim(), description: $('#cc-desc').value.trim(),
      icon_data: iconData, accept_risk: true, ...(tools ? { tools } : {}), ...scopeBody() } });
    if (r.authorize_url) { location.href = r.authorize_url; return; }
    toast(r.status === 'ok' ? 'Conector criado' : `Conector criado, mas o teste falhou: ${r.status_detail}`, r.status !== 'ok');
    location.hash = '#/connectors/' + encodeURIComponent(r.name);
  });
}

/* importar o JSON mcpServers (Claude Desktop, Cursor, .mcp.json) */
async function connectorImportPage() {
  main.innerHTML = `<a href="#/connectors" class="mute small">← Conectores</a><h1>Importar conectores</h1>
  <div class="sub">Cole o JSON no formato mcpServers (Claude Desktop, Cursor ou o .mcp.json do Claude Code). Os servidores remotos entram como conectores; segredos nos headers vão para o cofre.</div>
  <div class="card"><textarea id="ci-text" rows="10" placeholder='{"mcpServers": {"docs": {"url": "https://docs.example.com/mcp", "headers": {"Authorization": "Bearer …"}}}}' data-noi18n></textarea>
    <button id="ci-read" class="mt ghost">Ler JSON</button><div id="ci-out"></div></div>`;
  $('#ci-read').onclick = e => act(e.target, async () => {
    const r = await api('/connectors/import/preview', { method: 'POST', body: { text: $('#ci-text').value } });
    const out = $('#ci-out');
    out.innerHTML = `<table class="mt"><tr><th></th><th>Nome</th><th>Endereço</th><th>Headers</th></tr>${r.servers.map((s, i) => `<tr class="${s.importable ? '' : 'mute'}">
        <td><input type="checkbox" class="ci-pick" data-i="${i}" ${s.importable ? 'checked' : s.fixable ? '' : 'disabled'} style="width:auto"></td><td data-noi18n>${esc(s.name)}</td>
        <td>${s.url ? `<code class="inline" data-noi18n>${esc(s.url)}</code>` : `<span data-noi18n>${esc(s.command || '')}</span>`}
          ${s.stdio ? `<div class="small mt"><span>pacote</span> <input class="ci-pkg" data-i="${i}" value="${esc(s.stdio.package || '')}" style="width:auto;min-width:260px" data-noi18n></div>` : ''}
          ${s.why ? `<div class="small mute ci-why" data-i="${i}" data-noi18n>${esc(s.why)}</div>` : ''}</td>
        <td>${(s.headers || []).map(h => `<span class="chip">${esc(h.name)}${h.secret ? ` · <span>cofre</span>` : ''}</span>`).join(' ')}</td></tr>`).join('')}</table>
      ${riskBox()}<button id="cx-go" class="mt" disabled>Importar selecionados</button>`;
    wireRisk(out);
    $('#cx-go').onclick = ev => act(ev.target, async () => {
      const picks = [...out.querySelectorAll('.ci-pick:checked')].map(c => r.servers[Number(c.dataset.i)]);
      const done = [];
      for (const s of picks) {
        const pkg = out.querySelector(`.ci-pkg[data-i="${r.servers.indexOf(s)}"]`);
        const c = await api('/connectors', { method: 'POST', body: s.stdio
          ? { name: s.name, stdio: { ...s.stdio, package: pkg ? pkg.value.trim() : s.stdio.package }, accept_risk: true, scope: 'org' }
          : { name: s.name, url: s.url, transport: s.transport, auth: s.auth, headers: s.headers, accept_risk: true, scope: 'org' } });
        done.push(`${c.name}: ${c.status === 'ok' ? 'ok' : c.status_detail || c.status}`);
      }
      toast(`${done.length} importado(s). ${done.join('; ')}`);
      location.hash = '#/connectors?view=custom';
    });
  });
}

/* um conector instalado: status, servidor, ferramentas (aprovar, desligar, tipo de ação) e quem usa */
async function connectorDetail(name) {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  if (params.get('remote_ok')) toast('Autorizado: o conector está pronto');
  if (params.get('remote_error')) toast(params.get('remote_error'), true);
  const c = await api('/connectors/' + encodeURIComponent(name));
  const st = CX_STATUS[c.status] || CX_STATUS[''];
  const si = c.server_info || {};
  main.innerHTML = `<a href="#/connectors" class="mute small">← Conectores</a>
  <div class="row between mt" style="flex-wrap:wrap;gap:12px"><div class="row" style="gap:16px">${cxIcon(c.icon, 56, c.name)}<div><h1 style="margin:0" data-noi18n>${esc(c.name)}</h1>
      <div class="small"><span class="pill ${st[1]}">${esc(st[0])}</span> ${c.checked_at ? `<span class="mute">· <span>testado</span> ${ago(c.checked_at)}</span>` : ''}</div></div></div>
    ${c.can_manage ? `<div class="row">${c.bridged ? '<button id="cd-check" class="ghost">Reconectar</button>' : ''}<button id="cd-del" class="ghost">Remover</button></div>` : ''}</div>
  ${c.status_detail ? `<div class="card warn-card mt" data-noi18n>${esc(c.status_detail)}</div>` : ''}
  ${c.description ? `<div class="sub mt" data-noi18n>${esc(c.description)}</div>` : ''}
  <div class="grid g2 mt"><div class="card"><h2>Conexão</h2><dl class="kv">
      ${c.stdio ? `<dt>Servidor local</dt><dd><code class="inline" data-noi18n>${esc(c.stdio.runner)} ${esc(c.stdio.package)}${c.stdio.entry && c.stdio.runner === 'uvx' ? ' ' + esc(c.stdio.entry) : ''}</code>
          ${(c.stdio.args || []).map(a => `<code class="inline" data-noi18n>${esc(a)}</code>`).join(' ')}<div class="small mute">container isolado · disco só de leitura · sem privilégios</div></dd>
        <dt>Variáveis</dt><dd>${(c.stdio.env || []).map(e => `<div class="small"><code class="inline" data-noi18n>${esc(e.name)}</code> <span class="mute" data-noi18n>${esc(e.value)}</span></div>`).join('') || '<span class="mute">nenhuma</span>'}</dd>
        <dt>Internet</dt><dd class="small">${[...(c.stdio.registry || []), ...(c.stdio.egress || [])].map(h => `<code class="inline" data-noi18n>${esc(h)}</code>`).join(' ')}
          ${c.server_info && c.server_info.egress_denied ? `<div class="bad-text"><span>recusado:</span> <span data-noi18n>${esc(c.server_info.egress_denied.host)}</span> (${c.server_info.egress_denied.count}×)</div>` : ''}</dd>`
        : `<dt>Endereço</dt><dd>${copyable(c.url)}</dd><dt>Transporte</dt><dd>${c.transport === 'sse' ? 'SSE (legado)' : 'Streamable HTTP'}</dd>`}
      <dt>Autenticação</dt><dd>${esc({ none: 'nenhuma', bearer: 'Bearer token', headers: 'headers personalizados', oauth: 'OAuth' }[c.auth] || c.auth)}
        ${c.headers.map(h => `<div class="small"><code class="inline" data-noi18n>${esc(h.name)}</code> ${h.secret ? `<span class="mute" data-noi18n>${esc(h.value)}</span>` : '<span class="mute">····</span>'}</div>`).join('')}</dd>
      <dt>Quem pode usar</dt><dd>${esc({ org: 'toda a empresa', team: 'um time', personal: 'só quem criou (agentes pessoais)' }[c.scope])}</dd>
      ${c.bridged ? '<dt>Ponte</dt><dd class="small mute">os agentes falam com a central; a credencial fica no cofre</dd>' : ''}</dl></div>
    <div class="card"><h2>Servidor</h2>${si.server ? `<dl class="kv"><dt>Nome</dt><dd data-noi18n>${esc(si.server.name)} ${esc(si.server.version || '')}</dd><dt>Protocolo MCP</dt><dd>${esc(si.protocol_version || '')}</dd>
        <dt>Capacidades</dt><dd>${Object.entries(si.capabilities || {}).filter(([, v]) => v).map(([k]) => `<span class="chip">${esc(k)}</span>`).join(' ')}</dd>
        <dt>Resources</dt><dd>${si.resources ?? 0} · <span>templates</span> ${si.resource_templates ?? 0} · prompts ${si.prompts ?? 0}</dd></dl>` : '<div class="mute small">Sem dados ainda: use Reconectar.</div>'}
      <h3>Usado por</h3>${c.used_by.map(s => `<a class="chip" href="#/agents/${esc(s)}">${esc(s)}</a>`).join(' ') || '<span class="mute small">nenhum agente</span>'}</div></div>
  ${c.tools.length ? `<div class="card mt"><h2>Ferramentas (${c.tools.length})</h2><div class="mute small">Só as aprovadas chegam aos agentes. Ferramenta nova ou com descrição alterada fica bloqueada até um admin aprovar. O tipo de ação define a alçada dos Digital employees.</div>
    <div class="scroll"><table class="mt"><tr><th>Ferramenta</th><th>Situação</th><th>Tipo de ação</th><th></th></tr>
    ${c.tools.map(t => `<tr><td><code class="inline" data-noi18n>${esc(t.name)}</code><div class="mute small" data-noi18n>${esc(t.description || '')}</div>
        ${t.state === 'changed' ? `<div class="small mt"><b>Nova descrição:</b> <span data-noi18n>${esc(t.new_description || '')}</span></div>` : ''}</td>
      <td><span class="pill ${(CX_STATE[t.state] || ['', ''])[1]}">${esc((CX_STATE[t.state] || [t.state])[0])}</span></td>
      <td>${c.can_manage ? `<select class="cd-act" data-t="${esc(t.name)}" style="width:auto;min-height:30px;padding:3px 8px"><option value="">automático</option>${Object.entries(CX_ACTION).map(([k, v]) => `<option value="${k}" ${k === t.action_type ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>` : esc(CX_ACTION[t.action_type] || '—')}</td>
      <td>${c.can_manage ? (t.state === 'approved' ? `<button class="ghost cd-st" data-t="${esc(t.name)}" data-s="disabled">Desligar</button>` : `<button class="cd-st" data-t="${esc(t.name)}" data-s="approved">${t.state === 'disabled' ? 'Ligar' : 'Aprovar'}</button>`) : ''}</td></tr>`).join('')}</table></div></div>` : ''}`;
  const chk = $('#cd-check');
  if (chk) chk.onclick = e => act(e.target, () => api(`/connectors/${encodeURIComponent(name)}/check`, { method: 'POST' }).then(() => connectorDetail(name)), 'Conector testado');
  const del = $('#cd-del');
  if (del) del.onclick = e => confirm(`Remover o conector "${name}"?`) && act(e.target, () => api('/connectors/' + encodeURIComponent(name), { method: 'DELETE' }).then(() => { location.hash = '#/connectors'; }), 'Conector removido');
  main.querySelectorAll('.cd-st').forEach(b => b.onclick = e => act(e.target, () => api(`/connectors/${encodeURIComponent(name)}/tools/${encodeURIComponent(b.dataset.t)}`, { method: 'PATCH', body: { state: b.dataset.s } }).then(() => connectorDetail(name)), b.dataset.s === 'approved' ? 'Ferramenta aprovada' : 'Ferramenta desligada'));
  main.querySelectorAll('.cd-act').forEach(s => s.onchange = () => s.value && act(s, () => api(`/connectors/${encodeURIComponent(name)}/tools/${encodeURIComponent(s.dataset.t)}`, { method: 'PATCH', body: { action_type: s.value } }), 'Tipo de ação salvo'));
}
