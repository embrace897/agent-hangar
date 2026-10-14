/* ---------- Skills: lista, upload, criação e o popup com Visão Geral e Conteúdo (como as Skills do Claude) ---------- */
const SK_ICON = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4h9a2 2 0 0 1 2 2v12"/><path d="M5 8h9a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/><path d="M8 13h5M8 16h3"/></svg>';
const skDate = iso => iso ? new Date(iso).toLocaleDateString(document.documentElement.lang === 'en' ? 'en-US' : 'pt-BR', { day: 'numeric', month: 'short' }) : '';
let SK_LIST = [];

/* markdown simples e seguro: tudo é escapado antes de formatar */
function mdRender(src) {
  const out = [], lines = String(src || '').replace(/\r/g, '').split('\n');
  let list = null, code = null, para = [];
  const inline = s => esc(s).replace(/`([^`]+)`/g, '<code class="inline">$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>').replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const flush = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } if (list) { out.push(`</${list}>`); list = null; } };
  for (const l of lines) {
    if (code !== null) { if (/^```/.test(l)) { out.push(`<pre class="code">${esc(code.join('\n'))}</pre>`); code = null; } else code.push(l); continue; }
    if (/^```/.test(l)) { flush(); code = []; continue; }
    const h = l.match(/^(#{1,4})\s+(.*)$/);
    if (h) { flush(); out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); continue; }
    const li = l.match(/^\s*(?:[-*]|(\d+)\.)\s+(.*)$/);
    if (li) { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } const t = li[1] ? 'ol' : 'ul'; if (list !== t) { if (list) out.push(`</${list}>`); out.push(`<${t}>`); list = t; } out.push(`<li>${inline(li[2])}</li>`); continue; }
    if (!l.trim()) { flush(); continue; }
    if (list) { out.push(`</${list}>`); list = null; }
    para.push(l.trim());
  }
  if (code !== null) out.push(`<pre class="code">${esc(code.join('\n'))}</pre>`);
  flush();
  return out.join('');
}

function skModal(html, wide = true) {
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal card sk-modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true"><button class="ghost sk-x" title="Fechar">${icon('x')}</button>${html}</div>`;
  const close = () => { bg.remove(); document.removeEventListener('keydown', esc_); };
  const esc_ = e => { if (e.key === 'Escape') close(); };
  bg.onclick = e => { if (e.target === bg) close(); };
  bg.querySelector('.sk-x').onclick = close;
  document.addEventListener('keydown', esc_);
  document.body.appendChild(bg);
  return { box: bg.querySelector('.modal'), close };
}

function skAddMenu(btn) {
  const old = document.querySelector('.sk-menu'); if (old) { old.remove(); return; }
  const m = document.createElement('div');
  m.className = 'card sk-menu';
  m.innerHTML = `<a href="#" data-a="upload">${icon('file')} <span>Fazer upload de Skill</span></a><a href="#" data-a="create">${icon('template')} <span>Criar uma Skill</span></a>`;
  btn.parentElement.appendChild(m);
  m.querySelectorAll('a').forEach(a => a.onclick = e => { e.preventDefault(); m.remove(); a.dataset.a === 'upload' ? skUpload() : skEditor(); });
  setTimeout(() => document.addEventListener('click', function f(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener('click', f); } }), 0);
}

