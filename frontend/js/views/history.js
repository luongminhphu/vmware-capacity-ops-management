/* =========================================================
   Snapshot history panel — CPU/RAM change over time per vCenter or
   cluster, with a 3 / 6 / 12-month look-back selector, first-vs-now
   delta summary and (Technical view) the list of VMs added / removed in
   the window. Data comes from /api/dashboard/history and
   /api/dashboard/vm-changes; the same backend functions feed the PDF
   reports so the numbers always agree.

   One panel template is mounted into any container (#execHistoryMount,
   #techHistoryMount) so both perspectives share the exact same logic.
   ========================================================= */

const HISTORY_MONTH_OPTIONS = [3, 6, 12];
const HISTORY_SERIES_COLORS = ['#2563eb', '#0891b2', '#7c3aed', '#db2777', '#ea580c', '#16a34a', '#ca8a04', '#64748b'];

const historyState = {
  months: 12,
  group: 'cluster', // one cluster = one project team; vCenter grouping is the secondary view
  loaded: false,
};

(function restoreHistoryPrefs() {
  try {
    const m = parseInt(localStorage.getItem('vco.historyMonths') || '', 10);
    if (HISTORY_MONTH_OPTIONS.includes(m)) historyState.months = m;
    const g = localStorage.getItem('vco.historyGroupV2');
    if (g === 'vcenter' || g === 'cluster') historyState.group = g;
  } catch (e) { /* ignore */ }
  historyState.loaded = true;
})();

function historyVcenterParam() {
  if (typeof currentVcenterFilter === 'undefined') return 'all';
  return currentVcenterFilter === 'import' ? 'import' : (currentVcenterFilter || 'all');
}

function fmtDeltaPts(v, unit = ' điểm') {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  const sign = v > 0 ? '+' : (v < 0 ? '−' : '±');
  return `${sign}${Math.abs(v).toFixed(1)}${unit}`;
}

function fmtDeltaInt(v) {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  const sign = v > 0 ? '+' : (v < 0 ? '−' : '±');
  return `${sign}${Math.abs(Math.round(v)).toLocaleString('en-US')}`;
}

function deltaTone(v, invert = false) {
  if (v === null || v === undefined || !isFinite(v) || Math.abs(v) < 0.05) return 'flat';
  const up = v > 0;
  return (up !== invert) ? 'up' : 'down';
}

function fmtShortDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function historyPanelTemplate(mountId, opts) {
  const monthsBtns = HISTORY_MONTH_OPTIONS.map(m =>
    `<button type="button" data-history-months="${m}" class="${historyState.months === m ? 'active' : ''}">${m} tháng</button>`).join('');
  const groupBtns = [['cluster', 'Theo cluster'], ['vcenter', 'Theo vCenter']].map(([k, label]) =>
    `<button type="button" data-history-group="${k}" class="${historyState.group === k ? 'active' : ''}">${label}</button>`).join('');
  return `
    <section class="panel history-panel" id="${mountId}Panel">
      <div class="panel-head">
        <div>
          <div class="panel-title">Biến động tài nguyên CPU/RAM theo thời gian</div>
          <p class="panel-sub" id="${mountId}Sub">So sánh mức sử dụng so với ngưỡng overcommit giữa các snapshot đã lưu — chọn khoảng thời gian và cách nhóm.</p>
        </div>
        <div class="history-controls">
          <div class="view-toggle history-toggle" role="group" aria-label="Khoảng thời gian">${monthsBtns}</div>
          <div class="view-toggle history-toggle" role="group" aria-label="Nhóm theo">${groupBtns}</div>
        </div>
      </div>
      <div class="history-kpis" id="${mountId}Kpis"></div>
      <div class="chart-grid history-chart-grid">
        <article class="card chart-card">
          <div class="panel-head"><div><div class="panel-title">CPU — % ngưỡng overcommit</div><p class="panel-sub">Mỗi đường là một ${historyState.group === 'cluster' ? 'cluster (nhóm dự án)' : 'vCenter'}; đường đứt là tổng của phạm vi đang xem.</p></div></div>
          <div class="chart-wrap"><canvas id="${mountId}ChartCpu"></canvas></div>
        </article>
        <article class="card chart-card">
          <div class="panel-head"><div><div class="panel-title">RAM — % ngưỡng overcommit</div><p class="panel-sub">Mỗi đường là một ${historyState.group === 'cluster' ? 'cluster (nhóm dự án)' : 'vCenter'}; đường đứt là tổng của phạm vi đang xem.</p></div></div>
          <div class="chart-wrap"><canvas id="${mountId}ChartRam"></canvas></div>
        </article>
      </div>
      <div class="history-insights insight-note" id="${mountId}Insights"></div>
      <div class="table-scroll">
        <table class="data-table history-table" id="${mountId}Table">
          <thead><tr>
            <th>${historyState.group === 'cluster' ? 'Cluster' : 'vCenter'}</th>
            <th>CPU % đầu kỳ → nay</th><th>Δ CPU</th>
            <th>RAM % đầu kỳ → nay</th><th>Δ RAM</th>
            <th>Đỉnh CPU / RAM</th>
            <th>Hosts</th><th>VMs</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
      ${opts.showVmChanges ? `
      <div class="history-vm-changes" id="${mountId}VmChanges"></div>` : ''}
    </section>`;
}

