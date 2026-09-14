import csv
import datetime as dt
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import capacity, data_access
from ..config import get_settings
from ..database import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/export", tags=["export"])


def _scope_keys(vcenter: Optional[str]):
    return None if (not vcenter or vcenter == "all") else [vcenter]


@router.get("/csv")
def export_csv(
    type: str = Query(default="hosts", pattern="^(hosts|vms|findings)$"),
    vcenter: Optional[str] = Query(default="all"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    host_rows, vm_rows, _ = data_access.get_latest_combined(db, _scope_keys(vcenter))

    buf = io.StringIO()
    if type == "hosts":
        rows = capacity.build_host_rows(host_rows, vm_rows, policy)
        fields = ["cluster", "host", "cores", "ramGB", "vcpuUsed", "ramUsedGB", "cpuPct", "ramPct", "overall"]
        writer = csv.writer(buf)
        writer.writerow(fields)
        for r in rows:
            writer.writerow([r.get("cluster"), r.get("host"), r.get("cores"), r.get("ramGB"),
                              r.get("vcpuUsed"), r.get("ramUsedGB"), r.get("cpuPct"), r.get("ramPct"),
                              r.get("overall", {}).get("text")])
    elif type == "vms":
        fields = ["cluster", "host", "vm", "powerState", "vcpu", "ramGB", "diskProvGB", "diskUsedGB", "datastore"]
        writer = csv.writer(buf)
        writer.writerow(fields)
        for v in vm_rows:
            writer.writerow([v.get(f) for f in fields])
    else:
        findings = capacity.build_compliance_findings(host_rows, vm_rows, policy)
        fields = ["severity", "type", "typeLabel", "entity", "cluster", "metric", "recommendation"]
        writer = csv.writer(buf)
        writer.writerow(fields)
        for f in findings:
            writer.writerow([f.get(k) for k in fields])

    buf.seek(0)
    filename = f"vco_{type}_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
