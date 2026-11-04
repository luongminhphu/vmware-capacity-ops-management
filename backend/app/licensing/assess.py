"""License assessment engine.

Pure functions: (hosts, classified vms, rules, entitlements) -> result dict.
No database access, so the same inputs stored in a LicenseAssessment snapshot
reproduce the same result (verified by result_hash).

Windows Server is evaluated per *cluster* because HA/DRS/vMotion let every VM
run on every host in the cluster; both Standard and Datacenter options are
computed so the reviewer can compare. VMware per-core demand is per host and
summed over the scope.
"""
import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .os_classify import summarize

WS_PRODUCTS = {"ws_std": "Windows Server Standard", "ws_dc": "Windows Server Datacenter"}
VMWARE_CORE_PRODUCTS = {"vsphere_std": "vSphere Standard", "vsphere_ent_plus": "vSphere Enterprise Plus",
                        "vvf": "VMware vSphere Foundation (VVF)", "vcf": "VMware Cloud Foundation (VCF)"}
VMWARE_LEGACY_PRODUCT = {"vmware_cpu_legacy": "VMware per-CPU (perpetual)"}
PRODUCT_LABEL = {**WS_PRODUCTS, **VMWARE_CORE_PRODUCTS, **VMWARE_LEGACY_PRODUCT}
METRIC_CORES = {"core": 1, "pack2": 2, "pack16": 16}


def _i(v, default=0) -> int:
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f):
            return default
        return int(round(f))
    except Exception:
        return default


def _ceil_div(a: float, b: float) -> int:
    return int(math.ceil(a / b)) if b > 0 else 0


def _packs(cores: int, pack_sizes: List[int]) -> Dict[str, int]:
    """Greedy packaging: as many large packs as fit, remainder in small packs."""
    sizes = sorted({int(s) for s in pack_sizes if int(s) > 0}, reverse=True) or [16]
    out: Dict[str, int] = {}
    remaining = max(0, int(cores))
    for idx, s in enumerate(sizes):
        if idx == len(sizes) - 1:
            n = _ceil_div(remaining, s)
            remaining = 0
        else:
            n = remaining // s
            remaining -= n * s
        out[f"pack{s}"] = n
    out["pack16Only"] = _ceil_div(int(cores), 16)
    return out


# ---------------------------------------------------------------------------
# Per-host figures
# ---------------------------------------------------------------------------