function historyEmpty(mountId, message) {
  const kpis = document.getElementById(`${mountId}Kpis`);
  if (kpis) kpis.innerHTML = `<div class="empty-state history-empty">${esc(message)}</div>`;
  ['Cpu', 'Ram'].forEach(k => {
    destroyChart(`${mountId}${k}`);
    if (typeof toggleChartEmptyState === 'function') toggleChartEmptyState(`${mountId}Chart${k}`, false, 'Chưa đủ lịch sử snapshot để vẽ biểu đồ.');
  });
  const tbody = document.querySelector(`#${mountId}Table tbody`);
  if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="muted">${esc(message)}</td></tr>`;
}

function renderHistoryKpis(mountId, data, vmChanges) {
  const box = document.getElementById(`${mountId}Kpis`);
  if (!box) return;
  const s = data.overall.summary;
  const d = s.delta || {};
  const card = (label, value, sub, tone) => `
    <article class="card history-kpi ${tone || ''}">
      <div class="history-kpi-label">${esc(label)}</div>
      <div class="history-kpi-value">${value}</div>
      <div class="history-kpi-sub">${sub}</div>
    </article>`;
  const cpuTone = deltaTone(d.cpuPct);
  const ramTone = deltaTone(d.ramPct);
  const vmTone = deltaTone(d.vmCount);
  const hostTone = deltaTone(d.hostCount);
  const range = `${fmtShortDate(s.firstDate)} → ${fmtShortDate(s.lastDate)} · ${s.points} snapshot`;
  const vmSub = vmChanges && vmChanges.available
    ? `<span class="delta up">+${vmChanges.addedTotals.count} thêm mới</span> · <span class="delta down">−${vmChanges.removedTotals.count} loại bỏ</span>`
    : `${fmtInt(s.first.vmCount)} → ${fmtInt(s.last.vmCount)} VM`;
  box.innerHTML = [
    card('CPU % ngưỡng overcommit', `${fmtNum(s.first.cpuPct, 1)}% → <strong>${fmtNum(s.last.cpuPct, 1)}%</strong>`,
      `<span class="delta ${cpuTone}">${fmtDeltaPts(d.cpuPct)}</span> · ${fmtDeltaInt(d.usedVcpu)} vCPU đang dùng`, cpuTone),
    card('RAM % ngưỡng overcommit', `${fmtNum(s.first.ramPct, 1)}% → <strong>${fmtNum(s.last.ramPct, 1)}%</strong>`,
      `<span class="delta ${ramTone}">${fmtDeltaPts(d.ramPct)}</span> · ${fmtDeltaInt(d.usedRamGB)} GB đang dùng`, ramTone),
    card('Số VM', `${fmtInt(s.first.vmCount)} → <strong>${fmtInt(s.last.vmCount)}</strong>`,
      `<span class="delta ${vmTone}">${fmtDeltaInt(d.vmCount)}</span> · ${vmSub}`, vmTone),
    card('Số host / năng lực vật lý', `${fmtInt(s.first.hostCount)} → <strong>${fmtInt(s.last.hostCount)}</strong>`,
      `<span class="delta ${hostTone}">${fmtDeltaInt(d.hostCount)} host</span> · ${fmtDeltaInt(d.totalRamGB)} GB RAM vật lý`, hostTone),
  ].join('') + `<div class="history-range muted">${esc(range)}</div>`;
}

function renderHistoryCharts(mountId, data) {
  if (typeof Chart === 'undefined') return;
  const colors = chartPalette();
  const dates = data.overall.series.map(p => p.date);
  const byDate = (series, field) => {
    const map = new Map(series.map(p => [p.date, p[field]]));
    return dates.map(dd => (map.has(dd) ? map.get(dd) : null));
  };
  ['Cpu', 'Ram'].forEach((k, idx) => {
    const field = idx === 0 ? 'cpuPct' : 'ramPct';
    const key = `${mountId}${k}`;
    destroyChart(key);
    const canvasId = `${mountId}Chart${k}`;
    if (typeof toggleChartEmptyState === 'function') toggleChartEmptyState(canvasId, true, '');
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const datasets = data.groups.map((g, i) => ({
      label: g.label,
      data: byDate(g.series, field),
      borderColor: HISTORY_SERIES_COLORS[i % HISTORY_SERIES_COLORS.length],
      backgroundColor: HISTORY_SERIES_COLORS[i % HISTORY_SERIES_COLORS.length] + '22',
      borderWidth: 2, pointRadius: dates.length > 40 ? 0 : 2.5, pointHoverRadius: 4,
      tension: 0.25, spanGaps: true, fill: false,
    }));
    if (data.groups.length !== 1) {
      datasets.push({
        label: data.overall.label || 'Toàn bộ', data: data.overall.series.map(p => p[field]),
        borderColor: colors.text, borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0,
        tension: 0.25, spanGaps: true, fill: false,
      });
    }
    const maxVal = Math.max(100, ...datasets.flatMap(ds => ds.data.filter(v => v !== null)));
    appState.charts[key] = new Chart(canvas, {
      type: 'line',
      data: { labels: dates, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { labels: { color: colors.text, boxWidth: 12, usePointStyle: true } },
          tooltip: {
            callbacks: {
              title: items => items.length ? fmtShortDate(items[0].label) : '',
              label: item => `${item.dataset.label}: ${item.parsed.y === null ? '—' : item.parsed.y.toFixed(1) + '%'}`,
            },
          },
          annotationThreshold: { value: 100, color: colors.crit, label: 'Ngưỡng 100%' },
        },
        scales: {
          x: {
            ticks: { color: colors.muted, maxTicksLimit: 8, maxRotation: 0, callback: (v, i) => fmtShortDate(dates[i]).slice(0, 5) },
            grid: { color: colors.divider },
          },
          y: {
            min: 0, suggestedMax: Math.min(120, Math.ceil(maxVal / 10) * 10 + 5),
            ticks: { color: colors.muted, callback: v => `${v}%` }, grid: { color: colors.divider },
          },
        },
      },
      plugins: [historyThresholdPlugin],
    });
  });
}

/* Tiny inline Chart.js plugin: dashed 100% threshold line with a label. */
const historyThresholdPlugin = {
  id: 'annotationThreshold',
  afterDraw(chart, _args, opts) {
    if (!opts || opts.value === undefined) return;
    const y = chart.scales.y;
    if (!y || opts.value > y.max) return;
    const ctx = chart.ctx;
    const yPx = y.getPixelForValue(opts.value);
    ctx.save();
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = opts.color || '#b91c1c';
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(chart.chartArea.left, yPx); ctx.lineTo(chart.chartArea.right, yPx); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = opts.color || '#b91c1c';
    ctx.font = '600 10px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(opts.label || '', chart.chartArea.right - 4, yPx - 4);
    ctx.restore();
  },
};

function renderHistoryTable(mountId, data) {
  const tbody = document.querySelector(`#${mountId}Table tbody`);
  if (!tbody) return;
  const rows = data.groups.map(g => g.summary).filter(s => s.points);
  if (data.groups.length !== 1) rows.push({ ...data.overall.summary, label: data.overall.label || 'Toàn bộ', isTotal: true });
  tbody.innerHTML = rows.map(s => {
    const d = s.delta || {};
    return `<tr class="${s.isTotal ? 'history-total-row' : ''}">
      <td><strong>${esc(s.label)}</strong></td>
      <td>${fmtNum(s.first.cpuPct, 1)}% → <strong>${fmtNum(s.last.cpuPct, 1)}%</strong></td>
      <td><span class="delta ${deltaTone(d.cpuPct)}">${fmtDeltaPts(d.cpuPct)}</span></td>
      <td>${fmtNum(s.first.ramPct, 1)}% → <strong>${fmtNum(s.last.ramPct, 1)}%</strong></td>
      <td><span class="delta ${deltaTone(d.ramPct)}">${fmtDeltaPts(d.ramPct)}</span></td>
      <td>${fmtNum(s.peakCpuPct, 0)}% / ${fmtNum(s.peakRamPct, 0)}%</td>
      <td>${fmtInt(s.first.hostCount)} → ${fmtInt(s.last.hostCount)} <span class="delta ${deltaTone(d.hostCount)}">(${fmtDeltaInt(d.hostCount)})</span></td>
      <td>${fmtInt(s.first.vmCount)} → ${fmtInt(s.last.vmCount)} <span class="delta ${deltaTone(d.vmCount)}">(${fmtDeltaInt(d.vmCount)})</span></td>
    </tr>`;
  }).join('') || `<tr><td colspan="8" class="muted">Chưa có dữ liệu.</td></tr>`;
}

