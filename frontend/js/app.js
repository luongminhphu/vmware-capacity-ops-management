/* =========================================================
   app.js — bootstrap: auth gate, top bar (vCenter selector,
   sync, Executive/Technical toggle), live-data loading glue.
   Everything else (capacity model, tables, charts, what-if)
   lives in core.js / views/executive.js / views/technical.js
   exactly as ported from the original single-file app.
   ========================================================= */

let currentVcenterFilter = 'all';
let vcentersCache = [];

/* ---------- Screen switching ---------- */
function showLoginScreen() {
  $('#loginScreen').classList.remove('hidden');
  $('#appShell').classList.add('hidden');
}
function showAppShell() {
  $('#loginScreen').classList.add('hidden');
  $('#appShell').classList.remove('hidden');
}
window.onApiUnauthorized = () => showLoginScreen();

/* ---------- Login ---------- */
async function handleLoginSubmit(e) {
  e.preventDefault();
  const btn = $('#btnLogin');
  const username = $('#loginUsername').value.trim();
  const password = $('#loginPassword').value;
  $('#loginError').textContent = '';
  btn.disabled = true;
  btn.textContent = 'Đang đăng nhập...';
  try {
    const user = await api.login(username, password);
    $('#currentUserLabel').textContent = user.displayName || user.username;
    showAppShell();
    await initAppData();
  } catch (err) {
    $('#loginError').textContent = err.message || 'Đăng nhập thất bại.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Đăng nhập';
  }
}

async function handleLogout() {
  try { await api.logout(); } catch (err) { /* ignore */ }
  showLoginScreen();
}

/* ---------- vCenter top bar ---------- */
function renderVcenterStatusChips() {
  const row = $('#vcenterStatusRow');
  if (!vcentersCache.length) {
    row.innerHTML = `<span class="vcenter-chip"><span class="dot"></span>Chưa cấu hình vCenter</span>`;
    return;
  }
  row.innerHTML = vcentersCache.map(v => {
    const cls = v.demo ? 'demo' : (v.lastStatus === 'ok' ? 'ok' : (v.lastError ? 'error' : ''));
    const label = v.demo ? `${v.name} (DEMO)` : v.name;
    const when = v.lastSyncAt ? new Date(v.lastSyncAt).toLocaleString('vi-VN') : 'chưa đồng bộ';
    return `<span class="vcenter-chip ${cls}" title="${esc(v.lastError || '')}"><span class="dot"></span>${esc(label)} · ${esc(when)}</span>`;
  }).join('');
}

async function refreshVcenterList() {
  try {
    vcentersCache = await api.listVcenters();
    const sel = $('#vcenterSelect');
    const prev = sel.value || 'all';
    sel.innerHTML = `<option value="all">Tất cả vCenter</option>` +
      vcentersCache.map(v => `<option value="${esc(v.key)}">${esc(v.name)}${v.demo ? ' (DEMO)' : ''}</option>`).join('') +
      `<option value="import">Chỉ dữ liệu import</option>`;
    sel.value = [...sel.options].some(o => o.value === prev) ? prev : 'all';
    currentVcenterFilter = sel.value;
    renderVcenterStatusChips();
  } catch (err) {
    console.error('Không tải được danh sách vCenter', err);
  }
}

