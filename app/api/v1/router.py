"""
API v1 router — aggregates all v1 sub-routers under /api/v1.
"""

from fastapi import APIRouter

from app.api.v1.admin_auth import router as admin_auth_router
from app.api.v1.admin_doctors import router as admin_doctors_router
from app.api.v1.admin_doctors import document_router as admin_document_router
from app.api.v1.auth import router as auth_router
from app.api.v1.doctor import router as doctor_router
from app.api.v1.meetings import router as meetings_router

api_v1_router = APIRouter(prefix="/api/v1")

# Public authentication (patient/doctor)
api_v1_router.include_router(auth_router)

# SaaS Admin authentication
api_v1_router.include_router(admin_auth_router)

# Doctor onboarding & profile management
api_v1_router.include_router(doctor_router)

# SaaS Admin doctor application review
api_v1_router.include_router(admin_doctors_router)

# Admin document download (query-token auth for browser access)
api_v1_router.include_router(admin_document_router)

# Consultations, Availability, WebRTC Signaling & Transcripts
api_v1_router.include_router(meetings_router)

