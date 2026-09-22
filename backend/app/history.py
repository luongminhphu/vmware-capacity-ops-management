"""Snapshot history: CPU/RAM change over time per vCenter / cluster, and the
list of VMs added / removed inside a look-back window.

Everything here is derived from the persisted snapshot tables
(ClusterAggregate for capacity, VmSnapshot for VM inventory) so it works the
same for live vCenter polls and manual imports (vcenter_key = None, shown as
"Import thủ công"). The same functions feed the web dashboard endpoints and
the Executive / Technical PDF reports so numbers always agree.
"""
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import capacity
from .models import ClusterAggregate, SnapshotRun, VmSnapshot
from .timeutil import to_iso_utc, to_vn_date

ALLOWED_MONTHS = (3, 6, 12)
IMPORT_LABEL = "Import thủ công"


def normalize_months(value: Optional[int]) -> int:
    try:
        m = int(value) if value is not None else 12
    except (TypeError, ValueError):
        m = 12
    return m if m in ALLOWED_MONTHS else 12


def window_start(months: int, now: Optional[dt.datetime] = None) -> dt.datetime:
    now = now or dt.datetime.utcnow()
    # Calendar-ish months (30.44 days) keep "3 tháng" meaning ~1 quarter.
    return now - dt.timedelta(days=round(months * 30.44))


def _finite(x: Optional[float]) -> bool:
    return x is not None and x == x and x not in (float("inf"), float("-inf"))


def vcenter_label(key: Optional[str]) -> str:
    """Display name for a vCenter key (configured name, falling back to the key)."""
    if not key:
        return IMPORT_LABEL
    try:
        from .config import get_settings
        for cfg in get_settings().vcenters():
            if cfg.key == key:
                return cfg.name or key
    except Exception:  # pragma: no cover - settings unavailable in odd contexts
        pass
    return key


def _group_key(row: ClusterAggregate, group_by: str) -> Tuple[str, str]:
    vc = row.vcenter_key or "import"
    vc_label = vcenter_label(row.vcenter_key)
    if group_by == "cluster":
        return f"{vc}::{row.cluster}", f"{row.cluster} ({vc_label})"
    return vc, vc_label


def _pct(used: float, total: float, oc_max: float) -> Optional[float]:
    if total <= 0 or oc_max <= 0:
        return None
    return round(used / total / oc_max * 100.0, 1)


