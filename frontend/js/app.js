/* =========================================================
   app.js — bootstrap: auth gate, sidebar navigation grouped by
   workflow, top bar (page title, global scope, sync), report
   centre, settings page and live-data loading glue.
   Everything else (capacity model, tables, charts, what-if)
   lives in core.js / views/*.js.
   ========================================================= */

let currentVcenterFilter = 'all';
let vcentersCache = [];

/* ---------- Navigation model (workflow-grouped) ----------
   Every entry maps to one page (appState.view) and, for the
   license module, one sub-page (licState().sub). `scope` lists
   which global filters make sense on that page. */
const NAV_GROUPS = [
  { label: 'Tổng quan', items: [
    { id: 'executive', view: 'executive', icon: 'briefcase', label: 'Điều hành', title: 'Tổng quan điều hành',
      desc: 'Tình trạng capacity, rủi ro, đề xuất và nội dung cần phê duyệt theo System Owner của từng cluster.', scope: ['vcenter'] },
    { id: 'dashboard', view: 'dashboard', icon: 'layout-dashboard', label: 'Capacity', title: 'Capacity',
      desc: 'Tổng / đã dùng / còn dư CPU, RAM, Storage và tỷ lệ overcommit theo cluster, host, datastore.', scope: ['vcenter', 'cluster', 'host', 'footprint'] },
  ] },
  { label: 'Giám sát', items: [
    { id: 'compliance', view: 'compliance', icon: 'shield-alert', label: 'Compliance', title: 'Compliance',
      desc: 'Findings theo ngưỡng đã cấu hình: host CPU/RAM, overcommit cluster, VM idle, tắt lâu, over-provision.', scope: ['vcenter', 'cluster', 'host', 'footprint'] },
    { id: 'history', view: 'history', icon: 'trending-up', label: 'Xu hướng & biến động', title: 'Xu hướng & biến động',
      desc: 'Biến động CPU/RAM so với ngưỡng overcommit giữa các snapshot và danh sách VM thêm / bớt / đổi cấu hình.', scope: ['vcenter', 'cluster', 'host'] },
  ] },
  { label: 'Inventory', items: [
    { id: 'inventory', view: 'inventory', icon: 'server', label: 'Host & VM', title: 'Host & VM',
      desc: 'Danh sách ESXi host và VM đã đồng bộ hoặc import, kèm mức sử dụng; xuất Excel theo bộ lọc.', scope: ['vcenter', 'cluster', 'host', 'footprint'] },
    { id: 'lic-hardware', view: 'licensing', sub: 'hardware', icon: 'cpu', label: 'ESXi & phần cứng', title: 'ESXi & phần cứng',
      desc: 'Model, phiên bản ESXi, socket, physical core, logical thread, RAM và số VM của từng host.', scope: ['vcenter', 'cluster'] },
    { id: 'lic-os', view: 'licensing', sub: 'os', icon: 'app-window', label: 'Hệ điều hành', title: 'Hệ điều hành guest',
      desc: 'Phân loại guest OS (Windows Server, Linux, khác) làm cơ sở tính license Windows Server.', scope: ['vcenter', 'cluster'] },
  ] },
  { label: 'Quy hoạch', items: [
    { id: 'whatif', view: 'whatif', icon: 'git-branch', label: 'What-if capacity', title: 'What-if capacity',
      desc: 'Mô phỏng thêm / nâng cấp host, thêm / resize VM, decommission VM idle và kịch bản mẫu.', scope: ['vcenter'] },
    { id: 'sizing', view: 'sizing', icon: 'calculator', label: 'Sizing máy chủ', title: 'Sizing máy chủ',
      desc: 'Từ danh sách VM của dự án mới tính số host vật lý cần mua theo từng cấu hình ứng viên.', scope: ['vcenter'] },
    { id: 'planning', view: 'planning', icon: 'calendar-range', label: 'Quy hoạch tài nguyên', title: 'Quy hoạch tài nguyên',
      desc: 'Dự phóng tăng trưởng CPU/RAM và thời điểm cần bổ sung node máy chủ cho từng cluster.', scope: ['vcenter'] },
  ] },
  { label: 'License', items: [
    { id: 'lic-assess', view: 'licensing', sub: 'assess', icon: 'badge-check', label: 'Đánh giá license', title: 'Đánh giá license',
      desc: 'Nhu cầu license VMware per-core và Windows Server theo cluster, so với entitlement và rule đang áp dụng.', scope: ['vcenter', 'cluster'] },
    { id: 'lic-whatif', view: 'licensing', sub: 'whatif', icon: 'sliders-horizontal', label: 'What-if license', title: 'What-if license',
      desc: 'Ảnh hưởng license khi thêm / giảm / nâng cấp host, thêm VM hoặc chuyển workload.', scope: ['vcenter', 'cluster'] },
    { id: 'lic-snapshots', view: 'licensing', sub: 'snapshots', icon: 'camera', label: 'Snapshot license', title: 'Snapshot license',
      desc: 'Lưu kết quả đánh giá tại một thời điểm để đối chiếu, tái lập hoặc xuất PDF.', scope: ['vcenter', 'cluster'] },
  ] },
  { label: 'Báo cáo & cài đặt', items: [
    { id: 'reports', view: 'reports', icon: 'file-text', label: 'Trung tâm báo cáo', title: 'Trung tâm báo cáo',
      desc: 'Xuất báo cáo PDF, dữ liệu CSV / Excel và cấu hình phạm vi, loại finding, logo cho báo cáo.', scope: ['vcenter'] },
    { id: 'settings', view: 'settings', icon: 'settings', label: 'Cài đặt', title: 'Cài đặt',
      desc: 'Tiêu chí và ngưỡng đánh giá, nguồn dữ liệu vCenter và import thủ công.', scope: [] },
  ] },
];
const NAV_ITEMS = {};
NAV_GROUPS.forEach(g => g.items.forEach(it => { NAV_ITEMS[it.id] = it; }));
let activeNavId = 'dashboard';

