"""
Company Management Endpoints.
Guarantees authenticated carrier context retrieval.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models import Company
from ..auth import get_current_company

router = APIRouter(prefix="/api/v1/companies", tags=["companies"])


@router.get("/me")
async def get_my_company(
    company_id: str = Depends(get_current_company),
    db: AsyncSession = Depends(get_db),
):
    company = await db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    return {
        "id": company.id,
        "name": company.name,
        "email": company.email,
        "role": company.role,
        "trustScore": company.trust_score,
        "createdAt": company.created_at.isoformat() if company.created_at else None,
    }
