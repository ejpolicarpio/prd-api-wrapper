from fastapi import APIRouter

from src.dependencies.auth import CallerDep
from src.dependencies.common import CompletionServiceDep
from src.dependencies.rate_limit import RateLimitDep
from src.models.completion import CompletionRequest, CompletionResponse

router = APIRouter(prefix="/v1", tags=["completion"])


# Rate limiting is declared here rather than in the signature because it
# returns nothing the endpoint needs -- it either passes or raises.
@router.post(
    "/complete", response_model=CompletionResponse, dependencies=[RateLimitDep]
)
async def complete(
    payload: CompletionRequest,
    service: CompletionServiceDep,
    caller: CallerDep,
) -> CompletionResponse:
    return await service.complete(payload, caller)
