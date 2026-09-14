import datetime as dt
from typing import Optional

import bcrypt
from fastapi import Cookie, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import User

COOKIE_NAME = "vco_session"

# Using the `bcrypt` library directly (not passlib) — passlib 1.7.x's bcrypt
# backend detection is broken against bcrypt>=4.1 (missing `__about__`), which
# crashes bootstrap on a clean install. bcrypt truncates at 72 bytes natively
# so long passwords are hashed safely without raising.


def hash_password(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(username: str) -> str:
    settings = get_settings()
    expire = dt.datetime.utcnow() + dt.timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[str]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None


def get_current_user(
    vco_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not vco_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập")
    username = decode_access_token(vco_session)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Người dùng không tồn tại")
    return user


def ensure_bootstrap_admin(db: Session):
    """Create the first admin user from env vars if the users table is empty."""
    settings = get_settings()
    if db.query(User).count() > 0:
        return
    if not settings.admin_password:
        # No admin bootstrap password configured — skip silently, operator must
        # create a user manually via `python -m app.seed`.
        return
    user = User(
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        display_name="Administrator",
    )
    db.add(user)
    db.commit()