function renderHistoryVmChanges(mountId, changes) {
  const box = document.getElementById(`${mountId}VmChanges`);
  if (!box) return;
  if (!changes || !changes.available) {
    box.innerHTML = `<div class="empty-state">Chưa đủ snapshot trong khoảng thời gian này để so sánh danh sách VM (cần ít nhất 2 lần đồng bộ).</div>`;
    return;
  }
  const vmRow = (r, dateField, dateLabel) => `<tr>
      <td><strong>${esc(r.vm)}</strong></td><td>${esc(r.vcenterLabel)}</td><td>${esc(r.cluster || '—')}</td>
      <td>${esc(r.host || '—')}</td><td><span class="pill ${r.isPoweredOn ? 'ok' : ''}">${esc(r.powerState || '—')}</span></td>
      <td>${fmtInt(r.vcpu)}</td><td>${fmtNum(r.ramGB, 0)}</td><td title="${esc(dateLabel)}">${fmtShortDate(r[dateField])}</td>
    </tr>`;
  const table = (title, sub, rows, dateField, dateLabel, tone) => `
    <article class="card history-vm-card ${tone}">
      <div class="panel-head">
        <div><div class="panel-title">${esc(title)} <span class="pill ${tone === 'added' ? 'ok' : 'warn'}">${rows.length} VM</span></div><p class="panel-sub">${esc(sub)}</p></div>
      </div>
      <div class="table-scroll history-vm-scroll">
        <table class="data-table">
          <thead><tr><th>VM</th><th>vCenter</th><th>Cluster</th><th>Host</th><th>Trạng thái</th><th>vCPU</th><th>RAM (GB)</th><th>${esc(dateLabel)}</th></tr></thead>
          <tbody>${rows.length ? rows.map(r => vmRow(r, dateField, dateLabel)).join('') : `<tr><td colspan="8" class="muted">Không có VM nào.</td></tr>`}</tbody>
        </table>
      </div>
    </article>`;
  const a = changes.addedTotals, r = changes.removedTotals;
  box.innerHTML = `
    <div class="history-vm-summary">
      Trong ${changes.months} tháng gần nhất: <span class="delta up">+${a.count} VM thêm mới</span> (${fmtInt(a.vcpu)} vCPU · ${fmtInt(a.ramGB)} GB RAM) và
      <span class="delta down">−${r.count} VM loại bỏ</span> (${fmtInt(r.vcpu)} vCPU · ${fmtInt(r.ramGB)} GB RAM). Cùng danh sách này được đưa vào phụ lục Báo cáo Kỹ thuật.
    </div>
    <div class="history-vm-grid">
      ${table('VM thêm mới', 'Có trong snapshot mới nhất nhưng chưa có ở snapshot đầu kỳ.', changes.added, 'firstSeen', 'Xuất hiện từ', 'added')}
      ${table('VM đã loại bỏ', 'Có ở snapshot đầu kỳ nhưng không còn trong snapshot mới nhất.', changes.removed, 'lastSeen', 'Thấy lần cuối', 'removed')}
    </div>`;
}

