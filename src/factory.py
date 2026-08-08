from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.configuration import Settings
from src.endpoints.health import router as health_router


class Application(FastAPI):
    settings: Settings


def create_app(settings: Settings | None = None) -> Application:
    _settings = settings or Settings()

    _app = Application(debug=_settings.DEBUG)
    _app.settings = _settings

    _app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _app.include_router(health_router)

    return _app
