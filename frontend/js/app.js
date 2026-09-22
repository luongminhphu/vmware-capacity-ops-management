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

/* ---------- Report scope filter (cluster/host) for PDF export ---------- */
const SCOPE_PRESET_PATTERNS = {
  production: /prod/i,
  uat: /uat/i,
  dr: /(^|[^a-z])dr([^a-z]|$)|disaster/i,
};
const SCOPE_PRESET_LABELS = { production: 'Production', uat: 'UAT', dr: 'DR' };

function getCheckedValues(selector) {
  return Array.from(document.querySelectorAll(selector)).filter(cb => cb.checked).map(cb => cb.value);
}

function getSelectedPdfClusters() {
  const all = Array.from(document.querySelectorAll('.pdfClusterCb'));
  if (!all.length) return null;
  const checked = all.filter(cb => cb.checked).map(cb => cb.value);
  if (checked.length === all.length) return null; // no filter = every cluster
  return checked;
}

function getSelectedPdfHosts() {
  const toggle = $('#pdfHostNarrowToggle');
  if (!toggle || !toggle.checked) return null; // host narrowing disabled = every host in scope
  const all = Array.from(document.querySelectorAll('.pdfHostCb'));
  if (!all.length) return null;
  return all.filter(cb => cb.checked).map(cb => cb.value);
}

function renderPdfHostList() {
  const container = $('#pdfHostList');
  if (!container) return;
  const clusters = getSelectedPdfClusters();
  const hosts = typeof getHostsForClusters === 'function' ? getHostsForClusters(clusters) : [];
  const prevChecked = new Set(getCheckedValues('.pdfHostCb'));
  const keepPrev = prevChecked.size > 0;
  container.innerHTML = hosts.length
    ? hosts.map(h => `<label class="finding-filter-item"><input type="checkbox" class="pdfHostCb" value="${esc(h)}" ${(!keepPrev || prevChecked.has(h)) ? 'checked' : ''}><span>${esc(h)}</span></label>`).join('')
    : '<div class="scope-empty-note">Không có host trong phạm vi đã chọn</div>';
}

function updateScopeFilterSummary() {
  const dropdown = $('#pdfScopeFilterDropdown');
  const summary = dropdown && dropdown.querySelector('summary');
  if (!summary) return;
  const clusters = getSelectedPdfClusters();
  const hosts = getSelectedPdfHosts();
  const parts = [];
  if (clusters) parts.push(`${clusters.length} cluster`);
  if (hosts) parts.push(`${hosts.length} host`);
  summary.textContent = parts.length ? `Phạm vi cluster/host cho PDF (${parts.join(', ')})` : 'Phạm vi cluster/host cho PDF';

  const clusterAllCb = $('#pdfClusterAll');
  const clusterBoxes = Array.from(document.querySelectorAll('.pdfClusterCb'));
  if (clusterAllCb) clusterAllCb.checked = clusterBoxes.length > 0 && clusterBoxes.every(cb => cb.checked);

  $$('.scope-quick-btn').forEach(btn => {
    const preset = btn.dataset.scopeQuick;
    if (preset === 'all') {
      btn.classList.toggle('active', clusters === null);
      return;
    }
    const re = SCOPE_PRESET_PATTERNS[preset];
    const matching = clusterBoxes.filter(cb => re.test(cb.value)).map(cb => cb.value).sort();
    const checked = clusterBoxes.filter(cb => cb.checked).map(cb => cb.value).sort();
    btn.classList.toggle('active', matching.length > 0 && JSON.stringify(matching) === JSON.stringify(checked));
  });
}

function populateScopeFilterPresets() {
  const group = $('#scopeQuickGroup');
  if (!group) return;
  const clusters = typeof getClusterList === 'function' ? getClusterList() : [];
  const existing = new Set(['all']);
  Object.keys(SCOPE_PRESET_PATTERNS).forEach(preset => {
    if (clusters.some(c => SCOPE_PRESET_PATTERNS[preset].test(c)) && !group.querySelector(`[data-scope-quick="${preset}"]`)) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'scope-quick-btn';
      btn.dataset.scopeQuick = preset;
      btn.textContent = SCOPE_PRESET_LABELS[preset];
      group.appendChild(btn);
      btn.addEventListener('click', () => applyScopePreset(preset));
    }
    existing.add(preset);
  });
}

function applyScopePreset(preset) {
  const clusterBoxes = Array.from(document.querySelectorAll('.pdfClusterCb'));
  if (preset === 'all') {
    clusterBoxes.forEach(cb => { cb.checked = true; });
  } else {
    const re = SCOPE_PRESET_PATTERNS[preset];
    clusterBoxes.forEach(cb => { cb.checked = re.test(cb.value); });
  }
  renderPdfHostList();
  updateScopeFilterSummary();
}

