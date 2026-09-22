import base64
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import data_access
from ..config import get_settings
from ..database import get_db
from ..models import AppSetting, ReportLogo, User
from ..schemas import LogoUpload, SettingsUpdate
from ..security import get_current_user
from ..timeutil import to_iso_utc

router = APIRouter(prefix="/api/settings", tags=["settings"])

MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2MB cap keeps the base64 blob small in Postgres/HTTP payloads
DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|jpg|svg\+xml|webp));base64,(.+)$", re.IGNORECASE | re.DOTALL)


@router.get("")
def get_current_thresholds(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    return vars(policy)


@router.put("")
def update_thresholds(payload: SettingsUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    updates = payload.dict(exclude_none=True)
    for key, value in updates.items():
        # Booleans are stored as "true"/"false" so capacity.parse_bool reads
        # them back unambiguously; numbers keep their plain str() form.
        stored = ("true" if value else "false") if isinstance(value, bool) else str(value)
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = stored
        else:
            db.add(AppSetting(key=key, value=stored))
    db.commit()
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    return vars(policy)


@router.get("/logo")
def get_logo(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Returns the stored company logo as a ready-to-use data URI, or null
    fields when none has been uploaded yet."""
    row = db.query(ReportLogo).filter(ReportLogo.id == 1).first()
    if not row:
        return {"dataUrl": None, "filename": None, "uploadedAt": None}
    return {
        "dataUrl": f"data:{row.mime_type};base64,{row.data_base64}",
        "filename": row.filename,
        "uploadedAt": to_iso_utc(row.uploaded_at),
    }


@router.put("/logo")
def upload_logo(payload: LogoUpload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Stores the company logo (sent as a data URL from FileReader) so it can be
    embedded on Executive/Technical PDF report covers."""
    match = DATA_URL_RE.match(payload.data_url.strip())
    if not match:
        raise HTTPException(400, "Định dạng ảnh không hợp lệ. Chỉ chấp nhận PNG, JPEG, SVG hoặc WEBP.")
    mime_type, b64_data = match.group(1).lower(), match.group(2)
    try:
        decoded = base64.b64decode(b64_data, validate=True)
    except Exception:
        raise HTTPException(400, "Không thể giải mã dữ liệu ảnh đã tải lên.")
    if len(decoded) > MAX_LOGO_BYTES:
        raise HTTPException(400, f"Kích thước logo vượt quá {MAX_LOGO_BYTES // (1024*1024)}MB cho phép.")
    row = db.query(ReportLogo).filter(ReportLogo.id == 1).first()
    if not row:
        row = ReportLogo(id=1)
        db.add(row)
    row.mime_type = mime_type
    row.data_base64 = b64_data
    row.filename = payload.filename
    db.commit()
    return {"dataUrl": f"data:{row.mime_type};base64,{row.data_base64}", "filename": row.filename}


@router.delete("/logo")
def delete_logo(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.query(ReportLogo).filter(ReportLogo.id == 1).first()
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}
