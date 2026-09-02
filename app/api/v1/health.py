"""
Health check endpoints.

Liveness: Is the application running?
Readiness: Is the application ready to serve requests (DB connected)?
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.auth import MessageResponse

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=MessageResponse,
    summary="Liveness check",
    description="Returns OK if the application is running.",
)
async def health() -> MessageResponse:
    return MessageResponse(message="OK")


@router.get(
    "/health/ready",
    response_model=dict,
    summary="Readiness check",
    description=(
        "Verifies the application can connect to the database. "
        "Returns database connectivity status."
    ),
    responses={
        200: {"description": "Application is ready"},
        503: {"description": "Database is not available"},
    },
)
async def readiness(session: AsyncSession = Depends(get_db)) -> dict:
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception:
        return {"status": "not ready", "database": "disconnected"}