function populatePdfScopeFilter() {
  const container = $('#pdfClusterList');
  if (!container) return;
  const clusters = typeof getClusterList === 'function' ? getClusterList() : [];
  const prevChecked = new Set(getCheckedValues('.pdfClusterCb'));
  const keepPrev = prevChecked.size > 0;
  container.innerHTML = clusters.length
    ? clusters.map(c => `<label class="finding-filter-item"><input type="checkbox" class="pdfClusterCb" value="${esc(c)}" ${(!keepPrev || prevChecked.has(c)) ? 'checked' : ''}><span>${esc(c)}</span></label>`).join('')
    : '<div class="scope-empty-note">Chưa có cluster nào</div>';
  populateScopeFilterPresets();
  renderPdfHostList();
  updateScopeFilterSummary();
}

function bindPdfScopeFilterDropdown() {
  const clusterAllCb = $('#pdfClusterAll');
  if (clusterAllCb) {
    clusterAllCb.addEventListener('change', () => {
      document.querySelectorAll('.pdfClusterCb').forEach(cb => { cb.checked = clusterAllCb.checked; });
      renderPdfHostList();
      updateScopeFilterSummary();
    });
  }
  const hostToggle = $('#pdfHostNarrowToggle');
  const hostList = $('#pdfHostList');
  if (hostToggle && hostList) {
    hostToggle.addEventListener('change', () => {
      hostList.classList.toggle('hidden', !hostToggle.checked);
      updateScopeFilterSummary();
    });
  }
  $$('.scope-quick-btn').forEach(btn => btn.addEventListener('click', () => applyScopePreset(btn.dataset.scopeQuick)));
  const dropdown = $('#pdfScopeFilterDropdown');
  if (dropdown) {
    dropdown.addEventListener('change', e => {
      if (e.target.classList.contains('pdfClusterCb')) { renderPdfHostList(); updateScopeFilterSummary(); }
      else if (e.target.classList.contains('pdfHostCb')) { updateScopeFilterSummary(); }
    });
  }
}

function getSelectedPdfFindingTypes() {
  const all = Array.from(document.querySelectorAll('.pdfFindingTypeCb'));
  const checked = all.filter(cb => cb.checked).map(cb => cb.value);
  if (checked.length === all.length) return null; // no filter = every type
  return checked;
}

function updatePdfFindingFilterSummary() {
  const all = Array.from(document.querySelectorAll('.pdfFindingTypeCb'));
  const checkedCount = all.filter(cb => cb.checked).length;
  const dropdown = $('#pdfFindingFilterDropdown');
  const summary = dropdown && dropdown.querySelector('summary');
  if (!summary) return;
  summary.textContent = checkedCount === all.length
    ? 'Lọc findings cho PDF'
    : `Lọc findings cho PDF (${checkedCount}/${all.length})`;
  const allCb = $('#pdfFindingTypeAll');
  if (allCb) allCb.checked = checkedCount === all.length;
}

function bindPdfFindingFilterDropdown() {
  const allCb = $('#pdfFindingTypeAll');
  const typeCbs = Array.from(document.querySelectorAll('.pdfFindingTypeCb'));
  if (allCb) {
    allCb.addEventListener('change', () => {
      typeCbs.forEach(cb => { cb.checked = allCb.checked; });
      updatePdfFindingFilterSummary();
    });
  }
  typeCbs.forEach(cb => cb.addEventListener('change', updatePdfFindingFilterSummary));
  updatePdfFindingFilterSummary();
}

/* ---------- Assessment criteria (Storage on/off) ---------- */
function setCriteriaStatus(message, kind) {
  const el = $('#criteriaStatus');
  if (!el) return;
  el.textContent = message || '';
  el.classList.remove('ok', 'error');
  if (kind) el.classList.add(kind);
}

function updateCriteriaSummary() {
  const dropdown = $('#criteriaFilterDropdown');
  const summary = dropdown && dropdown.querySelector('summary');
  if (!summary) return;
  summary.textContent = appState.policy.includeStorage ? 'Tiêu chí đánh giá (CPU · RAM · Storage)' : 'Tiêu chí đánh giá (CPU · RAM — Storage tắt)';
  const cb = $('#criteriaStorageToggle');
  if (cb) cb.checked = !!appState.policy.includeStorage;
}

async function handleStorageCriteriaToggle(e) {
  const enabled = !!e.target.checked;
  appState.policy.includeStorage = enabled;
  try { localStorage.setItem('vco.includeStorage', enabled ? '1' : '0'); } catch (err) { /* ignore */ }
  updateCriteriaSummary();
  refreshAll();
  if (appState.perspective === 'executive') await renderExecutiveView();
  try {
    await api.updateSettings({ include_storage: enabled });
    setCriteriaStatus(enabled ? 'Đã bật đánh giá Storage (đã lưu cho toàn hệ thống).' : 'Đã tắt đánh giá Storage (đã lưu cho toàn hệ thống).', 'ok');
  } catch (err) {
    setCriteriaStatus(`Áp dụng cục bộ; không lưu được lên server: ${err.message}`, 'error');
  }
}

