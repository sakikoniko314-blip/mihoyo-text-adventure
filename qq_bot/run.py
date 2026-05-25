#!/usr/bin/env python3
"""
QQ Character Roleplay Bot - Main Entry Point
================================================

Setup Instructions:
1. Install dependencies:   pip install -r requirements.txt
2. Edit config.json:
   - Set ``deepseek.api_key`` to your DeepSeek API key.
   - Set ``bot.napcat_url`` to your NapCatQQ HTTP API address.
   - Adjust ``bot.port`` if needed (default 8080).
3. Configure NapCatQQ:
   - In NapCatQQ, add an HTTP reverse webhook pointing to this bot:
     URL: http://127.0.0.1:8080/
   - Ensure NapCatQQ's own HTTP API is enabled (default port 5700).
4. Run the bot:
     python run.py
   or make it executable and run directly:
     chmod +x run.py && ./run.py
5. The bot loads the character persona from ``characters/{name}.json``.
   Edit or add new character configs there.

Architecture:
   QQ → NapCatQQ → HTTP reverse → Bot Server (aiohttp) → RAG (FTS5) → DeepSeek API → Reply
"""

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from server import create_app, run_server

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("qq_bot")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config():
    """Load configuration from config.json (or exit if missing/malformed)."""
    config_path = Path(__file__).parent / "config.json"
    if not config_path.exists():
        logger.error("config.json not found at %s", config_path)
        sys.exit(1)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse config.json: %s", exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

shutdown_event = asyncio.Event()


def _signal_handler(sig, frame):
    if not shutdown_event.is_set():
        logger.info("Shutdown signal received, stopping...")
        shutdown_event.set()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    config = load_config()
    logger.info("Config loaded. Character: %s", config.get("character", "unknown"))

    app = create_app(config)
    host = config.get("bot", {}).get("host", "127.0.0.1")
    port = config.get("bot", {}).get("port", 8080)

    await run_server(app, host, port, shutdown_event)
    logger.info("Server stopped. Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
