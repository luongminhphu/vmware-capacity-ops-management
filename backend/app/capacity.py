"""
Python port of the client-side capacity model (see frontend/js/core.js /
buildCapacityModel, buildHostRows, buildClusterRows in the original single-file
app). Kept numerically identical on purpose so backend-persisted trend data and
frontend on-the-fly recalculation always agree.

Row shapes use the SAME camelCase field names as the frontend's appState.hosts /
appState.vms so JSON returned by the API can be dropped straight into the
existing frontend rendering functions with zero translation layer.
"""
import math
from typing import Any, Dict, List, Optional


class Policy:
    def __init__(self, cpu_overcommit_max=6.0, ram_overcommit_max=1.0, ht_factor=2.0, idle_days=30,
                 include_storage=False):
        self.cpu_overcommit_max = cpu_overcommit_max
        self.ram_overcommit_max = ram_overcommit_max
        self.ht_factor = ht_factor
        self.idle_days = idle_days
        # Whether Storage (provisioned/used % per datastore & cluster) is part
        # of the capacity assessment. Defaults to False because the reference
        # environment runs on shared SAN storage where thick-provisioned VMs
        # make "used / provisioned" sit near 100% regardless of real SAN
        # headroom -- a misleading signal for investment decisions. When
        # False, dashboards and PDF reports omit every storage KPI/chart/table
        # and state explicitly that Storage is out of scope.
        self.include_storage = bool(include_storage)


