"""License what-if coupled with capacity / HA guard.

A scenario mutates copies of the host/VM inventory, re-runs the license
assessment, and re-runs the capacity model + "lose largest host" HA check on
every touched cluster. If the scenario breaks capacity (overcommit > policy)
or removes HA survivability that existed before, the result is marked
`capacityVerdict = "violation"` and `recommendable = False` — the license
saving is still shown, but explicitly labelled as not actionable.
"""
import copy
from typing import Any, Dict, List, Optional

from .. import capacity
from .assess import assess
from .os_classify import classify_vm

SCENARIO_TYPES = ["add_host", "remove_host", "add_vms", "move_vms", "resize_host"]


def _cluster_hosts(hosts: List[Dict[str, Any]], vck: Optional[str], cluster: str) -> List[Dict[str, Any]]:
    return [h for h in hosts if h.get("cluster") == cluster and (vck is None or h.get("vcenterKey") == vck)]


def _cluster_vms(vms: List[Dict[str, Any]], vck: Optional[str], cluster: str) -> List[Dict[str, Any]]:
    return [v for v in vms if v.get("cluster") == cluster and (vck is None or v.get("vcenterKey") == vck)]


def _template_host(c_hosts: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not c_hosts:
        return {"cores": 32, "cpuSockets": 2, "cpuThreads": 64, "ramGB": 512, "clusterHa": True, "clusterDrs": True}
    # most common (cores, sockets, ram) combination
    from collections import Counter
    key = Counter((h.get("cores"), h.get("cpuSockets"), h.get("ramGB")) for h in c_hosts).most_common(1)[0][0]
    t = next(h for h in c_hosts if (h.get("cores"), h.get("cpuSockets"), h.get("ramGB")) == key)
    return t


def _capacity_snapshot(hosts, vms, policy, vck, cluster) -> Dict[str, Any]:
    ch, cv = _cluster_hosts(hosts, vck, cluster), _cluster_vms(vms, vck, cluster)
    model = capacity.build_capacity_model(ch, cv, policy)
    ha = capacity.evaluate_ha_headroom(ch, cv, policy)
    ha0 = ha[0] if ha else {"eligible": False, "hosts": len(ch)}
    return {
        "cluster": cluster, "vcenterKey": vck, "hosts": len(ch), "vms": len(cv),
        "cpuPct": model["cpuPct"], "ramPct": model["ramPct"],
        "cpuOvercommit": model["cpuOvercommit"], "ramOvercommit": model["ramOvercommit"],
        "haEligible": ha0.get("eligible", False), "haSurvives": ha0.get("survives"),
        "haCpuPctAfter": ha0.get("cpuPctAfter"), "haRamPctAfter": ha0.get("ramPctAfter"),
    }


def _fin(v) -> bool:
    try:
        return v == v and v not in (float("inf"), float("-inf"))
    except Exception:
        return False


def _verdict(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    level = "ok"
    for k, lbl in (("cpuPct", "CPU overcommit"), ("ramPct", "RAM overcommit")):
        a = after.get(k)
        if not _fin(a):
            continue
        if a > 100:
            level = "violation"
            reasons.append(f"{lbl} sau kịch bản = {a:.0f}% ngưỡng chính sách (>100%).")
        elif a > 80 and level != "violation":
            level = "warn"
            reasons.append(f"{lbl} sau kịch bản = {a:.0f}% ngưỡng (vùng cảnh báo >80%).")
    if before.get("haEligible") and before.get("haSurvives") and after.get("haEligible") and after.get("haSurvives") is False:
        level = "violation"
        reasons.append("Cluster mất khả năng chịu lỗi N+1 (mất 1 host lớn nhất) mà trước kịch bản vẫn có.")
    if before.get("haEligible") and not after.get("haEligible"):
        level = "violation" if before.get("haSurvives") else level
        reasons.append("Cluster còn <2 host — không còn dự phòng HA.")
    if after.get("haEligible") and after.get("haSurvives") is False and not (before.get("haEligible") and before.get("haSurvives")):
        if level == "ok":
            level = "warn"
        reasons.append("Cluster không đạt N+1 sau kịch bản (trước kịch bản cũng đã không đạt).")
    return {"level": level, "reasons": reasons}


def apply_scenario(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Return {hosts, vms, touchedClusters, description}. Does not mutate inputs."""
    hosts = copy.deepcopy(hosts)
    vms = copy.deepcopy(vms)
    st = scenario.get("type")
    vck = scenario.get("vcenterKey") or None
    cluster = scenario.get("cluster")
    touched: List[Any] = []
    desc = ""

    if st == "add_host":
        n = max(1, int(scenario.get("count") or 1))
        ch = _cluster_hosts(hosts, vck, cluster)
        tpl = _template_host(ch)
        cores = int(scenario.get("cores") or tpl.get("cores") or 32)
        sockets = int(scenario.get("sockets") or tpl.get("cpuSockets") or 2)
        ram = float(scenario.get("ramGB") or tpl.get("ramGB") or 512)
        for i in range(n):
            h = dict(tpl)
            h.update({"host": f"{cluster}-new-{i + 1:02d}", "cores": cores, "cpuSockets": sockets,
                      "cpuThreads": cores * 2, "ramGB": ram, "cluster": cluster, "vcenterKey": vck or tpl.get("vcenterKey"),
                      "cpuUsagePctRaw": 0, "ramUsagePctRaw": 0, "status": "planned", "model": scenario.get("model") or tpl.get("model")})
            hosts.append(h)
        touched.append((vck, cluster))
        desc = f"Thêm {n} host ({cores} core / {sockets} socket / {ram:.0f} GB) vào {cluster}"

    elif st == "remove_host":
        n = max(1, int(scenario.get("count") or 1))
        ch = _cluster_hosts(hosts, vck, cluster)
        names = scenario.get("hosts") or []
        if names:
            remove = [h for h in ch if h.get("host") in names]
        else:
            remove = sorted(ch, key=lambda h: ((h.get("cores") or 0), (h.get("ramGB") or 0)))[:n]  # smallest first
        rm = {(h.get("vcenterKey"), h.get("host")) for h in remove}
        hosts = [h for h in hosts if (h.get("vcenterKey"), h.get("host")) not in rm]
        # VMs stay in the cluster (they'd be vMotioned); re-home them to a remaining host for display
        remaining = _cluster_hosts(hosts, vck, cluster)
        if remaining:
            for i, v in enumerate(vms):
                if (v.get("vcenterKey"), v.get("host")) in rm:
                    v["host"] = remaining[i % len(remaining)].get("host")
        touched.append((vck, cluster))
        desc = f"Giảm {len(remove)} host khỏi {cluster}: " + ", ".join(h.get("host") for h in remove)

    elif st == "resize_host":
        ch = _cluster_hosts(hosts, vck, cluster)
        cores = scenario.get("cores")
        sockets = scenario.get("sockets")
        ram = scenario.get("ramGB")
        for h in ch:
            if cores:
                h["cores"] = int(cores)
                h["cpuThreads"] = int(cores) * 2
            if sockets:
                h["cpuSockets"] = int(sockets)
            if ram:
                h["ramGB"] = float(ram)
        touched.append((vck, cluster))
        desc = f"Nâng cấp {len(ch)} host trong {cluster}" + (f" → {cores} core" if cores else "") + (f" / {sockets} socket" if sockets else "") + (f" / {float(ram):.0f} GB" if ram else "")

    elif st == "add_vms":
        n = max(1, int(scenario.get("count") or 1))
        vcpu = float(scenario.get("vcpu") or 4)
        ram = float(scenario.get("ramGB") or 16)
        cat = scenario.get("category") or "windows_server"
        ch = _cluster_hosts(hosts, vck, cluster)
        guest = {"windows_server": ("windows2022srvNext_64Guest", "Microsoft Windows Server 2022 (64-bit)"),
                 "windows_client": ("windows11_64Guest", "Microsoft Windows 11 (64-bit)"),
                 "linux": ("rhel9_64Guest", "Red Hat Enterprise Linux 9 (64-bit)"),
                 "appliance": ("vmwarePhoton64Guest", "VMware Photon OS (64-bit)"),
                 "other_unknown": ("otherGuest64", "Other (64-bit)")}.get(cat, ("otherGuest64", "Other (64-bit)"))
        for i in range(n):
            host = ch[i % len(ch)].get("host") if ch else None
            vms.append({"vcenterKey": vck or (ch[0].get("vcenterKey") if ch else None), "cluster": cluster, "host": host,
                        "vm": f"planned-{cat}-{i + 1:03d}", "isPoweredOn": True, "powerState": "Powered On",
                        "vcpu": vcpu, "ramGB": ram, "diskProvGB": 0, "diskUsedGB": 0,
                        "guestId": guest[0], "guestFullName": guest[1], "toolsGuestFullName": guest[1],
                        "toolsGuestFamily": "windowsGuest" if cat.startswith("windows") else "linuxGuest",
                        "toolsStatus": "guestToolsRunning", "_planned": True, "_plannedCategory": cat})
        touched.append((vck, cluster))
        desc = f"Thêm {n} VM {cat} ({vcpu:.0f} vCPU / {ram:.0f} GB) vào {cluster}"

    elif st == "move_vms":
        src = scenario.get("sourceCluster") or cluster
        dst = scenario.get("targetCluster")
        src_vck = scenario.get("vcenterKey") or None
        dst_vck = scenario.get("targetVcenterKey") or src_vck
        cat = scenario.get("category") or "windows_server"
        n = scenario.get("count")
        cand = [v for v in _cluster_vms(vms, src_vck, src) if (v.get("os") or {}).get("category", classify_vm(v)["category"]) == cat]
        names = scenario.get("vms") or []
        if names:
            cand = [v for v in cand if v.get("vm") in names]
        elif n:
            cand = cand[:int(n)]
        dst_hosts = _cluster_hosts(hosts, dst_vck, dst)
        for i, v in enumerate(cand):
            v["cluster"] = dst
            if dst_vck:
                v["vcenterKey"] = dst_vck
            v["host"] = dst_hosts[i % len(dst_hosts)].get("host") if dst_hosts else None
            v["_moved"] = True
        touched.extend([(src_vck, src), (dst_vck, dst)])
        desc = f"Chuyển {len(cand)} VM {cat} từ {src} sang {dst}"
    else:
        raise ValueError(f"Loại kịch bản không hỗ trợ: {st}")

    return {"hosts": hosts, "vms": vms, "touched": touched, "description": desc}


def _delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    def d(path):
        b, a = before, after
        for p in path:
            b = (b or {}).get(p)
            a = (a or {}).get(p)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return {"before": b, "after": a, "delta": a - b}
        return {"before": b, "after": a, "delta": None}
    out = {
        "wsStandardCores": d(["windowsServer", "standard", "demandCores"]),
        "wsStandardCoresMax": d(["windowsServer", "standard", "demandCoresMax"]),
        "wsDatacenterCores": d(["windowsServer", "datacenter", "demandCores"]),
        "wsStandardShortfall": d(["windowsServer", "standard", "shortfallCores"]),
        "wsDatacenterShortfall": d(["windowsServer", "datacenter", "shortfallCores"]),
        "vmwareCores": d(["vmware", "demandCores"]),
        "vmwareShortfall": d(["vmware", "shortfallCores"]),
        "hosts": d(["totals", "hosts"]),
        "physicalCores": d(["totals", "physicalCores"]),
        "windowsServerVms": d(["totals", "windowsServerVms"]),
    }
    for key, path in (("wsStandardCost", ["windowsServer", "standard", "cost", "demandCost"]),
                      ("wsDatacenterCost", ["windowsServer", "datacenter", "cost", "demandCost"]),
                      ("vmwareCost", ["vmware", "cost", "demandCost"])):
        out[key] = d(path)
    return out


def run_whatif(hosts: List[Dict[str, Any]], vms_classified: List[Dict[str, Any]], rules: Dict[str, Dict[str, Any]],
               entitlements: List[Dict[str, Any]], scope: Dict[str, Any], policy: capacity.Policy,
               scenario: Dict[str, Any], classify_fn) -> Dict[str, Any]:
    before = assess(hosts, vms_classified, rules, entitlements, scope)
    applied = apply_scenario(hosts, vms_classified, scenario)
    # Re-classify any new/moved VMs (planned VMs have no override)
    after_vms = []
    for v in applied["vms"]:
        if v.get("_planned") or "os" not in v:
            v = dict(v)
            v["os"] = classify_fn(v)
        after_vms.append(v)
    after = assess(applied["hosts"], after_vms, rules, entitlements, scope)

    cap_before, cap_after, verdicts = [], [], []
    seen = set()
    for vck, cl in applied["touched"]:
        if (vck, cl) in seen or not cl:
            continue
        seen.add((vck, cl))
        b = _capacity_snapshot(hosts, vms_classified, policy, vck, cl)
        a = _capacity_snapshot(applied["hosts"], after_vms, policy, vck, cl)
        cap_before.append(b)
        cap_after.append(a)
        vd = _verdict(b, a)
        vd["cluster"] = cl
        verdicts.append(vd)
    worst = "ok"
    for vd in verdicts:
        if vd["level"] == "violation":
            worst = "violation"
        elif vd["level"] == "warn" and worst == "ok":
            worst = "warn"

    delta = _delta(before, after)
    insights: List[str] = []
    dv = delta["vmwareCores"]["delta"] or 0
    ds = delta["wsStandardCores"]["delta"] or 0
    dd = delta["wsDatacenterCores"]["delta"] or 0
    if dv:
        insights.append(f"Nhu cầu core VMware {'tăng' if dv > 0 else 'giảm'} {abs(dv):.0f} core.")
    if ds or dd:
        insights.append(f"Windows Server: Standard {'+' if ds >= 0 else '−'}{abs(ds):.0f} core, Datacenter {'+' if dd >= 0 else '−'}{abs(dd):.0f} core.")
    if worst == "violation":
        insights.append("Kịch bản vi phạm capacity/HA — KHÔNG đề xuất dù có tiết kiệm license.")
    elif worst == "warn":
        insights.append("Kịch bản đưa cluster vào vùng cảnh báo capacity — cần System Owner của cluster xác nhận.")
    cost_labels = {"wsStandardCost": "Windows Server Standard", "wsDatacenterCost": "Windows Server Datacenter", "vmwareCost": "VMware per-core"}
    for k, lbl in cost_labels.items():
        dd_ = delta[k]["delta"]
        if isinstance(dd_, (int, float)) and dd_:
            insights.append(f"Chi phí {lbl} (toàn bộ nhu cầu): {'+' if dd_ > 0 else '−'}{abs(dd_):,.0f}.")

    return {
        "scenario": scenario, "description": applied["description"],
        "before": _slim(before), "after": _slim(after), "delta": delta,
        "capacityBefore": cap_before, "capacityAfter": cap_after, "capacityVerdicts": verdicts,
        "capacityVerdict": worst, "recommendable": worst != "violation",
        "insights": insights,
    }


def _slim(res: Dict[str, Any]) -> Dict[str, Any]:
    return {"totals": res["totals"], "windowsServer": {k: res["windowsServer"][k] for k in ("standard", "datacenter", "recommendedEdition")},
            "vmware": {k: res["vmware"][k] for k in ("demandCores", "entitledCores", "shortfallCores", "status", "cost")},
            "clusters": [{k: c[k] for k in ("vcenterKey", "cluster", "hostCount", "wsVms", "unknownVms", "wsLicensableCores", "standard", "datacenter", "vmwareCores")} for c in res["clusters"]],
            "overallStatus": res["overallStatus"]}
