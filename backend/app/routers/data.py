from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import data_access
from ..database import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/latest")
def latest_data(
    vcenter: Optional[str] = Query(default="all"),
    include_import: bool = Query(default=True),
    group_by_vcenter: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Raw normalized host/vm rows (same camelCase shape the original
    single-file app's normalize functions produce) for the requested scope, so
    the frontend can reuse its existing buildCapacityModel/buildHostRows/etc.
    JS pipeline unchanged regardless of whether the data came from a live
    vCenter poll or a manual CSV/Excel import."""
    if group_by_vcenter:
        grouped = data_access.get_latest_grouped(db, include_import=include_import)
        return grouped
    keys = None if (not vcenter or vcenter == "all") else [vcenter]
    host_rows, vm_rows, run_meta = data_access.get_latest_combined(db, keys, include_import=include_import)
    return {"hosts": host_rows, "vms": vm_rows, "runMeta": run_meta}
