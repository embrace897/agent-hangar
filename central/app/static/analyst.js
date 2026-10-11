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
    ${e.can_manage ? '<div class="row mt"><button class="ghost" id="pt-save">Salvar ferramentas</button></div>' : ''}</div>`;
  const save = $('#pt-save', box);
  if (save) save.onclick = ev => act(ev.target, () => api(`/employees/${e.slug}/platform-tools`, { method: 'PUT',
    body: { disabled: [...box.querySelectorAll('.pt-g')].filter(c => !c.checked).map(c => c.value) } }).then(() => platformToolsCard(box, e)), 'Ferramentas salvas');
}

/* ---------- página da tarefa: o que estas fases acrescentam ---------- */
async function taskExtras(x, id) {
  const box = document.createElement('div');
  main.append(box);
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
