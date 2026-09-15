import uvicorn

from tradepilot.core.config import settings
from tradepilot.core.logging import setup_logging


def run() -> None:
    setup_logging()
    from tradepilot.api.app import create_app
    uvicorn.run(create_app(), host=settings.API_HOST, port=settings.API_PORT, log_level="info")


if __name__ == "__main__":
    run()
