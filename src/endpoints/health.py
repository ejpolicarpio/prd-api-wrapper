import httpx
from fastapi import APIRouter, Response, status
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.common import HttpClientDep, SessionDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: is this process running at all?

    Deliberately checks nothing else. A liveness probe that fails when the
    database is down gets the process killed and restarted, which does not fix
    a database and does turn a partial outage into a total one.
    """
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: SessionDep, client: HttpClientDep, response: Response) -> dict:
    """Readiness: should this process be sent traffic right now?

    Answering no takes it out of the load balancer's rotation without killing
    it, so it can rejoin when its dependencies recover.
    """
    checks = {
        "database": await _database_ok(session),
        "upstream": await _upstream_ok(client),
    }

    ready = all(checks.values())

    response.status_code = (
        status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    )

    return {"status": "ready" if ready else "degraded", "checks": checks}


async def _database_ok(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.opt(exception=True).warning("readiness: database unreachable")
        return False


async def _upstream_ok(client: httpx.AsyncClient) -> bool:
    try:
        # Short timeout of its own: a readiness probe that hangs is a readiness
        # probe that gets the process killed.
        response = await client.get("/models", timeout=2.0)
        return not response.is_error
    except Exception:
        logger.opt(exception=True).warning("readiness: upstream unreachable")
        return False
