"""
API endpoints for doctor ratings and post-consultation patient feedback.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.rating import RatingCreateRequest, RatingResponse
from app.services.rating_service import RatingService

router = APIRouter(prefix="/ratings", tags=["Doctor Ratings & Feedback"])


@router.post(
    "/{meeting_id}",
    response_model=RatingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit patient rating for a completed meeting",
)
async def submit_meeting_rating(
    meeting_id: uuid.UUID,
    payload: RatingCreateRequest,
    current_user: User = Depends(require_role(UserRole.PATIENT)),
    session: AsyncSession = Depends(get_db),
) -> RatingResponse:
    """
    Patient submits a 1–5 star rating and optional feedback for a completed consultation.
    """
    return await RatingService.submit_rating(
        session=session,
        meeting_id=meeting_id,
        current_user=current_user,
        payload=payload,
    )


@router.get(
    "/{meeting_id}",
    response_model=RatingResponse,
    summary="Get rating for a specific meeting",
)
async def get_meeting_rating(
    meeting_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RatingResponse:
    """
    Fetch the submitted rating for a given meeting. Accessible by meeting participants or admin.
    """
    return await RatingService.get_meeting_rating(
        session=session,
        meeting_id=meeting_id,
        current_user=current_user,
    )
