/* ---------- Provedores de LLM: um cartão por provedor (como no 9router) e, dentro, as conexões e os modelos ---------- */
const MODEL_KIND = { text: 'Texto', embedding: 'Embedding', rerank: 'Rerank', image: 'Imagem', video: 'Vídeo', tts: 'Voz (TTS)',
  stt: 'Transcrição (STT)', moderation: 'Moderação', other: 'Outro' };
const MODEL_KIND_HINT = { text: 'conversa, raciocínio, código', embedding: 'busca semântica e RAG', rerank: 'reordenar resultados de busca',
  image: 'gerar imagens', video: 'gerar vídeos', tts: 'texto para voz', stt: 'voz para texto', moderation: 'filtrar conteúdo', other: '' };
const CAP_LABEL = { vision: 'lê imagens', tools: 'ferramentas', reasoning: 'raciocínio', audio_in: 'ouve áudio', audio_out: 'fala' };
const SRC_LABEL = { provider: 'informado pelo provedor', catalog: 'tabela pública de modelos', name: 'deduzido pelo nome', admin: 'corrigido por um admin' };
const PV_GROUPS = [['gateway', 'Gateways e roteadores'], ['api', 'Provedores com API key'], ['local', 'Na sua máquina'], ['custom', 'Compatíveis (custom)']];
let PV = null;  // {providers, kinds}

async function pvLoad() {
  if (!PV) PV = await api('/catalog/providers');
  return PV;
}
const pvOf = id => PV.providers.find(p => p.id === id) || PV.providers.find(p => p.id === 'custom');
const pvConns = (c, id) => c.llm_connections.filter(x => (x.provider || 'custom') === id);
function pvLogo(p, size = 36) {
  return p.logo ? `<img class="pv-logo" src="${esc(p.logo)}" alt="" width="${size}" height="${size}" loading="lazy">`
    : `<span class="pv-logo pv-ini" style="--pv:${esc(p.color || '#6E7781')}">${esc(p.initials || p.name.slice(0, 2).toUpperCase())}</span>`;
}
function pvStatus(conns) {
  if (!conns.length) return '<span class="pv-st">Sem conexão</span>';
  const models = conns.reduce((n, x) => n + (x.models_total || 0), 0), bad = conns.some(x => x.models_error);
  return `<span class="pv-st ${bad ? 'bad' : 'on'}"><i></i>${conns.length} ${conns.length > 1 ? 'conectadas' : 'conectada'}</span>`
    + (models ? `<span class="pv-st">${fmt(models)} ${models > 1 ? 'modelos' : 'modelo'}</span>` : '');
}
const AREA_LABEL = { reasoning: 'Raciocínio', coding: 'Código', speed: 'Velocidade', cost: 'Custo' };
const SCORE_SRC = { artificial_analysis: 'medida (Artificial Analysis)', research: 'estimativa da pesquisa', admin: 'corrigida por um admin', price: 'pelo preço' };
const scoreChips = pf => pf && pf.scores ? ['reasoning', 'coding', 'speed', 'cost'].filter(a => pf.scores[a])
  .map(a => `<span class="chip sc-${esc(pf.scores[a].source)}" title="${esc(SCORE_SRC[pf.scores[a].source] || '')}: ${esc(pf.scores[a].basis || '')}"><span>${esc(AREA_LABEL[a])}</span> <b>${pf.scores[a].value}</b></span>`).join(' ') : '';
const kindChips = kinds => Object.entries(kinds || {}).sort((a, b) => b[1] - a[1])
  .map(([k, n]) => `<span class="chip" title="${esc(MODEL_KIND_HINT[k] || '')}"><span>${esc(MODEL_KIND[k] || k)}</span> <b>${n}</b></span>`).join(' ');

