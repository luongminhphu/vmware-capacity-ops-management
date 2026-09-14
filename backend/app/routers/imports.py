import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import capacity, data_access
from ..config import get_settings
from ..database import get_db
from ..models import (
    ClusterAggregate, ComplianceFindingSnapshot, HostSnapshot, SnapshotRun, User, VmSnapshot,
)
from ..schemas import ImportPayload
from ..security import get_current_user

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("")
def import_data(payload: ImportPayload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Persist a manually-imported CSV/Excel dataset (already parsed & normalized
    client-side, reusing the original app's normalizeHostRow/normalizeVmRow
    logic) as a SnapshotRun with source='import', so it also feeds the
    Executive-view historical trend charts alongside live vCenter polls."""
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)

    run = SnapshotRun(vcenter_key=None, source="import", status="ok", started_at=dt.datetime.utcnow())
    db.add(run)
    db.flush()

    host_rows = payload.hosts
    vm_rows = payload.vms

    for h in host_rows:
        db.add(HostSnapshot(
            run_id=run.id, vcenter_key=None, cluster=h.get("cluster", "UNKNOWN"), host=h.get("host", "unknown"),
            cores=h.get("cores", 0) or 0, cpu_mhz=h.get("cpuMhz", 0) or 0, ram_gb=h.get("ramGB", 0) or 0,
            cpu_usage_pct_raw=h.get("cpuUsagePctRaw"), ram_usage_pct_raw=h.get("ramUsagePctRaw"),
            status=h.get("status", "imported"),
        ))
    for v in vm_rows:
        db.add(VmSnapshot(
            run_id=run.id, vcenter_key=None, cluster=v.get("cluster", "UNKNOWN"), host=v.get("host"),
            vm=v.get("vm", "unknown"), power_state=v.get("powerState", "Powered Off"),
            is_powered_on=bool(v.get("isPoweredOn")), ip_address=v.get("ipAddress"),
            vcpu=v.get("vcpu", 0) or 0, ram_gb=v.get("ramGB", 0) or 0,
            disk_prov_gb=v.get("diskProvGB", 0) or 0, disk_used_gb=v.get("diskUsedGB", 0) or 0,
            datastore=v.get("datastore", "UNKNOWN"),
            cpu_usage_pct_raw=v.get("cpuUsagePctRaw"), ram_usage_pct_raw=v.get("ramUsagePctRaw"),
            last_activity_days=v.get("lastActivityDays"), power_off_days=v.get("powerOffDays"),
        ))

    cluster_rows = capacity.build_cluster_rows(host_rows, vm_rows, policy)
    for c in cluster_rows:
        m = c["model"]
        db.add(ClusterAggregate(
            run_id=run.id, vcenter_key=None, cluster=c["name"], host_count=c["hosts"], vm_count=c["vms"],
            total_cores=m["totalCores"], total_logical_cores=m["totalLogicalCores"], total_ram_gb=m["totalRamGB"],
            used_vcpu=m["usedVcpu"], used_ram_gb=m["usedRamGB"], prov_storage_gb=m["provStorageGB"],
            used_storage_gb=m["usedStorageGB"],
            cpu_overcommit=_safe(m["cpuOvercommit"]), ram_overcommit=_safe(m["ramOvercommit"]),
            cpu_pct=_safe(m["cpuPct"]), ram_pct=_safe(m["ramPct"]),
        ))

    findings = capacity.build_compliance_findings(host_rows, vm_rows, policy)
    for f in findings:
        db.add(ComplianceFindingSnapshot(
            run_id=run.id, vcenter_key=None, severity=f["severity"], type=f["type"],
            type_label=f.get("typeLabel"), entity=f.get("entity"), cluster=f.get("cluster"),
            metric=f.get("metric"), recommendation=f.get("recommendation"),
        ))

    run.finished_at = dt.datetime.utcnow()
    db.commit()

    return {
        "ok": True, "runId": run.id, "hostCount": len(host_rows), "vmCount": len(vm_rows),
        "findingCount": len(findings),
    }


def _safe(v):
    import math
    return v if (v is not None and math.isfinite(v)) else None