function bindHistoryControls(mountId, opts) {
  const panel = document.getElementById(`${mountId}Panel`);
  if (!panel || panel.dataset.bound) return;
  panel.dataset.bound = '1';
  panel.addEventListener('click', async (e) => {
    const mBtn = e.target.closest('[data-history-months]');
    const gBtn = e.target.closest('[data-history-group]');
    if (!mBtn && !gBtn) return;
    if (mBtn) historyState.months = parseInt(mBtn.dataset.historyMonths, 10);
    if (gBtn) historyState.group = gBtn.dataset.historyGroup;
    try {
      localStorage.setItem('vco.historyMonths', String(historyState.months));
      localStorage.setItem('vco.historyGroupV2', historyState.group);
    } catch (err) { /* ignore */ }
    // Re-render every mounted panel so Executive and Technical stay in sync.
    await Promise.all(Object.keys(historyMounts).map(id => renderHistoryPanel(id, historyMounts[id])));
  });
}

const historyMounts = {};

// Technical Dashboard follows the page's cluster filter so the history panel
// shows the same project-team scope as the tables above it. Executive View has
// no cluster filter and always shows every cluster in the vCenter scope.
function historyClustersParam(opts) {
  if (!opts || !opts.followClusterFilter) return null;
  const c = appState && appState.filters ? appState.filters.cluster : 'all';
  return c && c !== 'all' ? [c] : null;
}

