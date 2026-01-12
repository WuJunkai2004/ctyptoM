__version__ = "1.0.0"

import argparse
import sys
from pathlib import Path

import uvicorn
from loguru import logger

from .config import AppConfig
from .webapi import createApp


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

    # 1. Load configuration
    config_path = Path(config_str)
    if not config_path.exists():
        logger.error(f"Configuration file {config_path} does not exist.")
        sys.exit(1)
    preconfig = AppConfig.load(config_path)
    port = preconfig.port
    # Start the async engine
    logger.info("Starting CryptoM service...")
    api = createApp(config_path)
    # 2. Start the async engine in the background
    logger.info(f"Starting CryptoM API Server on port {port}...")
    uvicorn.run(api, host="0.0.0.0", port=port)


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
app.add_argument(
    "-v",
    "--version",
    action="version",
    version=f"CryptoM version {__version__}",
    help="Show the version of CryptoM",
)


def main():
    args = app.parse_args()
    start(args.config)


if __name__ == "__main__":
    main()
