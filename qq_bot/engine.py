"""
RAG + LLM Pipeline

- Searches the Mihoyo FTS5 database for relevant game text.
- Builds a system prompt enriched with retrieved context.
- Calls DeepSeek API for character replies.
"""

import asyncio
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("qq_bot.engine")

# ---------------------------------------------------------------------------
# FTS5 query builder (inlined from mhy_search.py to avoid desktop deps)
# ---------------------------------------------------------------------------

import re


def build_fts5_query(q):
    """Build an FTS5 MATCH query string from raw user input."""
    if not q:
        return ""
    cleaned = re.sub(r'[*"()+^-]', " ", q)
    for kw in ("AND", "OR", "NOT", "NEAR"):
        cleaned = re.sub(rf"\b{kw}\b", " ", cleaned, flags=re.IGNORECASE)
    terms = cleaned.split()
    if not terms:
        return ""
    return " ".join(f'"{t}"' for t in terms)

# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------

def _resolve_db_path(config_db_path: str) -> str:
    """Resolve DB path relative to qq_bot/ if not absolute."""
    if os.path.isabs(config_db_path):
        return config_db_path
    base = Path(__file__).parent
    return str(base / config_db_path)


def _search_context_sync(db_path: str, query: str, domain: str, limit: int) -> list[dict]:
    """
    Synchronous FTS5 search; meant to be run in a thread via asyncio.to_thread.
    """
    if not query or not query.strip():
        return []

    db_path = _resolve_db_path(db_path)
    if not os.path.exists(db_path):
        logger.warning("Database not found at %s", db_path)
        return []

    fts_query = build_fts5_query(query)
    if not fts_query:
        return []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        logger.error("Failed to open DB: %s", exc)
        return []

    filters: list[str] = []
    params: list[Any] = [fts_query]

    if domain and str(domain).strip():
        filters.append("d.domain = ?")
        params.append(str(domain).strip())

    filter_sql = " AND ".join(filters) if filters else "1=1"

    try:
        rows = conn.execute(
            f"SELECT d.name, d.content, d.doc_type, d.category, d.relative_path, "
            f"snippet(documents_fts, 1, '<mark>', '</mark>', '...', 64) AS snippet "
            f"FROM documents_fts f "
            f"JOIN documents d ON d.id = f.rowid "
            f"WHERE documents_fts MATCH ? AND {filter_sql} "
            f"ORDER BY rank "
            f"LIMIT ?",
            params + [limit],
        ).fetchall()
    except sqlite3.Error as exc:
        logger.error("FTS5 query failed: %s", exc)
        conn.close()
        return []

    results = []
    for row in rows:
        results.append({
            "name": row["name"],
            "content": row["content"],
            "doc_type": row["doc_type"],
            "category": row["category"],
            "relative_path": row["relative_path"],
            "snippet": row["snippet"],
        })

    conn.close()
    return results


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _enrich_system_prompt(persona_prompt: str, context: list[dict]) -> str:
    """Append retrieved game text as reference material to the persona prompt."""
    if not context:
        return persona_prompt

    lines = [persona_prompt, "", "【参考资料——来自游戏文本】（仅作参考，不必逐条引用，自然融入对话即可）"]
    for i, doc in enumerate(context, 1):
        name = doc.get("name") or "未知文档"
        content = doc.get("content", "")[:600]
        snippet = doc.get("snippet", "")
        if snippet:
            lines.append(f"{i}. [{name}] …{snippet}…")
        else:
            lines.append(f"{i}. [{name}] {content[:300]}")
    lines.append("")
    return "\n".join(lines)


