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
    def __init__(self, cpu_overcommit_max=6.0, ram_overcommit_max=1.5, ht_factor=2.0, idle_days=30):
        self.cpu_overcommit_max = cpu_overcommit_max
        self.ram_overcommit_max = ram_overcommit_max
        self.ht_factor = ht_factor
        self.idle_days = idle_days


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
                "entity": h.get("host"), "cluster": h.get("cluster"),
                "metric": f"CPU overcommit {h['cpuOvercommit']:.2f}x (ngưỡng {policy.cpu_overcommit_max:.1f}x)",
                "recommendation": "Overcommit CPU đã vượt ngưỡng cấu hình — cần di dời VM hoặc bổ sung host ngay." if h["cpuPct"] > 100 else "Overcommit CPU đang tiến gần ngưỡng — cân nhắc vMotion VM sang host khác hoặc lên kế hoạch bổ sung năng lực CPU.",
            })
        if _finite(h["ramPct"]) and h["ramPct"] >= 80:
            findings.append({
                "severity": "critical" if h["ramPct"] > 100 else "warning",
                "type": "host_ram", "typeLabel": "Host RAM vượt ngưỡng",
                "entity": h.get("host"), "cluster": h.get("cluster"),
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
                "entity": v.get("vm"), "cluster": v.get("cluster"),
                "metric": f"Không hoạt động {int(v['lastActivityDays'])} ngày",
                "recommendation": "Xác nhận với owner; cân nhắc decommission hoặc thu hồi tài nguyên nếu không còn dùng.",
            })
        if not v.get("isPoweredOn") and _finite(v.get("powerOffDays")) and v["powerOffDays"] >= policy.idle_days:
            findings.append({
                "severity": "critical" if v["powerOffDays"] >= policy.idle_days * 3 else "warning",
                "type": "vm_off", "typeLabel": "VM tắt lâu",
                "entity": v.get("vm"), "cluster": v.get("cluster"),
                "metric": f"Đã tắt {int(v['powerOffDays'])} ngày",
                "recommendation": "VM tắt lâu ngày vẫn giữ reservation vCPU/RAM — cân nhắc decommission để thu hồi tài nguyên.",
            })
        cpu_usage = v.get("cpuUsagePctRaw")
        if _finite(cpu_usage) and cpu_usage > 0 and cpu_usage < vm_overprovision_usage_threshold and (v.get("vcpu", 0) or 0) >= 4:
            findings.append({
                "severity": "info", "type": "vm_overprovision", "typeLabel": "VM over-provision (vCPU)",
                "entity": v.get("vm"), "cluster": v.get("cluster"),
                "metric": f"{int(v.get('vcpu', 0))} vCPU cấp phát, CPU usage chỉ {cpu_usage:.1f}% (ngưỡng {vm_overprovision_usage_threshold:.1f}% theo overcommit {policy.cpu_overcommit_max:.1f}x)",
                "recommendation": "vCPU cấp phát dư nhiều so với usage thực tế và ngưỡng overcommit CPU cấu hình; cân nhắc resize giảm vCPU.",
            })

    return findings