async function providersPage() {
  const [c] = await Promise.all([api('/catalog'), pvLoad()]);
  const total = {};
  c.llm_connections.forEach(x => Object.entries(x.models || {}).forEach(([k, n]) => { total[k] = (total[k] || 0) + n; }));
  const missing = PV.kinds.filter(k => k !== 'other' && !total[k]);
  const card = (p, conns) => `<a class="pv-card" href="#/providers/${encodeURIComponent(p.id)}">${pvLogo(p)}
    <span class="pv-txt"><b>${esc(p.name)}</b><span class="pv-sts">${pvStatus(conns)}</span></span></a>`;
  const section = ([g, title]) => {
    const list = PV.providers.filter(p => p.group === g);
    const conns = list.flatMap(p => pvConns(c, p.id));
    const cards = g === 'custom'
      ? conns.map(x => card({ ...pvOf(x.provider), name: x.name }, [x])).join('')
      : list.map(p => card(p, pvConns(c, p.id))).join('');
    return `<section class="mt"><div class="row between"><h2>${esc(title)}</h2><div class="row">
      ${g === 'custom' ? `<a class="btn ghost" href="#/providers/custom-anthropic">+ Compatível com Anthropic</a><a class="btn" href="#/providers/custom">+ Compatível com OpenAI</a>` : ''}
      ${conns.length ? `<button class="ghost pv-testall" data-n="${esc(conns.map(x => x.name).join(','))}">${icon('check')} Testar todas</button>` : ''}</div></div>
      ${cards ? `<div class="pv-grid">${cards}</div>` : '<div class="mute small">Nenhum endpoint compatível ainda: use os botões acima para um vLLM, Azure, Bedrock via gateway ou qualquer API no formato da OpenAI ou da Anthropic.</div>'}</section>`;
  };
  main.innerHTML = `<h1>Provedores de LLM</h1>
  <div class="sub">Conecte os provedores que a empresa usa. A central busca a lista de modelos de cada um (tipo de saída, contexto e preço) na hora e depois a cada hora; o construtor de agentes usa essa lista para escolher o modelo de cada agente e para saber quando falta um tipo de modelo.</div>
  <div class="card"><div class="row between" style="align-items:flex-start"><div><b>Modelos disponíveis por tipo</b>
    <div class="mt">${kindChips(total) || '<span class="mute">nenhum ainda: conecte um provedor abaixo</span>'}</div>
    ${missing.length && c.llm_connections.length ? `<div class="small mute mt"><span>Ainda sem modelo de:</span> ${missing.map(k => `<span class="chip" title="${esc(MODEL_KIND_HINT[k])}">${esc(MODEL_KIND[k])}</span>`).join(' ')}
      <div>Um agente que precisar desses tipos vai pedir para você cadastrar um provedor que os tenha.</div></div>` : ''}</div>
    ${ME.is_admin ? `<label class="small" style="min-width:230px">Código e LLMs<select id="code-policy"><option value="any">Qualquer conexão recebe código</option><option value="approved">Só conexões aprovadas para código</option></select></label>` : ''}</div></div>
  ${ME.is_admin ? '<div id="mr-card" class="mt"></div>' : ''}
  ${PV_GROUPS.map(section).join('')}
  <div class="mute small mt">Logos dos provedores: projeto 9router (licença MIT). As marcas são dos respectivos donos.</div>`;
  if ($('#mr-card')) researchCard($('#mr-card'), c);
  const pol = $('#code-policy');
  if (pol) {
    pol.value = (ME.org && ME.org.code_policy) || 'any';
    pol.onchange = () => act(pol, async () => {
      const o = await api('/org', { method: 'PATCH', body: { code_policy: pol.value } });
      ME.org = o;
      toast(o.code_policy === 'approved' ? 'Agora só conexões aprovadas recebem código' : 'Qualquer conexão pode receber código');
    });
  }
  document.querySelectorAll('.pv-testall').forEach(b => b.onclick = e => act(e.target, async () => {
    const res = await Promise.all(b.dataset.n.split(',').map(n => api(`/catalog/llm/${encodeURIComponent(n)}/test`, { method: 'POST' }).catch(err => ({ name: n, ok: false, detail: err.message }))));
    const bad = res.filter(r => !r.ok);
    toast(bad.length ? `${bad.length} de ${res.length} com problema: ${bad.map(r => `${r.name} (${r.detail})`).join('; ')}` : `${res.length} de ${res.length} responderam`, !!bad.length);
  }));
}