def _format_history(history: list[tuple[str, str]]) -> list[dict]:
    """Convert internal history tuples to OpenAI message dicts."""
    messages = []
    for role, content in history:
        messages.append({"role": role, "content": content})
    return messages


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class Engine:
    def __init__(self, config: dict):
        self.config = config
        self.db_path = config.get("db_path", "../retrieval/data/zlb.db")
        self.max_history = 5
        self.client: httpx.AsyncClient | None = None
        self._db_write_path = _resolve_db_path(self.db_path)
        self._init_db()

    def _init_db(self):
        try:
            db = sqlite3.connect(self._db_write_path)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS chat_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT,
                user_id TEXT,
                sender_id TEXT,
                text TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            db.commit()
            db.close()
        except Exception:
            logger.exception("Failed to initialize chat_memory table")

    def _get_client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=60.0)
        return self.client

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None

    def _get_dialogue_examples(self, query: str) -> list[str]:
        try:
            fts = build_fts5_query(query)
            if not fts:
                return []
            db = sqlite3.connect(f"file:{self._db_write_path}?mode=ro", uri=True)
            rows = db.execute(
                "SELECT text FROM xilian_dialogues_fts WHERE xilian_dialogues_fts MATCH ? ORDER BY rank LIMIT 10",
                (fts,),
            ).fetchall()
            db.close()
            return [r[0] for r in rows]
        except Exception:
            return []

    def _add_message_sync(self, group_id: str, user_id: str, sender_id: str, text: str):
        try:
            db = sqlite3.connect(self._db_write_path)
            db.execute(
                "INSERT INTO chat_memory (group_id, user_id, sender_id, text) VALUES (?,?,?,?)",
                (group_id, user_id, sender_id, text),
            )
            db.execute(
                "DELETE FROM chat_memory WHERE group_id=? AND user_id=? AND id NOT IN ("
                "SELECT id FROM chat_memory WHERE group_id=? AND user_id=? "
                "ORDER BY id DESC LIMIT 200)",
                (group_id, user_id, group_id, user_id),
            )
            db.commit()
            db.close()
        except Exception:
            logger.exception("Failed to store chat memory")

    def _get_recent_sync(self, group_id: str, user_id: str, limit: int) -> list[dict]:
        try:
            db = sqlite3.connect(f"file:{self._db_write_path}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id, group_id, user_id, sender_id, text, timestamp "
                "FROM chat_memory WHERE group_id=? AND user_id=? "
                "ORDER BY id DESC LIMIT ?",
                (group_id, user_id, limit),
            ).fetchall()
            db.close()
            return [dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to read chat memory")
            return []

    async def add_message(self, group_id: str, user_id: str, sender_id: str, text: str):
        await asyncio.to_thread(self._add_message_sync, group_id, user_id, sender_id, text)

    async def get_recent_messages(self, group_id: str = "", user_id: str = "", limit: int = 20) -> list[dict]:
        return await asyncio.to_thread(self._get_recent_sync, group_id, user_id, limit)

    async def get_conversation(self, user_id: str, limit: int = 10) -> list[dict]:
        return await asyncio.to_thread(self._get_recent_sync, "", str(user_id), limit)

    def add_group_msg(self, group_id, user_id, text):
        self._add_message_sync(str(group_id), "", str(user_id), text)

    def get_group_ctx(self, group_id):
        rows = self._get_recent_sync(str(group_id), "", 20)
        return [f"[{r['sender_id']}]: {r['text']}" for r in reversed(rows) if r['sender_id']]

    async def search_context(self, query: str, domain: str = "", limit: int = 5) -> list[dict]:
        """Async wrapper around the synchronous FTS5 search."""
        return await asyncio.to_thread(_search_context_sync, self.db_path, query, domain, limit)

    def build_prompt(
        self,
        persona,
        context: list[dict],
        user_msg: str,
        history: list[tuple[str, str]],
        group_context: list[str] = None,
    ) -> list[dict]:
        persona_text = persona.get_persona()
        system_text = _enrich_system_prompt(persona_text, context)

        examples = self._get_dialogue_examples(user_msg)
        if examples:
            system_text += "\n\n【昔涟在类似情境下是这样说话的】\n" + "\n".join(examples)

        if group_context:
            system_text += "\n\n【最近群聊消息——你刚打开QQ看到这些】\n"
            system_text += "\n".join(group_context[-10:])

        messages = [{"role": "system", "content": system_text}]
        messages.extend(_format_history(history))
        messages.append({"role": "user", "content": user_msg})
        return messages

    async def _web_search(self, query: str) -> str:
        try:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(None, self._ddg_search_sync, query)
            if results:
                return "；".join(results[:3])
        except Exception:
            pass
        return ""

    def _ddg_search_sync(self, query: str) -> list[str]:
        from ddgs import DDGS
        try:
            with DDGS() as ddgs:
                return [r["body"] for r in ddgs.text(query, max_results=3)]
        except Exception:
            return []

    async def generate_reply(self, messages: list[dict]) -> str:
        ds_cfg = self.config.get("deepseek", {})
        api_key = ds_cfg.get("api_key", "")
        base_url = ds_cfg.get("base_url", "https://api.deepseek.com").rstrip("/")
        model = ds_cfg.get("model", "deepseek-chat")
        temperature = ds_cfg.get("temperature", 0.7)
        max_tokens = ds_cfg.get("max_tokens", 500)

        if not api_key or api_key == "YOUR_API_KEY_HERE":
            logger.error("DeepSeek API key not configured")
            return "（配置错误：API 密钥未设置）"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        client = self._get_client()
        try:
            resp = await client.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            choice = data.get("choices", [{}])[0]
            reply = choice.get("message", {}).get("content", "")
            return reply.strip()
        except httpx.HTTPStatusError as exc:
            logger.error("DeepSeek API HTTP error: %s - %s", exc.response.status_code, exc.response.text)
            return "（API 调用失败，稍后再试哦）"
        except httpx.RequestError as exc:
            logger.error("DeepSeek API request error: %s", exc)
            return "（网络有点问题，稍后再试哦）"
        except Exception:
            logger.exception("Unexpected error calling DeepSeek API")
            return "（出了点小状况，稍后再试哦）"

    async def generate(self, session_key: str, user_msg: str, persona, group_context: list[str] = None) -> str:
        character = persona.data
        domain = character.get("game", "")
        parts = session_key.rsplit(":", 1)
        user_qq = parts[0]
        suffix = parts[1]
        if suffix == "private":
            group_id = ""
            context_id = user_qq
        else:
            group_id = suffix
            context_id = ""

        try:
            context = await self.search_context(user_msg, domain=domain, limit=15)
        except Exception:
            logger.exception("Context retrieval failed")
            context = []

        web_triggers = ["搜索", "查一下", "查查", "搜一下", "帮你查", "上网查", "百度", "谷歌", "帮我搜", "网上说", "网上怎么", "搜搜", "帮我查", "查一查"]
        if any(t in user_msg for t in web_triggers):
            web = await self._web_search(user_msg)
            if web:
                context.append({"name": "联网搜索", "snippet": web, "content": web})

        recent_raw = self._get_recent_sync(group_id, context_id, 50)
        if group_id:
            recent = [r for r in recent_raw if r["sender_id"] in (user_qq, "")]
        else:
            recent = recent_raw

        fts = build_fts5_query(user_msg)
        if fts:
            try:
                db = sqlite3.connect(f"file:{self._db_write_path}?mode=ro", uri=True)
                rows = db.execute(
                    "SELECT sender_id, text FROM chat_memory_fts WHERE chat_memory_fts MATCH ? ORDER BY rank LIMIT 10",
                    (fts,),
                ).fetchall()
                db.close()
                recent = [{"sender_id": r[0], "text": r[1]} for r in rows]
            except Exception:
                recent = recent[-8:]

        history: list[tuple[str, str]] = []
        for r in reversed(recent):
            role = "user" if r["sender_id"] else "assistant"
            history.append((role, r["text"]))

        messages = self.build_prompt(persona, context, user_msg, history, group_context)

        reply = await self.generate_reply(messages)
        if not reply:
            return ""

        await self.add_message(group_id, context_id, user_qq, user_msg)
        await self.add_message(group_id, context_id, "", reply)

        return reply