function refreshIcons() {
  if (window.lucide && typeof lucide.createIcons === 'function') {
    try { lucide.createIcons(); } catch (err) { /* ignore */ }
  }
}

function renderSidebarNav() {
  const nav = $('#mainNav');
  if (!nav) return;
  nav.innerHTML = NAV_GROUPS.map(g => `
    <div class="nav-group">
      <div class="nav-group-label">${esc(g.label)}</div>
      ${g.items.map(it => `<button type="button" class="nav-item" data-nav="${it.id}" title="${esc(it.desc)}"><i data-lucide="${it.icon}"></i><span>${esc(it.label)}</span></button>`).join('')}
    </div>`).join('');
  nav.addEventListener('click', e => {
    const btn = e.target.closest('[data-nav]');
    if (btn) navigateTo(btn.dataset.nav);
  });
  refreshIcons();
}

/* Which nav entry is current — derived from state so views that change
   state themselves (e.g. "Xem snapshot" switching to the assessment) stay in sync. */
function currentNavId() {
  if (appState.view === 'licensing') {
    const sub = (typeof licState === 'function' ? licState().sub : 'hardware') || 'hardware';
    const hit = Object.values(NAV_ITEMS).find(it => it.view === 'licensing' && it.sub === sub);
    return hit ? hit.id : 'lic-hardware';
  }
  const hit = Object.values(NAV_ITEMS).find(it => it.view === appState.view);
  return hit ? hit.id : 'dashboard';
}