function bindCriteriaDropdown() {
  const cb = $('#criteriaStorageToggle');
  if (cb) cb.addEventListener('change', handleStorageCriteriaToggle);
  updateCriteriaSummary();
}

/* ---------- Company logo upload (embedded on PDF report covers) ---------- */
function setLogoStatus(message, kind) {
  const el = $('#logoUploadStatus');
  if (!el) return;
  el.textContent = message || '';
  el.classList.remove('ok', 'error');
  if (kind) el.classList.add(kind);
}

function renderLogoPreview(dataUrl) {
  const img = $('#logoUploadImg');
  const emptyLabel = $('#logoUploadEmptyLabel');
  if (!img || !emptyLabel) return;
  if (dataUrl) {
    img.src = dataUrl;
    img.classList.remove('hidden');
    emptyLabel.classList.add('hidden');
  } else {
    img.removeAttribute('src');
    img.classList.add('hidden');
    emptyLabel.classList.remove('hidden');
  }
}

async function loadCompanyLogo() {
  try {
    const res = await api.getLogo();
    renderLogoPreview(res && res.dataUrl ? res.dataUrl : null);
  } catch (err) {
    renderLogoPreview(null);
  }
}

const MAX_LOGO_UPLOAD_BYTES = 2 * 1024 * 1024;

async function handleLogoFileSelected(e) {
  const file = e.target.files && e.target.files[0];
  e.target.value = '';
  if (!file) return;
  if (file.size > MAX_LOGO_UPLOAD_BYTES) {
    setLogoStatus('Kích thước logo vượt quá 2MB cho phép.', 'error');
    return;
  }
  setLogoStatus('Đang tải logo lên...', null);
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('Không đọc được tệp ảnh.'));
      reader.readAsDataURL(file);
    });
    const res = await api.saveLogo(dataUrl, file.name);
    renderLogoPreview(res && res.dataUrl ? res.dataUrl : dataUrl);
    setLogoStatus('Đã lưu logo. Logo sẽ xuất hiện trên báo cáo PDF tiếp theo.', 'ok');
  } catch (err) {
    setLogoStatus(`Tải logo thất bại: ${err.message}`, 'error');
  }
}

async function handleRemoveLogo() {
  setLogoStatus('Đang xóa logo...', null);
  try {
    await api.deleteLogo();
    renderLogoPreview(null);
    setLogoStatus('Đã xóa logo công ty.', 'ok');
  } catch (err) {
    setLogoStatus(`Xóa logo thất bại: ${err.message}`, 'error');
  }
}

function bindLogoUploadDropdown() {
  const input = $('#logoUploadInput');
  const removeBtn = $('#btnRemoveLogo');
  if (input) input.addEventListener('change', handleLogoFileSelected);
  if (removeBtn) removeBtn.addEventListener('click', handleRemoveLogo);
}

async function handleExportPdf(view, btn) {
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Đang tạo PDF...';
  try {
    const vcenterParam = currentVcenterFilter === 'import' ? '__none__' : currentVcenterFilter;
    const findingTypes = getSelectedPdfFindingTypes();
    const clusters = getSelectedPdfClusters();
    const hosts = getSelectedPdfHosts();
    await api.downloadPdfReport(view, vcenterParam, findingTypes, clusters, hosts, !!appState.policy.includeStorage);
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
      if (policy.include_storage !== undefined || policy.includeStorage !== undefined) {
        appState.policy.includeStorage = !!(policy.include_storage ?? policy.includeStorage);
      }
      $('#idleThresholdInput').value = appState.policy.idleDays;
      $('#cpuOvercommitInput').value = appState.policy.cpuOvercommitMax;
      $('#ramOvercommitInput').value = appState.policy.ramOvercommitMax;
      $('#htFactorInput').value = appState.policy.htFactor;
    }
  } catch (err) {
    // Server unreachable — fall back to the last locally chosen criteria.
    try { const v = localStorage.getItem('vco.includeStorage'); if (v !== null) appState.policy.includeStorage = v === '1'; } catch (e) { /* ignore */ }
  }
  updateCriteriaSummary();

  await refreshVcenterList();
  await loadLiveData();
  await loadCompanyLogo();
}

function bindTopBarInteractions() {
  $('#loginForm').addEventListener('submit', handleLoginSubmit);
  $('#btnLogout').addEventListener('click', handleLogout);
  $('#btnSyncNow').addEventListener('click', handleSyncNow);
  $('#btnExportPdfExec').addEventListener('click', e => handleExportPdf('executive', e.currentTarget));
  $('#btnExportPdfTech').addEventListener('click', e => handleExportPdf('technical', e.currentTarget));
  bindPdfFindingFilterDropdown();
  bindPdfScopeFilterDropdown();
  bindLogoUploadDropdown();
  bindCriteriaDropdown();
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
}

bootstrap();
