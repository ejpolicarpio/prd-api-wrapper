import uvicorn

from src.configuration import EnvironmentEnum
from src.factory import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "src.runserver:app",
        host=app.settings.HOST,
        port=app.settings.PORT,
        reload=app.settings.ENVIRONMENT != EnvironmentEnum.PRODUCTION,
    )
