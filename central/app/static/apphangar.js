/* ---------- App Hangar (desktop): visão geral e dispositivos conectados (console, só admins) ----------
   O app entra pela própria plataforma (OAuth com loopback) e registra o computador. Aqui o admin vê quem está
   conectado, em que versão, a URL para colar no app, e revoga um dispositivo (a autorização dele cai junto). */
async function appHangarPage(sub) {
  if (!ME.is_admin) { location.replace('#/'); return; }
  if (sub === 'devices') return appDevicesPage();
  const o = await api('/app/admin/overview');
  main.innerHTML = `<h1>App Hangar</h1>
    <div class="sub">O app de desktop conectado a esta plataforma: quem está usando, em que versão e como conectar um computador novo.</div>
    <div class="board">
      ${kpi(fmt(o.devices_active), 'Dispositivos ativos')}
      ${kpi(fmt(o.members_connected), 'Membros conectados')}
      ${kpi(fmt(o.versions.length), 'Versões em uso')}
      ${kpi(o.last_seen_at ? ago(o.last_seen_at) : '—', 'Último sinal de um app')}
    </div>
    <div class="grid g2 mt">
      <div class="card hero"><h2>Conectar o app</h2>
        <div class="mute small">URL da plataforma (cole no app):</div>
        <div class="mt" style="margin-top:6px">${copyable(o.platform_url)}</div>
        <ol class="small mt">
          <li>Instale o Hangar no computador.</li>
          <li>No app, abra <b>Configurações → Plataforma</b> (ou o passo <b>Conectar à plataforma</b> do primeiro uso) e cole a URL acima.</li>
          <li>Clique em <b>Conectar</b>: o navegador abre esta plataforma. Entre com a sua conta (SSO ou usuário e senha) e autorize o app.</li>
          <li>O navegador devolve a conexão ao app, que registra o computador. Ele aparece em <a href="#/app/devices">Dispositivos</a>.</li>
        </ol>
        <div class="mute small mt">Para padronizar na empresa, distribua pela política do Windows (chave <code class="inline">HKLM\\Software\\Policies\\Hangar</code>):</div>
        <ul class="mute small"><li><code class="inline">PlatformUrl</code> — a URL acima</li>
          <li><code class="inline">Mode</code> — <code class="inline">off | optional | required</code></li>
          <li>Com <code class="inline">required</code>, o app só cria sessões conectado à plataforma.</li></ul>
      </div>
      <div class="card"><h2>Versões em uso</h2>
        <table><tr><th>Versão</th><th>Dispositivos ativos</th></tr>
        ${o.versions.map(v => `<tr><td data-noi18n><code class="inline">${esc(v.version)}</code></td><td>${fmt(v.count)}</td></tr>`).join('')
          || '<tr><td colspan="2" class="empty">Nenhum app conectado ainda.</td></tr>'}</table>
        <div class="mute small mt">Revogar um dispositivo, desligar a pessoa ou revogar a autorização do app em <a href="#/keys">Chaves de API → Apps conectados</a> desconecta o app na hora.</div>
      </div>
    </div>`;
}

let APP_DEV = { q: '', revoked: false };  // o filtro da lista (vale enquanto a página estiver aberta)
async function appDevicesPage() {
  const list = await api('/app/admin/devices');
  main.innerHTML = `<h1>Dispositivos</h1>
    <div class="sub">Os computadores com o app Hangar conectado. Revogar desconecta o app na hora (a autorização dele cai junto) e impede que este computador volte a se registrar para a mesma pessoa.</div>
    <div class="row"><input id="ad-q" class="input" type="search" placeholder="Filtrar por pessoa, computador, sistema ou versão" value="${esc(APP_DEV.q)}" style="max-width:360px">
      <label class="small"><input type="checkbox" id="ad-rev" ${APP_DEV.revoked ? 'checked' : ''}> Mostrar revogados</label></div>
    <div class="card mt scroll"><table><thead><tr><th>Pessoa</th><th>Computador</th><th>App</th><th>Status</th><th>Registrado</th><th>Último sinal</th><th></th></tr></thead>
    <tbody id="ad-rows"></tbody></table></div>`;
  const by = d => !d.revoked_by ? '' : d.revoked_by.startsWith('sistema:') ? d.revoked_by.slice(8) : `por ${d.revoked_by}`;
  const draw = () => {
    const q = APP_DEV.q.toLowerCase();
    const rows = list.filter(d => (APP_DEV.revoked || d.status === 'active')
      && (!q || [d.user.email, d.user.name, d.name, d.os, d.os_version, d.app_version].join(' ').toLowerCase().includes(q)));
    $('#ad-rows').innerHTML = rows.map(d => `<tr class="${d.status === 'revoked' ? 'mute' : ''}">
      <td><b data-noi18n>${esc(d.user.name || d.user.email)}</b><div class="small mute" data-noi18n>${esc(d.user.email)}</div></td>
      <td><span data-noi18n>${esc(d.name)}</span><div class="small mute" data-noi18n>${esc([d.os, d.os_version].filter(Boolean).join(' '))}</div></td>
      <td data-noi18n><code class="inline">${esc(d.app_version || '?')}</code>${d.channel ? ` <span class="pill">${esc(d.channel)}</span>` : ''}</td>
      <td>${d.status === 'active' ? '<span class="pill ok">ativo</span>' : `<span class="pill bad">revogado</span><div class="small mute">${esc(by(d))}</div>`}</td>
      <td class="mute">${ago(d.created_at)}</td><td class="mute">${d.last_seen_at ? ago(d.last_seen_at) : 'nunca'}</td>
      <td>${d.status === 'active' ? `<button class="ghost ad-rev" data-id="${d.id}">Revogar</button>` : ''}</td></tr>`).join('')
      || `<tr><td colspan="7" class="empty">${list.length ? 'Nenhum dispositivo com esse filtro.' : 'Nenhum app conectado ainda. Veja o passo a passo em <a href="#/app">Visão geral</a>.'}</td></tr>`;
    document.querySelectorAll('.ad-rev').forEach(b => b.onclick = e => confirm('Revogar este dispositivo? O app sai na hora e este computador não volta a se registrar para esta pessoa.') &&
      act(e.target, () => api(`/app/admin/devices/${b.dataset.id}/revoke`, { method: 'POST' }).then(appDevicesPage), 'Dispositivo revogado'));
  };
  $('#ad-q').oninput = e => { APP_DEV.q = e.target.value.trim(); draw(); };
  $('#ad-rev').onchange = e => { APP_DEV.revoked = e.target.checked; draw(); };
  draw();
}
