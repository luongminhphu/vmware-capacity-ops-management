"""Physical-host sizing from a list of requested VMs.

Input: the VM list a Dev team hands over for a new application/system
(name, quantity, vCPU, RAM GB, Disk GB), a set of sizing assumptions
(target overcommit, headroom, growth buffer, N+x redundancy, hypervisor
reserve) and one or more candidate host configurations.

Output, per candidate host configuration: how many hosts are needed, which
resource is binding (CPU or RAM), the resulting overcommit / utilisation,
behaviour when `redundancy` hosts are lost, aggregate hardware totals and —
when unit prices are given — an estimated budget. Storage is sized as a SAN
capacity requirement by default (shared Pure/NetApp arrays), optionally as
local/vSAN disk per host.

All math is pure Python so it can be reused by the JSON API, the Excel export
and a future PDF report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Column aliases accepted when importing an Excel/CSV VM list (lower-case,
# matched as "contains" after stripping spaces / diacritics-insensitive words).
VM_COLUMN_ALIASES: Dict[str, List[str]] = {
    "name": ["ten vm", "tên vm", "ten may chu", "tên máy chủ", "vm name", "vmname", "name", "server", "may chu", "máy chủ", "vm", "hostname", "ten", "tên"],
    "qty": ["so luong", "số lượng", "sl", "qty", "quantity", "count", "so may", "số máy"],
    "vcpu": ["vcpu", "cpu", "core", "cores", "so cpu", "số cpu"],
    "ram_gb": ["ram", "memory", "mem", "bo nho", "bộ nhớ"],
    "disk_gb": ["disk", "storage", "o cung", "ổ cứng", "dung luong", "dung lượng", "hdd", "ssd", "luu tru", "lưu trữ"],
    "note": ["ghi chu", "ghi chú", "note", "notes", "mo ta", "mô tả", "description", "role", "vai tro", "vai trò", "os", "he dieu hanh"],
}


@dataclass
class VmSpec:
    name: str
    qty: int = 1
    vcpu: float = 0
    ram_gb: float = 0
    disk_gb: float = 0
    note: str = ""


@dataclass
class HostProfile:
    name: str
    sockets: int = 2
    cores_per_socket: int = 16
    ram_gb: float = 512
    disk_gb: float = 0          # local disk per host (only used when storage_mode == 'local')
    unit_price: Optional[float] = None  # optional, any currency; used for budget estimate

    @property
    def cores(self) -> int:
        return int(self.sockets * self.cores_per_socket)


@dataclass
class SizingParams:
    cpu_ratio: float = 3.0           # target vCPU : pCPU-logic (consistent with the app's overcommit metric)
    ht_factor: float = 2.0           # logical threads per physical core
    ram_ratio: float = 1.0           # target vRAM : pRAM (1.0 = no RAM overcommit)
    max_util_pct: float = 80.0       # headroom: plan to use at most this % of host capacity
    growth_pct: float = 20.0         # growth buffer applied to the Dev team's request
    redundancy: int = 1              # N + redundancy
    min_hosts: int = 2
    reserve_cores: float = 2.0       # physical cores reserved for ESXi per host
    reserve_ram_gb: float = 16.0     # RAM reserved for ESXi per host
    storage_mode: str = "san"        # 'san' | 'local'
    storage_overhead_pct: float = 25.0  # snapshots, swap, vmdk growth, free-space floor
    storage_protection_factor: float = 1.0  # local/vSAN only: FTT=1 mirror → 2.0, RAID-5 → 1.33 ...
    environment: str = ""            # free text shown on the report, e.g. "Core Banking phase 2"


def _ceil_pos(x: float) -> int:
    if not math.isfinite(x) or x <= 0:
        return 0
    return int(math.ceil(x - 1e-9))


def _pct(num: float, den: float) -> Optional[float]:
    if den <= 0 or not math.isfinite(num) or not math.isfinite(den):
        return None
    return num / den * 100.0


def summarize_demand(vms: List[VmSpec], params: SizingParams) -> Dict[str, Any]:
    g = 1.0 + max(0.0, params.growth_pct) / 100.0
    count = sum(max(0, int(v.qty)) for v in vms)
    vcpu = sum(max(0, int(v.qty)) * max(0.0, float(v.vcpu)) for v in vms)
    ram = sum(max(0, int(v.qty)) * max(0.0, float(v.ram_gb)) for v in vms)
    disk = sum(max(0, int(v.qty)) * max(0.0, float(v.disk_gb)) for v in vms)
    so = 1.0 + max(0.0, params.storage_overhead_pct) / 100.0
    prot = params.storage_protection_factor if params.storage_mode == "local" else 1.0
    prot = max(1.0, prot or 1.0)
    return {
        "vmCount": count,
        "vcpu": vcpu, "ramGB": ram, "diskGB": disk,
        "vcpuPlanned": vcpu * g, "ramPlannedGB": ram * g, "diskPlannedGB": disk * g,
        # what the hypervisor must actually provide once overcommit targets apply
        "logicalCpuNeeded": (vcpu * g) / max(0.1, params.cpu_ratio),
        "physRamNeededGB": (ram * g) / max(0.1, params.ram_ratio),
        "storageNeededGB": disk * g * so * prot,
        "storageOverheadFactor": so, "storageProtectionFactor": prot,
        "growthFactor": g,
        "avgVcpu": (vcpu / count) if count else 0.0,
        "avgRamGB": (ram / count) if count else 0.0,
        "avgDiskGB": (disk / count) if count else 0.0,
    }


def size_profile(profile: HostProfile, demand: Dict[str, Any], params: SizingParams) -> Dict[str, Any]:
    util = max(0.05, min(1.0, params.max_util_pct / 100.0))
    usable_cores = max(0.0, profile.cores - params.reserve_cores)
    usable_ram = max(0.0, profile.ram_gb - params.reserve_ram_gb)
    logical_per_host = usable_cores * max(1.0, params.ht_factor)

    # Capacity one host offers at target overcommit and headroom.
    vcpu_cap_host = logical_per_host * params.cpu_ratio * util
    vram_cap_host = usable_ram * params.ram_ratio * util

    n_cpu = _ceil_pos(demand["vcpuPlanned"] / vcpu_cap_host) if vcpu_cap_host > 0 else 0
    n_ram = _ceil_pos(demand["ramPlannedGB"] / vram_cap_host) if vram_cap_host > 0 else 0
    n_base = max(n_cpu, n_ram, 1 if demand["vmCount"] else 0)
    redundancy = max(0, int(params.redundancy))
    n_total = max(n_base + redundancy, int(params.min_hosts)) if n_base else 0
    if n_cpu == n_ram:
        binding = "CPU & RAM"
    else:
        binding = "CPU" if n_cpu > n_ram else "RAM"

    total_cores = profile.cores * n_total
    total_ram = profile.ram_gb * n_total
    total_logical_usable = logical_per_host * n_total
    total_ram_usable = usable_ram * n_total

    # Resulting overcommit (vs. physical) and utilisation of the headroom-adjusted capacity.
    cpu_oc_after = (demand["vcpuPlanned"] / total_logical_usable) if total_logical_usable > 0 else None
    ram_oc_after = (demand["ramPlannedGB"] / total_ram_usable) if total_ram_usable > 0 else None
    cpu_util_pct = _pct(demand["vcpuPlanned"], vcpu_cap_host * n_total / util) if n_total else None
    ram_util_pct = _pct(demand["ramPlannedGB"], vram_cap_host * n_total / util) if n_total else None

    # Failure case: lose `redundancy` hosts — can the remainder still carry the load within 100% of target capacity?
    n_fail = max(0, n_total - redundancy)
    cpu_fail_pct = _pct(demand["vcpuPlanned"], vcpu_cap_host * n_fail / util) if n_fail else None
    ram_fail_pct = _pct(demand["ramPlannedGB"], vram_cap_host * n_fail / util) if n_fail else None
    fail_ok = bool(n_fail) and (cpu_fail_pct or 0) <= 100.0 + 1e-6 and (ram_fail_pct or 0) <= 100.0 + 1e-6

    # Spare capacity after N+x (in average-VM units) – useful "how many more VMs fit" number.
    spare_vcpu = max(0.0, vcpu_cap_host * n_fail - demand["vcpuPlanned"])
    spare_ram = max(0.0, vram_cap_host * n_fail - demand["ramPlannedGB"])
    avg_vcpu = demand["avgVcpu"] or 0
    avg_ram = demand["avgRamGB"] or 0
    fit_cpu = int(spare_vcpu // avg_vcpu) if avg_vcpu > 0 else None
    fit_ram = int(spare_ram // avg_ram) if avg_ram > 0 else None
    fits = [x for x in (fit_cpu, fit_ram) if x is not None]
    extra_vms = min(fits) if fits else None

    # Storage
    storage: Dict[str, Any] = {"mode": params.storage_mode, "neededGB": demand["storageNeededGB"]}
    if params.storage_mode == "local":
        per_host = (demand["storageNeededGB"] / n_total) if n_total else 0.0
        storage.update({
            "perHostNeededGB": per_host,
            "perHostConfiguredGB": profile.disk_gb,
            "ok": (profile.disk_gb or 0) >= per_host - 1e-6 if n_total else None,
            "totalConfiguredGB": (profile.disk_gb or 0) * n_total,
        })

    budget = (profile.unit_price * n_total) if (profile.unit_price is not None and n_total) else None

    # Efficiency: how much of the purchased (headroom-adjusted, post-failure) capacity the request actually uses.
    eff_cpu = _pct(demand["vcpuPlanned"], vcpu_cap_host * n_fail) if n_fail else None
    eff_ram = _pct(demand["ramPlannedGB"], vram_cap_host * n_fail) if n_fail else None

    return {
        "profile": {
            "name": profile.name, "sockets": profile.sockets, "coresPerSocket": profile.cores_per_socket,
            "cores": profile.cores, "ramGB": profile.ram_gb, "diskGB": profile.disk_gb, "unitPrice": profile.unit_price,
        },
        "perHost": {
            "usableCores": usable_cores, "logicalCpu": logical_per_host, "usableRamGB": usable_ram,
            "vcpuCapacity": vcpu_cap_host, "vramCapacityGB": vram_cap_host,
        },
        "hostsForCpu": n_cpu, "hostsForRam": n_ram, "hostsBase": n_base, "redundancy": redundancy,
        "hostsTotal": n_total, "binding": binding,
        "totals": {"cores": total_cores, "ramGB": total_ram, "logicalCpu": profile.cores * max(1.0, params.ht_factor) * n_total},
        "after": {
            "cpuOvercommit": cpu_oc_after, "ramOvercommit": ram_oc_after,
            "cpuUtilPct": cpu_util_pct, "ramUtilPct": ram_util_pct,
        },
        "failure": {"hostsLeft": n_fail, "cpuPct": cpu_fail_pct, "ramPct": ram_fail_pct, "ok": fail_ok},
        "spare": {"vcpu": spare_vcpu, "ramGB": spare_ram, "extraAvgVms": extra_vms},
        "efficiency": {"cpuPct": eff_cpu, "ramPct": eff_ram},
        "storage": storage,
        "budget": budget,
    }


def _rank_key(r: Dict[str, Any]):
    # Prefer: fewest hosts → lowest budget (if any) → highest efficiency of the binding resource.
    eff = r["efficiency"]
    eff_bind = eff["cpuPct"] if r["binding"].startswith("CPU") else eff["ramPct"]
    return (
        r["hostsTotal"] if r["hostsTotal"] else 10**9,
        r["budget"] if r["budget"] is not None else float("inf"),
        -(eff_bind or 0),
    )


def build_sizing(vms: List[VmSpec], params: SizingParams, profiles: List[HostProfile]) -> Dict[str, Any]:
    demand = summarize_demand(vms, params)
    results = [size_profile(p, demand, params) for p in profiles]
    ranked = sorted(range(len(results)), key=lambda i: _rank_key(results[i]))
    best_idx = ranked[0] if ranked and demand["vmCount"] else None
    for i, r in enumerate(results):
        r["recommended"] = (i == best_idx)
        r["rank"] = ranked.index(i) + 1 if ranked else None

    recommendation = _recommendation_text(demand, params, results[best_idx] if best_idx is not None else None)
    return {
        "demand": demand,
        "params": params.__dict__,
        "results": results,
        "recommendedIndex": best_idx,
        "recommendation": recommendation,
        "assumptions": assumption_lines(params),
    }


def assumption_lines(p: SizingParams) -> List[str]:
    lines = [
        f"Overcommit CPU mục tiêu {p.cpu_ratio:g}x trên pCPU logic (HT ×{p.ht_factor:g}); RAM {p.ram_ratio:g}x.",
        f"Chỉ dùng tối đa {p.max_util_pct:g}% năng lực mỗi host (headroom {100 - p.max_util_pct:g}%).",
        f"Dự phòng tăng trưởng +{p.growth_pct:g}% trên yêu cầu của team Dev.",
        f"Dự phòng hỏng hóc N+{p.redundancy}; tối thiểu {p.min_hosts} host/cluster.",
        f"ESXi chiếm {p.reserve_cores:g} core và {p.reserve_ram_gb:g} GB RAM mỗi host.",
    ]
    if p.storage_mode == "local":
        lines.append(f"Lưu trữ cục bộ/vSAN: overhead {p.storage_overhead_pct:g}%, hệ số bảo vệ ×{p.storage_protection_factor:g}.")
    else:
        lines.append(f"Lưu trữ trên SAN dùng chung: dung lượng cấp phát + {p.storage_overhead_pct:g}% overhead (snapshot, swap, tăng trưởng vmdk).")
    return lines


def _fmt_int(v: float) -> str:
    return f"{int(round(v)):,}"


def _recommendation_text(demand: Dict[str, Any], params: SizingParams, best: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not best or not demand["vmCount"]:
        return {"headline": "Chưa có VM nào trong danh sách yêu cầu.", "detail": ""}
    p = best["profile"]
    tb = f"{_fmt_int(demand['storageNeededGB'] / 1024)} TB" if demand["storageNeededGB"] >= 1024 else f"{_fmt_int(demand['storageNeededGB'])} GB"
    headline = (
        f"Đề xuất {best['hostsTotal']} host {p['name']} ({p['cores']} cores · {_fmt_int(p['ramGB'])} GB RAM mỗi host) "
        f"cho {demand['vmCount']} VM "
        f"({_fmt_int(demand['vcpu'])} vCPU · {_fmt_int(demand['ramGB'])} GB RAM · {_fmt_int(demand['diskGB'])} GB disk)."
    )
    fail = best["failure"]
    fail_txt = (
        f"Mất {best['redundancy']} host vẫn chạy được ({fail['cpuPct']:.0f}% CPU · {fail['ramPct']:.0f}% RAM năng lực còn lại)."
        if fail["ok"] and fail["cpuPct"] is not None else
        "Chưa đạt dự phòng N+%d — cần thêm host hoặc hạ overcommit mục tiêu." % best["redundancy"]
    )
    store_txt = (
        f"Cần cấp {tb} trên SAN." if params.storage_mode == "san"
        else f"Cần {tb} lưu trữ cục bộ (~{_fmt_int(best['storage'].get('perHostNeededGB', 0))} GB/host)."
    )
    detail = (
        f"Ràng buộc chính: {best['binding']} (cần {best['hostsForCpu']} host theo CPU, {best['hostsForRam']} host theo RAM, "
        f"+{best['redundancy']} dự phòng). Sau khi lắp: overcommit CPU {best['after']['cpuOvercommit']:.2f}x, "
        f"RAM {best['after']['ramOvercommit']:.2f}x. {fail_txt} {store_txt}"
    )
    extra = best["spare"].get("extraAvgVms")
    if extra is not None:
        detail += f" Dư địa sau N+{best['redundancy']}: thêm được ~{extra} VM cỡ trung bình của dự án."
    return {"headline": headline, "detail": detail}


# ---------------------------------------------------------------------------
# Excel / CSV import of the Dev team's VM list
# ---------------------------------------------------------------------------
def _norm_header(s: Any) -> str:
    import unicodedata
    text = unicodedata.normalize("NFD", str(s or "").strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def map_columns(headers: List[Any]) -> Dict[str, int]:
    """Return {field: column index} using the alias table. Exact match wins,
    then 'contains'. Each column is assigned once."""
    norm = [_norm_header(h) for h in headers]
    mapping: Dict[str, int] = {}
    used: set = set()
    for field_name, aliases in VM_COLUMN_ALIASES.items():
        for alias in aliases:
            a = _norm_header(alias)
            for idx, h in enumerate(norm):
                if idx in used or not h:
                    continue
                if h == a or h.startswith(a + " ") or h.startswith(a + "(") or h == a.replace(" ", ""):
                    mapping[field_name] = idx
                    used.add(idx)
                    break
            if field_name in mapping:
                break
    # second pass: "contains" for anything still missing
    for field_name, aliases in VM_COLUMN_ALIASES.items():
        if field_name in mapping:
            continue
        for alias in aliases:
            a = _norm_header(alias)
            if len(a) < 3:
                continue
            for idx, h in enumerate(norm):
                if idx in used or not h:
                    continue
                if a in h:
                    mapping[field_name] = idx
                    used.add(idx)
                    break
            if field_name in mapping:
                break
    return mapping


def _to_num(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace(",", "")
    mult = 1.0
    if s.endswith("tb"):
        mult, s = 1024.0, s[:-2]
    elif s.endswith("gb"):
        s = s[:-2]
    elif s.endswith("mb"):
        mult, s = 1 / 1024.0, s[:-2]
    s = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    try:
        return float(s) * mult if s else 0.0
    except ValueError:
        return 0.0


def rows_to_vmspecs(table: List[List[Any]]) -> Dict[str, Any]:
    """`table` = list of rows (first non-empty row = header). Returns {rows, warnings, mapping}."""
    warnings: List[str] = []
    rows_out: List[Dict[str, Any]] = []
    # find header row: first row with ≥2 non-empty cells
    header_idx = None
    for i, row in enumerate(table[:20]):
        if sum(1 for c in row if str(c or "").strip()) >= 2:
            header_idx = i
            break
    if header_idx is None:
        return {"rows": [], "warnings": ["Không tìm thấy dòng tiêu đề trong file."], "mapping": {}}
    headers = table[header_idx]
    mapping = map_columns(headers)
    missing = [f for f in ("vcpu", "ram_gb") if f not in mapping]
    if missing:
        warnings.append(
            "Không nhận diện được cột " + ", ".join({"vcpu": "vCPU", "ram_gb": "RAM"}[m] for m in missing)
            + f". Tiêu đề đọc được: {', '.join(str(h) for h in headers if str(h or '').strip())}."
        )
        return {"rows": [], "warnings": warnings, "mapping": mapping}

    def cell(row, key, default=None):
        idx = mapping.get(key)
        if idx is None or idx >= len(row):
            return default
        return row[idx]

    for n, row in enumerate(table[header_idx + 1:], start=header_idx + 2):
        if not any(str(c or "").strip() for c in row):
            continue
        name = str(cell(row, "name", "") or "").strip()
        vcpu = _to_num(cell(row, "vcpu"))
        ram = _to_num(cell(row, "ram_gb"))
        disk = _to_num(cell(row, "disk_gb"))
        qty_raw = cell(row, "qty")
        qty = int(_to_num(qty_raw)) if qty_raw not in (None, "") else 1
        if qty <= 0:
            qty = 1
        if vcpu <= 0 and ram <= 0:
            if name:
                warnings.append(f"Dòng {n} ({name}): thiếu vCPU/RAM — bỏ qua.")
            continue
        if ram and ram < 1 and "mb" not in str(cell(row, "ram_gb", "")).lower():
            warnings.append(f"Dòng {n} ({name or 'VM'}): RAM {ram:g} GB có vẻ bất thường.")
        rows_out.append({
            "name": name or f"VM-{len(rows_out) + 1:02d}",
            "qty": qty, "vcpu": vcpu, "ramGB": ram, "diskGB": disk,
            "note": str(cell(row, "note", "") or "").strip(),
        })
    if not rows_out:
        warnings.append("File không có dòng VM hợp lệ.")
    return {"rows": rows_out, "warnings": warnings, "mapping": mapping}


def parse_vm_file(filename: str, content: bytes) -> Dict[str, Any]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        best: Optional[Dict[str, Any]] = None
        for ws in wb.worksheets:
            table = [list(r) for r in ws.iter_rows(values_only=True)]
            parsed = rows_to_vmspecs(table)
            parsed["sheet"] = ws.title
            if parsed["rows"] and (best is None or len(parsed["rows"]) > len(best["rows"])):
                best = parsed
            if best is None:
                best = parsed
        return best or {"rows": [], "warnings": ["File Excel không có sheet nào."], "mapping": {}}
    if name.endswith(".xls"):
        return {"rows": [], "warnings": ["Định dạng .xls cũ chưa hỗ trợ — hãy lưu lại thành .xlsx."], "mapping": {}}
    # CSV / TSV / TXT
    import csv
    import io
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    table = [row for row in csv.reader(io.StringIO(text), dialect)]
    return rows_to_vmspecs(table)


# ---------------------------------------------------------------------------
# Default candidate host profiles (typical 2-socket rack servers)
# ---------------------------------------------------------------------------
DEFAULT_PROFILES: List[Dict[str, Any]] = [
    {"name": "2×16C / 512 GB", "sockets": 2, "coresPerSocket": 16, "ramGB": 512, "diskGB": 0, "unitPrice": None},
    {"name": "2×24C / 768 GB", "sockets": 2, "coresPerSocket": 24, "ramGB": 768, "diskGB": 0, "unitPrice": None},
    {"name": "2×32C / 1 TB", "sockets": 2, "coresPerSocket": 32, "ramGB": 1024, "diskGB": 0, "unitPrice": None},
]

TEMPLATE_ROWS: List[Dict[str, Any]] = [
    {"name": "APP-WEB", "qty": 4, "vcpu": 4, "ramGB": 16, "diskGB": 100, "note": "Web/API tier, Ubuntu 22.04"},
    {"name": "APP-SVC", "qty": 6, "vcpu": 8, "ramGB": 32, "diskGB": 200, "note": "Service tier"},
    {"name": "DB-PRIMARY", "qty": 2, "vcpu": 16, "ramGB": 128, "diskGB": 2048, "note": "PostgreSQL primary/standby"},
    {"name": "CACHE", "qty": 3, "vcpu": 4, "ramGB": 64, "diskGB": 100, "note": "Redis cluster"},
    {"name": "MQ", "qty": 3, "vcpu": 4, "ramGB": 16, "diskGB": 500, "note": "Kafka"},
    {"name": "MGMT", "qty": 2, "vcpu": 2, "ramGB": 8, "diskGB": 100, "note": "Bastion, monitoring agent"},
]
