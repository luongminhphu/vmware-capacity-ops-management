from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import data_access
from ..config import get_settings
from ..database import get_db
from ..models import AppSetting, User
from ..schemas import SettingsUpdate
from ..security import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_current_thresholds(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    return vars(policy)


@router.put("")
def update_thresholds(payload: SettingsUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    updates = payload.dict(exclude_none=True)
    for key, value in updates.items():
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = str(value)
        else:
            db.add(AppSetting(key=key, value=str(value)))
    db.commit()
    settings = get_settings()
    policy = data_access.get_effective_policy(db, settings)
    return vars(policy)
