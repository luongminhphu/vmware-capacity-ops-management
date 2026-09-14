import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import data_access
from ..config import get_settings
from ..collectors.scheduler import sync_vcenter
from ..database import get_db
from ..models import SnapshotRun, User
from ..security import get_current_user
from ..vcenter.client import VCenterConnectionError

router = APIRouter(prefix="/api/vcenters", tags=["vcenters"])


def _vcenter_public(cfg, db: Session):
    last_run = data_access.latest_run_for_key(db, cfg.key, source="live")
    last_error_run = (
        db.query(SnapshotRun)
        .filter(SnapshotRun.vcenter_key == cfg.key, SnapshotRun.status == "error")
        .order_by(SnapshotRun.id.desc())
        .first()
    )
    return {
        "key": cfg.key,
        "name": cfg.name,
        "host": cfg.host,
        "demo": cfg.demo,
        "lastSyncAt": last_run.finished_at.isoformat() if last_run and last_run.finished_at else None,
        "lastStatus": "ok" if last_run else None,
        "lastError": last_error_run.error_message if last_error_run and (not last_run or last_error_run.id > last_run.id) else None,
    }


@router.get("")
def list_vcenters(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings()
    return [_vcenter_public(cfg, db) for cfg in settings.vcenters()]


@router.post("/{key}/sync")
def sync_one(key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings()
    cfg = next((c for c in settings.vcenters() if c.key == key), None)
    if not cfg:
        raise HTTPException(status_code=404, detail="Không tìm thấy vCenter với key này")
    try:
        run = sync_vcenter(db, cfg, settings)
    except VCenterConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Đồng bộ thất bại: {exc}") from exc
    return {
        "ok": True, "runId": run.id, "vcenterKey": key,
        "hostCount": len(run.hosts), "vmCount": len(run.vms), "findingCount": len(run.findings),
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.post("/sync-all")
def sync_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings()
    results = []
    for cfg in settings.vcenters():
        try:
            run = sync_vcenter(db, cfg, settings)
            results.append({"vcenterKey": cfg.key, "ok": True, "hostCount": len(run.hosts), "vmCount": len(run.vms)})
        except Exception as exc:  # noqa: BLE001
            results.append({"vcenterKey": cfg.key, "ok": False, "error": str(exc)})
    return {"results": results}
