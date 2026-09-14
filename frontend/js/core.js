/* =========================================================
   VMware Capacity Ops Management — Core state & utilities
   ========================================================= */
const appState = {
  hosts: [],            // normalized host objects
  vms: [],               // normalized vm objects
  hostsRaw: [],
  vmsRaw: [],
  view: 'dashboard',
  reportTimestamp: null,
  filters: { cluster: 'all', host: 'all' },
  sort: { table: {}, },
  compliance: { search: '', severity: 'all', type: 'all' },
  hostTableSearch: '',
  vmTableSearch: '',
  vmStatusFilter: 'all',
  policy: { idleDays: 30, cpuWarn: 50, cpuHigh: 80, ramWarn: 50, ramHigh: 80, dsWarn: 70, dsHigh: 85, dsCrit: 95, cpuOvercommitMax: 6, ramOvercommitMax: 1.5, htFactor: 2 },
  charts: {},
  simHistory: [],
  whatif: { cluster: null, tab: 'addHost', drafts: {} },
  validation: { errors: [], warnings: [] }
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
const deepClone = (o) => JSON.parse(JSON.stringify(o));
const cleanNum = (v) => { const n = parseFloat(String(v ?? '').replace(/,/g, '').trim()); return isFinite(n) ? n : 0; };
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, m => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[m]));
const fmtPct = (v) => isFinite(v) ? v.toFixed(1) + '%' : 'NA';
const fmtNum = (v, d = 1) => isFinite(v) ? Number(v).toFixed(d) : '0.0';
const fmtInt = (v) => isFinite(v) ? Math.round(v).toLocaleString('en-US') : '0';

function formatReportTime(value) {
  if (!value) return '--';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '--';
  return new Intl.DateTimeFormat('vi-VN', { dateStyle:'medium', timeStyle:'short' }).format(d);
}

function normKey(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ''); }

function buildKeyMap(row) {
  const map = new Map();
  Object.keys(row || {}).forEach(k => map.set(normKey(k), k));
  return map;
}

function pickField(row, keys) {
  const map = buildKeyMap(row);
  for (const key of keys) {
    if (row && row[key] !== undefined && String(row[key]).trim() !== '') return row[key];
    const real = map.get(normKey(key));
    if (real !== undefined && String(row[real]).trim() !== '') return row[real];
  }
  return '';
}

function parseSizeToGB(v) {
  const raw = String(v ?? '').trim();
  if (!raw) return 0;
  const m = raw.replace(/,/g, '').match(/(-?\d+(?:\.\d+)?)\s*(TB|GB|MB|KB)?/i);
  if (!m) return 0;
  const num = parseFloat(m[1]) || 0;
  const unit = (m[2] || 'GB').toUpperCase();
  if (unit === 'TB') return num * 1024;
  if (unit === 'MB') return num / 1024;
  if (unit === 'KB') return num / 1024 / 1024;
  return num;
}

/* ---------- CSV / JSON parsing ---------- */
function splitCsvLine(line) {
  const out = [];
  let current = '', inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') { current += '"'; i++; }
      else inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) { out.push(current); current = ''; }
    else current += ch;
  }
  out.push(current);
  return out;
}

function parseCSV(text) {
  const lines = String(text || '').split(/\r?\n/).filter(l => l.trim() !== '');
  if (lines.length < 2) return [];
  const headers = splitCsvLine(lines[0]).map(h => h.trim());
  return lines.slice(1).map(line => {
    const cols = splitCsvLine(line);
    const row = {};
    headers.forEach((h, i) => row[h] = (cols[i] || '').trim());
    return row;
  });
}

function parseInputText(text, fileName) {
  const trimmed = String(text || '').trim();
  if (!trimmed) return [];
  const isJson = /\.json$/i.test(fileName || '') || trimmed.startsWith('[') || trimmed.startsWith('{');
  if (isJson) {
    try {
      const data = JSON.parse(trimmed);
      if (Array.isArray(data)) return data;
      if (data && Array.isArray(data.rows)) return data.rows;
      if (data && Array.isArray(data.data)) return data.data;
      if (data && typeof data === 'object') return [data];
      return [];
    } catch (e) {
      throw new Error('File JSON không hợp lệ: ' + e.message);
    }
  }
  return parseCSV(trimmed);
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = reject;
    reader.readAsText(file);
  });
}
/* =========================================================
   Normalize & validate host / VM rows
   ========================================================= */

function normalizePowerState(row) {
  const raw = String(pickField(row, ['PowerState','Power State','VM Power State','State','Status','Runtime Power State'])).trim().toLowerCase();
  if (!raw) return { text:'Powered On', isPoweredOn:true };
  if (raw.includes('on') || raw === 'running') return { text:'Powered On', isPoweredOn:true };
  if (raw.includes('off') || raw === 'stopped' || raw.includes('suspend')) return { text:'Powered Off', isPoweredOn:false };
  return { text: raw, isPoweredOn:true };
}

function normalizeIpAddress(row) {
  const raw = String(pickField(row, ['IP Address','IP Addresses','IPAddress','IP','Primary IP','Guest IP Address','NIC 1 IP']) || '').trim();
  if (!raw) return '';
  const ipv4 = raw.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/);
  if (ipv4) return ipv4[0];
  return raw.split(/[;,|]/).map(x => x.trim()).filter(Boolean)[0] || raw;
}

function parseMemoryGB(row, mbKeys, gbKeys, genericKeys) {
  const mbVal = pickField(row, mbKeys);
  if (String(mbVal).trim()) return cleanNum(mbVal) / 1024;
  const gbVal = pickField(row, gbKeys);
  if (String(gbVal).trim()) return cleanNum(gbVal);
  return parseSizeToGB(pickField(row, genericKeys));
}

