"""
FastAPI application entry point.

Configures CORS, registers exception handlers, and includes all routers.
Does NOT run Alembic migrations — those must be executed separately.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.database import engine
from app.core.logging import get_logger

settings = get_settings()
from app.services.reminder_scheduler import shutdown_scheduler, start_scheduler

logger = get_logger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.APP_ENV}")
    # Start background medicine reminder scheduler
    try:
        start_scheduler()
    except Exception as e:
        logger.warning(f"Could not start reminder scheduler on startup: {e}")
    yield
    # Shutdown scheduler and database engine on shutdown
    try:
        shutdown_scheduler()
    except Exception as e:
        logger.warning(f"Could not stop reminder scheduler on shutdown: {e}")
    await engine.dispose()
    logger.info("Application shutdown complete")


# ── Application ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Production-grade authentication backend for a healthcare SaaS platform. "
        "Supports patient and doctor registration, JWT authentication with refresh "
        "token rotation, role-based access control, and SaaS Admin management."
    ),
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

# ── CORS ─────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────────────────

# Health checks at root level
app.include_router(health_router)

# API v1 routes
app.include_router(api_v1_router)
