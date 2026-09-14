import asyncio
import datetime as dt
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .. import capacity
from ..config import Settings, VCenterConfig, get_settings
from ..database import SessionLocal
from ..models import (
    ClusterAggregate, ComplianceFindingSnapshot, HostSnapshot, SnapshotRun, VmSnapshot,
)
from ..vcenter import client as vcenter_client
from ..vcenter import demo_generator

logger = logging.getLogger("vco.scheduler")


def _policy_from_settings(s: Settings) -> capacity.Policy:
    return capacity.Policy(
        cpu_overcommit_max=s.cpu_overcommit_max,
        ram_overcommit_max=s.ram_overcommit_max,
        ht_factor=s.ht_factor,
        idle_days=s.idle_days,
    )


def _enrich_activity(db: Session, vcenter_key: str, vm_rows: List[Dict[str, Any]]) -> None:
    """Fill lastActivityDays / powerOffDays from OUR OWN polling history, since a
    single vCenter snapshot has no concept of 'idle since'. We look at this VM's
    most recent snapshots and find how long it has continuously been idle/off."""
    idle_cpu_threshold = 5.0
    for v in vm_rows:
        vm_name = v.get("vm")
        if v.get("isPoweredOn"):
            if v.get("cpuUsagePctRaw") is not None and v["cpuUsagePctRaw"] >= idle_cpu_threshold:
                v["lastActivityDays"] = 0
                continue
            history = (
                db.query(VmSnapshot)
                .filter(VmSnapshot.vcenter_key == vcenter_key, VmSnapshot.vm == vm_name)
                .order_by(VmSnapshot.captured_at.desc())
                .limit(500)
                .all()
            )
            first_idle_at = None
            for h in history:
                if h.is_powered_on and (h.cpu_usage_pct_raw or 0) < idle_cpu_threshold:
                    first_idle_at = h.captured_at
                else:
                    break
            if first_idle_at:
                v["lastActivityDays"] = max(0.0, (dt.datetime.utcnow() - first_idle_at).total_seconds() / 86400.0)
            else:
                v["lastActivityDays"] = 0
        else:
            history = (
                db.query(VmSnapshot)
                .filter(VmSnapshot.vcenter_key == vcenter_key, VmSnapshot.vm == vm_name)
                .order_by(VmSnapshot.captured_at.desc())
                .limit(500)
                .all()
            )
            first_off_at = None
            for h in history:
                if not h.is_powered_on:
                    first_off_at = h.captured_at
                else:
                    break
            if first_off_at:
                v["powerOffDays"] = max(0.0, (dt.datetime.utcnow() - first_off_at).total_seconds() / 86400.0)
            else:
                v["powerOffDays"] = 0


def sync_vcenter(db: Session, cfg: VCenterConfig, settings: Optional[Settings] = None) -> SnapshotRun:
    """Query one vCenter (or generate demo data), persist a full SnapshotRun, and
    return it. Raises vcenter_client.VCenterConnectionError on connection failure
    (the run is still persisted with status='error' for visibility)."""
    settings = settings or get_settings()
    run = SnapshotRun(vcenter_key=cfg.key, source="live", status="ok", started_at=dt.datetime.utcnow())
    db.add(run)
    db.flush()

    try:
        if cfg.demo:
            host_rows, vm_rows = demo_generator.collect_inventory(cfg)
        else:
            host_rows, vm_rows = vcenter_client.collect_inventory(cfg)

        _enrich_activity(db, cfg.key, vm_rows)

        policy = _policy_from_settings(settings)

        for h in host_rows:
            db.add(HostSnapshot(
                run_id=run.id, vcenter_key=cfg.key, cluster=h["cluster"], host=h["host"],
                cores=h["cores"], cpu_mhz=h.get("cpuMhz", 0), ram_gb=h["ramGB"],
                cpu_usage_pct_raw=h.get("cpuUsagePctRaw"), ram_usage_pct_raw=h.get("ramUsagePctRaw"),
                status=h.get("status", "connected"),
            ))
        for v in vm_rows:
            db.add(VmSnapshot(
                run_id=run.id, vcenter_key=cfg.key, cluster=v["cluster"], host=v.get("host"),
                vm=v["vm"], power_state=v["powerState"], is_powered_on=v["isPoweredOn"],
                ip_address=v.get("ipAddress"), vcpu=v["vcpu"], ram_gb=v["ramGB"],
                disk_prov_gb=v["diskProvGB"], disk_used_gb=v["diskUsedGB"], datastore=v["datastore"],
                cpu_usage_pct_raw=v.get("cpuUsagePctRaw"), ram_usage_pct_raw=v.get("ramUsagePctRaw"),
                last_activity_days=v.get("lastActivityDays"), power_off_days=v.get("powerOffDays"),
            ))

        cluster_rows = capacity.build_cluster_rows(host_rows, vm_rows, policy)
        for c in cluster_rows:
            m = c["model"]
            db.add(ClusterAggregate(
                run_id=run.id, vcenter_key=cfg.key, cluster=c["name"],
                host_count=c["hosts"], vm_count=c["vms"],
                total_cores=m["totalCores"], total_logical_cores=m["totalLogicalCores"],
                total_ram_gb=m["totalRamGB"], used_vcpu=m["usedVcpu"], used_ram_gb=m["usedRamGB"],
                prov_storage_gb=m["provStorageGB"], used_storage_gb=m["usedStorageGB"],
                cpu_overcommit=_safe(m["cpuOvercommit"]), ram_overcommit=_safe(m["ramOvercommit"]),
                cpu_pct=_safe(m["cpuPct"]), ram_pct=_safe(m["ramPct"]),
            ))

        findings = capacity.build_compliance_findings(host_rows, vm_rows, policy)
        for f in findings:
            db.add(ComplianceFindingSnapshot(
                run_id=run.id, vcenter_key=cfg.key, severity=f["severity"], type=f["type"],
                type_label=f.get("typeLabel"), entity=f.get("entity"), cluster=f.get("cluster"),
                metric=f.get("metric"), recommendation=f.get("recommendation"),
            ))

        run.status = "ok"
        run.finished_at = dt.datetime.utcnow()
        db.commit()
        logger.info("Synced vCenter %s: %d hosts, %d vms, %d findings", cfg.key, len(host_rows), len(vm_rows), len(findings))
        return run
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.merge(run)
        run.status = "error"
        run.error_message = str(exc)
        run.finished_at = dt.datetime.utcnow()
        db.add(run)
        db.commit()
        logger.exception("Sync failed for vCenter %s", cfg.key)
        raise


def _safe(v):
    import math
    return v if (v is not None and math.isfinite(v)) else None


async def scheduler_loop(app_settings: Settings):
    """Background task started at FastAPI startup: polls every configured
    vCenter on a fixed interval so Executive-view trend charts keep filling in
    even when nobody has the dashboard open."""
    interval = max(5, app_settings.poll_interval_minutes) * 60
    while True:
        vcenters = app_settings.vcenters()
        for cfg in vcenters:
            db = SessionLocal()
            try:
                sync_vcenter(db, cfg, app_settings)
            except Exception:  # noqa: BLE001
                logger.warning("Scheduled sync failed for %s (will retry next cycle)", cfg.key)
            finally:
                db.close()
        await asyncio.sleep(interval)