function parseDateSafe(v) {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

function daysSince(date) {
  if (!date) return null;
  const diffMs = Date.now() - date.getTime();
  return Math.max(0, Math.floor(diffMs / 86400000));
}

/**
 * Normalize a raw host row into a canonical shape.
 * Returns { row, errors, warnings }
 */
function normalizeHostRow(row, index) {
  const errors = [], warnings = [];
  const name = String(pickField(row, ['Name','Host','Host Name','ESXi Host','name']) || '').trim();
  const cluster = String(pickField(row, ['Cluster','cluster','Cluster Name','Compute Cluster','ClusterName']) || '').trim();
  const cores = cleanNum(pickField(row, ['CPUs','NumCpu','Num CPU','CPU Cores','Cores','cores','Processor Cores','Cpu Total Cores']));
  const cpuMhz = cleanNum(pickField(row, ['CPU MHz','CpuMhz','CPU Mhz per Core','CPU Speed']));
  const ramGB = parseMemoryGB(row,
    ['Memory Size MB','Memory Size (MB)','MemoryMB','Memory_MB','RAM_MB','Host Memory MB','Total Memory MB','Memory Capacity MB'],
    ['Memory GB','RAM GB','MemoryGB','RAM_GB','Total Memory GB','Memory Capacity GB'],
    ['Memory Size','Memory','RAM','Host Memory','Total Memory','Memory Capacity']);
  const cpuUsagePct = cleanNum(pickField(row, ['CPU Usage %','CPU Usage(%)','CpuUsagePct','CPU Utilization']));
  const ramUsagePct = cleanNum(pickField(row, ['Memory Usage %','RAM Usage %','MemoryUsagePct','Memory Utilization']));
  const status = String(pickField(row, ['Connection State','ConnectionState','Status']) || 'Connected').trim();

  if (!name) errors.push(`Dòng host #${index + 1}: thiếu tên host.`);
  if (!cluster) warnings.push(`Host "${name || '#' + (index + 1)}": thiếu Cluster, sẽ gán vào UNKNOWN.`);
  if (cores <= 0) warnings.push(`Host "${name || '#' + (index + 1)}": CPU cores không hợp lệ hoặc bằng 0.`);
  if (ramGB <= 0) warnings.push(`Host "${name || '#' + (index + 1)}": RAM không hợp lệ hoặc bằng 0.`);

  return {
    row: {
      cluster: cluster || 'UNKNOWN',
      host: name || `HOST-${index + 1}`,
      name: name || `HOST-${index + 1}`,
      cores, cpuMhz, ramGB,
      cpuUsagePctRaw: cpuUsagePct,
      ramUsagePctRaw: ramUsagePct,
      status,
      raw: row
    },
    errors, warnings
  };
}

/**
 * Normalize a raw VM row.
 */
function normalizeVmRow(row, index) {
  const errors = [], warnings = [];
  const power = normalizePowerState(row);
  const vmName = String(pickField(row, ['Name','VM','VM Name','Virtual Machine','VMName','Display Name']) || '').trim();
  const cluster = String(pickField(row, ['Cluster','cluster','Cluster Name','Compute Cluster','ClusterName']) || '').trim();
  const host = String(pickField(row, ['Host','ESXi Host','VM Host','Host Name','HostName','Runtime Host']) || '').trim();
  const vcpu = cleanNum(pickField(row, ['CPUs','NumCpu','Num CPU','vCPU','vCPUs','Number of CPUs','Num vCPU','CPU Count']));
  const ramGB = parseMemoryGB(row,
    ['Memory Size MB','Memory Size (MB)','MemoryMB','Memory_MB','RAM_MB','Configured Memory MB','Guest Memory MB'],
    ['Memory GB','RAM GB','MemoryGB','RAM_GB'],
    ['Memory Size','Memory','RAM','Configured Memory','Guest Memory']);
  const diskProvGB = parseSizeToGB(pickField(row, ['Provisioned Space','Provisioned','Disk Prov GB','Provisioned GB','Provisioned Size','Provisioned Storage','Capacity Provisioned']));
  const diskUsedGB = parseSizeToGB(pickField(row, ['Used Space','Used','Disk Used GB','Used GB','Consumed Space','Consumed Storage','Used Storage']));
  const datastore = String(pickField(row, ['Datastore','Datastore Name','DS','Storage']) || 'UNKNOWN').trim();
  const cpuUsagePct = cleanNum(pickField(row, ['CPU Usage %','CPU Usage(%)','CpuUsagePct','CPU Utilization']));
  const ramUsagePct = cleanNum(pickField(row, ['Memory Usage %','RAM Usage %','MemoryUsagePct','Memory Utilization']));
  const lastActivity = parseDateSafe(pickField(row, ['Last Activity','LastActivity','Last Seen Active','Last IO','Last Used']));
  const powerOffSince = parseDateSafe(pickField(row, ['Power Off Date','PowerOffDate','Last Power Off','State Change Date']));
  const provisionedDate = parseDateSafe(pickField(row, ['Provisioned Date','Created Date','CreationDate']));

  if (!vmName) errors.push(`Dòng VM #${index + 1}: thiếu tên VM.`);
  if (!cluster) warnings.push(`VM "${vmName || '#' + (index + 1)}": thiếu Cluster, sẽ gán vào UNKNOWN.`);
  if (vcpu <= 0) warnings.push(`VM "${vmName || '#' + (index + 1)}": vCPU không hợp lệ hoặc bằng 0.`);
  if (diskProvGB > 0 && diskUsedGB > diskProvGB * 1.01) warnings.push(`VM "${vmName || '#' + (index + 1)}": Disk Used vượt quá Disk Provisioned.`);

  return {
    row: {
      cluster: cluster || 'UNKNOWN',
      host, vm: vmName || `VM-${index + 1}`,
      powerState: power.text, isPoweredOn: power.isPoweredOn,
      ipAddress: normalizeIpAddress(row),
      vcpu, ramGB, diskProvGB, diskUsedGB, datastore,
      cpuUsagePctRaw: cpuUsagePct, ramUsagePctRaw: ramUsagePct,
      lastActivity, powerOffSince, provisionedDate,
      lastActivityDays: daysSince(lastActivity),
      powerOffDays: daysSince(powerOffSince),
      raw: row
    },
    errors, warnings
  };
}

function normalizeAndValidate(hostRawRows, vmRawRows) {
  const errors = [], warnings = [];
  const hosts = [], vms = [];
  const seenHosts = new Set();

  (hostRawRows || []).forEach((row, i) => {
    const { row: h, errors: e, warnings: w } = normalizeHostRow(row, i);
    errors.push(...e); warnings.push(...w);
    const key = (h.cluster + '::' + h.host).toLowerCase();
    if (seenHosts.has(key)) {
      warnings.push(`Host trùng lặp bị bỏ qua: "${h.host}" trong cluster "${h.cluster}".`);
      return;
    }
    seenHosts.add(key);
    hosts.push(h);
  });

  (vmRawRows || []).forEach((row, i) => {
    const { row: v, errors: e, warnings: w } = normalizeVmRow(row, i);
    errors.push(...e); warnings.push(...w);
    vms.push(v);
  });

  return { hosts, vms, errors, warnings };
}
/* =========================================================
   Capacity calculations: aggregation, overcommit, status
   ========================================================= */

function getStatusForPct(pct, warnT, highT) {
  if (!isFinite(pct)) return { text:'NA', cls:'na' };
  if (pct > 100) return { text:'VƯỢT NGƯỠNG', cls:'crit' };
  if (pct >= highT) return { text:'CẢNH BÁO', cls:'bad' };
  if (pct >= warnT) return { text:'THEO DÕI', cls:'warn' };
  return { text:'AN TOÀN', cls:'ok' };
}

function combineStatus(...statuses) {
  const order = ['crit','bad','warn','ok','na'];
  const clsList = statuses.map(s => s.cls);
  for (const level of order) {
    if (clsList.includes(level)) {
      if (level === 'na' && clsList.some(c => c !== 'na')) continue;
      return { text: { crit:'VƯỢT NGƯỠNG', bad:'CẢNH BÁO', warn:'THEO DÕI', ok:'AN TOÀN', na:'NA' }[level], cls: level };
    }
  }
  return { text:'NA', cls:'na' };
}

function getBarColor(pct) {
  if (!isFinite(pct)) return 'var(--faint)';
  if (pct > 100) return 'var(--crit)';
  if (pct >= 80) return 'var(--bad)';
  if (pct >= 50) return 'var(--warn)';
  return 'var(--ok)';
}

/** Apply what-if deltas (host adds/removes, vm adds/resizes) to base hosts/vms for a target cluster. */
function getEffectiveData(overrides) {
  overrides = overrides || {};
  let hosts = deepClone(appState.hosts);
  let vms = deepClone(appState.vms);

  if (overrides.addHosts) hosts = hosts.concat(overrides.addHosts);
  if (overrides.removeHostNames && overrides.removeHostNames.length) {
    const set = new Set(overrides.removeHostNames.map(n => (n||'').toLowerCase()));
    hosts = hosts.filter(h => !set.has(h.host.toLowerCase()));
  }
  if (overrides.addVms) vms = vms.concat(overrides.addVms);
  if (overrides.removeVmNames && overrides.removeVmNames.length) {
    const set = new Set(overrides.removeVmNames.map(n => (n||'').toLowerCase()));
    vms = vms.filter(v => !set.has(v.vm.toLowerCase()));
  }
  if (overrides.resizeVm) {
    const target = overrides.resizeVm;
    vms = vms.map(v => {
      if (v.vm.toLowerCase() === target.name.toLowerCase() && v.cluster === target.cluster) {
        return { ...v, vcpu: target.vcpu != null ? target.vcpu : v.vcpu, ramGB: target.ramGB != null ? target.ramGB : v.ramGB };
      }
      return v;
    });
  }
  return { hosts, vms };
}

/** Build full capacity model from hosts + vms (optionally filtered/overridden). */
function buildCapacityModel(hosts, vms) {
  const poweredOn = vms.filter(v => v.isPoweredOn);

  const totalCores = hosts.reduce((a, h) => a + h.cores, 0);
  const totalRamGB = hosts.reduce((a, h) => a + h.ramGB, 0);
  const usedVcpu = poweredOn.reduce((a, v) => a + v.vcpu, 0);
  const usedRamGB = poweredOn.reduce((a, v) => a + v.ramGB, 0);
  const provStorageGB = vms.reduce((a, v) => a + v.diskProvGB, 0);
  const usedStorageGB = vms.reduce((a, v) => a + v.diskUsedGB, 0);

  const storagePct = provStorageGB > 0 ? (usedStorageGB / provStorageGB) * 100 : NaN;

  // pCPU logic = CPU cores vật lý × hệ số Hyperthreading (mặc định 2) — khớp cách vCenter tính số pCPU khả dụng cho VM.
  const htFactor = Math.max(1, appState.policy.htFactor || 2);
  const totalLogicalCores = totalCores * htFactor;
  const cpuOvercommit = totalLogicalCores > 0 ? usedVcpu / totalLogicalCores : NaN;
  const ramOvercommit = totalRamGB > 0 ? usedRamGB / totalRamGB : NaN;

  // CPU%/RAM% hiển thị (thanh bar, status) đều tinh theo mức độ đã dùng "chỗ overcommit cho phép":
  // % = (overcommit thực tế / ngưỡng overcommit cấu hình) x 100. Vượt 100% tức overcommit thực tế đã vượt ngưỡng cho phép.
  const cpuOcMax = appState.policy.cpuOvercommitMax;
  const ramOcMax = appState.policy.ramOvercommitMax;
  const cpuPct = (isFinite(cpuOvercommit) && cpuOcMax > 0) ? (cpuOvercommit / cpuOcMax) * 100 : NaN;
  const ramPct = (isFinite(ramOvercommit) && ramOcMax > 0) ? (ramOvercommit / ramOcMax) * 100 : NaN;

  return {
    hostCount: hosts.length,
    vmCount: vms.length,
    poweredOnCount: poweredOn.length,
    poweredOffCount: vms.length - poweredOn.length,
    totalCores, totalLogicalCores, htFactor, totalRamGB, provStorageGB, usedStorageGB,
    usedVcpu, usedRamGB,
    freeCores: Math.max(0, totalCores - usedVcpu),
    freeLogicalCores: Math.max(0, totalLogicalCores - usedVcpu),
    freeRamGB: Math.max(0, totalRamGB - usedRamGB),
    freeStorageGB: Math.max(0, provStorageGB - usedStorageGB),
    cpuPct, ramPct, storagePct,
    cpuOvercommit, ramOvercommit
  };
}

function getFilteredHosts() {
  return appState.hosts.filter(h => {
    if (appState.filters.cluster !== 'all' && h.cluster !== appState.filters.cluster) return false;
    if (appState.filters.host !== 'all' && h.host !== appState.filters.host) return false;
    return true;
  });
}

function getFilteredVms() {
  return appState.vms.filter(v => {
    if (appState.filters.cluster !== 'all' && v.cluster !== appState.filters.cluster) return false;
    if (appState.filters.host !== 'all' && v.host !== appState.filters.host) return false;
    return true;
  });
}

function getClusterList() {
  const set = new Set();
  appState.hosts.forEach(h => set.add(h.cluster));
  appState.vms.forEach(v => set.add(v.cluster));
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

function getHostList(clusterFilter) {
  const set = new Set();
  appState.hosts.forEach(h => { if (clusterFilter === 'all' || !clusterFilter || h.cluster === clusterFilter) set.add(h.host); });
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

function buildClusterRows() {
  const clusters = getClusterList();
  return clusters.map(name => {
    const hosts = appState.hosts.filter(h => h.cluster === name);
    const vms = appState.vms.filter(v => v.cluster === name);
    const model = buildCapacityModel(hosts, vms);
    // CPU%/RAM% đã được tính theo overcommit ratio / ngưỡng overcommit (xem buildCapacityModel) nên dùng ngưỡng cố định 80%/100% để đánh giá: <80% AN TOÀN, 80-100% THEO DÕI, >100% nghĩa là đã vượt ngưỡng overcommit cấu hình.
    const cpuStatus = getStatusForPct(model.cpuPct, 80, 100);
    const ramStatus = getStatusForPct(model.ramPct, 80, 100);
    const dsStatus = getStatusForPct(model.storagePct, appState.policy.dsWarn, appState.policy.dsHigh);
    // Status tổng chỉ dựa trên CPU/RAM (đã gắn overcommit) — không tính storage (dùng shared SAN, không thuộc phạm vi compliance).
    const overall = combineStatus(cpuStatus, ramStatus);
    return { name, hosts: hosts.length, vms: vms.length, model, cpuStatus, ramStatus, dsStatus, overall };
  });
}

function buildHostRows() {
  return appState.hosts.map(h => {
    const vmsOnHost = appState.vms.filter(v => v.host === h.host && v.cluster === h.cluster && v.isPoweredOn);
    const vcpuUsed = vmsOnHost.reduce((a, v) => a + v.vcpu, 0);
    const ramUsedGB = vmsOnHost.reduce((a, v) => a + v.ramGB, 0);
    // Overcommit ratio cấp host (vCPU:pCPU logic, vRAM:pRAM) - giống cách tính cấp cluster, pCPU logic = cores × hệ số Hyperthreading.
    const htFactor = Math.max(1, appState.policy.htFactor || 2);
    const logicalCores = h.cores * htFactor;
    const cpuOvercommit = logicalCores > 0 ? vcpuUsed / logicalCores : NaN;
    const ramOvercommit = h.ramGB > 0 ? ramUsedGB / h.ramGB : NaN;
    const cpuAllocPct = logicalCores > 0 ? (vcpuUsed / logicalCores) * 100 : NaN;
    const ramAllocPct = h.ramGB > 0 ? (ramUsedGB / h.ramGB) * 100 : NaN;
    // CPU%/RAM% hiển thị = (overcommit thực tế cấp host / ngưỡng overcommit cấu hình) x 100 — nhất quán với cấp cluster.
    const cpuOcMax = appState.policy.cpuOvercommitMax;
    const ramOcMax = appState.policy.ramOvercommitMax;
    const cpuPct = (isFinite(cpuOvercommit) && cpuOcMax > 0) ? (cpuOvercommit / cpuOcMax) * 100 : NaN;
    const ramPct = (isFinite(ramOvercommit) && ramOcMax > 0) ? (ramOvercommit / ramOcMax) * 100 : NaN;
    const cpuStatus = getStatusForPct(cpuPct, 80, 100);
    const ramStatus = getStatusForPct(ramPct, 80, 100);
    const overall = combineStatus(cpuStatus, ramStatus);
    return { ...h, vcpuUsed, ramUsedGB, cpuAllocPct, ramAllocPct, cpuOvercommit, ramOvercommit, cpuPct, ramPct, vmCount: vmsOnHost.length, cpuStatus, ramStatus, overall };
  });
}

function buildDatastoreRows() {
  const map = new Map();
  appState.vms.forEach(v => {
    const key = v.datastore || 'UNKNOWN';
    if (!map.has(key)) map.set(key, { name: key, provGB: 0, usedGB: 0, vmCount: 0 });
    const d = map.get(key);
    d.provGB += v.diskProvGB;
    d.usedGB += v.diskUsedGB;
    d.vmCount += 1;
  });
  return Array.from(map.values()).map(d => {
    const pct = d.provGB > 0 ? (d.usedGB / d.provGB) * 100 : NaN;
    return { ...d, pct, status: getStatusForPct(pct, appState.policy.dsWarn, appState.policy.dsHigh) };
  }).sort((a, b) => (b.pct || 0) - (a.pct || 0));
}
/* =========================================================
   Compliance checks
   ========================================================= */

function buildComplianceFindings() {
  const findings = [];
  const P = appState.policy;

  // 1. Host CPU/RAM vượt ngưỡng overcommit (CPU%/RAM% = overcommit thực tế / ngưỡng overcommit cấu hình x 100)
  buildHostRows().forEach(h => {
    if (isFinite(h.cpuPct) && h.cpuPct >= 80) {
      findings.push({
        severity: h.cpuPct > 100 ? 'critical' : 'warning',
        type: 'host_cpu',
        typeLabel: 'Host CPU vượt ngưỡng',
        entity: h.host, cluster: h.cluster,
        metric: `CPU overcommit ${fmtNum(h.cpuOvercommit,2)}x (ngưỡng ${fmtNum(P.cpuOvercommitMax,1)}x) — ${fmtInt(h.vcpuUsed)}/${fmtInt(h.cores * (P.htFactor||2))} pCPU logic (${fmtInt(h.cores)} core vật lý × HT${fmtNum(P.htFactor||2,0)}), ${fmtPct(h.cpuPct)} ngưỡng`,
        recommendation: h.cpuPct > 100 ? 'Overcommit CPU đã vượt ngưỡng cấu hình — cần di dời VM hoặc bổ sung host ngay.' : 'Overcommit CPU đang tiến gần ngưỡng — cân nhắc vMotion VM sang host khác hoặc lên kế hoạch bổ sung năng lực CPU.'
      });
    }
    if (isFinite(h.ramPct) && h.ramPct >= 80) {
      findings.push({
        severity: h.ramPct > 100 ? 'critical' : 'warning',
        type: 'host_ram',
        typeLabel: 'Host RAM vượt ngưỡng',
        entity: h.host, cluster: h.cluster,
        metric: `RAM overcommit ${fmtNum(h.ramOvercommit,2)}x (ngưỡng ${fmtNum(P.ramOvercommitMax,1)}x) — ${fmtNum(h.ramUsedGB)}/${fmtNum(h.ramGB)} GB, ${fmtPct(h.ramPct)} ngưỡng`,
        recommendation: h.ramPct > 100 ? 'Overcommit RAM đã vượt ngưỡng cấu hình — rủi ro cao, cần hành động ngay.' : 'Overcommit RAM đang tiến gần ngưỡng — theo dõi sát, cân nhắc bổ sung RAM hoặc cân bằng lại workload.'
      });
    }
  });

  // 2. Overcommit ratio vượt ngưỡng ở cấp cluster (vCPU:pCPU, vRAM:pRAM)
  buildClusterRows().forEach(c => {
    const cpuOc = c.model.cpuOvercommit;
    if (isFinite(cpuOc) && cpuOc > P.cpuOvercommitMax) {
      findings.push({
        severity: cpuOc > P.cpuOvercommitMax * 1.25 ? 'critical' : 'warning',
        type: 'overcommit_cpu',
        typeLabel: 'Overcommit ratio vượt ngưỡng (CPU)',
        entity: c.name, cluster: c.name,
        metric: `CPU overcommit ${cpuOc.toFixed(2)}x (ngưỡng ${fmtNum(P.cpuOvercommitMax,1)}x)`,
        recommendation: 'Tỷ lệ vCPU:pCPU vượt ngưỡng khuyến nghị — cân nhắc bổ sung host hoặc giảm vCPU cấp phát cho VM trong cluster.'
      });
    }
    const ramOc = c.model.ramOvercommit;
    if (isFinite(ramOc) && ramOc > P.ramOvercommitMax) {
      findings.push({
        severity: ramOc > P.ramOvercommitMax * 1.25 ? 'critical' : 'warning',
        type: 'overcommit_ram',
        typeLabel: 'Overcommit ratio vượt ngưỡng (RAM)',
        entity: c.name, cluster: c.name,
        metric: `RAM overcommit ${ramOc.toFixed(2)}x (ngưỡng ${fmtNum(P.ramOvercommitMax,1)}x)`,
        recommendation: 'Tỷ lệ vRAM:pRAM vượt ngưỡng khuyến nghị — cân nhắc bổ sung RAM vật lý hoặc giảm RAM cấp phát cho VM trong cluster.'
      });
    }
  });

  // 3. VM idle lâu / tắt lâu / over-provision CPU — chỉ tập trung compliance resource CPU/RAM,
  // bỏ kiểm tra storage/datastore vì hạ tầng dùng shared SAN storage.
  appState.vms.forEach(v => {
    if (v.isPoweredOn && isFinite(v.lastActivityDays) && v.lastActivityDays >= P.idleDays) {
      findings.push({
        severity: v.lastActivityDays >= P.idleDays * 2 ? 'critical' : 'warning',
        type: 'vm_idle',
        typeLabel: 'VM idle lâu',
        entity: v.vm, cluster: v.cluster,
        metric: `Không hoạt động ${fmtInt(v.lastActivityDays)} ngày`,
        recommendation: 'Xác nhận với owner; cân nhắc decommission hoặc thu hồi tài nguyên nếu không còn dùng.'
      });
    }
    if (!v.isPoweredOn && isFinite(v.powerOffDays) && v.powerOffDays >= P.idleDays) {
      findings.push({
        severity: v.powerOffDays >= P.idleDays * 3 ? 'critical' : 'warning',
        type: 'vm_off',
        typeLabel: 'VM tắt lâu',
        entity: v.vm, cluster: v.cluster,
        metric: `Đã tắt ${fmtInt(v.powerOffDays)} ngày`,
        recommendation: 'VM tắt lâu ngày vẫn giữ reservation vCPU/RAM — cân nhắc decommission để thu hồi tài nguyên.'
      });
    }
    // Ngưỡng usage% suy ra từ ngưỡng overcommit CPU (ví dụ 1:6 → ~16.7%): usage thực tế thấp hơn mức này
    // cho thấy vCPU cấp phát đang dư so với tỷ lệ overcommit khuyến nghị.
    const vmOverprovisionUsageThreshold = P.cpuOvercommitMax > 0 ? 100 / P.cpuOvercommitMax : 15;
    if (isFinite(v.cpuUsagePctRaw) && v.cpuUsagePctRaw > 0 && v.cpuUsagePctRaw < vmOverprovisionUsageThreshold && v.vcpu >= 4) {
      findings.push({
        severity: 'info',
        type: 'vm_overprovision',
        typeLabel: 'VM over-provision (vCPU)',
        entity: v.vm, cluster: v.cluster,
        metric: `${fmtInt(v.vcpu)} vCPU cấp phát, CPU usage chỉ ${fmtPct(v.cpuUsagePctRaw)} (ngưỡng ${vmOverprovisionUsageThreshold.toFixed(1)}% theo overcommit ${fmtNum(P.cpuOvercommitMax,1)}x)`,
        recommendation: 'vCPU cấp phát dư nhiều so với usage thực tế và ngưỡng overcommit CPU cấu hình; cân nhắc resize giảm vCPU.'
      });
    }
  });

  return findings;
}

const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 };

function getFilteredCompliance() {
  const P = appState.compliance;
  let findings = buildComplianceFindings();

  // apply cluster/host global filters
  findings = findings.filter(f => {
    if (appState.filters.cluster !== 'all' && f.cluster !== '—' && f.cluster !== appState.filters.cluster) return false;
    return true;
  });

  if (P.severity !== 'all') findings = findings.filter(f => f.severity === P.severity);
  if (P.type !== 'all') findings = findings.filter(f => f.type === P.type);
  if (P.search.trim()) {
    const q = P.search.trim().toLowerCase();
    findings = findings.filter(f => (f.entity + ' ' + f.cluster + ' ' + f.typeLabel).toLowerCase().includes(q));
  }

  findings.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  return findings;
}
/* =========================================================
   Render: Dashboard view (KPIs, charts, cluster table)
   ========================================================= */

function barCellHtml(pct, extraLabel) {
  const width = isFinite(pct) ? clamp(pct, 0, 130) : 0;
  const color = getBarColor(pct);
  return `<div class="mini-bar-cell">
      <div class="mini-bar"><span style="width:${width}%; background:${color}"></span></div>
      <span>${fmtPct(pct)}${extraLabel ? ' · ' + extraLabel : ''}</span>
    </div>`;
}

function renderCapacityKpis() {
  const hosts = getFilteredHosts();
  const vms = getFilteredVms();
  const model = buildCapacityModel(hosts, vms);
  // CPU%/RAM% đã tính theo overcommit ratio / ngưỡng overcommit (xem buildCapacityModel) nên dùng ngưỡng cố định 80%/100%.
  const cpuStatus = getStatusForPct(model.cpuPct, 80, 100);
  const ramStatus = getStatusForPct(model.ramPct, 80, 100);
  const dsStatus = getStatusForPct(model.storagePct, appState.policy.dsWarn, appState.policy.dsHigh);

  const cards = [
    {
      label: 'CPU — Total / Used / Free', value: `${fmtInt(model.usedVcpu)} / ${fmtInt(model.totalLogicalCores)} <span class="mono" style="font-size:.6em;color:var(--muted)">pCPU logic</span>`,
      sub: `Free: ${fmtInt(model.freeLogicalCores)} pCPU (${fmtInt(model.totalCores)} core vật lý × HT${fmtNum(model.htFactor,0)}) · Overcommit ${isFinite(model.cpuOvercommit) ? model.cpuOvercommit.toFixed(2)+'x' : 'NA'}`,
      pct: model.cpuPct, status: cpuStatus
    },
    {
      label: 'RAM — Total / Used / Free', value: `${fmtNum(model.usedRamGB,0)} / ${fmtNum(model.totalRamGB,0)} <span class="mono" style="font-size:.6em;color:var(--muted)">GB</span>`,
      sub: `Free: ${fmtNum(model.freeRamGB,0)} GB · Overcommit ${isFinite(model.ramOvercommit) ? model.ramOvercommit.toFixed(2)+'x' : 'NA'}`,
      pct: model.ramPct, status: ramStatus
    },
    {
      label: 'Storage — Provisioned / Used / Free', value: `${fmtNum(model.usedStorageGB,0)} / ${fmtNum(model.provStorageGB,0)} <span class="mono" style="font-size:.6em;color:var(--muted)">GB</span>`,
      sub: `Free: ${fmtNum(model.freeStorageGB,0)} GB`,
      pct: model.storagePct, status: dsStatus
    },
    {
      label: 'Hosts / VMs', value: `${fmtInt(model.hostCount)} <span class="mono" style="font-size:.6em;color:var(--muted)">hosts</span>`,
      sub: `${fmtInt(model.vmCount)} VM tổng · ${fmtInt(model.poweredOnCount)} on / ${fmtInt(model.poweredOffCount)} off`,
      pct: null, status: null
    }
  ];

  $('#capacityKpis').innerHTML = cards.map(c => `
    <article class="kpi-card">
      <div class="label">${esc(c.label)}</div>
      <div class="value">${c.value}</div>
      <div class="sub">${esc(c.sub)}</div>
      ${c.pct !== null ? `
        <div class="bar" style="margin-top:.7rem;"><span style="width:${clamp(isFinite(c.pct)?c.pct:0,0,130)}%; background:${getBarColor(c.pct)}"></span></div>
        <div class="bar-label"><span class="pill ${c.status.cls}">${esc(c.status.text)}</span><span>${fmtPct(c.pct)}</span></div>
      ` : ''}
    </article>
  `).join('');
}

function renderClusterTable() {
  const rows = buildClusterRows().filter(r => appState.filters.cluster === 'all' || r.name === appState.filters.cluster);
  const tbody = $('#clusterTable tbody');
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="9"><div class="empty-note">Chưa có dữ liệu cluster.</div></td></tr>`; return; }

  const sorted = applySort(rows, 'clusterTable', (r, key) => {
    switch (key) {
      case 'name': return r.name;
      case 'hosts': return r.hosts;
      case 'vms': return r.vms;
      case 'cpuPct': return r.model.cpuPct;
      case 'ramPct': return r.model.ramPct;
      case 'storagePct': return r.model.storagePct;
      case 'cpuOvercommit': return r.model.cpuOvercommit;
      case 'ramOvercommit': return r.model.ramOvercommit;
      case 'status': return SEVERITY_RANK(r.overall.cls);
      default: return 0;
    }
  });

  tbody.innerHTML = sorted.map(r => `
    <tr>
      <td><strong>${esc(r.name)}</strong></td>
      <td>${r.hosts}</td>
      <td>${r.vms}</td>
      <td>${barCellHtml(r.model.cpuPct)}</td>
      <td>${barCellHtml(r.model.ramPct)}</td>
      <td>${barCellHtml(r.model.storagePct)}</td>
      <td class="mono">${isFinite(r.model.cpuOvercommit) ? r.model.cpuOvercommit.toFixed(2)+'x' : 'NA'}</td>
      <td class="mono">${isFinite(r.model.ramOvercommit) ? r.model.ramOvercommit.toFixed(2)+'x' : 'NA'}</td>
      <td><span class="pill ${r.overall.cls}">${esc(r.overall.text)}</span></td>
    </tr>
  `).join('');
}

function SEVERITY_RANK(cls) { return { crit:0, bad:1, warn:2, ok:3, na:4 }[cls] ?? 5; }

function chartPalette() {
  const cs = getComputedStyle(document.documentElement);
  const g = (name) => cs.getPropertyValue(name).trim();
  return { text:g('--text'), muted:g('--muted'), divider:g('--divider'), primary:g('--primary'), accent:g('--accent'), ok:g('--ok'), warn:g('--warn'), bad:g('--bad'), crit:g('--crit'), faint:g('--faint') };
}

function destroyChart(key) {
  if (appState.charts[key]) { appState.charts[key].destroy(); appState.charts[key] = null; }
}

function renderDashboardCharts() {
  if (typeof Chart === 'undefined') return;
  const colors = chartPalette();
  const clusterRows = buildClusterRows();
  const hostRows = buildHostRows().sort((a,b) => Math.max(b.cpuPct||0,b.ramPct||0) - Math.max(a.cpuPct||0,a.ramPct||0)).slice(0, 10);
  const dsRows = buildDatastoreRows().slice(0, 10);

  ['clusterUtil','hostUtil','powerState','datastore','overcommit'].forEach(destroyChart);

  appState.charts.clusterUtil = new Chart($('#chartClusterUtil'), {
    type: 'bar',
    data: {
      labels: clusterRows.map(c => c.name),
      datasets: [
        { label:'CPU % (overcommit)', data: clusterRows.map(c => Number((c.model.cpuPct||0).toFixed(1))), backgroundColor: colors.primary, borderRadius: 5 },
        { label:'RAM % (overcommit)', data: clusterRows.map(c => Number((c.model.ramPct||0).toFixed(1))), backgroundColor: colors.accent, borderRadius: 5 },
        { label:'Storage %', data: clusterRows.map(c => Number((c.model.storagePct||0).toFixed(1))), backgroundColor: colors.warn, borderRadius: 5 }
      ]
    },
    options: { maintainAspectRatio:false, plugins:{legend:{labels:{color:colors.text}}}, scales:{ x:{ticks:{color:colors.muted},grid:{color:colors.divider}}, y:{beginAtZero:true,ticks:{color:colors.muted},grid:{color:colors.divider}} } }
  });

  appState.charts.hostUtil = new Chart($('#chartHostUtil'), {
    type: 'bar',
    data: {
      labels: hostRows.map(h => h.host),
      datasets: [
        { label:'CPU % (overcommit)', data: hostRows.map(h => Number((h.cpuPct||0).toFixed(1))), backgroundColor: colors.primary, borderRadius: 5 },
        { label:'RAM % (overcommit)', data: hostRows.map(h => Number((h.ramPct||0).toFixed(1))), backgroundColor: colors.accent, borderRadius: 5 }
      ]
    },
    options: { indexAxis:'y', maintainAspectRatio:false, plugins:{legend:{labels:{color:colors.text}}}, scales:{ x:{beginAtZero:true,ticks:{color:colors.muted},grid:{color:colors.divider}}, y:{ticks:{color:colors.muted},grid:{color:colors.divider}} } }
  });

  const vms = getFilteredVms();
  const onCount = vms.filter(v => v.isPoweredOn).length;
  const offCount = vms.length - onCount;
  appState.charts.powerState = new Chart($('#chartPowerState'), {
    type: 'doughnut',
    data: { labels:['Powered On','Powered Off'], datasets:[{ data:[onCount, offCount], backgroundColor:[colors.ok, colors.faint], borderWidth:0 }] },
    options: { maintainAspectRatio:false, plugins:{legend:{labels:{color:colors.text}}} }
  });

  appState.charts.datastore = new Chart($('#chartDatastore'), {
    type: 'bar',
    data: {
      labels: dsRows.map(d => d.name),
      datasets: [
        { label:'Provisioned GB', data: dsRows.map(d => Number(d.provGB.toFixed(1))), backgroundColor: colors.faint, borderRadius: 5 },
        { label:'Used GB', data: dsRows.map(d => Number(d.usedGB.toFixed(1))), backgroundColor: colors.warn, borderRadius: 5 }
      ]
    },
    options: { maintainAspectRatio:false, plugins:{legend:{labels:{color:colors.text}}}, scales:{ x:{ticks:{color:colors.muted},grid:{color:colors.divider}}, y:{beginAtZero:true,ticks:{color:colors.muted},grid:{color:colors.divider}} } }
  });

  const cpuOcMax = appState.policy.cpuOvercommitMax;
  const ramOcMax = appState.policy.ramOvercommitMax;
  appState.charts.overcommit = new Chart($('#chartOvercommit'), {
    data: {
      labels: clusterRows.map(c => c.name),
      datasets: [
        { type:'bar', label:'CPU overcommit (x)', data: clusterRows.map(c => Number((c.model.cpuOvercommit||0).toFixed(2))), backgroundColor: colors.primary, borderRadius: 5 },
        { type:'bar', label:'RAM overcommit (x)', data: clusterRows.map(c => Number((c.model.ramOvercommit||0).toFixed(2))), backgroundColor: colors.accent, borderRadius: 5 },
        { type:'line', label:`Ngưỡng CPU (${fmtNum(cpuOcMax,1)}x)`, data: clusterRows.map(() => cpuOcMax), borderColor: colors.bad, borderWidth: 2, borderDash: [6,4], pointRadius: 0, fill: false },
        { type:'line', label:`Ngưỡng RAM (${fmtNum(ramOcMax,1)}x)`, data: clusterRows.map(() => ramOcMax), borderColor: colors.warn, borderWidth: 2, borderDash: [2,3], pointRadius: 0, fill: false }
      ]
    },
    options: { maintainAspectRatio:false, plugins:{legend:{labels:{color:colors.text}}}, scales:{ x:{ticks:{color:colors.muted},grid:{color:colors.divider}}, y:{beginAtZero:true,ticks:{color:colors.muted},grid:{color:colors.divider}} } }
  });
}
/* =========================================================
   Sort helper + Render: Compliance & Inventory views
   ========================================================= */

function applySort(rows, tableKey, valueFn) {
  const s = appState.sort.table[tableKey];
  if (!s) return rows;
  const sorted = [...rows].sort((a, b) => {
    const av = valueFn(a, s.key), bv = valueFn(b, s.key);
    if (typeof av === 'string' || typeof bv === 'string') return String(av).localeCompare(String(bv)) * (s.dir === 'asc' ? 1 : -1);
    const an = isFinite(av) ? av : -Infinity, bn = isFinite(bv) ? bv : -Infinity;
    return (an - bn) * (s.dir === 'asc' ? 1 : -1);
  });
  return sorted;
}

function bindSortableHeaders(tableId, tableKey, renderFn) {
  $$(`#${tableId} th[data-key]`).forEach(th => {
    th.onclick = () => {
      const key = th.dataset.key;
      const current = appState.sort.table[tableKey];
      let dir = 'desc';
      if (current && current.key === key) dir = current.dir === 'desc' ? 'asc' : 'desc';
      appState.sort.table[tableKey] = { key, dir };
      renderFn();
    };
  });
}

