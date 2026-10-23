/* =========================================================
   Sizing máy chủ — size physical hosts from a requested-VM list.
   Input: the Dev team's VM list (typed in or imported from Excel/CSV),
   sizing assumptions and candidate host configurations.
   Output: hosts needed per configuration (N+x), binding resource,
   resulting overcommit, storage to provision, budget, plus a check of
   whether an existing cluster could absorb the request instead.
   Calculation runs on the backend (backend/app/sizing.py) so the Excel
   export and a future PDF share the same math.
   ========================================================= */

const SIZING_STORE_KEY = 'vco.sizing.v1';

const SIZING_DEFAULT_PARAMS = {
  environment: '', cpu_ratio: 3, ht_factor: 2, ram_ratio: 1.0, max_util_pct: 80, growth_pct: 20,
  redundancy: 1, min_hosts: 2, reserve_cores: 2, reserve_ram_gb: 16,
  storage_mode: 'san', storage_overhead_pct: 25, storage_protection_factor: 2.0,
};
const SIZING_DEFAULT_PROFILES = [
  { name: '2×16C / 512 GB', sockets: 2, coresPerSocket: 16, ramGB: 512, diskGB: 0, unitPrice: null },
  { name: '2×24C / 768 GB', sockets: 2, coresPerSocket: 24, ramGB: 768, diskGB: 0, unitPrice: null },
  { name: '2×32C / 1 TB', sockets: 2, coresPerSocket: 32, ramGB: 1024, diskGB: 0, unitPrice: null },
];

let sizingSeq = 1;
const nextSizingId = () => `sz${Date.now().toString(36)}${(sizingSeq++).toString(36)}`;

function sizingState() {
  if (!appState.sizing) {
    appState.sizing = { vms: [], params: { ...SIZING_DEFAULT_PARAMS }, profiles: deepClone(SIZING_DEFAULT_PROFILES), result: null, warnings: [], bound: false };
    try {
      const raw = localStorage.getItem(SIZING_STORE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        if (Array.isArray(saved.vms)) appState.sizing.vms = saved.vms;
        if (saved.params) appState.sizing.params = { ...SIZING_DEFAULT_PARAMS, ...saved.params };
        if (Array.isArray(saved.profiles) && saved.profiles.length) appState.sizing.profiles = saved.profiles;
      }
    } catch (e) { /* ignore corrupt storage */ }
    appState.sizing.vms.forEach(v => { if (!v.id) v.id = nextSizingId(); });
    appState.sizing.profiles.forEach(p => { if (!p.id) p.id = nextSizingId(); });
  }
  return appState.sizing;
}

function persistSizing() {
  const st = sizingState();
  try {
    localStorage.setItem(SIZING_STORE_KEY, JSON.stringify({ vms: st.vms, params: st.params, profiles: st.profiles }));
  } catch (e) { /* storage full / disabled */ }
}

/* ---------- Formatting helpers ---------- */
const szNum = (v, d = 0) => (v === null || v === undefined || !isFinite(v)) ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
const szGB = (gb) => (gb === null || gb === undefined || !isFinite(gb)) ? '—' : (gb >= 1024 ? `${szNum(gb / 1024, 1)} TB` : `${szNum(gb)} GB`);
const szMoney = (v) => (v === null || v === undefined || !isFinite(v)) ? '—' : Number(v).toLocaleString('vi-VN');
const szOptNum = (v) => { const s = String(v ?? '').trim(); if (!s) return null; const n = cleanNum(s); return isFinite(n) && n > 0 ? n : null; };

/* ---------- Params <-> DOM ---------- */
const SIZING_PARAM_FIELDS = {
  environment: 'szEnv', cpu_ratio: 'szCpuRatio', ht_factor: 'szHt', ram_ratio: 'szRamRatio', max_util_pct: 'szUtil',
  growth_pct: 'szGrowth', redundancy: 'szRedundancy', min_hosts: 'szMinHosts', reserve_cores: 'szReserveCores',
  reserve_ram_gb: 'szReserveRam', storage_mode: 'szStorageMode', storage_overhead_pct: 'szStorageOverhead',
  storage_protection_factor: 'szProtection',
};