async function skillsHome() {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  const view = params.get('view') || 'all';
  SK_LIST = await api('/skills');
  const canCreate = ME.is_admin || (typeof myTeams === 'function' && myTeams('developer').length);
  let sort = 'recent', q = '';
  main.innerHTML = `<div class="sk-head"><div class="row" style="gap:14px"><h1 style="margin:0">Skills</h1>
      <div class="seg"><button class="ghost small ${view === 'all' ? 'on' : ''}" data-v="all">Todas</button><button class="ghost small ${view === 'mine' ? 'on' : ''}" data-v="mine">Minhas</button></div></div>
    <div class="row sk-tools"><input id="sk-q" class="cx-search" placeholder="Procurar" autocomplete="off">
      <button class="ghost" id="sk-sort" title="Ordenar">${icon('chart')}</button>
      ${canCreate ? `<div class="cx-addw"><button id="sk-add" class="sk-dark">${icon('plus')} Adicionar</button></div>` : ''}</div></div>
  <div class="sub">Procedimentos e conhecimento que os agentes da empresa usam. Clique numa skill para ver a visão geral e o conteúdo.</div>
  <div class="card sk-list" id="sk-list"></div>`;
  const draw = () => {
    const me = [ME.name, ME.email, (ME.user || {}).email, (ME.user || {}).name].filter(Boolean);
    let l = SK_LIST.filter(s => (view !== 'mine' || me.includes(s.created_by))
      && (!q || `${s.name} ${s.description}`.toLowerCase().includes(q)));
    l = l.sort((a, b) => sort === 'name' ? a.name.localeCompare(b.name) : String(b.updated_at || '').localeCompare(String(a.updated_at || '')) || a.name.localeCompare(b.name));
    $('#sk-list').innerHTML = l.map(s => `<div class="sk-row" data-n="${esc(s.name)}" tabindex="0">
        <span class="sk-ic">${SK_ICON}</span>
        <span class="sk-txt"><span><b data-noi18n>${esc(s.name)}</b> ${s.team ? `<span class="chip" data-noi18n>${esc(s.team)}</span>` : ''}${s.status === 'deprecated' ? ' <span class="pill warn">descontinuada</span>' : ''}</span>
          <span class="mute small sk-sub">${s.created_by ? `<span>por</span> <span data-noi18n>${esc(s.created_by)}</span> · ` : ''}<span data-noi18n>${esc(s.description || '')}</span></span></span>
        <span class="mute small sk-date">${esc(skDate(s.updated_at))}</span>
        <button class="ghost sk-more" data-n="${esc(s.name)}" title="Mais">⋮</button></div>`).join('')
      || '<div class="empty mute">Nenhuma skill encontrada.</div>';
    main.querySelectorAll('.sk-row').forEach(r => {
      r.onclick = e => { if (!e.target.closest('.sk-more')) skOpen(r.dataset.n); };
      r.onkeydown = e => { if (e.key === 'Enter') skOpen(r.dataset.n); };
    });
    main.querySelectorAll('.sk-more').forEach(b => b.onclick = e => { e.stopPropagation(); skMoreMenu(b, SK_LIST.find(s => s.name === b.dataset.n)); });
  };
  draw();
  $('#sk-q').oninput = e => { q = e.target.value.trim().toLowerCase(); draw(); };
  $('#sk-sort').onclick = () => { sort = sort === 'recent' ? 'name' : 'recent'; toast(sort === 'name' ? 'Ordem: nome' : 'Ordem: atualizadas recentemente'); draw(); };
  main.querySelectorAll('.seg button').forEach(b => b.onclick = () => { location.hash = '#/catalog' + (b.dataset.v === 'mine' ? '?view=mine' : ''); });
  const add = $('#sk-add'); if (add) add.onclick = e => { e.stopPropagation(); skAddMenu(add); };
}

function skMoreMenu(btn, s) {
  document.querySelectorAll('.sk-menu').forEach(m => m.remove());
  const m = document.createElement('div');
  m.className = 'card sk-menu sk-menu-r';
  m.innerHTML = `<a href="#" data-a="dl">${icon('file')} <span>Baixar</span></a>${s.can_edit ? `<a href="#" data-a="up">${icon('plus')} <span>Atualizar com arquivo</span></a><a href="#" data-a="ed">${icon('template')} <span>Editar</span></a>` : ''}
    <a href="#/skills/${encodeURIComponent(s.name)}">${icon('flask')} <span>Abrir no Skill Studio</span></a>`;
  btn.parentElement.style.position = 'relative';
  btn.parentElement.appendChild(m);
  m.querySelectorAll('a[data-a]').forEach(a => a.onclick = async e => {
    e.preventDefault(); m.remove();
    if (a.dataset.a !== 'dl' && s.close) s.close();  // editar/atualizar a partir do popup: fecha o popup antes
    if (a.dataset.a === 'dl') skDownload(s.name);
    if (a.dataset.a === 'up') skUpload(s.name);
    if (a.dataset.a === 'ed') skEditor(await api('/skills/' + encodeURIComponent(s.name)));
  });
  setTimeout(() => document.addEventListener('click', function f(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener('click', f); } }), 0);
}

