"""
Role-Based Access Control (RBAC) (Phase 19).

Roles are assigned by the server at account creation and carried in the JWT.
They are never accepted from a client request body.
"""
import enum
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from jose import JWTError, jwt

from services.core_api.app.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
security = HTTPBearer(auto_error=False)


class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    CARRIER_OWNER = "CARRIER_OWNER"
    OPS_MANAGER = "OPS_MANAGER"
    DISPATCHER = "DISPATCHER"
    FINANCE = "FINANCE"
    AUDITOR = "AUDITOR"
    DRIVER = "DRIVER"


#: Roles a company account may legitimately hold. A self-service registration
#: always yields CARRIER_OWNER; elevated roles are provisioned out of band.
COMPANY_ROLES = frozenset(
    {Role.CARRIER_OWNER, Role.OPS_MANAGER, Role.DISPATCHER, Role.FINANCE, Role.AUDITOR, Role.ADMIN}
)

#: Roles permitted to authorise the movement of money on their own escrows.
#: CARRIER_OWNER is included deliberately: a single-operator carrier is the
#: finance function, and excluding it made escrow release unreachable for
#: every real account.
ESCROW_AUTHORISING_ROLES = (Role.CARRIER_OWNER, Role.OPS_MANAGER, Role.FINANCE, Role.ADMIN)


def _decode(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


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
        payload = _decode(jwt_token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_role = payload.get("role")
    try:
        return Role(raw_role)
    except ValueError:
        # An unrecognised role is not a reason to fall back to a permissive
        # default. Treat it as an invalid credential.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token carries an unrecognised role",
        )


def require_role(*allowed: Role):
    """Guard a route on an explicit allow-list.

    There is no implicit superuser escape hatch: ADMIN only passes where it is
    named in `allowed`. A blanket bypass turned the self-assignable `role`
    field at registration into full privilege escalation.
    """
    allowed_set = frozenset(allowed)

    async def checker(current_role: Role = Depends(get_current_role)) -> Role:
        if current_role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required one of: {[r.value for r in allowed]}",
            )
        return current_role

    return checker