async function renderHistoryPanel(mountId, opts = {}) {
  const mount = document.getElementById(mountId);
  if (!mount) return;
  historyMounts[mountId] = opts;
  ['Cpu', 'Ram'].forEach(k => destroyChart(`${mountId}${k}`));
  mount.innerHTML = historyPanelTemplate(mountId, opts);
  bindHistoryControls(mountId, opts);

  let data = null, changes = null;
  const clusters = historyClustersParam(opts);
  try {
    const vcenter = historyVcenterParam();
    const jobs = [api.getResourceHistory(vcenter, historyState.months, historyState.group, clusters)];
    jobs.push(api.getVmChanges(vcenter, historyState.months, clusters));
    [data, changes] = await Promise.all(jobs);
  } catch (err) {
    historyEmpty(mountId, `Không tải được lịch sử snapshot: ${err.message || err}`);
    return;
  }
  const sub = document.getElementById(`${mountId}Sub`);
  if (sub) {
    const scope = clusters ? ` · Cluster: ${clusters.join(', ')}` : '';
    sub.textContent = `${historyState.months} tháng gần nhất${scope} · ${data.pointCount} ngày có snapshot · % tính theo ngưỡng overcommit hiện tại (CPU ${appState.policy.cpuOvercommitMax}x, RAM ${appState.policy.ramOvercommitMax}x).`;
  }
  if (!data.available) {
    historyEmpty(mountId, data.pointCount === 1
      ? 'Mới có 1 ngày snapshot trong khoảng này — biểu đồ biến động sẽ xuất hiện sau lần đồng bộ tiếp theo (polling tự động theo lịch).'
      : 'Chưa có snapshot nào trong khoảng thời gian này. Hãy đồng bộ vCenter hoặc chờ polling tự động.');
    if (opts.showVmChanges) renderHistoryVmChanges(mountId, changes);
    return;
  }
  renderHistoryKpis(mountId, data, changes);
  renderHistoryCharts(mountId, data);
  const ins = document.getElementById(`${mountId}Insights`);
  if (ins) {
    // Backend-generated sentences (trusted HTML: only <b> tags around numbers/labels, labels escaped server-side).
    ins.innerHTML = (data.insights || []).map(t => `<div>${t}</div>`).join('');
    ins.classList.toggle('hidden', !(data.insights || []).length);
  }
  renderHistoryTable(mountId, data);
  if (opts.showVmChanges) renderHistoryVmChanges(mountId, changes);
}
