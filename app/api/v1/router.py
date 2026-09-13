"""
API v1 router — aggregates all v1 sub-routers under /api/v1.
"""

from fastapi import APIRouter

from app.api.v1.admin_auth import router as admin_auth_router
from app.api.v1.admin_doctors import router as admin_doctors_router
from app.api.v1.admin_patients import router as admin_patients_router
from app.api.v1.auth import router as auth_router
from app.api.v1.consultation_ai import router as consultation_ai_router
from app.api.v1.doctor import router as doctor_router
from app.api.v1.meetings import router as meetings_router
from app.api.v1.patient import router as patient_router
from app.api.v1.patient_documents import router as patient_documents_router
from app.api.v1.patient_meeting_docs import router as patient_meeting_docs_router
from app.api.v1.prescriptions import router as prescriptions_router
from app.api.v1.ratings import router as ratings_router

api_v1_router = APIRouter(prefix="/api/v1")

# Public authentication (patient/doctor)
api_v1_router.include_router(auth_router)

# SaaS Admin authentication
api_v1_router.include_router(admin_auth_router)

# Doctor onboarding & profile management
api_v1_router.include_router(doctor_router)

# Patient profile management
api_v1_router.include_router(patient_router)

# SaaS Admin doctor application review
api_v1_router.include_router(admin_doctors_router)

# SaaS Admin patient management
api_v1_router.include_router(admin_patients_router)

# Consultations, Availability, WebRTC Signaling & Transcripts
api_v1_router.include_router(meetings_router)

# Patient medical document management
api_v1_router.include_router(patient_documents_router)

# Meeting-specific patient document access (for doctors)
api_v1_router.include_router(patient_meeting_docs_router)

# Doctor ratings & patient feedback
api_v1_router.include_router(ratings_router)

# Medical prescriptions & dosage schedules
api_v1_router.include_router(prescriptions_router)

# Consultation AI — transcription, extraction, and approval
api_v1_router.include_router(consultation_ai_router)

