"""
SaaS Admin authentication routes.

Logically separated from public patient/doctor authentication.
Admin accounts are created via CLI script, not via public signup.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.auth import AdminLoginRequest, LoginResponse
from app.services.admin_service import AdminService

router = APIRouter(prefix="/admin/auth", tags=["Admin Authentication"])


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate a SaaS Admin",
    description=(
        "Authenticates a SaaS Admin and returns JWT tokens. "
        "Only users with role='saas_admin' can use this endpoint. "
        "Admin accounts are created via the CLI script, not via public signup."
    ),
    responses={
        200: {"description": "Admin login successful"},
        401: {"description": "Invalid credentials or not an admin"},
    },
)
async def admin_login(
    data: AdminLoginRequest,
    session: AsyncSession = Depends(get_db),
) -> LoginResponse:
    return await AdminService.admin_login(session, data)