async function providerDetail(id) {
  const [c] = await Promise.all([api('/catalog'), pvLoad()]);
  const p = pvOf(id);
  const conns = pvConns(c, p.id);
  const used = new Set(c.llm_connections.map(x => x.name));
  let suggested = p.id;
  for (let i = 2; used.has(suggested); i++) suggested = `${p.id}-${i}`;
  main.innerHTML = `<a href="#/providers" class="mute small">← Provedores de LLM</a>
  <div class="row mt" style="gap:14px">${pvLogo(p, 48)}<div><h1 style="margin:0">${esc(p.name)}</h1>
    <div class="mute small">${p.kinds ? `<span>Costuma ter:</span> ${p.kinds.split(', ').map(k => `<span class="chip">${esc(MODEL_KIND[k] || CAP_LABEL[k] || k)}</span>`).join(' ')}` : ''}${p.key_url ? ` · <a href="${esc(p.key_url)}" target="_blank" rel="noopener">onde pegar a chave</a>` : ''}</div></div></div>
  ${p.note ? `<div class="sub mt">${esc(p.note)}</div>` : '<div class="mt"></div>'}
  <div id="pv-conns">${conns.map(connCard).join('') || ''}</div>
  ${ME.is_admin ? `<details class="card mt" ${conns.length ? '' : 'open'}><summary><b>${conns.length ? 'Adicionar outra conexão' : 'Conectar'}</b></summary>
    <div class="grid g2 mt">
      <label>Nome da conexão<input id="nc-name" value="${esc(suggested)}"></label>
      <label>API key<input id="nc-key" type="password" autocomplete="off" placeholder="${p.id === 'ollama' ? 'qualquer valor (ex.: ollama)' : 'chave do provedor (de preferência uma chave virtual)'}"></label></div>
    <div class="grid g2 mt">
      <label>Endereço (base URL)<input id="nc-url" value="${esc(p.base_url)}" placeholder="https://…/v1"></label>
      <label>Modelo padrão <span class="mute">(opcional: vazio = escolhido da lista do provedor)</span><input id="nc-model" placeholder="${p.id === 'perplexity' ? 'sonar' : 'ex.: o id de um modelo de texto'}"></label></div>
    <details class="mt"><summary class="small">Avançado</summary><div class="grid g4 mt">
      <label>Protocolo<select id="nc-proto">${PROTO_OPTIONS(p.protocol)}</select></label>
      <label>US$/1M entrada<input id="nc-pin" type="number" step="0.01" min="0" placeholder="da lista"></label>
      <label>US$/1M saída<input id="nc-pout" type="number" step="0.01" min="0" placeholder="da lista"></label>
      <label>Descrição<input id="nc-desc" placeholder="opcional"></label></div>
      <div class="mute small mt">O preço informado aqui vale para o modelo padrão; os outros modelos usam o preço da lista.</div></details>
    <button id="nc-go" class="mt">Salvar e buscar os modelos</button></details>` : ''}`;
  conns.forEach(x => connWire(x, p));
  const go = $('#nc-go');
  if (go) go.onclick = e => act(e.target, async () => {
    const r = await api('/catalog/llm', { method: 'POST', body: { name: $('#nc-name').value.trim(), provider: p.id,
      base_url: $('#nc-url').value.trim(), api_key: $('#nc-key').value, model_name: $('#nc-model').value.trim(),
      protocol: $('#nc-proto').value, description: $('#nc-desc').value, price_in_per_mtok: num($('#nc-pin').value),
      price_out_per_mtok: num($('#nc-pout').value) } });
    toast(r.sync && r.sync.ok ? `Conexão "${r.name}" salva: ${r.sync.total} modelo(s) encontrado(s)` : `Conexão "${r.name}" salva, mas a lista de modelos falhou: ${r.models_error}`, !(r.sync && r.sync.ok));
    await providerDetail(id);
  });
}

function connCard(x) {
  const n = esc(x.name);
  return `<div class="card mt" id="cc-${n}"><div class="row between" style="align-items:flex-start">
    <div><h2 style="margin:0">${n}</h2><div class="mute small">${copyable(x.base_url)} · chave ${esc(x.api_key || 'nenhuma')} · ${protoPill(x.protocol)}
      ${x.allow_code ? '<span class="pill ok">aprovada para código</span>' : ''}</div>
      <div class="small mt">${x.models_error ? `<span class="bad-text">Lista de modelos: ${esc(x.models_error)}</span>`
        : x.models_synced_at ? `<span class="mute"><span>Lista atualizada</span> ${ago(x.models_synced_at)}</span>` : '<span class="mute">Lista ainda não buscada</span>'}
        ${x.models_total ? ` · ${kindChips(x.models)}` : ''}</div></div>
    ${ME.is_admin ? `<div class="row" style="flex-wrap:wrap;justify-content:flex-end">
      <button class="ghost cc-test" data-n="${n}">Testar</button><button class="ghost cc-sync" data-n="${n}">Atualizar modelos</button>
      <button class="ghost cc-code" data-n="${n}" data-v="${x.allow_code ? 0 : 1}">${x.allow_code ? 'Retirar aprovação para código' : 'Aprovar para código'}</button>
      <button class="ghost cc-del" data-n="${n}">Remover</button></div>` : ''}</div>
    <div class="row mt" style="gap:10px;align-items:flex-end"><label style="min-width:260px">Modelo padrão
      <select class="cc-default" data-n="${n}" ${ME.is_admin ? '' : 'disabled'}><option>${esc(x.model_name)}</option></select></label>
      <span class="mute small">usado pelos agentes que não escolhem um modelo</span></div>
    <div class="cc-models mt" data-n="${n}"><div class="mute small">carregando os modelos…</div></div></div>`;
}

