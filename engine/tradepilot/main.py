import asyncio
import sys

import uvicorn
from loguru import logger

from tradepilot.core.config import settings
from tradepilot.core.logging import setup_logging


def run() -> None:
    setup_logging()
    from tradepilot.api.app import create_app

    # pyzmq (asyncio) necesita un bucle "Selector". En Windows, uvicorn.run() usa
    # el "Proactor" y ZMQ falla con "Proactor event loop does not implement
    # add_reader"; por eso arrancamos el servidor en nuestro propio bucle.
    config = uvicorn.Config(create_app(), host=settings.API_HOST, port=settings.API_PORT, log_level="info")
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        logger.info("Windows: usando SelectorEventLoop (requerido por ZMQ)")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    run()