function highlightSortHeaders(tableId, tableKey) {
  const s = appState.sort.table[tableKey];
  $$(`#${tableId} th[data-key]`).forEach(th => {
    th.classList.remove('sorted', 'sorted-asc');
    if (s && th.dataset.key === s.key) th.classList.add(s.dir === 'desc' ? 'sorted' : 'sorted-asc');
  });
}

/* ---------- Compliance ---------- */
const SEVERITY_PILL = { critical:'crit', warning:'warn', info:'na' };
const SEVERITY_LABEL = { critical:'Critical', warning:'Warning', info:'Info' };

function renderComplianceKpis() {
  const findings = buildComplianceFindings();
  const crit = findings.filter(f => f.severity === 'critical').length;
  const warn = findings.filter(f => f.severity === 'warning').length;
  const info = findings.filter(f => f.severity === 'info').length;
  const byType = {};
  findings.forEach(f => byType[f.type] = (byType[f.type]||0) + 1);

  const cards = [
    { label:'Tổng findings', value: findings.length, sub:`Critical ${crit} · Warning ${warn} · Info ${info}`, tone: crit>0?'tone-crit':(warn>0?'tone-warn':'tone-ok') },
    { label:'Host CPU/RAM vượt ngưỡng', value: (byType.host_cpu||0) + (byType.host_ram||0), sub:'CPU hoặc RAM usage vượt ngưỡng cấu hình', tone:'tone-bad' },
    { label:'Overcommit ratio vượt ngưỡng', value: (byType.overcommit_cpu||0) + (byType.overcommit_ram||0), sub:'Tỷ lệ vCPU:pCPU hoặc vRAM:pRAM vượt ngưỡng cấu hình', tone:'tone-warn' },
    { label:'VM cần rà soát', value: (byType.vm_idle||0)+(byType.vm_off||0)+(byType.vm_overprovision||0), sub:'Idle lâu, tắt lâu, hoặc over-provision vCPU', tone:'tone-warn' }
  ];

  $('#complianceKpis').innerHTML = cards.map(c => `
    <article class="kpi-card">
      <div class="label">${esc(c.label)}</div>
      <div class="value ${c.tone}">${c.value}</div>
      <div class="sub">${esc(c.sub)}</div>
    </article>
  `).join('');
}