async function connWire(x, p) {
  const box = document.querySelector(`.cc-models[data-n="${CSS.escape(x.name)}"]`);
  const root = document.getElementById('cc-' + x.name);
  const q = s => root.querySelector(s);
  root.querySelectorAll('.cc-test').forEach(b => b.onclick = e => act(e.target, async () => {
    const r = await api(`/catalog/llm/${encodeURIComponent(x.name)}/test`, { method: 'POST' });
    toast(`${x.name}: ${r.ok ? '✓ ' : '✗ '}${r.detail}`, !r.ok);
  }));
  root.querySelectorAll('.cc-sync').forEach(b => b.onclick = e => act(e.target, async () => {
    const r = await api(`/catalog/llm/${encodeURIComponent(x.name)}/models/sync`, { method: 'POST' });
    toast(r.ok ? `${r.total} modelo(s)${r.added.length ? `, ${r.added.length} novo(s)` : ''}${r.lost.length ? `, ${r.lost.length} saíram do provedor` : ''}` : r.error, !r.ok);
    await providerDetail(p.id);
  }));
  root.querySelectorAll('.cc-code').forEach(b => b.onclick = e => act(e.target, () => api(`/catalog/llm/${encodeURIComponent(x.name)}/code`,
    { method: 'PATCH', body: { allow_code: b.dataset.v === '1' } }).then(() => providerDetail(p.id)),
  b.dataset.v === '1' ? 'Conexão aprovada para código' : 'Aprovação retirada'));
  root.querySelectorAll('.cc-del').forEach(b => b.onclick = e => confirm(`Remover a conexão "${x.name}" e a lista de modelos dela?`) &&
    act(e.target, () => api('/catalog/llm/' + encodeURIComponent(x.name), { method: 'DELETE' }).then(() => providerDetail(p.id)), 'Removida'));
  const models = await api(`/catalog/models?connection=${encodeURIComponent(x.name)}&all=true`);
  const text = models.filter(m => m.kind === 'text' && m.available);
  const sel = q('.cc-default');
  if (text.length) {
    sel.innerHTML = text.map(m => `<option value="${esc(m.id)}" ${m.id === x.model_name ? 'selected' : ''}>${esc(m.id)}</option>`).join('')
      + (text.some(m => m.id === x.model_name) ? '' : `<option selected>${esc(x.model_name)}</option>`);
    sel.onchange = () => act(sel, () => api(`/catalog/llm/${encodeURIComponent(x.name)}/models/${encodeURIComponent(sel.value)}`,
      { method: 'PATCH', body: { default: true } }), 'Modelo padrão trocado');
  }
  const kinds = [...new Set(models.map(m => m.kind))];
  const state = { kind: '', q: '', open: '' };
  const draw = () => {
    const list = models.filter(m => (!state.kind || m.kind === state.kind) && (!state.q || m.id.toLowerCase().includes(state.q)));
    const shown = list.slice(0, 200);
    const price = m => m.price_in_per_mtok == null && m.price_out_per_mtok == null ? '<span class="mute">—</span>'
      : `${m.price_in_per_mtok ?? 0} · ${m.price_out_per_mtok ?? 0}`;
    q('.cc-rows').innerHTML = shown.map(m => `<tr class="${m.available ? '' : 'mute'}"><td><a href="#" class="cc-prof" data-m="${esc(m.id)}" title="ver a ficha"><code class="inline" data-noi18n>${esc(m.id)}</code></a>
        ${m.default ? ' <span class="pill ok">padrão</span>' : ''}${m.available ? '' : ' <span class="pill bad">saiu do provedor</span>'}${m.manual ? ' <span class="pill">incluído à mão</span>' : ''}</td>
      <td>${ME.is_admin ? `<select class="cc-kind" data-m="${esc(m.id)}" title="${esc(SRC_LABEL[m.kind_source] || '')}" style="width:auto;min-height:30px;padding:3px 8px">
          ${PV.kinds.map(k => `<option value="${k}" ${k === m.kind ? 'selected' : ''}>${esc(MODEL_KIND[k])}</option>`).join('')}</select>`
        : esc(MODEL_KIND[m.kind] || m.kind)}</td>
      <td>${(m.caps || []).map(cp => `<span class="chip">${esc(CAP_LABEL[cp] || cp)}</span>`).join(' ')}</td>
      <td>${scoreChips(m.profile) || '<span class="mute">—</span>'}</td>
      <td class="mute">${m.context_length ? fmt(m.context_length) : '—'}</td>
      <td title="${esc(SRC_LABEL[m.price_source] || '')}">${price(m)}</td>
      <td>${ME.is_admin && m.kind === 'text' && m.available && !m.default ? `<a href="#" class="small cc-mkdef" data-m="${esc(m.id)}">tornar padrão</a>` : ''}
        ${ME.is_admin && (m.manual || !m.available) ? ` <a href="#" class="small cc-rm" data-m="${esc(m.id)}">tirar</a>` : ''}</td></tr>`).join('')
      || `<tr><td colspan="7" class="empty">${models.length ? 'Nenhum modelo com esse filtro' : (p.list === 'none' ? 'Este provedor não publica a lista: inclua abaixo os modelos que vai usar.' : 'Nenhum modelo ainda')}</td></tr>`;
    q('.cc-count').textContent = list.length > shown.length ? `mostrando ${shown.length} de ${list.length}; refine a busca` : `${list.length} modelo(s)`;
    root.querySelectorAll('.cc-prof').forEach(a => a.onclick = e => { e.preventDefault(); state.open = state.open === a.dataset.m ? '' : a.dataset.m; draw(); });
    const pfBox = q('.cc-pf');  // a ficha aparece abaixo da tabela (dentro dela ficaria cortada em telas estreitas)
    pfBox.hidden = !state.open;
    if (state.open && pfBox.dataset.m !== state.open) {
      pfBox.dataset.m = state.open;
      pfBox.innerHTML = `<div class="row between"><h3 style="margin:0"><code class="inline" data-noi18n>${esc(state.open)}</code></h3><a href="#" class="small cc-pf-close">Fechar</a></div><div class="cc-pf-body mt"><span class="mute small">carregando a ficha…</span></div>`;
      pfBox.querySelector('.cc-pf-close').onclick = e => { e.preventDefault(); state.open = ''; draw(); };
      profileView(pfBox.querySelector('.cc-pf-body'), state.open, () => api(`/catalog/models?connection=${encodeURIComponent(x.name)}&all=true`)
        .then(l => { models.splice(0, models.length, ...l); pfBox.dataset.m = ''; draw(); }));
      pfBox.scrollIntoView({ block: 'nearest' });
    }
    if (!state.open) pfBox.dataset.m = '';
    root.querySelectorAll('.cc-kind').forEach(s => s.onchange = () => act(s, async () => {
      const r = await api(`/catalog/llm/${encodeURIComponent(x.name)}/models/${encodeURIComponent(s.dataset.m)}`, { method: 'PATCH', body: { kind: s.value } });
      Object.assign(models.find(m => m.id === r.id), r);
    }, 'Tipo corrigido: a atualização da lista não desfaz'));
    root.querySelectorAll('.cc-mkdef').forEach(a => a.onclick = e => { e.preventDefault(); act(a, () => api(`/catalog/llm/${encodeURIComponent(x.name)}/models/${encodeURIComponent(a.dataset.m)}`,
      { method: 'PATCH', body: { default: true } }).then(() => providerDetail(p.id)), 'Modelo padrão trocado'); });
    root.querySelectorAll('.cc-rm').forEach(a => a.onclick = e => { e.preventDefault(); act(a, () => api(`/catalog/llm/${encodeURIComponent(x.name)}/models/${encodeURIComponent(a.dataset.m)}`,
      { method: 'DELETE' }).then(() => providerDetail(p.id)), 'Modelo retirado'); });
  };
  box.innerHTML = `<div class="row between" style="flex-wrap:wrap;gap:8px"><div class="row" style="flex-wrap:wrap;gap:6px">
      <button class="ghost cc-f on" data-k="">Todos</button>${kinds.map(k => `<button class="ghost cc-f" data-k="${k}">${esc(MODEL_KIND[k] || k)}</button>`).join('')}</div>
      <input class="cc-q" placeholder="buscar modelo" style="max-width:240px"></div>
    <div class="scroll mt"><table><tr><th>Modelo</th><th>Tipo</th><th>Recursos</th><th title="0 a 100; passe o mouse para ver de onde veio cada nota">Notas</th><th>Contexto</th><th>US$/1M (entrada · saída)</th><th></th></tr><tbody class="cc-rows"></tbody></table></div>
    <div class="mute small cc-count"></div>
    <div class="card mt cc-pf" hidden></div>
    ${ME.is_admin ? `<details class="mt" ${p.list === 'none' && !models.length ? 'open' : ''}><summary class="small">Incluir um modelo à mão (provedor sem lista ou modelo que a lista não mostra)</summary>
      <div class="grid g4 mt"><input class="cm-id" placeholder="id do modelo"><select class="cm-kind"><option value="">tipo: deduzir pelo nome</option>${PV.kinds.map(k => `<option value="${k}">${esc(MODEL_KIND[k])}</option>`).join('')}</select>
        <input class="cm-pin" type="number" step="0.01" min="0" placeholder="US$/1M entrada"><input class="cm-pout" type="number" step="0.01" min="0" placeholder="US$/1M saída"></div>
      <button class="ghost cm-go mt">Incluir modelo</button></details>` : ''}`;
  box.querySelectorAll('.cc-f').forEach(b => b.onclick = () => {
    state.kind = b.dataset.k;
    box.querySelectorAll('.cc-f').forEach(o => o.classList.toggle('on', o === b));
    draw();
  });
  q('.cc-q').oninput = e => { state.q = e.target.value.trim().toLowerCase(); draw(); };
  const add = q('.cm-go');
  if (add) add.onclick = e => act(e.target, () => api(`/catalog/llm/${encodeURIComponent(x.name)}/models`, { method: 'POST', body: {
    model_id: q('.cm-id').value.trim(), kind: q('.cm-kind').value, price_in_per_mtok: num(q('.cm-pin').value),
    price_out_per_mtok: num(q('.cm-pout').value) } }).then(() => providerDetail(p.id)), 'Modelo incluído');
  draw();
}