def host_license_row(h: Dict[str, Any], ws: Dict[str, Any], vm_core: Dict[str, Any], vm_legacy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cores = _i(h.get("cores"))
    sockets_raw = h.get("cpuSockets")
    sockets = _i(sockets_raw)
    flags: List[str] = []
    if sockets <= 0:
        sockets = 2 if cores >= 16 else 1
        flags.append("sockets_assumed")
    if cores <= 0:
        flags.append("cores_missing")
    threads = _i(h.get("cpuThreads")) or None

    min_per_proc = _i(ws.get("min_cores_per_proc"), 8)
    min_per_host = _i(ws.get("min_cores_per_host"), 16)
    ws_lic_cores = max(cores, sockets * min_per_proc, min_per_host) if cores > 0 else 0
    ws_floor_applied = ws_lic_cores > cores

    vmw_min = _i(vm_core.get("min_cores_per_cpu"), 16)
    vmw_cores = max(cores, sockets * vmw_min) if cores > 0 else 0
    vmw_floor_applied = vmw_cores > cores

    legacy_cpu = None
    if vm_legacy:
        per = _i(vm_legacy.get("cores_per_cpu_license"), 32)
        cores_per_socket = cores / sockets if sockets > 0 else cores
        legacy_cpu = sockets * _ceil_div(cores_per_socket, per) if cores > 0 else 0

    return {
        "vcenterKey": h.get("vcenterKey"), "cluster": h.get("cluster"), "host": h.get("host"),
        "vendor": h.get("vendor"), "model": h.get("model"), "esxiVersion": h.get("esxiVersion"), "esxiBuild": h.get("esxiBuild"),
        "cpuModel": h.get("cpuModel"), "sockets": sockets, "cores": cores, "threads": threads, "ramGB": h.get("ramGB"),
        "clusterHa": h.get("clusterHa"), "clusterDrs": h.get("clusterDrs"),
        "wsLicensableCores": ws_lic_cores, "wsFloorApplied": ws_floor_applied,
        "vmwareCores": vmw_cores, "vmwareFloorApplied": vmw_floor_applied,
        "vmwareLegacyCpuLicenses": legacy_cpu,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Entitlements
# ---------------------------------------------------------------------------

def _ent_cores(e: Dict[str, Any]) -> float:
    return float(e.get("quantity") or 0) * METRIC_CORES.get(e.get("metric") or "core", 1)


def _ent_matches_scope(e: Dict[str, Any], vcenter_keys: Optional[List[str]], clusters: Optional[List[str]]) -> bool:
    if e.get("scopeVcenter") and vcenter_keys and e["scopeVcenter"] not in vcenter_keys:
        return False
    if e.get("scopeCluster") and clusters and e["scopeCluster"] not in clusters:
        return False
    return True


def _ent_summary(ents: List[Dict[str, Any]], product: str, unit: str) -> Dict[str, Any]:
    """unit: 'core' or 'cpu'."""
    rows = [e for e in ents if e.get("product") == product]
    qty = 0.0
    cost_known = True
    total_cost = 0.0
    currencies = set()
    price_per_core: Optional[float] = None
    for e in rows:
        if unit == "core":
            units = _ent_cores(e)
        else:
            units = float(e.get("quantity") or 0)
        qty += units
        if e.get("unitPrice") is not None and units > 0:
            total_cost += float(e["unitPrice"]) * float(e.get("quantity") or 0)
            currencies.add(e.get("currency") or "VND")
            per = float(e["unitPrice"]) / (METRIC_CORES.get(e.get("metric") or "core", 1) if unit == "core" else 1)
            price_per_core = per if price_per_core is None else min(price_per_core, per)
        elif units > 0:
            cost_known = False
    return {
        "product": product, "label": PRODUCT_LABEL.get(product, product), "count": len(rows),
        "units": qty, "unit": unit, "hasSa": any(e.get("hasSa") for e in rows),
        "costKnown": cost_known and bool(rows), "totalCost": total_cost if rows else None,
        "currency": (sorted(currencies)[0] if len(currencies) == 1 else ("mixed" if currencies else None)),
        "pricePerUnit": price_per_core,  # per core (or per CPU for legacy); lowest if several
        "items": rows,
    }


def _status(demand: float, demand_max: float, entitled: float, has_ent: bool) -> str:
    if not has_ent:
        return "no_entitlement"
    if entitled >= demand_max:
        return "ok"
    if entitled >= demand:
        return "insufficient_data"   # OK on confirmed VMs, unknown VMs could tip it
    return "shortfall"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def assess(hosts: List[Dict[str, Any]], vms_classified: List[Dict[str, Any]], rules: Dict[str, Dict[str, Any]],
           entitlements: List[Dict[str, Any]], scope: Dict[str, Any]) -> Dict[str, Any]:
    ws_rule = rules.get("windows_server")
    vm_rule = rules.get("vmware")
    legacy_rule = rules.get("vmware_cpu_legacy") if (rules.get("vmware_cpu_legacy") or {}).get("isActive") else None
    ws = (ws_rule or {}).get("params") or {}
    vmc = (vm_rule or {}).get("params") or {}
    vml = (legacy_rule or {}).get("params") if legacy_rule else None

    vcenter_keys = scope.get("vcenterKeys")
    clusters_filter = scope.get("clusters")
    if clusters_filter:
        hosts = [h for h in hosts if h.get("cluster") in clusters_filter]
        vms_classified = [v for v in vms_classified if v.get("cluster") in clusters_filter]

    host_rows = [host_license_row(h, ws, vmc, vml) for h in hosts]

    # VM counts per host/cluster
    include_off = bool(ws.get("include_powered_off", True))
    ws_vms_cluster: Dict[Any, int] = defaultdict(int)
    ws_vms_cluster_lowconf: Dict[Any, int] = defaultdict(int)
    unknown_cluster: Dict[Any, int] = defaultdict(int)
    client_cluster: Dict[Any, int] = defaultdict(int)
    ws_vms_host: Dict[Any, int] = defaultdict(int)
    vm_count_host: Dict[Any, int] = defaultdict(int)
    excluded_off = 0
    for v in vms_classified:
        ckey = (v.get("vcenterKey"), v.get("cluster"))
        hkey = (v.get("vcenterKey"), v.get("host"))
        vm_count_host[hkey] += 1
        cat = v["os"]["category"]
        if not include_off and not v.get("isPoweredOn"):
            if cat == "windows_server":
                excluded_off += 1
            continue
        if cat == "windows_server":
            ws_vms_cluster[ckey] += 1
            ws_vms_host[hkey] += 1
            if v["os"]["confidence"] in ("low", "medium"):
                ws_vms_cluster_lowconf[ckey] += 1
        elif cat == "other_unknown":
            unknown_cluster[ckey] += 1
        elif cat == "windows_client":
            client_cluster[ckey] += 1
    for r in host_rows:
        k = (r["vcenterKey"], r["host"])
        r["vmCount"] = vm_count_host.get(k, 0)
        r["wsVmsOnHost"] = ws_vms_host.get(k, 0)

    # Cluster rows
    oses_per = max(1, _i(ws.get("standard_oses_per_license"), 2))
    pack_sizes = ws.get("pack_sizes") or [2, 16]
    peak_mode = ws.get("standard_peak_mode") or "cluster_total"
    cluster_rows: List[Dict[str, Any]] = []
    groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for r in host_rows:
        groups[(r["vcenterKey"], r["cluster"])].append(r)
    for (vck, cname), hs in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        ws_n = ws_vms_cluster.get((vck, cname), 0)
        unk = unknown_cluster.get((vck, cname), 0)
        lic_cores = sum(h["wsLicensableCores"] for h in hs)
        ha = any(h.get("clusterHa") for h in hs)
        drs = any(h.get("clusterDrs") for h in hs)
        mobility = ha or drs or len(hs) > 1
        # Standard
        if peak_mode == "current_placement":
            std_cores = sum(h["wsLicensableCores"] * _ceil_div(h["wsVmsOnHost"], oses_per) for h in hs)
            std_cores_max = std_cores  # unknowns not placed
            sets_desc = "theo host hiện tại"
        else:
            sets = _ceil_div(ws_n, oses_per)
            sets_max = _ceil_div(ws_n + unk, oses_per)
            std_cores = sum(h["wsLicensableCores"] * sets for h in hs)
            std_cores_max = sum(h["wsLicensableCores"] * sets_max for h in hs)
            sets_desc = f"ceil({ws_n}/{oses_per}) = {sets} bộ license/host"
        dc_cores = lic_cores
        cluster_rows.append({
            "vcenterKey": vck, "cluster": cname, "hostCount": len(hs),
            "sockets": sum(h["sockets"] for h in hs), "physicalCores": sum(h["cores"] for h in hs),
            "logicalThreads": sum((h["threads"] or 0) for h in hs) or None,
            "wsLicensableCores": lic_cores, "ha": ha, "drs": drs, "mobility": mobility,
            "vmCount": sum(h["vmCount"] for h in hs),
            "wsVms": ws_n, "wsVmsLowConfidence": ws_vms_cluster_lowconf.get((vck, cname), 0),
            "unknownVms": unk, "clientVms": client_cluster.get((vck, cname), 0),
            "standard": {"sets": _ceil_div(ws_n, oses_per) if peak_mode != "current_placement" else None,
                         "setsDesc": sets_desc, "cores": std_cores, "coresMax": std_cores_max,
                         "packs": _packs(std_cores, pack_sizes), "packsMax": _packs(std_cores_max, pack_sizes)},
            "datacenter": {"cores": dc_cores, "coresMax": dc_cores, "packs": _packs(dc_cores, pack_sizes)},
            "recommendedEdition": ("datacenter" if std_cores > dc_cores else "standard") if ws_n > 0 else None,
            "vmwareCores": sum(h["vmwareCores"] for h in hs),
            "vmwareLegacyCpuLicenses": (sum((h["vmwareLegacyCpuLicenses"] or 0) for h in hs) if vml else None),
            "hostsWithAssumedSockets": sum(1 for h in hs if "sockets_assumed" in h["flags"]),
        })

    present_clusters = sorted({c["cluster"] for c in cluster_rows})
    ents_in_scope = [e for e in entitlements if _ent_matches_scope(e, vcenter_keys, present_clusters)]

    # ---- Windows Server totals ----
    std_cores = sum(c["standard"]["cores"] for c in cluster_rows)
    std_cores_max = sum(c["standard"]["coresMax"] for c in cluster_rows)
    dc_cores = sum(c["datacenter"]["cores"] for c in cluster_rows)
    ws_total_vms = sum(c["wsVms"] for c in cluster_rows)
    unknown_total = sum(c["unknownVms"] for c in cluster_rows)
    ent_std = _ent_summary(ents_in_scope, "ws_std", "core")
    ent_dc = _ent_summary(ents_in_scope, "ws_dc", "core")

    def _ws_option(name: str, cores: int, cores_max: int, ent: Dict[str, Any]) -> Dict[str, Any]:
        has = ent["units"] > 0
        shortfall = max(0.0, cores - ent["units"])
        shortfall_max = max(0.0, cores_max - ent["units"])
        cost = None
        if ent["pricePerUnit"] is not None:
            cost = {
                "currency": ent["currency"], "pricePerCore": ent["pricePerUnit"],
                "demandCost": cores * ent["pricePerUnit"], "demandCostMax": cores_max * ent["pricePerUnit"],
                "shortfallCost": shortfall * ent["pricePerUnit"], "shortfallCostMax": shortfall_max * ent["pricePerUnit"],
                "ownedCost": ent["totalCost"],
            }
        return {
            "edition": name, "demandCores": cores, "demandCoresMax": cores_max,
            "packs": _packs(cores, pack_sizes), "packsMax": _packs(cores_max, pack_sizes),
            "entitledCores": ent["units"], "shortfallCores": shortfall, "shortfallCoresMax": shortfall_max,
            "shortfallPacks": _packs(int(shortfall), pack_sizes), "shortfallPacksMax": _packs(int(shortfall_max), pack_sizes),
            "status": _status(cores, cores_max, ent["units"], has) if ws_total_vms + unknown_total > 0 else ("ok" if not has else "ok"),
            "entitlement": {k: v for k, v in ent.items() if k != "items"}, "cost": cost,
        }

    ws_std_opt = _ws_option("standard", std_cores, std_cores_max, ent_std)
    ws_dc_opt = _ws_option("datacenter", dc_cores, dc_cores, ent_dc)

    ws_formula = [
        f"Core cần license mỗi host = max(physical core, {_i(ws.get('min_cores_per_proc'), 8)} × socket, {_i(ws.get('min_cores_per_host'), 16)}).",
        f"Standard: mỗi lần license đủ core của host cấp quyền chạy {oses_per} OSE (VM Windows Server). "
        + ("Phạm vi đếm OSE = toàn cluster (HA/DRS/vMotion cho phép VM chạy trên mọi host) → số bộ license/host = ceil(số VM Windows Server trong cluster / "
           f"{oses_per}); core Standard = Σ(host) core licensable × số bộ." if peak_mode != "current_placement"
           else "Chế độ tham chiếu 'theo host hiện tại': core Standard = Σ(host) core licensable × ceil(VM Windows Server trên host / 2) — chỉ đúng khi có License Mobility/SA hoặc VM không di chuyển."),
        "Datacenter: license đủ core licensable của mỗi host một lần; không giới hạn OSE.",
        f"Đóng gói: pack {', '.join(str(p) for p in sorted(pack_sizes))} core; cột 'chỉ pack 16' = ceil(core/16).",
        "Thiếu hụt = max(0, core nhu cầu − core entitlement trong phạm vi). Chi phí = thiếu hụt × đơn giá/core (từ đơn giá pack đã nhập).",
    ]
    ws_assumptions = [
        ("Bao gồm VM Windows Server đang tắt (powered-off) vào số OSE." if include_off else f"Không tính {excluded_off} VM Windows Server đang tắt (tuỳ chọn include_powered_off=false)."),
        "VM Windows Client (Win 10/11) không thuộc phạm vi Windows Server license (cần VDA/Windows Enterprise riêng).",
        "Edition thực tế của từng VM không lấy được từ vCenter — kết quả so sánh cả hai phương án Standard/Datacenter; edition chỉ có khi override thủ công.",
        "CAL (User/Device) không được tính trong công cụ này.",
    ]
    ws_gaps: List[str] = []
    if unknown_total:
        ws_gaps.append(f"{unknown_total} VM Other/Unknown có thể là Windows Server → nhu cầu Standard hiển thị dạng khoảng [{std_cores}; {std_cores_max}] core.")
    lowconf = sum(c["wsVmsLowConfidence"] for c in cluster_rows)
    if lowconf:
        ws_gaps.append(f"{lowconf} VM Windows Server chỉ có bằng chứng từ cấu hình/tên (confidence medium/low) — nên xác nhận bằng VMware Tools hoặc override.")
    assumed = sum(c["hostsWithAssumedSockets"] for c in cluster_rows)
    if assumed:
        ws_gaps.append(f"{assumed} host thiếu số socket → giả định 2 socket (1 nếu <16 core); kiểm tra lại trước khi chốt.")
    if not ws_rule:
        ws_gaps.append("Chưa có rule Windows Server đang active — không thể kết luận.")
    elif ws_rule.get("status") != "verified":
        ws_gaps.append(f"Rule '{ws_rule.get('name')}' v{ws_rule.get('version')} chưa được xác minh (needs_verification).")
    if ent_std["count"] + ent_dc["count"] == 0:
        ws_gaps.append("Chưa nhập entitlement Windows Server → chỉ hiển thị nhu cầu, không kết luận compliance.")

    # ---- VMware totals ----
    vmw_cores = sum(h["vmwareCores"] for h in host_rows)
    vmw_ents = {p: _ent_summary(ents_in_scope, p, "core") for p in VMWARE_CORE_PRODUCTS}
    vmw_ent_total = sum(e["units"] for e in vmw_ents.values())
    vmw_has = vmw_ent_total > 0
    vmw_short = max(0.0, vmw_cores - vmw_ent_total)
    min_order = _i(vmc.get("min_cores_per_order"), 0)
    order_qty = max(vmw_short, min_order) if vmw_short > 0 and min_order > 0 else vmw_short
    vmw_cost = None
    priced = [e for e in vmw_ents.values() if e["pricePerUnit"] is not None]
    if priced:
        pick = min(priced, key=lambda e: e["pricePerUnit"])
        vmw_cost = {"basis": pick["label"], "currency": pick["currency"], "pricePerCore": pick["pricePerUnit"],
                    "demandCost": vmw_cores * pick["pricePerUnit"], "shortfallCost": order_qty * pick["pricePerUnit"]}
    legacy = None
    if vml:
        cpu_demand = sum((h["vmwareLegacyCpuLicenses"] or 0) for h in host_rows)
        ent_leg = _ent_summary(ents_in_scope, "vmware_cpu_legacy", "cpu")
        legacy = {"demandCpuLicenses": cpu_demand, "entitled": ent_leg["units"],
                  "shortfall": max(0.0, cpu_demand - ent_leg["units"]),
                  "status": _status(cpu_demand, cpu_demand, ent_leg["units"], ent_leg["units"] > 0),
                  "entitlement": {k: v for k, v in ent_leg.items() if k != "items"},
                  "formula": f"Số license CPU = Σ(host) socket × ceil(core/socket / {_i(vml.get('cores_per_cpu_license'), 32)})."}

    vmw_formula = [
        f"Core VMware mỗi host = max(physical core, {_i(vmc.get('min_cores_per_cpu'), 16)} × socket) — tối thiểu {_i(vmc.get('min_cores_per_cpu'), 16)} core/CPU.",
        "Nhu cầu = Σ core của tất cả ESXi host trong phạm vi (mọi host trong vCenter đều phải có license).",
        "Thiếu hụt = max(0, core nhu cầu − Σ core entitlement VCF/VVF/vSphere trong phạm vi)."
        + (f" Áp dụng số core tối thiểu mỗi đơn hàng = {min_order}." if min_order > 0 else ""),
    ]
    vmw_assumptions = [
        "Chỉ đếm core vật lý, không đếm hyper-thread (logical thread) và không đếm vCPU.",
        "Entitlement của các sản phẩm VCF/VVF/vSphere được cộng gộp để so với nhu cầu; thực tế mỗi cluster nên dùng một dòng sản phẩm.",
        "Không có 'số core tối thiểu mỗi đơn hàng' trong tài liệu Broadcom hiện hành; tham số này mặc định 0." if min_order == 0 else
        "Tham số số core tối thiểu mỗi đơn hàng do người dùng bật — cần đối chiếu hợp đồng.",
    ]
    vmw_gaps: List[str] = []
    if assumed:
        vmw_gaps.append(f"{assumed} host thiếu số socket → giả định socket; ảnh hưởng mức sàn 16 core/CPU.")
    if not vm_rule:
        vmw_gaps.append("Chưa có rule VMware đang active.")
    elif vm_rule.get("status") != "verified":
        vmw_gaps.append(f"Rule '{vm_rule.get('name')}' v{vm_rule.get('version')} chưa được xác minh (needs_verification).")
    if not vmw_has:
        vmw_gaps.append("Chưa nhập entitlement VMware → chỉ hiển thị nhu cầu core.")

    result = {
        "scope": scope,
        "rules": {
            "windows_server": _rule_meta(ws_rule), "vmware": _rule_meta(vm_rule),
            "vmware_cpu_legacy": _rule_meta(legacy_rule) if legacy_rule else None,
        },
        "hosts": host_rows,
        "clusters": cluster_rows,
        "inventory": summarize(vms_classified),
        "totals": {
            "hosts": len(host_rows), "sockets": sum(h["sockets"] for h in host_rows),
            "physicalCores": sum(h["cores"] for h in host_rows),
            "logicalThreads": sum((h["threads"] or 0) for h in host_rows) or None,
            "vcpuAllocated": sum(float(v.get("vcpu") or 0) for v in vms_classified),
            "vms": len(vms_classified), "windowsServerVms": ws_total_vms, "unknownVms": unknown_total,
        },
        "windowsServer": {
            "standard": ws_std_opt, "datacenter": ws_dc_opt,
            "recommendedEdition": _recommend(ws_std_opt, ws_dc_opt),
            "formula": ws_formula, "assumptions": ws_assumptions, "dataGaps": ws_gaps,
            "peakMode": peak_mode,
        },
        "vmware": {
            "demandCores": vmw_cores, "entitledCores": vmw_ent_total, "shortfallCores": vmw_short,
            "orderCores": order_qty, "status": _status(vmw_cores, vmw_cores, vmw_ent_total, vmw_has) if host_rows else "ok",
            "entitlements": {p: {k: v for k, v in e.items() if k != "items"} for p, e in vmw_ents.items()},
            "cost": vmw_cost, "legacy": legacy,
            "formula": vmw_formula, "assumptions": vmw_assumptions, "dataGaps": vmw_gaps,
        },
        "entitlementsUsed": ents_in_scope,
    }
    result["overallStatus"] = _overall(result)
    return result


def _rule_meta(r: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not r:
        return None
    return {k: r.get(k) for k in ("id", "key", "version", "name", "status", "verifiedAt", "verifiedBy", "sourceUrl", "params")}


def _recommend(std: Dict[str, Any], dc: Dict[str, Any]) -> Dict[str, Any]:
    """Compare editions on cores (and on cost when both prices exist)."""
    out: Dict[str, Any] = {"byCores": None, "byCost": None, "note": None}
    if std["demandCores"] == 0 and dc["demandCores"] == 0:
        return out
    out["byCores"] = "datacenter" if std["demandCores"] > dc["demandCores"] else "standard"
    if std.get("cost") and dc.get("cost") and std["cost"]["currency"] == dc["cost"]["currency"] and std["cost"]["currency"] != "mixed":
        s, d = std["cost"]["demandCost"], dc["cost"]["demandCost"]
        out["byCost"] = "datacenter" if s > d else "standard"
        out["saving"] = abs(s - d)
        out["currency"] = std["cost"]["currency"]
    else:
        out["note"] = "Chưa đủ đơn giá cả hai edition (cùng đơn vị tiền) để so sánh chi phí."
    return out


def _overall(res: Dict[str, Any]) -> str:
    sts = [res["vmware"]["status"], res["windowsServer"]["standard"]["status"], res["windowsServer"]["datacenter"]["status"]]
    # Windows: good enough if either edition is OK (organisation chooses one)
    ws_pair = {res["windowsServer"]["standard"]["status"], res["windowsServer"]["datacenter"]["status"]}
    ws_status = "ok" if "ok" in ws_pair else ("insufficient_data" if "insufficient_data" in ws_pair else
                                              ("shortfall" if "shortfall" in ws_pair else "no_entitlement"))
    order = ["shortfall", "insufficient_data", "no_entitlement", "ok"]
    for s in order:
        if s in (res["vmware"]["status"], ws_status):
            return s
    return sts[0]


def result_hash(result: Dict[str, Any]) -> str:
    core = {k: result.get(k) for k in ("totals", "windowsServer", "vmware", "clusters")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]
