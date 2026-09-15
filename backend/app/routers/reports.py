import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import data_access, report_pdf
from ..config import get_settings
from ..database import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _scope_keys(vcenter: Optional[str]):
    return None if (not vcenter or vcenter == "all") else [vcenter]


def _scope_label(vcenter: Optional[str]) -> str:
    return "Toàn bộ vCenter" if (not vcenter or vcenter == "all") else vcenter


@router.get("/pdf")
def export_pdf(
    view: str = Query(default="executive", pattern="^(executive|technical)$"),
    vcenter: Optional[str] = Query(default="all"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    host_rows, vm_rows, run_meta = data_access.get_latest_combined(db, _scope_keys(vcenter))

    if not host_rows and not vm_rows:
        raise HTTPException(status_code=404, detail="Chưa có dữ liệu để tạo báo cáo. Hãy đồng bộ hoặc import dữ liệu trước.")

    try:
        pdf_bytes = report_pdf.build_report_pdf(view, host_rows, vm_rows, policy, _scope_label(vcenter), run_meta)
    except Exception as exc:  # pragma: no cover - surfaced to the UI
        raise HTTPException(status_code=500, detail=f"Không thể tạo PDF: {exc}")

    label = "executive" if view == "executive" else "technical"
    filename = f"vco_report_{label}_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