/* o modelo de um agente novo: a lista de modelos de texto da conexão escolhida */
async function modelOptions(select, conn) {
  if (!conn) { select.innerHTML = '<option value="">padrão da conexão</option>'; select.disabled = true; return; }
  const list = await api(`/catalog/models?connection=${encodeURIComponent(conn)}&kind=text`).catch(() => []);
  select.disabled = !list.length;
  select.innerHTML = '<option value="">padrão da conexão</option>' + list.map(m => `<option value="${esc(m.id)}">${esc(m.id)}${m.default ? ' (padrão)' : ''}${m.price_in_per_mtok != null ? ` · US$ ${m.price_in_per_mtok}/${m.price_out_per_mtok ?? 0}` : ''}${(m.caps || []).includes('vision') ? ' · lê imagens' : ''}${m.profile && m.profile.scores ? ['reasoning', 'coding'].filter(a => m.profile.scores[a]).map(a => ` · ${AREA_LABEL[a].toLowerCase()} ${m.profile.scores[a].value}`).join('') : ''}</option>`).join('');
}

/* a ficha de um modelo: para que é bom, notas com a origem, fontes; o admin pesquisa e corrige */
async function profileView(box, modelId, reload) {
  let pf = null;
  try { pf = await api(`/catalog/model-profiles/${encodeURIComponent(modelId)}`); } catch (e) { pf = null; }
  const list = (t, xs) => xs && xs.length ? `<div class="mt"><b class="small">${esc(t)}</b><ul class="small" style="margin:4px 0 0 18px;padding:0">${xs.map(v => `<li data-noi18n>${esc(v)}</li>`).join('')}</ul></div>` : '';
  const scores = pf ? Object.entries(pf.scores || {}) : [];
  box.innerHTML = pf ? `<div class="grid g2"><div>
      ${pf.summary ? `<div data-noi18n>${esc(pf.summary)}</div>` : '<div class="mute">Sem resumo ainda.</div>'}
      ${pf.best_for && pf.best_for.length ? `<div class="mt"><b class="small">Bom para</b> ${pf.best_for.map(v => `<span class="chip" data-noi18n>${esc(v)}</span>`).join(' ')}</div>` : ''}
      ${list('Pontos fortes', pf.strengths)}${list('Limitações', pf.weaknesses)}</div>
    <div>${scores.length ? `<table class="small"><tr><th>Área</th><th>Nota</th><th>Origem</th></tr>${scores.map(([a, v]) => `<tr><td>${esc(AREA_LABEL[a] || a)}</td><td><b>${v.value}</b></td>
        <td><span>${esc(SCORE_SRC[v.source] || v.source)}</span>${v.basis ? `<div class="mute" data-noi18n>${esc(v.basis)}</div>` : ''}</td></tr>`).join('')}</table>` : '<div class="mute small">Sem notas ainda.</div>'}
      ${(pf.sources || []).length ? `<div class="mt small"><b>Fontes</b>${pf.sources.map(s => `<div><a href="${esc(s.url)}" target="_blank" rel="noopener" data-noi18n>${esc(s.title)}</a> <span class="mute">· ${ago(s.fetched_at)}</span></div>`).join('')}</div>` : ''}
      <div class="mute small mt">${pf.researched_at ? `<span>Pesquisada</span> ${ago(pf.researched_at)}${pf.research_cost_usd ? ` · US$ ${pf.research_cost_usd}` : ''}` : '<span>Ainda não pesquisada com LLM</span>'}
        ${pf.research_status === 'error' || pf.research_status === 'partial' ? ` · <span class="bad-text" data-noi18n>${esc(pf.research_error)}</span>` : ''}
        ${(pf.locked || []).length ? ` · <span>corrigido por um admin:</span> ${pf.locked.map(f => esc(AREA_LABEL[f] || f)).join(', ')}` : ''}</div></div></div>`
    : '<div class="mute small">Este modelo ainda não tem ficha. As fontes sem custo entram de hora em hora; a pesquisa com LLM precisa estar configurada em Pesquisa de modelos.</div>';
  if (!ME.is_admin) return;
  box.insertAdjacentHTML('beforeend', `<div class="row mt" style="flex-wrap:wrap;gap:8px;align-items:flex-start"><button class="ghost pf-go">Pesquisar agora</button>
    <details style="flex:1;min-width:260px"><summary class="small">Corrigir a ficha</summary>
      <label class="mt">Resumo<textarea class="pf-sum" rows="3">${esc(pf ? pf.summary : '')}</textarea></label>
      <label class="mt">Bom para (vírgula)<input class="pf-best" value="${esc(pf ? (pf.best_for || []).join(', ') : '')}"></label>
      <div class="grid g4 mt">${['reasoning', 'coding', 'speed', 'cost'].map(a => `<label>${esc(AREA_LABEL[a])}<input class="pf-sc" data-a="${a}" type="number" min="0" max="100" value="${pf && pf.scores && pf.scores[a] ? pf.scores[a].value : ''}"></label>`).join('')}</div>
      <div class="mute small mt">O que você corrigir fica travado: a pesquisa não muda mais. Nota vazia volta a vir das fontes.</div>
      <button class="pf-save mt">Salvar ficha</button></details></div>`);
  box.querySelector('.pf-go').onclick = e => act(e.target, async () => {
    const r = await api(`/catalog/model-profiles/${encodeURIComponent(modelId)}/research`, { method: 'POST' });
    toast(r.ok ? `Ficha pesquisada (${r.sources} fonte(s), US$ ${r.cost_usd})` : `Pesquisa sem resultado: ${r.error}`, !r.ok);
    await reload();
  });
  box.querySelector('.pf-save').onclick = e => act(e.target, async () => {
    const scores = {}, unlock = [];
    box.querySelectorAll('.pf-sc').forEach(i => {
      if (i.value === '') { if (pf && (pf.locked || []).includes(i.dataset.a)) unlock.push(i.dataset.a); } else scores[i.dataset.a] = Number(i.value);
    });
    await api(`/catalog/model-profiles/${encodeURIComponent(modelId)}`, { method: 'PATCH', body: { summary: box.querySelector('.pf-sum').value,
      best_for: box.querySelector('.pf-best').value.split(',').map(v => v.trim()).filter(Boolean), scores, unlock } });
    toast('Ficha salva');
    await reload();
  });
}

