from fastapi import APIRouter

from app.api.v1.endpoints import (
    assessment,
    auth,
    facilities,
    inquiries,
    onboarding,
    profile,
    resources,
    saved,
)

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(onboarding.router)
api_router.include_router(facilities.router)
api_router.include_router(inquiries.router)
api_router.include_router(assessment.router)
api_router.include_router(saved.router)
api_router.include_router(profile.router)
api_router.include_router(resources.router)
