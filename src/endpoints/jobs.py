from fastapi import APIRouter, BackgroundTasks, Request, status

from src.dependencies.auth import CallerDep
from src.dependencies.common import JobServiceDep, SettingsDep
from src.dependencies.rate_limit import RateLimitDep
from src.models.job import JobAccepted, JobRequest, JobView

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[RateLimitDep],
)
async def submit(
    request: Request,
    payload: JobRequest,
    caller: CallerDep,
    service: JobServiceDep,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> JobAccepted:
    accepted = await service.accept(
        payload, caller, model=payload.model or settings.UPSTREAM_MODEL
    )

    # Queued after the response is sent, so the caller waits for a write, not
    # for a model. The request id travels with it so the work stays traceable
    # back to the call that asked for it.
    background.add_task(
        service.run,
        accepted.id,
        payload,
        caller,
        getattr(request.state, "request_id", None),
    )

    return accepted


@router.get("/{job_id}", response_model=JobView)
async def show(job_id: str, caller: CallerDep, service: JobServiceDep) -> JobView:
    return await service.get(job_id, caller)
