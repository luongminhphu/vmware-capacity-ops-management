from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import capacity, data_access
from ..config import get_settings
from ..database import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/compliance", tags=["compliance"])


@router.get("/findings")
def get_findings(
    vcenter: Optional[str] = Query(default="all"),
    severity: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    keys = None if (not vcenter or vcenter == "all") else [vcenter]
    host_rows, vm_rows, run_meta = data_access.get_latest_combined(db, keys)
    findings = capacity.build_compliance_findings(host_rows, vm_rows, policy)
    if severity:
        findings = [f for f in findings if f["severity"] == severity]
    return {"findings": findings, "runMeta": run_meta}
