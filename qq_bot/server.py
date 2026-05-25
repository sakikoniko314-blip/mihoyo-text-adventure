"""
OneBot v11 HTTP Server

Listens for NapCatQQ reverse-webhook events and routes text messages
to the character engine.  Replies are sent back via NapCatQQ's HTTP API.
"""

import asyncio
import json
import logging
from typing import Any

import aiohttp
from aiohttp import web

from character import CharacterPersona
from engine import Engine

logger = logging.getLogger("qq_bot.server")


# ---------------------------------------------------------------------------
# OneBot event parsing helpers
# ---------------------------------------------------------------------------

def _extract_text(msg_segments: list[dict]) -> str:
    """Concatenate all text segments from a OneBot message array."""
    parts = []
    for seg in msg_segments:
        if seg.get("type") == "text":
            parts.append(seg.get("data", {}).get("text", ""))
    return "".join(parts).strip()


def _is_at_bot(msg_segments: list[dict], self_id: int) -> bool:
    """Check if any segment is an @mention of the bot."""
    for seg in msg_segments:
        if seg.get("type") == "at":
            qq = seg.get("data", {}).get("qq", "")
            if str(qq) == str(self_id):
                return True
    return False


def _should_reply(event: dict, persona) -> bool:
    """
    Determine whether the bot should generate a reply for this event.

    Rules:
    - Ignore bot's own messages.
    - Private messages: always reply.
    - Group messages: only reply when @-mentioned or the character name appears.
    """
    if event.get("sender", {}).get("user_id") == event.get("self_id"):
        return False

    msg_type = event.get("message_type")
    if msg_type == "private":
        return True

    if msg_type == "group":
        msg_segments = event.get("message", [])
        if _is_at_bot(msg_segments, event.get("self_id", 0)):
            return True
        text = _extract_text(msg_segments)
        if "昔涟" in text or "Xilian" in text:
            return True
        return False


# ---------------------------------------------------------------------------
# NapCatQQ API sender
# ---------------------------------------------------------------------------

class NapCatSender:
    def __init__(self, api_url: str, token: str = ""):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def send_reply(self, event: dict, text: str) -> None:
        msg_type = event.get("message_type")
        if msg_type == "group":
            endpoint = "/send_group_msg"
            payload = {
                "group_id": event.get("group_id"),
                "message": text,
            }
        elif msg_type == "private":
            endpoint = "/send_private_msg"
            payload = {
                "user_id": event.get("user_id"),
                "message": text,
            }
        else:
            logger.warning("Unknown message_type %r; cannot send reply.", msg_type)
            return

        await self._post(endpoint, payload)

    async def get_group_history(self, group_id: int, count: int = 20) -> list[dict]:
        payload = {"group_id": group_id, "count": count}
        try:
            session = await self._get_session()
            url = f"{self.api_url}/get_group_msg_history"
            if self.token:
                url += f"?access_token={self.token}"
            resp = await session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10),
            )
            if resp.status == 200:
                data = await resp.json()
                return data.get("data", {}).get("messages", [])
        except Exception:
            logger.exception("Failed to fetch group history")
        return []

    async def _post(self, endpoint: str, payload: dict) -> None:
        try:
            session = await self._get_session()
            url = f"{self.api_url}{endpoint}"
            if self.token:
                url += f"?access_token={self.token}"
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("NapCatQQ API error: status=%s body=%s", resp.status, body)
        except Exception:
            logger.exception("Failed to send via NapCatQQ API")

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()


# ---------------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------------

def create_app(config: dict) -> web.Application:
    """Create and configure the aiohttp Application."""
    app = web.Application()

    # Attach shared state
    app["config"] = config
    app["engine"] = Engine(config)
    app["sender"] = NapCatSender(
        config.get("bot", {}).get("napcat_url", "http://127.0.0.1:5700"),
        config.get("bot", {}).get("access_token", "")
    )
    app["persona"] = CharacterPersona(config.get("character", "paimon"))

    app.router.add_get("/health", health_handler)
    app.router.add_post("/", onebot_handler)

    return app


async def health_handler(request: web.Request) -> web.Response:
    """Simple health-check endpoint."""
    return web.json_response({"status": "ok"})


async def onebot_handler(request: web.Request) -> web.Response:
    """Handle incoming OneBot v11 events from NapCatQQ."""
    try:
        event = await request.json()
    except json.JSONDecodeError:
        logger.warning("Received non-JSON POST body")
        return web.json_response({"status": "ignored"})

    post_type = event.get("post_type")
    if post_type != "message":
        # Ignore meta_event, notice, request, etc.
        return web.json_response({"status": "ignored"})

    config = request.app["config"]
    engine: Engine = request.app["engine"]
    sender: NapCatSender = request.app["sender"]
    persona: CharacterPersona = request.app["persona"]

    # Ignore bot's own messages
    if event.get("user_id") == event.get("self_id"):
        return web.json_response({"status": "ignored"})

    # Hot-reload character config if changed
    persona.reload_if_changed()

    # Determine if we should reply
    if not _should_reply(event, persona):
        return web.json_response({"status": "ignored"})

    msg_segments = event.get("message", [])
    text = _extract_text(msg_segments)
    if not text:
        return web.json_response({"status": "ignored"})

    user_id = event.get("user_id")
    group_id = event.get("group_id")
    session_key = f"{user_id}:{group_id}" if group_id else f"{user_id}:private"

    logger.info(
        "[%s] %s message from %s: %s",
        session_key,
        event.get("message_type"),
        user_id,
        text[:80],
    )

    group_context = None
    if group_id:
        engine.add_group_msg(group_id, user_id, text)
        group_context = engine.get_group_ctx(group_id)

    try:
        reply = await engine.generate(session_key, text, persona, group_context)
    except Exception:
        logger.exception("Engine failed to generate reply")
        return web.json_response({"status": "error"})

    if reply:
        if "[PASS]" in reply:
            return web.json_response({"status": "ignored"})
        return web.json_response({"reply": reply, "at_sender": False})
    return web.json_response({"status": "ok"})


async def _process_message(engine, sender, persona, session_key, text, event):
    pass


# ---------------------------------------------------------------------------
# Server runner
# ---------------------------------------------------------------------------

async def run_server(app: web.Application, host: str, port: int, shutdown_event: asyncio.Event) -> None:
    """Start the aiohttp server and wait for shutdown signal."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("OneBot v11 server listening on http://%s:%s", host, port)

    try:
        await shutdown_event.wait()
    finally:
        logger.info("Shutting down server...")
        sender: NapCatSender = app["sender"]
        await sender.close()
        engine: Engine = app["engine"]
        await engine.close()
        await runner.cleanup()