function syncNavState() {
  activeNavId = currentNavId();
  const item = NAV_ITEMS[activeNavId];
  $$('#mainNav .nav-item').forEach(b => {
    const on = b.dataset.nav === activeNavId;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  const group = NAV_GROUPS.find(g => g.items.includes(item));
  const groupEl = $('#pageGroup'); if (groupEl) groupEl.textContent = group ? group.label : '';
  const titleEl = $('#pageTitle'); if (titleEl) titleEl.textContent = item.title;
  const descEl = $('#pageDesc'); if (descEl) descEl.textContent = item.desc;
  document.title = `${item.title} — VMware Capacity Ops Management`;
  updateScopeControls(item);
  const want = `#/${activeNavId}`;
  if (location.hash !== want) history.replaceState(null, '', want);
}

/* Show only the global filters that apply to the current page. */
function updateScopeControls(item) {
  const scope = new Set(item.scope || []);
  $$('#topbarScope [data-scope]').forEach(el => el.classList.toggle('hidden', !scope.has(el.dataset.scope)));
  const bar = $('#topbarScope');
  if (bar) bar.classList.toggle('hidden', scope.size === 0);
}

function closeSidebar() { document.body.classList.remove('nav-open'); }

function navigateTo(id) {
  const item = NAV_ITEMS[id] || NAV_ITEMS.dashboard;
  appState.view = item.view;
  appState.perspective = item.view === 'executive' ? 'executive' : 'technical';
  if (item.view === 'licensing' && typeof licState === 'function') licState().sub = item.sub || 'hardware';
  closeSidebar();
  syncViewSections();
  renderActiveView();
  const main = $('#mainContent');
  if (main) window.scrollTo({ top: 0, behavior: 'auto' });
}

function navIdFromHash() {
  const m = /^#\/([a-z-]+)/.exec(location.hash || '');
  return m && NAV_ITEMS[m[1]] ? m[1] : null;
}

/* Apply the hash route to state without rendering (used before the first data load). */
function applyInitialRoute() {
  const id = navIdFromHash() || 'dashboard';
  const item = NAV_ITEMS[id];
  appState.view = item.view;
  appState.perspective = item.view === 'executive' ? 'executive' : 'technical';
  if (item.view === 'licensing' && typeof licState === 'function') licState().sub = item.sub || 'hardware';
  syncViewSections();
}

function bindNavigation() {
  renderSidebarNav();
  document.addEventListener('click', e => {
    const link = e.target.closest('[data-nav-link]');
    if (link) { e.preventDefault(); navigateTo(link.dataset.navLink); }
  });
  window.addEventListener('hashchange', () => {
    const id = navIdFromHash();
    if (id && id !== activeNavId) navigateTo(id);
  });
  const open = $('#btnSidebarOpen'), close = $('#btnSidebarClose'), scrim = $('#sidebarScrim');
  if (open) open.addEventListener('click', () => document.body.classList.add('nav-open'));
  if (close) close.addEventListener('click', closeSidebar);
  if (scrim) scrim.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSidebar(); });
}

/* ---------- Theme (dark is the product default) ---------- */
function initTheme() {
  let theme = 'dark';
  try { const v = localStorage.getItem('vco.theme'); if (v === 'light' || v === 'dark') theme = v; } catch (err) { /* ignore */ }
  document.documentElement.setAttribute('data-theme', theme);
}

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
function vcenterStatusInfo(v) {
  const cls = v.demo ? 'demo' : (v.lastStatus === 'ok' ? 'ok' : (v.lastError ? 'error' : ''));
  const label = v.demo ? `${v.name} (DEMO)` : v.name;
  const when = v.lastSyncAt ? new Date(v.lastSyncAt).toLocaleString('vi-VN', { hour12: false }) : 'chưa đồng bộ';
  const statusText = v.demo ? 'Demo' : (v.lastStatus === 'ok' ? 'Kết nối tốt' : (v.lastError ? 'Lỗi' : 'Chưa đồng bộ'));
  return { cls, label, when, statusText };
}

