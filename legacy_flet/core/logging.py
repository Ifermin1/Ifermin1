import sys
from loguru import logger
from core.config import settings

def setup_logging():
    logger.remove() # Eliminar handler por defecto
    
    # Formato institucional para consola
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    
    logger.add(sys.stderr, format=fmt, level="DEBUG" if settings.DEBUG else "INFO")
    logger.add("logs/tradepilot_{time:YYYY-MM-DD}.log", rotation="100 MB", retention="10 days", format=fmt)

setup_logging()