function sizingParamsToDom() {
  const p = sizingState().params;
  Object.entries(SIZING_PARAM_FIELDS).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (el) el.value = p[key] ?? '';
  });
  $('#szProtectionField').classList.toggle('hidden', p.storage_mode !== 'local');
}

function sizingParamsFromDom() {
  const p = sizingState().params;
  Object.entries(SIZING_PARAM_FIELDS).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (key === 'environment' || key === 'storage_mode') p[key] = el.value.trim();
    else if (key === 'redundancy' || key === 'min_hosts') p[key] = Math.max(0, Math.round(cleanNum(el.value)));
    else p[key] = cleanNum(el.value);
  });
  if (p.min_hosts < 1) p.min_hosts = 1;
  $('#szProtectionField').classList.toggle('hidden', p.storage_mode !== 'local');
  persistSizing();
  return p;
}

/* ---------- VM table ---------- */
function sizingTotals(vms) {
  return vms.reduce((a, v) => {
    const q = Math.max(0, Math.round(cleanNum(v.qty)));
    a.count += q; a.vcpu += q * cleanNum(v.vcpu); a.ram += q * cleanNum(v.ramGB); a.disk += q * cleanNum(v.diskGB);
    return a;
  }, { count: 0, vcpu: 0, ram: 0, disk: 0 });
}

function renderSizingVmTable() {
  const st = sizingState();
  const tbody = $('#sizingVmTable tbody');
  if (!st.vms.length) {
    tbody.innerHTML = `<tr><td colspan="11"><div class="empty-note">Chưa có VM nào. Bấm <b>+ Thêm dòng</b> để nhập tay, <b>Import Excel/CSV</b> để đọc file từ team Dev, hoặc <b>Nạp ví dụ</b> để xem thử.</div></td></tr>`;
  } else {
    tbody.innerHTML = st.vms.map((v, i) => {
      const q = Math.max(0, Math.round(cleanNum(v.qty)));
      return `
      <tr data-id="${v.id}">
        <td class="muted">${i + 1}</td>
        <td><input class="sz-cell sz-name" value="${esc(v.name)}" placeholder="VD: APP-WEB"></td>
        <td><input class="sz-cell sz-qty num" value="${esc(v.qty)}"></td>
        <td><input class="sz-cell sz-vcpu num" value="${esc(v.vcpu)}"></td>
        <td><input class="sz-cell sz-ram num" value="${esc(v.ramGB)}"></td>
        <td><input class="sz-cell sz-disk num" value="${esc(v.diskGB)}"></td>
        <td class="sz-total">${szNum(q * cleanNum(v.vcpu))}</td>
        <td class="sz-total">${szGB(q * cleanNum(v.ramGB))}</td>
        <td class="sz-total">${szGB(q * cleanNum(v.diskGB))}</td>
        <td><input class="sz-cell sz-note" value="${esc(v.note || '')}" placeholder="Vai trò, OS…"></td>
        <td><button class="btn btn-secondary btn-sm icon-btn sz-remove" type="button" title="Xoá dòng">✕</button></td>
      </tr>`;
    }).join('');
  }
  const t = sizingTotals(st.vms);
  $('#sizingVmFoot').innerHTML = st.vms.length
    ? `<b>${st.vms.length}</b> dòng · <b>${szNum(t.count)}</b> VM · <b>${szNum(t.vcpu)}</b> vCPU · <b>${szGB(t.ram)}</b> RAM · <b>${szGB(t.disk)}</b> disk`
    : '';

  // Bind cell edits: update state on input, recompute totals on change.
  $$('#sizingVmTable tbody tr[data-id]').forEach(tr => {
    const row = st.vms.find(v => v.id === tr.dataset.id);
    if (!row) return;
    const sync = () => {
      row.name = tr.querySelector('.sz-name').value;
      row.qty = tr.querySelector('.sz-qty').value;
      row.vcpu = tr.querySelector('.sz-vcpu').value;
      row.ramGB = tr.querySelector('.sz-ram').value;
      row.diskGB = tr.querySelector('.sz-disk').value;
      row.note = tr.querySelector('.sz-note').value;
      persistSizing();
    };
    tr.querySelectorAll('.sz-cell').forEach(inp => {
      inp.addEventListener('input', sync);
      inp.addEventListener('change', () => { sync(); renderSizingVmTable(); });
      inp.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); sync(); if (tr === tr.parentElement.lastElementChild) { addSizingVmRow(); } else { renderSizingVmTable(); } }
      });
    });
    tr.querySelector('.sz-remove').addEventListener('click', () => {
      st.vms = st.vms.filter(v => v.id !== row.id);
      persistSizing(); renderSizingVmTable();
    });
  });
}

