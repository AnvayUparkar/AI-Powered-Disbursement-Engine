from fastapi import APIRouter
from idp.schemas.response import HealthCheckResponse
from idp.core.config import settings

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """Health check endpoint to verify Node 2 system status."""
    return HealthCheckResponse(
        status="ok",
        app_name=settings.APP_NAME,
        environment=settings.APP_ENV,
        ocr_engine=f"{settings.OCR_ENGINE} ({settings.OCR_MODEL})",
        vlm_enabled=settings.VLM_ENABLED
    )