async function skDownload(name, version) {
  const r = await fetch(`/api/skills/${encodeURIComponent(name)}/download${version ? `?version=${version}` : ''}`, { credentials: 'same-origin' });
  if (!r.ok) { toast(`Não consegui baixar: HTTP ${r.status}`, true); return; }
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(await r.blob()), download: `${name}${version ? `-v${version}` : ''}.zip` });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/* o popup da skill: Visão Geral e Conteúdo (árvore de arquivos, ver formatado ou o código) */
async function skOpen(name, tab = 'overview') {
  const s = await api('/skills/' + encodeURIComponent(name));
  const { box, close } = skModal('<div class="sk-pop"></div>');
  const pop = box.querySelector('.sk-pop');
  let rev = null, file = 'SKILL.md', mode = 'view';
  const filesOf = () => [{ path: 'SKILL.md', content: (rev ? `---\nname: ${s.name}\ndescription: ${rev.description}\n---\n\n${rev.content}` : `---\nname: ${s.name}\ndescription: ${s.description}\n---\n\n${s.content}`) },
    ...((rev ? rev.files : s.files) || [])];
  const tree = files => {
    const dirs = {}; const top = [];
    files.forEach(f => { const i = f.path.indexOf('/'); if (i < 0) top.push(f); else (dirs[f.path.slice(0, i)] = dirs[f.path.slice(0, i)] || []).push(f); });
    const item = (f, label) => `<a href="#" class="sk-f ${f.path === file ? 'on' : ''}" data-p="${esc(f.path)}" data-noi18n>${esc(label)}</a>`;
    return top.map(f => item(f, f.path)).join('') + Object.entries(dirs).map(([d, fs]) => `<div class="sk-dir">${icon('folder')} <span data-noi18n>${esc(d)}</span></div>${fs.map(f => item(f, f.path.slice(d.length + 1))).join('')}`).join('');
  };
  const draw = () => {
    const files = filesOf(), f = files.find(x => x.path === file) || files[0];
    const md = /\.md$/i.test(f.path);
    pop.innerHTML = `<div class="row sk-pop-head"><span class="sk-ic big">${SK_ICON}</span><div style="flex:1;min-width:0"><h2 style="margin:0" data-noi18n>${esc(s.name)}</h2>
        <div class="mute small">${s.created_by ? `<span>por</span> <span data-noi18n>${esc(s.created_by)}</span> · ` : ''}${s.updated_at ? `<span>atualizado</span> ${esc(skDate(s.updated_at))} · ` : ''}v${s.version}</div></div>
      <a class="btn sk-dark" href="#/skills/${encodeURIComponent(s.name)}">${icon('flask')} <span>Testar no Skill Studio</span></a>
      <div class="cx-addw"><button class="ghost sk-pmore" title="Mais">⋮</button></div></div>
      <div class="tabs sk-tabs"><a href="#" data-t="overview" class="${tab === 'overview' ? 'on' : ''}">Visão Geral</a><a href="#" data-t="content" class="${tab === 'content' ? 'on' : ''}"><span>Conteúdo</span> · ${files.length}</a></div>
      ${tab === 'overview' ? `<div class="sk-ov"><div><h3 class="mute">Descrição</h3><div data-noi18n>${esc((rev || s).description || '—')}</div>
          ${s.status === 'deprecated' ? '<div class="mt"><span class="pill warn">descontinuada</span> <span class="small mute">não use em agentes novos</span></div>' : ''}</div>
        <div class="card sk-side"><div class="mute small">Criado por</div><div class="row mt" style="gap:8px">${icon('user')} <span data-noi18n>${esc(s.created_by || '—')}</span></div>
          ${s.team ? `<div class="mute small mt">Time</div><div data-noi18n>${esc(s.team)}</div>` : ''}
          <div class="mute small mt">Versão</div><div>v${s.version} · ${(s.revisions || []).length} <span>no histórico</span></div>
          ${s.last_run ? `<div class="mute small mt">Último teste</div><div>${s.last_run.passed ? '<span class="pill ok">passou</span>' : '<span class="pill bad">falhou</span>'}</div>` : ''}</div></div>`
      : `<div class="row mt" style="justify-content:space-between"><select class="sk-ver" style="width:auto">${(s.revisions || []).map(r => `<option value="${r.version}" ${(rev ? rev.version : s.version) === r.version ? 'selected' : ''}>${esc(s.name)} · v${r.version}${r.version === s.version ? ' · atual' : ''}</option>`).join('')
          || `<option>${esc(s.name)} · atual</option>`}</select>
          <button class="ghost small sk-dlv">${icon('file')} <span>Baixar esta versão</span></button></div>
        <div class="sk-content mt"><nav class="sk-tree">${tree(files)}</nav>
          <div class="sk-view"><div class="row between sk-view-head"><code class="small" data-noi18n>/${esc(f.path)}</code>
            <div class="seg">${md ? `<button class="ghost small ${mode === 'view' ? 'on' : ''}" data-m="view" title="Ver formatado">👁</button>` : ''}<button class="ghost small ${mode === 'code' || !md ? 'on' : ''}" data-m="code" title="Ver código">&lt;/&gt;</button></div></div>
            <div class="sk-body" data-noi18n>${md && mode === 'view' ? mdRender(f.path === 'SKILL.md' ? f.content.replace(/^---[\s\S]*?---\s*/, '') : f.content) : `<pre class="code">${esc(f.content)}</pre>`}</div></div></div>`}`;
    pop.querySelectorAll('.sk-tabs a').forEach(a => a.onclick = e => { e.preventDefault(); tab = a.dataset.t; draw(); });
    pop.querySelectorAll('.sk-f').forEach(a => a.onclick = e => { e.preventDefault(); file = a.dataset.p; draw(); });
    pop.querySelectorAll('[data-m]').forEach(b => b.onclick = () => { mode = b.dataset.m; draw(); });
    const ver = pop.querySelector('.sk-ver');
    if (ver) ver.onchange = () => { const v = Number(ver.value); rev = v === s.version ? null : (s.revisions || []).find(r => r.version === v); file = 'SKILL.md'; draw(); };
    const dlv = pop.querySelector('.sk-dlv'); if (dlv) dlv.onclick = () => skDownload(s.name, rev ? rev.version : undefined);
    pop.querySelector('.sk-pmore').onclick = e => { e.stopPropagation(); skMoreMenu(e.target, { ...s, close }); };
  };
  draw();
}

