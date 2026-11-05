/* =========================================================
   License & Inventory — Inventory & License Assessment module.
   Sub-views: ESXi & phần cứng · Phân loại OS · License assessment ·
   What-if license · Snapshot. All math runs on the backend
   (backend/app/licensing/*); this file only renders and edits inputs
   (OS overrides, rule versions, entitlements, what-if scenarios).
   Scope follows the global vCenter / cluster filters.
   ========================================================= */

const LIC_CATEGORY_LABEL = { windows_server: 'Windows Server', windows_client: 'Windows Client', linux: 'Linux', appliance: 'Appliance', other_unknown: 'Other / Unknown' };
const LIC_CONF_LABEL = { confirmed: 'Đã xác nhận', high: 'Cao', medium: 'Trung bình', low: 'Thấp', none: 'Không có' };
const LIC_SOURCE_LABEL = { manual: 'Override thủ công', tools: 'VMware Tools', config: 'Cấu hình VM', name: 'Tên VM', none: '—' };
const LIC_STATUS = {
  ok: { cls: 'ok', label: 'Đủ license' }, shortfall: { cls: 'crit', label: 'Thiếu hụt' },
  insufficient_data: { cls: 'warn', label: 'Chưa đủ dữ liệu' }, no_entitlement: { cls: 'na', label: 'Chưa nhập entitlement' },
};
const LIC_KIND_LABEL = { owned: 'Đang sở hữu', none: 'Chưa có (chưa mua)' };
const LIC_RULE_STATUS = { needs_verification: { cls: 'warn', label: 'Chưa xác minh' }, verified: { cls: 'ok', label: 'Đã xác minh' }, retired: { cls: 'na', label: 'Ngừng dùng' } };
const LIC_SCENARIOS = [
  { type: 'add_host', label: 'Thêm host' }, { type: 'remove_host', label: 'Giảm host' }, { type: 'resize_host', label: 'Nâng cấp host' },
  { type: 'add_vms', label: 'Thêm VM' }, { type: 'move_vms', label: 'Chuyển workload' },
];

function licState() {
  if (!appState.licensing) {
    appState.licensing = {
      sub: 'hardware', inv: null, assess: null, rules: null, ents: null, snaps: null, bound: false, loading: false,
      clusters: null, scopeClusters: licLoadScopePref(),
      filters: { category: 'all', confidence: 'all', source: 'all', cluster: 'all', q: '' },
      hwOpen: {}, editing: null, entEditing: null, ruleEditing: null,
      whatif: { type: 'remove_host', form: {}, result: null, busy: false },
      viewingSnapshot: null, lastScopeKey: null,
    };
  }
  return appState.licensing;
}

const licNum = (v, d = 0) => (v === null || v === undefined || !isFinite(v)) ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
const licMoney = (v, cur) => (v === null || v === undefined || !isFinite(v)) ? '—' : `${Number(v).toLocaleString('vi-VN', { maximumFractionDigits: 0 })}${cur && cur !== 'mixed' ? ' ' + cur : ''}`;
const licPill = (status) => { const s = LIC_STATUS[status] || { cls: 'na', label: status || '—' }; return `<span class="pill ${s.cls}">${esc(s.label)}</span>`; };
const licRulePill = (status) => { const s = LIC_RULE_STATUS[status] || { cls: 'na', label: status }; return `<span class="pill ${s.cls}">${esc(s.label)}</span>`; };
const licDate = (iso) => { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? esc(iso) : d.toLocaleString('vi-VN', { hour12: false }); };
const licBool = (v) => v === true ? 'Bật' : v === false ? 'Tắt' : '—';
const licRange = (a, b) => (a === b || b === null || b === undefined) ? licNum(a) : `${licNum(a)} – ${licNum(b)}`;

/* Purchase scope: a set of "vcenterKey::cluster" keys chosen in the License tab
   (persisted per browser) so a first buying round can cover only some
   production clusters. The global cluster filter, when set, narrows further. */
const LIC_SCOPE_PREF = 'vco.lic.scopeClusters';
function licLoadScopePref() { try { const v = JSON.parse(localStorage.getItem(LIC_SCOPE_PREF) || '[]'); return Array.isArray(v) ? v : []; } catch { return []; } }
function licSaveScopePref(arr) { try { localStorage.setItem(LIC_SCOPE_PREF, JSON.stringify(arr || [])); } catch { /* ignore */ } }

function licKeyLabel(k) { const [vc, cl] = String(k).split('::'); return cl ? `${cl} (${vc})` : k; }

function licScope() {
  const st = licState();
  const vc = (typeof currentVcenterFilter !== 'undefined' && currentVcenterFilter && currentVcenterFilter !== 'import') ? currentVcenterFilter : 'all';
  const cl = appState.filters?.cluster || 'all';
  // only keep selected keys that exist in the current vCenter scope
  const avail = new Set((st.clusters || []).map(c => c.key));
  // global cluster filter wins: when it is set, the chip selection is paused (not combined)
  const clusters = cl !== 'all' ? [] : (st.scopeClusters || []).filter(k => !st.clusters || avail.has(k));
  return { vcenter: vc, cluster: cl, clusters };
}

function renderLicScopeBar() {
  const st = licState();
  const bar = $('#licScopeBar');
  if (!bar) return;
  const list = st.clusters || [];
  if (!list.length) { bar.innerHTML = ''; return; }
  const sc = licScope();
  const globalCl = sc.cluster !== 'all' ? sc.cluster : null;
  const sel = new Set(sc.clusters);
  const all = sel.size === 0;
  const chips = list.map(c => {
    const on = all || sel.has(c.key);
    const dis = globalCl && c.cluster !== globalCl;
    return `<button type="button" class="lic-chip${on && !dis ? ' on' : ''}${dis ? ' disabled' : ''}" data-lic-scope="${esc(c.key)}" title="${esc(c.vcenterKey || 'import')} · ${c.hostCount} host · ${licNum(c.physicalCores)} core · ${c.vmCount} VM">${esc(c.cluster)} <span class="lic-chip-sub">${esc(c.vcenterKey || 'import')} · ${c.hostCount}H · ${licNum(c.physicalCores)}C</span></button>`;
  }).join('');
  const inScope = globalCl ? list.filter(c => c.cluster === globalCl).length : (all ? list.length : list.filter(c => sel.has(c.key)).length);
  const note = globalCl
    ? `Bộ lọc cluster toàn cục đang chọn <b>${esc(globalCl)}</b> — phạm vi license theo cluster đó. Đặt bộ lọc về "Tất cả cluster" để chọn nhiều cluster ở đây.`
    : (all ? `Đang tính cho toàn bộ ${list.length} cluster. Bấm vào cluster để giới hạn phạm vi mua license (ví dụ chỉ production trước).`
           : `Phạm vi mua đợt này: <b>${inScope}/${list.length} cluster</b> — ${list.length - inScope} cluster còn lại chưa tính license; báo cáo và snapshot ghi rõ phạm vi này.`);
  bar.innerHTML = `<span class="lic-scope-label">Phạm vi cluster</span>
    <button type="button" class="lic-chip${all && !globalCl ? ' on' : ''}${globalCl ? ' disabled' : ''}" data-lic-scope="__all__">Tất cả</button>${chips}
    <span class="lic-scope-note${!all && !globalCl ? ' partial' : ''}">${note}</span>`;
}

function licSetStatus(msg, isError = false) {
  const el = $('#licStatus');
  if (!el) return;
  el.classList.toggle('hidden', !msg);
  el.textContent = msg || '';
  el.style.color = isError ? 'var(--bad)' : '';
}

/* ---------- Data loading ---------- */
async function licLoad(force = false) {
  const st = licState();
  const vc0 = licScope().vcenter;
  if (force || !st.clusters || st.clustersVc !== vc0) {
    try { st.clusters = await api.licClusters(vc0); st.clustersVc = vc0; } catch (e) { st.clusters = st.clusters || []; }
  }
  const sc = licScope();
  const key = `${sc.vcenter}|${sc.cluster}|${sc.clusters.join(',')}`;
  renderLicScopeBar();
  if (!force && st.inv && st.lastScopeKey === key) return;
  st.loading = true;
  licSetStatus('Đang tải inventory và tính toán license…');
  try {
    const [inv, assess, rules, ents, snaps] = await Promise.all([
      api.licInventory(sc.vcenter, sc.cluster, sc.clusters), api.licAssess(sc.vcenter, sc.cluster, sc.clusters),
      api.licRules(), api.licEntitlements(), api.licSnapshots(),
    ]);
    st.inv = inv; st.assess = assess; st.rules = rules; st.ents = ents.items; st.entMeta = ents; st.snaps = snaps;
    st.lastScopeKey = key; st.viewingSnapshot = null;
    licSetStatus('');
  } catch (e) {
    licSetStatus(`Không tải được dữ liệu license: ${e.message}`, true);
  } finally {
    st.loading = false;
  }
  const pill = $('#licScopePill');
  if (pill) {
    const n = sc.clusters.length;
    pill.textContent = `${sc.vcenter === 'all' ? 'Tất cả vCenter' : sc.vcenter}${sc.cluster !== 'all' ? ' · ' + sc.cluster : (n ? ` · ${n}/${(st.clusters || []).length} cluster` : ' · tất cả cluster')}`;
    pill.className = `pill ${sc.cluster !== 'all' || n ? 'warn' : 'na'}`;
  }
}

/* ---------- Binding ---------- */
function bindLicensingView() {
  const st = licState();
  if (st.bound) return;
  st.bound = true;
  // Sub-pages (hardware / os / assess / whatif / snapshots) are chosen from the
  // sidebar; navigateTo() sets st.sub before calling renderLicensingView().
  $('#btnLicRefresh').addEventListener('click', async () => { await licLoad(true); renderLicBody(); });
  $('#licScopeBar').addEventListener('click', async (e) => {
    const chip = e.target.closest('[data-lic-scope]');
    if (!chip || chip.classList.contains('disabled')) return;
    const k = chip.dataset.licScope;
    const all = (st.clusters || []).map(c => c.key);
    if (k === '__all__') st.scopeClusters = [];
    else {
      let cur = st.scopeClusters.length ? st.scopeClusters.filter(x => all.includes(x)) : all.slice();
      cur = cur.includes(k) ? cur.filter(x => x !== k) : [...cur, k];
      if (!cur.length) { licSetStatus('Cần ít nhất một cluster trong phạm vi.', true); return; }
      st.scopeClusters = cur.length === all.length ? [] : cur;
    }
    licSaveScopePref(st.scopeClusters);
    st.whatif.result = null;
    await licLoad(false); renderLicBody();
  });
  // Delegated actions inside the body
  $('#licBody').addEventListener('click', onLicBodyClick);
  $('#licBody').addEventListener('change', onLicBodyChange);
  $('#licBody').addEventListener('input', (e) => {
    if (e.target.id === 'licOsSearch') { st.filters.q = e.target.value; renderLicOsTable(); }
  });
}

