import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.pipeline_flags import is_dgcl_pipeline_enabled, set_dgcl_pipeline_enabled

logger = logging.getLogger("disbursement_pipeline.api.settings")

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class DgclPipelineFlag(BaseModel):
    enabled: bool


@router.get("/dgcl-pipeline", summary="Get whether the DGCL verification pipeline is enabled")
def get_dgcl_pipeline_flag() -> DgclPipelineFlag:
    return DgclPipelineFlag(enabled=is_dgcl_pipeline_enabled())


@router.post("/dgcl-pipeline", summary="Enable/disable the DGCL verification pipeline")
def update_dgcl_pipeline_flag(body: DgclPipelineFlag) -> DgclPipelineFlag:
    set_dgcl_pipeline_enabled(body.enabled)
    return DgclPipelineFlag(enabled=body.enabled)
