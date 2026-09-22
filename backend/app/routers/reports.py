import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import capacity, data_access, history, report_pdf
from ..config import get_settings
from ..database import get_db
from ..models import ReportLogo, User
from ..security import get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _scope_keys(vcenter: Optional[str]):
    return None if (not vcenter or vcenter == "all") else [vcenter]


def _scope_label(vcenter: Optional[str]) -> str:
    return "Toàn bộ vCenter" if (not vcenter or vcenter == "all") else vcenter


def _split_csv(value: Optional[str]) -> Optional[list]:
    """Parse a comma-separated query param.

    Returns None when the param was omitted entirely ("no filter").
    Returns a list (possibly empty) when the param was explicitly supplied,
    including an empty string, so a deliberate zero-item selection on the
    frontend still filters results down to nothing instead of being treated
    as "no filter".
    """
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


@router.get("/pdf")
def export_pdf(
    view: str = Query(default="executive", pattern="^(executive|technical)$"),
    vcenter: Optional[str] = Query(default="all"),
    types: Optional[str] = Query(default=None, description="Comma-separated finding types to include (omit = all types)"),
    clusters: Optional[str] = Query(default=None, description="Comma-separated cluster names to include (omit = every cluster)"),
    hosts: Optional[str] = Query(default=None, description="Comma-separated host names to include (omit = every host in scope)"),
    storage: Optional[str] = Query(default=None, description="'1'/'true' to include Storage criteria, '0'/'false' to exclude (omit = use saved setting)"),
    months: int = Query(default=12, description="Snapshot-history window for the trend section / VM appendix: 3, 6 or 12 months"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    if storage is not None:
        # Explicit per-export override from the UI toggle; otherwise the
        # persisted app setting (include_storage) decides.
        policy.include_storage = capacity.parse_bool(storage, default=policy.include_storage)
    host_rows, vm_rows, run_meta = data_access.get_latest_combined(db, _scope_keys(vcenter))

    if not host_rows and not vm_rows:
        raise HTTPException(status_code=404, detail="Chưa có dữ liệu để tạo báo cáo. Hãy đồng bộ hoặc import dữ liệu trước.")

    def _display_list(items: list, limit: int = 5) -> str:
        shown = ", ".join(items[:limit])
        return shown if len(items) <= limit else f"{shown} và {len(items) - limit} khác"

    cluster_list = _split_csv(clusters)
    host_list = _split_csv(hosts)
    scope_parts = [_scope_label(vcenter)]
    if cluster_list is not None:
        host_rows = [h for h in host_rows if h.get("cluster") in cluster_list]
        vm_rows = [v for v in vm_rows if v.get("cluster") in cluster_list]
        scope_parts.append(f"Cluster: {_display_list(cluster_list)}" if cluster_list else "Cluster: (không chọn cluster nào)")
    if host_list is not None:
        host_rows = [h for h in host_rows if h.get("host") in host_list]
        vm_rows = [v for v in vm_rows if v.get("host") in host_list]
        scope_parts.append(f"Host: {_display_list(host_list)}" if host_list else "Host: (không chọn host nào)")

    if (cluster_list is not None or host_list is not None) and not host_rows and not vm_rows:
        raise HTTPException(status_code=404, detail="Không có dữ liệu phù hợp với phạm vi cluster/host đã chọn.")

    scope_label = " · ".join(scope_parts)

    include_types = None
    if types:
        requested = {t.strip() for t in types.split(",") if t.strip()}
        include_types = [t for t in report_pdf.ALL_FINDING_TYPES if t in requested]

    logo_row = db.query(ReportLogo).filter(ReportLogo.id == 1).first()
    logo_data_url = f"data:{logo_row.mime_type};base64,{logo_row.data_base64}" if logo_row else None

    # Snapshot history is always per cluster (one cluster = one project team)
    # and follows the same cluster scope as the rest of the report. Host
    # narrowing applies to the VM added/removed appendix; the CPU/RAM series
    # stay at cluster level (that is the unit snapshots are aggregated at).
    months = history.normalize_months(months)
    scope_keys = _scope_keys(vcenter)
    try:
        history_data = history.get_resource_history(db, scope_keys, months, "cluster", policy, clusters=cluster_list)
        vm_changes = history.get_vm_changes(db, scope_keys, months, clusters=cluster_list, hosts=host_list)
    except Exception:  # pragma: no cover - history must never block the report
        history_data, vm_changes = None, None

    try:
        pdf_bytes = report_pdf.build_report_pdf(view, host_rows, vm_rows, policy, scope_label, run_meta, include_types, logo_data_url,
                                                history=history_data, vm_changes=vm_changes)
    except Exception as exc:  # pragma: no cover - surfaced to the UI
        raise HTTPException(status_code=500, detail=f"Không thể tạo PDF: {exc}")

    label = "executive" if view == "executive" else "technical"
    filename = f"vco_report_{label}_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/brief")
def management_brief(
    vcenter: Optional[str] = Query(default="all"),
    storage: Optional[str] = Query(default=None, description="'1'/'true' to include Storage criteria, '0'/'false' to exclude (omit = use saved setting)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Management-friendly brief (headline, key points, recommendations,
    decision register) for the web Executive View -- same logic as the
    Executive PDF."""
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    if storage is not None:
        policy.include_storage = capacity.parse_bool(storage, default=policy.include_storage)
    host_rows, vm_rows, _run_meta = data_access.get_latest_combined(db, _scope_keys(vcenter))
    if not host_rows and not vm_rows:
        return {"available": False, "scope": _scope_label(vcenter)}
    brief = report_pdf.build_management_brief(host_rows, vm_rows, policy)
    brief["available"] = True
    brief["scope"] = _scope_label(vcenter)
    return brief
