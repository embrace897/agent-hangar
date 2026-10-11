/* O dia a dia do analista (fases E a H): ferramentas da plataforma por funcionário, conferência de campos extraídos,
   esquemas de extração e lote de documentos, calculadoras certificadas, identidade visual dos entregáveis…
   Usa os helpers de app.js ($, api, esc, act, toast, when, ago, copyable, route, ME). */

/* Tipos de pedido de decisão trazidos por estas fases: render(d) → {body, btns}; bind(card, id, after). */
const DECISION_KINDS = {};
const decide = (id, body) => api(`/decisions/${id}`, { method: 'POST', body });

/* ---------- ferramentas da plataforma (Configurações do funcionário) ---------- */
async function platformToolsCard(box, e) {
  const groups = await api(`/employees/${e.slug}/platform-tools`).catch(() => null);
  if (!groups || !box) return;
  box.innerHTML = `<div class="card mt"><h2>Ferramentas da plataforma</h2>
    <div class="mute small">O que ele pode fazer com a própria plataforma. Tudo roda na central, com a alçada valendo para cada ação que muda algo fora.</div>
    ${groups.map(g => `<label class="row small mt" style="gap:8px"><input type="checkbox" class="pt-g" value="${esc(g.group)}" ${g.enabled ? 'checked' : ''} ${e.can_manage ? '' : 'disabled'}>
      <span><b>${esc(g.title)}</b> ${g.available ? '' : `<span class="pill">${window.t('indisponível na instalação')}</span>`}
      <span class="mute" data-noi18n>${esc(g.tools.join(', '))}</span></span></label>`).join('')}
    ${e.can_manage ? '<div class="row mt"><button class="ghost" id="pt-save">Salvar ferramentas</button></div>' : ''}
    ${groups.some(g => g.group === 'browser') ? `<label class="mt">Domínios que o navegador pode abrir (separados por vírgula)<input id="pt-doms" data-noi18n
      value="${esc((e.browser_domains || []).join(', '))}" ${e.can_manage ? '' : 'disabled'} placeholder="banco.com.br, gov.br"></label>
      ${e.can_manage ? '<div class="row mt"><button class="ghost" id="pt-doms-save">Salvar domínios</button></div>' : ''}` : ''}</div>`;
  const ds = $('#pt-doms-save', box);
  if (ds) ds.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/browser-domains`, { method: 'PUT',
    body: { domains: $('#pt-doms', box).value.split(',').map(x => x.trim()).filter(Boolean) } }), 'Domínios salvos');
  const save = $('#pt-save', box);
  if (save) save.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/platform-tools`, { method: 'PUT',
    body: { disabled: [...box.querySelectorAll('.pt-g')].filter(c => !c.checked).map(c => c.value) } }).then(() => platformToolsCard(box, e)), 'Ferramentas salvas');
}

/* ---------- página da tarefa: o que estas fases acrescentam ---------- */
async function taskExtras(x, id) {
  const box = document.createElement('div');
  main.append(box);
  taskConversations(id, box);  // conversas com gente de fora (fase F)
  taskGovernance(x, id, box);  // classificação, retenção legal e o caminho do navegador (fase G)
  const memo = (x.events || []).filter(e => e.kind === 'memo').pop();  // memorando de decisão (fase H)
  if (memo || x.mode === 'copilot') box.insertAdjacentHTML('afterbegin', `<div class="card mt row between">${x.mode === 'copilot' ? `<span><span class="pill info">${window.t('modo par')}</span>
    <span class="small mute">${window.t('o Digital employee orienta; quem faz é a pessoa')}</span></span>` : '<span></span>'}
    ${memo ? `<a href="#/memos/${memo.payload.id}"><button class="ghost">${window.t('Abrir o memorando de decisão')}</button></a>` : ''}</div>`);
  const ex = await api(`/tasks/${id}/extractions`).catch(() => []);
  if (ex.length) box.insertAdjacentHTML('beforeend', `<div class="card mt"><h2>Campos extraídos</h2>
    ${ex.map(x2 => `<div class="mt"><b data-noi18n>${esc(x2.file)}</b> <span class="mute small">v${x2.file_version} · ${esc(x2.schema)}</span>
      <span class="pill ${x2.status === 'review' ? 'warn' : x2.status === 'rejected' ? 'bad' : 'ok'}">${esc(window.t(EX_STATUS[x2.status] || x2.status))}</span>
      ${x2.corrections ? `<span class="mute small">${x2.corrections} ${window.t('corrigido(s) por')} <span data-noi18n>${esc(x2.reviewed_by)}</span></span>` : ''}
      <table class="small mt"><tr><th>Campo</th><th>Valor</th><th>Confiança</th><th>Origem</th></tr>
      ${Object.values(x2.values).map(v => `<tr><td>${esc(v.label || '')}</td><td data-noi18n>${esc(v.value == null ? '—' : String(v.value))}${v.corrected_by ? ' <span class="pill info">corrigido</span>' : ''}</td>
        <td>${confPill(v.confidence)}</td><td class="mute" data-noi18n>${esc((v.evidence || '').slice(0, 120))}</td></tr>`).join('')}</table></div>`).join('')}</div>`);
}
const EX_STATUS = { auto: 'direto', review: 'em conferência', reviewed: 'conferido', rejected: 'recusado' };
const confPill = c => `<span class="pill ${c >= 0.9 ? 'ok' : c >= 0.6 ? 'warn' : 'bad'}">${Math.round((c || 0) * 100)}%</span>`;

/* ---------- conferência campo a campo ---------- */
DECISION_KINDS.review_fields = {
  render(d) {
    const p = d.payload || {}, fields = p.fields || {};
    const rows = Object.entries(fields).sort((a, b) => (b[1].review ? 1 : 0) - (a[1].review ? 1 : 0));
    return {
      body: `<div class="mt"><b>Conferir campos extraídos</b> de <a href="#/tasks/${d.task ? d.task.id : ''}" data-noi18n>${esc(p.file || '')}</a>
        <span class="mute small">(${esc(p.schema || '')})</span> — <span class="small">${window.t('só os destacados estão em dúvida')}</span></div>
        <table class="small mt"><tr><th>Campo</th><th>Valor</th><th>Confiança</th><th>Trecho de origem</th></tr>
        ${rows.map(([k, v]) => `<tr class="${v.review ? 'rf-doubt' : ''}"><td>${esc(v.label || k)}${v.error ? `<div class="pill bad">${esc(window.t(v.error))}</div>` : ''}</td>
          <td><input class="rf-v" data-k="${esc(k)}" data-orig="${esc(v.value == null ? '' : String(v.value))}" value="${esc(v.value == null ? '' : String(v.value))}" data-noi18n></td>
          <td>${confPill(v.confidence)}</td><td class="mute" data-noi18n>${esc((v.evidence || '').slice(0, 200)) || '—'}</td></tr>`).join('')}</table>
        <input class="dc-reason mt" placeholder="Motivo (para recusar o documento)">`,
      btns: '<button class="rf-ok">Confirmar os valores</button><button class="ghost danger rf-no">Documento não serve</button>',
    };
  },
  bind(card, id, after) {
    $('.rf-ok', card).onclick = ev => {
      const fields = {};
      card.querySelectorAll('.rf-v').forEach(i => { if (i.value !== i.dataset.orig) fields[i.dataset.k] = i.value; });
      act(ev.target, () => decide(id, { decision: 'approve', edit: { fields } }).then(after), 'Campos conferidos');
    };
    $('.rf-no', card).onclick = ev => {
      const why = $('.dc-reason', card).value.trim();
      if (!why) return toast(window.t('Diga por que o documento não serve'), true);
      act(ev.target, () => decide(id, { decision: 'reject', reason: why }).then(after), 'Recusado');
    };
  },
};

/* ---------- esquemas de extração e lote de documentos ---------- */
const EX_TYPES = ['text', 'number', 'money', 'date', 'cpf', 'cnpj', 'email', 'bool', 'enum'];
const fieldsToText = fs => (fs || []).map(f => [f.name, f.label, f.type, f.required ? 'sim' : 'não',
  f.type === 'enum' ? (f.options || []).join(',') : (f.pattern || '')].join(' | ')).join('\n');
function textToFields(text) {
  return lines(text).map(l => {
    const [name, label, type, req, extra] = l.split('|').map(x => (x || '').trim());
    const f = { name, label: label || name, type: type || 'text', required: /^(s|sim|y|yes|true|1)$/i.test(req || '') };
    if (f.type === 'enum') f.options = (extra || '').split(',').map(x => x.trim()).filter(Boolean); else if (extra) f.pattern = extra;
    return f;
  });
}

async function extractionPage() {
  const [schemas, emps, teams] = await Promise.all([api('/extraction/schemas'), api('/employees').catch(() => []), api('/teams').catch(() => [])]);
  const working = emps.filter(e => ['probation', 'active', 'paused'].includes(e.status));
  main.innerHTML = `<h1>Extração de documentos</h1><div class="sub">Esquemas dizem que campos tirar de cada tipo de documento (contrato, nota fiscal, boleto…). O Digital employee extrai com
    a confiança e o trecho de cada campo; o que ficar em dúvida uma pessoa confere em Decisões, ao lado do trecho.</div>
    <div class="card"><h2>Esquemas (${schemas.length})</h2>${schemas.length ? `<table class="small"><tr><th>Esquema</th><th>Campos</th><th>Confiança mínima</th><th>Versão</th><th></th></tr>
      ${schemas.map(s => `<tr data-n="${esc(s.name)}"><td><b data-noi18n>${esc(s.title)}</b><div class="mute" data-noi18n>${esc(s.name)}</div></td>
        <td data-noi18n>${esc(s.fields.map(f => `${f.label} (${f.type})`).join(', '))}</td><td>${Math.round(s.min_confidence * 100)}%</td><td>v${s.version}</td>
        <td class="row" style="gap:4px"><button class="ghost small ex-edit">Editar</button><button class="ghost small danger ex-arch">Arquivar</button></td></tr>`).join('')}</table>`
      : '<div class="mute small">Nenhum esquema ainda.</div>'}</div>
    <div class="grid g2 mt"><div class="card"><h2 id="ex-h">Novo esquema</h2>
      <label>Nome<input id="ex-name" placeholder="ex.: contrato_fornecedor" data-noi18n></label><label class="mt">Título<input id="ex-title"></label>
      <label class="mt">Time<select id="ex-team"><option value="">${window.t('Empresa (admins)')}</option>${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label>
      <label class="mt">Campos — um por linha: nome | rótulo | tipo | obrigatório | opções ou regra<textarea id="ex-fields" rows="7" class="code" data-noi18n
        placeholder="contratante | Contratante | text | sim&#10;cnpj | CNPJ | cnpj | sim&#10;valor | Valor mensal | money | sim&#10;vigencia | Fim da vigência | date | não&#10;indice | Índice de reajuste | enum | não | IPCA,IGP-M"></textarea></label>
      <div class="mute small">${window.t('Tipos')}: <span data-noi18n>${EX_TYPES.join(', ')}</span></div>
      <label class="mt">Confiança mínima (abaixo disso, uma pessoa confere)<input id="ex-conf" type="number" min="0" max="100" value="80"></label>
      <div class="row mt"><button id="ex-save">Salvar esquema</button></div></div>
    <div class="card"><h2>Lote de documentos</h2><div class="mute small">Uma tarefa por arquivo e uma de consolidação, que espera todas e gera a planilha com tudo.</div>
      <label class="mt">Digital employee<select id="bt-emp">${working.map(e => `<option value="${esc(e.slug)}">${esc(e.name)}</option>`).join('')}</select></label>
      <label class="mt">Esquema<select id="bt-schema">${schemas.map(s => `<option value="${esc(s.name)}">${esc(s.title)}</option>`).join('')}</select></label>
      <label class="mt">Título do lote<input id="bt-title" placeholder="ex.: Notas fiscais de outubro"></label>
      <label class="mt">Arquivos<input id="bt-files" type="file" multiple></label>
      <div class="row mt"><button id="bt-go" ${working.length && schemas.length ? '' : 'disabled'}>Enviar o lote</button></div><div id="bt-out"></div></div></div>`;
  const fill = s => { $('#ex-h').textContent = window.t('Editar esquema'); $('#ex-name').value = s.name; $('#ex-title').value = s.title;
    $('#ex-fields').value = fieldsToText(s.fields); $('#ex-conf').value = Math.round(s.min_confidence * 100); $('#ex-name').scrollIntoView(); };
  main.querySelectorAll('tr[data-n]').forEach(tr => {
    const s = schemas.find(x => x.name === tr.dataset.n);
    $('.ex-edit', tr).onclick = () => fill(s);
    $('.ex-arch', tr).onclick = ev => confirm(window.t('Arquivar este esquema?')) && act(ev.target, () => api('/extraction/schemas/' + s.name, { method: 'DELETE' }).then(extractionPage));
  });
  $('#ex-save').onclick = ev => act(ev.target, () => api('/extraction/schemas', { method: 'PUT', body: { name: $('#ex-name').value.trim(),
    title: $('#ex-title').value.trim(), team: $('#ex-team').value, fields: textToFields($('#ex-fields').value),
    min_confidence: Number($('#ex-conf').value) / 100 } }).then(extractionPage), 'Esquema salvo');
  $('#bt-go').onclick = ev => {
    const files = [...$('#bt-files').files];
    if (!files.length) return toast(window.t('Escolha os arquivos'), true);
    act(ev.target, async () => {
      const enc = await Promise.all(files.map(f => new Promise((ok, bad) => { const r = new FileReader();
        r.onload = () => ok({ name: f.name, content_type: f.type, content_base64: String(r.result).split(',')[1] || '' }); r.onerror = bad; r.readAsDataURL(f); })));
      const r = await api(`/employees/${$('#bt-emp').value}/batch`, { method: 'POST', body: { title: $('#bt-title').value.trim() || window.t('Lote'),
        schema: $('#bt-schema').value, files: enc } });
      $('#bt-out').innerHTML = `<div class="card mt ok-card">${r.tasks.length} ${window.t('tarefa(s) criada(s)')}. ${window.t('Consolidação')}: <a href="#/tasks/${r.parent}">#${r.parent}</a></div>`;
    }, 'Lote enviado');
  };
}

