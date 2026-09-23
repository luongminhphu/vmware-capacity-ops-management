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
  const powerPill = (r) => `<span class="pill ${r.isPoweredOn ? 'ok' : ''}">${esc(r.powerState || '—')}</span>`;
  const vmRow = (r, dateField, dateLabel) => `<tr>
      <td><strong>${esc(r.vm)}</strong></td><td>${esc(r.vcenterLabel)}</td><td>${esc(r.cluster || '—')}</td>
      <td>${esc(r.host || '—')}</td><td>${powerPill(r)}</td>
      <td>${fmtInt(r.vcpu)}</td><td>${fmtNum(r.ramGB, 0)}</td><td title="${esc(dateLabel)}">${fmtShortDate(r[dateField])}</td>
    </tr>`;
  // Rows arrive sorted by cluster; insert one header row per cluster (project team).
  const groupedRows = (rows, rowFn, colspan) => {
    const out = [];
    let cur = Symbol('none');
    rows.forEach(r => {
      if (r.cluster !== cur) {
        cur = r.cluster;
        const n = rows.filter(x => x.cluster === cur).length;
        out.push(`<tr class="group-row"><td colspan="${colspan}"><strong>Cluster ${esc(cur || '—')}</strong> · ${n} VM</td></tr>`);
      }
      out.push(rowFn(r));
    });
    return out.join('');
  };
  const card = (title, sub, count, tone, headers, bodyHtml) => `
    <article class="card history-vm-card ${tone}">
      <div class="panel-head">
        <div><div class="panel-title">${esc(title)} <span class="pill ${tone === 'added' ? 'ok' : tone === 'removed' ? 'warn' : ''}">${count} VM</span></div><p class="panel-sub">${esc(sub)}</p></div>
      </div>
      <div class="table-scroll history-vm-scroll">
        <table class="data-table">
          <thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
          <tbody>${bodyHtml || `<tr><td colspan="${headers.length}" class="muted">Không có VM nào.</td></tr>`}</tbody>
        </table>
      </div>
    </article>`;
  const vmTable = (title, sub, rows, dateField, dateLabel, tone) => card(title, sub, rows.length, tone,
    ['VM', 'vCenter', 'Cluster', 'Host', 'Trạng thái', 'vCPU', 'RAM (GB)', dateLabel],
    rows.length ? groupedRows(rows, r => vmRow(r, dateField, dateLabel), 8) : '');

  const a = changes.addedTotals, r = changes.removedTotals;
  const mv = changes.movedTotals || { count: 0, cluster: 0, host: 0, vcpu: 0, ramGB: 0 };
  const rs = changes.resizedTotals || { count: 0, deltaVcpu: 0, deltaRamGB: 0, up: 0, down: 0 };
  const rn = changes.renamedTotals || { vms: 0, clusters: 0, hosts: 0 };
  const excluded = changes.excludedFalsePositives || 0;
  const net = (v) => `<span class="delta ${v > 0 ? 'up' : v < 0 ? 'down' : 'flat'}">${v > 0 ? '+' : ''}${v}</span>`;
  const signed = (v, unit) => { const n = Number(v) || 0; return `<span class="delta ${n > 0 ? 'up' : n < 0 ? 'down' : 'flat'}">${n > 0 ? '+' : ''}${fmtInt(n)}${unit ? ` ${unit}` : ''}</span>`; };
  const clusterCell = (b) => `<strong>${esc(b.cluster)}</strong>${b.renamedFrom ? `<div class="muted" style="font-size:.78em">trước: ${esc(b.renamedFrom)}</div>` : ''}`;
  const summaryRows = (changes.clusterSummary || []).map(b => `<tr>
      <td>${clusterCell(b)}</td><td>${esc(b.vcenterLabel)}</td><td>${fmtInt(b.baselineVmCount)} → ${fmtInt(b.currentVmCount)}</td><td>${net(b.netVm)}</td>
      <td><span class="delta up">+${fmtInt(b.added)}</span> <span class="muted">(${fmtInt(b.addedVcpu)} vCPU · ${fmtInt(b.addedRamGB)} GB)</span></td>
      <td><span class="delta down">−${fmtInt(b.removed)}</span> <span class="muted">(${fmtInt(b.removedVcpu)} vCPU · ${fmtInt(b.removedRamGB)} GB)</span></td>
      <td class="muted">${fmtInt(b.movedIn)} / ${fmtInt(b.movedOut)}</td>
      <td class="muted">${fmtInt(b.renamed)}</td>
      <td>${fmtInt(b.resized)} <span class="muted">(${signed(b.resizedVcpuDelta, 'vCPU')} · ${signed(b.resizedRamDelta, 'GB')})</span></td></tr>`).join('');
  const hostRows = (changes.hostSummary || []).map(b => `<tr>
      <td><strong>${esc(b.host)}</strong></td><td>${esc(b.cluster)}</td><td>${fmtInt(b.baselineVmCount)} → ${fmtInt(b.currentVmCount)}</td><td>${net(b.netVm)}</td>
      <td><span class="delta up">+${fmtInt(b.added)}</span> <span class="muted">(${fmtInt(b.addedVcpu)} vCPU · ${fmtInt(b.addedRamGB)} GB)</span></td>
      <td><span class="delta down">−${fmtInt(b.removed)}</span> <span class="muted">(${fmtInt(b.removedVcpu)} vCPU · ${fmtInt(b.removedRamGB)} GB)</span></td>
      <td class="muted">${fmtInt(b.movedIn)} / ${fmtInt(b.movedOut)}</td></tr>`).join('');

  const identityNote = changes.identity === 'uuid'
    ? 'Định danh VM theo instanceUuid của vCenter (ổn định khi rename/migrate).'
    : 'Chưa có instanceUuid trong snapshot — định danh theo tên VM; VM đổi tên được nhận diện theo cluster + IP + vCPU + RAM.';
  const excludedParts = [];
  if (mv.count) excludedParts.push(`${fmtInt(mv.count)} VM di chuyển (${fmtInt(mv.cluster)} giữa cluster · ${fmtInt(mv.host)} giữa host)`);
  if (rn.vms) excludedParts.push(`${fmtInt(rn.vms)} VM đổi tên`);
  if (rn.clusters || rn.hosts) excludedParts.push(`${fmtInt(rn.clusters)} cluster / ${fmtInt(rn.hosts)} host đổi tên`);
  const excludedNote = excluded
    ? ` Đã loại <strong>${fmtInt(excluded)}</strong> trường hợp không làm thay đổi tài nguyên khỏi thêm mới/loại bỏ: ${excludedParts.join(', ')}.`
    : ' Không phát hiện trường hợp di chuyển/đổi tên nào cần loại trừ.';
  const resizedNote = rs.count
    ? ` ${fmtInt(rs.count)} VM đổi cấu hình vCPU/RAM (${fmtInt(rs.up)} tăng · ${fmtInt(rs.down)} giảm · tổng ${signed(rs.deltaVcpu, 'vCPU')} · ${signed(rs.deltaRamGB, 'GB RAM')}) được tách riêng.`
    : '';
  const renamedList = [...(changes.renamedClusters || []).map(x => `Cluster ${esc(x.from)} → ${esc(x.to)} (${esc(x.vcenterLabel)}; trùng ${fmtNum((x.overlap || 0) * 100, 0)}% ${x.matchedBy === 'host' ? 'host' : 'VM'})`),
    ...(changes.renamedHosts || []).map(x => `Host ${esc(x.from)} → ${esc(x.to)} (${esc(x.vcenterLabel)}${x.cluster ? ` · ${esc(x.cluster)}` : ''}; trùng ${fmtNum((x.overlap || 0) * 100, 0)}% VM)`)];
  const renamedBox = renamedList.length
    ? `<div class="history-vm-note muted"><strong>Đổi tên cluster/host trong kỳ</strong> — số liệu đầu kỳ đã gộp về tên hiện tại, không tính là loại bỏ/thêm mới: ${renamedList.join('; ')}.</div>`
    : '';

  const movedRow = (m) => `<tr>
      <td><strong>${esc(m.vm)}</strong></td><td>${esc(m.vcenterLabel)}</td><td>${m.kind === 'cluster' ? 'Giữa cluster' : 'Giữa host (vMotion)'}</td>
      <td>${m.kind === 'cluster' ? `${esc(m.fromCluster)} → ${esc(m.toCluster)}` : esc(m.cluster || '—')}</td>
      <td>${esc(m.fromHost || '—')} → ${esc(m.toHost || '—')}</td><td>${powerPill(m)}</td><td>${fmtInt(m.vcpu)}</td><td>${fmtNum(m.ramGB, 0)}</td>
    </tr>`;
  const renamedRow = (x) => `<tr>
      <td class="muted">${esc(x.fromName)}</td><td><strong>${esc(x.toName)}</strong></td><td>${esc(x.vcenterLabel)}</td><td>${esc(x.cluster || '—')}</td>
      <td>${esc(x.host || '—')}</td><td>${fmtInt(x.vcpu)}</td><td>${fmtNum(x.ramGB, 0)}</td>
      <td class="muted">${x.matchedBy === 'uuid' ? 'cùng instanceUuid' : `cùng cluster + IP${x.ipAddress ? ` (${esc(x.ipAddress)})` : ''} + vCPU + RAM`}</td>
    </tr>`;
  const resizedRow = (x) => `<tr>
      <td><strong>${esc(x.vm)}</strong></td><td>${esc(x.vcenterLabel)}</td><td>${esc(x.cluster || '—')}</td><td>${esc(x.host || '—')}</td><td>${powerPill(x)}</td>
      <td>${fmtInt(x.vcpuBefore)} → ${fmtInt(x.vcpuAfter)} ${signed(x.deltaVcpu)}</td>
      <td>${fmtNum(x.ramBefore, 0)} → ${fmtNum(x.ramAfter, 0)} ${signed(x.deltaRamGB)}</td>
    </tr>`;
  const moved = changes.moved || [], renamedVms = changes.renamedVms || [], resized = changes.resized || [];

  box.innerHTML = `
    <div class="history-vm-summary">
      Trong ${changes.months} tháng gần nhất: <span class="delta up">+${a.count} VM thêm mới</span> (${fmtInt(a.vcpu)} vCPU · ${fmtInt(a.ramGB)} GB RAM) và
      <span class="delta down">−${r.count} VM loại bỏ</span> (${fmtInt(r.vcpu)} vCPU · ${fmtInt(r.ramGB)} GB RAM), đối chiếu định danh VM trên toàn vCenter.${excludedNote}${resizedNote}
      <span class="muted">${esc(identityNote)} Cùng danh sách này được đưa vào Phụ lục A của Báo cáo Kỹ thuật.</span>
    </div>
    ${renamedBox}
    <div class="table-scroll">
      <table class="data-table history-table">
        <thead><tr><th>Cluster</th><th>vCenter</th><th>VM đầu kỳ → nay</th><th>Chênh lệch</th><th>Thêm mới</th><th>Loại bỏ</th><th>Chuyển vào / ra</th><th>Đổi tên VM</th><th>Đổi cấu hình</th></tr></thead>
        <tbody>${summaryRows || '<tr><td colspan="9" class="muted">Không có cluster nào trong phạm vi.</td></tr>'}</tbody>
      </table>
    </div>
    ${hostRows ? `<div class="table-scroll" style="margin-top:var(--space-3)">
      <table class="data-table history-table">
        <thead><tr><th>Host (phạm vi đã chọn)</th><th>Cluster</th><th>VM đầu kỳ → nay</th><th>Chênh lệch</th><th>Thêm mới</th><th>Loại bỏ</th><th>Chuyển vào / ra</th></tr></thead>
        <tbody>${hostRows}</tbody>
      </table>
    </div>` : ''}
    <div class="history-vm-grid">
      ${vmTable('VM thêm mới', 'Định danh VM có trong snapshot mới nhất nhưng chưa có ở bất kỳ cluster/host nào của vCenter tại đầu kỳ.', changes.added, 'firstSeen', 'Xuất hiện từ', 'added')}
      ${vmTable('VM đã loại bỏ', 'Định danh VM có ở snapshot đầu kỳ nhưng không còn ở bất kỳ cluster/host nào của vCenter hiện nay.', changes.removed, 'lastSeen', 'Thấy lần cuối', 'removed')}
    </div>
    <div class="history-vm-grid">
      ${card('VM di chuyển — không tính thêm mới/loại bỏ', `vMotion giữa host hoặc migrate giữa cluster (${fmtInt(mv.vcpu)} vCPU · ${fmtInt(mv.ramGB)} GB RAM); di chuyển giữa host chỉ liệt kê khi lọc theo host.`, moved.length, 'moved',
        ['VM', 'vCenter', 'Loại', 'Cluster (từ → đến)', 'Host (từ → đến)', 'Trạng thái', 'vCPU', 'RAM (GB)'], moved.length ? groupedRows(moved, movedRow, 8) : '')}
      ${card('VM đổi tên — không tính thêm mới/loại bỏ', 'Cùng một VM nhưng tên khác giữa đầu kỳ và nay.', renamedVms.length, 'renamed',
        ['Tên cũ', 'Tên mới', 'vCenter', 'Cluster', 'Host', 'vCPU', 'RAM (GB)', 'Căn cứ'], renamedVms.length ? groupedRows(renamedVms, renamedRow, 8) : '')}
    </div>
    ${card('VM đổi cấu hình vCPU/RAM', `Tách riêng vì có làm thay đổi tài nguyên đã cấp: ${fmtInt(rs.up)} tăng · ${fmtInt(rs.down)} giảm · tổng ${rs.deltaVcpu > 0 ? '+' : ''}${fmtInt(rs.deltaVcpu)} vCPU · ${rs.deltaRamGB > 0 ? '+' : ''}${fmtInt(rs.deltaRamGB)} GB RAM.`, resized.length, 'resized',
      ['VM', 'vCenter', 'Cluster', 'Host', 'Trạng thái', 'vCPU (đầu kỳ → nay)', 'RAM GB (đầu kỳ → nay)'], resized.length ? groupedRows(resized, resizedRow, 7) : '')}`;
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

// Host filter only narrows the VM added/removed lists (CPU/RAM series are per cluster).
function historyHostsParam(opts) {
  if (!opts || !opts.followClusterFilter) return null;
  const hst = appState && appState.filters ? appState.filters.host : 'all';
  return hst && hst !== 'all' ? [hst] : null;
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
  const hosts = historyHostsParam(opts);
  try {
    const vcenter = historyVcenterParam();
    const jobs = [api.getResourceHistory(vcenter, historyState.months, historyState.group, clusters)];
    jobs.push(api.getVmChanges(vcenter, historyState.months, clusters, hosts));
    [data, changes] = await Promise.all(jobs);
  } catch (err) {
    historyEmpty(mountId, `Không tải được lịch sử snapshot: ${err.message || err}`);
    return;
  }
  const sub = document.getElementById(`${mountId}Sub`);
  if (sub) {
    const scope = (clusters ? ` · Cluster: ${clusters.join(', ')}` : '') + (hosts ? ` · Host (chỉ áp dụng cho VM thêm/bỏ): ${hosts.join(', ')}` : '');
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