function renderComplianceTable() {
  const rows = getFilteredCompliance();
  const tbody = $('#complianceTable tbody');
  $('#complianceRowCount').textContent = `${rows.length} findings`;
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="6"><div class="empty-note">Không có compliance finding phù hợp bộ lọc.</div></td></tr>`; return; }

  const sorted = applySort(rows, 'complianceTable', (r, key) => {
    if (key === 'severity') return SEVERITY_ORDER[r.severity];
    return r[key];
  });

  tbody.innerHTML = sorted.map(f => `
    <tr>
      <td><span class="pill ${SEVERITY_PILL[f.severity]}"><span class="pill-dot"></span>${SEVERITY_LABEL[f.severity]}</span></td>
      <td>${esc(f.typeLabel)}</td>
      <td><strong>${esc(f.entity)}</strong></td>
      <td>${esc(f.cluster)}</td>
      <td class="mono">${esc(f.metric)}</td>
      <td class="wrap"><div class="recommendation">${esc(f.recommendation)}</div></td>
    </tr>
  `).join('');
}

function renderComplianceView() {
  renderComplianceKpis();
  renderComplianceTable();
  bindSortableHeaders('complianceTable', 'complianceTable', renderComplianceTable);
  highlightSortHeaders('complianceTable', 'complianceTable');
}

/* ---------- Inventory: Host table ---------- */
function renderHostTable() {
  let rows = buildHostRows().filter(h => {
    if (appState.filters.cluster !== 'all' && h.cluster !== appState.filters.cluster) return false;
    if (appState.filters.host !== 'all' && h.host !== appState.filters.host) return false;
    if (appState.hostTableSearch.trim()) {
      const q = appState.hostTableSearch.trim().toLowerCase();
      if (!(h.host + ' ' + h.cluster).toLowerCase().includes(q)) return false;
    }
    return true;
  });

  const tbody = $('#hostTable tbody');
  $('#hostRowCount').textContent = `${rows.length} hosts`;
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="10"><div class="empty-note">Không có host phù hợp.</div></td></tr>`; return; }

  rows = applySort(rows, 'hostTable', (r, key) => r[key]);

  tbody.innerHTML = rows.map(h => `
    <tr>
      <td>${esc(h.cluster)}</td>
      <td><strong>${esc(h.host)}</strong></td>
      <td class="mono">${fmtInt(h.cores)}</td>
      <td class="mono">${fmtNum(h.ramGB,0)}</td>
      <td class="mono">${fmtInt(h.vcpuUsed)}</td>
      <td>${barCellHtml(h.cpuPct)}</td>
      <td class="mono">${fmtNum(h.ramUsedGB,0)}</td>
      <td>${barCellHtml(h.ramPct)}</td>
      <td class="mono">${h.vmCount}</td>
      <td><span class="pill ${h.overall.cls}">${esc(h.overall.text)}</span></td>
    </tr>
  `).join('');
}