function renderVcenterStatusChips() {
  const row = $('#vcenterStatusRow');
  if (row) {
    row.innerHTML = vcentersCache.length
      ? vcentersCache.map(v => {
          const i = vcenterStatusInfo(v);
          return `<span class="vcenter-chip ${i.cls}" title="${esc(i.label)} · ${i.statusText} · đồng bộ ${esc(i.when)}${v.lastError ? ' · ' + esc(v.lastError) : ''}"><span class="dot"></span><span class="vc-name">${esc(v.name)}</span></span>`;
        }).join('')
      : `<span class="vcenter-chip"><span class="dot"></span>Chưa cấu hình vCenter</span>`;
  }
  const tbody = document.querySelector('#vcenterSourceTable tbody');
  if (tbody) {
    tbody.innerHTML = vcentersCache.length
      ? vcentersCache.map(v => {
          const i = vcenterStatusInfo(v);
          const pill = i.cls === 'ok' ? 'ok' : (i.cls === 'error' ? 'crit' : (i.cls === 'demo' ? 'warn' : 'na'));
          return `<tr><td>${esc(v.name)}<span class="sub muted-sub">${esc(v.key || '')}</span></td><td><span class="pill ${pill}">${i.statusText}</span>${v.lastError ? `<div class="muted-sub">${esc(v.lastError)}</div>` : ''}</td><td>${esc(i.when)}</td></tr>`;
        }).join('')
      : '<tr><td colspan="3" class="empty-note">Chưa cấu hình vCenter — dùng import thủ công bên dưới.</td></tr>';
  }
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
  const summary = $('#scopeFilterSummary');
  if (!summary) return;
  const clusters = getSelectedPdfClusters();
  const hosts = getSelectedPdfHosts();
  const parts = [];
  if (clusters) parts.push(`${clusters.length} cluster`);
  if (hosts) parts.push(`${hosts.length} host`);
  summary.textContent = parts.length ? `Đang giới hạn: ${parts.join(', ')}` : 'Toàn bộ cluster trong vCenter đã chọn';
  summary.classList.toggle('is-narrowed', parts.length > 0);

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
  ['#pdfClusterList', '#pdfHostList'].forEach(sel => {
    const list = $(sel);
    if (!list) return;
    list.addEventListener('change', e => {
      if (e.target.classList.contains('pdfClusterCb')) { renderPdfHostList(); updateScopeFilterSummary(); }
      else if (e.target.classList.contains('pdfHostCb')) { updateScopeFilterSummary(); }
    });
  });
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
  const summary = $('#pdfFindingFilterSummary');
  if (!summary) return;
  summary.textContent = checkedCount === all.length ? 'Tất cả loại finding' : `Đang chọn ${checkedCount}/${all.length} loại`;
  summary.classList.toggle('is-narrowed', checkedCount !== all.length);
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
  const cb = $('#criteriaStorageToggle');
  if (cb) cb.checked = !!appState.policy.includeStorage;
}