function addSizingVmRow(preset) {
  const st = sizingState();
  st.vms.push({ id: nextSizingId(), name: '', qty: 1, vcpu: 4, ramGB: 16, diskGB: 100, note: '', ...(preset || {}) });
  persistSizing();
  renderSizingVmTable();
  const last = $('#sizingVmTable tbody tr:last-child .sz-name');
  if (last) last.focus();
}

/* ---------- Candidate host profiles ---------- */
function renderSizingProfiles() {
  const st = sizingState();
  const list = $('#sizingProfiles');
  const local = st.params.storage_mode === 'local';
  list.innerHTML = st.profiles.map(p => `
    <div class="multi-row-item sz-profile" data-id="${p.id}">
      <div class="sim-form-grid sz-profile-grid">
        <div class="field"><label>Tên cấu hình</label><input class="pName" value="${esc(p.name)}" placeholder="VD: R760 2×24C"></div>
        <div class="field"><label>Sockets</label><input class="pSockets" value="${esc(p.sockets)}"></div>
        <div class="field"><label>Cores / socket</label><input class="pCores" value="${esc(p.coresPerSocket)}"></div>
        <div class="field"><label>RAM GB / host</label><input class="pRam" value="${esc(p.ramGB)}"></div>
        <div class="field ${local ? '' : 'hidden'}"><label>Đĩa cục bộ GB / host</label><input class="pDisk" value="${esc(p.diskGB || 0)}"></div>
        <div class="field"><label>Đơn giá / host (tuỳ chọn)</label><input class="pPrice" value="${p.unitPrice == null ? '' : esc(p.unitPrice)}" placeholder="VND"></div>
      </div>
      <button class="btn btn-secondary btn-sm icon-btn sz-profile-remove" type="button" title="Xoá cấu hình" ${st.profiles.length <= 1 ? 'disabled' : ''}>✕</button>
    </div>`).join('');

  $$('#sizingProfiles .sz-profile').forEach(el => {
    const p = st.profiles.find(x => x.id === el.dataset.id);
    if (!p) return;
    const sync = () => {
      p.sockets = Math.max(1, Math.round(cleanNum(el.querySelector('.pSockets').value)) || 1);
      p.coresPerSocket = Math.max(1, Math.round(cleanNum(el.querySelector('.pCores').value)) || 1);
      p.ramGB = Math.max(1, cleanNum(el.querySelector('.pRam').value) || 1);
      p.diskGB = cleanNum(el.querySelector('.pDisk').value);
      p.unitPrice = szOptNum(el.querySelector('.pPrice').value);
      const typed = el.querySelector('.pName').value.trim();
      p.name = typed || `${p.sockets}×${p.coresPerSocket}C / ${p.ramGB >= 1024 ? (p.ramGB / 1024).toLocaleString('en-US', { maximumFractionDigits: 1 }) + ' TB' : p.ramGB + ' GB'}`;
      persistSizing();
    };
    el.querySelectorAll('input').forEach(inp => inp.addEventListener('change', () => { sync(); renderSizingProfiles(); }));
    el.querySelector('.sz-profile-remove').addEventListener('click', () => {
      st.profiles = st.profiles.filter(x => x.id !== p.id);
      persistSizing(); renderSizingProfiles();
    });
  });
}

