"""JWT auth for api.py.

DEV-ONLY: the user table below is two hardcoded accounts (`operator1`/`approver1`), not a real
identity provider. It exists to demonstrate a genuine role split — `operator` submits
escalations, `approver` resolves the human-approval gate — not to be a production auth backend.
Swap this module for a real user store/IdP before deploying anywhere that matters.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from config import CONFIG

Role = Literal["operator", "approver"]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


@dataclass(frozen=True)
class DevUser:
    username: str
    role: Role
    hashed_password: str


_DEV_USERS: dict[str, DevUser] = {
    "operator1": DevUser(
        username="operator1", role="operator", hashed_password=_hash_password(CONFIG.dev_operator_password)
    ),
    "approver1": DevUser(
        username="approver1", role="approver", hashed_password=_hash_password(CONFIG.dev_approver_password)
    ),
}


def authenticate_user(username: str, password: str) -> DevUser | None:
    user = _DEV_USERS.get(username)
    if user is None or not _verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(username: str, role: str) -> str:
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=CONFIG.jwt_expire_minutes
    )
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, CONFIG.jwt_secret_key, algorithm=CONFIG.jwt_algorithm)


@dataclass(frozen=True)
class TokenUser:
    username: str
    role: str


def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenUser:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, CONFIG.jwt_secret_key, algorithms=[CONFIG.jwt_algorithm])
    except JWTError as exc:
        raise credentials_error from exc

    username = payload.get("sub")
    role = payload.get("role")
    if not username or not role:
        raise credentials_error
    return TokenUser(username=username, role=role)


def require_role(role: Role):
    def _dependency(user: TokenUser = Depends(get_current_user)) -> TokenUser:
        if user.role != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the {role!r} role (you are {user.role!r}).",
            )
        return user

    return _dependency