def parse_bool(value, default: bool = False) -> bool:
    """Lenient bool parser for env vars / app_settings string values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _finite(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


def get_status_for_pct(pct: Optional[float], warn_t: float, high_t: float) -> Dict[str, str]:
    if not _finite(pct):
        return {"text": "NA", "cls": "na"}
    if pct > 100:
        return {"text": "VƯỢT NGƯỠNG", "cls": "crit"}
    if pct >= high_t:
        return {"text": "CẢNH BÁO", "cls": "bad"}
    if pct >= warn_t:
        return {"text": "THEO DÕI", "cls": "warn"}
    return {"text": "AN TOÀN", "cls": "ok"}


def combine_status(*statuses: Dict[str, str]) -> Dict[str, str]:
    order = ["crit", "bad", "warn", "ok", "na"]
    labels = {"crit": "VƯỢT NGƯỠNG", "bad": "CẢNH BÁO", "warn": "THEO DÕI", "ok": "AN TOÀN", "na": "NA"}
    cls_list = [s["cls"] for s in statuses]
    for level in order:
        if level in cls_list:
            if level == "na" and any(c != "na" for c in cls_list):
                continue
            return {"text": labels[level], "cls": level}
    return {"text": "NA", "cls": "na"}


def build_capacity_model(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: Policy) -> Dict[str, Any]:
    powered_on = [v for v in vms if v.get("isPoweredOn")]

    total_cores = sum(h.get("cores", 0) or 0 for h in hosts)
    total_ram_gb = sum(h.get("ramGB", 0) or 0 for h in hosts)
    used_vcpu = sum(v.get("vcpu", 0) or 0 for v in powered_on)
    used_ram_gb = sum(v.get("ramGB", 0) or 0 for v in powered_on)
    prov_storage_gb = sum(v.get("diskProvGB", 0) or 0 for v in vms)
    used_storage_gb = sum(v.get("diskUsedGB", 0) or 0 for v in vms)

    storage_pct = (used_storage_gb / prov_storage_gb * 100) if prov_storage_gb > 0 else float("nan")

    ht_factor = max(1.0, policy.ht_factor or 2.0)
    total_logical_cores = total_cores * ht_factor
    cpu_overcommit = (used_vcpu / total_logical_cores) if total_logical_cores > 0 else float("nan")
    ram_overcommit = (used_ram_gb / total_ram_gb) if total_ram_gb > 0 else float("nan")

    cpu_oc_max = policy.cpu_overcommit_max
    ram_oc_max = policy.ram_overcommit_max
    cpu_pct = (cpu_overcommit / cpu_oc_max * 100) if _finite(cpu_overcommit) and cpu_oc_max > 0 else float("nan")
    ram_pct = (ram_overcommit / ram_oc_max * 100) if _finite(ram_overcommit) and ram_oc_max > 0 else float("nan")

    return {
        "hostCount": len(hosts),
        "vmCount": len(vms),
        "poweredOnCount": len(powered_on),
        "poweredOffCount": len(vms) - len(powered_on),
        "totalCores": total_cores,
        "totalLogicalCores": total_logical_cores,
        "htFactor": ht_factor,
        "totalRamGB": total_ram_gb,
        "provStorageGB": prov_storage_gb,
        "usedStorageGB": used_storage_gb,
        "usedVcpu": used_vcpu,
        "usedRamGB": used_ram_gb,
        "freeCores": max(0.0, total_cores - used_vcpu),
        "freeLogicalCores": max(0.0, total_logical_cores - used_vcpu),
        "freeRamGB": max(0.0, total_ram_gb - used_ram_gb),
        "freeStorageGB": max(0.0, prov_storage_gb - used_storage_gb),
        "cpuPct": cpu_pct,
        "ramPct": ram_pct,
        "storagePct": storage_pct,
        "cpuOvercommit": cpu_overcommit,
        "ramOvercommit": ram_overcommit,
    }


def get_cluster_list(hosts, vms) -> List[str]:
    names = {h.get("cluster") for h in hosts} | {v.get("cluster") for v in vms}
    return sorted(n for n in names if n)


def build_cluster_rows(hosts, vms, policy: Policy) -> List[Dict[str, Any]]:
    rows = []
    for name in get_cluster_list(hosts, vms):
        c_hosts = [h for h in hosts if h.get("cluster") == name]
        c_vms = [v for v in vms if v.get("cluster") == name]
        model = build_capacity_model(c_hosts, c_vms, policy)
        cpu_status = get_status_for_pct(model["cpuPct"], 80, 100)
        ram_status = get_status_for_pct(model["ramPct"], 80, 100)
        overall = combine_status(cpu_status, ram_status)
        rows.append({
            "name": name, "hosts": len(c_hosts), "vms": len(c_vms),
            "model": model, "cpuStatus": cpu_status, "ramStatus": ram_status, "overall": overall,
        })
    return rows


def build_host_rows(hosts, vms, policy: Policy) -> List[Dict[str, Any]]:
    ht_factor = max(1.0, policy.ht_factor or 2.0)
    rows = []
    for h in hosts:
        vms_on_host = [v for v in vms if v.get("host") == h.get("host") and v.get("cluster") == h.get("cluster") and v.get("isPoweredOn")]
        vcpu_used = sum(v.get("vcpu", 0) or 0 for v in vms_on_host)
        ram_used_gb = sum(v.get("ramGB", 0) or 0 for v in vms_on_host)
        logical_cores = (h.get("cores", 0) or 0) * ht_factor
        cpu_overcommit = (vcpu_used / logical_cores) if logical_cores > 0 else float("nan")
        ram_overcommit = (ram_used_gb / h.get("ramGB", 0)) if h.get("ramGB", 0) else float("nan")
        cpu_pct = (cpu_overcommit / policy.cpu_overcommit_max * 100) if _finite(cpu_overcommit) and policy.cpu_overcommit_max > 0 else float("nan")
        ram_pct = (ram_overcommit / policy.ram_overcommit_max * 100) if _finite(ram_overcommit) and policy.ram_overcommit_max > 0 else float("nan")
        cpu_status = get_status_for_pct(cpu_pct, 80, 100)
        ram_status = get_status_for_pct(ram_pct, 80, 100)
        overall = combine_status(cpu_status, ram_status)
        row = dict(h)
        row.update({
            "vcpuUsed": vcpu_used, "ramUsedGB": ram_used_gb,
            "cpuOvercommit": cpu_overcommit, "ramOvercommit": ram_overcommit,
            "cpuPct": cpu_pct, "ramPct": ram_pct, "vmCount": len(vms_on_host),
            "cpuStatus": cpu_status, "ramStatus": ram_status, "overall": overall,
        })
        rows.append(row)
    return rows


def build_datastore_rows(vms, ds_warn=80.0, ds_high=90.0) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for v in vms:
        key = v.get("datastore") or "UNKNOWN"
        b = buckets.setdefault(key, {"name": key, "provGB": 0.0, "usedGB": 0.0, "vmCount": 0})
        b["provGB"] += v.get("diskProvGB", 0) or 0
        b["usedGB"] += v.get("diskUsedGB", 0) or 0
        b["vmCount"] += 1
    rows = []
    for b in buckets.values():
        pct = (b["usedGB"] / b["provGB"] * 100) if b["provGB"] > 0 else float("nan")
        rows.append({**b, "pct": pct, "status": get_status_for_pct(pct, ds_warn, ds_high)})
    rows.sort(key=lambda r: (r["pct"] if _finite(r["pct"]) else -1), reverse=True)
    return rows


FINDING_PREFIX = {
    "host_cpu": "HCPU", "host_ram": "HRAM", "overcommit_cpu": "CCPU", "overcommit_ram": "CRAM",
    "vm_idle": "IDLE", "vm_off": "POFF", "vm_overprovision": "OVP",
}


def finding_code(ftype: str, cluster: Any, entity: Any, vcenter_key: Any = "") -> str:
    """Stable reconciliation code ("mã đối soát") for a finding: type prefix +
    5 hex chars of FNV-1a(type|vcenterKey|cluster|entity). Deterministic, so
    the same host/VM issue carries the same code in the Executive PDF, the
    Technical PDF and the web Compliance tab (frontend/js/core.js has the same
    hash). vcenterKey disambiguates identical host/VM names across vCenters;
    cluster-level findings are aggregated by name and carry no vcenterKey."""
    h = 0x811C9DC5
    for b in f"{ftype}|{vcenter_key or ''}|{cluster or ''}|{entity or ''}".encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{FINDING_PREFIX.get(ftype, 'FND')}-{h:08X}"[:len(FINDING_PREFIX.get(ftype, 'FND')) + 6]


def assign_finding_codes(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for f in findings:
        f["code"] = finding_code(f.get("type", ""), f.get("cluster"), f.get("entity"), f.get("vcenterKey"))
    return findings


def build_compliance_findings(hosts, vms, policy: Policy) -> List[Dict[str, Any]]:
    """Python port of buildComplianceFindings() — five CPU/RAM checks only
    (storage checks intentionally dropped, shared SAN storage — see project
    knowledge: concepts/vmware-compliance-checks)."""
    findings: List[Dict[str, Any]] = []
    host_rows = build_host_rows(hosts, vms, policy)
    cluster_rows = build_cluster_rows(hosts, vms, policy)

    for h in host_rows:
        if _finite(h["cpuPct"]) and h["cpuPct"] >= 80:
            findings.append({
                "severity": "critical" if h["cpuPct"] > 100 else "warning",
                "type": "host_cpu", "typeLabel": "Host CPU vượt ngưỡng",
                "entity": h.get("host"), "cluster": h.get("cluster"), "vcenterKey": h.get("vcenterKey") or "",
                "metric": f"CPU overcommit {h['cpuOvercommit']:.2f}x (ngưỡng {policy.cpu_overcommit_max:.1f}x)",
                "recommendation": "Overcommit CPU đã vượt ngưỡng cấu hình — cần di dời VM hoặc bổ sung host ngay." if h["cpuPct"] > 100 else "Overcommit CPU đang tiến gần ngưỡng — cân nhắc vMotion VM sang host khác hoặc lên kế hoạch bổ sung năng lực CPU.",
            })
        if _finite(h["ramPct"]) and h["ramPct"] >= 80:
            findings.append({
                "severity": "critical" if h["ramPct"] > 100 else "warning",
                "type": "host_ram", "typeLabel": "Host RAM vượt ngưỡng",
                "entity": h.get("host"), "cluster": h.get("cluster"), "vcenterKey": h.get("vcenterKey") or "",
                "metric": f"RAM overcommit {h['ramOvercommit']:.2f}x (ngưỡng {policy.ram_overcommit_max:.1f}x)",
                "recommendation": "Overcommit RAM đã vượt ngưỡng cấu hình — rủi ro cao, cần hành động ngay." if h["ramPct"] > 100 else "Overcommit RAM đang tiến gần ngưỡng — theo dõi sát, cân nhắc bổ sung RAM hoặc cân bằng lại workload.",
            })

    for c in cluster_rows:
        cpu_oc = c["model"]["cpuOvercommit"]
        if _finite(cpu_oc) and cpu_oc > policy.cpu_overcommit_max:
            findings.append({
                "severity": "critical" if cpu_oc > policy.cpu_overcommit_max * 1.25 else "warning",
                "type": "overcommit_cpu", "typeLabel": "Overcommit ratio vượt ngưỡng (CPU)",
                "entity": c["name"], "cluster": c["name"],
                "metric": f"CPU overcommit {cpu_oc:.2f}x (ngưỡng {policy.cpu_overcommit_max:.1f}x)",
                "recommendation": "Tỷ lệ vCPU:pCPU vượt ngưỡng khuyến nghị — cân nhắc bổ sung host hoặc giảm vCPU cấp phát cho VM trong cluster.",
            })
        ram_oc = c["model"]["ramOvercommit"]
        if _finite(ram_oc) and ram_oc > policy.ram_overcommit_max:
            findings.append({
                "severity": "critical" if ram_oc > policy.ram_overcommit_max * 1.25 else "warning",
                "type": "overcommit_ram", "typeLabel": "Overcommit ratio vượt ngưỡng (RAM)",
                "entity": c["name"], "cluster": c["name"],
                "metric": f"RAM overcommit {ram_oc:.2f}x (ngưỡng {policy.ram_overcommit_max:.1f}x)",
                "recommendation": "Tỷ lệ vRAM:pRAM vượt ngưỡng khuyến nghị — cân nhắc bổ sung RAM vật lý hoặc giảm RAM cấp phát cho VM trong cluster.",
            })

    vm_overprovision_usage_threshold = (100 / policy.cpu_overcommit_max) if policy.cpu_overcommit_max > 0 else 15.0
    for v in vms:
        if v.get("isPoweredOn") and _finite(v.get("lastActivityDays")) and v["lastActivityDays"] >= policy.idle_days:
            findings.append({
                "severity": "critical" if v["lastActivityDays"] >= policy.idle_days * 2 else "warning",
                "type": "vm_idle", "typeLabel": "VM idle lâu",
                "entity": v.get("vm"), "cluster": v.get("cluster"), "vcenterKey": v.get("vcenterKey") or "",
                "metric": f"Không hoạt động {int(v['lastActivityDays'])} ngày",
                "recommendation": "Xác nhận với owner; cân nhắc decommission hoặc thu hồi tài nguyên nếu không còn dùng.",
            })
        if not v.get("isPoweredOn") and _finite(v.get("powerOffDays")) and v["powerOffDays"] >= policy.idle_days:
            findings.append({
                "severity": "critical" if v["powerOffDays"] >= policy.idle_days * 3 else "warning",
                "type": "vm_off", "typeLabel": "VM tắt lâu",
                "entity": v.get("vm"), "cluster": v.get("cluster"), "vcenterKey": v.get("vcenterKey") or "",
                "metric": f"Đã tắt {int(v['powerOffDays'])} ngày",
                "recommendation": "VM tắt lâu ngày vẫn giữ reservation vCPU/RAM — cân nhắc decommission để thu hồi tài nguyên.",
            })
        cpu_usage = v.get("cpuUsagePctRaw")
        if _finite(cpu_usage) and cpu_usage > 0 and cpu_usage < vm_overprovision_usage_threshold and (v.get("vcpu", 0) or 0) >= 4:
            findings.append({
                "severity": "info", "type": "vm_overprovision", "typeLabel": "VM over-provision (vCPU)",
                "entity": v.get("vm"), "cluster": v.get("cluster"), "vcenterKey": v.get("vcenterKey") or "",
                "metric": f"{int(v.get('vcpu', 0))} vCPU cấp phát, CPU usage chỉ {cpu_usage:.1f}% (ngưỡng {vm_overprovision_usage_threshold:.1f}% theo overcommit {policy.cpu_overcommit_max:.1f}x)",
                "recommendation": "vCPU cấp phát dư nhiều so với usage thực tế và ngưỡng overcommit CPU cấu hình; cân nhắc resize giảm vCPU.",
            })

    return assign_finding_codes(findings)


# ---------------------------------------------------------------------------
# Forward-looking capacity planning helpers (report_pdf.py "Executive" /
# "Technical" additions). All numbers here are DERIVED from the current
# snapshot only -- this deployment currently retains a single snapshot per
# vCenter (no repeated time-series captures yet), so there is no real
# historical trend to fit. Growth-scenario projections are therefore
# explicitly assumption-based (a stated quarterly growth %) rather than
# regression on history, and every caller must render them as such.
# ---------------------------------------------------------------------------
def estimate_avg_vm_footprint(vms: List[Dict[str, Any]]) -> Dict[str, float]:
    powered_on = [v for v in vms if v.get("isPoweredOn")]
    n_on = len(powered_on) or 1
    n_all = len(vms) or 1
    return {
        "avgVcpu": sum(v.get("vcpu", 0) or 0 for v in powered_on) / n_on,
        "avgRamGB": sum(v.get("ramGB", 0) or 0 for v in powered_on) / n_on,
        "avgStorageGB": sum(v.get("diskProvGB", 0) or 0 for v in vms) / n_all,
    }


def estimate_capacity_headroom(model: Dict[str, Any], vms: List[Dict[str, Any]], policy: Policy) -> Dict[str, Any]:
    """Absolute-unit headroom remaining before CPU/RAM/Storage would breach
    its policy threshold, plus how many more 'average-sized' VMs (based on
    the current fleet's own average footprint) each resource could still
    absorb. Storage ceiling uses the same 90% used/allocated threshold as
    `get_status_for_pct`'s storage warning band elsewhere in this file."""
    avg = estimate_avg_vm_footprint(vms)
    max_vcpu = model["totalLogicalCores"] * policy.cpu_overcommit_max
    free_vcpu = max(0.0, max_vcpu - model["usedVcpu"])
    max_ram = model["totalRamGB"] * policy.ram_overcommit_max
    free_ram = max(0.0, max_ram - model["usedRamGB"])
    storage_ceiling = model["provStorageGB"] * 0.90
    free_storage = max(0.0, storage_ceiling - model["usedStorageGB"])

    def vms_fit(free_val, avg_val):
        if not _finite(free_val) or not _finite(avg_val) or avg_val <= 0:
            return None
        return int(math.floor(free_val / avg_val))

    return {
        "avg": avg,
        "cpu": {"freeVcpu": free_vcpu, "maxVcpu": max_vcpu, "vmsFit": vms_fit(free_vcpu, avg["avgVcpu"])},
        "ram": {"freeRamGB": free_ram, "maxRamGB": max_ram, "vmsFit": vms_fit(free_ram, avg["avgRamGB"])},
        "storage": {"freeStorageGB": free_storage, "ceilingGB": storage_ceiling, "vmsFit": vms_fit(free_storage, avg["avgStorageGB"])},
    }


def _months_to_target(current: Optional[float], target: float, growth_pct_per_quarter: float) -> Optional[float]:
    """Months until `current` compounds up to `target` at `growth_pct_per_quarter`
    (quarterly compounding). None when inputs are non-finite/non-positive or
    growth is zero/negative (never reaches target)."""
    if not _finite(current) or current is None or current <= 0 or target <= 0:
        return None
    if current >= target:
        return 0.0
    g = growth_pct_per_quarter / 100.0
    if g <= 0:
        return None
    quarters = math.log(target / current) / math.log(1 + g)
    return max(0.0, quarters * 3)


def estimate_growth_scenarios(model: Dict[str, Any], scenarios=(5.0, 10.0, 20.0)) -> Dict[str, Any]:
    """For CPU/RAM (already '% of overcommit threshold', ceiling=100) and
    Storage ('% used of allocated', ceiling=90 -- matching the storage
    warning band used across this app), estimate months-to-threshold under
    each assumed quarterly growth-rate scenario. ASSUMPTION-BASED planning
    aid, not a fit to historical data -- label it as such at render time."""
    metrics = [("cpu", model.get("cpuPct"), 100.0), ("ram", model.get("ramPct"), 100.0),
               ("storage", model.get("storagePct"), 90.0)]
    out = {}
    for key, pct, target in metrics:
        out[key] = {
            "currentPct": pct, "target": target,
            "scenarios": [{"growthPct": g, "months": _months_to_target(pct, target, g)} for g in scenarios],
        }
    return out


def evaluate_ha_headroom(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: Policy) -> List[Dict[str, Any]]:
    """Per-cluster 'lose the largest host' resiliency check (N+1-style DR
    read): recompute CPU/RAM overcommit against the cluster's REMAINING hosts
    assuming every powered-on VM must still be accommodated (worst case, no
    VM is evicted/decommissioned). Clusters with fewer than 2 hosts have no
    redundancy to test and are flagged as such rather than scored."""
    out = []
    ht_factor = max(1.0, policy.ht_factor or 2.0)
    for name in get_cluster_list(hosts, vms):
        c_hosts = [h for h in hosts if h.get("cluster") == name]
        c_vms = [v for v in vms if v.get("cluster") == name]
        if len(c_hosts) < 2:
            out.append({"cluster": name, "eligible": False, "hosts": len(c_hosts)})
            continue
        largest = max(c_hosts, key=lambda h: (h.get("cores", 0) or 0) * ht_factor + (h.get("ramGB", 0) or 0) * 1e-6)
        remaining = [h for h in c_hosts if h is not largest]
        remaining_logical = sum((h.get("cores", 0) or 0) for h in remaining) * ht_factor
        remaining_ram = sum((h.get("ramGB", 0) or 0) for h in remaining)
        model_now = build_capacity_model(c_hosts, c_vms, policy)
        used_vcpu, used_ram = model_now["usedVcpu"], model_now["usedRamGB"]
        cpu_oc_after = (used_vcpu / remaining_logical) if remaining_logical > 0 else float("inf")
        ram_oc_after = (used_ram / remaining_ram) if remaining_ram > 0 else float("inf")
        cpu_pct_after = (cpu_oc_after / policy.cpu_overcommit_max * 100) if policy.cpu_overcommit_max > 0 else float("nan")
        ram_pct_after = (ram_oc_after / policy.ram_overcommit_max * 100) if policy.ram_overcommit_max > 0 else float("nan")
        out.append({
            "cluster": name, "eligible": True, "hosts": len(c_hosts),
            "largestHost": largest.get("host"),
            "cpuPctAfter": cpu_pct_after, "ramPctAfter": ram_pct_after,
            "survives": _finite(cpu_pct_after) and _finite(ram_pct_after) and cpu_pct_after <= 100 and ram_pct_after <= 100,
        })
    return out
