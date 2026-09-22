/* =========================================================
   Executive view — management/investment-decision perspective.
   Reuses the exact same capacity engine (buildCapacityModel,
   buildClusterRows, getStatusForPct, combineStatus, chartPalette,
   destroyChart) from core.js so numbers always match the
   Technical view; adds org-wide KPIs, historical trend +
   investment forecast, per-vCenter split, and top-risk clusters.
   ========================================================= */

/** Toggle a canvas + its sibling empty-state note without ever removing the
 *  canvas node, so repeated renders (toggling perspective back and forth)
 *  keep working. `hasData` controls which one is visible. */
function toggleChartEmptyState(canvasId, hasData, emptyMessage) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const wrap = canvas.closest('.chart-wrap');
  let note = wrap.querySelector('.chart-empty-note');
  if (!note) {
    note = document.createElement('div');
    note.className = 'empty-state chart-empty-note hidden';
    wrap.appendChild(note);
  }
  note.textContent = emptyMessage || '';
  canvas.classList.toggle('hidden', !hasData);
  note.classList.toggle('hidden', hasData);
}

function execKpiCard(label, value, sub, tone) {
  return `
    <article class="card exec-kpi">
      <div class="exec-label">${esc(label)}</div>
      <div class="exec-value ${tone || ''}">${value}</div>
      <div class="exec-sub">${esc(sub || '')}</div>
    </article>`;
}

function renderExecutiveKpis() {
  const clusters = buildClusterRows();
  const model = buildCapacityModel(appState.hosts, appState.vms);
  const findings = buildComplianceFindings();
  const critCount = findings.filter(f => f.severity === 'critical').length;
  const overCount = clusters.filter(c => c.overall.cls === 'crit' || c.overall.cls === 'bad').length;

  const cards = [
    execKpiCard('Tổng năng lực CPU (pCPU logic)', fmtInt(model.totalLogicalCores), `${fmtInt(model.totalCores)} core vật lý × HT ${model.htFactor}x`),
    execKpiCard('Tổng năng lực RAM', `${fmtNum(model.totalRamGB, 0)} GB`, `Đang dùng ${fmtNum(model.usedRamGB, 0)} GB (${isFinite(model.ramPct) ? fmtNum(model.ramPct, 0) : 'NA'}% ngưỡng overcommit)`),
    execKpiCard('Cluster cần đầu tư/mở rộng', overCount, `Trên tổng ${clusters.length} cluster đang vận hành`, overCount > 0 ? 'tone-bad' : 'tone-ok'),
    execKpiCard('Compliance Critical', critCount, `${findings.length} findings tổng cộng`, critCount > 0 ? 'tone-crit' : 'tone-ok'),
  ];
  $('#execKpis').innerHTML = cards.join('');
}

function renderExecutiveTopRisk() {
  const clusters = buildClusterRows()
    .slice()
    .sort((a, b) => Math.max(b.model.cpuPct || 0, b.model.ramPct || 0) - Math.max(a.model.cpuPct || 0, a.model.ramPct || 0))
    .slice(0, 6);

  const box = $('#execTopRisk');
  if (!clusters.length) { box.innerHTML = `<div class="empty-state">Chưa có dữ liệu cluster.</div>`; return; }

  box.innerHTML = clusters.map(c => `
    <div class="risk-cluster-row">
      <div>
        <div class="rc-name">${esc(c.name)}</div>
        <div class="rc-metric">${c.hosts} host · ${c.vms} VM</div>
      </div>
      <div style="display:flex;gap:1.2rem;align-items:center;">
        <span class="rc-metric">CPU ${isFinite(c.model.cpuPct) ? fmtNum(c.model.cpuPct, 0) : 'NA'}%</span>
        <span class="rc-metric">RAM ${isFinite(c.model.ramPct) ? fmtNum(c.model.ramPct, 0) : 'NA'}%</span>
        <span class="pill ${c.overall.cls}">${esc(c.overall.text)}</span>
      </div>
    </div>
  `).join('');
}

function renderExecutiveForecastCard(elId, label, forecast, unavailableReason) {
  const el = $(elId);
  if (!forecast) {
    el.classList.remove('risk');
    el.innerHTML = `<h4>${esc(label)}</h4><p>${esc(unavailableReason || 'Chưa có dữ liệu dự báo.')}</p>`;
    return;
  }
  const isRisk = forecast.daysTo100Pct && forecast.daysTo100Pct <= 180;
  el.classList.toggle('risk', !!isRisk);
  el.innerHTML = `
    <h4>${esc(label)} — hiện tại ${fmtNum(forecast.currentPct, 0)}% công suất vật lý</h4>
    <p>${esc(forecast.insight)}</p>`;
}

