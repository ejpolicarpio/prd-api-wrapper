import secrets
from datetime import UTC, datetime

from loguru import logger

from src.errors.exceptions import AppError, JobNotFound, WebhookDeliveryFailed
from src.models.caller import Caller
from src.models.completion import CompletionRequest
from src.models.job import JobAccepted, JobRequest, JobStatus, JobView, WebhookPayload
from src.models.tables import Job
from src.repositories.jobs import JobRepository
from src.services.completion import CompletionService
from src.services.webhooks import WebhookService


class JobService:
    """Accepts work now, answers later.

    A completion can take tens of seconds. Holding an HTTP connection open for
    that is fragile -- a dropped connection loses the result, and every
    in-flight request occupies a worker. So the request is recorded, a 202 is
    returned immediately, and the answer is pushed to the caller's callback.
    """

    def __init__(
        self,
        jobs: JobRepository,
        completions: CompletionService,
        webhooks: WebhookService,
    ) -> None:
        self._jobs = jobs
        self._completions = completions
        self._webhooks = webhooks

    async def accept(
        self, request: JobRequest, caller: Caller, model: str
    ) -> JobAccepted:
        job = Job(
            id=secrets.token_hex(16),
            api_key_id=caller.id,
            status=JobStatus.PENDING,
            model=model,
            prompt=request.prompt,
            callback_url=str(request.callback_url),
        )

        # Persisted before the 202: an id we hand out must be one the caller
        # can look up, even if the process dies before the work starts.
        await self._jobs.create(job)

        return JobAccepted(id=job.id, status=JobStatus.PENDING)

    async def get(self, job_id: str, caller: Caller) -> JobView:
        view = await self._jobs.get(job_id, caller.id)

        if view is None:
            # Same answer whether the job belongs to someone else or does not
            # exist: otherwise job ids become an existence oracle.
            raise JobNotFound()

        return view

    async def run(self, job_id: str, request: JobRequest, caller: Caller) -> None:
        """The background half. Nothing here may raise: there is no client
        left to receive an error, only a job row to record it in."""
        await self._jobs.update(job_id, status=JobStatus.RUNNING)

        payload = await self._execute(job_id, request, caller)

        await self._deliver(job_id, str(request.callback_url), payload)

    async def _execute(
        self, job_id: str, request: JobRequest, caller: Caller
    ) -> WebhookPayload:
        try:
            completion = await self._completions.complete(
                CompletionRequest(
                    prompt=request.prompt,
                    model=request.model,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                ),
                caller,
            )
        except AppError as error:
            await self._jobs.update(
                job_id,
                status=JobStatus.FAILED,
                error_code=error.code,
                error_message=error.message,
            )

            return WebhookPayload(
                job_id=job_id,
                status=JobStatus.FAILED,
                model=request.model or "",
                error_code=error.code,
                error_message=error.message,
            )

        await self._jobs.update(
            job_id,
            status=JobStatus.SUCCEEDED,
            content=completion.content,
            model=completion.model,
            prompt_tokens=completion.usage.prompt_tokens,
            completion_tokens=completion.usage.completion_tokens,
        )

        return WebhookPayload(
            job_id=job_id,
            status=JobStatus.SUCCEEDED,
            model=completion.model,
            content=completion.content,
        )

    async def _deliver(self, job_id: str, url: str, payload: WebhookPayload) -> None:
        try:
            attempts = await self._webhooks.deliver(url, payload)
            await self._jobs.update(
                job_id,
                delivery_attempts=attempts,
                delivered_at=datetime.now(UTC),
            )
        except WebhookDeliveryFailed as error:
            # The work itself succeeded or failed on its own terms; delivery
            # failing does not change that, so the job keeps its status and
            # the caller can still poll for the result. delivered_at staying
            # null is what marks it as undelivered.
            await self._jobs.update(
                job_id, delivery_attempts=error.details.get("attempts", 0)
            )
        except Exception:
            logger.opt(exception=True).error("job {} delivery crashed", job_id)
