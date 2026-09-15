import asyncio
import sys
import traceback
from app.bootstrap import bootstrap_app
from loguru import logger

if __name__ == "__main__":
    logger.info("Starting TradePilot X Terminal...")
    try:
        # Flet often handles its own event loop under the hood when app_async is called,
        # but since bootstrap_app is an async function, we run it to initialize everything.
        asyncio.run(bootstrap_app())
    except KeyboardInterrupt:
        logger.info("TradePilot X Terminal stopped by user.")
    except Exception as e:
        logger.exception(f"Fatal error starting application: {e}")