/* ---------- calculadoras certificadas ---------- */
const calcStatus = c => c.status === 'certified' ? `<span class="pill ok">${window.t('certificada')} v${c.certified.version}</span>${c.pending_changes ? ` <span class="pill warn">v${c.version} ${window.t('aguardando certificação')}</span>` : ''}`
  : c.status === 'deprecated' ? `<span class="pill">${window.t('descontinuada')}</span>` : `<span class="pill warn">${window.t('rascunho')}</span>`;

async function calculatorsPage() {
  const list = await api('/calculators');
  main.innerHTML = `<div class="row between"><div><h1>Calculadoras</h1><div class="sub">O número que precisa estar certo sai de uma fórmula versionada, testada e certificada por outra pessoa —
    nunca da “cabeça” do modelo. Cada conta guarda a memória de cálculo.</div></div><a href="#/calculators/_nova"><button>+ Nova calculadora</button></a></div>
    <div class="card">${list.length ? `<table class="small"><tr><th>Calculadora</th><th>Status</th><th>Testes</th><th>Revisão humana</th></tr>
      ${list.map(c => `<tr><td><a href="#/calculators/${esc(c.name)}"><b data-noi18n>${esc(c.title)}</b></a><div class="mute" data-noi18n>${esc(c.description.slice(0, 120))}</div></td>
        <td>${calcStatus(c)}</td><td>${c.last_test ? `${c.last_test.passed}/${c.last_test.passed + c.last_test.failed}` : '—'}</td><td>${c.requires_review ? window.t('exige') : '—'}</td></tr>`).join('')}</table>`
      : '<div class="mute small">Nenhuma calculadora ainda.</div>'}</div>`;
}

