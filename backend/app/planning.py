"""Resource planning ("Quy hoạch tài nguyên"): project CPU/RAM demand under
assumed quarterly growth scenarios and work out how many physical nodes each
cluster must add (and when) to stay inside the overcommit policy while
keeping N+1 redundancy. Pure functions over the same host/VM rows the rest of
the app uses; no DB access here.

Vocabulary used by callers / the PDF:
  * scenario  -- {key, label, growthPct (per quarter), source: 'assumed'|'trend'}
  * checkpoint -- month offset from today (6, 12, ... horizon)
  * plan row  -- per cluster x scenario: nodes to add at each checkpoint, the
                 binding resource, first month a node is needed, order-by month
"""
import datetime as dt
import math
from typing import Any, Dict, List, Optional

from . import capacity
from .capacity import Policy

DEFAULT_GROWTH = (5.0, 10.0, 20.0)
SCENARIO_LABELS = ("Thấp", "Cơ sở", "Cao")
RAM_STEP_GB = 64  # DIMM upgrade granularity used for the "upgrade RAM" option


class PlanningParams:
    def __init__(self, horizon_months: int = 36, growth=DEFAULT_GROWTH, node_cores: Optional[int] = None,
                 node_ram_gb: Optional[int] = None, lead_months: int = 3, target_pct: float = 80.0,
                 base_index: int = 1):
        self.horizon_months = max(6, min(60, int(horizon_months or 36)))
        g = [float(x) for x in (growth or DEFAULT_GROWTH) if x is not None]
        self.growth = tuple(sorted(set(g)))[:4] or DEFAULT_GROWTH
        self.node_cores = int(node_cores) if node_cores else None
        self.node_ram_gb = int(node_ram_gb) if node_ram_gb else None
        self.lead_months = max(0, min(12, int(lead_months if lead_months is not None else 3)))
        self.target_pct = max(50.0, min(100.0, float(target_pct or 80.0)))
        # which assumed scenario is the "base case" used for the headline
        self.base_index = max(0, min(len(self.growth) - 1, int(base_index)))

    def checkpoints(self) -> List[int]:
        step = 6 if self.horizon_months <= 36 else 12
        pts = list(range(step, self.horizon_months + 1, step))
        if not pts or pts[-1] != self.horizon_months:
            pts.append(self.horizon_months)
        return pts


def _quarterly_growth_from_series(series: List[Dict[str, Any]], field: str) -> Optional[float]:
    """Observed compound growth per quarter of `field` between the first and
    last snapshot of a history series. None when not enough signal."""
    pts = [p for p in series if p.get(field) is not None and p.get("date")]
    if len(pts) < 2:
        return None
    first, last = pts[0], pts[-1]
    try:
        d0 = dt.date.fromisoformat(str(first["date"])[:10])
        d1 = dt.date.fromisoformat(str(last["date"])[:10])
    except ValueError:
        return None
    days = (d1 - d0).days
    a, b = float(first[field] or 0), float(last[field] or 0)
    if days < 30 or a <= 0 or b <= 0:
        return None
    ratio = b / a
    g = ratio ** (91.25 / days) - 1.0
    return round(g * 100.0, 1)


