import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(Path(__file__).parent / "adventure.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("adventure")

sys.path.insert(0, str(Path(__file__).parent / "retrieval"))

HOST = "127.0.0.1"
PORT = 8888
SAVES_DIR = Path(__file__).parent / "saves"
STATIC_DIR = Path(__file__).parent
DB_PATH = str(Path(__file__).parent / "retrieval" / "data" / "zlb.db")

GAME_NAMES = {"gi": "原神", "hsr": "崩坏：星穹铁道"}

DEEPSEEK_CONFIG = {
    "api_key": "sk-your-key-here",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "temperature": 0.3,
    "max_tokens": 800,
}


def load_user_config():
    for cfg_path in [
        Path(__file__).parent / "adventure_config.json",
        Path(__file__).parent / "qq_bot" / "config.json",
    ]:
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                ds = cfg.get("deepseek", {})
                if ds.get("api_key"):
                    DEEPSEEK_CONFIG["api_key"] = ds["api_key"]
                if ds.get("base_url"):
                    DEEPSEEK_CONFIG["base_url"] = ds.get("base_url").rstrip("/")
                break
            except Exception:
                pass
    if DEEPSEEK_CONFIG["api_key"] == "sk-your-key-here":
        env_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if env_key:
            DEEPSEEK_CONFIG["api_key"] = env_key
    logger.info("Config: model=%s temp=%.1f max_tokens=%d",
                DEEPSEEK_CONFIG["model"], DEEPSEEK_CONFIG["temperature"],
                DEEPSEEK_CONFIG["max_tokens"])


CHARACTERS = {"gi": [], "hsr": []}
LOCATIONS = {"gi": [], "hsr": []}
FACTIONS = {"gi": [], "hsr": []}


def load_characters():
    path = Path(__file__).parent / "world_info.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in ("gi", "hsr"):
                w = data.get(key, {})
                CHARACTERS[key] = w.get("characters", [])
                LOCATIONS[key] = w.get("locations", [])
                FACTIONS[key] = w.get("factions", [])
            logger.info("Loaded %d GI + %d HSR characters, %d+%d locations, %d+%d factions",
                        len(CHARACTERS["gi"]), len(CHARACTERS["hsr"]),
                        len(LOCATIONS["gi"]), len(LOCATIONS["hsr"]),
                        len(FACTIONS["gi"]), len(FACTIONS["hsr"]))
        except Exception as e:
            logger.warning("Failed to load world_info.json: %s", e)


def build_fts5_query(q):
    if not q:
        return ""
    cleaned = re.sub(r'[*"()+^-]', " ", q)
    for kw in ("AND", "OR", "NOT", "NEAR"):
        cleaned = re.sub(rf"\b{kw}\b", " ", cleaned, flags=re.IGNORECASE)
    terms = cleaned.split()
    if not terms:
        return ""
    return " ".join(f'"{t}"' for t in terms)


def search_game_text(query, domain, limit=5):
    fts_query = build_fts5_query(query)
    if not fts_query:
        return []

    if not os.path.exists(DB_PATH):
        print(f"[WARN] DB not found at {DB_PATH}, RAG disabled")
        return []

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        print(f"[WARN] Failed to open DB: {e}")
        return []

    try:
        rows = conn.execute(
            "SELECT d.name, d.content, d.doc_type, d.category, "
            "snippet(documents_fts, 1, '<mark>', '</mark>', '...', 40) AS snippet "
            "FROM documents_fts f "
            "JOIN documents d ON d.id = f.rowid "
            "WHERE documents_fts MATCH ? AND d.domain = ? "
            "ORDER BY rank "
            "LIMIT ?",
            (fts_query, domain, limit),
        ).fetchall()
    except sqlite3.Error as e:
        print(f"[WARN] FTS5 query failed: {e}")
        conn.close()
        return []

    results = []
    for row in rows:
        results.append({
            "name": row["name"],
            "snippet": row["snippet"],
            "content": (row["content"] or "")[:500],
        })
    conn.close()
    return results


class GameState:
    def __init__(self, world="gi", character="旅行者"):
        self.world = world
        self.character = character
        self.history = []
        self.created_at = datetime.now().isoformat()

    def to_dict(self):
        return {
            "world": self.world,
            "character": self.character,
            "history": self.history,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d):
        gs = cls()
        gs.world = d["world"]
        gs.character = d["character"]
        gs.history = d["history"]
        gs.created_at = d["created_at"]
        return gs

    def add_turn(self, role, content):
        self.history.append({"role": role, "content": content})


current_game = None


def save_game(name):
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in name if c.isalnum() or c in "_- ")
    filepath = SAVES_DIR / f"{safe}.json"
    filepath.write_text(json.dumps(current_game.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(filepath)


def load_game(name):
    global current_game
    safe = "".join(c for c in name if c.isalnum() or c in "_- ")
    filepath = SAVES_DIR / f"{safe}.json"
    if not filepath.exists():
        return None
    data = json.loads(filepath.read_text(encoding="utf-8"))
    current_game = GameState.from_dict(data)
    return current_game


def list_saves():
    if not SAVES_DIR.exists():
        return []
    return sorted(
        [p.stem for p in SAVES_DIR.glob("*.json")],
        key=lambda n: (SAVES_DIR / f"{n}.json").stat().st_mtime,
        reverse=True,
    )


def delete_save(name):
    safe = "".join(c for c in name if c.isalnum() or c in "_- ")
    filepath = SAVES_DIR / f"{safe}.json"
    if filepath.exists():
        os.remove(filepath)
        return True
    return False


async def call_deepseek(messages):
    import httpx
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_CONFIG['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_CONFIG["model"],
        "messages": messages,
        "temperature": DEEPSEEK_CONFIG["temperature"],
        "max_tokens": DEEPSEEK_CONFIG["max_tokens"],
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{DEEPSEEK_CONFIG['base_url']}/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


PROMPT_SYSTEM_TEMPLATE = """你是《{game_name}》世界的AI叙事者（DM）。你为玩家创作互动文字冒险故事。

## 红线（绝对禁止）
- 禁止编造游戏中不存在的地点、角色、组织、物品
- 禁止修改已有人物/地点的核心设定
- 禁止跨作品混合

## 已知世界信息（你可使用的元素）
角色：{characters_section}
地点：{locations_section}
阵营/组织：{factions_section}

## 当前故事状态
{story_state}

## 输出格式
每轮输出：2-4段叙事 + 【你的选择】分隔符 + 4个选项（每行"数字. 内容"）

## 叙事原则
- 写你确定存在的东西，不确定就写场景氛围和角色互动，不写具体设定
- 用小说式描写：场景细节、角色神态、氛围、对话
- 选项要有有意义的差异，引导不同方向

{rag_section}

开始游戏。"""


def smart_rag(query, domain, narrative=""):
    results = []

    if query:
        results.extend(search_game_text(query, domain, limit=3))

    chars = CHARACTERS.get(domain, [])
    if narrative and chars:
        mentioned = [c for c in chars if len(c) >= 2 and c in narrative]
        for char_name in mentioned[:3]:
            char_docs = search_game_text(char_name, domain, limit=2)
            for doc in char_docs:
                if doc not in results:
                    results.append(doc)

    return results[:6]


def build_rag_context(query, domain, narrative=""):
    if not os.path.exists(DB_PATH):
        logger.warning("RAG: DB not found at %s", DB_PATH)
        return ""
    docs = smart_rag(query, domain, narrative)
    logger.info("RAG: query='%s' domain=%s characters_found=%d -> %d docs",
                query[:40], domain,
                sum(1 for c in CHARACTERS.get(domain, []) if narrative and c in narrative),
                len(docs))
    if not docs:
        return ""
    lines = ["## 世界观参考资料（来自游戏文本，请严格遵循）"]
    for i, d in enumerate(docs, 1):
        name = d["name"]
        snip = d["snippet"]
        lines.append(f"{i}. [{name}] {snip}")
    return "\n".join(lines)


def build_messages(game, rag_context, extra_user="开始冒险。", story_state=""):
    world = game.world
    game_name = GAME_NAMES.get(world, "米哈游")

    chars = CHARACTERS.get(world, [])
    char_list = "、".join(chars[:80]) if chars else "（未加载）"

    locs = LOCATIONS.get(world, [])
    loc_list = "、".join(locs[:30]) if locs else "（未加载）"

    factions = FACTIONS.get(world, [])
    fac_list = "、".join(factions[:30]) if factions else "（未加载）"

    state = story_state if story_state else "新冒险刚开始，玩家尚未到达任何地点。"

    system_text = PROMPT_SYSTEM_TEMPLATE.format(
        game_name=game_name,
        characters_section=char_list,
        locations_section=loc_list,
        factions_section=fac_list,
        story_state=state,
        rag_section=rag_context,
    )
    messages = [{"role": "system", "content": system_text}]

    if game.history:
        recent = game.history[-8:]
        for m in recent:
            if m["role"] == "system":
                messages.append({"role": "assistant", "content": m["content"]})
            else:
                messages.append({"role": m["role"], "content": m["content"]})

    messages.append({"role": "user", "content": extra_user})
    return messages


def parse_narrative(text):
    parts = text.split("【你的选择】")
    narrative = parts[0].strip() if parts else text.strip()
    options = []
    if len(parts) > 1:
        option_text = parts[1]
        for line in option_text.strip().split("\n"):
            match = re.match(r"^(\d+)[.、]?\s*(.*)", line.strip())
            if match:
                options.append(match.group(0).strip())
    if len(options) < 2:
        return narrative, ["1. 继续前进", "2. 仔细观察周围", "3. 转身离开", "4. 试着呼喊"]
    return narrative, options


async def index(request):
    return web.FileResponse(STATIC_DIR / "adventure_index.html")


async def handle_start(request):
    global current_game
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    world = body.get("world", "gi")
    character = body.get("character", "旅行者")
    if world not in ("gi", "hsr"):
        return web.json_response({"error": "world must be gi or hsr"}, status=400)

    current_game = GameState(world, character)
    logger.info("START game: world=%s character=%s", world, character)
    rag = build_rag_context(character, world)
    messages = build_messages(current_game, rag)

    try:
        raw = await call_deepseek(messages)
    except Exception as e:
        logger.error("DeepSeek call failed: %s", e)
        return web.json_response({"error": f"DeepSeek call failed: {e}"}, status=500)

    narrative, options = parse_narrative(raw)
    current_game.add_turn("system", raw)
    current_game.add_turn("user", character)
    logger.info("START done: narrative=%d chars options=%d", len(narrative), len(options))

    return web.json_response({"narrative": narrative, "options": options})


async def handle_action(request):
    global current_game
    if current_game is None:
        logger.warning("ACTION denied: no current_game")
        return web.json_response({"error": "no active game, start first"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    choice = body.get("choice", "").strip()
    story_state = body.get("state", "")
    if not choice:
        return web.json_response({"error": "choice is required"}, status=400)
    logger.info("ACTION: choice=%s state=%s", choice[:60], story_state[:60])

    current_game.add_turn("user", choice)

    keyword = choice
    if current_game.history:
        recent_user = [m["content"] for m in current_game.history if m["role"] == "user"]
        keyword = " ".join(recent_user[-3:])

    prev_narration = ""
    if current_game.history:
        for m in reversed(current_game.history):
            if m["role"] == "system":
                prev_narration = m["content"]
                break

    rag = build_rag_context(keyword, current_game.world, narrative=prev_narration)

    prev_summary = prev_narration[:300]
    extra = f"上一轮故事概要：{prev_summary}\n\n我的选择：{choice}"

    messages = build_messages(current_game, rag, extra, story_state=story_state)

    try:
        raw = await call_deepseek(messages)
    except Exception as e:
        return web.json_response({"error": f"DeepSeek call failed: {e}"}, status=500)

    narrative, options = parse_narrative(raw)
    current_game.add_turn("system", raw)

    return web.json_response({"narrative": narrative, "options": options})


async def handle_saves(request):
    saves = list_saves()
    return web.json_response({"saves": saves})


async def handle_save(request):
    global current_game
    if current_game is None:
        return web.json_response({"error": "no active game"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    name = body.get("name", "").strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    save_game(name)
    return web.json_response({"ok": True, "name": name})


async def handle_load(request):
    global current_game
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    name = body.get("name", "").strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    game = load_game(name)
    if game is None:
        return web.json_response({"error": "save not found"}, status=404)

    return web.json_response(game.to_dict())


async def handle_delete(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    name = body.get("name", "").strip()
    if not name:
        return web.json_response({"error": "name is required"}, status=400)

    ok = delete_save(name)
    if not ok:
        return web.json_response({"error": "save not found"}, status=404)

    return web.json_response({"ok": True})


async def handle_restore(request):
    global current_game
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    world = body.get("world", "gi")
    character = body.get("character", "旅行者")
    history = body.get("history", [])
    if world not in ("gi", "hsr"):
        return web.json_response({"error": "world must be gi or hsr"}, status=400)

    current_game = GameState(world, character)
    for turn in history:
        current_game.add_turn("system", turn.get("narrative", ""))
        current_game.add_turn("user", turn.get("choice", ""))
    logger.info("RESTORE: world=%s character=%s history=%d turns", world, character, len(history))
    return web.json_response({"ok": True})


def create_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/api/adventure/start", handle_start)
    app.router.add_post("/api/adventure/action", handle_action)
    app.router.add_post("/api/adventure/restore", handle_restore)
    app.router.add_get("/api/adventure/saves", handle_saves)
    app.router.add_post("/api/adventure/save", handle_save)
    app.router.add_post("/api/adventure/load", handle_load)
    app.router.add_post("/api/adventure/delete", handle_delete)
    return app


def main():
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    load_user_config()
    load_characters()
    app = create_app()
    print(f"Server starting at http://{HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, handle_signals=True)


if __name__ == "__main__":
    main()