async function renderExecutiveTrendChart() {
  if (typeof Chart === 'undefined') return;
  let trendData;
  try {
    trendData = await api.getTrend({ days: 90 });
  } catch (err) {
    trendData = { trend: [], forecast: { available: false, reason: 'Không tải được lịch sử.' } };
  }
  const colors = chartPalette();
  destroyChart('execTrend');

  const trend = trendData.trend || [];
  const labels = trend.map(t => t.date);
  const cpuUsedPct = trend.map(t => t.totalLogicalCores ? (t.usedVcpu / t.totalLogicalCores) * 100 : null);
  const ramUsedPct = trend.map(t => t.totalRamGB ? (t.usedRamGB / t.totalRamGB) * 100 : null);

  toggleChartEmptyState('chartExecTrend', labels.length > 0, 'Chưa có đủ lịch sử polling để vẽ xu hướng. Dữ liệu sẽ tích luỹ sau mỗi lần đồng bộ vCenter hoặc import.');
  if (labels.length) {
    appState.charts.execTrend = new Chart($('#chartExecTrend'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'CPU % công suất vật lý', data: cpuUsedPct, borderColor: colors.primary, backgroundColor: colors.primary + '33', tension: .3, fill: true },
          { label: 'RAM % công suất vật lý', data: ramUsedPct, borderColor: colors.accent, backgroundColor: colors.accent + '33', tension: .3, fill: true },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: colors.muted, maxRotation: 0 }, grid: { color: colors.divider } },
          y: { beginAtZero: true, max: 100, ticks: { color: colors.muted }, grid: { color: colors.divider } },
        },
        plugins: { legend: { labels: { color: colors.text } } },
      }
    });
  }

  const forecast = trendData.forecast || {};
  renderExecutiveForecastCard('#execForecastCpu', 'Dự báo CPU', forecast.cpu, forecast.reason);
  renderExecutiveForecastCard('#execForecastRam', 'Dự báo RAM', forecast.ram, forecast.reason);
}

async function renderExecutiveByVcenterChart() {
  if (typeof Chart === 'undefined') return;
  const colors = chartPalette();
  destroyChart('execByVcenter');
  let grouped;
  try {
    grouped = await api.getLatestData({ group_by_vcenter: true });
  } catch (err) {
    grouped = { byVcenter: {} };
  }
  const byVcenter = grouped.byVcenter || {};
  const keys = Object.keys(byVcenter);
  toggleChartEmptyState('chartExecByVcenter', keys.length > 0, 'Chưa có dữ liệu theo vCenter.');
  if (!keys.length) return;
  const cpuVals = [], ramVals = [];
  keys.forEach(k => {
    const m = buildCapacityModel(byVcenter[k].hosts || [], byVcenter[k].vms || []);
    cpuVals.push(isFinite(m.cpuPct) ? Math.round(m.cpuPct) : 0);
    ramVals.push(isFinite(m.ramPct) ? Math.round(m.ramPct) : 0);
  });

  appState.charts.execByVcenter = new Chart($('#chartExecByVcenter'), {
    type: 'bar',
    data: {
      labels: keys.map(k => k === 'import' ? 'Import thủ công' : k),
      datasets: [
        { label: 'CPU % ngưỡng overcommit', data: cpuVals, backgroundColor: colors.primary },
        { label: 'RAM % ngưỡng overcommit', data: ramVals, backgroundColor: colors.accent },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: colors.muted }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: colors.muted }, grid: { color: colors.divider } },
      },
      plugins: { legend: { labels: { color: colors.text } } },
    }
  });
}

/* ---- Management brief (headline, key points, recommendations, decision
   register). Fetched from the backend so the web view shows exactly the same
   wording as the Executive PDF; degrades gracefully when unavailable. ---- */
const PRIORITY_PILL_CLS = { cao: 'prio-cao', tb: 'prio-tb', thap: 'prio-thap', info: 'prio-info' };

function briefPriorityPill(rec) {
  const cls = PRIORITY_PILL_CLS[rec.priority] || 'prio-info';
  return `<span class="pill ${cls}">${esc(rec.priority_label || rec.priority || '')}</span>`;
}