def get_resource_history(db: Session, vcenter_keys: Optional[List[str]], months: int,
                         group_by: str, policy: capacity.Policy,
                         include_import: bool = True) -> Dict[str, Any]:
    """Daily CPU/RAM series per group (vCenter or cluster) plus an org-wide
    "Toàn bộ" series and a first-vs-last delta summary per group.

    For each (group, cluster, VN-calendar day) only the latest run of that day
    is kept so multiple polls per day do not double count."""
    months = normalize_months(months)
    group_by = "cluster" if group_by == "cluster" else "vcenter"
    since = window_start(months)
    q = db.query(ClusterAggregate).join(SnapshotRun, SnapshotRun.id == ClusterAggregate.run_id) \
        .filter(ClusterAggregate.captured_at >= since, SnapshotRun.status == "ok")
    if vcenter_keys is not None:
        q = q.filter(ClusterAggregate.vcenter_key.in_(vcenter_keys))
    elif not include_import:
        q = q.filter(ClusterAggregate.vcenter_key.isnot(None))
    rows = q.order_by(ClusterAggregate.captured_at.asc(), ClusterAggregate.run_id.asc()).all()

    # (group_key, day, vcenter, cluster) -> latest row that day
    latest: Dict[Tuple[str, str, str, str], ClusterAggregate] = {}
    labels: Dict[str, str] = {}
    for r in rows:
        gkey, glabel = _group_key(r, group_by)
        labels[gkey] = glabel
        day = to_vn_date(r.captured_at).isoformat()
        latest[(gkey, day, r.vcenter_key or "import", r.cluster)] = r

    # Aggregate per group/day and org-wide per day.
    per_group: Dict[str, Dict[str, Dict[str, float]]] = {}
    overall: Dict[str, Dict[str, float]] = {}

    def _bucket(store: Dict[str, Dict[str, float]], day: str) -> Dict[str, float]:
        return store.setdefault(day, {"usedVcpu": 0.0, "totalLogicalCores": 0.0, "usedRamGB": 0.0,
                                      "totalRamGB": 0.0, "hostCount": 0, "vmCount": 0})

    for (gkey, day, _vc, _cl), r in latest.items():
        for store in (per_group.setdefault(gkey, {}), overall):
            b = _bucket(store, day)
            b["usedVcpu"] += r.used_vcpu or 0
            b["totalLogicalCores"] += r.total_logical_cores or 0
            b["usedRamGB"] += r.used_ram_gb or 0
            b["totalRamGB"] += r.total_ram_gb or 0
            b["hostCount"] += r.host_count or 0
            b["vmCount"] += r.vm_count or 0

    cpu_oc, ram_oc = policy.cpu_overcommit_max, policy.ram_overcommit_max

    def _series(store: Dict[str, Dict[str, float]]) -> List[Dict[str, Any]]:
        out = []
        for day in sorted(store):
            b = store[day]
            out.append({
                "date": day,
                "usedVcpu": round(b["usedVcpu"], 1), "totalLogicalCores": round(b["totalLogicalCores"], 1),
                "usedRamGB": round(b["usedRamGB"], 1), "totalRamGB": round(b["totalRamGB"], 1),
                "hostCount": int(b["hostCount"]), "vmCount": int(b["vmCount"]),
                "cpuPct": _pct(b["usedVcpu"], b["totalLogicalCores"], cpu_oc),
                "ramPct": _pct(b["usedRamGB"], b["totalRamGB"], ram_oc),
            })
        return out

    def _summary(key: str, label: str, series: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not series:
            return {"key": key, "label": label, "points": 0}
        first, last = series[0], series[-1]

        def _delta(field: str) -> Optional[float]:
            a, b = first.get(field), last.get(field)
            if a is None or b is None:
                return None
            return round(b - a, 1)

        peak_cpu = max((p["cpuPct"] for p in series if p["cpuPct"] is not None), default=None)
        peak_ram = max((p["ramPct"] for p in series if p["ramPct"] is not None), default=None)
        return {
            "key": key, "label": label, "points": len(series),
            "firstDate": first["date"], "lastDate": last["date"],
            "first": first, "last": last,
            "delta": {f: _delta(f) for f in ("cpuPct", "ramPct", "usedVcpu", "usedRamGB",
                                             "totalLogicalCores", "totalRamGB", "hostCount", "vmCount")},
            "peakCpuPct": peak_cpu, "peakRamPct": peak_ram,
        }

    groups = []
    for gkey in sorted(per_group, key=lambda k: labels.get(k, k)):
        s = _series(per_group[gkey])
        groups.append({"key": gkey, "label": labels.get(gkey, gkey), "series": s,
                       "summary": _summary(gkey, labels.get(gkey, gkey), s)})
    overall_series = _series(overall)
    return {
        "months": months, "groupBy": group_by,
        "since": to_iso_utc(since), "until": to_iso_utc(dt.datetime.utcnow()),
        "available": len(overall_series) >= 2,
        "pointCount": len(overall_series),
        "overall": {"key": "all", "label": "Toàn bộ", "series": overall_series,
                    "summary": _summary("all", "Toàn bộ", overall_series)},
        "groups": groups,
    }


def _vm_row(v: VmSnapshot) -> Dict[str, Any]:
    return {
        "vcenterKey": v.vcenter_key or "import",
        "vcenterLabel": vcenter_label(v.vcenter_key),
        "cluster": v.cluster, "host": v.host, "vm": v.vm,
        "powerState": v.power_state, "isPoweredOn": bool(v.is_powered_on),
        "vcpu": v.vcpu or 0, "ramGB": v.ram_gb or 0, "diskProvGB": v.disk_prov_gb or 0,
    }


def get_vm_changes(db: Session, vcenter_keys: Optional[List[str]], months: int,
                   include_import: bool = True) -> Dict[str, Any]:
    """VMs added / removed inside the window, per vCenter.

    Baseline = earliest successful run inside the window; current = latest
    successful run. A VM present now but not in the baseline is "added"
    (first-seen date taken from its earliest snapshot in the window); a VM in
    the baseline but absent now is "removed" (last-seen date = the last
    snapshot that still contained it)."""
    months = normalize_months(months)
    since = window_start(months)

    if vcenter_keys is not None:
        scopes: List[Optional[str]] = list(vcenter_keys)
    else:
        scopes = [r[0] for r in db.query(SnapshotRun.vcenter_key)
                  .filter(SnapshotRun.vcenter_key.isnot(None)).distinct().all()]
        if include_import:
            scopes.append(None)

    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    per_scope: List[Dict[str, Any]] = []

    for key in scopes:
        base_q = db.query(SnapshotRun).filter(SnapshotRun.status == "ok", SnapshotRun.finished_at >= since)
        base_q = base_q.filter(SnapshotRun.vcenter_key.is_(None)) if key is None else base_q.filter(SnapshotRun.vcenter_key == key)
        first_run = base_q.order_by(SnapshotRun.finished_at.asc(), SnapshotRun.id.asc()).first()
        last_run = base_q.order_by(SnapshotRun.finished_at.desc(), SnapshotRun.id.desc()).first()
        if not first_run or not last_run or first_run.id == last_run.id:
            per_scope.append({"vcenterKey": key or "import", "vcenterLabel": vcenter_label(key),
                              "available": False, "runs": 1 if first_run else 0})
            continue

        base_vms = {v.vm: v for v in first_run.vms}
        cur_vms = {v.vm: v for v in last_run.vms}

        # first/last seen per VM name inside the window (one grouped query).
        seen_q = db.query(VmSnapshot.vm, func.min(VmSnapshot.captured_at), func.max(VmSnapshot.captured_at)) \
            .filter(VmSnapshot.captured_at >= since)
        seen_q = seen_q.filter(VmSnapshot.vcenter_key.is_(None)) if key is None else seen_q.filter(VmSnapshot.vcenter_key == key)
        seen = {name: (mn, mx) for name, mn, mx in seen_q.group_by(VmSnapshot.vm).all()}

        for name, v in sorted(cur_vms.items()):
            if name not in base_vms:
                row = _vm_row(v)
                row["firstSeen"] = to_iso_utc(seen.get(name, (v.captured_at, None))[0])
                added.append(row)
        for name, v in sorted(base_vms.items()):
            if name not in cur_vms:
                row = _vm_row(v)
                row["lastSeen"] = to_iso_utc(seen.get(name, (None, v.captured_at))[1])
                removed.append(row)

        per_scope.append({
            "vcenterKey": key or "import", "vcenterLabel": vcenter_label(key), "available": True,
            "baselineAt": to_iso_utc(first_run.finished_at), "currentAt": to_iso_utc(last_run.finished_at),
            "baselineVmCount": len(base_vms), "currentVmCount": len(cur_vms),
            "added": sum(1 for r in added if r["vcenterKey"] == (key or "import")),
            "removed": sum(1 for r in removed if r["vcenterKey"] == (key or "import")),
        })

    def _totals(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        return {"count": len(rows), "vcpu": round(sum(r["vcpu"] for r in rows), 0),
                "ramGB": round(sum(r["ramGB"] for r in rows), 0),
                "poweredOn": sum(1 for r in rows if r["isPoweredOn"])}

    return {
        "months": months, "since": to_iso_utc(since),
        "available": any(s.get("available") for s in per_scope),
        "scopes": per_scope,
        "added": added, "removed": removed,
        "addedTotals": _totals(added), "removedTotals": _totals(removed),
    }


def downsample(series: List[Dict[str, Any]], max_points: int = 60) -> List[Dict[str, Any]]:
    """Thin a daily series for compact PDF charts, always keeping the last point."""
    if len(series) <= max_points:
        return series
    step = len(series) / float(max_points)
    picked = [series[int(i * step)] for i in range(max_points)]
    if picked[-1] is not series[-1]:
        picked.append(series[-1])
    return picked
