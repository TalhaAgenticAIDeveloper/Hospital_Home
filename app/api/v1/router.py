"""
API v1 router — aggregates all v1 sub-routers under /api/v1.
"""

from fastapi import APIRouter

from app.api.v1.admin_auth import router as admin_auth_router
from app.api.v1.auth import router as auth_router
from app.api.v1.health import router as health_router

api_v1_router = APIRouter(prefix="/api/v1")

# Public authentication (patient/doctor)
api_v1_router.include_router(auth_router)

# Admin authentication
api_v1_router.include_router(admin_auth_router)

# Health checks are mounted at root level, not under /api/v1
# They are included in the main app directly.