/* upload: arraste ou escolha vários .md/.zip/.skill; a verificação de segurança roda no envio */
function skUpload(target) {
  const { box, close } = skModal(`<a href="#" class="small sk-back">← <span>Suas Skills</span></a>
    <h2>${target ? 'Atualizar a skill com um arquivo' : 'Fazer upload de uma Skill'}</h2>
    <div class="mute">${target ? `<span>O arquivo vira a versão nova de</span> <b data-noi18n>${esc(target)}</b> <span>(o nome no cabeçalho precisa ser o mesmo).</span>` : 'Adicione skills ao espaço de trabalho da empresa. Uma verificação de segurança é executada ao fazer upload.'}</div>
    <h3>Arquivo da Skill</h3>
    <label class="sk-drop" tabindex="0">${icon('file')}<span>Arraste e solte arquivos de Skill aqui ou procure-os — você pode adicionar vários de uma vez</span>
      <input type="file" id="sk-files" accept=".md,.zip,.skill" multiple hidden></label>
    <ul class="small mute sk-rules"><li>O arquivo .md deve conter o nome e a descrição da skill formatados em YAML</li><li>Arquivo .zip ou .skill deve incluir um arquivo SKILL.md</li></ul>
    <div id="sk-picked" class="small"></div>
    <div class="card sk-scan"><div class="row between"><b>Verificação de segurança</b><span class="mute small" id="sk-scan-st">Executa quando você envia</span></div><div id="sk-scan-out"></div></div>
    <div class="row mt"><button id="sk-send" disabled>Fazer upload</button><button class="ghost" id="sk-cancel">Cancelar</button><span class="mute small" id="sk-hint">Escolha um arquivo para continuar.</span></div>`);
  let picked = [];
  box.querySelector('.sk-back').onclick = e => { e.preventDefault(); close(); };
  box.querySelector('#sk-cancel').onclick = close;
  const input = box.querySelector('#sk-files'), drop = box.querySelector('.sk-drop');
  const take = list => {
    picked = [...list].filter(f => /\.(md|zip|skill)$/i.test(f.name));
    box.querySelector('#sk-picked').innerHTML = picked.map(f => `<span class="chip" data-noi18n>${esc(f.name)} · ${Math.ceil(f.size / 1024)} KB</span>`).join(' ');
    box.querySelector('#sk-send').disabled = !picked.length;
    box.querySelector('#sk-hint').hidden = !!picked.length;
  };
  input.onchange = () => take(input.files);
  ['dragover', 'dragenter'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('on'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('on'); }));
  drop.addEventListener('drop', e => take(e.dataTransfer.files));
  box.querySelector('#sk-send').onclick = e => act(e.target, async () => {
    box.querySelector('#sk-scan-st').textContent = window.t('Verificando…');
    const files = await Promise.all(picked.map(f => new Promise((ok, bad) => {
      const rd = new FileReader(); rd.onload = () => ok({ name: f.name, data: String(rd.result).split(',')[1] }); rd.onerror = bad; rd.readAsDataURL(f);
    })));
    if (target) {  // atualizar: confere o nome antes de gravar
      const chk = (await api('/skills/upload', { method: 'POST', body: { files, check_only: true } })).results;
      const wrong = chk.find(r => r.ok && r.name !== target);
      if (wrong) throw new Error(`o arquivo traz a skill "${wrong.name}", não "${target}"`);
    }
    const res = (await api('/skills/upload', { method: 'POST', body: { files } })).results;
    box.querySelector('#sk-scan-st').textContent = res.every(r => r.ok) ? window.t('Aprovado') : window.t('Com problemas');
    box.querySelector('#sk-scan-out').innerHTML = res.map(r => `<div class="mt small"><b data-noi18n>${esc(r.name || r.file)}</b>
        ${r.ok ? `<span class="pill ok">${r.version ? `v${r.version}` : 'ok'}</span>` : `<span class="pill bad">recusado</span> <span data-noi18n>${esc(r.error)}</span>`}
        ${(r.scan && r.scan.blocked || []).map(x => `<div class="bad-text" data-noi18n>✗ ${esc(x)}</div>`).join('')}
        ${(r.scan && r.scan.warnings || []).map(x => `<div class="mute" data-noi18n>⚠ ${esc(x)}</div>`).join('')}
        ${(r.skipped || []).map(x => `<div class="mute" data-noi18n>– ${esc(x.path)}: ${esc(x.why)}</div>`).join('')}</div>`).join('');
    if (res.some(r => r.ok)) {
      toast(`${res.filter(r => r.ok).length} skill(s) salva(s)`);
      if (location.hash.startsWith('#/catalog')) skillsHome(); else if (location.hash.startsWith('#/skills')) route();
    }
  });
}