async function renderLicensingView() {
  bindLicensingView();
  await licLoad(false);
  renderLicBody();
}
window.renderLicensingView = renderLicensingView;

function renderLicBody() {
  const st = licState();
  const body = $('#licBody');
  if (!st.inv || !st.assess) { body.innerHTML = `<div class="empty-note">${st.loading ? 'Đang tải dữ liệu…' : 'Chưa có dữ liệu inventory. Hãy đồng bộ vCenter hoặc import dữ liệu trước.'}</div>`; return; }
  if (st.sub === 'hardware') body.innerHTML = renderLicHardware();
  else if (st.sub === 'os') { body.innerHTML = renderLicOs(); renderLicOsTable(); }
  else if (st.sub === 'assess') body.innerHTML = renderLicAssess();
  else if (st.sub === 'whatif') body.innerHTML = renderLicWhatif();
  else if (st.sub === 'snapshots') body.innerHTML = renderLicSnapshots();
}

/* =========================================================
   1. ESXi & phần cứng
   ========================================================= */
function renderLicHardware() {
  const st = licState();
  const inv = st.inv, t = inv.totals;
  const ratio = t.physicalCores > 0 ? (t.vcpuAllocated / t.physicalCores) : NaN;
  const kpis = [
    ['ESXi host', licNum(t.hosts), `${Object.keys(inv.distribution.model).length} model · ${Object.keys(inv.distribution.esxiVersion).length} phiên bản ESXi`],
    ['CPU socket', licNum(t.sockets), 'Số CPU vật lý — cơ sở mức sàn 16 core/CPU (VMware) và 8 core/CPU (Windows Server)'],
    ['Physical core', licNum(t.physicalCores), 'Core vật lý — đơn vị tính license, KHÔNG phải vCPU'],
    ['Logical thread', licNum(t.logicalThreads), 'Hyper-thread — không dùng để tính license'],
    ['vCPU đã cấp', licNum(t.vcpuAllocated), `Tỷ lệ vCPU : physical core = ${isFinite(ratio) ? licNum(ratio, 2) : '—'}`],
    ['VM', licNum(t.vms), 'Trong phạm vi lọc'],
  ];
  const distChips = (obj, label) => `<div class="lic-dist"><span class="lic-dist-label">${label}</span>${Object.entries(obj).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<span class="pill na">${esc(k)} · ${v}</span>`).join('')}</div>`;

  // Group hosts: vCenter → cluster
  const groups = {};
  inv.hosts.forEach(h => {
    const k = `${h.vcenterKey || 'import'}||${h.cluster || '—'}`;
    (groups[k] = groups[k] || { vcenterKey: h.vcenterKey || 'import', cluster: h.cluster || '—', hosts: [] }).hosts.push(h);
  });
  const rows = Object.values(groups).sort((a, b) => (a.vcenterKey + a.cluster).localeCompare(b.vcenterKey + b.cluster)).map(g => {
    const key = `${g.vcenterKey}||${g.cluster}`;
    const open = !!st.hwOpen[key];
    const sum = (f) => g.hosts.reduce((s, h) => s + (Number(h[f]) || 0), 0);
    const models = [...new Set(g.hosts.map(h => h.model || '—'))].join(', ');
    const versions = [...new Set(g.hosts.map(h => h.esxiVersion || '—'))].join(', ');
    const ha = g.hosts.some(h => h.clusterHa), drs = g.hosts.some(h => h.clusterDrs);
    const head = `<tr class="lic-cluster-row" data-hw-toggle="${esc(key)}">
      <td><span class="lic-caret">${open ? '▾' : '▸'}</span> <b>${esc(g.cluster)}</b><div class="muted-sub">${esc(g.vcenterKey)}</div></td>
      <td>${esc(models)}</td><td>${esc(versions)}</td>
      <td class="num">${g.hosts.length}</td><td class="num">${licNum(sum('sockets'))}</td><td class="num"><b>${licNum(sum('cores'))}</b></td>
      <td class="num">${licNum(sum('threads'))}</td><td class="num">${licNum(sum('ramGB'))}</td><td class="num">${licNum(sum('vmCount'))}</td>
      <td>${ha ? '<span class="pill ok">HA</span>' : '<span class="pill na">HA tắt</span>'} ${drs ? '<span class="pill ok">DRS</span>' : '<span class="pill na">DRS tắt</span>'}</td>
    </tr>`;
    const hostRows = open ? g.hosts.map(h => `<tr class="lic-host-row">
      <td class="lic-indent">${esc(h.host)}${h.flags?.length ? ` <span class="pill warn" title="${esc(h.flags.join(', '))}">giả định</span>` : ''}</td>
      <td>${esc(h.vendor || '')} ${esc(h.model || '—')}<div class="muted-sub">${esc(h.cpuModel || '')}</div></td>
      <td>${esc(h.esxiVersion || '—')}${h.esxiBuild ? `<div class="muted-sub">build ${esc(h.esxiBuild)}</div>` : ''}</td>
      <td class="num">1</td><td class="num">${licNum(h.sockets)}</td><td class="num"><b>${licNum(h.cores)}</b></td>
      <td class="num">${licNum(h.threads)}</td><td class="num">${licNum(h.ramGB)}</td><td class="num">${licNum(h.vmCount)}</td>
      <td><span class="muted-sub">WS lic. core ${licNum(h.wsLicensableCores)}${h.wsFloorApplied ? ' (sàn)' : ''} · VMware core ${licNum(h.vmwareCores)}${h.vmwareFloorApplied ? ' (sàn)' : ''}</span></td>
    </tr>`).join('') : '';
    return head + hostRows;
  }).join('');

  return `
    <div class="kpi-grid lic-kpi-6">${kpis.map(([l, v, s]) => `<div class="kpi-card"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${s}</div></div>`).join('')}</div>
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title">ESXi theo vCenter / cluster / host</div><p class="panel-sub">Bấm vào dòng cluster để xem từng host. Physical core là số core vật lý của CPU; logical thread là số luồng khi bật Hyper-Threading; vCPU là tài nguyên ảo cấp cho VM — chỉ physical core được dùng để tính license.</p></div>
        <div class="panel-toolbar">
          <button class="btn btn-secondary btn-sm" data-lic-action="hw-expand" type="button">Mở tất cả</button>
          <button class="btn btn-secondary btn-sm" data-lic-action="hw-collapse" type="button">Thu gọn</button>
          <button class="btn btn-secondary btn-sm" data-lic-action="export-hw" type="button">Xuất Excel</button>
        </div>
      </div>
      ${distChips(inv.distribution.model, 'Model')}${distChips(inv.distribution.esxiVersion, 'ESXi')}
      <div class="table-scroll"><table class="data-table lic-table">
        <thead><tr><th>Cluster / Host</th><th>Model</th><th>ESXi</th><th class="num">Host</th><th class="num">Socket</th><th class="num">Physical core</th><th class="num">Logical thread</th><th class="num">RAM GB</th><th class="num">VM</th><th>HA / DRS · license core</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="10" class="empty-note">Không có host trong phạm vi.</td></tr>'}</tbody>
      </table></div>
      <div class="row-count">${inv.hosts.length} host · ${Object.keys(groups).length} cluster</div>
    </section>`;
}

/* =========================================================
   2. Phân loại OS
   ========================================================= */
function renderLicOs() {
  const st = licState();
  const s = st.inv.summary;
  const cats = Object.entries(s.byCategory);
  const clusters = [...new Set(st.inv.vms.map(v => v.cluster).filter(Boolean))].sort();
  const opt = (obj, cur, allLabel) => `<option value="all"${cur === 'all' ? ' selected' : ''}>${allLabel}</option>` + Object.entries(obj).map(([k, l]) => `<option value="${k}"${cur === k ? ' selected' : ''}>${esc(l)}</option>`).join('');
  return `
    <div class="kpi-grid lic-kpi-5">${cats.map(([k, b]) => `<div class="kpi-card lic-cat-card" data-lic-cat="${k}" title="Lọc theo ${LIC_CATEGORY_LABEL[k]}"><div class="label">${LIC_CATEGORY_LABEL[k]}</div><div class="value">${b.count}</div><div class="sub">${b.poweredOn} đang bật · ${licNum(b.vcpu)} vCPU · ${licNum(b.ramGB)} GB</div></div>`).join('')}</div>
    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title">VM inventory &amp; phân loại guest OS</div>
          <p class="panel-sub">Nguồn bằng chứng theo thứ tự ưu tiên: Override thủ công → VMware Tools (OS thực tế guest báo về) → Cấu hình VM (guestId đặt lúc tạo, có thể lỗi thời) → Tên VM. Edition Windows Server không lấy được từ vCenter, chỉ có khi override. Độ tin cậy: ${Object.entries(s.byConfidence).map(([k, v]) => `${LIC_CONF_LABEL[k]} ${v}`).join(' · ')}.</p></div>
        <div class="panel-toolbar">
          <select id="licOsCat" data-lic-filter="category">${opt(LIC_CATEGORY_LABEL, st.filters.category, 'Tất cả phân loại')}</select>
          <select id="licOsConf" data-lic-filter="confidence">${opt(LIC_CONF_LABEL, st.filters.confidence, 'Mọi độ tin cậy')}</select>
          <select id="licOsSrc" data-lic-filter="source">${opt(LIC_SOURCE_LABEL, st.filters.source, 'Mọi nguồn')}</select>
          <select id="licOsCluster" data-lic-filter="cluster"><option value="all">Tất cả cluster</option>${clusters.map(c => `<option value="${esc(c)}"${st.filters.cluster === c ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>
          <input id="licOsSearch" placeholder="Tìm VM / OS / host…" value="${esc(st.filters.q)}">
          <button class="btn btn-secondary btn-sm" data-lic-action="export-os" type="button">Xuất Excel</button>
        </div>
      </div>
      <div id="licOverrideEditor"></div>
      <div class="table-scroll"><table class="data-table lic-table" id="licOsTable">
        <thead><tr><th>VM</th><th>Cluster / Host</th><th>Trạng thái</th><th class="num">vCPU</th><th class="num">RAM GB</th><th>Phân loại</th><th>OS version</th><th>Edition</th><th>Nguồn</th><th>Độ tin cậy</th><th>Ghi chú</th><th></th></tr></thead>
        <tbody></tbody></table></div>
      <div class="row-count" id="licOsFoot"></div>
    </section>`;
}

function licFilteredVms() {
  const st = licState();
  const f = st.filters, q = (f.q || '').toLowerCase();
  return st.inv.vms.filter(v => {
    if (f.category !== 'all' && v.os.category !== f.category) return false;
    if (f.confidence !== 'all' && v.os.confidence !== f.confidence) return false;
    if (f.source !== 'all' && v.os.source !== f.source) return false;
    if (f.cluster !== 'all' && v.cluster !== f.cluster) return false;
    if (q) {
      const hay = `${v.vm} ${v.host} ${v.cluster} ${v.os.osVersion || ''} ${v.os.evidence?.guestFullName || ''} ${v.os.evidence?.toolsGuestFullName || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function renderLicOsTable() {
  const tb = $('#licOsTable tbody');
  if (!tb) return;
  const rows = licFilteredVms();
  const confCls = { confirmed: 'ok', high: 'ok', medium: 'warn', low: 'bad', none: 'na' };
  const catCls = { windows_server: 'crit', windows_client: 'warn', linux: 'ok', appliance: 'na', other_unknown: 'bad' };
  tb.innerHTML = rows.slice(0, 1500).map((v, i) => {
    const o = v.os;
    const ev = o.evidence || {};
    const title = `guestId: ${ev.guestId || '—'}\nconfig: ${ev.guestFullName || '—'}\ntools: ${ev.toolsGuestFullName || '—'} (${ev.toolsStatus || '—'})\nhostname: ${ev.guestHostname || '—'}`;
    return `<tr>
      <td><b>${esc(v.vm)}</b>${o.overridden ? ' <span class="pill ok" title="Override thủ công">override</span>' : ''}</td>
      <td>${esc(v.cluster || '—')}<div class="muted-sub">${esc(v.host || '—')}</div></td>
      <td>${v.isPoweredOn ? '<span class="pill ok">Bật</span>' : '<span class="pill na">Tắt</span>'}</td>
      <td class="num">${licNum(v.vcpu)}</td><td class="num">${licNum(v.ramGB)}</td>
      <td><span class="pill ${catCls[o.category] || 'na'}">${esc(o.categoryLabel)}</span></td>
      <td title="${esc(title)}">${esc(o.osVersion || '—')}</td>
      <td>${esc(o.osEdition || '—')}</td>
      <td>${esc(LIC_SOURCE_LABEL[o.source] || o.source)}</td>
      <td><span class="pill ${confCls[o.confidence] || 'na'}">${esc(LIC_CONF_LABEL[o.confidence] || o.confidence)}</span></td>
      <td class="lic-note">${esc(o.note || '')}</td>
      <td><button class="btn btn-secondary btn-sm" data-lic-action="override" data-idx="${st_idx(v)}" type="button">Override</button></td>
    </tr>`;
  }).join('') || '<tr><td colspan="12" class="empty-note">Không có VM khớp bộ lọc.</td></tr>';
  $('#licOsFoot').textContent = `${rows.length} / ${licState().inv.vms.length} VM${rows.length > 1500 ? ' (hiển thị 1500 dòng đầu — thu hẹp bộ lọc hoặc xuất Excel)' : ''}`;
}
function st_idx(v) { return licState().inv.vms.indexOf(v); }

function renderLicOverrideEditor(vm) {
  const st = licState();
  const box = $('#licOverrideEditor');
  if (!box) return;
  if (!vm) { box.innerHTML = ''; return; }
  const o = vm.os;
  const existing = st.overrides?.find(x => x.vcenterKey === (vm.vcenterKey || null) && ((vm.instanceUuid && x.instanceUuid === vm.instanceUuid) || (!x.instanceUuid && x.vm === vm.vm)));
  const ev = o.evidence || {};
  box.innerHTML = `<div class="planning-panel lic-editor">
    <div class="planning-panel-title">Override phân loại OS — ${esc(vm.vm)} <span class="muted-sub">(${esc(vm.cluster || '')} · ${esc(vm.host || '')})</span></div>
    <div class="muted-sub">Bằng chứng từ vCenter: guestId <b>${esc(ev.guestId || '—')}</b> · cấu hình <b>${esc(ev.guestFullName || '—')}</b> · VMware Tools <b>${esc(ev.toolsGuestFullName || '—')}</b> (${esc(ev.toolsStatus || '—')}) · hostname <b>${esc(ev.guestHostname || '—')}</b>. Hệ thống suy ra: ${esc(o.categoryLabel)} / ${esc(o.osVersion || '—')} (${esc(LIC_SOURCE_LABEL[o.source])}, ${esc(LIC_CONF_LABEL[o.confidence])}).</div>
    <div class="sim-form-grid">
      <div class="field"><label>Phân loại</label><select id="licOvCat">${Object.entries(LIC_CATEGORY_LABEL).map(([k, l]) => `<option value="${k}"${(existing?.category || o.category) === k ? ' selected' : ''}>${l}</option>`).join('')}</select></div>
      <div class="field"><label>OS version</label><input id="licOvVer" value="${esc(existing?.osVersion || o.osVersion || '')}" placeholder="VD: Windows Server 2019"></div>
      <div class="field"><label>Edition (nếu đã kiểm tra)</label><input id="licOvEd" value="${esc(existing?.osEdition || '')}" placeholder="Standard / Datacenter" list="licEdList"><datalist id="licEdList"><option value="Standard"><option value="Datacenter"><option value="Essentials"></datalist></div>
      <div class="field"><label>Ghi chú / bằng chứng</label><input id="licOvNote" value="${esc(existing?.note || '')}" placeholder="VD: kiểm tra winver qua RDP ngày …"></div>
    </div>
    <div class="sim-actions">
      <button class="btn btn-primary btn-sm" data-lic-action="override-save" type="button">Lưu override</button>
      ${existing ? `<button class="btn btn-secondary btn-sm btn-danger" data-lic-action="override-delete" data-id="${existing.id}" type="button">Xoá override</button>` : ''}
      <button class="btn btn-secondary btn-sm" data-lic-action="override-cancel" type="button">Đóng</button>
    </div>
  </div>`;
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/* =========================================================
   3. License assessment
   ========================================================= */
function renderLicAssess() {
  const st = licState();
  const a = st.viewingSnapshot ? st.viewingSnapshot.result : st.assess;
  const ws = a.windowsServer, vm = a.vmware;
  const snapBanner = st.viewingSnapshot ? `<div class="insight-box lic-snap-banner"><strong>Đang xem snapshot #${st.viewingSnapshot.id}</strong> — ${esc(st.viewingSnapshot.title || '')} · lưu lúc ${licDate(st.viewingSnapshot.createdAt)} bởi ${esc(st.viewingSnapshot.createdBy || '—')} · hash ${esc(st.viewingSnapshot.resultHash || '')}. <button class="btn btn-secondary btn-sm" data-lic-action="snap-close" type="button">Quay về dữ liệu hiện tại</button></div>` : '';
  const ruleWarn = [a.rules.windows_server, a.rules.vmware, a.rules.vmware_cpu_legacy].filter(Boolean).filter(r => r.status !== 'verified');
  const scp = a.scope || {};
  const scopeBanner = scp.partial ? `<div class="insight-box"><strong>Phạm vi mua đợt này:</strong> ${a.clusters.length} cluster (${esc(a.clusters.map(c => `${c.cluster} (${c.vcenterKey})`).join(', '))}). Chưa tính ${scp.excludedClusterKeys.length} cluster: ${esc(scp.excludedClusterKeys.map(licKeyLabel).join(', '))}.</div>` : '';
  const ruleBanner = ruleWarn.length ? `<div class="insight-box lic-warn-box"><strong>Rule chưa xác minh:</strong> ${ruleWarn.map(r => `${esc(r.name)} v${r.version}`).join('; ')}. Kết quả chỉ mang tính tham khảo cho tới khi rule được đối chiếu với tài liệu nguồn và ghi nhận người/ngày xác minh (mục Rule licensing bên dưới).</div>` : '';

  const wsCard = (opt, title) => {
    const c = opt.cost;
    return `<div class="sim-card lic-opt-card">
      <div class="lic-opt-head"><b>${title}</b> ${licPill(opt.status)}</div>
      <table class="lic-kv">
        <tr><td>Nhu cầu core</td><td class="num"><b>${licRange(opt.demandCores, opt.demandCoresMax)}</b></td></tr>
        <tr><td>Pack 16 + pack 2</td><td class="num">${opt.packs.pack16 || 0} × 16 + ${opt.packs.pack2 || 0} × 2${opt.demandCoresMax !== opt.demandCores ? ` <span class="muted-sub">(tối đa ${opt.packsMax.pack16} × 16 + ${opt.packsMax.pack2} × 2)</span>` : ''}</td></tr>
        <tr><td>Chỉ pack 16 core</td><td class="num">${licRange(opt.packs.pack16Only, opt.packsMax.pack16Only)} pack</td></tr>
        <tr><td>Entitlement hiện có</td><td class="num">${opt.entitlement.declaredNone && !(opt.entitledCores > 0) ? '<span class="pill crit">Chưa có (đã xác nhận)</span>' : `${licNum(opt.entitledCores)} core${opt.entitlement.hasSa ? ' <span class="pill ok">SA</span>' : ''}`}</td></tr>
        <tr><td>Thiếu hụt</td><td class="num"><b class="${opt.shortfallCores > 0 ? 'lic-bad' : ''}">${licRange(opt.shortfallCores, opt.shortfallCoresMax)} core</b> → ${opt.shortfallPacks.pack16} × 16 + ${opt.shortfallPacks.pack2} × 2</td></tr>
        ${c ? `<tr><td>Đơn giá / core</td><td class="num">${licMoney(c.pricePerCore, c.currency)}</td></tr>
        <tr><td>Chi phí toàn bộ nhu cầu</td><td class="num">${licMoney(c.demandCost, c.currency)}${c.demandCostMax !== c.demandCost ? ` <span class="muted-sub">– ${licMoney(c.demandCostMax, c.currency)}</span>` : ''}</td></tr>
        <tr><td>Chi phí bù thiếu hụt</td><td class="num"><b>${licMoney(c.shortfallCost, c.currency)}</b>${c.shortfallCostMax !== c.shortfallCost ? ` <span class="muted-sub">– ${licMoney(c.shortfallCostMax, c.currency)}</span>` : ''}</td></tr>` : '<tr><td>Chi phí</td><td class="num muted-sub">Chưa có đơn giá</td></tr>'}
      </table></div>`;
  };
  const rec = ws.recommendedEdition || {};
  const recText = rec.byCores ? `Theo số core: <b>${rec.byCores === 'datacenter' ? 'Datacenter' : 'Standard'}</b> cần ít core hơn.` + (rec.byCost ? ` Theo chi phí: <b>${rec.byCost === 'datacenter' ? 'Datacenter' : 'Standard'}</b> rẻ hơn ${licMoney(rec.saving, rec.currency)}.` : ` ${esc(rec.note || '')}`) : 'Không có VM Windows Server trong phạm vi.';
  const vmwCost = vm.cost;
  const legacy = vm.legacy;

  const clusterRows = a.clusters.map(c => `<tr>
    <td><b>${esc(c.cluster)}</b><div class="muted-sub">${esc(c.vcenterKey || 'import')}</div></td>
    <td class="num">${c.hostCount}</td><td class="num">${c.sockets}</td><td class="num">${licNum(c.physicalCores)}</td><td class="num">${licNum(c.wsLicensableCores)}</td>
    <td>${c.ha ? '<span class="pill ok">HA</span>' : ''}${c.drs ? ' <span class="pill ok">DRS</span>' : ''}${!c.ha && !c.drs ? '<span class="pill na">—</span>' : ''}</td>
    <td class="num">${c.wsVms}${c.wsVmsLowConfidence ? ` <span class="pill warn" title="VM chỉ có bằng chứng cấu hình/tên">${c.wsVmsLowConfidence} ?</span>` : ''}</td>
    <td class="num">${c.unknownVms || 0}</td>
    <td class="num">${c.standard.sets !== null && c.standard.sets !== undefined ? `${c.standard.sets} bộ → ` : ''}<b>${licRange(c.standard.cores, c.standard.coresMax)}</b></td>
    <td class="num"><b>${licNum(c.datacenter.cores)}</b></td>
    <td class="num"><b>${licNum(c.vmwareCores)}</b>${legacy ? `<div class="muted-sub">${c.vmwareLegacyCpuLicenses} CPU lic.</div>` : ''}</td>
    <td>${c.recommendedEdition ? `<span class="pill ${c.recommendedEdition === 'datacenter' ? 'crit' : 'ok'}">${c.recommendedEdition === 'datacenter' ? 'Datacenter' : 'Standard'}</span>` : '—'}</td>
  </tr>`).join('');

  const list = (arr) => arr && arr.length ? `<ul class="lic-list">${arr.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '<div class="muted-sub">—</div>';

  return `${snapBanner}${scopeBanner}${ruleBanner}
    <div class="kpi-grid lic-kpi-4">
      <div class="kpi-card"><div class="label">Kết luận chung</div><div class="value">${licPill(a.overallStatus)}</div><div class="sub">${a.totals.hosts} host · ${licNum(a.totals.physicalCores)} physical core · ${a.totals.vms} VM · ${a.totals.windowsServerVms} Windows Server${a.totals.unknownVms ? ` · ${a.totals.unknownVms} chưa rõ` : ''}</div></div>
      <div class="kpi-card"><div class="label">VMware per-core</div><div class="value">${licNum(vm.demandCores)} core</div><div class="sub">Có ${licNum(vm.entitledCores)} · thiếu <b>${licNum(vm.shortfallCores)}</b> ${licPill(vm.status)}</div></div>
      <div class="kpi-card"><div class="label">Windows Server Standard</div><div class="value">${licRange(ws.standard.demandCores, ws.standard.demandCoresMax)} core</div><div class="sub">Có ${licNum(ws.standard.entitledCores)} · thiếu <b>${licRange(ws.standard.shortfallCores, ws.standard.shortfallCoresMax)}</b> ${licPill(ws.standard.status)}</div></div>
      <div class="kpi-card"><div class="label">Windows Server Datacenter</div><div class="value">${licNum(ws.datacenter.demandCores)} core</div><div class="sub">Có ${licNum(ws.datacenter.entitledCores)} · thiếu <b>${licNum(ws.datacenter.shortfallCores)}</b> ${licPill(ws.datacenter.status)}</div></div>
    </div>

    <section class="table-card">
      <div class="panel-head">
        <div><div class="panel-title">Nhu cầu license theo cluster</div><p class="panel-sub">Windows Server được tính theo cluster vì HA/DRS/vMotion cho phép mọi VM chạy trên mọi host; không đếm theo host hiện tại và không chỉ đếm số VM Windows. Chế độ đếm OSE Standard: <b>${ws.peakMode === 'current_placement' ? 'theo host hiện tại (tham chiếu)' : 'toàn cluster'}</b>.</p></div>
        <div class="panel-toolbar">
          ${st.viewingSnapshot ? '' : '<button class="btn btn-primary btn-sm" data-lic-action="snap-save" type="button">Lưu snapshot</button>'}
          <button class="btn btn-secondary btn-sm" data-lic-action="export-pdf" type="button" title="Executive summary: kết luận, quyết định đề nghị, tổng quan license theo cluster">Xuất PDF · Executive summary</button>
          <button class="btn btn-secondary btn-sm" data-lic-action="export-assess" type="button">Xuất Excel</button>
        </div>
      </div>
      <div class="table-scroll"><table class="data-table lic-table">
        <thead><tr><th>Cluster</th><th class="num">Host</th><th class="num">Socket</th><th class="num">Physical core</th><th class="num">Core licensable (WS)</th><th>HA / DRS</th><th class="num">VM Windows Server</th><th class="num">VM chưa rõ</th><th class="num">WS Standard core</th><th class="num">WS Datacenter core</th><th class="num">VMware core</th><th>Edition ít core hơn</th></tr></thead>
        <tbody>${clusterRows || '<tr><td colspan="12" class="empty-note">Không có cluster.</td></tr>'}</tbody>
      </table></div>
    </section>

    <div class="lic-3col">
      ${wsCard(ws.standard, 'Windows Server Standard')}
      ${wsCard(ws.datacenter, 'Windows Server Datacenter')}
      <div class="sim-card lic-opt-card">
        <div class="lic-opt-head"><b>VMware (VCF / VVF / vSphere, per-core)</b> ${licPill(vm.status)}</div>
        <table class="lic-kv">
          <tr><td>Nhu cầu core</td><td class="num"><b>${licNum(vm.demandCores)}</b></td></tr>
          <tr><td>Entitlement hiện có</td><td class="num">${vm.declaredNone && !(vm.entitledCores > 0) ? '<span class="pill crit">Chưa có (đã xác nhận)</span>' : `${licNum(vm.entitledCores)} core`}${Object.values(vm.entitlements).filter(e => e.units > 0).map(e => `<div class="muted-sub">${esc(e.label)}: ${licNum(e.units)}</div>`).join('')}</td></tr>
          <tr><td>Thiếu hụt</td><td class="num"><b class="${vm.shortfallCores > 0 ? 'lic-bad' : ''}">${licNum(vm.shortfallCores)} core</b>${vm.orderCores !== vm.shortfallCores ? ` <span class="muted-sub">(đặt hàng tối thiểu ${licNum(vm.orderCores)})</span>` : ''}</td></tr>
          ${vmwCost ? `<tr><td>Đơn giá / core (${esc(vmwCost.basis)})</td><td class="num">${licMoney(vmwCost.pricePerCore, vmwCost.currency)}</td></tr>
          <tr><td>Chi phí toàn bộ nhu cầu</td><td class="num">${licMoney(vmwCost.demandCost, vmwCost.currency)}</td></tr>
          <tr><td>Chi phí bù thiếu hụt</td><td class="num"><b>${licMoney(vmwCost.shortfallCost, vmwCost.currency)}</b></td></tr>` : '<tr><td>Chi phí</td><td class="num muted-sub">Chưa có đơn giá</td></tr>'}
          ${legacy ? `<tr><td colspan="2" class="lic-sep">Per-CPU perpetual (legacy)</td></tr><tr><td>Số license CPU cần</td><td class="num">${licNum(legacy.demandCpuLicenses)}</td></tr><tr><td>Có / thiếu</td><td class="num">${licNum(legacy.entitled)} / <b>${licNum(legacy.shortfall)}</b> ${licPill(legacy.status)}</td></tr>` : ''}
        </table></div>
    </div>

    <div class="insight-box"><strong>So sánh edition Windows Server:</strong> ${recText}</div>

    <div class="lic-3col">
      <div class="sim-card"><h4 class="lic-h4">Công thức — Windows Server</h4>${list(ws.formula)}<h4 class="lic-h4">Giả định</h4>${list(ws.assumptions)}</div>
      <div class="sim-card"><h4 class="lic-h4">Công thức — VMware</h4>${list(vm.formula)}<h4 class="lic-h4">Giả định</h4>${list(vm.assumptions)}</div>
      <div class="sim-card lic-gap-card"><h4 class="lic-h4">Thiếu dữ liệu / cần xác nhận</h4>${list([...(ws.dataGaps || []), ...(vm.dataGaps || [])])}</div>
    </div>

    ${st.viewingSnapshot ? '' : renderLicEntitlements() + renderLicRules()}`;
}

/* ---------- Entitlements ---------- */
function renderLicEntitlements() {
  const st = licState();
  const items = st.ents || [];
  const products = st.entMeta?.products || {};
  const metrics = st.entMeta?.metrics || {};
  const clusters = [...new Set(st.inv.hosts.map(h => h.cluster).filter(Boolean))].sort();
  const vcs = [...new Set(st.inv.hosts.map(h => h.vcenterKey).filter(Boolean))].sort();
  const e = st.entEditing || {};
  const kinds = st.entMeta?.kinds || LIC_KIND_LABEL;
  const isNone = (e.id ? (e.kind || 'owned') : (st.entKindDraft || 'owned')) === 'none';
  const form = `<div class="planning-panel lic-editor">
    <div class="planning-panel-title">${e.id ? `Sửa entitlement #${e.id}` : 'Thêm entitlement / báo giá'}</div>
    <div class="sim-form-grid lic-form-6">
      <div class="field"><label>Sản phẩm</label><select id="licEntProduct">${Object.entries(products).map(([k, l]) => `<option value="${k}"${(e.product || 'ws_dc') === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div class="field"><label>Tình trạng</label><select id="licEntKind" data-lic-entkind="1">${Object.entries(kinds).map(([k, l]) => `<option value="${k}"${(isNone ? 'none' : 'owned') === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div class="field"><label>Đơn vị</label><select id="licEntMetric">${Object.entries(metrics).map(([k, l]) => `<option value="${k}"${(e.metric || 'pack16') === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></div>
      <div class="field"><label>Số lượng${isNone ? ' (chưa có = 0)' : ''}</label><input id="licEntQty" type="number" min="0" step="1" value="${isNone ? 0 : (e.quantity ?? '')}"${isNone ? ' disabled' : ''}></div>
      <div class="field"><label>${isNone ? 'Báo giá / đơn vị (tuỳ chọn)' : 'Đơn giá / đơn vị (tuỳ chọn)'}</label><input id="licEntPrice" type="number" min="0" step="1" value="${e.unitPrice ?? ''}" placeholder="VD: 160000000"></div>
      <div class="field"><label>Tiền tệ</label><input id="licEntCur" value="${esc(e.currency || 'VND')}"></div>
      <div class="field"><label>SA / Subscription</label><select id="licEntSa"><option value="0"${!e.hasSa ? ' selected' : ''}>Không</option><option value="1"${e.hasSa ? ' selected' : ''}>Có</option></select></div>
      <div class="field"><label>Phạm vi vCenter</label><select id="licEntVc"><option value="">Tất cả</option>${vcs.map(v => `<option value="${esc(v)}"${e.scopeVcenter === v ? ' selected' : ''}>${esc(v)}</option>`).join('')}</select></div>
      <div class="field"><label>Phạm vi cluster</label><select id="licEntCl"><option value="">Tất cả</option>${clusters.map(c => `<option value="${esc(c)}"${e.scopeCluster === c ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select></div>
      <div class="field"><label>Hết hạn (tuỳ chọn)</label><input id="licEntValid" type="date" value="${e.validTo ? String(e.validTo).slice(0, 10) : ''}"></div>
      <div class="field"><label>Ghi chú (hợp đồng, PO…)</label><input id="licEntNote" value="${esc(e.note || '')}"></div>
    </div>
    <div class="sim-actions">
      <button class="btn btn-primary btn-sm" data-lic-action="ent-save" type="button">${e.id ? 'Cập nhật' : 'Thêm'}</button>
      ${e.id ? '<button class="btn btn-secondary btn-sm" data-lic-action="ent-cancel" type="button">Huỷ</button>' : ''}
    </div></div>`;
  const rows = items.map(x => `<tr>
    <td><b>${esc(x.label)}</b>${x.kind === 'none' ? ' <span class="pill crit">Chưa có</span>' : ''}${x.hasSa && x.kind !== 'none' ? ' <span class="pill ok">SA</span>' : ''}</td><td>${esc(metrics[x.metric] || x.metric)}</td><td class="num">${x.kind === 'none' ? '0' : licNum(x.quantity)}</td>
    <td class="num">${x.unitPrice != null ? licMoney(x.unitPrice, x.currency) : '—'}</td>
    <td>${esc(x.scopeVcenter || 'Tất cả')} / ${esc(x.scopeCluster || 'Tất cả')}</td><td>${x.validTo ? licDate(x.validTo).split(' ')[0] : '—'}</td><td class="lic-note">${esc(x.note || '')}</td>
    <td><button class="btn btn-secondary btn-sm" data-lic-action="ent-edit" data-id="${x.id}" type="button">Sửa</button> <button class="btn btn-secondary btn-sm btn-danger" data-lic-action="ent-delete" data-id="${x.id}" type="button">Xoá</button></td>
  </tr>`).join('');
  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title">Entitlement hiện có &amp; đơn giá</div><p class="panel-sub">Nhập license đang sở hữu, hoặc chọn tình trạng <b>Chưa có</b> để xác nhận chưa mua license sản phẩm đó (toàn bộ nhu cầu được tính là thiếu hụt; có thể kèm báo giá để ước chi phí). Số lượng pack được quy đổi sang core; đơn giá là giá mỗi đơn vị đã chọn (pack/core/CPU). Không có giá mặc định — thiếu giá thì không hiển thị chi phí.</p></div></div>
    ${form}
    <div class="table-scroll"><table class="data-table lic-table"><thead><tr><th>Sản phẩm</th><th>Đơn vị</th><th class="num">Số lượng</th><th class="num">Đơn giá</th><th>Phạm vi</th><th>Hết hạn</th><th>Ghi chú</th><th></th></tr></thead>
    <tbody>${rows || '<tr><td colspan="8" class="empty-note">Chưa có entitlement nào.</td></tr>'}</tbody></table></div>
  </section>`;
}

/* ---------- Rules ---------- */
function renderLicRules() {
  const st = licState();
  const rules = st.rules || [];
  const ed = st.ruleEditing;
  const rows = rules.map(r => `<tr class="${r.isActive ? 'lic-rule-active' : ''}">
    <td><b>${esc(r.name)}</b><div class="muted-sub">${esc(r.product)} · ${esc(r.key)}</div></td>
    <td class="num">v${r.version}</td>
    <td>${licRulePill(r.status)}${r.isActive ? ' <span class="pill ok">đang dùng</span>' : ''}</td>
    <td>${r.sourceUrl ? `<a href="${esc(r.sourceUrl)}" target="_blank" rel="noopener">Nguồn</a>` : '—'}<div class="muted-sub lic-note" title="${esc(r.sourceNote || '')}">${esc((r.sourceNote || '').split('\n')[0]).slice(0, 140)}</div></td>
    <td>${r.verifiedAt ? `${licDate(r.verifiedAt)}<div class="muted-sub">${esc(r.verifiedBy || '')}</div>` : '<span class="muted-sub">chưa</span>'}</td>
    <td class="lic-actions">
      <button class="btn btn-secondary btn-sm" data-lic-action="rule-edit" data-id="${r.id}" type="button">Xem / tạo version</button>
      ${r.status !== 'verified' ? `<button class="btn btn-secondary btn-sm" data-lic-action="rule-verify" data-id="${r.id}" type="button">Xác minh</button>` : ''}
      ${r.isActive ? `<button class="btn btn-secondary btn-sm" data-lic-action="rule-deactivate" data-id="${r.id}" type="button">Tắt</button>` : `<button class="btn btn-secondary btn-sm" data-lic-action="rule-activate" data-id="${r.id}" type="button">Kích hoạt</button>`}
    </td></tr>`).join('');
  const editor = ed ? `<div class="planning-panel lic-editor">
    <div class="planning-panel-title">Rule ${esc(ed.name)} v${ed.version} — tạo version mới (rule không sửa đè để assessment cũ tái lập được)</div>
    <div class="sim-form-grid lic-form-2">
      <div class="field"><label>Tên</label><input id="licRuleName" value="${esc(ed.name)}"></div>
      <div class="field"><label>URL nguồn</label><input id="licRuleUrl" value="${esc(ed.sourceUrl || '')}"></div>
    </div>
    <div class="field"><label>Tham số (JSON)</label><textarea id="licRuleParams" rows="10" class="lic-json">${esc(JSON.stringify(ed.params, null, 2))}</textarea></div>
    <div class="field"><label>Ghi chú nguồn / lý do thay đổi</label><input id="licRuleNote" value="${esc(ed.sourceNote || '')}"></div>
    <div class="sim-actions">
      <button class="btn btn-primary btn-sm" data-lic-action="rule-save" data-id="${ed.id}" data-activate="1" type="button">Lưu version mới &amp; kích hoạt</button>
      <button class="btn btn-secondary btn-sm" data-lic-action="rule-save" data-id="${ed.id}" type="button">Chỉ lưu version mới</button>
      <button class="btn btn-secondary btn-sm" data-lic-action="rule-cancel" type="button">Đóng</button>
    </div>
    <p class="logo-upload-hint hidden" id="licRuleErr"></p></div>` : '';
  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title">Rule licensing (có version, nguồn, ngày xác minh)</div><p class="panel-sub">Engine chỉ đọc tham số từ rule đang kích hoạt. Rule mặc định lấy từ tài liệu công khai của Microsoft/Broadcom và ở trạng thái "Chưa xác minh" cho tới khi người có trách nhiệm đối chiếu và bấm Xác minh. Tham số ví dụ: min_cores_per_host, standard_peak_mode (cluster_total | current_placement), include_powered_off, min_cores_per_cpu, min_cores_per_order.</p></div></div>
    ${editor}
    <div class="table-scroll"><table class="data-table lic-table"><thead><tr><th>Rule</th><th class="num">Version</th><th>Trạng thái</th><th>Nguồn</th><th>Xác minh</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
  </section>`;
}

/* =========================================================
   4. What-if license
   ========================================================= */
function renderLicWhatif() {
  const st = licState();
  const w = st.whatif;
  const clusters = [...new Set(st.inv.hosts.map(h => `${h.vcenterKey || ''}||${h.cluster}`))].sort().map(k => { const [vc, cl] = k.split('||'); return { vc, cl }; });
  const clOpt = (id, cur) => `<select id="${id}">${clusters.map(c => `<option value="${esc(c.vc)}||${esc(c.cl)}"${cur === `${c.vc}||${c.cl}` ? ' selected' : ''}>${esc(c.cl)} (${esc(c.vc || 'import')})</option>`).join('')}</select>`;
  const f = w.form;
  let fields = '';
  if (w.type === 'add_host') fields = `<div class="field"><label>Cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Số host thêm</label><input id="lwCount" type="number" min="1" value="${f.count ?? 1}"></div><div class="field"><label>Physical core / host (trống = như host hiện có)</label><input id="lwCores" type="number" min="1" value="${f.cores ?? ''}"></div><div class="field"><label>Socket / host</label><input id="lwSockets" type="number" min="1" value="${f.sockets ?? ''}"></div><div class="field"><label>RAM GB / host</label><input id="lwRam" type="number" min="1" value="${f.ramGB ?? ''}"></div>`;
  else if (w.type === 'remove_host') fields = `<div class="field"><label>Cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Số host giảm (host nhỏ nhất trước)</label><input id="lwCount" type="number" min="1" value="${f.count ?? 1}"></div>`;
  else if (w.type === 'resize_host') fields = `<div class="field"><label>Cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Physical core / host mới</label><input id="lwCores" type="number" min="1" value="${f.cores ?? ''}"></div><div class="field"><label>Socket / host mới</label><input id="lwSockets" type="number" min="1" value="${f.sockets ?? ''}"></div><div class="field"><label>RAM GB / host mới</label><input id="lwRam" type="number" min="1" value="${f.ramGB ?? ''}"></div>`;
  else if (w.type === 'add_vms') fields = `<div class="field"><label>Cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Số VM</label><input id="lwCount" type="number" min="1" value="${f.count ?? 5}"></div><div class="field"><label>Loại OS</label><select id="lwCat">${Object.entries(LIC_CATEGORY_LABEL).map(([k, l]) => `<option value="${k}"${(f.category || 'windows_server') === k ? ' selected' : ''}>${l}</option>`).join('')}</select></div><div class="field"><label>vCPU / VM</label><input id="lwVcpu" type="number" min="1" value="${f.vcpu ?? 4}"></div><div class="field"><label>RAM GB / VM</label><input id="lwRam" type="number" min="1" value="${f.ramGB ?? 16}"></div>`;
  else if (w.type === 'move_vms') fields = `<div class="field"><label>Từ cluster</label>${clOpt('lwCluster', f.cluster)}</div><div class="field"><label>Sang cluster</label>${clOpt('lwTarget', f.target)}</div><div class="field"><label>Loại OS cần chuyển</label><select id="lwCat">${Object.entries(LIC_CATEGORY_LABEL).map(([k, l]) => `<option value="${k}"${(f.category || 'windows_server') === k ? ' selected' : ''}>${l}</option>`).join('')}</select></div><div class="field"><label>Số VM (trống = tất cả)</label><input id="lwCount" type="number" min="1" value="${f.count ?? ''}"></div>`;

  const hint = {
    add_host: 'Thêm host làm tăng core VMware và (với Standard) nhân thêm số bộ license cho cluster. Dùng để thấy chi phí license đi kèm khi mở rộng.',
    remove_host: 'Giảm host tiết kiệm core VMware/Windows Server nhưng bị chặn nếu cluster vượt ngưỡng overcommit hoặc mất khả năng N+1.',
    resize_host: 'Nâng core/socket mỗi host (thay CPU) — license per-core tăng tương ứng; thường nhiều core ít host rẻ hơn Datacenter.',
    add_vms: 'Thêm VM Windows Server trong cluster Standard làm tăng số bộ license; với Datacenter thì không đổi. Capacity được kiểm tra song song.',
    move_vms: 'Gom VM Windows Server về cluster đã license Datacenter để giảm nhu cầu Standard ở cluster nguồn — chỉ đề xuất khi cluster đích còn đủ capacity/HA.',
  }[w.type];

  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title">What-if license gắn với capacity</div><p class="panel-sub">Mỗi kịch bản được tính lại cả license (VMware per-core, Windows Server Standard/Datacenter) lẫn capacity (overcommit CPU/RAM theo chính sách, khả năng chịu lỗi N+1 của cluster). Kịch bản vi phạm capacity/HA sẽ bị đánh dấu <span class="pill crit">Không đề xuất</span> dù có tiết kiệm license.</p></div></div>
    <div class="sim-tabbar">${LIC_SCENARIOS.map(s => `<button class="sim-tab${w.type === s.type ? ' active' : ''}" data-lic-wtype="${s.type}" type="button">${s.label}</button>`).join('')}</div>
    <p class="muted-sub">${hint}</p>
    <div class="sim-form-grid lic-form-5">${fields}</div>
    <div class="sim-actions"><button class="btn btn-primary" data-lic-action="whatif-run" type="button"${w.busy ? ' disabled' : ''}>${w.busy ? 'Đang tính…' : 'Chạy kịch bản'}</button>${w.result ? '<button class="btn btn-secondary" data-lic-action="export-whatif" type="button">Xuất Excel</button>' : ''}</div>
    <p class="logo-upload-hint hidden" id="licWhatifErr"></p>
  </section>
  ${w.result ? renderLicWhatifResult(w.result) : ''}`;
}

function renderLicWhatifResult(r) {
  const verdict = { ok: ['ok', 'Đạt capacity/HA'], warn: ['warn', 'Vùng cảnh báo capacity'], violation: ['crit', 'Không đề xuất — vi phạm capacity/HA'] }[r.capacityVerdict] || ['na', r.capacityVerdict];
  const d = r.delta;
  const dRow = (label, x, unit = 'core', money = false) => {
    if (!x || x.before === null || x.before === undefined) return '';
    const fmt = money ? (v) => licMoney(v) : (v) => licNum(v);
    const dl = x.delta;
    const cls = dl > 0 ? 'lic-bad' : dl < 0 ? 'lic-good' : '';
    return `<tr><td>${label}</td><td class="num">${fmt(x.before)}</td><td class="num">${fmt(x.after)}</td><td class="num ${cls}"><b>${dl > 0 ? '+' : ''}${fmt(dl)}</b> ${money ? '' : unit}</td></tr>`;
  };
  const capRows = r.capacityAfter.map((a, i) => {
    const b = r.capacityBefore[i];
    const pct = (v) => isFinite(v) ? `${Math.round(v)}%` : '—';
    const ha = (c) => !c.haEligible ? '<span class="pill na">1 host</span>' : c.haSurvives ? '<span class="pill ok">N+1 đạt</span>' : '<span class="pill crit">N+1 không đạt</span>';
    return `<tr><td><b>${esc(a.cluster)}</b></td><td class="num">${b.hosts} → ${a.hosts}</td><td class="num">${b.vms} → ${a.vms}</td><td class="num">${pct(b.cpuPct)} → <b>${pct(a.cpuPct)}</b></td><td class="num">${pct(b.ramPct)} → <b>${pct(a.ramPct)}</b></td><td>${ha(b)} → ${ha(a)}</td></tr>`;
  }).join('');
  const reasons = r.capacityVerdicts.flatMap(v => v.reasons.map(x => `${v.cluster}: ${x}`));
  return `<section class="sim-card sim-result">
    <div class="lic-opt-head"><b>${esc(r.description)}</b> <span class="pill ${verdict[0]}">${verdict[1]}</span></div>
    <div class="insight-box">${r.insights.map(x => `<div>• ${esc(x)}</div>`).join('')}${reasons.length ? `<div class="lic-reasons">${reasons.map(x => `<div>⚠ ${esc(x)}</div>`).join('')}</div>` : ''}</div>
    <div class="lic-2col">
      <div><h4 class="lic-h4">License trước → sau</h4><table class="data-table lic-table"><thead><tr><th>Chỉ số</th><th class="num">Trước</th><th class="num">Sau</th><th class="num">Δ</th></tr></thead><tbody>
        ${dRow('Host', d.hosts, 'host')}${dRow('Physical core', d.physicalCores)}${dRow('VM Windows Server', d.windowsServerVms, 'VM')}
        ${dRow('VMware core nhu cầu', d.vmwareCores)}${dRow('VMware core thiếu', d.vmwareShortfall)}
        ${dRow('WS Standard core', d.wsStandardCores)}${dRow('WS Standard core (tối đa)', d.wsStandardCoresMax)}${dRow('WS Standard thiếu', d.wsStandardShortfall)}
        ${dRow('WS Datacenter core', d.wsDatacenterCores)}${dRow('WS Datacenter thiếu', d.wsDatacenterShortfall)}
        ${dRow('Chi phí VMware (toàn bộ nhu cầu)', d.vmwareCost, '', true)}${dRow('Chi phí WS Standard', d.wsStandardCost, '', true)}${dRow('Chi phí WS Datacenter', d.wsDatacenterCost, '', true)}
      </tbody></table></div>
      <div><h4 class="lic-h4">Capacity cluster bị ảnh hưởng</h4><table class="data-table lic-table"><thead><tr><th>Cluster</th><th class="num">Host</th><th class="num">VM</th><th class="num">CPU % ngưỡng</th><th class="num">RAM % ngưỡng</th><th>HA N+1</th></tr></thead><tbody>${capRows}</tbody></table>
      <p class="muted-sub">% ngưỡng = overcommit sau kịch bản / overcommit tối đa theo chính sách. &gt;80% cảnh báo, &gt;100% vi phạm.</p></div>
    </div>
  </section>`;
}

/* =========================================================
   5. Snapshot
   ========================================================= */
function renderLicSnapshots() {
  const st = licState();
  const snaps = st.snaps || [];
  const rows = snaps.map(s => {
    const sm = s.summary || {};
    const sc = s.scope || {};
    return `<tr>
      <td class="num">#${s.id}</td><td>${licDate(s.createdAt)}<div class="muted-sub">${esc(s.createdBy || '')}</div></td>
      <td><b>${esc(s.title || '')}</b>${s.note ? `<div class="muted-sub">${esc(s.note)}</div>` : ''}</td>
      <td>${esc((sc.vcenterKeys || ['Tất cả vCenter']).join(', '))}${sc.partial ? ` <span class="pill warn" title="Chưa tính: ${esc((sc.excludedClusterKeys || []).map(licKeyLabel).join(', '))}">một phần</span>` : ''}<div class="muted-sub lic-scope-cell">${sc.clusters ? esc(sc.clusters.join(', ')) : 'tất cả cluster'}</div></td>
      <td>${licPill(sm.overallStatus)}</td>
      <td class="num">${sm.vmware ? `${licNum(sm.vmware.demandCores)} / thiếu ${licNum(sm.vmware.shortfallCores)}` : '—'}</td>
      <td class="num">${sm.windowsServer ? `Std ${licRange(sm.windowsServer.standard.demandCores, sm.windowsServer.standard.demandCoresMax)} · DC ${licNum(sm.windowsServer.datacenter.demandCores)}` : '—'}</td>
      <td><code class="lic-hash" title="${esc(s.resultHash || '')}">${esc(String(s.resultHash || '').slice(0, 10))}</code><span class="muted-sub" id="licRecomp${s.id}"></span></td>
      <td class="lic-actions"><div class="lic-btns"><button class="btn btn-secondary btn-sm" data-lic-action="snap-view" data-id="${s.id}" type="button">Xem</button> <button class="btn btn-secondary btn-sm" data-lic-action="snap-recompute" data-id="${s.id}" type="button">Tái lập</button> <button class="btn btn-secondary btn-sm" data-lic-action="snap-pdf" data-id="${s.id}" type="button">PDF</button> <button class="btn btn-secondary btn-sm btn-danger" data-lic-action="snap-delete" data-id="${s.id}" type="button">Xoá</button></div></td>
    </tr>`;
  }).join('');
  return `<section class="table-card">
    <div class="panel-head"><div><div class="panel-title">Snapshot assessment</div><p class="panel-sub">Mỗi snapshot lưu toàn bộ đầu vào (inventory, override, version rule và tham số, entitlement) cùng kết quả và hash. "Tái lập" chạy lại engine trên đầu vào đã lưu và so hash để chứng minh kết quả tái lập được, độc lập với inventory hiện tại.</p></div>
      <div class="panel-toolbar"><input id="licSnapTitle" placeholder="Tiêu đề snapshot (tuỳ chọn)"><button class="btn btn-primary btn-sm" data-lic-action="snap-save" type="button">Lưu snapshot phạm vi hiện tại</button></div></div>
    <div class="table-scroll"><table class="data-table lic-table"><thead><tr><th class="num">#</th><th>Thời điểm</th><th>Tiêu đề</th><th>Phạm vi</th><th>Kết luận</th><th class="num">VMware core</th><th class="num">Windows Server core</th><th>Hash</th><th></th></tr></thead>
    <tbody>${rows || '<tr><td colspan="9" class="empty-note">Chưa có snapshot.</td></tr>'}</tbody></table></div>
  </section>`;
}

/* =========================================================
   Actions
   ========================================================= */
function onLicBodyChange(e) {
  const st = licState();
  const f = e.target.dataset.licFilter;
  if (f) { st.filters[f] = e.target.value; renderLicOsTable(); return; }
  if (e.target.dataset.licEntkind) {
    const none = e.target.value === 'none';
    st.entKindDraft = e.target.value;
    if (st.entEditing) st.entEditing = { ...st.entEditing, kind: e.target.value };
    const qty = $('#licEntQty'); if (qty) { qty.disabled = none; if (none) qty.value = 0; }
    const lbl = qty?.closest('.field')?.querySelector('label'); if (lbl) lbl.textContent = none ? 'Số lượng (chưa có = 0)' : 'Số lượng';
  }
}

async function onLicBodyClick(e) {
  const st = licState();
  const wtype = e.target.closest('[data-lic-wtype]');
  if (wtype) { st.whatif.type = wtype.dataset.licWtype; st.whatif.result = null; renderLicBody(); return; }
  const catCard = e.target.closest('[data-lic-cat]');
  if (catCard) { st.filters.category = st.filters.category === catCard.dataset.licCat ? 'all' : catCard.dataset.licCat; renderLicBody(); return; }
  const tog = e.target.closest('[data-hw-toggle]');
  if (tog) { const k = tog.dataset.hwToggle; st.hwOpen[k] = !st.hwOpen[k]; renderLicBody(); return; }
  const btn = e.target.closest('[data-lic-action]');
  if (!btn) return;
  const act = btn.dataset.licAction;
  const id = btn.dataset.id ? Number(btn.dataset.id) : null;
  try {
    if (act === 'hw-expand') { st.inv.hosts.forEach(h => { st.hwOpen[`${h.vcenterKey || 'import'}||${h.cluster || '—'}`] = true; }); renderLicBody(); }
    else if (act === 'hw-collapse') { st.hwOpen = {}; renderLicBody(); }
    else if (act === 'export-hw') await licExportHardware();
    else if (act === 'export-os') await licExportOs();
    else if (act === 'export-assess') await licExportAssess();
    else if (act === 'export-pdf') {
      const sc = licScope();
      licSetStatus('Đang tạo PDF executive summary…');
      await api.licReportPdf(sc.vcenter, sc.cluster, sc.clusters, st.viewingSnapshot ? st.viewingSnapshot.id : null);
      licSetStatus('Đã xuất PDF executive summary.');
    }
    else if (act === 'snap-pdf') { licSetStatus(`Đang tạo PDF từ snapshot #${id}…`); await api.licReportPdf('all', null, [], id); licSetStatus(`Đã xuất PDF snapshot #${id}.`); }
    else if (act === 'export-whatif') await licExportWhatif();
    else if (act === 'override') { const vm = st.inv.vms[Number(btn.dataset.idx)]; st.overrides = await api.licOverrides(); st.editing = vm; renderLicOverrideEditor(vm); }
    else if (act === 'override-cancel') { st.editing = null; renderLicOverrideEditor(null); }
    else if (act === 'override-save') {
      const vm = st.editing;
      await api.licOverrideSave({ vcenterKey: vm.vcenterKey || null, instanceUuid: vm.instanceUuid || null, vm: vm.vm, category: $('#licOvCat').value, osVersion: $('#licOvVer').value.trim() || null, osEdition: $('#licOvEd').value.trim() || null, note: $('#licOvNote').value.trim() || null });
      st.editing = null; await licLoad(true); renderLicBody(); licSetStatus(`Đã lưu override cho ${vm.vm}.`);
    }
    else if (act === 'override-delete') { await api.licOverrideDelete(id); st.editing = null; await licLoad(true); renderLicBody(); licSetStatus('Đã xoá override.'); }
    else if (act === 'ent-edit') { st.entEditing = st.ents.find(x => x.id === id); renderLicBody(); $('#licEntProduct')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    else if (act === 'ent-cancel') { st.entEditing = null; st.entKindDraft = null; renderLicBody(); }
    else if (act === 'ent-save') {
      const kind = $('#licEntKind')?.value || 'owned';
      const payload = { product: $('#licEntProduct').value, kind, metric: $('#licEntMetric').value, quantity: kind === 'none' ? 0 : Number($('#licEntQty').value || 0), unitPrice: $('#licEntPrice').value === '' ? null : Number($('#licEntPrice').value), currency: $('#licEntCur').value.trim() || 'VND', hasSa: $('#licEntSa').value === '1', scopeVcenter: $('#licEntVc').value || null, scopeCluster: $('#licEntCl').value || null, validTo: $('#licEntValid').value || null, note: $('#licEntNote').value.trim() || null };
      if (kind === 'owned' && !(payload.quantity > 0)) { licSetStatus('Số lượng entitlement phải lớn hơn 0 — hoặc chọn tình trạng "Chưa có" nếu chưa mua license.', true); return; }
      if (st.entEditing?.id) await api.licEntitlementUpdate(st.entEditing.id, payload); else await api.licEntitlementCreate(payload);
      st.entEditing = null; st.entKindDraft = null; await licLoad(true); renderLicBody(); licSetStatus(kind === 'none' ? 'Đã ghi nhận "Chưa có license" và tính lại assessment.' : 'Đã lưu entitlement và tính lại assessment.');
    }
    else if (act === 'ent-delete') { if (!confirm('Xoá entitlement này?')) return; await api.licEntitlementDelete(id); await licLoad(true); renderLicBody(); }
    else if (act === 'rule-edit') { st.ruleEditing = st.rules.find(r => r.id === id); renderLicBody(); $('#licRuleParams')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    else if (act === 'rule-cancel') { st.ruleEditing = null; renderLicBody(); }
    else if (act === 'rule-save') {
      let params;
      try { params = JSON.parse($('#licRuleParams').value); } catch (err) { const el = $('#licRuleErr'); el.classList.remove('hidden'); el.style.color = 'var(--bad)'; el.textContent = `JSON không hợp lệ: ${err.message}`; return; }
      await api.licRuleVersion(id, { params, name: $('#licRuleName').value.trim() || null, sourceUrl: $('#licRuleUrl').value.trim() || null, sourceNote: $('#licRuleNote').value.trim() || null, activate: btn.dataset.activate === '1' });
      st.ruleEditing = null; await licLoad(true); renderLicBody(); licSetStatus('Đã tạo version rule mới.');
    }
    else if (act === 'rule-activate') { await api.licRuleActivate(id); await licLoad(true); renderLicBody(); }
    else if (act === 'rule-deactivate') { if (!confirm('Tắt rule này? Assessment sẽ báo thiếu rule cho tới khi kích hoạt version khác.')) return; await api.licRuleDeactivate(id); await licLoad(true); renderLicBody(); }
    else if (act === 'rule-verify') {
      const note = prompt('Ghi chú xác minh (tài liệu đã đối chiếu, ngày, người):', `Đối chiếu tài liệu nguồn ngày ${new Date().toLocaleDateString('vi-VN')}`);
      if (note === null) return;
      await api.licRuleVerify(id, note); await licLoad(true); renderLicBody(); licSetStatus('Đã ghi nhận xác minh rule.');
    }
    else if (act === 'whatif-run') await licRunWhatif();
    else if (act === 'snap-save') {
      const sc = licScope();
      const title = $('#licSnapTitle')?.value.trim() || null;
      const s = await api.licSnapshotCreate({ vcenter: sc.vcenter, cluster: sc.cluster !== 'all' ? sc.cluster : null, clusters: sc.clusters.length ? sc.clusters : null, title });
      st.snaps = await api.licSnapshots(); licSetStatus(`Đã lưu snapshot #${s.id} (hash ${s.resultHash}).`);
      if (st.sub === 'snapshots') renderLicBody();
    }
    else if (act === 'snap-view') { const full = await api.licSnapshotGet(id); st.viewingSnapshot = full; st.sub = 'assess'; if (typeof syncNavState === 'function') syncNavState(); renderLicBody(); }
    else if (act === 'snap-close') { st.viewingSnapshot = null; renderLicBody(); }
    else if (act === 'snap-recompute') { const r = await api.licSnapshotRecompute(id); const el = $(`#licRecomp${id}`); if (el) el.innerHTML = r.reproducible ? ' <span class="pill ok">tái lập đúng</span>' : ` <span class="pill crit">khác: ${esc(r.recomputedHash)}</span>`; }
    else if (act === 'snap-delete') { if (!confirm(`Xoá snapshot #${id}?`)) return; await api.licSnapshotDelete(id); st.snaps = await api.licSnapshots(); renderLicBody(); }
  } catch (err) {
    licSetStatus(`Thao tác thất bại: ${err.message}`, true);
  }
}

async function licRunWhatif() {
  const st = licState();
  const w = st.whatif;
  const err = $('#licWhatifErr');
  const split = (v) => { const [vc, cl] = String(v || '').split('||'); return { vc: vc || null, cl }; };
  const num = (id) => { const el = $(`#${id}`); if (!el || el.value === '') return null; const n = Number(el.value); return isFinite(n) ? n : null; };
  const src = split($('#lwCluster')?.value);
  const scenario = { type: w.type, vcenterKey: src.vc, cluster: src.cl };
  w.form = { cluster: $('#lwCluster')?.value, target: $('#lwTarget')?.value, count: num('lwCount'), cores: num('lwCores'), sockets: num('lwSockets'), ramGB: num('lwRam'), vcpu: num('lwVcpu'), category: $('#lwCat')?.value };
  if (w.form.count) scenario.count = w.form.count;
  if (w.form.cores) scenario.cores = w.form.cores;
  if (w.form.sockets) scenario.sockets = w.form.sockets;
  if (w.form.ramGB) scenario.ramGB = w.form.ramGB;
  if (w.form.vcpu) scenario.vcpu = w.form.vcpu;
  if (w.form.category) scenario.category = w.form.category;
  if (w.type === 'move_vms') {
    const dst = split($('#lwTarget')?.value);
    scenario.sourceCluster = src.cl; scenario.targetCluster = dst.cl; scenario.targetVcenterKey = dst.vc;
    if (dst.cl === src.cl && dst.vc === src.vc) { err.classList.remove('hidden'); err.style.color = 'var(--bad)'; err.textContent = 'Cluster đích phải khác cluster nguồn.'; return; }
  }
  if (w.type === 'resize_host' && !scenario.cores && !scenario.sockets && !scenario.ramGB) { err.classList.remove('hidden'); err.style.color = 'var(--bad)'; err.textContent = 'Nhập ít nhất một thông số mới (core, socket hoặc RAM).'; return; }
  w.busy = true; renderLicBody();
  try {
    const sc = licScope();
    w.result = await api.licWhatif({ vcenter: sc.vcenter, cluster: sc.cluster !== 'all' ? sc.cluster : null, clusters: sc.clusters.length ? sc.clusters : null, scenario });
  } catch (e) {
    w.result = null;
    renderLicBody();
    const el = $('#licWhatifErr'); if (el) { el.classList.remove('hidden'); el.style.color = 'var(--bad)'; el.textContent = `Không chạy được kịch bản: ${e.message}`; }
    w.busy = false; return;
  }
  w.busy = false; renderLicBody();
}

/* ---------- Exports (generic server-side xlsx) ---------- */
function licScopeSlug() { const sc = licScope(); return `${sc.vcenter}${sc.cluster !== 'all' ? '_' + sc.cluster : (sc.clusters.length ? '_' + sc.clusters.map(k => k.split('::')[1]).slice(0, 3).join('_') : '')}`.replace(/[^A-Za-z0-9_-]+/g, '_'); }

async function licExportHardware() {
  const st = licState();
  const cols = [['vcenterKey', 'vCenter'], ['cluster', 'Cluster'], ['host', 'Host'], ['vendor', 'Vendor'], ['model', 'Model'], ['esxiVersion', 'ESXi version'], ['esxiBuild', 'ESXi build'], ['cpuModel', 'CPU model'], ['sockets', 'CPU socket'], ['cores', 'Physical core'], ['threads', 'Logical thread'], ['ramGB', 'RAM GB'], ['vmCount', 'Số VM'], ['clusterHa', 'HA'], ['clusterDrs', 'DRS'], ['wsLicensableCores', 'WS licensable core'], ['vmwareCores', 'VMware core'], ['flags', 'Giả định']].map(([key, label]) => ({ key, label }));
  const rows = st.inv.hosts.map(h => ({ ...h, clusterHa: licBool(h.clusterHa), clusterDrs: licBool(h.clusterDrs), flags: (h.flags || []).join(', ') }));
  await api.downloadXlsx({ filename: `VCO_ESXi_inventory_${licScopeSlug()}`, sheets: [{ name: 'ESXi hosts', columns: cols, rows }] });
}

async function licExportOs() {
  const rows = licFilteredVms().map(v => ({ vcenterKey: v.vcenterKey, cluster: v.cluster, host: v.host, vm: v.vm, power: v.isPoweredOn ? 'Bật' : 'Tắt', vcpu: v.vcpu, ramGB: v.ramGB, category: v.os.categoryLabel, osVersion: v.os.osVersion || '', osEdition: v.os.osEdition || '', source: LIC_SOURCE_LABEL[v.os.source] || v.os.source, confidence: LIC_CONF_LABEL[v.os.confidence] || v.os.confidence, guestId: v.os.evidence?.guestId || '', guestFullName: v.os.evidence?.guestFullName || '', toolsGuestFullName: v.os.evidence?.toolsGuestFullName || '', toolsStatus: v.os.evidence?.toolsStatus || '', note: v.os.note || '' }));
  const cols = [['vcenterKey', 'vCenter'], ['cluster', 'Cluster'], ['host', 'Host'], ['vm', 'VM'], ['power', 'Trạng thái'], ['vcpu', 'vCPU'], ['ramGB', 'RAM GB'], ['category', 'Phân loại'], ['osVersion', 'OS version'], ['osEdition', 'Edition'], ['source', 'Nguồn'], ['confidence', 'Độ tin cậy'], ['guestId', 'guestId'], ['guestFullName', 'Cấu hình VM'], ['toolsGuestFullName', 'VMware Tools'], ['toolsStatus', 'Tools status'], ['note', 'Ghi chú']].map(([key, label]) => ({ key, label }));
  await api.downloadXlsx({ filename: `VCO_VM_OS_inventory_${licScopeSlug()}`, sheets: [{ name: 'VM OS', columns: cols, rows }] });
}

async function licExportAssess() {
  const st = licState();
  const a = st.viewingSnapshot ? st.viewingSnapshot.result : st.assess;
  const ws = a.windowsServer, vm = a.vmware;
  const summary = [
    { k: 'Kết luận chung', v: LIC_STATUS[a.overallStatus]?.label || a.overallStatus },
    { k: 'Phạm vi', v: `${(a.scope.vcenterKeys || ['Tất cả vCenter']).join(', ')}${a.scope.clusters ? ' · ' + a.scope.clusters.join(', ') : ' · tất cả cluster'}` },
    ...(a.scope.partial ? [{ k: 'Cluster ngoài phạm vi (chưa tính)', v: (a.scope.excludedClusterKeys || []).map(k => k.split('::')[1]).join(', ') }] : []),
    { k: 'Thời điểm tính', v: a.generatedAt || '' }, { k: 'Hash kết quả', v: a.resultHash || '' },
    { k: 'Rule Windows Server', v: a.rules.windows_server ? `${a.rules.windows_server.name} v${a.rules.windows_server.version} (${a.rules.windows_server.status})` : '—' },
    { k: 'Rule VMware', v: a.rules.vmware ? `${a.rules.vmware.name} v${a.rules.vmware.version} (${a.rules.vmware.status})` : '—' },
    { k: 'VMware core nhu cầu', v: vm.demandCores }, { k: 'VMware core entitlement', v: vm.entitledCores }, { k: 'VMware core thiếu', v: vm.shortfallCores },
    { k: 'WS Standard core nhu cầu', v: `${ws.standard.demandCores}${ws.standard.demandCoresMax !== ws.standard.demandCores ? ' – ' + ws.standard.demandCoresMax : ''}` }, { k: 'WS Standard entitlement', v: ws.standard.entitledCores }, { k: 'WS Standard thiếu', v: ws.standard.shortfallCores },
    { k: 'WS Datacenter core nhu cầu', v: ws.datacenter.demandCores }, { k: 'WS Datacenter entitlement', v: ws.datacenter.entitledCores }, { k: 'WS Datacenter thiếu', v: ws.datacenter.shortfallCores },
    ...(ws.standard.cost ? [{ k: 'Chi phí bù thiếu Standard', v: ws.standard.cost.shortfallCost }] : []), ...(ws.datacenter.cost ? [{ k: 'Chi phí bù thiếu Datacenter', v: ws.datacenter.cost.shortfallCost }] : []), ...(vm.cost ? [{ k: 'Chi phí bù thiếu VMware', v: vm.cost.shortfallCost }] : []),
    ...ws.formula.map((x, i) => ({ k: `Công thức WS ${i + 1}`, v: x })), ...vm.formula.map((x, i) => ({ k: `Công thức VMware ${i + 1}`, v: x })),
    ...ws.assumptions.map((x, i) => ({ k: `Giả định WS ${i + 1}`, v: x })), ...vm.assumptions.map((x, i) => ({ k: `Giả định VMware ${i + 1}`, v: x })),
    ...[...ws.dataGaps, ...vm.dataGaps].map((x, i) => ({ k: `Thiếu dữ liệu ${i + 1}`, v: x })),
  ];
  const clusters = a.clusters.map(c => ({ vcenterKey: c.vcenterKey, cluster: c.cluster, hostCount: c.hostCount, sockets: c.sockets, physicalCores: c.physicalCores, wsLicensableCores: c.wsLicensableCores, ha: licBool(c.ha), drs: licBool(c.drs), wsVms: c.wsVms, wsVmsLowConfidence: c.wsVmsLowConfidence, unknownVms: c.unknownVms, stdSets: c.standard.sets, stdCores: c.standard.cores, stdCoresMax: c.standard.coresMax, dcCores: c.datacenter.cores, vmwareCores: c.vmwareCores, recommended: c.recommendedEdition || '' }));
  const ccols = [['vcenterKey', 'vCenter'], ['cluster', 'Cluster'], ['hostCount', 'Host'], ['sockets', 'Socket'], ['physicalCores', 'Physical core'], ['wsLicensableCores', 'WS licensable core'], ['ha', 'HA'], ['drs', 'DRS'], ['wsVms', 'VM Windows Server'], ['wsVmsLowConfidence', 'WS confidence thấp'], ['unknownVms', 'VM chưa rõ'], ['stdSets', 'Bộ Standard/host'], ['stdCores', 'WS Standard core'], ['stdCoresMax', 'WS Standard core tối đa'], ['dcCores', 'WS Datacenter core'], ['vmwareCores', 'VMware core'], ['recommended', 'Edition ít core hơn']].map(([key, label]) => ({ key, label }));
  const hcols = [['vcenterKey', 'vCenter'], ['cluster', 'Cluster'], ['host', 'Host'], ['model', 'Model'], ['esxiVersion', 'ESXi'], ['sockets', 'Socket'], ['cores', 'Physical core'], ['threads', 'Logical thread'], ['wsLicensableCores', 'WS licensable core'], ['vmwareCores', 'VMware core'], ['wsVmsOnHost', 'WS VM trên host'], ['vmCount', 'VM']].map(([key, label]) => ({ key, label }));
  const ents = (a.entitlementsUsed || []).map(e => ({ product: e.label || e.product, kind: LIC_KIND_LABEL[e.kind || 'owned'], metric: e.metric, quantity: e.quantity, unitPrice: e.unitPrice, currency: e.currency, hasSa: licBool(e.hasSa), scope: `${e.scopeVcenter || 'Tất cả'} / ${e.scopeCluster || 'Tất cả'}`, note: e.note || '' }));
  const ecols = [['product', 'Sản phẩm'], ['kind', 'Tình trạng'], ['metric', 'Đơn vị'], ['quantity', 'Số lượng'], ['unitPrice', 'Đơn giá'], ['currency', 'Tiền tệ'], ['hasSa', 'SA'], ['scope', 'Phạm vi'], ['note', 'Ghi chú']].map(([key, label]) => ({ key, label }));
  await api.downloadXlsx({ filename: `VCO_License_assessment_${licScopeSlug()}${st.viewingSnapshot ? '_snapshot' + st.viewingSnapshot.id : ''}`, sheets: [
    { name: 'Tóm tắt', columns: [{ key: 'k', label: 'Chỉ tiêu' }, { key: 'v', label: 'Giá trị' }], rows: summary },
    { name: 'Theo cluster', columns: ccols, rows: clusters }, { name: 'Theo host', columns: hcols, rows: a.hosts }, { name: 'Entitlement', columns: ecols, rows: ents },
  ] });
}

async function licExportWhatif() {
  const r = licState().whatif.result; if (!r) return;
  const rows = Object.entries(r.delta).filter(([, x]) => x && x.before !== null && x.before !== undefined).map(([k, x]) => ({ k, before: x.before, after: x.after, delta: x.delta }));
  const cap = r.capacityAfter.map((a, i) => ({ cluster: a.cluster, hostsBefore: r.capacityBefore[i].hosts, hostsAfter: a.hosts, cpuBefore: r.capacityBefore[i].cpuPct, cpuAfter: a.cpuPct, ramBefore: r.capacityBefore[i].ramPct, ramAfter: a.ramPct, haBefore: r.capacityBefore[i].haSurvives, haAfter: a.haSurvives }));
  await api.downloadXlsx({ filename: `VCO_License_whatif_${licScopeSlug()}`, sheets: [
    { name: 'Kịch bản', columns: [{ key: 'k', label: 'Mục' }, { key: 'v', label: 'Giá trị' }], rows: [{ k: 'Mô tả', v: r.description }, { k: 'Kết luận capacity', v: r.capacityVerdict }, { k: 'Đề xuất được', v: r.recommendable ? 'Có' : 'Không' }, ...r.insights.map((x, i) => ({ k: `Insight ${i + 1}`, v: x }))] },
    { name: 'License delta', columns: [{ key: 'k', label: 'Chỉ số' }, { key: 'before', label: 'Trước' }, { key: 'after', label: 'Sau' }, { key: 'delta', label: 'Δ' }], rows },
    { name: 'Capacity', columns: [{ key: 'cluster', label: 'Cluster' }, { key: 'hostsBefore', label: 'Host trước' }, { key: 'hostsAfter', label: 'Host sau' }, { key: 'cpuBefore', label: 'CPU % trước' }, { key: 'cpuAfter', label: 'CPU % sau' }, { key: 'ramBefore', label: 'RAM % trước' }, { key: 'ramAfter', label: 'RAM % sau' }, { key: 'haBefore', label: 'N+1 trước' }, { key: 'haAfter', label: 'N+1 sau' }], rows: cap },
  ] });
}
