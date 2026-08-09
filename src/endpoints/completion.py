from fastapi import APIRouter

from src.dependencies.common import CompletionServiceDep
from src.models.completion import CompletionRequest, CompletionResponse

router = APIRouter(prefix="/v1", tags=["completion"])


@router.post("/complete", response_model=CompletionResponse)
async def complete(
    payload: CompletionRequest, service: CompletionServiceDep
) -> CompletionResponse:
    return await service.complete(payload)