/* ---------- Payload + run ---------- */
function sizingPayload() {
  const st = sizingState();
  sizingParamsFromDom();
  const vms = st.vms
    .map(v => ({ name: String(v.name || '').trim(), qty: Math.max(0, Math.round(cleanNum(v.qty))), vcpu: cleanNum(v.vcpu), ramGB: cleanNum(v.ramGB), diskGB: cleanNum(v.diskGB), note: String(v.note || '') }))
    .filter(v => v.qty > 0 && (v.vcpu > 0 || v.ramGB > 0));
  const profiles = st.profiles.map(p => ({ name: p.name, sockets: p.sockets, coresPerSocket: p.coresPerSocket, ramGB: p.ramGB, diskGB: p.diskGB || 0, unitPrice: p.unitPrice == null ? null : p.unitPrice }));
  const params = { ...st.params };
  if (params.storage_mode !== 'local') params.storage_protection_factor = 1.0;
  return { vms, params, profiles };
}

async function runSizing(btn) {
  const payload = sizingPayload();
  if (!payload.vms.length) {
    showValidationBanner(['Danh sách VM trống hoặc chưa có dòng nào hợp lệ (cần số lượng > 0 và vCPU/RAM > 0).'], []);
    return;
  }
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Đang tính…'; }
  try {
    const result = await api.calcSizing(payload);
    sizingState().result = result;
    renderSizingResult(result, payload);
  } catch (err) {
    showValidationBanner([`Không tính được sizing: ${err.message}`], []);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

/* ---------- Existing-cluster absorption check ---------- */
function sizingAbsorptionRows(demand) {
  if (!appState.hosts || !appState.hosts.length) return [];
  const clusters = getClusterList();
  return clusters.map(name => {
    const hosts = appState.hosts.filter(h => h.cluster === name);
    const vms = appState.vms.filter(v => v.cluster === name);
    const before = buildCapacityModel(hosts, vms);
    const synthetic = { vm: '__sizing__', cluster: name, isPoweredOn: true, vcpu: demand.vcpuPlanned, ramGB: demand.ramPlannedGB, diskProvGB: 0, diskUsedGB: 0 };
    const after = buildCapacityModel(hosts, vms.concat([synthetic]));
    // N+1 after absorbing: drop the largest host.
    let n1 = null;
    if (hosts.length > 1) {
      const biggest = hosts.reduce((a, h) => (h.ramGB > a.ramGB ? h : a), hosts[0]);
      const rest = hosts.filter(h => h !== biggest);
      const m = buildCapacityModel(rest, vms.concat([synthetic]));
      n1 = { cpuPct: m.cpuPct, ramPct: m.ramPct, ok: m.cpuPct <= 100 && m.ramPct <= 100 };
    }
    const worst = Math.max(after.cpuPct || 0, after.ramPct || 0);
    const cls = worst > 100 ? 'crit' : worst > 80 ? 'warn' : 'ok';
    return { name, hostCount: hosts.length, before, after, n1, cls };
  });
}

/* ---------- Result rendering ---------- */
function renderSizingResult(result, payload, scroll = true) {
  const area = $('#sizingResult');
  const d = result.demand;
  const rows = result.results;
  const best = result.recommendedIndex != null ? rows[result.recommendedIndex] : null;
  const p = result.params;
  const anyPrice = rows.some(r => r.budget != null);
  const local = p.storage_mode === 'local';

  const tiles = `
    <div class="kpi-grid sz-kpis">
      <article class="kpi-card"><div class="label">Số VM yêu cầu</div><div class="value">${szNum(d.vmCount)}</div><div class="sub">${payload.vms.length} dòng · TB ${szNum(d.avgVcpu, 1)} vCPU / ${szGB(d.avgRamGB)} / VM</div></article>
      <article class="kpi-card"><div class="label">vCPU</div><div class="value">${szNum(d.vcpu)} <span class="sz-arrow">→</span> ${szNum(d.vcpuPlanned)}</div><div class="sub">sau +${szNum(p.growth_pct)}% tăng trưởng · cần ${szNum(d.logicalCpuNeeded)} pCPU logic @ ${p.cpu_ratio}x</div></article>
      <article class="kpi-card"><div class="label">RAM</div><div class="value">${szGB(d.ramGB)} <span class="sz-arrow">→</span> ${szGB(d.ramPlannedGB)}</div><div class="sub">sau tăng trưởng · RAM vật lý cần ${szGB(d.physRamNeededGB)} @ ${p.ram_ratio}x</div></article>
      <article class="kpi-card"><div class="label">${local ? 'Lưu trữ cục bộ cần' : 'Dung lượng SAN cần cấp'}</div><div class="value">${szGB(d.storageNeededGB)}</div><div class="sub">từ ${szGB(d.diskGB)} yêu cầu · +${szNum(p.growth_pct)}% tăng trưởng · +${szNum(p.storage_overhead_pct)}% overhead${local ? ` · ×${p.storage_protection_factor} bảo vệ` : ''}</div></article>
    </div>`;

  const cmpRows = rows.map(r => {
    const pr = r.profile;
    const failTxt = r.failure.hostsLeft ? `${szNum(r.failure.cpuPct)}% / ${szNum(r.failure.ramPct)}%` : '—';
    const failPill = r.redundancy === 0 ? '<span class="pill na">N</span>' : (r.failure.ok ? `<span class="pill ok">Đạt N+${r.redundancy}</span>` : `<span class="pill crit">Chưa đạt</span>`);
    const storageCell = local
      ? `${szGB(r.storage.perHostNeededGB)}/host ${r.storage.ok === false ? '<span class="pill crit">thiếu đĩa</span>' : ''}`
      : '';
    return `
      <tr class="${r.recommended ? 'sz-best' : ''}">
        <td><b>${esc(pr.name)}</b><div class="muted-sub">${pr.sockets}×${pr.coresPerSocket}C = ${pr.cores} cores · ${szGB(pr.ramGB)} RAM${local ? ` · ${szGB(pr.diskGB)} đĩa` : ''}</div></td>
        <td class="num">${r.hostsForCpu}</td>
        <td class="num">${r.hostsForRam}</td>
        <td class="num">+${r.redundancy}</td>
        <td class="num sz-hosts"><b>${r.hostsTotal}</b></td>
        <td>${esc(r.binding)}</td>
        <td class="num">${szNum(r.totals.cores)} / ${szGB(r.totals.ramGB)}</td>
        <td class="num">${szNum(r.after.cpuOvercommit, 2)}x / ${szNum(r.after.ramOvercommit, 2)}x</td>
        <td class="num">${failTxt} ${failPill}</td>
        <td class="num">${r.spare.extraAvgVms == null ? '—' : '~' + szNum(r.spare.extraAvgVms)}</td>
        ${local ? `<td>${storageCell}</td>` : ''}
        ${anyPrice ? `<td class="num">${r.budget == null ? '—' : szMoney(r.budget)}</td>` : ''}
        <td>${r.recommended ? '<span class="pill ok">Đề xuất</span>' : `<span class="muted-sub">#${r.rank}</span>`}</td>
      </tr>`;
  }).join('');

  const absorption = sizingAbsorptionRows(d);
  let absorbHtml = '';
  if (absorption.length) {
    const canAbsorb = absorption.filter(a => a.cls === 'ok' && (!a.n1 || a.n1.ok));
    const absorbRows = absorption.map(a => `
      <tr>
        <td><b>${esc(a.name)}</b><div class="muted-sub">${a.hostCount} host · ${a.before.poweredOnCount} VM bật</div></td>
        <td class="num">${fmtPct(a.before.cpuPct)} → <b>${fmtPct(a.after.cpuPct)}</b></td>
        <td class="num">${fmtPct(a.before.ramPct)} → <b>${fmtPct(a.after.ramPct)}</b></td>
        <td class="num">${a.n1 ? `${fmtPct(a.n1.cpuPct)} / ${fmtPct(a.n1.ramPct)} ${a.n1.ok ? '<span class="pill ok">Đạt</span>' : '<span class="pill crit">Không</span>'}` : '—'}</td>
        <td><span class="pill ${a.cls}">${a.cls === 'ok' ? 'Hấp thụ được' : a.cls === 'warn' ? 'Sát ngưỡng' : 'Vượt ngưỡng'}</span></td>
      </tr>`).join('');
    absorbHtml = `
      <div class="sz-section-title">Hạ tầng hiện hữu có hấp thụ được không?</div>
      <p class="panel-sub">Giả định đưa toàn bộ nhu cầu (sau tăng trưởng) vào từng cluster đang có; % tính theo ngưỡng overcommit của chính sách hiện hành (${appState.policy.cpuOvercommitMax}x CPU · ${appState.policy.ramOvercommitMax}x RAM).</p>
      <div class="table-scroll"><table class="data-table sz-compare">
        <thead><tr><th>Cluster</th><th>CPU % ngưỡng (trước → sau)</th><th>RAM % ngưỡng (trước → sau)</th><th>Khi mất 1 host (CPU / RAM)</th><th>Kết luận</th></tr></thead>
        <tbody>${absorbRows}</tbody>
      </table></div>
      <div class="insight-box">${canAbsorb.length
        ? `<strong>Có thể chưa cần mua host mới:</strong> cluster ${canAbsorb.map(a => esc(a.name)).join(', ')} hấp thụ được toàn bộ nhu cầu mà vẫn dưới 80% ngưỡng và giữ N+1. Phương án mua host bên trên là để tách riêng dự án hoặc để dành dư địa.`
        : `<strong>Không cluster nào hấp thụ được toàn bộ nhu cầu</strong> mà vẫn an toàn — cần bổ sung host theo đề xuất bên trên (hoặc chia nhỏ dự án vào nhiều cluster).`}</div>`;
  }

  area.innerHTML = `
    <div class="panel-head"><div><div class="panel-title">Kết quả sizing${p.environment ? ` — ${esc(p.environment)}` : ''}</div><p class="panel-sub">Số host vật lý cần cho ${szNum(d.vmCount)} VM theo từng cấu hình ứng viên; phương án đề xuất là ít host nhất, rồi đến ngân sách thấp nhất.</p></div></div>
    ${best ? `<div class="insight-box sz-headline"><strong>${esc(result.recommendation.headline)}</strong><div class="sz-detail">${esc(result.recommendation.detail)}</div></div>` : ''}
    ${tiles}
    <div class="sz-section-title">So sánh cấu hình host</div>
    <div class="table-scroll"><table class="data-table sz-compare">
      <thead><tr>
        <th>Cấu hình</th><th title="Số host cần để đáp ứng vCPU ở overcommit mục tiêu và mức dùng tối đa">Host theo CPU</th><th title="Số host cần để đáp ứng RAM">Host theo RAM</th><th>Dự phòng</th><th>Tổng host</th><th>Ràng buộc</th>
        <th>Tổng cores / RAM</th><th title="Overcommit thực tế sau khi lắp đủ số host (trên phần năng lực khả dụng sau khi trừ ESXi)">OC CPU / RAM sau</th><th title="% năng lực mục tiêu đang dùng khi mất đúng số host dự phòng">Khi mất host (CPU / RAM)</th><th title="Số VM cỡ trung bình của dự án còn thêm được mà vẫn giữ dự phòng">Thêm được</th>
        ${local ? '<th>Đĩa cục bộ</th>' : ''}${anyPrice ? '<th>Ngân sách ước tính</th>' : ''}<th></th>
      </tr></thead>
      <tbody>${cmpRows}</tbody>
    </table></div>
    <div class="sz-assumptions"><b>Giả định:</b> ${result.assumptions.map(esc).join(' ')}</div>
    ${absorbHtml}
  `;
  area.classList.remove('hidden');
  if (scroll) area.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------- Import / template / sample / export ---------- */
async function importSizingFile(file) {
  if (!file) return;
  try {
    const parsed = await api.importSizingFile(file);
    const st = sizingState();
    const rows = (parsed.rows || []).map(r => ({ id: nextSizingId(), name: r.name, qty: r.qty, vcpu: r.vcpu, ramGB: r.ramGB, diskGB: r.diskGB, note: r.note || '' }));
    if (!rows.length) {
      showValidationBanner([`Không đọc được VM nào từ "${file.name}". ${(parsed.warnings || []).join(' ')}`], []);
      return;
    }
    st.vms = rows;
    persistSizing();
    renderSizingVmTable();
    const t = sizingTotals(rows);
    showValidationBanner(parsed.warnings || [], [`Đã import ${rows.length} dòng (${szNum(t.count)} VM · ${szNum(t.vcpu)} vCPU · ${szGB(t.ram)} RAM · ${szGB(t.disk)} disk) từ "${file.name}"${parsed.sheet ? `, sheet "${parsed.sheet}"` : ''}.`]);
    $('#sizingResult').classList.add('hidden');
  } catch (err) {
    showValidationBanner([`Import thất bại: ${err.message}`], []);
  }
}

async function loadSizingSample() {
  try {
    const defaults = await api.getSizingDefaults();
    const st = sizingState();
    st.vms = (defaults.sampleVms || []).map(r => ({ id: nextSizingId(), name: r.name, qty: r.qty, vcpu: r.vcpu, ramGB: r.ramGB, diskGB: r.diskGB, note: r.note || '' }));
    if (!st.params.environment) { st.params.environment = 'Ví dụ — ứng dụng 3 lớp'; sizingParamsToDom(); }
    persistSizing();
    renderSizingVmTable();
    $('#sizingResult').classList.add('hidden');
  } catch (err) {
    showValidationBanner([`Không nạp được ví dụ: ${err.message}`], []);
  }
}

async function exportSizingXlsx(btn) {
  const payload = sizingPayload();
  if (!payload.vms.length) { showValidationBanner(['Danh sách VM trống — chưa có gì để xuất.'], []); return; }
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = 'Đang tạo Excel…';
  try { await api.downloadSizingXlsx(payload); }
  catch (err) { showValidationBanner([`Không xuất được Excel: ${err.message}`], []); }
  finally { btn.disabled = false; btn.textContent = label; }
}

/* ---------- View entry + bindings ---------- */
function bindSizingView() {
  const st = sizingState();
  if (st.bound) return;
  st.bound = true;

  $('#btnSizingAddRow').addEventListener('click', () => addSizingVmRow());
  $('#btnSizingClear').addEventListener('click', () => {
    if (!st.vms.length || confirm('Xoá toàn bộ danh sách VM yêu cầu?')) { st.vms = []; persistSizing(); renderSizingVmTable(); $('#sizingResult').classList.add('hidden'); }
  });
  $('#btnSizingImport').addEventListener('click', () => $('#sizingFile').click());
  $('#sizingFile').addEventListener('change', e => { importSizingFile(e.target.files && e.target.files[0]); e.target.value = ''; });
  $('#btnSizingTemplate').addEventListener('click', async e => {
    const b = e.currentTarget; const l = b.textContent; b.disabled = true; b.textContent = 'Đang tải…';
    try { await api.downloadSizingTemplate(); } catch (err) { showValidationBanner([`Không tải được file mẫu: ${err.message}`], []); }
    finally { b.disabled = false; b.textContent = l; }
  });
  $('#btnSizingSample').addEventListener('click', loadSizingSample);
  $('#btnSizingAddProfile').addEventListener('click', () => {
    st.profiles.push({ id: nextSizingId(), name: '', sockets: 2, coresPerSocket: 32, ramGB: 1024, diskGB: 0, unitPrice: null });
    const last = st.profiles[st.profiles.length - 1];
    last.name = `${last.sockets}×${last.coresPerSocket}C / 1 TB`;
    persistSizing(); renderSizingProfiles();
  });
  $('#btnSizingRun').addEventListener('click', e => runSizing(e.currentTarget));
  $('#btnSizingExport').addEventListener('click', e => exportSizingXlsx(e.currentTarget));

  Object.values(SIZING_PARAM_FIELDS).forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', () => {
      sizingParamsFromDom();
      if (id === 'szStorageMode') renderSizingProfiles();
    });
  });
}

function renderSizingView() {
  sizingState();
  bindSizingView();
  sizingParamsToDom();
  renderSizingVmTable();
  renderSizingProfiles();
  if (appState.sizing.result) renderSizingResult(appState.sizing.result, sizingPayload(), false);
}

window.renderSizingView = renderSizingView;
