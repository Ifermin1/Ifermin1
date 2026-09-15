import sys
from pathlib import Path

from loguru import logger

from tradepilot.core.config import settings


def setup_logging() -> None:
    logger.remove()
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    logger.add(sys.stderr, format=fmt, level="DEBUG" if settings.DEBUG else "INFO")
    Path("logs").mkdir(exist_ok=True)
    logger.add("logs/tradepilot_{time:YYYY-MM-DD}.log", rotation="100 MB", retention="10 days", format=fmt, enqueue=True)
