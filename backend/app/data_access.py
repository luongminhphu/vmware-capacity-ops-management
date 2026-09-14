"""
Shared query helpers: turn persisted SnapshotRun rows back into the camelCase
row dicts the frontend/capacity engine expects, and figure out "latest data
per source" (each live vCenter's most recent successful run, plus the most
recent manual import, combined).
"""
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from . import capacity
from .config import Settings
from .models import AppSetting, ComplianceFindingSnapshot, HostSnapshot, SnapshotRun, VmSnapshot


def get_effective_policy(db: Session, settings: Settings) -> "capacity.Policy":
    """Merge env-var defaults with any admin-adjusted thresholds stored in the
    app_settings table (see routers/settings.py)."""
    overrides = {row.key: row.value for row in db.query(AppSetting).all()}
    return capacity.Policy(
        cpu_overcommit_max=float(overrides.get("cpu_overcommit_max", settings.cpu_overcommit_max)),
        ram_overcommit_max=float(overrides.get("ram_overcommit_max", settings.ram_overcommit_max)),
        ht_factor=float(overrides.get("ht_factor", settings.ht_factor)),
        idle_days=int(float(overrides.get("idle_days", settings.idle_days))),
    )


def _host_to_row(h: HostSnapshot) -> Dict[str, Any]:
    return {
        "cluster": h.cluster, "host": h.host, "cores": h.cores, "cpuMhz": h.cpu_mhz,
        "ramGB": h.ram_gb, "cpuUsagePctRaw": h.cpu_usage_pct_raw, "ramUsagePctRaw": h.ram_usage_pct_raw,
        "status": h.status, "vcenterKey": h.vcenter_key,
    }


def _vm_to_row(v: VmSnapshot) -> Dict[str, Any]:
    return {
        "cluster": v.cluster, "host": v.host, "vm": v.vm, "powerState": v.power_state,
        "isPoweredOn": v.is_powered_on, "ipAddress": v.ip_address, "vcpu": v.vcpu, "ramGB": v.ram_gb,
        "diskProvGB": v.disk_prov_gb, "diskUsedGB": v.disk_used_gb, "datastore": v.datastore,
        "cpuUsagePctRaw": v.cpu_usage_pct_raw, "ramUsagePctRaw": v.ram_usage_pct_raw,
        "lastActivityDays": v.last_activity_days, "powerOffDays": v.power_off_days,
        "vcenterKey": v.vcenter_key,
    }


def latest_run_for_key(db: Session, vcenter_key: Optional[str], source: Optional[str] = None) -> Optional[SnapshotRun]:
    q = db.query(SnapshotRun).filter(SnapshotRun.status == "ok")
    if vcenter_key is None:
        q = q.filter(SnapshotRun.vcenter_key.is_(None))
    else:
        q = q.filter(SnapshotRun.vcenter_key == vcenter_key)
    if source:
        q = q.filter(SnapshotRun.source == source)
    return q.order_by(SnapshotRun.finished_at.desc().nullslast(), SnapshotRun.id.desc()).first()


def known_vcenter_keys(db: Session) -> List[str]:
    rows = db.query(SnapshotRun.vcenter_key).filter(SnapshotRun.vcenter_key.isnot(None)).distinct().all()
    return [r[0] for r in rows]


def get_latest_combined(db: Session, vcenter_keys: Optional[List[str]] = None, include_import: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Returns (host_rows, vm_rows, run_meta) combining the latest successful run
    for each requested vCenter key (default: all known keys) plus, optionally,
    the latest manual import run. run_meta lists each contributing run's
    {vcenterKey, source, finishedAt} for UI freshness display."""
    keys = vcenter_keys if vcenter_keys is not None else known_vcenter_keys(db)
    host_rows: List[Dict[str, Any]] = []
    vm_rows: List[Dict[str, Any]] = []
    run_meta: List[Dict[str, Any]] = []

    for key in keys:
        run = latest_run_for_key(db, key, source="live")
        if not run:
            continue
        host_rows.extend(_host_to_row(h) for h in run.hosts)
        vm_rows.extend(_vm_to_row(v) for v in run.vms)
        run_meta.append({"vcenterKey": key, "source": "live", "finishedAt": run.finished_at.isoformat() if run.finished_at else None})

    if include_import:
        run = latest_run_for_key(db, None, source="import")
        if run:
            host_rows.extend(_host_to_row(h) for h in run.hosts)
            vm_rows.extend(_vm_to_row(v) for v in run.vms)
            run_meta.append({"vcenterKey": "import", "source": "import", "finishedAt": run.finished_at.isoformat() if run.finished_at else None})

    return host_rows, vm_rows, run_meta


def get_latest_grouped(db: Session, include_import: bool = True) -> Dict[str, Any]:
    """Like get_latest_combined, but also returns a byVcenter breakdown so the
    frontend can compute per-vCenter KPIs (Executive view) without N round
    trips."""
    keys = known_vcenter_keys(db)
    host_rows: List[Dict[str, Any]] = []
    vm_rows: List[Dict[str, Any]] = []
    run_meta: List[Dict[str, Any]] = []
    by_vcenter: Dict[str, Any] = {}

    for key in keys:
        run = latest_run_for_key(db, key, source="live")
        if not run:
            continue
        h = [_host_to_row(x) for x in run.hosts]
        v = [_vm_to_row(x) for x in run.vms]
        host_rows.extend(h)
        vm_rows.extend(v)
        by_vcenter[key] = {"hosts": h, "vms": v}
        run_meta.append({"vcenterKey": key, "source": "live", "finishedAt": run.finished_at.isoformat() if run.finished_at else None})

    if include_import:
        run = latest_run_for_key(db, None, source="import")
        if run:
            h = [_host_to_row(x) for x in run.hosts]
            v = [_vm_to_row(x) for x in run.vms]
            host_rows.extend(h)
            vm_rows.extend(v)
            by_vcenter["import"] = {"hosts": h, "vms": v}
            run_meta.append({"vcenterKey": "import", "source": "import", "finishedAt": run.finished_at.isoformat() if run.finished_at else None})

    return {"hosts": host_rows, "vms": vm_rows, "byVcenter": by_vcenter, "runMeta": run_meta}


def get_findings_for_run(db: Session, run: SnapshotRun) -> List[Dict[str, Any]]:
    return [
        {
            "severity": f.severity, "type": f.type, "typeLabel": f.type_label, "entity": f.entity,
            "cluster": f.cluster, "metric": f.metric, "recommendation": f.recommendation,
        }
        for f in run.findings
    ]
