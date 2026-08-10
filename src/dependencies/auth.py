from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.dependencies.common import SettingsDep
from src.models.caller import Caller
from src.repositories.api_keys import ApiKeyRepository
from src.services.authentication import AuthenticationService

# auto_error=False: we raise our own errors so 401s use our envelope rather
# than FastAPI's. Declaring the scheme still gives /docs its Authorize button.
bearer_scheme = HTTPBearer(auto_error=False, description="Your API key")

BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_api_key_repository(request: Request) -> ApiKeyRepository:
    return request.app.api_key_repository


ApiKeyRepositoryDep = Annotated[ApiKeyRepository, Depends(get_api_key_repository)]


def get_authentication_service(
    repository: ApiKeyRepositoryDep, settings: SettingsDep
) -> AuthenticationService:
    return AuthenticationService(repository, require_api_key=settings.REQUIRE_API_KEY)


AuthenticationServiceDep = Annotated[
    AuthenticationService, Depends(get_authentication_service)
]


async def get_caller(
    credentials: BearerDep, service: AuthenticationServiceDep
) -> Caller:
    """Adapter only: pull the bearer token out of HTTP, let the service decide."""
    return await service.authenticate(credentials.credentials if credentials else None)


CallerDep = Annotated[Caller, Depends(get_caller)]