async function handleStorageCriteriaToggle(e) {
  const enabled = !!e.target.checked;
  appState.policy.includeStorage = enabled;
  try { localStorage.setItem('vco.includeStorage', enabled ? '1' : '0'); } catch (err) { /* ignore */ }
  updateCriteriaSummary();
  refreshAll();
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

/* ---------- Thresholds: applied live by core.js; "Lưu ngưỡng" persists them ---------- */
function setThresholdStatus(message, kind) {
  const el = $('#thresholdStatus');
  if (!el) return;
  el.textContent = message || '';
  el.classList.remove('ok', 'error');
  if (kind) el.classList.add(kind);
}

async function handleSaveThresholds(e) {
  const btn = e.currentTarget;
  rebuildFromRaw(); // normalises the inputs into appState.policy
  refreshAll();
  btn.disabled = true;
  setThresholdStatus('Đang lưu...', null);
  try {
    await api.updateSettings({
      idle_days: appState.policy.idleDays,
      cpu_overcommit_max: appState.policy.cpuOvercommitMax,
      ram_overcommit_max: appState.policy.ramOvercommitMax,
      ht_factor: appState.policy.htFactor,
    });
    setThresholdStatus(`Đã lưu ngưỡng cho toàn hệ thống lúc ${new Date().toLocaleTimeString('vi-VN', { hour12: false })}.`, 'ok');
  } catch (err) {
    setThresholdStatus(`Đang áp dụng cục bộ; không lưu được lên server: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
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

function getPlanningParams() {
  const growthRaw = ($('#planGrowth').value || '').split(',').map(s => parseFloat(s.trim())).filter(n => isFinite(n) && n >= 0);
  if (!growthRaw.length) throw new Error('Tăng trưởng %/quý không hợp lệ (ví dụ: 5,10,20).');
  const num = (sel) => { const v = parseFloat($(sel).value); return isFinite(v) ? v : null; };
  return {
    horizon: $('#planHorizon').value,
    growth: growthRaw.join(','),
    node_cores: num('#planNodeCores'),
    node_ram: num('#planNodeRam'),
    lead: num('#planLead') ?? 3,
    target: num('#planTarget') ?? 80,
  };
}

async function handleExportPdf(view, btn) {
  const originalLabel = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = 'Đang tạo PDF...';
  try {
    const vcenterParam = currentVcenterFilter === 'import' ? '__none__' : currentVcenterFilter;
    const findingTypes = getSelectedPdfFindingTypes();
    const clusters = getSelectedPdfClusters();
    const hosts = getSelectedPdfHosts();
    const months = (typeof historyState !== 'undefined' && historyState.months) ? historyState.months : 12;
    const extra = view === 'planning' ? getPlanningParams() : null;
    await api.downloadPdfReport(view, vcenterParam, findingTypes, clusters, hosts, !!appState.policy.includeStorage, months, extra);
  } catch (err) {
    showValidationBanner([`Không thể xuất báo cáo PDF: ${err.message}`], []);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}

/* License executive summary from the report centre — same scope rules as the License pages. */
async function handleExportLicSummaryPdf(btn) {
  const originalLabel = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = 'Đang tạo PDF...';
  try {
    if (typeof licLoad === 'function') await licLoad(false);
    const sc = licScope();
    await api.licReportPdf(sc.vcenter, sc.cluster, sc.clusters, null);
  } catch (err) {
    showValidationBanner([`Không thể xuất báo cáo license: ${err.message}`], []);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}

async function handleSyncNow() {
  const btn = $('#btnSyncNow');
  const originalLabel = btn.innerHTML;
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
    btn.innerHTML = originalLabel;
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
    refreshAll(); // renders whichever page is active, including Điều hành
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

/* ---------- Bootstrap ---------- */
async function initAppData() {
  applyInitialRoute();
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
  $('#btnExportPdfPlan').addEventListener('click', e => handleExportPdf('planning', e.currentTarget));
  const planQuick = $('#btnExportPdfPlanQuick');
  if (planQuick) planQuick.addEventListener('click', e => handleExportPdf('planning', e.currentTarget));
  const licSummary = $('#btnExportPdfLicSummary');
  if (licSummary) licSummary.addEventListener('click', e => handleExportLicSummaryPdf(e.currentTarget));
  const saveThr = $('#btnSaveThresholds');
  if (saveThr) saveThr.addEventListener('click', handleSaveThresholds);
  bindPdfFindingFilterDropdown();
  bindPdfScopeFilterDropdown();
  bindLogoUploadDropdown();
  bindCriteriaDropdown();
  $('#vcenterSelect').addEventListener('change', e => {
    currentVcenterFilter = e.target.value;
    loadLiveData();
  });
}

async function bootstrap() {
  initTheme();
  bindNavigation();
  bindGlobalInteractions();
  bindTopBarInteractions();
  syncNavState();
  refreshIcons();
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
