# src/cryptom/__main__.py
import argparse
import asyncio
import signal
import sys
from pathlib import Path

from loguru import logger

from .config import AppConfig
from .engine import CryptoEngine


async def _shutdown(signal_name, loop, engine: CryptoEngine):
    """
    Handle shutdown signals.
    """
    logger.info(f"Received signal {signal_name}, shutting down...")
    await engine.stop()
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
        else:
            await task
    logger.info("Canceling outstanding tasks...")
    loop.stop()


async def _start_async(config: AppConfig):
    engine = CryptoEngine(config)

    # Setup signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(_shutdown(s.name, loop, engine))
            )
        except NotImplementedError:
            # Windows implementation of asyncio loop usually doesn't support add_signal_handler
            # We can handle KeyboardInterrupt in the main block instead
            pass

    try:
        await engine.start()
        logger.info("System started. Press Ctrl+C to exit.")
        asyncio.Event()
        # If on Windows, we might need a way to capture signals if add_signal_handler fails
        # Or just rely on the try/except KeyboardInterrupt in the synchronous wrapper if we weren't inside asyncio.run
        # But since we are here, we can just wait.

        # On Windows, standard asyncio loop (ProactorEventLoop) does not support add_signal_handler.
        # So we use a simple sleep loop to check for interruption if needed,
        # or rely on the fact that Ctrl+C raises KeyboardInterrupt in the main thread
        # which asyncio.run propagates.
        while True:
            await asyncio.sleep(1)

    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        await engine.stop()


def start(config_str: str = "config.yaml"):
    """
    start the CryptoM monitoring service.
    """
    # Configure logging format
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    )

    # Load configuration
    logger.info(f"Loading config file: {config_str}")
    config_path = Path(config_str)
    if not config_path.exists():
        logger.error(f"cannot find config file: {config_path.absolute()}")
        return
    config = AppConfig.load(config_path)
    logger.info("Config file loaded successfully.")

    # Start the async engine
    logger.info("Starting CryptoM service...")
    try:
        asyncio.run(_start_async(config))
    except KeyboardInterrupt:
        # This catches Ctrl+C on Windows when add_signal_handler is not available
        logger.info("Received KeyboardInterrupt, shutting down...")


app = argparse.ArgumentParser(description="CryptoM service command line interface")
app.add_argument(
    "-c",
    "--config",
    type=str,
    default="config.yaml",
    help="Path to the configuration file (default: config.yaml)",
)
app.add_argument(
    "-l",
    "--log-level",
    type=str,
    default="INFO",
    help="Logging level (default: INFO)",
)
app.add_argument(
    "-t",
    "--ttl",
    type=int,
    default=60,
    help="Time to live for cached data in seconds (default: 60)",
)
app.set_defaults(func=start)


def main():
    args = app.parse_args()
    args.func(args.config)


if __name__ == "__main__":
    main()