/* ---------- Inventory: VM table ---------- */
function renderVmTable() {
  let rows = getFilteredVms().filter(v => {
    if (appState.vmStatusFilter === 'on' && !v.isPoweredOn) return false;
    if (appState.vmStatusFilter === 'off' && v.isPoweredOn) return false;
    if (appState.vmTableSearch.trim()) {
      const q = appState.vmTableSearch.trim().toLowerCase();
      if (!(v.vm + ' ' + v.ipAddress + ' ' + v.cluster + ' ' + v.host).toLowerCase().includes(q)) return false;
    }
    return true;
  }).map(v => ({ ...v, diskUsedPct: v.diskProvGB > 0 ? (v.diskUsedGB / v.diskProvGB) * 100 : NaN }));

  const tbody = $('#vmTable tbody');
  $('#vmRowCount').textContent = `${rows.length} VMs`;
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="12"><div class="empty-note">Không có VM phù hợp.</div></td></tr>`; return; }

  rows = applySort(rows, 'vmTable', (r, key) => r[key]);

  tbody.innerHTML = rows.map(v => `
    <tr>
      <td>${esc(v.cluster)}</td>
      <td>${esc(v.host || '-')}</td>
      <td><strong>${esc(v.vm)}</strong></td>
      <td><span class="pill ${v.isPoweredOn ? 'ok' : 'na'}">${esc(v.powerState)}</span></td>
      <td class="mono">${esc(v.ipAddress || '-')}</td>
      <td class="mono">${fmtInt(v.vcpu)}</td>
      <td class="mono">${fmtNum(v.ramGB,0)}</td>
      <td class="mono">${fmtNum(v.diskProvGB,0)}</td>
      <td class="mono">${fmtNum(v.diskUsedGB,0)}</td>
      <td>${barCellHtml(v.diskUsedPct)}</td>
      <td class="mono">${v.isPoweredOn && isFinite(v.lastActivityDays) ? fmtInt(v.lastActivityDays) : '-'}</td>
      <td class="mono">${!v.isPoweredOn && isFinite(v.powerOffDays) ? fmtInt(v.powerOffDays) : '-'}</td>
    </tr>
  `).join('');
}

function renderInventoryView() {
  renderHostTable();
  renderVmTable();
  bindSortableHeaders('hostTable', 'hostTable', renderHostTable);
  bindSortableHeaders('vmTable', 'vmTable', renderVmTable);
  highlightSortHeaders('hostTable', 'hostTable');
  highlightSortHeaders('vmTable', 'vmTable');
}
/* =========================================================
   What-if Simulation
   ========================================================= */

const SCENARIO_TEMPLATES = [
  { id:'peakSeason', title:'Peak season', desc:'Tăng tải VM 30% (vCPU & RAM) để mô phỏng cao điểm kinh doanh.' },
  { id:'drDrill', title:'DR drill', desc:'Bật toàn bộ VM đang tắt để mô phỏng kích hoạt DR site.' },
  { id:'lostHost', title:'Mất 1 host', desc:'Loại bỏ host lớn nhất khỏi cluster để kiểm tra khả năng chịu lỗi N+1.' },
  { id:'migration', title:'Migration', desc:'Di chuyển 30% VM từ cluster tải cao nhất sang cluster tải thấp nhất.' },
  { id:'costOpt', title:'Cost optimization', desc:'Decommission toàn bộ VM idle/tắt lâu để giải phóng tài nguyên.' },
  { id:'expansion', title:'Expansion plan', desc:'Thêm 1 host mới (trung bình cấu hình hiện có) vào cluster mục tiêu.' }
];

function currentWhatifCluster() {
  return appState.whatif.cluster || getClusterList()[0] || null;
}

function baseModelForCluster(clusterName) {
  const hosts = appState.hosts.filter(h => h.cluster === clusterName);
  const vms = appState.vms.filter(v => v.cluster === clusterName);
  return buildCapacityModel(hosts, vms);
}

function modelWithOverrides(clusterName, overrides) {
  const eff = getEffectiveData(overrides);
  const hosts = eff.hosts.filter(h => h.cluster === clusterName);
  const vms = eff.vms.filter(v => v.cluster === clusterName);
  return buildCapacityModel(hosts, vms);
}

function deltaBadge(before, after, higherIsWorse = true) {
  const delta = after - before;
  if (!isFinite(delta) || Math.abs(delta) < 0.05) return `<span class="delta-badge delta-flat">±0.0</span>`;
  const worse = higherIsWorse ? delta > 0 : delta < 0;
  return `<span class="delta-badge ${worse ? 'delta-up' : 'delta-down'}">${delta >= 0 ? '+' : ''}${delta.toFixed(1)}</span>`;
}

function renderCompareBlock(label, before, after, unit, higherIsWorse = true) {
  return `
    <div class="metric-row">
      <span>${esc(label)}</span>
      <span class="mono">${fmtNum(before,1)}${unit} → ${fmtNum(after,1)}${unit} ${deltaBadge(before, after, higherIsWorse)}</span>
    </div>`;
}

function renderSingleBlock(label, value, unit) {
  return `
    <div class="metric-row">
      <span>${esc(label)}</span>
      <span class="mono">${fmtNum(value,1)}${unit}</span>
    </div>`;
}

function buildInsight(clusterName, before, after) {
  // CPU%/RAM% đã tính theo overcommit ratio / ngưỡng overcommit nên dùng ngưỡng cố định 80%/100%.
  const bStatus = getStatusForPct(before.cpuPct, 80, 100);
  const aStatusCpu = getStatusForPct(after.cpuPct, 80, 100);
  const aStatusRam = getStatusForPct(after.ramPct, 80, 100);
  const worstAfter = combineStatus(aStatusCpu, aStatusRam);
  let msg = `Cluster <strong>${esc(clusterName)}</strong>: `;
  if (worstAfter.cls === 'crit') msg += 'sau thay đổi sẽ VƯỢT NGƯỠNG năng lực — cần bổ sung tài nguyên trước khi triển khai.';
  else if (worstAfter.cls === 'bad') msg += 'sau thay đổi bước vào vùng CẢNH BÁO — nên có kế hoạch mở rộng trong ngắn hạn.';
  else if (worstAfter.cls === 'warn') msg += 'sau thay đổi ở mức THEO DÕI — vẫn an toàn nhưng cần giám sát sát sao.';
  else msg += 'sau thay đổi vẫn AN TOÀN với biên năng lực còn dư.';
  if (after.cpuPct - before.cpuPct > 15) msg += ' CPU tăng đáng kể, là yếu tố cần ưu tiên theo dõi.';
  if (after.ramPct - before.ramPct > 15) msg += ' RAM tăng đáng kể, là yếu tố cần ưu tiên theo dõi.';
  return msg;
}

function renderSimResult(clusterName, before, after, extraRows, title) {
  const html = `
    <div class="panel-head"><div><div class="panel-title">${esc(title)}</div><p class="panel-sub">So sánh before/after cho cluster ${esc(clusterName)}.</p></div></div>
    <div class="compare-grid">
      <div class="compare-col">
        <h5>Before</h5>
        ${renderSingleBlock('CPU %', before.cpuPct, '%')}
        ${renderSingleBlock('RAM %', before.ramPct, '%')}
        ${renderSingleBlock('Storage %', before.storagePct, '%')}
        ${renderSingleBlock('CPU overcommit', before.cpuOvercommit, 'x')}
        ${renderSingleBlock('RAM overcommit', before.ramOvercommit, 'x')}
      </div>
      <div class="compare-arrow">→</div>
      <div class="compare-col">
        <h5>After</h5>
        ${renderCompareBlock('CPU %', before.cpuPct, after.cpuPct, '%')}
        ${renderCompareBlock('RAM %', before.ramPct, after.ramPct, '%')}
        ${renderCompareBlock('Storage %', before.storagePct, after.storagePct, '%')}
        ${renderCompareBlock('CPU overcommit', before.cpuOvercommit, after.cpuOvercommit, 'x')}
        ${renderCompareBlock('RAM overcommit', before.ramOvercommit, after.ramOvercommit, 'x')}
      </div>
    </div>
    ${extraRows || ''}
    <div class="insight-box">💡 ${buildInsight(clusterName, before, after)}</div>
  `;
  const area = $('#simResultArea');
  area.innerHTML = html;
  area.classList.remove('hidden');

  appState.simHistory.unshift({ title, cluster: clusterName, time: new Date().toISOString(), before, after });
  appState.simHistory = appState.simHistory.slice(0, 12);
  renderSimHistory();
}

function renderSimHistory() {
  const list = $('#simHistoryList');
  if (!appState.simHistory.length) { list.innerHTML = `<div class="empty-note">Chưa có simulation nào.</div>`; return; }
  list.innerHTML = appState.simHistory.map(h => `
    <div class="sim-log-item">
      <strong>${esc(h.title)}</strong> · ${esc(h.cluster)}<br>
      CPU ${fmtPct(h.before.cpuPct)} → ${fmtPct(h.after.cpuPct)} · RAM ${fmtPct(h.before.ramPct)} → ${fmtPct(h.after.ramPct)}
      <div style="color:var(--faint);margin-top:.2rem;">${formatReportTime(h.time)}</div>
    </div>
  `).join('');
}

/* ---------- Multi-row Add Host ---------- */
function ensureHostRows(cluster) {
  const d = appState.whatif.drafts;
  if (!d.addHostRows || !d.addHostRows.length) {
    d.addHostRows = [{ id: 1, name: 'NEW-HOST-01', cores: 32, ramGB: 256 }];
    d.addHostSeq = 1;
  }
}

function renderAddHostForm(cluster, area) {
  const rows = appState.whatif.drafts.addHostRows;
  area.innerHTML = `
    <div class="multi-row-list" id="addHostRowsList">
      ${rows.map(r => `
        <div class="multi-row-item" data-row-id="${r.id}">
          <div class="sim-form-grid">
            <div class="field"><label>Tên host mới</label><input class="rowName" value="${esc(r.name)}"></div>
            <div class="field"><label>CPU cores</label><input class="rowCores" value="${r.cores}"></div>
            <div class="field"><label>RAM GB</label><input class="rowRam" value="${r.ramGB}"></div>
          </div>
          <button class="btn btn-secondary btn-sm icon-btn btn-remove-row" type="button" title="Xoá host này" ${rows.length <= 1 ? 'disabled' : ''}>✕</button>
        </div>
      `).join('')}
    </div>
    <div class="multi-row-add"><button class="btn btn-secondary btn-sm" id="btnAddHostRow" type="button">+ Thêm host</button></div>
    <div class="sim-actions"><button class="btn btn-primary" id="btnRunAddHost" type="button">Chạy simulation (${rows.length} host)</button></div>
  `;

  const syncRowsFromDom = () => {
    const items = $$('#addHostRowsList .multi-row-item');
    rows.forEach((r, i) => {
      const el = items[i];
      if (!el) return;
      r.name = el.querySelector('.rowName').value.trim() || `NEW-HOST-${i+1}`;
      r.cores = cleanNum(el.querySelector('.rowCores').value);
      r.ramGB = cleanNum(el.querySelector('.rowRam').value);
    });
  };

  $$('#addHostRowsList .btn-remove-row').forEach((btn, i) => {
    btn.onclick = () => {
      syncRowsFromDom();
      rows.splice(i, 1);
      renderAddHostForm(cluster, area);
    };
  });

  $('#btnAddHostRow').onclick = () => {
    syncRowsFromDom();
    appState.whatif.drafts.addHostSeq += 1;
    const n = appState.whatif.drafts.addHostSeq;
    rows.push({ id: n, name: `NEW-HOST-0${n}`, cores: 32, ramGB: 256 });
    renderAddHostForm(cluster, area);
  };

  $('#btnRunAddHost').onclick = () => {
    syncRowsFromDom();
    const addHosts = rows.map(r => ({ cluster, host: r.name || 'NEW-HOST', cores: cleanNum(r.cores), ramGB: cleanNum(r.ramGB) }));
    const before = baseModelForCluster(cluster);
    const after = modelWithOverrides(cluster, { addHosts });
    const totalCores = addHosts.reduce((a,h)=>a+h.cores,0), totalRam = addHosts.reduce((a,h)=>a+h.ramGB,0);
    const extra = addHosts.length > 1
      ? `<p class="panel-sub" style="margin-top:.5rem;">Thêm ${addHosts.length} host mới: tổng +${fmtInt(totalCores)} cores, +${fmtNum(totalRam,0)} GB RAM.</p>`
      : '';
    renderSimResult(cluster, before, after, extra, addHosts.length > 1 ? `Add Host (${addHosts.length} host)` : 'Add Host');
  };
}

/* ---------- Multi-row Add VM ---------- */
function ensureVmRows(cluster) {
  const d = appState.whatif.drafts;
  if (!d.addVmRows || !d.addVmRows.length) {
    d.addVmRows = [{ id: 1, count: 5, vcpu: 4, ramGB: 16, diskGB: 100 }];
    d.addVmSeq = 1;
  }
}

function renderAddVmForm(cluster, area) {
  const rows = appState.whatif.drafts.addVmRows;
  area.innerHTML = `
    <div class="multi-row-list" id="addVmRowsList">
      ${rows.map(r => `
        <div class="multi-row-item" data-row-id="${r.id}">
          <div class="sim-form-grid">
            <div class="field"><label>Số lượng VM</label><input class="rowCount" value="${r.count}"></div>
            <div class="field"><label>vCPU / VM</label><input class="rowVcpu" value="${r.vcpu}"></div>
            <div class="field"><label>RAM GB / VM</label><input class="rowRam" value="${r.ramGB}"></div>
            <div class="field"><label>Disk GB / VM</label><input class="rowDisk" value="${r.diskGB}"></div>
          </div>
          <button class="btn btn-secondary btn-sm icon-btn btn-remove-row" type="button" title="Xoá nhóm VM này" ${rows.length <= 1 ? 'disabled' : ''}>✕</button>
        </div>
      `).join('')}
    </div>
    <div class="multi-row-add"><button class="btn btn-secondary btn-sm" id="btnAddVmRow" type="button">+ Thêm nhóm VM</button></div>
    <div class="sim-actions"><button class="btn btn-primary" id="btnRunAddVm" type="button">Chạy simulation</button></div>
  `;

  const syncRowsFromDom = () => {
    const items = $$('#addVmRowsList .multi-row-item');
    rows.forEach((r, i) => {
      const el = items[i];
      if (!el) return;
      r.count = Math.max(1, Math.round(cleanNum(el.querySelector('.rowCount').value)) || 1);
      r.vcpu = cleanNum(el.querySelector('.rowVcpu').value);
      r.ramGB = cleanNum(el.querySelector('.rowRam').value);
      r.diskGB = cleanNum(el.querySelector('.rowDisk').value);
    });
  };

  $$('#addVmRowsList .btn-remove-row').forEach((btn, i) => {
    btn.onclick = () => {
      syncRowsFromDom();
      rows.splice(i, 1);
      renderAddVmForm(cluster, area);
    };
  });

  $('#btnAddVmRow').onclick = () => {
    syncRowsFromDom();
    appState.whatif.drafts.addVmSeq += 1;
    const n = appState.whatif.drafts.addVmSeq;
    rows.push({ id: n, count: 3, vcpu: 2, ramGB: 8, diskGB: 50 });
    renderAddVmForm(cluster, area);
  };

  $('#btnRunAddVm').onclick = () => {
    syncRowsFromDom();
    let seq = 0;
    const addVms = [];
    rows.forEach((r, gi) => {
      const count = Math.max(1, Math.round(cleanNum(r.count)) || 1);
      const vcpu = cleanNum(r.vcpu), ram = cleanNum(r.ramGB), disk = cleanNum(r.diskGB);
      for (let i = 0; i < count; i++) {
        seq += 1;
        addVms.push({ cluster, host:'', vm:`SIM-VM-${gi+1}-${seq}`, powerState:'Powered On', isPoweredOn:true, ipAddress:'', vcpu, ramGB:ram, diskProvGB:disk, diskUsedGB:disk*0.5, datastore:'UNKNOWN', lastActivityDays:0, powerOffDays:null });
      }
    });
    const before = baseModelForCluster(cluster);
    const after = modelWithOverrides(cluster, { addVms });
    const totalVcpu = addVms.reduce((a,v)=>a+v.vcpu,0), totalRam = addVms.reduce((a,v)=>a+v.ramGB,0);
    const groupSummary = rows.map(r => `${Math.round(r.count)}× (${fmtNum(r.vcpu,0)} vCPU/${fmtNum(r.ramGB,0)}GB)`).join(', ');
    const extra = `<p class="panel-sub" style="margin-top:.5rem;">Thêm ${addVms.length} VM mới từ ${rows.length} nhóm [${esc(groupSummary)}], tổng +${fmtInt(totalVcpu)} vCPU, +${fmtNum(totalRam,0)} GB RAM.</p>`;
    renderSimResult(cluster, before, after, extra, rows.length > 1 ? `Add VM (${rows.length} nhóm, ${addVms.length} VM)` : 'Add VM');
  };
}

/* ---------- Individual sim forms ---------- */
function renderSimForm() {
  const tab = appState.whatif.tab;
  const area = $('#simFormArea');
  const cluster = currentWhatifCluster();
  if (!cluster) { area.innerHTML = `<div class="empty-note">Cần import dữ liệu trước khi mô phỏng.</div>`; return; }

  if (tab === 'addHost') {
    ensureHostRows(cluster);
    renderAddHostForm(cluster, area);
  } else if (tab === 'addVm') {
    ensureVmRows(cluster);
    renderAddVmForm(cluster, area);
  } else if (tab === 'resizeVm') {
    const vmOptions = appState.vms.filter(v => v.cluster === cluster).map(v => `<option value="${esc(v.vm)}">${esc(v.vm)}</option>`).join('');
    area.innerHTML = `
      <div class="sim-form-grid">
        <div class="field"><label>Chọn VM</label><select id="simResizeVmName">${vmOptions || '<option value="">Không có VM</option>'}</select></div>
        <div class="field"><label>vCPU mới</label><input id="simResizeVcpu" value="8"></div>
        <div class="field"><label>RAM GB mới</label><input id="simResizeRam" value="32"></div>
      </div>
      <div class="sim-actions"><button class="btn btn-primary" id="btnRunResize" type="button">Chạy simulation</button></div>
    `;
    $('#btnRunResize').onclick = () => {
      const name = $('#simResizeVmName').value;
      if (!name) return;
      const before = baseModelForCluster(cluster);
      const after = modelWithOverrides(cluster, { resizeVm: { cluster, name, vcpu: cleanNum($('#simResizeVcpu').value), ramGB: cleanNum($('#simResizeRam').value) } });
      renderSimResult(cluster, before, after, '', `Resize VM — ${esc(name)}`);
    };
  } else if (tab === 'decommission') {
    const idleVms = appState.vms.filter(v => v.cluster === cluster && ((v.isPoweredOn && isFinite(v.lastActivityDays) && v.lastActivityDays >= appState.policy.idleDays) || (!v.isPoweredOn && isFinite(v.powerOffDays) && v.powerOffDays >= appState.policy.idleDays)));
    area.innerHTML = `
      <p class="panel-sub">Tìm thấy <strong>${idleVms.length}</strong> VM idle/tắt lâu (≥ ${appState.policy.idleDays} ngày) trong cluster ${esc(cluster)}.</p>
      <div class="sim-actions"><button class="btn btn-primary" id="btnRunDecom" type="button" ${idleVms.length ? '' : 'disabled'}>Decommission toàn bộ VM idle</button></div>
    `;
    $('#btnRunDecom').onclick = () => {
      const before = baseModelForCluster(cluster);
      const after = modelWithOverrides(cluster, { removeVmNames: idleVms.map(v => v.vm) });
      renderSimResult(cluster, before, after, `<p class="panel-sub" style="margin-top:.5rem;">Giải phóng ${fmtNum(idleVms.reduce((a,v)=>a+v.diskProvGB,0),0)} GB storage provisioned.</p>`, 'Decommission Idle VM');
    };
  }
}

function runScenarioTemplate(id) {
  const cluster = currentWhatifCluster();
  if (!cluster) return;
  const before = baseModelForCluster(cluster);
  let after, title, extra = '';

  if (id === 'peakSeason') {
    const vms = appState.vms.filter(v => v.cluster === cluster && v.isPoweredOn);
    const addVms = vms.map(v => ({ ...v, vm: 'PEAK-' + v.vm, vcpu: Math.round(v.vcpu * 0.3), ramGB: v.ramGB * 0.3, diskProvGB: 0, diskUsedGB: 0 }));
    after = modelWithOverrides(cluster, { addVms });
    title = 'Scenario: Peak season (+30% tải)';
  } else if (id === 'drDrill') {
    const offVms = appState.vms.filter(v => v.cluster === cluster && !v.isPoweredOn);
    const eff = getEffectiveData({});
    eff.vms = eff.vms.map(v => (v.cluster === cluster && !v.isPoweredOn) ? { ...v, isPoweredOn: true, powerState: 'Powered On' } : v);
    after = buildCapacityModel(eff.hosts.filter(h => h.cluster === cluster), eff.vms.filter(v => v.cluster === cluster));
    title = 'Scenario: DR drill (bật toàn bộ VM tắt)';
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Kích hoạt ${offVms.length} VM đang tắt.</p>`;
  } else if (id === 'lostHost') {
    const hosts = appState.hosts.filter(h => h.cluster === cluster);
    if (!hosts.length) return;
    const biggest = [...hosts].sort((a,b) => b.cores - a.cores)[0];
    after = modelWithOverrides(cluster, { removeHostNames: [biggest.host] });
    title = `Scenario: Mất 1 host (${esc(biggest.host)})`;
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Kiểm tra N+1: cluster có chịu được mất host lớn nhất (${fmtInt(biggest.cores)} cores, ${fmtNum(biggest.ramGB,0)} GB) không.</p>`;
  } else if (id === 'migration') {
    const rows = buildClusterRows();
    const sortedByCpu = [...rows].sort((a,b) => (b.model.cpuPct||0) - (a.model.cpuPct||0));
    const highest = sortedByCpu[0], lowest = sortedByCpu[sortedByCpu.length - 1];
    if (!highest || !lowest || highest.name === lowest.name) return;
    const vmsToMove = appState.vms.filter(v => v.cluster === highest.name);
    const moveCount = Math.max(1, Math.round(vmsToMove.length * 0.3));
    const moving = vmsToMove.slice(0, moveCount);
    const eff = getEffectiveData({});
    eff.vms = eff.vms.map(v => moving.some(m => m.vm === v.vm && m.cluster === v.cluster) ? { ...v, cluster: lowest.name } : v);
    const targetCluster = cluster === highest.name ? highest.name : (cluster === lowest.name ? lowest.name : highest.name);
    after = buildCapacityModel(eff.hosts.filter(h => h.cluster === targetCluster), eff.vms.filter(v => v.cluster === targetCluster));
    const beforeTarget = baseModelForCluster(targetCluster);
    title = `Scenario: Migration ${esc(highest.name)} → ${esc(lowest.name)}`;
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Di chuyển ${moveCount} VM từ cluster tải cao nhất (${esc(highest.name)}) sang cluster tải thấp nhất (${esc(lowest.name)}). Kết quả hiển thị cho cluster ${esc(targetCluster)}.</p>`;
    renderSimResult(targetCluster, beforeTarget, after, extra, title);
    return;
  } else if (id === 'costOpt') {
    const P = appState.policy;
    const idleVms = appState.vms.filter(v => v.cluster === cluster && ((v.isPoweredOn && isFinite(v.lastActivityDays) && v.lastActivityDays >= P.idleDays) || (!v.isPoweredOn && isFinite(v.powerOffDays) && v.powerOffDays >= P.idleDays)));
    after = modelWithOverrides(cluster, { removeVmNames: idleVms.map(v => v.vm) });
    title = 'Scenario: Cost optimization (decommission idle/off)';
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Loại bỏ ${idleVms.length} VM idle/tắt lâu, giải phóng ${fmtNum(idleVms.reduce((a,v)=>a+v.diskProvGB,0),0)} GB storage.</p>`;
  } else if (id === 'expansion') {
    const hosts = appState.hosts.filter(h => h.cluster === cluster);
    const avgCores = hosts.length ? hosts.reduce((a,h)=>a+h.cores,0)/hosts.length : 32;
    const avgRam = hosts.length ? hosts.reduce((a,h)=>a+h.ramGB,0)/hosts.length : 256;
    after = modelWithOverrides(cluster, { addHosts: [{ cluster, host: 'EXPANSION-HOST', cores: Math.round(avgCores), ramGB: Math.round(avgRam) }] });
    title = 'Scenario: Expansion plan (+1 host trung bình)';
    extra = `<p class="panel-sub" style="margin-top:.5rem;">Host mới: ${fmtInt(avgCores)} cores, ${fmtInt(avgRam)} GB RAM (trung bình hosts hiện có).</p>`;
  }

  renderSimResult(cluster, before, after, extra, title);
}

function renderScenarioTemplates() {
  $('#scenarioTemplates').innerHTML = SCENARIO_TEMPLATES.map(t => `
    <button class="scenario-btn" type="button" data-scenario="${t.id}">
      <div class="t">${esc(t.title)}</div>
      <div class="d">${esc(t.desc)}</div>
    </button>
  `).join('');
  $$('#scenarioTemplates [data-scenario]').forEach(btn => btn.onclick = () => runScenarioTemplate(btn.dataset.scenario));
}

function renderWhatifClusterSelect() {
  const sel = $('#whatifClusterSelect');
  const clusters = getClusterList();
  if (!clusters.length) { sel.innerHTML = `<option value="">Không có cluster</option>`; return; }
  sel.innerHTML = clusters.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  if (!appState.whatif.cluster || !clusters.includes(appState.whatif.cluster)) appState.whatif.cluster = clusters[0];
  sel.value = appState.whatif.cluster;
}

function renderWhatifView() {
  renderScenarioTemplates();
  renderWhatifClusterSelect();
  $$('#simTabs .sim-tab').forEach(btn => btn.classList.toggle('active', btn.dataset.sim === appState.whatif.tab));
  renderSimForm();
  renderSimHistory();
}
/* =========================================================
   Sample data, validation banner, export CSV/HTML
   ========================================================= */

function daysAgoISO(days) { return new Date(Date.now() - days * 86400000).toISOString(); }

function loadSampleData() {
  appState.hostsRaw = [
    { Cluster:'Cluster-Prod-A', Host:'esx-a-01', 'CPUs':'64', 'Memory GB':'512', 'CPU Usage %':'62', 'Memory Usage %':'70' },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-02', 'CPUs':'64', 'Memory GB':'512', 'CPU Usage %':'58', 'Memory Usage %':'65' },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-03', 'CPUs':'64', 'Memory GB':'512', 'CPU Usage %':'91', 'Memory Usage %':'88' },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-01', 'CPUs':'48', 'Memory GB':'384', 'CPU Usage %':'35', 'Memory Usage %':'40' },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-02', 'CPUs':'48', 'Memory GB':'384', 'CPU Usage %':'28', 'Memory Usage %':'33' },
    { Cluster:'Cluster-DR', Host:'esx-dr-01', 'CPUs':'32', 'Memory GB':'256', 'CPU Usage %':'12', 'Memory Usage %':'15' },
    { Cluster:'Cluster-DR', Host:'esx-dr-02', 'CPUs':'32', 'Memory GB':'256', 'CPU Usage %':'10', 'Memory Usage %':'14' }
  ];

  appState.vmsRaw = [
    { Cluster:'Cluster-Prod-A', Host:'esx-a-01', Name:'app-web-01', 'Power State':'Powered On', 'CPUs':'8', 'Memory GB':'16', 'Provisioned GB':'300', 'Used GB':'210', 'IP Address':'10.0.0.11', Datastore:'DS-A1', 'CPU Usage %':'45', 'Last Activity': daysAgoISO(2) },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-02', Name:'db-core-01', 'Power State':'Powered On', 'CPUs':'16', 'Memory GB':'64', 'Provisioned GB':'800', 'Used GB':'620', 'IP Address':'10.0.0.12', Datastore:'DS-A1', 'CPU Usage %':'70', 'Last Activity': daysAgoISO(1) },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-03', Name:'app-web-02', 'Power State':'Powered On', 'CPUs':'8', 'Memory GB':'16', 'Provisioned GB':'250', 'Used GB':'90', 'IP Address':'10.0.0.13', Datastore:'DS-A2', 'CPU Usage %':'8', 'Last Activity': daysAgoISO(45) },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-03', Name:'legacy-svc-01', 'Power State':'Powered Off', 'CPUs':'4', 'Memory GB':'8', 'Provisioned GB':'150', 'Used GB':'60', 'IP Address':'10.0.0.14', Datastore:'DS-A2', 'Power Off Date': daysAgoISO(120) },
    { Cluster:'Cluster-Prod-A', Host:'esx-a-01', Name:'batch-job-01', 'Power State':'Powered On', 'CPUs':'12', 'Memory GB':'32', 'Provisioned GB':'400', 'Used GB':'110', 'IP Address':'10.0.0.15', Datastore:'DS-A1', 'CPU Usage %':'6', 'Last Activity': daysAgoISO(60) },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-01', Name:'app-b-01', 'Power State':'Powered On', 'CPUs':'12', 'Memory GB':'32', 'Provisioned GB':'250', 'Used GB':'160', 'IP Address':'10.0.1.11', Datastore:'DS-B1', 'CPU Usage %':'38', 'Last Activity': daysAgoISO(0) },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-02', Name:'db-b-01', 'Power State':'Powered On', 'CPUs':'12', 'Memory GB':'48', 'Provisioned GB':'400', 'Used GB':'380', 'IP Address':'10.0.1.12', Datastore:'DS-B1', 'CPU Usage %':'55', 'Last Activity': daysAgoISO(1) },
    { Cluster:'Cluster-Prod-B', Host:'esx-b-01', Name:'cache-b-01', 'Power State':'Powered Off', 'CPUs':'4', 'Memory GB':'16', 'Provisioned GB':'80', 'Used GB':'40', 'IP Address':'10.0.1.13', Datastore:'DS-B2', 'Power Off Date': daysAgoISO(15) },
    { Cluster:'Cluster-DR', Host:'esx-dr-01', Name:'dr-standby-01', 'Power State':'Powered Off', 'CPUs':'8', 'Memory GB':'32', 'Provisioned GB':'300', 'Used GB':'150', 'IP Address':'10.0.2.11', Datastore:'DS-DR1', 'Power Off Date': daysAgoISO(200) },
    { Cluster:'Cluster-DR', Host:'esx-dr-02', Name:'dr-standby-02', 'Power State':'Powered Off', 'CPUs':'8', 'Memory GB':'32', 'Provisioned GB':'300', 'Used GB':'140', 'IP Address':'10.0.2.12', Datastore:'DS-DR1', 'Power Off Date': daysAgoISO(200) }
  ];

  appState.reportTimestamp = new Date().toISOString();
  rebuildFromRaw();
  refreshAll();
}

function showValidationBanner(errors, warnings) {
  const banner = $('#validationBanner');
  if (!errors.length && !warnings.length) { banner.classList.remove('show', 'error', 'warn'); banner.innerHTML = ''; return; }
  banner.classList.add('show');
  banner.classList.toggle('error', errors.length > 0);
  banner.classList.toggle('warn', errors.length === 0 && warnings.length > 0);
  let html = '';
  if (errors.length) html += `<strong>${errors.length} lỗi dữ liệu:</strong><ul>${errors.slice(0,8).map(e => `<li>${esc(e)}</li>`).join('')}</ul>`;
  if (warnings.length) html += `<strong>${warnings.length} cảnh báo:</strong><ul>${warnings.slice(0,8).map(w => `<li>${esc(w)}</li>`).join('')}</ul>`;
  banner.innerHTML = html;
}

function rebuildFromRaw() {
  appState.policy.idleDays = Math.max(1, cleanNum($('#idleThresholdInput').value) || 30);
  appState.policy.cpuOvercommitMax = Math.max(0.1, cleanNum($('#cpuOvercommitInput').value) || 6);
  appState.policy.ramOvercommitMax = Math.max(0.1, cleanNum($('#ramOvercommitInput').value) || 1.5);
  appState.policy.htFactor = Math.max(1, cleanNum($('#htFactorInput').value) || 2);
  // No raw rows staged (data came from a live vCenter sync or was already
  // loaded from the server via /api/data/latest) — nothing to re-parse.
  // Keep the already-normalized appState.hosts/vms as-is; only the policy
  // thresholds above changed, and every render function reads those live.
  if (!appState.hostsRaw.length && !appState.vmsRaw.length) return;
  const { hosts, vms, errors, warnings } = normalizeAndValidate(appState.hostsRaw, appState.vmsRaw);
  appState.hosts = hosts;
  appState.vms = vms;
  appState.validation = { errors, warnings };
  showValidationBanner(errors, warnings);
}

async function loadFileInput(input, target) {
  const file = input.files && input.files[0];
  if (!file) return;
  try {
    const text = await readFile(file);
    const rows = parseInputText(text, file.name);
    appState[target] = rows;
    appState.reportTimestamp = new Date().toISOString();
    rebuildFromRaw();
    refreshAll();
    if (typeof persistImportIfReady === 'function') persistImportIfReady();
  } catch (err) {
    showValidationBanner([`Không thể đọc file "${file.name}": ${err.message}`], []);
  }
}

/* ---------- Export CSV ---------- */
function csvEscape(v) {
  const s = String(v ?? '');
  if (/[",\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function downloadBlob(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportCsv() {
  const hostRows = buildHostRows();
  const vmRows = getFilteredVms();
  const compliance = buildComplianceFindings();

  const sections = [];

  sections.push('=== HOSTS ===');
  sections.push(['Cluster','Host','CPU Cores','RAM GB','vCPU Used','CPU % (overcommit)','RAM Used GB','RAM % (overcommit)','VM Count','Status'].join(','));
  hostRows.forEach(h => sections.push([h.cluster,h.host,h.cores,h.ramGB,h.vcpuUsed,fmtNum(h.cpuPct),h.ramUsedGB.toFixed(1),fmtNum(h.ramPct),h.vmCount,h.overall.text].map(csvEscape).join(',')));

  sections.push('');
  sections.push('=== VMS ===');
  sections.push(['Cluster','Host','VM','Power State','IP','vCPU','RAM GB','Disk Prov GB','Disk Used GB','Idle Days','Power Off Days'].join(','));
  vmRows.forEach(v => sections.push([v.cluster,v.host,v.vm,v.powerState,v.ipAddress,v.vcpu,v.ramGB,v.diskProvGB.toFixed(1),v.diskUsedGB.toFixed(1),v.lastActivityDays ?? '',v.powerOffDays ?? ''].map(csvEscape).join(',')));

  sections.push('');
  sections.push('=== COMPLIANCE FINDINGS ===');
  sections.push(['Severity','Type','Entity','Cluster','Metric','Recommendation'].join(','));
  compliance.forEach(f => sections.push([f.severity,f.typeLabel,f.entity,f.cluster,f.metric,f.recommendation].map(csvEscape).join(',')));

  downloadBlob(`vmware-capacity-export-${Date.now()}.csv`, sections.join('\n'), 'text/csv;charset=utf-8');
}

/* ---------- Export HTML snapshot report ---------- */
function exportHtmlReport() {
  const clone = document.documentElement.cloneNode(true);
  const stateNode = clone.querySelector('#exported-state');
  const state = {
    hostsRaw: appState.hostsRaw, vmsRaw: appState.vmsRaw,
    reportTimestamp: appState.reportTimestamp, policy: appState.policy,
    filters: appState.filters, view: appState.view
  };
  stateNode.textContent = JSON.stringify(state).replace(/</g, '\\u003c');
  ['#hostFile','#vmFile'].forEach(sel => { const el = clone.querySelector(sel); if (el) el.removeAttribute('required'); });
  downloadBlob(`vmware-capacity-report-${Date.now()}.html`, '<!DOCTYPE html>\n' + clone.outerHTML, 'text/html;charset=utf-8');
}

function hydrateExportedState() {
  const node = $('#exported-state');
  if (!node || !node.textContent.trim()) return;
  try {
    const s = JSON.parse(node.textContent);
    appState.hostsRaw = s.hostsRaw || [];
    appState.vmsRaw = s.vmsRaw || [];
    appState.reportTimestamp = s.reportTimestamp || null;
    if (s.policy) appState.policy = { ...appState.policy, ...s.policy };
    if (s.filters) appState.filters = { ...appState.filters, ...s.filters };
    if (s.view) appState.view = s.view;
    if (appState.hostsRaw.length || appState.vmsRaw.length) {
      rebuildFromRaw();
      refreshAll();
    }
  } catch (err) { console.error('Failed to hydrate exported state', err); }
}
/* =========================================================
   Filters, view switching, bindings, refreshAll, init
   ========================================================= */

function populateFilterSelects() {
  const clusterSel = $('#filterCluster');
  const hostSel = $('#filterHost');
  const clusters = getClusterList();

  const prevCluster = appState.filters.cluster;
  clusterSel.innerHTML = `<option value="all">Tất cả cluster</option>` + clusters.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  appState.filters.cluster = clusters.includes(prevCluster) ? prevCluster : 'all';
  clusterSel.value = appState.filters.cluster;

  const hosts = getHostList(appState.filters.cluster);
  const prevHost = appState.filters.host;
  hostSel.innerHTML = `<option value="all">Tất cả host</option>` + hosts.map(h => `<option value="${esc(h)}">${esc(h)}</option>`).join('');
  appState.filters.host = hosts.includes(prevHost) ? prevHost : 'all';
  hostSel.value = appState.filters.host;

  $('#dataFootprint').textContent = `Hosts: ${appState.hosts.length} | VMs: ${appState.vms.length}`;
}

function syncViewSections() {
  $$('.view-section').forEach(sec => sec.classList.add('hidden'));
  const map = { dashboard:'#viewDashboard', compliance:'#viewCompliance', inventory:'#viewInventory', whatif:'#viewWhatif' };
  const target = $(map[appState.view] || map.dashboard);
  if (target) target.classList.remove('hidden');
  $$('#viewTabs .tab-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.view === appState.view));
}

function renderActiveView() {
  if (appState.view === 'dashboard') { renderCapacityKpis(); renderDashboardCharts(); renderClusterTable(); bindSortableHeaders('clusterTable','clusterTable',renderClusterTable); highlightSortHeaders('clusterTable','clusterTable'); }
  else if (appState.view === 'compliance') renderComplianceView();
  else if (appState.view === 'inventory') renderInventoryView();
  else if (appState.view === 'whatif') renderWhatifView();
}

function refreshAll() {
  populateFilterSelects();
  syncViewSections();
  renderActiveView();
}

function bindGlobalInteractions() {
  $('#hostFile').addEventListener('change', e => loadFileInput(e.target, 'hostsRaw'));
  $('#vmFile').addEventListener('change', e => loadFileInput(e.target, 'vmsRaw'));
  $('#btnLoadSample').addEventListener('click', loadSampleData);
  $('#btnExportCsv').addEventListener('click', exportCsv);
  $('#btnExportReport').addEventListener('click', exportHtmlReport);

  $('#btnResetAll').addEventListener('click', () => {
    appState.hostsRaw = []; appState.vmsRaw = []; appState.hosts = []; appState.vms = [];
    appState.reportTimestamp = null; appState.filters = { cluster:'all', host:'all' };
    appState.simHistory = []; showValidationBanner([], []);
    refreshAll();
  });

  $('#idleThresholdInput').addEventListener('change', () => { rebuildFromRaw(); refreshAll(); });
  $('#cpuOvercommitInput').addEventListener('change', () => { rebuildFromRaw(); refreshAll(); });
  $('#ramOvercommitInput').addEventListener('change', () => { rebuildFromRaw(); refreshAll(); });
  $('#htFactorInput').addEventListener('change', () => { rebuildFromRaw(); refreshAll(); });

  $('#filterCluster').addEventListener('change', e => {
    appState.filters.cluster = e.target.value;
    appState.filters.host = 'all';
    refreshAll();
  });
  $('#filterHost').addEventListener('change', e => { appState.filters.host = e.target.value; refreshAll(); });

  $('#themeToggle').addEventListener('click', () => {
    const html = document.documentElement;
    html.setAttribute('data-theme', html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
    if (appState.view === 'dashboard') renderDashboardCharts();
  });

  $$('#viewTabs .tab-btn').forEach(btn => btn.addEventListener('click', () => {
    appState.view = btn.dataset.view;
    syncViewSections();
    renderActiveView();
  }));

  $('#complianceSearch').addEventListener('input', e => { appState.compliance.search = e.target.value; renderComplianceTable(); renderComplianceKpis(); });
  $('#complianceSeverityFilter').addEventListener('change', e => { appState.compliance.severity = e.target.value; renderComplianceTable(); });
  $('#complianceTypeFilter').addEventListener('change', e => { appState.compliance.type = e.target.value; renderComplianceTable(); });

  $('#hostSearch').addEventListener('input', e => { appState.hostTableSearch = e.target.value; renderHostTable(); });
  $('#vmSearch').addEventListener('input', e => { appState.vmTableSearch = e.target.value; renderVmTable(); });
  $('#vmStatusFilter').addEventListener('change', e => { appState.vmStatusFilter = e.target.value; renderVmTable(); });

  $('#whatifClusterSelect').addEventListener('change', e => { appState.whatif.cluster = e.target.value; renderSimForm(); });
  $$('#simTabs .sim-tab').forEach(btn => btn.addEventListener('click', () => {
    appState.whatif.tab = btn.dataset.sim;
    $$('#simTabs .sim-tab').forEach(b => b.classList.toggle('active', b === btn));
    $('#simResultArea').classList.add('hidden');
    renderSimForm();
  }));
  $('#btnClearSimHistory').addEventListener('click', () => { appState.simHistory = []; renderSimHistory(); });
}