function renderExecutiveBriefCard(brief) {
  const card = $('#execBrief');
  if (!card) return;
  if (!brief || !brief.available) {
    card.className = 'card exec-brief-card';
    card.innerHTML = `<div class="empty-state">${esc((brief && brief.message) || 'Chưa có dữ liệu để tổng hợp nhận định. Hãy đồng bộ vCenter hoặc import dữ liệu trước.')}</div>`;
    return;
  }
  card.className = `card exec-brief-card ${brief.health_cls || ''}`;
  const storageNote = brief.include_storage ? '' : `<span class="pill">Storage: không đánh giá (SAN)</span>`;
  card.innerHTML = `
    <div class="exec-brief-head">
      <span class="pill ${esc(brief.health_cls || '')}">${esc(brief.health_word || '')}</span>
      <span class="exec-brief-kicker">Tóm tắt 30 giây cho Ban lãnh đạo · ${esc(brief.scope || '')}</span>
      ${storageNote}
    </div>
    <div class="exec-brief-headline">${esc(brief.headline || '')}</div>
    <ul class="exec-brief-points">${(brief.points || []).map(p => `<li>${esc(p)}</li>`).join('')}</ul>
    <div class="exec-brief-decision"><strong>Quyết định cần từ Ban lãnh đạo:</strong> ${esc(brief.decision_line || '')}</div>`;
}

function renderExecutiveRecommendations(brief) {
  const box = $('#execRecommendations');
  const panel = $('#execRecommendationsPanel');
  if (!box) return;
  const recs = (brief && brief.available && brief.recommendations) || [];
  panel.classList.toggle('hidden', !recs.length);
  box.innerHTML = `<div class="rec-grid-web">${recs.map(r => `
    <article class="rec-card-web prio-${esc(r.priority)} tone-${esc(r.tone || 'neutral')}">
      <div class="rec-head"><span class="rec-num">${r.index}</span><div class="rec-title">${esc(r.title)}</div></div>
      <div class="rec-meta">${briefPriorityPill(r)}<span class="pill">${esc(r.timeframe || '')}</span></div>
      <div class="rec-block"><span class="rec-label">Điều này có nghĩa</span>${esc(r.meaning)}</div>
      <div class="rec-block"><span class="rec-label">Chúng tôi đề xuất</span>${esc(r.action)}</div>
      <div class="rec-block"><span class="rec-label">Nếu chưa hành động</span>${esc(r.impact)}</div>
      <div class="rec-decision"><strong>Đề nghị Ban lãnh đạo:</strong> ${esc(r.decision)}</div>
    </article>`).join('')}</div>`;
}

function renderExecutiveDecisionRegister(brief) {
  const panel = $('#execDecisionPanel');
  const tbody = document.querySelector('#execDecisionTable tbody');
  if (!tbody) return;
  const recs = ((brief && brief.available && brief.recommendations) || []).filter(r => r.priority !== 'info');
  panel.classList.toggle('hidden', !recs.length);
  tbody.innerHTML = recs.map((r, i) => `
    <tr>
      <td>${i + 1}</td>
      <td class="decision-cell"><strong>${esc(r.decision)}</strong><span class="sub">${esc(r.title)}</span></td>
      <td>${briefPriorityPill(r)}</td>
      <td>${esc(r.timeframe || '')}</td>
      <td class="decision-cell">${esc(r.impact)}</td>
    </tr>`).join('');
}

async function renderExecutiveBrief() {
  let brief = null;
  try {
    const vcenter = (typeof currentVcenterFilter !== 'undefined' && currentVcenterFilter !== 'import') ? currentVcenterFilter : 'all';
    brief = await api.getManagementBrief(vcenter, !!appState.policy.includeStorage);
  } catch (err) {
    brief = { available: false, message: `Không tải được nhận định cho Ban lãnh đạo: ${err.message || err}` };
  }
  renderExecutiveBriefCard(brief);
  renderExecutiveRecommendations(brief);
  renderExecutiveDecisionRegister(brief);
}

async function renderExecutiveView() {
  renderExecutiveKpis();
  renderExecutiveTopRisk();
  await Promise.all([renderExecutiveTrendChart(), renderExecutiveByVcenterChart(), renderExecutiveBrief(),
    renderHistoryPanel('execHistoryMount', { showVmChanges: false })]);
}