async function handleExportPdf(view, btn) {
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Đang tạo PDF...';
  try {
    const vcenterParam = currentVcenterFilter === 'import' ? '__none__' : currentVcenterFilter;
    await api.downloadPdfReport(view, vcenterParam);
  } catch (err) {
    showValidationBanner([`Không thể xuất báo cáo PDF: ${err.message}`], []);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

async function handleSyncNow() {
  const btn = $('#btnSyncNow');
  btn.disabled = true;
  btn.textContent = 'Đang đồng bộ...';
  try {
    if (currentVcenterFilter === 'all' || currentVcenterFilter === 'import') {
      await api.syncAll();
    } else {
      await api.syncVcenter(currentVcenterFilter);
    }
    await refreshVcenterList();
    await loadLiveData();
    showValidationBanner([], [`Đã đồng bộ dữ liệu từ vCenter lúc ${new Date().toLocaleString('vi-VN')}.`]);
  } catch (err) {
    showValidationBanner([`Đồng bộ vCenter thất bại: ${err.message}`], []);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Đồng bộ ngay';
  }
}

/* ---------- Live data loading (replaces/augments file-import flow) ---------- */
async function loadLiveData() {
  try {
    const includeImport = currentVcenterFilter !== 'import' ? true : true;
    const vcenterParam = currentVcenterFilter === 'import' ? 'none' : currentVcenterFilter;
    const params = { vcenter: vcenterParam === 'none' ? 'all' : vcenterParam, include_import: includeImport };
    if (currentVcenterFilter === 'import') {
      // "Chỉ dữ liệu import" — ask for a scope that matches no live vCenter key
      // so only the manual-import snapshot comes back.
      params.vcenter = '__none__';
    }
    const data = await api.getLatestData(params);
    appState.hostsRaw = [];
    appState.vmsRaw = [];
    appState.hosts = data.hosts || [];
    appState.vms = data.vms || [];
    appState.reportTimestamp = new Date().toISOString();
    appState.validation = { errors: [], warnings: [] };
    const footprintNote = data.runMeta && data.runMeta.length
      ? [`Dữ liệu tổng hợp từ: ${data.runMeta.map(r => `${r.vcenterKey === 'import' ? 'Import thủ công' : r.vcenterKey} (${r.finishedAt ? new Date(r.finishedAt).toLocaleString('vi-VN') : 'n/a'})`).join(', ')}.`]
      : [];
    showValidationBanner([], footprintNote);
    refreshAll();
    if (appState.perspective === 'executive') await renderExecutiveView();
  } catch (err) {
    console.error('Không tải được dữ liệu vCenter', err);
    showValidationBanner([`Không tải được dữ liệu từ server: ${err.message}`], []);
  }
}

/* Called after a manual CSV/JSON import so it is also persisted server-side
   and therefore feeds the Executive trend history alongside live polls. */
async function persistImportIfReady() {
  if (!appState.hosts.length && !appState.vms.length) return;
  try {
    await api.importData({ hosts: appState.hosts, vms: appState.vms, label: 'manual-import' });
    await refreshVcenterList();
  } catch (err) {
    console.warn('Không lưu được dữ liệu import lên server (dữ liệu vẫn hiển thị cục bộ):', err.message);
  }
}

/* ---------- Perspective toggle (Executive / Technical) ---------- */
async function setPerspective(mode) {
  appState.perspective = mode;
  $$('#perspectiveToggle button').forEach(b => b.classList.toggle('active', b.dataset.perspective === mode));
  $('#executiveView').classList.toggle('hidden', mode !== 'executive');
  $('#technicalView').classList.toggle('hidden', mode !== 'technical');
  if (mode === 'executive') await renderExecutiveView();
  else refreshAll();
}

/* ---------- Bootstrap ---------- */
async function initAppData() {
  appState.perspective = appState.perspective || 'technical';
  try {
    const policy = await api.getSettings();
    if (policy) {
      appState.policy.idleDays = policy.idle_days ?? policy.idleDays ?? appState.policy.idleDays;
      appState.policy.cpuOvercommitMax = policy.cpu_overcommit_max ?? policy.cpuOvercommitMax ?? appState.policy.cpuOvercommitMax;
      appState.policy.ramOvercommitMax = policy.ram_overcommit_max ?? policy.ramOvercommitMax ?? appState.policy.ramOvercommitMax;
      appState.policy.htFactor = policy.ht_factor ?? policy.htFactor ?? appState.policy.htFactor;
      $('#idleThresholdInput').value = appState.policy.idleDays;
      $('#cpuOvercommitInput').value = appState.policy.cpuOvercommitMax;
      $('#ramOvercommitInput').value = appState.policy.ramOvercommitMax;
      $('#htFactorInput').value = appState.policy.htFactor;
    }
  } catch (err) { /* fall back to client defaults */ }

  await refreshVcenterList();
  await loadLiveData();
}

function bindTopBarInteractions() {
  $('#loginForm').addEventListener('submit', handleLoginSubmit);
  $('#btnLogout').addEventListener('click', handleLogout);
  $('#btnSyncNow').addEventListener('click', handleSyncNow);
  $('#btnExportPdfExec').addEventListener('click', e => handleExportPdf('executive', e.currentTarget));
  $('#btnExportPdfTech').addEventListener('click', e => handleExportPdf('technical', e.currentTarget));
  $('#vcenterSelect').addEventListener('change', e => {
    currentVcenterFilter = e.target.value;
    loadLiveData();
  });
  $$('#perspectiveToggle button').forEach(btn => {
    btn.addEventListener('click', () => setPerspective(btn.dataset.perspective));
  });
}

async function bootstrap() {
  bindGlobalInteractions();
  bindTopBarInteractions();
  try {
    const user = await api.me();
    $('#currentUserLabel').textContent = user.displayName || user.username;
    showAppShell();
    await initAppData();
  } catch (err) {
    showLoginScreen();
  }
  hydrateExportedState();
}

bootstrap();
