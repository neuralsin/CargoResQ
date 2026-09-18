"""
Role-Based Access Control (RBAC) (Phase 19).
Enforces authorization for sensitive operations (escrow release, pricing overrides).
"""
import enum
import os
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
security = HTTPBearer(auto_error=False)


class Role(str, enum.Enum):
    OPS_MANAGER = "OPS_MANAGER"
    DRIVER = "DRIVER"
    FINANCE = "FINANCE"
    ADMIN = "ADMIN"
    DISPATCHER = "DISPATCHER"
    CARRIER_OWNER = "CARRIER_OWNER"
    AUDITOR = "AUDITOR"


JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-change-this-cargoresq-secret")
JWT_ALGORITHM = "HS256"


async def get_current_role(
    token: Optional[str] = Depends(oauth2_scheme),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Role:
    jwt_token = token
    if not jwt_token and auth_header:
        jwt_token = auth_header.credentials

    if not jwt_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(jwt_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        role_str = payload.get("role", Role.DRIVER.value)
        return Role(role_str)
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_role(*allowed: Role):
    async def checker(current_role: Role = Depends(get_current_role)) -> Role:
        if current_role not in allowed and current_role != Role.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required one of: {[r.value for r in allowed]}",
            )
        return current_role

    return checker
