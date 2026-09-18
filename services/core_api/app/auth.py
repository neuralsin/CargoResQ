"""
JWT Authentication & Multi-Tenant Company Isolation (Phase 1.5).
Enforces company data boundaries at the repository query level, not just in the UI.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from jose import jwt, JWTError
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from .config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        pwd_bytes = plain_password.encode("utf-8")[:72]
        return bcrypt.checkpw(pwd_bytes, hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(
    subject: str,
    company_id: str,
    role: str = "CARRIER_OWNER",
    expires_delta: Optional[timedelta] = None,
    principal_type: str = "company",
    principal_id: Optional[str] = None,
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )

    payload = {
        "sub": subject,
        "company_id": company_id,
        "role": role,
        "principal_type": principal_type,
        "principal_id": principal_id or subject,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_current_company(token: str = Depends(oauth2_scheme)) -> str:
    """
    Decodes the JWT token and returns the authenticated company ID.
    Raises 401 Unauthorized if invalid or expired.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        company_id = payload.get("company_id")
        if not company_id or not isinstance(company_id, str):
            raise credentials_exception
        return company_id
    except JWTError:
        raise credentials_exception


async def get_current_principal(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """Decode a token once for driver and company-scoped endpoints."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if not payload.get("company_id") or not payload.get("sub"):
            raise credentials_exception
        return payload
    except JWTError:
        raise credentials_exception


async def get_current_driver(principal: Dict[str, Any] = Depends(get_current_principal)) -> Dict[str, Any]:
    if principal.get("principal_type") != "driver" or not principal.get("principal_id"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Driver access required")
    return principal


async def get_current_company_principal(
    principal: Dict[str, Any] = Depends(get_current_principal),
) -> Dict[str, Any]:
    """Require a company-staff token (as opposed to a driver's device token)."""
    if principal.get("principal_type") != "company":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Company operator access required",
        )
    return principal


def actor_id_of(principal: Dict[str, Any]) -> str:
    """The audit-trail actor for a principal.

    Derived from the verified token, never from a request body -- `actor_id`
    used to be a free-text field defaulting to the literal "ops".
    """
    return str(principal.get("principal_id") or principal.get("sub") or "unknown")


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT outside of a request dependency.

    Used by the WebSocket handshake, which cannot use Depends().
    Raises JWTError on any failure.
    """
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
