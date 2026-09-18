"""
Authentication Endpoints (Phase 1.5).
Register new carrier companies and issue signed JWT access tokens.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Company
from ..auth import hash_password, verify_password, create_access_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterCompanyRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str = "CARRIER_OWNER"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    company_id: str
    company_name: str
    role: str


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=TokenResponse)
async def register(req: RegisterCompanyRequest, db: AsyncSession = Depends(get_db)):
    # Check if email exists
    existing = await db.execute(select(Company).where(Company.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company with this email already registered",
        )

    company = Company(
        name=req.name,
        email=req.email,
        hashed_password=hash_password(req.password),
        role=req.role,
    )
    db.add(company)
    await db.commit()
    await db.refresh(company)

    token = create_access_token(
        subject=company.email,
        company_id=company.id,
        role=company.role,
    )
    return TokenResponse(
        access_token=token,
        company_id=company.id,
        company_name=company.name,
        role=company.role,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Company).where(Company.email == form_data.username)
    res = await db.execute(stmt)
    company = res.scalar_one_or_none()

    if not company or not verify_password(form_data.password, company.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        subject=company.email,
        company_id=company.id,
        role=company.role,
    )
    return TokenResponse(
        access_token=token,
        company_id=company.id,
        company_name=company.name,
        role=company.role,
    )