async function calculatorPage(name) {
  const isNew = name === '_nova';
  const [c, teams] = await Promise.all([isNew ? Promise.resolve(null) : api('/calculators/' + encodeURIComponent(name)), api('/teams').catch(() => [])]);
  const x = c || { name: '', title: '', description: '', inputs: [], steps: [], outputs: [], cases: [], requires_review: false, can_edit: true };
  main.innerHTML = `<a href="#/calculators" class="mute small">← Calculadoras</a>
    <h1>${isNew ? 'Nova calculadora' : `<span data-noi18n>${esc(x.title)}</span>`} ${c ? calcStatus(c) : ''}</h1>
    ${c && c.certified ? `<div class="sub">${window.t('Certificada por')} <span data-noi18n>${esc(c.certified.by)}</span> · ${when(c.certified.at)} · ${window.t('editada por último por')} <span data-noi18n>${esc(c.edited_by)}</span></div>` : ''}
    <div class="grid g2"><div class="card"><h2>Definição</h2>
      <label>Nome<input id="cc-name" value="${esc(x.name)}" ${isNew ? '' : 'disabled'} data-noi18n placeholder="ex.: multa_rescisoria"></label>
      <label class="mt">Título<input id="cc-title" value="${esc(x.title)}"></label><label class="mt">Descrição<input id="cc-desc" value="${esc(x.description)}"></label>
      ${isNew ? `<label class="mt">Time<select id="cc-team"><option value="">${window.t('Empresa (admins)')}</option>${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label>` : ''}
      <label class="mt">Entradas — uma por linha: nome | rótulo | unidade<textarea id="cc-in" rows="4" class="code" data-noi18n>${esc(x.inputs.map(i => [i.name, i.label, i.unit].join(' | ')).join('\n'))}</textarea></label>
      <label class="mt">Passos — um por linha: nome = fórmula<textarea id="cc-steps" rows="6" class="code" data-noi18n placeholder="saldo = valor_mensal * meses_restantes&#10;multa = round(pct(saldo, percentual), 2)">${esc(x.steps.map(s => `${s.name} = ${s.expr}`).join('\n'))}</textarea></label>
      <div class="mute small">${window.t('Funções')}: <span data-noi18n>round(x, casas), pct(x, p), min, max, abs, sum, len, floor, ceil, days_between(a, b), months_between(a, b); a if cond else b</span></div>
      <label class="mt">Saídas (separadas por vírgula)<input id="cc-out" value="${esc(x.outputs.join(', '))}" data-noi18n></label>
      <label class="mt">Casos de teste (JSON)<textarea id="cc-cases" rows="6" class="code" data-noi18n>${esc(JSON.stringify(x.cases, null, 2))}</textarea></label>
      <label class="row small mt" style="gap:6px"><input type="checkbox" id="cc-rev" ${x.requires_review ? 'checked' : ''}> <span>A tarefa que usar esta calculadora passa por revisão humana</span></label>
      ${x.can_edit ? `<div class="row mt"><button id="cc-save">Salvar</button>${c ? `<button class="ghost" id="cc-cert">Certificar v${c.version}</button><button class="ghost danger" id="cc-dep">Descontinuar</button>` : ''}</div>` : ''}
      <div class="mute small mt">${window.t('Quatro olhos: quem editou por último não certifica; todos os casos precisam passar.')}</div></div>
    <div class="card"><h2>Testes e simulação</h2><div id="cc-tests">${c && c.last_test ? testsView(c.last_test) : '<div class="mute small">Salve para rodar os casos.</div>'}</div>
      ${c ? `<h3 class="mt">Simular</h3><textarea id="cc-try" rows="4" class="code" data-noi18n>${esc(JSON.stringify(Object.fromEntries(x.inputs.map(i => [i.name, 0])), null, 2))}</textarea>
        <div class="row mt"><button class="ghost" id="cc-run">Calcular</button></div><div id="cc-sim"></div>` : ''}
      ${c && c.runs && c.runs.length ? `<h3 class="mt">Contas feitas</h3><table class="small"><tr><th>#</th><th>Versão</th><th>Tarefa</th><th>Saídas</th><th>Quando</th></tr>
        ${c.runs.map(r => `<tr><td>${r.id}</td><td>v${r.version}</td><td>${r.task ? `<a href="#/tasks/${r.task}">#${r.task}</a>` : '—'}</td><td data-noi18n>${esc(JSON.stringify(r.outputs))}</td><td>${ago(r.at)}</td></tr>`).join('')}</table>` : ''}</div></div>`;
  const body = () => {
    let cases;
    try { cases = JSON.parse($('#cc-cases').value || '[]'); } catch { throw new Error(window.t('Casos de teste: JSON inválido')); }
    return { name: $('#cc-name').value.trim(), title: $('#cc-title').value.trim(), description: $('#cc-desc').value.trim(),
      team: $('#cc-team') ? $('#cc-team').value : '',
      inputs: lines($('#cc-in').value).map(l => { const [n, label, unit] = l.split('|').map(s => (s || '').trim()); return { name: n, label: label || n, unit }; }),
      steps: lines($('#cc-steps').value).map(l => { const i = l.indexOf('='); return { name: l.slice(0, i).trim(), expr: l.slice(i + 1).trim() }; }),
      outputs: $('#cc-out').value.split(',').map(s => s.trim()).filter(Boolean), cases, requires_review: $('#cc-rev').checked };
  };
  const save = $('#cc-save');
  if (save) save.onclick = ev => act(ev.target, async () => { const r = await api('/calculators', { method: 'PUT', body: body() }); location.hash = '#/calculators/' + r.name; return route(); }, 'Calculadora salva');
  const cert = $('#cc-cert');
  if (cert) cert.onclick = ev => act(ev.target, () => api(`/calculators/${c.name}/certify`, { method: 'POST' }).then(route), 'Certificada');
  const dep = $('#cc-dep');
  if (dep) dep.onclick = ev => confirm(window.t('Descontinuar? Os Digital employees deixam de usá-la.')) && act(ev.target, () => api(`/calculators/${c.name}/deprecate`, { method: 'POST' }).then(route));
  const run = $('#cc-run');
  if (run) run.onclick = ev => act(ev.target, async () => {
    let inputs; try { inputs = JSON.parse($('#cc-try').value); } catch { throw new Error(window.t('Entradas: JSON inválido')); }
    const r = await api(`/calculators/${c.name}/test`, { method: 'POST', body: { inputs } });
    $('#cc-sim').innerHTML = r.error ? `<div class="pill bad mt" data-noi18n>${esc(r.error)}</div>` : `<table class="small mt"><tr><th>Passo</th><th>Fórmula</th><th>Valor</th></tr>
      ${r.trace.map(s => `<tr><td data-noi18n>${esc(s.name)}</td><td><code class="inline" data-noi18n>${esc(s.expr)}</code></td><td data-noi18n><b>${esc(JSON.stringify(s.value))}</b></td></tr>`).join('')}</table>`;
  });
}
function testsView(t) {
  return `<div class="small">${t.failed ? `<span class="pill bad">${t.failed} ${window.t('falhando')}</span>` : `<span class="pill ok">${window.t('todos passando')}</span>`} v${t.version}</div>
    <table class="small mt"><tr><th>Caso</th><th>Resultado</th></tr>${(t.results || []).map(r => `<tr><td data-noi18n>${esc(r.name)}</td>
      <td>${r.passed ? '<span class="pill ok">ok</span>' : `<span class="pill bad">${window.t('falhou')}</span> <span class="mute" data-noi18n>${esc(r.error || JSON.stringify(r.diff))}</span>`}</td></tr>`).join('')}</table>`;
}

/* ---------- identidade visual dos entregáveis (console → Digital employees → Entregáveis) ---------- */
async function wfBrand(t) {
  const b = await api('/brand');
  t.innerHTML = `<div class="card"><h2>Identidade visual dos entregáveis</h2>
    <div class="mute small">Apresentações, documentos e dashboards que os Digital employees geram saem com o tema, a cor e o rodapé da empresa.</div>
    <div class="small mt">Data Studio: ${b.data_studio ? '<span class="pill ok">no ar</span>' : `<span class="pill warn">${window.t('desligado')}</span> <span class="mute">docker compose --profile data-studio up -d</span>`}</div>
    <div class="grid g2 mt"><label>Tema<select id="br-theme">${['light', 'dark', 'corporate'].map(x => `<option ${x === b.theme ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
      <label>Cor de destaque<input id="br-accent" type="color" value="${esc(b.accent || '#0B5FFF')}"></label>
      <label style="grid-column:1/-1">Rodapé<input id="br-footer" value="${esc(b.footer || '')}" placeholder="ex.: Acme · uso interno"></label></div>
    <div class="row mt"><button id="br-save">Salvar</button></div></div>`;
  $('#br-save').onclick = ev => act(ev.target, () => api('/brand', { method: 'PUT', body: { theme: $('#br-theme').value, accent: $('#br-accent').value,
    footer: $('#br-footer').value } }).then(() => wfBrand(t)), 'Identidade salva');
}

/* ================================================================== fase F: processos, filas, gente de fora, reuniões */
const WI_STATUS = { waiting: ['esperando etapa anterior', ''], open: ['na fila', 'warn'], in_progress: ['em andamento', 'info'],
  employee: ['com o Digital employee', 'info'], review: ['aguardando aprovação', 'warn'], done: ['concluído', 'ok'], cancelled: ['cancelado', ''] };
const wiPill = s => { const [t, c] = WI_STATUS[s] || [s, '']; return `<span class="pill ${c}">${esc(window.t(t))}</span>`; };
const HANDLED = { employee: 'automático', assisted: 'assistido', human: 'humano' };
const dtLocal = v => v ? new Date(v).toLocaleString(window.LOCALE || 'pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '—';

function itemRow(i) {
  return `<tr><td><a href="#/work/${i.id}"><b data-noi18n>#${i.id} ${esc(i.title)}</b></a><div class="mute small" data-noi18n>${esc(i.queue_name || '')}${i.category ? ' · ' + esc(i.category) : ''}</div></td>
    <td>${wiPill(i.status)}${i.sla_breached ? ' <span class="pill bad">SLA</span>' : ''}${i.late ? ` <span class="pill bad">${window.t('atrasado')}</span>` : ''}</td>
    <td data-noi18n>${esc(i.assignee || '—')}</td><td class="small">${i.sla_due_at ? dtLocal(i.sla_due_at) : i.due_at ? dtLocal(i.due_at) : '—'}</td></tr>`;
}
const itemsTable = list => list.length ? `<table class="small"><tr><th>Item</th><th>Status</th><th>Com</th><th>Prazo</th></tr>${list.map(itemRow).join('')}</table>`
  : '<div class="mute small">Nada aqui.</div>';

async function myWorkPage() {
  const [mine, open] = await Promise.all([api('/work-items?mine=true&status=open'), api('/work-items?status=open')]);
  const unassigned = open.filter(i => i.status === 'open' && !i.assignee);
  main.innerHTML = `<h1>Meu trabalho</h1><div class="sub">Os itens com você — de filas, playbooks, reuniões ou o que um Digital employee preparou para você terminar.</div>
    <div class="card"><h2>Comigo (${mine.length})</h2>${itemsTable(mine)}</div>
    <div class="card mt"><h2>Nos meus times, sem dono (${unassigned.length})</h2>${itemsTable(unassigned)}</div>`;
}

async function workItemPage(id) {
  const i = await api('/work-items/' + id);
  const can = ['open', 'in_progress', 'review', 'waiting'].includes(i.status);
  main.innerHTML = `<a href="javascript:history.back()" class="mute small">← voltar</a>
    <h1 data-noi18n>#${i.id} ${esc(i.title)}</h1>
    <div class="sub">${wiPill(i.status)} ${i.queue ? `<a href="#/queues/${esc(i.queue)}" data-noi18n>${esc(i.queue_name)}</a>` : ''} ${i.category ? `· <span data-noi18n>${esc(i.category)}</span>` : ''}
      ${i.handled_by ? `· <span class="chip">${esc(window.t(HANDLED[i.handled_by] || i.handled_by))}</span>` : ''}
      ${i.employee_task ? ` · <a href="#/tasks/${i.employee_task}">${window.t('tarefa do Digital employee')} #${i.employee_task}</a>` : ''}
      ${i.playbook_run ? ` · <a href="#/runs/${i.playbook_run}">${window.t('execução do playbook')}</a>` : ''}</div>
    <div class="grid g2"><div class="card"><h2>Pedido</h2><dl class="kv"><dt>Com</dt><dd data-noi18n>${esc(i.assignee || '—')}</dd>
        <dt>Pedido por</dt><dd data-noi18n>${esc(i.requester || '—')}</dd><dt>Prazo</dt><dd>${dtLocal(i.due_at)}</dd>
        <dt>SLA</dt><dd>${dtLocal(i.sla_due_at)}${i.sla_breached ? ' <span class="pill bad">estourado</span>' : ''}</dd>
        <dt>Evidência</dt><dd>${esc(window.t({ none: 'nenhuma', file: 'arquivo', approval: 'aprovação de outra pessoa' }[i.evidence] || i.evidence))}</dd></dl>
        <div class="md mt" data-noi18n>${mdLite(i.body || '')}</div></div>
      <div class="card">${i.prepared ? `<h2>O que o Digital employee deixou pronto</h2><div class="md" data-noi18n>${mdLite(i.prepared)}</div>` : '<h2>Resultado</h2>'}
        ${i.result ? `<h3 class="mt">Resultado</h3><div class="md" data-noi18n>${mdLite(i.result)}</div>` : ''}
        ${i.approved_by ? `<div class="small mt">${window.t('Aprovado por')} <span data-noi18n>${esc(i.approved_by)}</span></div>` : ''}</div></div>
    <div class="card mt"><h2>Arquivos (${i.files.length})</h2>${i.files.map(f => `<div class="li small"><a href="/api/work-item-files/${f.id}" data-noi18n>${esc(f.name)}</a>
        <span class="mute" data-noi18n>${esc(f.by)} · ${ago(f.at)}</span></div>`).join('') || '<div class="mute small">Nenhum.</div>'}
      ${can ? '<div class="row mt" style="gap:6px"><input type="file" id="wi-file"><button class="ghost" id="wi-up">Anexar</button></div>' : ''}</div>
    ${can ? `<div class="card mt"><h2>Ação</h2><textarea id="wi-note" rows="3" placeholder="Resultado ou observação"></textarea>
      <div class="row mt" style="gap:6px">${i.status === 'open' ? '<button class="wi-a" data-a="start">Pegar</button>' : ''}
        ${['open', 'in_progress'].includes(i.status) ? '<button class="wi-a" data-a="done">Concluir</button><button class="ghost wi-a" data-a="release">Devolver para a fila</button>' : ''}
        ${i.status === 'review' ? '<button class="wi-a" data-a="approve">Aprovar</button><button class="ghost wi-a" data-a="reject">Pedir ajuste</button>' : ''}
        <input id="wi-to" placeholder="e-mail para atribuir" style="max-width:240px" data-noi18n><button class="ghost wi-a" data-a="assign">Atribuir</button>
        <button class="ghost danger wi-a" data-a="cancel">Cancelar</button></div></div>`
      : (['done', 'cancelled'].includes(i.status) ? '<div class="row mt"><button class="ghost wi-a" data-a="reopen">Reabrir</button></div>' : '')}`;
  if (['in_progress', 'review'].includes(i.status) || (i.status === 'done' && i.result)) itemReviewBox(id);  // modo par (fase H)
  main.querySelectorAll('.wi-a').forEach(b => b.onclick = ev => act(ev.target, () => api('/work-items/' + id, { method: 'POST',
    body: { action: b.dataset.a, note: ($('#wi-note') || {}).value || '', assignee: ($('#wi-to') || {}).value || '' } }).then(() => workItemPage(id)), 'Feito'));
  const up = $('#wi-up');
  if (up) up.onclick = ev => { const f = $('#wi-file').files[0]; if (!f) return toast(window.t('Escolha um arquivo'), true);
    act(ev.target, async () => { const r = await fetch(`/api/work-items/${id}/files?name=${encodeURIComponent(f.name)}`, { method: 'POST', body: f,
      credentials: 'same-origin', headers: { 'Content-Type': f.type || 'application/octet-stream', 'X-CSRF-Token': cookieVal('hangar_csrf') } });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText); return workItemPage(id); }, 'Arquivo anexado'); };
}

/* ---------- filas ---------- */
const catToLine = c => [c.name, (c.keywords || []).join(','), c.sla_hours == null ? '' : c.sla_hours,
  c.route.type === 'employee' ? 'employee:' + c.route.refs.join(',') : 'people'].join(' | ');
function lineToCat(l) {
  const [name, kw, sla, dest] = l.split('|').map(x => (x || '').trim());
  const route = (dest || 'people').startsWith('employee:') ? { type: 'employee', refs: dest.slice(9).split(',').map(x => x.trim()).filter(Boolean) } : { type: 'people' };
  return { name, keywords: (kw || '').split(',').map(x => x.trim()).filter(Boolean), sla_hours: sla ? Number(sla) : null, route };
}

async function queuesPage() {
  const [qs, teams] = await Promise.all([api('/queues'), api('/teams').catch(() => [])]);
  const mt = teams.filter(t => ME.is_admin || t.my_role === 'maintainer');
  main.innerHTML = `<h1>Filas</h1><div class="sub">A entrada única de pedidos de cada time: categoria por palavras-chave, SLA e destino — um Digital employee (o menos ocupado da lista) ou as pessoas, que pegam a próxima.</div>
    <div class="grid g3">${qs.map(q => `<a class="card" href="#/queues/${esc(q.slug)}" style="text-decoration:none"><h2 data-noi18n>${esc(q.name)}</h2>
      <div class="mute small" data-noi18n>${esc(q.team_name)}</div><div class="row mt" style="gap:6px;flex-wrap:wrap">
      <span class="pill warn">${q.stats.open} ${window.t('abertos')}</span>${q.stats.breached ? `<span class="pill bad">${q.stats.breached} SLA</span>` : ''}
      <span class="pill">${q.stats.done_30d} ${window.t('em 30 dias')}</span></div>
      <div class="small mt">${window.t('automático')} ${q.stats.automatic} · ${window.t('assistido')} ${q.stats.assisted} · ${window.t('humano')} ${q.stats.human}</div></a>`).join('')
      || '<div class="card mute small">Nenhuma fila ainda.</div>'}</div>
    ${mt.length ? `<div class="card mt"><h2>Nova fila</h2><div class="grid g2"><label>Nome<input id="q-name"></label>
      <label>Time<select id="q-team">${mt.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label></div>
      <label class="mt">Categorias — uma por linha: nome | palavras-chave | SLA em horas | destino (people ou employee:slug1,slug2)
        <textarea id="q-cats" rows="4" class="code" data-noi18n placeholder="Nota fiscal | nota,nf,boleto | 8 | employee:analista-fiscal&#10;Cadastro | cadastro,alteração | 24 | people"></textarea></label>
      <div class="row mt"><button id="q-save">Criar fila</button></div></div>` : ''}`;
  const save = $('#q-save');
  if (save) save.onclick = ev => act(ev.target, () => api('/queues', { method: 'PUT', body: { name: $('#q-name').value.trim(), team: $('#q-team').value,
    categories: lines($('#q-cats').value).map(lineToCat) } }).then(q => { location.hash = '#/queues/' + q.slug; }), 'Fila criada');
}

async function queuePage(slug) {
  const [q, items] = await Promise.all([api('/queues').then(l => l.find(x => x.slug === slug)), api(`/work-items?queue=${encodeURIComponent(slug)}`)]);
  if (!q) throw new Error(window.t('fila não encontrada'));
  const col = (title, st) => { const l = items.filter(i => st.includes(i.status)); return `<div class="card"><h3>${window.t(title)} (${l.length})</h3>
    ${l.slice(0, 30).map(i => `<a class="li small" href="#/work/${i.id}" style="display:block" data-noi18n><b>#${i.id}</b> ${esc(i.title)}
      <div class="mute">${esc(i.category || '')}${i.assignee ? ' · ' + esc(i.assignee) : ''}${i.sla_breached ? ' · SLA!' : ''}</div></a>`).join('') || '<div class="mute small">—</div>'}</div>`; };
  main.innerHTML = `<a href="#/queues" class="mute small">← Filas</a><div class="row between"><h1 data-noi18n>${esc(q.name)}</h1>
    <div class="row" style="gap:6px"><button id="q-next">Pegar a próxima</button></div></div>
    <div class="sub"><span data-noi18n>${esc(q.team_name)}</span> · ${q.stats.open} ${window.t('abertos')} · ${q.stats.breached} ${window.t('com SLA estourado')} ·
      ${window.t('tempo médio')}: ${q.stats.avg_hours == null ? '—' : q.stats.avg_hours + ' h'}</div>
    <div class="grid g4">${col('Na fila', ['open'])}${col('Em andamento', ['in_progress', 'review'])}${col('Com o Digital employee', ['employee'])}${col('Concluídos', ['done'])}</div>
    <div class="grid g2 mt"><div class="card"><h2>Novo pedido</h2><label>Título<input id="qi-title"></label>
      <label class="mt">Detalhes<textarea id="qi-body" rows="3"></textarea></label><div class="row mt"><button id="qi-go">Enviar</button></div></div>
      <div class="card"><h2>Categorias</h2><table class="small"><tr><th>Categoria</th><th>Palavras-chave</th><th>SLA</th><th>Destino</th></tr>
        ${q.categories.map(c => `<tr><td data-noi18n>${esc(c.name)}</td><td data-noi18n>${esc((c.keywords || []).join(', '))}</td><td>${c.sla_hours == null ? '—' : c.sla_hours + ' h'}</td>
          <td data-noi18n>${c.route.type === 'employee' ? esc(c.route.refs.join(', ')) : window.t('pessoas do time')}</td></tr>`).join('')}</table>
        ${q.can_manage ? `<label class="mt">Editar — uma por linha: nome | palavras-chave | SLA | destino<textarea id="qc-edit" rows="4" class="code" data-noi18n>${esc(q.categories.map(catToLine).join('\n'))}</textarea></label>
          <div class="row mt" style="gap:6px"><button class="ghost" id="qc-save">Salvar categorias</button><button class="ghost" id="q-tok">${q.intake.enabled ? 'Trocar o token de entrada' : 'Ligar a entrada por webhook'}</button></div>
          <div class="small mt">Webhook: <code class="inline" data-noi18n>POST ${esc(location.origin + q.intake.url)}</code>${q.intake.enabled ? ` · token …${esc(q.intake.hint)}` : ''}</div><div id="q-tok-out"></div>` : ''}</div></div>`;
  $('#q-next').onclick = ev => act(ev.target, () => api(`/queues/${slug}/next`, { method: 'POST' }).then(i => { location.hash = '#/work/' + i.id; }));
  $('#qi-go').onclick = ev => act(ev.target, () => api(`/queues/${slug}/items`, { method: 'POST', body: { title: $('#qi-title').value.trim(), body: $('#qi-body').value } })
    .then(() => queuePage(slug)), 'Pedido enviado');
  const cs = $('#qc-save');
  if (cs) cs.onclick = ev => act(ev.target, () => api('/queues', { method: 'PUT', body: { name: q.name, team: q.team, slug, categories: lines($('#qc-edit').value).map(lineToCat) } })
    .then(() => queuePage(slug)), 'Categorias salvas');
  const tk = $('#q-tok');
  if (tk) tk.onclick = ev => act(ev.target, async () => { const r = await api(`/queues/${slug}/intake-token`, { method: 'POST', body: { enabled: true } });
    $('#q-tok-out').innerHTML = `<div class="card warn-card mt"><b>${window.t('Copie agora: o token não aparece de novo.')}</b><div class="mt">${copyable(r.token)}</div></div>`; });
}

/* ---------- playbooks ---------- */
const stepToLine = s => [s.key, s.title, `${s.assignee.type}:${s.assignee.ref}`, (s.depends_on || []).join(','),
  `${s.due.from}+${s.due.business_days}`, s.evidence, s.criteria || ''].join(' | ');
function lineToStep(l) {
  const [key, title, who, deps, due, evidence, criteria] = l.split('|').map(x => (x || '').trim());
  const [type, ...ref] = (who || '').split(':');
  const [from, n] = (due || 'start+0').split('+');
  return { key, title, assignee: { type, ref: ref.join(':') }, depends_on: (deps || '').split(',').map(x => x.trim()).filter(Boolean),
    due: { from: from || 'start', business_days: Number(n || 0) }, evidence: evidence || 'none', criteria };
}

async function playbooksPage() {
  const [list, teams] = await Promise.all([api('/playbooks'), api('/teams').catch(() => [])]);
  main.innerHTML = `<div class="row between"><div><h1>Playbooks</h1><div class="sub">Processos recorrentes — fechamento, folha, onboarding de cliente, auditoria — com etapas de pessoas e de
    Digital employees, dependências, prazos em dias úteis e evidência. Cada execução cria as tarefas e acompanha o atraso.</div></div><a href="#/playbooks/_novo"><button>+ Novo playbook</button></a></div>
    <div class="card">${list.length ? `<table class="small"><tr><th>Playbook</th><th>Etapas</th><th>Agenda</th><th>Última execução</th></tr>
      ${list.map(p => `<tr><td><a href="#/playbooks/${esc(p.name)}"><b data-noi18n>${esc(p.title)}</b></a><div class="mute" data-noi18n>${esc(p.team || '')}</div></td>
        <td>${p.steps.length}</td><td>${esc(p.schedule_text || '—')}${p.next_run_at ? `<div class="mute">${window.t('próxima')}: ${dtLocal(p.next_run_at)}</div>` : ''}</td>
        <td>${p.runs[0] ? `<a href="#/runs/${p.runs[0].id}" data-noi18n>${esc(p.runs[0].title)}</a> <span class="pill">${esc(window.t(p.runs[0].status))}</span>` : '—'}</td></tr>`).join('')}</table>`
      : '<div class="mute small">Nenhum playbook ainda.</div>'}</div>`;
  void teams;
}

async function playbookPage(name) {
  const isNew = name === '_novo';
  const [p, teams] = await Promise.all([isNew ? Promise.resolve(null) : api('/playbooks/' + encodeURIComponent(name)), api('/teams').catch(() => [])]);
  const x = p || { name: '', title: '', description: '', team: '', steps: [], schedule: '', run_title: '', runs: [], history: [], can_edit: true };
  main.innerHTML = `<a href="#/playbooks" class="mute small">← Playbooks</a><h1>${isNew ? 'Novo playbook' : `<span data-noi18n>${esc(x.title)}</span>`}</h1>
    <div class="grid g2"><div class="card"><h2>Definição</h2>
      <div class="grid g2"><label>Nome<input id="pb-name" value="${esc(x.name)}" ${isNew ? '' : 'disabled'} data-noi18n placeholder="ex.: fechamento_contabil"></label>
        <label>Time<select id="pb-team" ${isNew ? '' : 'disabled'}>${teams.map(t => `<option value="${esc(t.slug)}" ${t.slug === x.team ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></label></div>
      <label class="mt">Título<input id="pb-title" value="${esc(x.title)}"></label>
      <label class="mt">Etapas — uma por linha: chave | título | quem (employee:slug, person:e-mail, queue:fila) | depende de | prazo (start+N ou etapa+N dias úteis) | evidência (none, file, approval) | critério de pronto
        <textarea id="pb-steps" rows="7" class="code" data-noi18n placeholder="extrair | Extrair os saldos | employee:analista-contabil |  | start+1 | none | planilha com os saldos&#10;conferir | Conferir | person:ana@acme.com | extrair | extrair+1 | file |">${esc(x.steps.map(stepToLine).join('\n'))}</textarea></label>
      <div class="grid g2 mt"><label>Agenda (opcional)<input id="pb-sched" value="${esc(x.schedule)}" placeholder="@bd 1 08:00 · @bdaily 09:00 · 0 9 * * 1" data-noi18n></label>
        <label>Título de cada execução<input id="pb-rt" value="${esc(x.run_title)}" placeholder="Fechamento {mes}/{ano}" data-noi18n></label></div>
      ${x.can_edit ? '<div class="row mt"><button id="pb-save">Salvar</button></div>' : ''}</div>
    ${p ? `<div class="card"><h2>Executar</h2><div class="grid g2"><label>Marco (dia zero)<input id="pb-anchor" type="date"></label><label>Título<input id="pb-rtitle" placeholder="${esc(window.t('padrão do playbook'))}"></label></div>
      <div class="row mt"><button id="pb-run">Abrir uma execução</button></div>
      <h3 class="mt">Execuções</h3>${x.runs.map(r => `<div class="li small"><a href="#/runs/${r.id}" data-noi18n>${esc(r.title)}</a> <span class="pill">${esc(window.t(r.status))}</span></div>`).join('') || '<div class="mute small">—</div>'}
      <h3 class="mt">Onde costuma atrasar</h3><table class="small"><tr><th>Etapa</th><th>Atrasou</th></tr>${x.history.map(h => `<tr><td data-noi18n>${esc(h.title)}</td><td>${h.late}/${h.runs}</td></tr>`).join('')}</table></div>` : ''}</div>`;
  const save = $('#pb-save');
  if (save) save.onclick = ev => act(ev.target, async () => {
    const r = await api('/playbooks', { method: 'PUT', body: { name: $('#pb-name').value.trim(), team: $('#pb-team').value, title: $('#pb-title').value.trim(),
      steps: lines($('#pb-steps').value).map(lineToStep), schedule: $('#pb-sched').value.trim(), run_title: $('#pb-rt').value.trim() } });
    location.hash = '#/playbooks/' + r.name; return route(); }, 'Playbook salvo');
  const run = $('#pb-run');
  if (run) run.onclick = ev => act(ev.target, () => api(`/playbooks/${p.name}/runs`, { method: 'POST', body: { anchor: $('#pb-anchor').value, title: $('#pb-rtitle').value.trim() } })
    .then(r => { location.hash = '#/runs/' + r.id; }), 'Execução aberta');
}

async function runPage(id) {
  const r = await api('/playbook-runs/' + id);
  const ST = { waiting: 'esperando', open: 'a fazer', in_progress: 'em andamento', done: 'concluída', failed: 'falhou', cancelled: 'cancelada' };
  main.innerHTML = `<a href="#/playbooks/${esc(r.playbook)}" class="mute small">← <span data-noi18n>${esc(r.playbook_title)}</span></a>
    <h1 data-noi18n>${esc(r.title)}</h1><div class="sub">${r.done}/${r.steps.length} ${window.t('etapas concluídas')} · ${r.late ? `<span class="pill bad">${r.late} ${window.t('com atraso')}</span>` : `<span class="pill ok">${window.t('no prazo')}</span>`}
      · ${window.t('marco')} ${esc(r.anchor)}</div>
    <div class="card"><table class="small"><tr><th>Etapa</th><th>Com</th><th>Depende de</th><th>Prazo</th><th>Status</th></tr>
      ${r.steps.map(s => `<tr><td><a href="${esc(s.link)}" data-noi18n><b>${esc(s.title)}</b></a>${s.evidence !== 'none' ? ` <span class="chip">${esc(window.t(s.evidence === 'file' ? 'evidência: arquivo' : 'evidência: aprovação'))}</span>` : ''}</td>
        <td data-noi18n>${esc(s.owner || '—')}</td><td data-noi18n>${esc(s.depends_on.join(', ') || '—')}</td><td>${dtLocal(s.due)}</td>
        <td><span class="pill ${s.status === 'done' ? 'ok' : s.status === 'failed' ? 'bad' : s.status === 'waiting' ? '' : 'info'}">${esc(window.t(ST[s.status] || s.status))}</span>${s.late ? ` <span class="pill bad">${window.t('atrasada')}</span>` : ''}</td></tr>`).join('')}</table></div>`;
}

/* ---------- modelos de mensagem ---------- */
async function templatesMsgPage() {
  const [list, teams] = await Promise.all([api('/message-templates'), api('/teams').catch(() => [])]);
  main.innerHTML = `<h1>Modelos de mensagem</h1><div class="sub">Textos para gente de fora (e-mail ou WhatsApp), com variáveis {{assim}}. Aprovados uma vez por quem mantém o time,
    podem sair só com aviso — se a alçada do funcionário (e o piso da empresa) permitirem; texto livre continua pedindo aprovação.</div>
    <div class="card">${list.length ? `<table class="small"><tr><th>Modelo</th><th>Canal</th><th>Variáveis</th><th>Status</th><th></th></tr>
      ${list.map(m => `<tr data-n="${esc(m.name)}"><td><b data-noi18n>${esc(m.name)}</b><div class="mute one-line" data-noi18n style="max-width:420px">${esc(m.subject ? m.subject + ' — ' : '')}${esc(m.body)}</div></td>
        <td>${esc(m.channel)}</td><td data-noi18n>${esc(m.vars.join(', '))}</td>
        <td>${m.status === 'approved' ? `<span class="pill ok">${window.t('aprovado')}</span> <span class="mute" data-noi18n>${esc(m.approved_by)}</span>` : `<span class="pill warn">${window.t('rascunho')}</span>`}</td>
        <td class="row" style="gap:4px"><button class="ghost small mt-edit">Editar</button>${m.status !== 'approved' ? '<button class="ghost small mt-ok">Aprovar</button>' : ''}</td></tr>`).join('')}</table>`
      : '<div class="mute small">Nenhum modelo ainda.</div>'}</div>
    <div class="card mt"><h2 id="mt-h">Novo modelo</h2><div class="grid g2"><label>Nome<input id="mt-name" data-noi18n placeholder="ex.: cobranca_documento"></label>
      <label>Canal<select id="mt-ch"><option value="any">e-mail ou WhatsApp</option><option value="email">e-mail</option><option value="whatsapp">WhatsApp</option></select></label>
      <label>Time<select id="mt-team">${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label>
      <label>Modelo aprovado no WhatsApp (para fora da janela de 24 h)<input id="mt-wa" data-noi18n></label></div>
      <label class="mt">Assunto (e-mail)<input id="mt-subject" data-noi18n placeholder="Documento pendente: {{doc}}"></label>
      <label class="mt">Texto<textarea id="mt-body" rows="4" data-noi18n placeholder="Olá {{nome}}, ainda precisamos do {{doc}} para seguir."></textarea></label>
      <div class="row mt"><button id="mt-save">Salvar (volta a rascunho se o texto mudar)</button></div></div>`;
  main.querySelectorAll('tr[data-n]').forEach(tr => {
    const m = list.find(x => x.name === tr.dataset.n);
    $('.mt-edit', tr).onclick = () => { $('#mt-h').textContent = window.t('Editar modelo'); $('#mt-name').value = m.name; $('#mt-ch').value = m.channel;
      $('#mt-wa').value = m.wa_template; $('#mt-subject').value = m.subject; $('#mt-body').value = m.body; $('#mt-name').scrollIntoView(); };
    const okb = $('.mt-ok', tr);
    if (okb) okb.onclick = ev => act(ev.target, () => api(`/message-templates/${m.name}/approve`, { method: 'POST' }).then(templatesMsgPage), 'Modelo aprovado');
  });
  $('#mt-save').onclick = ev => act(ev.target, () => api('/message-templates', { method: 'PUT', body: { name: $('#mt-name').value.trim(), channel: $('#mt-ch').value,
    team: $('#mt-team').value, wa_template: $('#mt-wa').value.trim(), subject: $('#mt-subject').value, body: $('#mt-body').value } }).then(templatesMsgPage), 'Modelo salvo');
}

/* ---------- reuniões ---------- */
async function meetingsPage() {
  const [list, teams, emps] = await Promise.all([api('/meetings'), api('/teams').catch(() => []), api('/employees').catch(() => [])]);
  main.innerHTML = `<h1>Reuniões</h1><div class="sub">Marque pela agenda da empresa, receba o briefing antes, transforme a transcrição em ata e as ações em tarefas — cobradas na reunião seguinte da série.</div>
    <div class="card">${list.length ? `<table class="small"><tr><th>Reunião</th><th>Quando</th><th>Status</th></tr>${list.map(m => `<tr><td><a href="#/meetings/${m.id}"><b data-noi18n>${esc(m.title)}</b></a>
      <div class="mute" data-noi18n>${esc(m.series || '')}</div></td><td>${dtLocal(m.starts_at)}</td><td><span class="pill">${esc(window.t({ scheduled: 'marcada', held: 'aconteceu', minutes: 'com ata' }[m.status] || m.status))}</span></td></tr>`).join('')}</table>`
      : '<div class="mute small">Nenhuma reunião ainda.</div>'}</div>
    <div class="card mt"><h2>Nova reunião</h2><div class="grid g2"><label>Título<input id="mg-title"></label><label>Início (fuso da empresa)<input id="mg-start" type="datetime-local"></label>
      <label>Duração (min)<input id="mg-dur" type="number" value="30"></label><label>Série (para cobrar as ações na próxima)<input id="mg-series" data-noi18n placeholder="ex.: comite-semanal"></label>
      <label>Participantes (e-mails, separados por vírgula)<input id="mg-att" data-noi18n></label>
      <label>Time<select id="mg-team"><option value="">—</option>${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label>
      <label>Digital employee da ata e do briefing<select id="mg-emp"><option value="">—</option>${emps.filter(e => ['active', 'probation'].includes(e.status)).map(e => `<option value="${esc(e.slug)}">${esc(e.name)}</option>`).join('')}</select></label>
      <label>Briefing (horas antes; 0 = não)<input id="mg-prep" type="number" value="0" min="0" max="72"></label></div>
      <label class="mt">Pauta<textarea id="mg-agenda" rows="2"></textarea></label>
      <label class="row small mt" style="gap:6px"><input type="checkbox" id="mg-sched"> <span>Marcar na agenda da empresa e convidar</span></label>
      <div class="row mt"><button id="mg-go">Criar</button></div></div>`;
  $('#mg-go').onclick = ev => act(ev.target, () => api('/meetings', { method: 'POST', body: { title: $('#mg-title').value.trim(), starts_at: $('#mg-start').value.replace('T', ' '),
    duration_min: Number($('#mg-dur').value), series: $('#mg-series').value.trim(), attendees: $('#mg-att').value.split(',').map(x => x.trim()).filter(Boolean),
    team: $('#mg-team').value, employee: $('#mg-emp').value, prep_hours: Number($('#mg-prep').value), agenda: $('#mg-agenda').value, schedule: $('#mg-sched').checked } })
    .then(m => { location.hash = '#/meetings/' + m.id; }), 'Reunião criada');
}

async function meetingPage(id) {
  const m = await api('/meetings/' + id);
  main.innerHTML = `<a href="#/meetings" class="mute small">← Reuniões</a><h1 data-noi18n>${esc(m.title)}</h1>
    <div class="sub">${dtLocal(m.starts_at)} · <span data-noi18n>${esc((m.attendees || []).join(', ') || '—')}</span>${m.provider ? ` · ${window.t('na agenda')} (${esc(m.provider)})` : ''}
      ${m.prep_task ? ` · <a href="#/tasks/${m.prep_task}">${window.t('briefing')}</a>` : ''}${m.minutes_task ? ` · <a href="#/tasks/${m.minutes_task}">${window.t('tarefa da ata')}</a>` : ''}</div>
    ${m.previous_open.length ? `<div class="card warn-card"><h2>Pendências da reunião anterior</h2>${m.previous_open.map(a => `<div class="li small"><a href="${esc(a.link)}" data-noi18n>${esc(a.title)}</a>
      <span class="mute" data-noi18n>${esc(a.owner)} · ${esc(a.status)}</span></div>`).join('')}</div>` : ''}
    <div class="grid g2 mt"><div class="card"><h2>Ata</h2>${m.minutes ? `<div class="md" data-noi18n>${mdLite(m.minutes)}</div>
      ${(m.decisions || []).length ? `<h3 class="mt">Decisões</h3><ul class="small" data-noi18n>${m.decisions.map(d => `<li>${esc(d)}</li>`).join('')}</ul>` : ''}`
      : `<div class="mute small">${m.transcript ? window.t('A ata está sendo escrita.') : window.t('Cole a transcrição para o Digital employee escrever a ata.')}</div>`}
      ${m.transcript ? '' : `<label class="mt">Transcrição<textarea id="mg-tr" rows="6"></textarea></label><div class="row mt"><button id="mg-tr-go" ${m.employee ? '' : 'disabled'}>Enviar a transcrição</button></div>
        ${m.employee ? '' : `<div class="mute small">${window.t('Escolha um Digital employee na reunião para escrever a ata.')}</div>`}`}</div>
      <div class="card"><h2>Ações (${m.actions.length})</h2>${m.actions.map(a => `<div class="li small"><a href="${esc(a.link)}" data-noi18n>${esc(a.title)}</a>
        <span class="mute" data-noi18n>${esc(a.owner)} · ${esc(a.status)}${a.due ? ' · ' + dtLocal(a.due) : ''}</span></div>`).join('') || '<div class="mute small">—</div>'}</div></div>`;
  const go = $('#mg-tr-go');
  if (go) go.onclick = ev => act(ev.target, () => api(`/meetings/${id}/transcript`, { method: 'POST', body: { text: $('#mg-tr').value } }).then(() => meetingPage(id)), 'Transcrição enviada');
}

/* ---------- conversas da tarefa (página da tarefa) ---------- */
async function taskConversations(id, box) {
  const list = await api(`/tasks/${id}/conversations`).catch(() => []);
  if (!list.length) return;
  const TS = { open: 'aberta', waiting: 'aguardando resposta', replied: 'respondida', no_reply: 'sem resposta', closed: 'encerrada' };
  box.insertAdjacentHTML('beforeend', `<div class="card mt"><h2>Conversas com gente de fora</h2>${list.map(c => `<div class="mt"><b data-noi18n>${esc(c.contact_name || c.contact)}</b>
    <span class="mute small" data-noi18n>${esc(c.channel)} · ${esc(c.contact)}</span> <span class="pill ${c.status === 'waiting' ? 'warn' : c.status === 'no_reply' ? 'bad' : 'ok'}">${esc(window.t(TS[c.status] || c.status))}</span>
    ${c.next_follow_up_at ? `<span class="small mute">${window.t('próximo lembrete')}: ${dtLocal(c.next_follow_up_at)} (${c.follow_ups_sent}/${c.max_follow_ups})</span>` : ''}
    ${(c.promises || []).length ? `<div class="small mt">${window.t('Prometeu')}: ${c.promises.map(p => `<span class="chip" data-noi18n>${esc(p.text)} → ${esc(p.due)}</span>`).join(' ')}</div>` : ''}
    ${c.messages.map(m => `<div class="li small ${m.direction === 'in' ? 'ok-card' : ''}"><b>${m.direction === 'in' ? '←' : '→'}</b> <span class="mute">${ago(m.at)}</span>
      ${m.template ? `<span class="chip" data-noi18n>${esc(m.template)}</span>` : ''}${m.status === 'failed' ? ` <span class="pill bad" data-noi18n>${esc(m.error)}</span>` : ''}
      <div data-noi18n style="white-space:pre-wrap">${esc(m.body.slice(0, 1200))}</div></div>`).join('')}</div>`).join('')}</div>`);
}

/* ---------- console: calendário, WhatsApp, agenda e transcrições ---------- */
async function wfCalendar(t) {
  const c = await api('/calendar');
  const DAYS = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom'];
  t.innerHTML = `<div class="card"><h2>Calendário da empresa</h2><div class="mute small">Dias úteis e feriados valem para prazos de playbooks (“D+3 úteis”), rotinas no
    “1º dia útil do mês” (@bd 1 09:00) e horários de reunião. Os feriados nacionais já vêm; acrescente os estaduais e municipais.</div>
    <div class="row mt" style="gap:10px;flex-wrap:wrap">${DAYS.map((d, i) => `<label class="row small" style="gap:4px"><input type="checkbox" class="cl-d" value="${i}" ${c.workdays.includes(i) ? 'checked' : ''}> ${window.t(d)}</label>`).join('')}</div>
    <div class="grid g2 mt"><label>Início do expediente<input id="cl-start" type="time" value="${esc(c.start)}"></label><label>Fim do expediente<input id="cl-end" type="time" value="${esc(c.end)}"></label></div>
    <label class="mt">Feriados da empresa (estaduais, municipais, pontes) — um por linha: AAAA-MM-DD nome<textarea id="cl-hol" rows="4" class="code" data-noi18n>${esc(c.extra.map(h => `${h.date} ${h.name}`).join('\n'))}</textarea></label>
    <label class="mt">Feriados nacionais em que a empresa trabalha — datas separadas por vírgula<input id="cl-rem" value="${esc(c.removed.join(', '))}" data-noi18n></label>
    <div class="row mt"><button id="cl-save">Salvar</button></div>
    <h3 class="mt">Feriados (${c.holidays.length})</h3><div class="small" data-noi18n>${c.holidays.map(h => `${esc(h.date)} ${esc(h.name)}${h.national ? '' : ' *'}`).join(' · ')}</div></div>`;
  $('#cl-save').onclick = ev => act(ev.target, () => api('/calendar', { method: 'PUT', body: {
    workdays: [...t.querySelectorAll('.cl-d')].filter(x => x.checked).map(x => Number(x.value)), start: $('#cl-start').value, end: $('#cl-end').value,
    holidays: lines($('#cl-hol').value).map(l => ({ date: l.slice(0, 10), name: l.slice(11).trim() || 'Feriado' })),
    removed: $('#cl-rem').value.split(',').map(x => x.trim()).filter(Boolean) } }).then(() => wfCalendar(t)), 'Calendário salvo');
}

function notifyExtras(t, n, put) {
  const w = n.whatsapp || {}, c = n.calendar || {};
  t.insertAdjacentHTML('beforeend', `<div class="grid g2 mt"><div class="card"><h2>WhatsApp ${w.configured ? '<span class="pill ok">ligado</span>' : '<span class="pill">desligado</span>'}</h2>
      <div class="mute small">WhatsApp Business (Cloud API): conversas com gente de fora acompanhadas pela tarefa e pedidos de trabalho das pessoas da empresa. No app da Meta, use a URL abaixo
        como webhook, com o token de verificação daqui; cada chamada é conferida pela assinatura do app.</div>
      <div class="small mt">${copyable(w.webhook_url || '')}</div>
      <div class="grid g2 mt"><label>ID do número<input id="wa-phone" value="${esc(w.phone_id || '')}" data-noi18n></label>
        <label>Token de verificação do webhook<input id="wa-verify" autocomplete="off" placeholder="${w.verify_token ? window.t('cadastrado — cole outro para trocar') : ''}"></label>
        <label>Token de acesso<input id="wa-token" type="password" autocomplete="off" placeholder="${w.configured ? window.t('cadastrado — cole outro para trocar') : ''}"></label>
        <label>Segredo do app<input id="wa-secret" type="password" autocomplete="off" placeholder="${w.app_secret ? window.t('cadastrado — cole outro para trocar') : ''}"></label></div>
      <div class="row mt"><button class="ghost" id="wa-save">Salvar</button></div></div>
    <div class="card"><h2>Agenda da empresa ${c.provider ? `<span class="pill ok">${esc(c.provider)}</span>` : '<span class="pill">desligada</span>'}</h2>
      <div class="mute small">Os Digital employees acham horários livres e mandam convites pela agenda da empresa — Microsoft 365 (aplicativo do tenant com permissão de calendário)
        ou Google Workspace (conta de serviço com delegação no domínio). Ninguém precisa conectar a própria agenda.</div>
      <label class="mt">Provedor<select id="cal-p"><option value="">—</option><option value="microsoft" ${c.chosen === 'microsoft' ? 'selected' : ''}>Microsoft 365</option>
        <option value="google" ${c.chosen === 'google' ? 'selected' : ''}>Google Workspace</option></select></label>
      <div class="grid g2 mt"><label>Tenant ID (Microsoft)<input id="cal-tenant" value="${esc(c.ms_tenant || '')}" data-noi18n></label><label>Client ID<input id="cal-client" value="${esc(c.ms_client_id || '')}" data-noi18n></label>
        <label>Client secret<input id="cal-secret" type="password" autocomplete="off" placeholder="${c.ms_client_secret ? window.t('cadastrado — cole outro para trocar') : ''}"></label></div>
      <label class="mt">Conta de serviço do Google (JSON)${c.google_sa ? ` — <span data-noi18n>${esc(c.google_sa)}</span>` : ''}<textarea id="cal-sa" rows="2" class="code" placeholder="{&quot;client_email&quot;: …}"></textarea></label>
      <div class="row mt" style="gap:6px"><button class="ghost" id="cal-save">Salvar</button><button class="ghost" id="mt-token">Token das transcrições</button></div><div id="mt-token-out"></div></div></div>`);
  const val = id => $(id).value.trim();
  $('#wa-save').onclick = ev => { const b = { whatsapp_phone_id: val('#wa-phone') };
    if (val('#wa-verify')) b.whatsapp_verify_token = val('#wa-verify'); if (val('#wa-token')) b.whatsapp_token = val('#wa-token'); if (val('#wa-secret')) b.whatsapp_app_secret = val('#wa-secret');
    act(ev.target, () => put(b), 'WhatsApp salvo'); };
  $('#cal-save').onclick = ev => { const b = { calendar_provider: $('#cal-p').value, ms_tenant: val('#cal-tenant'), ms_client_id: val('#cal-client') };
    if (val('#cal-secret')) b.ms_client_secret = val('#cal-secret'); if (val('#cal-sa')) b.google_sa = val('#cal-sa');
    act(ev.target, () => put(b), 'Agenda salva'); };
  $('#mt-token').onclick = ev => act(ev.target, async () => { const r = await api('/meetings-token', { method: 'POST' });
    $('#mt-token-out').innerHTML = `<div class="card warn-card mt"><b>${window.t('Copie agora: o token não aparece de novo.')}</b><div class="mt">${copyable(r.token)}</div>
      <div class="small mt"><code class="inline" data-noi18n>POST ${esc(r.url)} {"meeting_id": …, "transcript": "…"}</code></div></div>`; });
}

/* ================================================================== fase G: conhecimento, navegador, governança */
const KS_KIND = { upload: 'arquivos enviados', url: 'páginas (URL)', confluence: 'Confluence', sharepoint: 'SharePoint', gdrive: 'Google Drive' };
const CLS = ['public', 'internal', 'confidential', 'restricted'];
const CLS_LABEL = { public: 'pública', internal: 'interna', confidential: 'confidencial', restricted: 'restrita' };

function citations(list) {
  return list.length ? list.map(h => `<div class="li"><b>[${h.n}]</b> ${h.url ? `<a href="${esc(h.url)}" target="_blank" rel="noopener" data-noi18n>${esc(h.title)}</a>` : `<b data-noi18n>${esc(h.title)}</b>`}
    <span class="mute small">${h.updated ? esc(h.updated) : ''}</span>${h.stale ? ` <span class="pill warn">${window.t('vencido')}</span>` : ''}
    <div class="small" data-noi18n style="white-space:pre-wrap">${esc(h.snippet)}</div></div>`).join('') : `<div class="mute small">${window.t('Nada que você possa ler cobre isso.')}</div>`;
}

async function knowledgePage() {
  const [sources, teams] = await Promise.all([api('/knowledge/sources'), api('/teams').catch(() => [])]);
  const gaps = await api('/knowledge/gaps').catch(() => null), stale = await api('/knowledge/stale').catch(() => []);
  main.innerHTML = `<h1>Base de conhecimento</h1><div class="sub">Políticas, procedimentos e histórico da empresa, com as permissões de cada documento: os Digital employees buscam com o
    acesso de quem pediu a tarefa e citam a fonte.</div>
    <div class="card"><div class="row" style="gap:6px"><input id="kq" placeholder="Pergunte à base (ex.: limite de hospedagem)"><button id="kq-go">Buscar</button></div><div id="kq-out" class="mt"></div></div>
    <div class="card mt"><h2>Fontes (${sources.length})</h2>${sources.length ? `<table class="small"><tr><th>Fonte</th><th>Tipo</th><th>Acesso</th><th>Documentos</th><th>Sincronizada</th><th></th></tr>
      ${sources.map(s => `<tr data-n="${esc(s.name)}"><td><b data-noi18n>${esc(s.title)}</b><div class="mute" data-noi18n>${esc(s.name)}</div></td><td>${esc(window.t(KS_KIND[s.kind] || s.kind))}</td>
        <td>${esc(window.t({ inherit: 'herda do original', team: 'times da fonte', org: 'empresa toda' }[s.acl_mode]))}</td><td>${s.docs}${s.stale ? ` <span class="pill warn">${s.stale} ${window.t('vencidos')}</span>` : ''}</td>
        <td>${s.last_sync_at ? ago(s.last_sync_at) : '—'}${s.last_error ? ` <span class="pill bad" data-noi18n>${esc(s.last_error)}</span>` : ''}</td>
        <td>${s.can_manage ? (s.kind === 'upload' ? '<button class="ghost small ks-up">Enviar documento</button>' : '<button class="ghost small ks-sync">Sincronizar</button>') +
          ' <button class="ghost small danger ks-del">Remover</button>' : ''}</td></tr>`).join('')}</table>`
      : '<div class="mute small">Nenhuma fonte ainda.</div>'}<div id="ks-up-box"></div></div>
    <div class="card mt"><h2>Nova fonte</h2><div class="grid g2"><label>Nome<input id="ks-name" data-noi18n></label>
      <label>Tipo<select id="ks-kind">${Object.entries(KS_KIND).map(([k, v]) => `<option value="${k}">${window.t(v)}</option>`).join('')}</select></label>
      <label>Acesso<select id="ks-acl"><option value="team">times da fonte</option><option value="inherit">herdar as permissões do original</option><option value="org">empresa toda (admin)</option></select></label>
      <label>Times<select id="ks-teams" multiple size="3">${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label>
      <label>Revisão (dias até ficar vencido)<input id="ks-rev" type="number" value="365"></label>
      <label>Classificação<select id="ks-cls">${CLS.map(c => `<option value="${c}" ${c === 'internal' ? 'selected' : ''}>${window.t(CLS_LABEL[c])}</option>`).join('')}</select></label></div>
      <label class="mt">Configuração (JSON) — url: {"urls": […]}; confluence: {"base_url", "space", "email", "api_token"}; sharepoint: {"drive_id", "folder"}; gdrive: {"folder_id", "subject"}
        <textarea id="ks-cfg" rows="3" class="code" data-noi18n>{}</textarea></label>
      <div class="row mt"><button id="ks-save">Criar fonte</button></div></div>
    ${gaps ? `<div class="grid g2 mt"><div class="card"><h2>Lacunas (${gaps.length})</h2><div class="mute small">Perguntas que a base não respondeu bem.</div>
      ${gaps.slice(0, 30).map(g => `<div class="li small row between" data-g="${g.id}"><span data-noi18n>${esc(g.query)}</span><button class="ghost small kg-ok">Resolvida</button></div>`).join('') || '<div class="mute small">—</div>'}</div>
      <div class="card"><h2>Documentos vencidos (${stale.length})</h2>${stale.slice(0, 30).map(d => `<div class="li small"><span data-noi18n>${esc(d.title)}</span> <span class="mute">${esc(d.review_by)}</span></div>`).join('') || '<div class="mute small">—</div>'}</div></div>` : ''}`;
  const search = () => act($('#kq-go'), async () => { $('#kq-out').innerHTML = citations(await api('/knowledge/search?q=' + encodeURIComponent($('#kq').value))); });
  $('#kq-go').onclick = search;
  $('#kq').onkeydown = e => { if (e.key === 'Enter') search(); };
  $('#ks-save').onclick = ev => { let cfg; try { cfg = JSON.parse($('#ks-cfg').value || '{}'); } catch { return toast(window.t('Configuração: JSON inválido'), true); }
    act(ev.target, () => api('/knowledge/sources', { method: 'PUT', body: { name: $('#ks-name').value.trim(), kind: $('#ks-kind').value, acl_mode: $('#ks-acl').value,
      teams: [...$('#ks-teams').selectedOptions].map(o => o.value), review_days: Number($('#ks-rev').value), classification: $('#ks-cls').value, config: cfg } }).then(knowledgePage), 'Fonte criada'); };
  main.querySelectorAll('tr[data-n]').forEach(tr => {
    const n = tr.dataset.n, sy = $('.ks-sync', tr), up = $('.ks-up', tr), del = $('.ks-del', tr);
    if (del) del.onclick = ev => confirm(window.t('Remover esta fonte e os documentos copiados dela? O original não é tocado.'))
      && act(ev.target, () => api(`/knowledge/sources/${n}`, { method: 'DELETE' }).then(knowledgePage), 'Fonte removida');
    if (sy) sy.onclick = ev => act(ev.target, () => api(`/knowledge/sources/${n}/sync`, { method: 'POST' }).then(r => { toast(`${r.docs} ${window.t('documento(s)')}`); return knowledgePage(); }));
    if (up) up.onclick = () => { $('#ks-up-box').innerHTML = `<div class="card mt"><h3>Enviar documento</h3><label>Título<input id="kd-title"></label>
      <label class="mt">Texto<textarea id="kd-text" rows="4"></textarea></label><label class="mt">ou arquivo (DOCX, TXT, MD, HTML; PDF com o Data Studio)<input id="kd-file" type="file"></label>
      <div class="row mt"><button id="kd-go">Enviar</button></div></div>`;
      $('#kd-go').onclick = ev => act(ev.target, async () => { const f = $('#kd-file').files[0]; const body = { title: $('#kd-title').value.trim() || (f ? f.name : ''), text: $('#kd-text').value };
        if (f) { body.file_name = f.name; body.content_type = f.type; body.content_base64 = await new Promise(ok => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1] || ''); r.readAsDataURL(f); }); }
        await api(`/knowledge/sources/${n}/documents`, { method: 'POST', body }); return knowledgePage(); }, 'Documento enviado'); };
  });
  main.querySelectorAll('[data-g] .kg-ok').forEach(b => b.onclick = ev => act(ev.target, () => api(`/knowledge/gaps/${b.closest('[data-g]').dataset.g}/resolve`, { method: 'POST' }).then(knowledgePage)));
}

/* ---------- procedimentos do navegador ---------- */
async function proceduresPage() {
  const [d, teams] = await Promise.all([api('/procedures'), api('/teams').catch(() => [])]);
  const toLines = st => st.map(s => [s.action, s.target, s.value, s.commit ? 'commit' : ''].join(' | ')).join('\n');
  main.innerHTML = `<h1>Procedimentos do navegador</h1><div class="sub">Passo a passo para sistemas sem API — portal do banco, site do governo, ERP antigo. O Digital employee executa no
    navegador governado (só nos domínios do procedimento); cliques que confirmam algo passam pela alçada, e senhas vêm do cofre (secret://nome).</div>
    <div class="small">${window.t('Navegador')}: ${d.ready ? '<span class="pill ok">no ar</span>' : `<span class="pill warn">${window.t('desligado')}</span> <span class="mute">docker compose --profile browser up -d</span>`}</div>
    <div class="card mt">${d.procedures.length ? `<table class="small"><tr><th>Procedimento</th><th>Domínios</th><th>Passos</th><th>Variáveis</th><th></th></tr>
      ${d.procedures.map(p => `<tr data-n="${esc(p.name)}"><td><b data-noi18n>${esc(p.title)}</b> <span class="mute">v${p.version}</span></td><td data-noi18n>${esc(p.domains.join(', '))}</td>
        <td>${p.steps.length}</td><td data-noi18n>${esc(p.vars.join(', ') || '—')}</td><td><button class="ghost small pr-edit">Editar</button></td></tr>`).join('')}</table>`
      : '<div class="mute small">Nenhum procedimento ainda. Na página de uma tarefa que usou o navegador, “Salvar o caminho como procedimento”.</div>'}</div>
    <div class="card mt"><h2 id="pr-h">Novo procedimento</h2><div class="grid g2"><label>Nome<input id="pr-name" data-noi18n></label><label>Título<input id="pr-title"></label>
      <label>Domínios (separados por vírgula)<input id="pr-doms" data-noi18n placeholder="banco.com.br"></label>
      <label>Time<select id="pr-team">${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label></div>
      <label class="mt">Passos — um por linha: ação (open, fill, select, click, read, screenshot) | alvo (URL, rótulo ou seletor) | valor ({{variável}} ou secret://nome) | commit
        <textarea id="pr-steps" rows="6" class="code" data-noi18n placeholder="open | https://portal.banco.com.br/login&#10;fill | Usuário | {{usuario}}&#10;fill | Senha | secret://banco-senha&#10;click | Entrar&#10;click | Gerar extrato | | commit"></textarea></label>
      <div class="row mt"><button id="pr-save">Salvar</button></div></div>`;
  main.querySelectorAll('tr[data-n]').forEach(tr => { const p = d.procedures.find(x => x.name === tr.dataset.n);
    $('.pr-edit', tr).onclick = () => { $('#pr-h').textContent = window.t('Editar procedimento'); $('#pr-name').value = p.name; $('#pr-title').value = p.title;
      $('#pr-doms').value = p.domains.join(', '); $('#pr-steps').value = toLines(p.steps); $('#pr-name').scrollIntoView(); }; });
  $('#pr-save').onclick = ev => act(ev.target, () => api('/procedures', { method: 'PUT', body: { name: $('#pr-name').value.trim(), title: $('#pr-title').value.trim(), team: $('#pr-team').value,
    domains: $('#pr-doms').value.split(',').map(x => x.trim()).filter(Boolean),
    steps: lines($('#pr-steps').value).map(l => { const [action, target, value, commit] = l.split('|').map(x => (x || '').trim()); return { action, target, value, commit: commit === 'commit' }; }) } })
    .then(proceduresPage), 'Procedimento salvo');
}

/* ---------- governança de dados ---------- */
async function governancePage() {
  const [g, reqs, projects] = await Promise.all([api('/governance'), api('/subject-requests').catch(() => []), api('/projects').catch(() => [])]);
  const DET = { cpf: 'CPF', cnpj: 'CNPJ', card: 'cartão', email: 'e-mail', phone: 'telefone', health: 'CID (saúde)' };
  main.innerHTML = `<h1>Governança de dados</h1><div class="sub">Classificação, mascaramento (DLP), retenção, pedidos do titular (LGPD) e barreiras de informação.</div>
    <div class="grid g2"><div class="card"><h2>DLP</h2><div class="mute small">Mascarar no que vai ao modelo (ele vê [CPF_1]; quem executa e as pessoas veem o valor real) e bloquear nas mensagens para fora.</div>
      <table class="small mt"><tr><th>Dado</th><th>Mascarar</th><th>Bloquear para fora</th></tr>${g.detectors.map(d => `<tr><td>${esc(window.t(DET[d] || d))}</td>
        <td><input type="checkbox" class="dl-m" value="${d}" ${g.dlp.mask.includes(d) ? 'checked' : ''}></td><td><input type="checkbox" class="dl-b" value="${d}" ${g.dlp.block_outbound.includes(d) ? 'checked' : ''}></td></tr>`).join('')}</table>
      <h3 class="mt">Retenção (dias depois de encerrada; vazio = não apaga)</h3><div class="grid g2">${g.levels.map(l => `<label>${window.t(CLS_LABEL[l])}<input class="rt-d" data-l="${l}" type="number" min="30" value="${g.retention[l] ?? ''}"></label>`).join('')}</div>
      <div class="row mt"><button id="gv-save">Salvar política</button></div></div>
    <div class="card"><h2>Modelos e classificação</h2><div class="mute small">O mais sensível que cada conexão de LLM pode receber; uma tarefa acima disso não vai para aquele modelo.</div>
      ${g.llm.map(c => `<div class="row between li small" data-c="${esc(c.name)}"><span><b data-noi18n>${esc(c.name)}</b> <span class="mute" data-noi18n>${esc(c.model)}</span></span>
        <select class="gl-c">${CLS.map(x => `<option value="${x}" ${x === c.max_classification ? 'selected' : ''}>${window.t(CLS_LABEL[x])}</option>`).join('')}</select></div>`).join('') || '<div class="mute small">—</div>'}</div></div>
    <div class="grid g2 mt"><div class="card"><h2>Pedidos do titular (LGPD)</h2><div class="grid g2"><label>CPF, e-mail ou nome<input id="sr-s" data-noi18n></label>
      <label>Tipo<select id="sr-k"><option value="access">localizar</option><option value="erase">remover (outro admin aprova)</option></select></label></div>
      <div class="row mt"><button id="sr-go">Abrir pedido</button></div>
      ${reqs.map(r => `<div class="li small" data-r="${r.id}"><b data-noi18n>${esc(r.subject)}</b> · ${esc(window.t(r.kind === 'erase' ? 'remoção' : 'localização'))} · <span class="pill">${esc(window.t(r.status))}</span>
        · ${r.report ? r.report.total : 0} ${window.t('ocorrência(s)')} <span class="mute" data-noi18n>${esc(r.requested_by)}</span>
        ${r.kind === 'erase' && r.status === 'open' ? '<button class="ghost small sr-ok">Aprovar a remoção</button><button class="ghost small danger sr-no">Recusar</button>' : ''}</div>`).join('')}</div>
    <div class="card"><h2>Projetos</h2><div class="mute small">Projeto confidencial: só os membros (e quem pediu a tarefa) leem — admins e auditores só com acesso de emergência, justificado e avisado ao dono.</div>
      <label class="mt">Projeto<select id="pg-p">${projects.map(p => `<option value="${esc(p.slug)}">${esc(p.name)}</option>`).join('')}</select></label>
      <div class="grid g2 mt"><label>Classificação<select id="pg-c"><option value="">—</option>${CLS.map(c => `<option value="${c}">${window.t(CLS_LABEL[c])}</option>`).join('')}</select></label>
        <label class="row small" style="gap:6px"><input type="checkbox" id="pg-conf"> ${window.t('Confidencial (barreira de informação)')}</label>
        <label class="row small" style="gap:6px"><input type="checkbox" id="pg-hold"> ${window.t('Retenção legal')}</label>
        <label>Membros (e-mails)<input id="pg-mem" data-noi18n></label></div>
      <div class="row mt" style="gap:6px"><button class="ghost" id="pg-save">Salvar</button><input id="bg-why" placeholder="motivo do acesso de emergência"><button class="ghost danger" id="bg-go">Acesso de emergência</button></div></div></div>`;
  $('#gv-save').onclick = ev => act(ev.target, () => api('/governance', { method: 'PUT', body: {
    dlp: { mask: [...main.querySelectorAll('.dl-m')].filter(x => x.checked).map(x => x.value), block_outbound: [...main.querySelectorAll('.dl-b')].filter(x => x.checked).map(x => x.value) },
    retention: Object.fromEntries([...main.querySelectorAll('.rt-d')].map(i => [i.dataset.l, i.value ? Number(i.value) : null])) } }).then(governancePage), 'Política salva');
  main.querySelectorAll('[data-c] .gl-c').forEach(s => s.onchange = () => act(s, () => api(`/governance/llm/${encodeURIComponent(s.closest('[data-c]').dataset.c)}`, { method: 'PUT', body: { max_classification: s.value } }), 'Salvo'));
  $('#sr-go').onclick = ev => act(ev.target, () => api('/subject-requests', { method: 'POST', body: { subject: $('#sr-s').value.trim(), kind: $('#sr-k').value } }).then(governancePage), 'Pedido aberto');
  main.querySelectorAll('[data-r]').forEach(el => { const id = el.dataset.r, ok = $('.sr-ok', el), no = $('.sr-no', el);
    if (ok) ok.onclick = ev => confirm(window.t('Remover os dados desta pessoa? Não dá para desfazer.')) && act(ev.target, () => api(`/subject-requests/${id}/erase`, { method: 'POST', body: { approve: true } }).then(governancePage));
    if (no) no.onclick = ev => act(ev.target, () => api(`/subject-requests/${id}/erase`, { method: 'POST', body: { approve: false } }).then(governancePage)); });
  $('#pg-save').onclick = ev => act(ev.target, () => api(`/projects/${$('#pg-p').value}/governance`, { method: 'PUT', body: { classification: $('#pg-c').value || null,
    confidential: $('#pg-conf').checked, legal_hold: ME.is_admin ? $('#pg-hold').checked : null, members: $('#pg-mem').value.split(',').map(x => x.trim()).filter(Boolean) } }), 'Projeto salvo');
  $('#bg-go').onclick = ev => act(ev.target, () => api(`/projects/${$('#pg-p').value}/break-glass`, { method: 'POST', body: { reason: $('#bg-why').value } })
    .then(r => toast(`${window.t('Acesso liberado até')} ${dtLocal(r.until)}`)));
}

/* ---------- alteração de documento (decisão) ---------- */
DECISION_KINDS.doc_change = {
  render(d) {
    const p = d.payload || {}, hs = p.hunks || [];
    return {
      body: `<div class="mt"><b>Alteração proposta</b> em <code class="inline" data-noi18n>${esc(p.document || '')}</code> — <span data-noi18n>${esc(p.summary || '')}</span></div>
        <div class="small mute">${window.t('Marque os trechos que aceita; você pode ajustar o texto antes de aplicar.')}</div>
        ${hs.map(h => `<div class="card mt dch" data-h="${h.id}"><label class="row small" style="gap:6px"><input type="checkbox" class="dch-ok" checked> <b>${window.t('Trecho')} ${h.id}</b></label>
          <div class="grid g2 mt"><div><div class="mute small">${window.t('Antes')}</div><div class="small" data-noi18n style="white-space:pre-wrap;background:color-mix(in srgb,var(--stop,#c33) 10%,transparent);padding:6px">${esc(h.before.join('\n\n')) || '—'}</div></div>
            <div><div class="mute small">${window.t('Depois')}</div><textarea class="dch-after small" rows="${Math.max(2, h.after.join('\n\n').length / 70 | 0)}" data-noi18n>${esc(h.after.join('\n\n'))}</textarea></div></div></div>`).join('')}
        <input class="dc-reason mt" placeholder="Comentário (para recusar, diga o porquê)">`,
      btns: '<button class="dch-go">Aplicar os trechos marcados</button><button class="ghost danger dch-no">Recusar</button>',
    };
  },
  bind(card, id, after) {
    $('.dch-go', card).onclick = ev => {
      const accept = [], edits = {};
      card.querySelectorAll('.dch').forEach(h => { if (!$('.dch-ok', h).checked) return; const n = Number(h.dataset.h); accept.push(n);
        const ta = $('.dch-after', h); if (ta.value !== ta.defaultValue) edits[n] = ta.value; });
      act(ev.target, () => decide(id, { decision: Object.keys(edits).length ? 'approve_edited' : 'approve', edit: { accept, edits }, reason: $('.dc-reason', card).value }).then(after), 'Alteração aplicada');
    };
    $('.dch-no', card).onclick = ev => act(ev.target, () => decide(id, { decision: 'reject', reason: $('.dc-reason', card).value }).then(after), 'Recusado');
  },
};

/* ---------- página da tarefa: classificação e caminho do navegador ---------- */
function taskGovernance(x, id, box) {
  const used = (x.events || []).some(e => e.kind === 'browser');
  box.insertAdjacentHTML('beforeend', `<div class="card mt"><div class="row between"><h2>Dados desta tarefa</h2>
    <span class="row small" style="gap:6px">${window.t('Classificação')} <select id="tg-cls">${CLS.map(c => `<option value="${c}" ${c === (x.classification || 'internal') ? 'selected' : ''}>${window.t(CLS_LABEL[c])}</option>`).join('')}</select>
    ${ME.is_admin ? `<label class="row" style="gap:4px"><input type="checkbox" id="tg-hold" ${x.legal_hold ? 'checked' : ''}> ${window.t('retenção legal')}</label>` : ''}</span></div>
    ${used ? `<div class="row mt" style="gap:6px"><input id="tg-pname" placeholder="nome do procedimento" data-noi18n><button class="ghost" id="tg-proc">Salvar o caminho como procedimento</button></div>` : ''}</div>`);
  $('#tg-cls').onchange = () => act($('#tg-cls'), () => api(`/tasks/${id}/classification`, { method: 'PUT', body: { classification: $('#tg-cls').value } }), 'Classificação salva');
  const hold = $('#tg-hold');
  if (hold) hold.onchange = () => act(hold, () => api(`/tasks/${id}/classification`, { method: 'PUT', body: { legal_hold: hold.checked } }), 'Salvo');
  const pr = $('#tg-proc');
  if (pr) pr.onclick = ev => act(ev.target, () => api('/procedures/from-task', { method: 'POST', body: { task_id: Number(id), name: $('#tg-pname').value.trim() } })
    .then(() => { location.hash = '#/procedures'; }), 'Procedimento salvo');
}

/* ================================================================== fase H: julgamento, vigias, aprendizado, valor */
const OUTCOME = { good: ['deu certo', 'ok'], mixed: ['em parte', 'warn'], bad: ['não deu certo', 'bad'] };

async function memosPage() {
  const list = await api('/memos');
  main.innerHTML = `<h1>Memorandos de decisão</h1><div class="sub">Quando o pedido é uma escolha, o Digital employee entrega opções, critérios com peso, notas, a recomendação com confiança,
    riscos e fontes. Registre o que foi decidido e, depois, como foi — é assim que se mede se a recomendação era boa.</div>
    <div class="card">${list.length ? `<table class="small"><tr><th>Pergunta</th><th>Recomendação</th><th>Decidido</th><th>Resultado</th></tr>
      ${list.map(m => `<tr><td><a href="#/memos/${m.id}" data-noi18n>${esc(m.question.slice(0, 120))}</a></td><td data-noi18n>${esc(m.recommendation)}
        <span class="mute">${Math.round(m.confidence * 100)}%</span></td><td data-noi18n>${esc(m.decided_option || '—')}</td>
        <td>${m.outcome ? `<span class="pill ${OUTCOME[m.outcome][1]}">${esc(window.t(OUTCOME[m.outcome][0]))}</span>` : (m.follow_up_at ? `<span class="mute">${window.t('perguntar em')} ${dtLocal(m.follow_up_at)}</span>` : '—')}</td></tr>`).join('')}</table>`
      : '<div class="mute small">Nenhum memorando ainda.</div>'}</div>`;
}

async function memoPage(id) {
  const m = await api('/memos/' + id);
  const best = Object.entries(m.totals).sort((a, b) => b[1] - a[1])[0] || [];
  main.innerHTML = `<a href="#/memos" class="mute small">← Memorandos</a><h1 data-noi18n>${esc(m.question)}</h1>
    <div class="sub">${window.t('Recomendação')}: <b data-noi18n>${esc(m.recommendation)}</b> · ${window.t('confiança')} ${Math.round(m.confidence * 100)}%
      ${m.task ? ` · <a href="#/tasks/${m.task}">${window.t('tarefa')} #${m.task}</a>` : ''}</div>
    <div class="card"><table class="small"><tr><th>Opção</th>${m.criteria.map(c => `<th data-noi18n>${esc(c.name)} <span class="mute">×${c.weight}</span></th>`).join('')}<th>Nota ponderada</th></tr>
      ${m.options.map(o => `<tr${o.name === m.recommendation ? ' style="font-weight:600"' : ''}><td data-noi18n>${esc(o.name)}${o.name === best[0] ? ' ★' : ''}</td>
        ${m.criteria.map(c => `<td>${(m.scores[o.name] || {})[c.name] ?? '—'}</td>`).join('')}<td><b>${m.totals[o.name]}</b></td></tr>`).join('')}</table></div>
    <div class="grid g2 mt">${m.options.map(o => `<div class="card"><h3 data-noi18n>${esc(o.name)}</h3><div class="small" data-noi18n>${esc(o.summary || '')}</div>
      <ul class="small" data-noi18n>${(o.pros || []).map(p => `<li>+ ${esc(p)}</li>`).join('')}${(o.cons || []).map(c => `<li>− ${esc(c)}</li>`).join('')}</ul></div>`).join('')}</div>
    <div class="grid g2 mt"><div class="card"><h2>Riscos</h2><ul class="small" data-noi18n>${m.risks.map(r => `<li>${esc(r)}</li>`).join('') || '<li>—</li>'}</ul>
      <h3 class="mt">Fontes</h3><div class="small" data-noi18n>${m.citations.map(c => esc(c)).join('<br>') || '—'}</div></div>
      <div class="card"><h2>Revisor crítico</h2>${m.critic ? `<span class="pill ${m.critic.severity === 'high' ? 'bad' : m.critic.severity === 'medium' ? 'warn' : 'ok'}">${esc(m.critic.severity)}</span>
        <ul class="small" data-noi18n>${(m.critic.notes || []).map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : '<div class="mute small">Desligado para este funcionário.</div>'}</div></div>
    <div class="card mt"><h2>Decisão e resultado</h2>${m.decided_option ? `<div class="small">${window.t('Decidido')}: <b data-noi18n>${esc(m.decided_option)}</b> ·
        <span data-noi18n>${esc(m.decided_by)}</span> · ${dtLocal(m.decided_at)}${m.decided_note ? ` — <span data-noi18n>${esc(m.decided_note)}</span>` : ''}</div>
      ${m.outcome ? `<div class="small mt">${window.t('Resultado')}: <span class="pill ${OUTCOME[m.outcome][1]}">${esc(window.t(OUTCOME[m.outcome][0]))}</span> <span data-noi18n>${esc(m.outcome_note)}</span></div>`
        : `<div class="row mt" style="gap:6px"><select id="mo-r"><option value="good">${window.t('deu certo')}</option><option value="mixed">${window.t('em parte')}</option>
          <option value="bad">${window.t('não deu certo')}</option></select><input id="mo-n" placeholder="o que aconteceu"><button id="mo-go">Registrar o resultado</button></div>`}`
      : `<div class="row" style="gap:6px"><select id="md-o">${m.options.map(o => `<option ${o.name === m.recommendation ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}<option value="outra">${window.t('outra')}</option></select>
        <input id="md-n" placeholder="por quê (opcional)"><label class="row small" style="gap:4px">${window.t('perguntar como foi em')} <input id="md-d" type="number" value="30" style="width:70px"> ${window.t('dias')}</label>
        <button id="md-go">Registrar a decisão</button></div>`}</div>`;
  const dg = $('#md-go');
  if (dg) dg.onclick = ev => act(ev.target, () => api(`/memos/${id}/decide`, { method: 'POST', body: { option: $('#md-o').value, note: $('#md-n').value,
    follow_up_days: Number($('#md-d').value) } }).then(() => memoPage(id)), 'Decisão registrada');
  const og = $('#mo-go');
  if (og) og.onclick = ev => act(ev.target, () => api(`/memos/${id}/outcome`, { method: 'POST', body: { outcome: $('#mo-r').value, note: $('#mo-n').value } })
    .then(() => memoPage(id)), 'Resultado registrado');
}

/* ---------- vigias ---------- */
async function watchersPage() {
  const [list, teams, emps] = await Promise.all([api('/watchers'), api('/teams').catch(() => []), api('/employees').catch(() => [])]);
  main.innerHTML = `<h1>Vigias</h1><div class="sub">Olham uma fonte de tempos em tempos e só agem quando algo muda ou cruza um limiar: mudança regulatória, preço de concorrente,
    fila estourando, nova versão de uma política.</div>
    <div class="card">${list.length ? `<table class="small"><tr><th>Vigia</th><th>Fonte</th><th>Quando</th><th>Disparos</th><th>Última mudança</th><th></th></tr>
      ${list.map(w => `<tr data-w="${w.id}"><td><b data-noi18n>${esc(w.name)}</b>${w.enabled ? '' : ` <span class="pill">${window.t('pausado')}</span>`}${w.last_error ? ` <span class="pill bad" data-noi18n>${esc(w.last_error)}</span>` : ''}</td>
        <td class="mute" data-noi18n>${esc(w.source.type)}: ${esc(w.source.url || w.source.query || w.source.metric || '')}</td><td>${esc(w.schedule_text)}</td><td>${w.fired}</td>
        <td class="mute" data-noi18n style="max-width:280px;white-space:pre-wrap">${esc((w.last_change || '—').slice(0, 200))}</td>
        <td class="row" style="gap:4px"><button class="ghost small wt-chk">Olhar agora</button><button class="ghost small danger wt-del">×</button></td></tr>`).join('')}</table>`
      : '<div class="mute small">Nenhum vigia ainda.</div>'}</div>
    <div class="card mt"><h2>Novo vigia</h2><div class="grid g2"><label>Nome<input id="wt-name"></label>
      <label>Time<select id="wt-team">${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label>
      <label>Fonte<select id="wt-src"><option value="url">página (texto)</option><option value="json">JSON (um valor)</option><option value="knowledge">base de conhecimento</option><option value="metric">número da plataforma</option></select></label>
      <label>URL, pergunta ou métrica<input id="wt-ref" data-noi18n placeholder="https://… · open_tasks · queue_breached:backoffice"></label>
      <label>Caminho no JSON (opcional)<input id="wt-path" data-noi18n placeholder="data.0.price"></label>
      <label>Condição<select id="wt-cond"><option value="changed">mudou</option><option value="threshold">cruzou um limiar</option><option value="contains">passou a conter</option></select></label>
      <label>Limiar (op valor) ou frase<input id="wt-val" data-noi18n placeholder="> 100 · nova resolução"></label>
      <label>Agenda<input id="wt-sched" value="0 * * * *" data-noi18n></label>
      <label>Ação<select id="wt-act"><option value="task">tarefa para um Digital employee</option><option value="notify">me avisar</option><option value="log">só registrar</option></select></label>
      <label>Digital employee<select id="wt-emp"><option value="">—</option>${emps.filter(e => e.status === 'active').map(e => `<option value="${esc(e.slug)}">${esc(e.name)}</option>`).join('')}</select></label>
      <label style="grid-column:1/-1">Título da tarefa<input id="wt-title" placeholder="ex.: Analisar mudança regulatória"></label></div>
      <div class="row mt"><button id="wt-go">Criar vigia</button></div></div>`;
  $('#wt-go').onclick = ev => {
    const kind = $('#wt-src').value, ref = $('#wt-ref').value.trim(), c = $('#wt-cond').value, v = $('#wt-val').value.trim();
    const source = { type: kind, ...(kind === 'url' || kind === 'json' ? { url: ref, path: $('#wt-path').value.trim() } : kind === 'knowledge' ? { query: ref } : { metric: ref }) };
    const m = v.match(/^(>=|<=|==|!=|>|<)\s*(-?[\d.]+)$/);
    const condition = c === 'threshold' ? (m ? { type: c, op: m[1], value: Number(m[2]) } : { type: c }) : c === 'contains' ? { type: c, text: v } : { type: c };
    act(ev.target, () => api('/watchers', { method: 'POST', body: { name: $('#wt-name').value.trim(), team: $('#wt-team').value, source, condition, schedule: $('#wt-sched').value.trim(),
      action: { type: $('#wt-act').value, title: $('#wt-title').value.trim() }, employee: $('#wt-emp').value } }).then(watchersPage), 'Vigia criado');
  };
  main.querySelectorAll('tr[data-w]').forEach(tr => { const id = tr.dataset.w;
    $('.wt-chk', tr).onclick = ev => act(ev.target, () => api(`/watchers/${id}/check`, { method: 'POST' }).then(watchersPage));
    $('.wt-del', tr).onclick = ev => confirm(window.t('Remover este vigia?')) && act(ev.target, () => api('/watchers/' + id, { method: 'DELETE' }).then(watchersPage)); });
}

/* ---------- trilhas ---------- */
async function learningPage() {
  const [tracks, pbs, procs, teams] = await Promise.all([api('/learning'), api('/playbooks').catch(() => []), api('/procedures').catch(() => ({ procedures: [] })), api('/teams').catch(() => [])]);
  main.innerHTML = `<h1>Trilhas de aprendizado</h1><div class="sub">Perguntas tiradas dos playbooks e procedimentos: quem faz cada etapa, o que vem antes, a evidência, o prazo. Bom para quem está chegando.</div>
    <div class="card">${tracks.length ? tracks.map(t => `<div class="li row between" data-t="${t.id}"><span><b data-noi18n>${esc(t.name)}</b> <span class="mute small">${t.count} ${window.t('perguntas')}</span>
      ${t.my_best != null ? ` <span class="pill ${t.my_best >= 0.8 ? 'ok' : 'warn'}">${Math.round(t.my_best * 100)}%</span>` : ''}</span>
      <span class="row" style="gap:4px"><button class="ghost small tr-go">Fazer</button><button class="ghost small tr-res">Notas do time</button></span></div>`).join('')
      : '<div class="mute small">Nenhuma trilha ainda.</div>'}<div id="tr-box"></div></div>
    <div class="card mt"><h2>Nova trilha</h2><div class="grid g2"><label>A partir de<select id="tr-src">
      ${pbs.map(p => `<option value="playbook:${esc(p.name)}">playbook: ${esc(p.title)}</option>`).join('')}${procs.procedures.map(p => `<option value="procedure:${esc(p.name)}">procedimento: ${esc(p.title)}</option>`).join('')}</select></label>
      <label>Time<select id="tr-team">${teams.map(t => `<option value="${esc(t.slug)}">${esc(t.name)}</option>`).join('')}</select></label></div>
      <div class="row mt"><button id="tr-new" ${pbs.length + procs.procedures.length ? '' : 'disabled'}>Criar trilha</button></div></div>`;
  $('#tr-new').onclick = ev => act(ev.target, () => api('/learning', { method: 'POST', body: { source: $('#tr-src').value, team: $('#tr-team').value } }).then(learningPage), 'Trilha criada');
  main.querySelectorAll('[data-t]').forEach(el => { const t = tracks.find(x => String(x.id) === el.dataset.t);
    $('.tr-go', el).onclick = () => { $('#tr-box').innerHTML = `<div class="card mt"><h3 data-noi18n>${esc(t.name)}</h3>${t.questions.map((q, i) => `<div class="mt"><b data-noi18n>${i + 1}. ${esc(q.q)}</b>
      ${q.options.map((o, j) => `<label class="row small" style="gap:6px"><input type="radio" name="q${i}" value="${j}"> <span data-noi18n>${esc(o)}</span></label>`).join('')}</div>`).join('')}
      <div class="row mt"><button id="tr-send">Enviar respostas</button></div><div id="tr-out"></div></div>`;
      $('#tr-send').onclick = ev => act(ev.target, async () => {
        const answers = t.questions.map((_, i) => Number((main.querySelector(`input[name=q${i}]:checked`) || { value: -1 }).value));
        const r = await api(`/learning/${t.id}/answer`, { method: 'POST', body: { answers } });
        $('#tr-out').innerHTML = `<div class="card mt"><b>${Math.round(r.score * 100)}%</b>${r.results.map(x => `<div class="small mt">${x.right ? '✓' : '✗'} <span data-noi18n>${esc(x.q)}</span>
          ${x.right ? '' : `<div class="mute" data-noi18n>${esc(x.answer)} — ${esc(x.explain)}</div>`}</div>`).join('')}</div>`; }); };
    $('.tr-res', el).onclick = ev => act(ev.target, async () => { const r = await api(`/learning/${t.id}/results`);
      $('#tr-box').innerHTML = `<div class="card mt"><table class="small"><tr><th>Pessoa</th><th>Melhor nota</th><th>Tentativas</th></tr>${r.map(x => `<tr><td data-noi18n>${esc(x.user)}</td>
        <td>${x.best == null ? '—' : Math.round(x.best * 100) + '%'}</td><td>${x.attempts}</td></tr>`).join('')}</table></div>`; }); });
}

/* ---------- valor e oportunidades ---------- */
async function valuePage() {
  const days = Number(new URLSearchParams(location.hash.split('?')[1] || '').get('days') || 30);
  const [roi, ops, base] = await Promise.all([api(`/value/roi?days=${days}`), api('/value/opportunities'), api('/value/baselines')]);
  const t = roi.total || {};
  main.innerHTML = `<div class="row between"><div><h1>Valor e oportunidades</h1><div class="sub">Quanto os Digital employees economizam, a que custo e com quanto retrabalho — e o que ainda é feito por pessoas e vale automatizar.</div></div>
    <div class="row" style="gap:6px"><select id="vl-days">${[7, 30, 90, 365].map(d => `<option value="${d}" ${d === days ? 'selected' : ''}>${d} ${window.t('dias')}</option>`).join('')}</select>
    <a href="/api/value/report?days=${days}"><button class="ghost">Relatório (markdown)</button></a></div></div>
    <div class="board">${kpi(fmt(t.done || 0), 'Tarefas concluídas')}${kpi((t.hours_saved || 0) + ' h', 'Horas poupadas')}${kpi(fmt(t.human_cost || 0), 'Custo equivalente humano')}
      ${kpi(usd(t.llm_cost || 0), 'Custo de LLM')}${kpi(fmt(t.net || 0), 'Líquido')}${kpi(roi.queues.automation_rate == null ? '—' : Math.round(roi.queues.automation_rate * 100) + '%', 'Filas: automático')}</div>
    <div class="card mt"><h2>Por Digital employee</h2>${roi.employees.length ? `<table class="small"><tr><th>Digital employee</th><th>Concluídas</th><th>Horas poupadas</th><th>Custo humano</th><th>LLM</th><th>Líquido</th><th>Retrabalho</th></tr>
      ${roi.employees.map(e => `<tr><td><a href="#/employees/${esc(e.employee)}" data-noi18n>${esc(e.name)}</a></td><td>${e.done}</td><td>${e.hours_saved}</td><td>${fmt(e.human_cost)}</td>
        <td>${usd(e.llm_cost)}</td><td><b>${fmt(e.net)}</b></td><td>${Math.round(e.rework_rate * 100)}%</td></tr>`).join('')}</table>` : '<div class="mute small">Nada concluído na janela.</div>'}</div>
    <div class="grid g2 mt"><div class="card"><h2>Oportunidades</h2><div class="mute small">Pedidos que se repetem e ainda são feitos por pessoas (últimos 90 dias).</div>
      ${ops.map(o => `<div class="li"><b data-noi18n>${esc(o.label)}</b> <span class="mute small">${o.per_month} ${window.t('por mês')} · ${o.hours} h</span>
        <div class="small">${esc(window.t(o.suggestion))}</div><div class="mute small" data-noi18n>${esc(o.examples.join(' · '))}</div></div>`).join('') || '<div class="mute small mt">Nada repetido o bastante ainda.</div>'}</div>
      <div class="card"><h2>Linhas de base</h2><div class="mute small">Quanto tempo uma pessoa levaria e quanto custa a hora — padrão, por Digital employee (employee:slug) ou por categoria de fila (queue:fila:categoria).</div>
        <textarea id="vb" rows="5" class="code mt" data-noi18n placeholder="default | 30 | 60&#10;employee:analista-fiscal | 45 | 80">${esc(base.map(b => `${b.scope} | ${b.minutes} | ${b.hourly_cost}`).join('\n'))}</textarea>
        ${ME.is_admin ? '<div class="row mt"><button class="ghost" id="vb-save">Salvar linhas de base</button></div>' : ''}</div></div>`;
  $('#vl-days').onchange = () => { location.hash = `#/value?days=${$('#vl-days').value}`; };
  const vs = $('#vb-save');
  if (vs) vs.onclick = ev => { const now = lines($('#vb').value).map(l => { const [scope, minutes, hourly_cost] = l.split('|').map(x => (x || '').trim()); return { scope, minutes: Number(minutes), hourly_cost: Number(hourly_cost || 0) }; });
    const gone = base.filter(b => !now.some(n => n.scope === b.scope)).map(b => ({ scope: b.scope, remove: true }));
    act(ev.target, () => api('/value/baselines', { method: 'PUT', body: { items: [...now, ...gone] } }).then(() => valuePage()), 'Linhas de base salvas'); };
}

/* ---------- tarefas de ouro e revisor crítico (Configurações do funcionário) ---------- */
async function goldenCard(box, e) {
  const g = await api(`/employees/${e.slug}/golden`).catch(() => null);
  if (!g || !box) return;
  const last = g.runs[0];
  box.innerHTML = `<div class="card mt"><h2>Tarefas de ouro</h2><div class="mute small">O que ele precisa continuar acertando. Cada mudança no cargo roda estas tarefas de novo (sem agir de verdade);
    se piorar, você decide manter a mudança ou voltar à versão anterior.</div>
    <label class="row small mt" style="gap:6px"><input type="checkbox" id="gd-auto" ${g.mode === 'auto' ? 'checked' : ''} ${e.can_manage ? '' : 'disabled'}> ${window.t('Rodar a cada mudança no cargo')}</label>
    <label class="row small" style="gap:6px"><input type="checkbox" id="gd-critic" ${e.critic_mode === 'auto' ? 'checked' : ''} ${e.can_manage ? '' : 'disabled'}> ${window.t('Revisor crítico nos memorandos de decisão')}</label>
    ${(e.decision_record || {}).memos ? `<div class="small mute">${window.t('Memorandos')}: ${e.decision_record.memos} · ${window.t('com resultado')}: ${e.decision_record.with_outcome}
      · ${window.t('acerto quando seguido')}: ${e.decision_record.accuracy == null ? '—' : Math.round(e.decision_record.accuracy * 100) + '%'}</div>` : ''}
    <table class="small mt"><tr><th>Tarefa</th><th>Resposta esperada</th><th>Última nota</th><th></th></tr>${g.golden.filter(x => x.active).map(x => {
      const r = last ? last.results.find(y => y.golden === x.id) : null;
      return `<tr data-g="${x.id}"><td data-noi18n>${esc(x.title)}</td><td class="mute" data-noi18n>${esc(x.expected.slice(0, 100))}</td>
        <td>${r && r.score != null ? `${r.before != null ? r.before + ' → ' : ''}<b>${r.score}</b>` : '—'}</td><td>${e.can_manage ? '<button class="ghost small danger gd-del">×</button>' : ''}</td></tr>`; }).join('')}</table>
    ${last ? `<div class="small mt">${window.t('Última rodada')}: <span class="pill ${last.status === 'passed' || last.status === 'kept' ? 'ok' : last.status === 'running' ? 'info' : 'bad'}">${esc(window.t(last.status))}</span>
      <span class="mute" data-noi18n>${esc(last.trigger)}</span></div>` : ''}
    ${e.can_manage ? `<div class="grid g2 mt"><label>Título<input id="gd-title"></label><label>Resposta esperada<input id="gd-exp"></label>
      <label style="grid-column:1/-1">Pedido<textarea id="gd-body" rows="2"></textarea></label></div>
      <div class="row mt" style="gap:6px"><button class="ghost" id="gd-add">Adicionar</button><button class="ghost" id="gd-prob">Trazer as tarefas da experiência</button><button class="ghost" id="gd-run">Rodar agora</button></div>` : ''}</div>`;
  if (!e.can_manage) return;
  const re = () => goldenCard(box, e);
  $('#gd-auto', box).onchange = ev => act(ev.target, () => api(`/employees/${e.slug}/golden/mode`, { method: 'PUT', body: { mode: ev.target.checked ? 'auto' : 'off' } }));
  $('#gd-critic', box).onchange = ev => act(ev.target, () => api(`/employees/${e.slug}/critic`, { method: 'PUT', body: { mode: ev.target.checked ? 'auto' : 'off' } }).then(r => { e.critic_mode = r.critic_mode; }));
  $('#gd-add', box).onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/golden`, { method: 'POST', body: { title: $('#gd-title').value.trim(), body: $('#gd-body').value,
    expected: $('#gd-exp').value.trim() } }).then(re), 'Tarefa de ouro adicionada');
  $('#gd-prob', box).onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/golden/from-probation`, { method: 'POST' }).then(r => { toast(`${r.added} ${window.t('adicionada(s)')}`); return re(); }));
  $('#gd-run', box).onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/golden/run`, { method: 'POST' }).then(re), 'Rodada iniciada');
  box.querySelectorAll('tr[data-g] .gd-del').forEach(b => b.onclick = ev => act(ev.target, () => api('/golden/' + b.closest('tr').dataset.g, { method: 'DELETE' }).then(re)));
}

DECISION_KINDS.regression = {
  render(d) {
    const rs = (d.payload || {}).results || [];
    return {
      body: `<div class="mt"><b>Uma mudança no cargo piorou as tarefas de ouro.</b></div><div class="small mt" data-noi18n style="white-space:pre-wrap">${esc(d.question || '')}</div>
        <table class="small mt"><tr><th>Tarefa</th><th>Antes</th><th>Agora</th></tr>${rs.map(r => `<tr><td><a href="#/tasks/${r.task}" data-noi18n>${esc(r.title)}</a></td><td>${r.before ?? '—'}</td>
          <td><b>${r.score ?? '—'}</b></td></tr>`).join('')}</table><input class="dc-reason mt" placeholder="Comentário (opcional)">`,
      btns: '<button class="ghost danger rg-go" data-d="revert">Voltar à versão anterior do cargo</button><button class="ghost rg-go" data-d="keep">Manter a mudança</button>',
    };
  },
  bind(card, id, after) {
    card.querySelectorAll('.rg-go').forEach(b => b.onclick = ev => act(ev.target, () => decide(id, { decision: b.dataset.d, reason: $('.dc-reason', card).value }).then(after),
      b.dataset.d === 'revert' ? 'Cargo restaurado' : 'Mudança mantida'));
  },
};

/* ---------- item: pedir revisão a um Digital employee (modo par) ---------- */
async function itemReviewBox(id) {
  const emps = (await api('/employees').catch(() => [])).filter(e => e.status === 'active');
  if (!emps.length) return;
  main.insertAdjacentHTML('beforeend', `<div class="card mt"><h2>Pedir revisão do meu trabalho</h2><div class="mute small">Um Digital employee confere o que você fez contra o pedido e devolve um checklist — em modo par: ele aponta, não refaz.</div>
    <div class="row mt" style="gap:6px"><select id="ir-emp">${emps.map(e => `<option value="${esc(e.slug)}">${esc(e.name)}</option>`).join('')}</select><button class="ghost" id="ir-go">Pedir revisão</button></div></div>`);
  $('#ir-go').onclick = ev => act(ev.target, () => api(`/work-items/${id}/review-request`, { method: 'POST', body: { employee: $('#ir-emp').value } })
    .then(r => { location.hash = '#/tasks/' + r.task; }), 'Revisão pedida');
}