/* criar (ou editar) uma skill: nome, descrição, o SKILL.md e outros arquivos */
function skEditor(s) {
  const DRAFT = 'hangar_skill_draft';
  let draft = null;
  if (!s) { try { draft = JSON.parse(localStorage.getItem(DRAFT) || 'null'); } catch (e) { draft = null; } }
  const init = s ? { name: s.name, description: s.description, content: s.content, files: (s.files || []).map(f => ({ ...f })) }
    : draft || { name: '', description: '', content: '', files: [] };
  let files = [{ path: 'SKILL.md', content: init.content }, ...init.files], cur = 0;
  const { box, close } = skModal(`<h2>${s ? 'Editar a skill' : 'Criar uma Skill'}</h2>
    <label>Nome da skill<input id="se-name" placeholder="Relatório semanal de status" value="${esc(init.name)}" ${s ? 'disabled' : ''}></label>
    <label class="mt">Descrição<textarea id="se-desc" rows="2" placeholder="Gerar relatórios de status semanais a partir do trabalho recente. Use quando pedirem atualizações ou resumos de progresso.">${esc(init.description)}</textarea></label>
    <div class="card sk-ed mt"><div class="row between"><div class="row sk-ftabs" style="gap:4px;flex-wrap:wrap"></div><button class="ghost small" id="se-add">${icon('plus')} <span>Adicionar arquivo</span></button></div>
      <textarea id="se-body" class="code" rows="16" placeholder="Resuma meu trabalho recente em três seções: conquistas, obstáculos e próximos passos. Mantenha o tom profissional, mas não rígido..." data-noi18n></textarea></div>
    <div class="row between mt">${s ? '<span></span>' : '<button class="ghost small" id="se-draft">Rascunho</button>'}
      <div class="row"><button class="ghost" id="se-cancel">Cancelar</button><button id="se-go">${s ? 'Salvar versão nova' : 'Criar'}</button></div></div>`);
  const body = box.querySelector('#se-body');
  const tabs = () => {
    box.querySelector('.sk-ftabs').innerHTML = files.map((f, i) => `<button class="ghost small ${i === cur ? 'on' : ''}" data-i="${i}" data-noi18n>${esc(f.path)}${i ? ' <span class="se-rm" data-i="' + i + '">×</span>' : ''}</button>`).join('');
    box.querySelectorAll('.sk-ftabs button').forEach(b => b.onclick = e => {
      files[cur].content = body.value;
      if (e.target.classList.contains('se-rm')) { files.splice(Number(e.target.dataset.i), 1); cur = 0; } else cur = Number(b.dataset.i);
      body.value = files[cur].content; tabs();
    });
  };
  body.value = files[0].content; tabs();
  box.querySelector('#se-add').onclick = () => {
    const p = prompt(window.t('Caminho do arquivo (ex.: references/guia.md)'), 'references/');
    if (!p || !p.trim() || files.some(f => f.path === p.trim())) return;
    files[cur].content = body.value; files.push({ path: p.trim(), content: '' }); cur = files.length - 1; body.value = ''; tabs(); body.focus();
  };
  const collect = () => { files[cur].content = body.value; return { name: box.querySelector('#se-name').value.trim(), description: box.querySelector('#se-desc').value.trim(), content: files[0].content, files: files.slice(1) }; };
  box.querySelector('#se-cancel').onclick = close;
  const dr = box.querySelector('#se-draft');
  if (dr) dr.onclick = () => { try { localStorage.setItem(DRAFT, JSON.stringify(collect())); toast('Rascunho guardado neste navegador'); } catch (e) { toast('Não deu para guardar o rascunho', true); } close(); };
  box.querySelector('#se-go').onclick = e => act(e.target, async () => {
    const v = collect();
    if (!v.name) throw new Error(window.t('Dê um nome à skill'));
    const name = s ? s.name : v.name.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 100);
    const r = await api('/skills/' + encodeURIComponent(name), { method: 'PUT', body: { description: v.description, content: v.content, files: v.files, note: s ? 'editada na tela de Skills' : 'criada na tela de Skills' } });
    if (!s) { try { localStorage.removeItem(DRAFT); } catch (err) { /* sem armazenamento */ } }
    close();
    toast(s ? `Versão v${r.version} salva` : 'Skill criada');
    if (location.hash.startsWith('#/catalog')) { await skillsHome(); skOpen(r.name); } else route();
  });
}