/* como as fichas são pesquisadas: conexão que resume, busca na web, Artificial Analysis e orçamento */
async function researchCard(box, c) {
  const s = await api('/catalog/model-profiles/settings');
  const conns = c.llm_connections.filter(x => x.protocol === 'openai');
  box.innerHTML = `<details class="card" ${s.connection ? '' : 'open'}><summary><b>Pesquisa de modelos (fichas)</b>
      <span class="mute small"> · <span>${s.researcher_ready ? 'ligada' : 'desligada'}</span> · <span>gasto no mês</span> US$ ${s.spent_this_month_usd} / ${s.monthly_budget_usd}</span></summary>
    <div class="mute small mt">Cada modelo ganha uma ficha: para que é bom, notas de 0 a 100 por área e de onde veio cada informação. Sem custo e de hora em hora: a descrição pública do OpenRouter e, com a chave, as medições do Artificial Analysis. Com a pesquisa ligada, um modelo da empresa lê o card do Hugging Face e a busca na web e resume a ficha (primeiro os modelos em uso).</div>
    ${s.public_sources ? '' : '<div class="small mt"><b>Fontes públicas desligadas nesta instalação (MODEL_PUBLIC_SOURCES=0).</b></div>'}
    <div class="grid g4 mt">
      <label>Conexão que pesquisa<select id="mr-conn"><option value="">nenhuma (só fontes sem custo)</option>${conns.map(x => `<option value="${esc(x.name)}" ${x.name === s.connection ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label>
      <label>Modelo<select id="mr-model"><option value="">padrão da conexão</option></select></label>
      <label>Pesquisa automática<select id="mr-auto"><option value="off">desligada (só sob pedido)</option><option value="used">modelos em uso e padrões</option><option value="all">todos os modelos de texto</option></select></label>
      <label>Orçamento do mês (US$)<input id="mr-budget" type="number" step="0.5" min="0" value="${s.monthly_budget_usd}"></label></div>
    <div class="grid g4 mt">
      <label>Busca na web<select id="mr-web"><option value="">nenhuma</option><option value="brave">Brave Search</option><option value="tavily">Tavily</option><option value="searxng">SearXNG (próprio)</option></select></label>
      <label>Chave da busca (cofre)<input id="mr-skey" placeholder="secret://brave-search" value="${esc(s.search_key)}"></label>
      <label>Endereço do SearXNG<input id="mr-surl" placeholder="https://searx.empresa.com" value="${esc(s.search_url)}"></label>
      <label>Chave do Artificial Analysis (cofre)<input id="mr-aa" placeholder="secret://artificial-analysis" value="${esc(s.aa_key)}"></label></div>
    <div class="mute small mt">As chaves ficam no cofre (Configurações → Segredos); aqui vai só a referência secret://nome.</div>
    <div class="row mt"><button id="mr-save">Salvar</button><button id="mr-refresh" class="ghost">Atualizar fichas agora (fontes sem custo)</button></div></details>`;
  $('#mr-auto').value = s.auto;
  $('#mr-web').value = s.web;
  const fill = async () => { await modelOptions($('#mr-model'), $('#mr-conn').value); $('#mr-model').value = s.model || ''; };
  $('#mr-conn').onchange = fill;
  if (s.connection) fill();
  $('#mr-save').onclick = e => act(e.target, async () => {
    await api('/catalog/model-profiles/settings', { method: 'PUT', body: { connection: $('#mr-conn').value, model: $('#mr-model').value,
      auto: $('#mr-auto').value, monthly_budget_usd: Number($('#mr-budget').value || 0), web: $('#mr-web').value,
      search_key: $('#mr-skey').value.trim(), search_url: $('#mr-surl').value.trim(), aa_key: $('#mr-aa').value.trim() } });
    await researchCard(box, c);
  }, 'Pesquisa de modelos salva');
  $('#mr-refresh').onclick = e => act(e.target, async () => {
    const r = await api('/catalog/model-profiles/refresh', { method: 'POST' });
    toast(`${r.profiles_updated} ficha(s) atualizada(s) de ${r.models} modelo(s)`);
  });
}