def trend_growth(history: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Observed quarterly growth of allocated vCPU / RAM from snapshot
    history (overall + per cluster). Returns None when history is missing."""
    if not history or not history.get("available"):
        return None
    overall = history.get("overall", {}).get("series", [])
    g_cpu = _quarterly_growth_from_series(overall, "usedVcpu")
    g_ram = _quarterly_growth_from_series(overall, "usedRamGB")
    if g_cpu is None and g_ram is None:
        return None
    per_cluster = {}
    for grp in history.get("groups", []):
        s = grp.get("series", [])
        per_cluster[grp.get("label") or grp.get("key")] = {
            "cpu": _quarterly_growth_from_series(s, "usedVcpu"),
            "ram": _quarterly_growth_from_series(s, "usedRamGB"),
        }
    return {"months": history.get("months"), "cpu": g_cpu, "ram": g_ram,
            "combined": max(x for x in (g_cpu, g_ram) if x is not None), "clusters": per_cluster}


def build_scenarios(params: PlanningParams, trend: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for i, g in enumerate(params.growth):
        label = SCENARIO_LABELS[i] if len(params.growth) == 3 and i < 3 else f"Kịch bản {i + 1}"
        out.append({"key": f"s{i}", "label": label, "growthPct": g, "source": "assumed",
                    "base": i == params.base_index})
    if trend and trend.get("combined") is not None and trend["combined"] > 0:
        out.append({"key": "trend", "label": f"Xu hướng {trend.get('months')} tháng", "growthPct": trend["combined"],
                    "source": "trend", "base": False, "cpuGrowth": trend.get("cpu"), "ramGrowth": trend.get("ram")})
    return out


def _grow(value: float, growth_pct: float, months: float) -> float:
    return value * (1.0 + growth_pct / 100.0) ** (months / 3.0)


def _node_spec_for_cluster(c_hosts: List[Dict[str, Any]], params: PlanningParams) -> Dict[str, Any]:
    """New-node spec: explicit from params, else the cluster's typical host
    (max of existing hosts so a new node is never smaller than the fleet)."""
    cores = params.node_cores
    ram = params.node_ram_gb
    src = "params"
    if not cores or not ram:
        src = "cluster"
        if c_hosts:
            cores = cores or max(int(h.get("cores") or 0) for h in c_hosts) or 32
            ram = ram or max(int(h.get("ramGB") or 0) for h in c_hosts) or 512
        else:
            cores, ram = cores or 32, ram or 512
    return {"cores": int(cores), "ramGB": int(ram), "source": src}


def _nodes_needed(demand_vcpu: float, demand_ram: float, cap_logical: float, cap_ram: float, n_hosts: int,
                  largest_logical: float, largest_ram: float, node_logical: float, node_ram: float,
                  policy: Policy, target_pct: float) -> Dict[str, Any]:
    """Smallest k >= 0 such that with k extra nodes:
       demand / (capacity * oc_max) <= target   (normal operation)
       demand / ((capacity - largest host) * oc_max) <= 100%   (N+1)
    for both CPU and RAM. Returns k plus which resource binds."""
    t = target_pct / 100.0

    def ok(k: int, demand: float, cap: float, largest: float, node: float, oc: float) -> bool:
        total = cap + k * node
        if total <= 0 or oc <= 0:
            return False
        if demand / (total * oc) > t + 1e-9:
            return False
        # N+1: lose the largest host (a new node may itself be the largest)
        big = max(largest, node) if (k > 0) else largest
        remaining = total - big
        if n_hosts + k < 2:
            return False  # single-node cluster never passes N+1
        return remaining > 0 and demand / (remaining * oc) <= 1.0 + 1e-9

    k_cpu = k_ram = None
    for k in range(0, 200):
        if k_cpu is None and ok(k, demand_vcpu, cap_logical, largest_logical, node_logical, policy.cpu_overcommit_max):
            k_cpu = k
        if k_ram is None and ok(k, demand_ram, cap_ram, largest_ram, node_ram, policy.ram_overcommit_max):
            k_ram = k
        if k_cpu is not None and k_ram is not None:
            break
    k_cpu = 200 if k_cpu is None else k_cpu
    k_ram = 200 if k_ram is None else k_ram
    k = max(k_cpu, k_ram)
    binding = "RAM" if k_ram > k_cpu else ("CPU" if k_cpu > k_ram else ("CPU+RAM" if k > 0 else "—"))
    return {"nodes": k, "nodesCpu": k_cpu, "nodesRam": k_ram, "binding": binding}


def _pct(demand: float, cap: float, oc: float) -> Optional[float]:
    if cap <= 0 or oc <= 0:
        return None
    return demand / (cap * oc) * 100.0


def _month_label(offset_months: int, today: Optional[dt.date] = None) -> str:
    today = today or dt.date.today()
    y, m = today.year, today.month + offset_months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{m:02d}/{y}"


def _quarter_label(offset_months: int, today: Optional[dt.date] = None) -> str:
    if offset_months <= 0:
        return "Ngay"
    today = today or dt.date.today()
    y, m = today.year, today.month + offset_months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"Q{(m - 1) // 3 + 1}/{y}"


def plan_cluster(name: str, c_hosts: List[Dict[str, Any]], c_vms: List[Dict[str, Any]], policy: Policy,
                 params: PlanningParams, scenarios: List[Dict[str, Any]], trend: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    model = capacity.build_capacity_model(c_hosts, c_vms, policy)
    ht = model["htFactor"]
    spec = _node_spec_for_cluster(c_hosts, params)
    node_logical = spec["cores"] * ht
    node_ram = float(spec["ramGB"])
    largest_logical = max(((h.get("cores") or 0) * ht for h in c_hosts), default=0.0)
    largest_ram = max((float(h.get("ramGB") or 0) for h in c_hosts), default=0.0)
    cap_logical, cap_ram = model["totalLogicalCores"], float(model["totalRamGB"])
    used_vcpu, used_ram = float(model["usedVcpu"]), float(model["usedRamGB"])
    n_hosts = len(c_hosts)
    checkpoints = params.checkpoints()

    cluster_trend = (trend or {}).get("clusters", {}).get(name) if trend else None

    sc_rows = []
    for sc in scenarios:
        g_cpu = g_ram = sc["growthPct"]
        if sc["source"] == "trend" and cluster_trend:
            # per-cluster observed growth when available, else overall trend
            g_cpu = cluster_trend.get("cpu") if cluster_trend.get("cpu") is not None else g_cpu
            g_ram = cluster_trend.get("ram") if cluster_trend.get("ram") is not None else g_ram
        g_cpu, g_ram = max(0.0, g_cpu), max(0.0, g_ram)
        points = []
        first_need = None
        for m in range(0, params.horizon_months + 1):
            d_cpu, d_ram = _grow(used_vcpu, g_cpu, m), _grow(used_ram, g_ram, m)
            need = _nodes_needed(d_cpu, d_ram, cap_logical, cap_ram, n_hosts, largest_logical, largest_ram,
                                 node_logical, node_ram, policy, params.target_pct)
            if first_need is None and need["nodes"] > 0:
                first_need = m
            points.append({"month": m, "demandVcpu": d_cpu, "demandRamGB": d_ram,
                           "cpuPct": _pct(d_cpu, cap_logical, policy.cpu_overcommit_max),
                           "ramPct": _pct(d_ram, cap_ram, policy.ram_overcommit_max), **need})
        cps = [points[m] for m in checkpoints if m < len(points)]
        final = points[params.horizon_months]
        # RAM-upgrade alternative: only when RAM alone drives the need
        ram_upgrade = None
        if final["nodesRam"] > final["nodesCpu"] and final["nodesCpu"] == 0 and n_hosts > 0:
            gap = final["demandRamGB"] / (policy.ram_overcommit_max * params.target_pct / 100.0) - cap_ram
            per_host = math.ceil(max(0.0, gap) / n_hosts / RAM_STEP_GB) * RAM_STEP_GB
            if per_host > 0:
                ram_upgrade = {"perHostGB": per_host, "totalGB": per_host * n_hosts, "hosts": n_hosts}
        sc_rows.append({
            "scenario": sc, "growthCpu": g_cpu, "growthRam": g_ram,
            "checkpoints": cps, "final": final, "firstNeedMonth": first_need,
            "orderByMonth": (None if first_need is None else max(0, first_need - params.lead_months)),
            "firstNeedLabel": (None if first_need is None else _quarter_label(first_need)),
            "orderByLabel": (None if first_need is None else _quarter_label(max(0, first_need - params.lead_months))),
            "ramUpgrade": ram_upgrade,
            "series": {"cpuPct": [p["cpuPct"] for p in points], "ramPct": [p["ramPct"] for p in points]},
        })

    ha = capacity.evaluate_ha_headroom(c_hosts, c_vms, policy)
    ha_row = ha[0] if ha else {"eligible": False, "hosts": n_hosts}
    return {
        "name": name, "hosts": n_hosts, "vms": len(c_vms), "poweredOn": model["poweredOnCount"],
        "model": model, "nodeSpec": spec, "ha": ha_row,
        "cpuStatus": capacity.get_status_for_pct(model["cpuPct"], 80, 100),
        "ramStatus": capacity.get_status_for_pct(model["ramPct"], 80, 100),
        "scenarios": sc_rows,
    }


def build_plan(hosts: List[Dict[str, Any]], vms: List[Dict[str, Any]], policy: Policy, params: PlanningParams,
               history: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    trend = trend_growth(history)
    scenarios = build_scenarios(params, trend)
    clusters = []
    for name in capacity.get_cluster_list(hosts, vms):
        c_hosts = [h for h in hosts if h.get("cluster") == name]
        c_vms = [v for v in vms if v.get("cluster") == name]
        if not c_hosts:
            continue  # VMs without hosts in scope: nothing physical to plan
        clusters.append(plan_cluster(name, c_hosts, c_vms, policy, params, scenarios, trend))

    model = capacity.build_capacity_model(hosts, vms, policy)
    checkpoints = params.checkpoints()
    # Totals per scenario (sum over clusters) at each checkpoint + horizon
    totals = []
    for i, sc in enumerate(scenarios):
        per_cp = []
        for j, m in enumerate(checkpoints):
            per_cp.append({"month": m, "nodes": sum(c["scenarios"][i]["checkpoints"][j]["nodes"] for c in clusters)})
        final_nodes = sum(c["scenarios"][i]["final"]["nodes"] for c in clusters)
        add_cores = sum(c["scenarios"][i]["final"]["nodes"] * c["nodeSpec"]["cores"] for c in clusters)
        add_ram = sum(c["scenarios"][i]["final"]["nodes"] * c["nodeSpec"]["ramGB"] for c in clusters)
        firsts = [c["scenarios"][i]["firstNeedMonth"] for c in clusters if c["scenarios"][i]["firstNeedMonth"] is not None]
        first = min(firsts) if firsts else None
        # Scope-level demand curve as % of threshold (sum of cluster demand / total capacity)
        cpu_curve, ram_curve = [], []
        for m in range(0, params.horizon_months + 1):
            d_cpu = sum(_grow(float(c["model"]["usedVcpu"]), c["scenarios"][i]["growthCpu"], m) for c in clusters)
            d_ram = sum(_grow(float(c["model"]["usedRamGB"]), c["scenarios"][i]["growthRam"], m) for c in clusters)
            cpu_curve.append(_pct(d_cpu, model["totalLogicalCores"], policy.cpu_overcommit_max))
            ram_curve.append(_pct(d_ram, float(model["totalRamGB"]), policy.ram_overcommit_max))
        totals.append({"scenario": sc, "checkpoints": per_cp, "finalNodes": final_nodes, "addCores": add_cores,
                       "addRamGB": add_ram, "firstNeedMonth": first,
                       "firstNeedLabel": (None if first is None else _quarter_label(first)),
                       "orderByLabel": (None if first is None else _quarter_label(max(0, first - params.lead_months))),
                       "series": {"cpuPct": cpu_curve, "ramPct": ram_curve}})

    base_idx = next((i for i, sc in enumerate(scenarios) if sc.get("base")), 0)
    return {
        "params": {"horizonMonths": params.horizon_months, "growth": list(params.growth), "nodeCores": params.node_cores,
                   "nodeRamGB": params.node_ram_gb, "leadMonths": params.lead_months, "targetPct": params.target_pct,
                   "checkpoints": checkpoints},
        "policy": {"cpuOvercommitMax": policy.cpu_overcommit_max, "ramOvercommitMax": policy.ram_overcommit_max,
                   "htFactor": policy.ht_factor},
        "model": model, "trend": trend, "scenarios": scenarios, "baseIndex": base_idx,
        "clusters": clusters, "totals": totals,
        "monthLabels": [_month_label(m) for m in range(0, params.horizon_months + 1)],
    }


def plan_recommendations(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Headline + short decision list for the top summary box. Base scenario
    drives the asks; the high scenario is a contingency note."""
    base_i = plan["baseIndex"]
    scenarios = plan["scenarios"]
    high_i = max(range(len(scenarios)), key=lambda i: scenarios[i]["growthPct"] if scenarios[i]["source"] == "assumed" else -1)
    horizon = plan["params"]["horizonMonths"]
    base_total = plan["totals"][base_i]
    high_total = plan["totals"][high_i]
    recs: List[Dict[str, Any]] = []

    def prio_for(month: Optional[int]) -> str:
        if month is None:
            return "thap"
        return "cao" if month <= 12 else ("tb" if month <= 24 else "thap")

    need_clusters = []
    for c in plan["clusters"]:
        sc = c["scenarios"][base_i]
        if sc["final"]["nodes"] > 0:
            need_clusters.append((sc["firstNeedMonth"] if sc["firstNeedMonth"] is not None else horizon, c, sc))
    need_clusters.sort(key=lambda t: t[0])
    for _, c, sc in need_clusters:
        n = sc["final"]["nodes"]
        spec = c["nodeSpec"]
        alt = ""
        if sc["ramUpgrade"]:
            alt = f"; phương án thay thế: nâng RAM +{sc['ramUpgrade']['perHostGB']} GB/host × {sc['ramUpgrade']['hosts']} host"
        if sc["firstNeedMonth"] == 0:
            timing = "đặt hàng ngay — hiện đã vượt mục tiêu hoặc không còn dự phòng N+1"
        else:
            timing = f"đặt hàng trước {sc['orderByLabel']} (cần vận hành từ {sc['firstNeedLabel']})"
        recs.append({
            "priority": prio_for(sc["firstNeedMonth"]),
            "title": f"{c['name']}: bổ sung {n} node ({spec['cores']} cores / {spec['ramGB']} GB) trong {horizon} tháng",
            "decision": f"Phê duyệt ngân sách {n} node; {timing}{alt}",
            "timeframe": sc["orderByLabel"] or "—",
            "binding": sc["final"]["binding"],
        })
    extra_high = high_total["finalNodes"] - base_total["finalNodes"]
    if extra_high > 0:
        recs.append({
            "priority": "info",
            "title": f"Kịch bản cao (+{scenarios[high_i]['growthPct']:.0f}%/quý): cần thêm {extra_high} node so với cơ sở",
            "decision": "Chuẩn bị phương án dự phòng, chưa cần phê duyệt; rà soát lại mỗi quý theo tăng trưởng thực tế",
            "timeframe": "Rà soát hằng quý", "binding": "",
        })
    no_need = [c["name"] for c in plan["clusters"] if c["scenarios"][base_i]["final"]["nodes"] == 0]
    if no_need:
        recs.append({
            "priority": "info",
            "title": f"{', '.join(no_need)}: đủ năng lực trong {horizon} tháng (kịch bản cơ sở)",
            "decision": "Không cần ngân sách phần cứng; giữ ngưỡng và N+1 hiện tại",
            "timeframe": "—", "binding": "",
        })
    if plan["trend"] is None:
        recs.append({
            "priority": "info",
            "title": "Chưa có lịch sử snapshot đủ dài để tính xu hướng thực tế",
            "decision": "Dự phóng dựa trên giả định; cập nhật lại báo cáo sau 1–2 quý thu thập dữ liệu",
            "timeframe": "—", "binding": "",
        })

    total_nodes = base_total["finalNodes"]
    if total_nodes > 0:
        status_cls = "crit" if (base_total["firstNeedMonth"] is not None and base_total["firstNeedMonth"] <= 12) else "warn"
        status_label = "CẦN MỞ RỘNG" if status_cls == "crit" else "LẬP KẾ HOẠCH"
        if base_total["firstNeedMonth"] == 0:
            when = "đã thiếu năng lực ngay hiện tại (vượt mục tiêu hoặc mất N+1) — cần đặt hàng ngay."
        else:
            when = (f"node đầu tiên cần vận hành từ {base_total['firstNeedLabel']}, "
                    f"đặt hàng trước {base_total['orderByLabel']}.")
        headline = (f"Cần bổ sung {total_nodes} node máy chủ trong {horizon} tháng tới (kịch bản cơ sở "
                    f"+{scenarios[base_i]['growthPct']:.0f}%/quý); {when}")
    else:
        status_cls, status_label = "ok", "ĐỦ NĂNG LỰC"
        headline = (f"Không cần bổ sung node trong {horizon} tháng tới theo kịch bản cơ sở "
                    f"(+{scenarios[base_i]['growthPct']:.0f}%/quý)"
                    + (f"; kịch bản cao cần {high_total['finalNodes']} node." if high_total["finalNodes"] else "."))
    return {"headline": headline, "statusCls": status_cls, "statusLabel": status_label, "recommendations": recs[:6]}
