import asyncio
import hashlib
import json
import logging
import os
import random
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

HOST = "0.0.0.0"
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
    "max_tokens": 1600,
}

CHARACTERS = {"gi": [], "hsr": []}
LOCATIONS = {"gi": [], "hsr": []}
FACTIONS = {"gi": [], "hsr": []}
PERSONAS = {"gi": {}, "hsr": {}}

current_game = None
ADMIN_KEY = os.environ.get("ADMIN_KEY", "change-me")
IP_QUOTA = {}
IP_QUOTA_FILE = Path(__file__).parent / "ip_quota.json"


def load_ip_quota():
    global IP_QUOTA
    if IP_QUOTA_FILE.exists():
        try:
            IP_QUOTA = json.loads(IP_QUOTA_FILE.read_text(encoding="utf-8"))
        except Exception:
            IP_QUOTA = {}


def save_ip_quota():
    IP_QUOTA_FILE.write_text(json.dumps(IP_QUOTA), encoding="utf-8")
FREE_TURNS = 40


def load_user_config():
    global ADMIN_KEY
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
                if cfg.get("admin_key"):
                    ADMIN_KEY = cfg["admin_key"]
                break
            except Exception:
                pass
    if DEEPSEEK_CONFIG["api_key"] == "sk-your-key-here":
        env_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if env_key:
            DEEPSEEK_CONFIG["api_key"] = env_key
    logger.info("Config: model=%s temp=%.1f", DEEPSEEK_CONFIG["model"], DEEPSEEK_CONFIG["temperature"])


def load_characters():
    for path in [
        Path(__file__).parent / "world_info.json",
        Path(__file__).parent / "character_personas.json",
    ]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "characters" in data.get("gi", {}) or "characters" in data.get("hsr", {}):
                for key in ("gi", "hsr"):
                    w = data.get(key, {})
                    CHARACTERS[key] = w.get("characters", [])
                    LOCATIONS[key] = w.get("locations", [])
                    FACTIONS[key] = w.get("factions", [])
            if isinstance(data.get("gi", {}), dict) and isinstance(list(data["gi"].values())[:1][0] if data["gi"] else "", str):
                for key in ("gi", "hsr"):
                    PERSONAS[key] = data.get(key, {})
        except Exception as e:
            logger.warning("Failed to load %s: %s", path, e)
    logger.info("World: %d+%d chars, %d+%d personas",
                len(CHARACTERS["gi"]), len(CHARACTERS["hsr"]),
                len(PERSONAS["gi"]), len(PERSONAS["hsr"]))


def build_fts5_query(q):
    if not q:
        return ""
    cleaned = re.sub(r'[*"()+^-]', " ", q)
    for kw in ("AND", "OR", "NOT", "NEAR"):
        cleaned = re.sub(rf"\b{kw}\b", " ", cleaned, flags=re.IGNORECASE)
    terms = cleaned.split()
    return " ".join(f'"{t}"' for t in terms) if terms else ""


def search_game_text(query, domain, limit=5):
    fts_query = build_fts5_query(query)
    if not fts_query or not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT d.name, d.content, d.doc_type, d.category, "
            "snippet(documents_fts, 1, '<mark>', '</mark>', '...', 40) AS snippet "
            "FROM documents_fts f JOIN documents d ON d.id = f.rowid "
            "WHERE documents_fts MATCH ? AND d.domain = ? ORDER BY rank LIMIT ?",
            (fts_query, domain, limit),
        ).fetchall()
        results = [{"name": r["name"], "snippet": r["snippet"], "content": (r["content"] or "")[:500]} for r in rows]
        conn.close()
        return results
    except sqlite3.Error:
        return []


class GameState:
    def __init__(self, world="gi", character="旅行者"):
        self.world = world
        self.character = character
        self.history = []
        self.summary = ""
        self.free_turns = FREE_TURNS
        self.has_own_key = False
        self.created_at = datetime.now().isoformat()

    def to_dict(self):
        return {
            "world": self.world, "character": self.character,
            "history": self.history, "summary": self.summary,
            "free_turns": self.free_turns, "has_own_key": self.has_own_key,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d):
        gs = cls()
        gs.world = d["world"]
        gs.character = d["character"]
        gs.history = d["history"]
        gs.summary = d.get("summary", "")
        gs.free_turns = d.get("free_turns", FREE_TURNS)
        gs.has_own_key = d.get("has_own_key", False)
        gs.created_at = d["created_at"]
        return gs

    def add_turn(self, role, content):
        self.history.append({"role": role, "content": content})


def save_game(name):
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in name if c.isalnum() or c in "_- ")
    filepath = SAVES_DIR / f"{safe}.json"
    filepath.write_text(json.dumps(current_game.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


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
    return sorted([p.stem for p in SAVES_DIR.glob("*.json")], key=lambda n: (SAVES_DIR / f"{n}.json").stat().st_mtime, reverse=True)


def delete_save(name):
    safe = "".join(c for c in name if c.isalnum() or c in "_- ")
    filepath = SAVES_DIR / f"{safe}.json"
    if filepath.exists():
        os.remove(filepath)
        return True
    return False


async def call_deepseek(messages, api_key=None):
    import httpx
    key = api_key or DEEPSEEK_CONFIG["api_key"]
    if key == "sk-your-key-here":
        raise Exception("No API key configured")
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{DEEPSEEK_CONFIG['base_url']}/chat/completions",
            json={"model": DEEPSEEK_CONFIG["model"], "messages": messages,
                  "temperature": DEEPSEEK_CONFIG["temperature"], "max_tokens": DEEPSEEK_CONFIG["max_tokens"],
                  "stream": False, "frequency_penalty": 0.3},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


PROMPT_NARRATIVE = """你是《{game_name}》世界的AI叙事者（DM）。你为玩家创作互动文字冒险故事。

## 红线
- 禁止编造游戏中不存在的地点、角色、组织、物品
- 禁止修改已有人物/地点的核心设定
- 禁止跨作品混合

## 已知世界信息
角色：{characters_section}
地点：{locations_section}
阵营：{factions_section}

## 当前故事状态
{story_state}

## 叙事原则
- 写确定存在的东西，不确定就写场景氛围和角色互动
- 用小说式描写：场景细节、角色神态、氛围、对话
- 2-4个段落，每段之间空行

## 输出格式
只输出叙事段落。最后一行必须是【故事状态】，格式如下：

（叙事段落）

【故事状态】
地点: xxx
在场角色: xxx
当前目标: xxx

当故事发展到一个自然的章节终点（重要事件完成、谜团解开、情感高潮后），
【故事状态】改为【冒险结束】，故事状态行只写"冒险结束"。

{rag_section}

开始游戏。"""

PROMPT_OPTIONS = """你是原神/崩铁文字冒险的游戏设计师。根据玩家当前面对的场景，设计4个有趣的行动选项。

## 场景上下文
游戏：{game_name}
地点：{location}
在场角色：{present}
当前目标：{goal}

## 最新叙事
{narrative}

## 玩家上一次选择
{last_choice}

## 选项设计原则
- 每个选项控制在15字以内，简洁有力
- 禁止"观察""继续""呼喊""离开"等空洞动词
- 4个选项必须覆盖：推进剧情 | 探索发现 | 人物互动 | 隐藏动作
- 每个选项要衔接上文，有悬念感

## 输出格式
【你的选择】
1. 选项内容
2. 选项内容
3. 选项内容
4. 选项内容"""


def smart_rag(query, domain, narrative=""):
    results = []
    if query:
        results.extend(search_game_text(query, domain, limit=3))
    chars = CHARACTERS.get(domain, [])
    personas = PERSONAS.get(domain, {})
    persona_lines = []
    if narrative and chars:
        mentioned = [c for c in chars if len(c) >= 2 and c in narrative]
        for char_name in mentioned[:3]:
            char_docs = search_game_text(char_name, domain, limit=2)
            for doc in char_docs:
                if doc not in results:
                    results.append(doc)
            if char_name in personas:
                persona_lines.append(f"[{char_name}] {personas[char_name][:200]}")
    results = results[:4]
    if persona_lines:
        results.append({"name": "角色语气", "snippet": " | ".join(persona_lines[:3]), "content": ""})
    return results[:6]


def build_rag_context(query, domain, narrative=""):
    if not os.path.exists(DB_PATH):
        return ""
    docs = smart_rag(query, domain, narrative)
    if not docs:
        return ""
    lines = ["## 世界观参考资料"]
    for i, d in enumerate(docs, 1):
        lines.append(f"{i}. [{d['name']}] {d['snippet']}")
    return "\n".join(lines)


def build_messages(game, rag_context, extra_user="开始冒险。", story_state=""):
    world = game.world
    game_name = GAME_NAMES.get(world, "米哈游")
    has_history = bool(game.history)

    if has_history:
        char_list = loc_list = fac_list = "（见上文）"
    else:
        char_list = "、".join(CHARACTERS.get(world, [])[:80]) or "（未加载）"
        loc_list = "、".join(LOCATIONS.get(world, [])[:30]) or "（未加载）"
        fac_list = "、".join(FACTIONS.get(world, [])[:30]) or "（未加载）"

    state = story_state or "新冒险刚开始，玩家尚未到达任何地点。"

    system_text = PROMPT_NARRATIVE.format(
        game_name=game_name, characters_section=char_list,
        locations_section=loc_list, factions_section=fac_list,
        story_state=state, rag_section=rag_context,
    )
    messages = [{"role": "system", "content": system_text}]

    if game.history:
        summary_idx = -1
        for i, m in enumerate(game.history):
            if m["role"] == "summary_marker":
                summary_idx = i
        if summary_idx >= 0 and game.summary:
            messages.append({"role": "assistant", "content": "【故事摘要】" + game.summary})
        recent = game.history[summary_idx + 1:][-6:]
        for m in recent:
            role = "assistant" if m["role"] == "system" else m["role"]
            messages.append({"role": role, "content": m["content"]})

    messages.append({"role": "user", "content": extra_user})
    return messages


async def maybe_summarize(game, api_key=None):
    if len(game.history) < 12:
        return
    last_summary_at = 0
    for i, m in enumerate(game.history):
        if m["role"] == "summary_marker":
            last_summary_at = i
    if len(game.history) - last_summary_at < 10:
        return
    to_summarize = game.history[last_summary_at:]
    story_text = "\n".join(m["content"][:300] for m in to_summarize if m["role"] == "system")
    old_summary = game.summary or "无"
    msg = [
        {"role": "system", "content": f"之前的故事摘要：{old_summary}\n\n用2-3句中文补充总结最近几轮的新进展，与之前摘要衔接。包含：发生了什么事、遇到了谁、当前目标。"},
        {"role": "user", "content": story_text},
    ]
    try:
        result = await call_deepseek(msg, api_key=api_key)
        game.summary = result.strip()[:200]
        game.history.insert(last_summary_at + 1, {"role": "summary_marker", "content": ""})
        logger.info("Summary updated: %s", game.summary[:80])
    except Exception as e:
        logger.warning("Summary failed: %s", e)


async def generate_options(narrative, world, story_state="", last_choice="", api_key=None):
    game_name = GAME_NAMES.get(world, "米哈游")
    location = "未知"
    present = "未知"
    goal = "未知"
    for line in story_state.split("\n"):
        if line.startswith("地点:"): location = line[3:].strip()
        elif line.startswith("在场角色:"): present = line[5:].strip()
        elif line.startswith("当前目标:"): goal = line[5:].strip()
    prompt = PROMPT_OPTIONS.format(
        game_name=game_name, location=location, present=present,
        goal=goal, narrative=narrative, last_choice=last_choice or "（新游戏）",
    )
    try:
        raw = await call_deepseek([{"role": "system", "content": prompt}], api_key=api_key)
        options = []
        for line in raw.strip().split("\n"):
            m = re.match(r"^(\d+)[.、)]?\s*(.*)", line.strip())
            if m:
                options.append(m.group(0).strip())
        if len(options) >= 2:
            return options
    except Exception as e:
        logger.warning("Options generation failed: %s", e)
    return None


def parse_narrative(text):
    narrative = text.strip()
    options = []
    story_state = ""
    is_ending = False

    ending_idx = text.find("【冒险结束】")
    if ending_idx >= 0:
        is_ending = True
        narrative = text[:ending_idx].strip()
        story_state = "冒险结束"
        return narrative, options, story_state, is_ending

    state_idx = text.find("【故事状态】")
    if state_idx >= 0:
        if narrative.endswith(text[state_idx:]):
            narrative = text[:state_idx].strip()
        story_state = text[state_idx + 6:].strip()

    choice_idx = text.find("【你的选择】")
    if choice_idx < 0:
        choice_idx = text.find("【你的選擇】")
    if choice_idx >= 0:
        narrative = text[:choice_idx].strip() if choice_idx < len(narrative) else narrative
        rest = text[choice_idx + 6:]
        st_idx = rest.find("【故事状态】")
        opt_text = rest[:st_idx] if st_idx >= 0 else rest
        for line in opt_text.strip().split("\n"):
            m = re.match(r"^(\d+)[.、)]?\s*(.*)", line.strip())
            if m:
                options.append(m.group(0).strip())
    if len(options) < 2:
        lines = text.split("\n")
        found = []
        for line in lines[-15:]:
            m = re.match(r"^(\d+)[.、)]?\s*(.*)", line.strip())
            if m:
                found.append(m.group(0).strip())
        if len(found) >= 2:
            options = found[:4]
            first_opt = found[0]
            if first_opt in narrative:
                narrative = narrative[:narrative.rfind(first_opt)].strip()
    if len(options) < 2:
        return narrative, [], story_state, is_ending
    return narrative, options, story_state, is_ending


def get_visitor_id(request):
    ip = request.headers.get("CF-Connecting-IP") or request.remote
    return hashlib.sha256(ip.encode()).hexdigest()[:12]


async def index(request):
    response = web.FileResponse(STATIC_DIR / "adventure_index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


async def handle_admin(request):
    if request.query.get("key") != ADMIN_KEY:
        return web.Response(
            content_type="text/html",
            text="""<html><body style="background:#0b0f1a;color:#e8ecf4;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh"><form>
<h2>Arc Admin</h2>
<input name="key" type="password" placeholder="密钥" autofocus style="background:#1a1a2e;color:#e8ecf4;border:1px solid #303060;padding:8px 12px;border-radius:6px;font-size:14px">
<button style="background:#2a2a4a;color:#c0c0ff;border:1px solid #4040a0;padding:8px 16px;border-radius:6px;cursor:pointer">进入</button>
</form></body></html>"""
        )
    html = "<h2>Visitors</h2><table border=1 cellpadding=5 style='font-family:monospace'>"
    for vid, remaining in sorted(IP_QUOTA.items(), key=lambda x: -x[1]):
        if remaining < 0:
            tag = "<span style='color:#22c55e'>own key</span>"
        elif remaining == 0:
            tag = "<span style='color:#ef4444'>out of free turns</span>"
        else:
            tag = f"<span style='color:#f59e0b'>{remaining} turns left</span>"
        html += f"<tr><td>{vid}</td><td>{tag}</td></tr>"
    html += "</table><br><a href='/arc-5813f'>登出</a>"
    return web.Response(text=html, content_type="text/html")


async def handle_start(request):
    global current_game
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    world = body.get("world", "gi")
    character = body.get("character", "旅行者")
    user_chosen_loc = body.get("location", "")
    user_key = body.get("api_key", "")
    if world not in ("gi", "hsr"):
        return web.json_response({"error": "world must be gi or hsr"}, status=400)

    current_game = GameState(world, character)
    if user_key:
        current_game.has_own_key = True
    logger.info("START: world=%s character=%s location=%s", world, character, user_chosen_loc or "random")

    locs = LOCATIONS.get(world, [])
    if user_chosen_loc and user_chosen_loc in locs:
        start_loc = user_chosen_loc
    else:
        start_loc = random.choice(locs) if locs else ""
    start_hint = f"从{start_loc}开始冒险。" if start_loc else "开始冒险。"

    rag = build_rag_context(character, world)
    if start_loc:
        loc_rag = build_rag_context(start_loc, world)
        if loc_rag:
            rag = rag + "\n\n" + loc_rag if rag else loc_rag
    messages = build_messages(current_game, rag, start_hint)
    try:
        raw = await call_deepseek(messages, api_key=user_key or None)
    except Exception as e:
        return web.json_response({"error": f"DeepSeek call failed: {e}"}, status=500)
    narrative, options, story_state, is_ending = parse_narrative(raw)
    if not options or len(options) < 2:
        gen_opts = await generate_options(narrative, world, story_state, api_key=user_key or None)
        if gen_opts:
            options = gen_opts
        else:
            options = ["1. 继续前进", "2. 仔细观察周围", "3. 转身离开", "4. 试着呼喊"]
    if is_ending:
        options = ["【结局】迎接冒险的终章"] + options[:3]
    current_game.add_turn("system", narrative)
    current_game.add_turn("user", character)
    return web.json_response({"narrative": narrative, "options": options, "story_state": story_state, "is_ending": is_ending})


async def handle_action(request):
    global current_game
    if current_game is None:
        return web.json_response({"error": "no active game, start first"}, status=400)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    choice = body.get("choice", "").strip()
    story_state = body.get("state", "")
    user_key = body.get("api_key", "")
    if not choice:
        return web.json_response({"error": "choice is required"}, status=400)

    # Handle ending choice
    if choice.startswith("【结局】"):
        game_name = GAME_NAMES.get(current_game.world, "米哈游")
        ending_prompt = f"你是{game_name}文字冒险的终章叙事者。基于冒险历程，用2-3段荡气回肠的结尾叙事收束整个故事，包括角色去向、世界变化和一句有韵味的结尾。只输出叙事，无需选项。"
        try:
            finale_raw = await call_deepseek([{"role": "system", "content": ending_prompt}], api_key=user_key or None)
        except Exception as e:
            return web.json_response({"error": f"Finale failed: {e}"}, status=500)
        current_game.add_turn("system", finale_raw)
        return web.json_response({"narrative": finale_raw, "options": [], "story_state": "", "is_ending": True})

    visitor_id = get_visitor_id(request)

    if user_key:
        current_game.has_own_key = True
    
    if not current_game.has_own_key:
        ip_left = IP_QUOTA.get(visitor_id, FREE_TURNS)
        if ip_left <= 0:
            return web.json_response({"error": "free_turns_exhausted"}, status=402)
        IP_QUOTA[visitor_id] = ip_left - 1
        save_ip_quota()
    
    if current_game.has_own_key:
        logger.info("ACTION: own-key visitor=%s", visitor_id)
    else:
        logger.info("ACTION: visitor=%s free_turns_left=%d", visitor_id, IP_QUOTA.get(visitor_id, FREE_TURNS))

    current_game.add_turn("user", choice)
    short_choice = re.sub(r"^\d+[.、]\s*", "", choice.strip())[:80]
    prev_narration = ""
    for m in reversed(current_game.history):
        if m["role"] == "system":
            prev_narration = m["content"]
            break
    rag = build_rag_context(short_choice, current_game.world, narrative=prev_narration)
    messages = build_messages(current_game, rag, f"我的选择：{choice}", story_state=story_state)
    try:
        raw = await call_deepseek(messages, api_key=user_key or None)
    except Exception as e:
        return web.json_response({"error": f"DeepSeek call failed: {e}"}, status=500)
    narrative, options, story_state, is_ending = parse_narrative(raw)
    if not options or len(options) < 2:
        gen_opts = await generate_options(narrative, current_game.world, story_state, choice, api_key=user_key or None)
        if gen_opts:
            options = gen_opts
        else:
            options = ["1. 继续前进", "2. 仔细观察周围", "3. 转身离开", "4. 试着呼喊"]
    if is_ending:
        options = ["【结局】迎接冒险的终章"] + options[:3]
    current_game.add_turn("system", narrative)
    await maybe_summarize(current_game, api_key=user_key or None)
    return web.json_response({"narrative": narrative, "options": options, "story_state": story_state, "is_ending": is_ending})


async def handle_context(request):
    global current_game
    if current_game is None:
        return web.json_response({"error": "no active game"}, status=400)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    choice = body.get("choice", "").strip()
    story_state = body.get("state", "")
    user_key = body.get("api_key", "")
    if not user_key:
        return web.json_response({"error": "own key required for direct mode"}, status=400)

    current_game.has_own_key = True
    prev_narration = ""
    for m in reversed(current_game.history):
        if m["role"] == "system":
            prev_narration = m["content"]
            break
    short_choice = re.sub(r"^\d+[.、]\s*", "", choice.strip())[:80]
    rag = build_rag_context(short_choice, current_game.world, narrative=prev_narration)
    messages = build_messages(current_game, rag, f"我的选择：{choice}", story_state=story_state)
    current_game.add_turn("user", choice)
    return web.json_response({"messages": messages})


async def handle_saves(request):  return web.json_response({"saves": list_saves()})
async def handle_save(request):
    global current_game
    if current_game is None: return web.json_response({"error": "no active game"}, status=400)
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        if not name: return web.json_response({"error": "name required"}, status=400)
        save_game(name)
        return web.json_response({"ok": True, "name": name})
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

async def handle_load(request):
    global current_game
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        if not name: return web.json_response({"error": "name required"}, status=400)
        game = load_game(name)
        if game is None: return web.json_response({"error": "save not found"}, status=404)
        return web.json_response(game.to_dict())
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

async def handle_delete(request):
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        if not name: return web.json_response({"error": "name required"}, status=400)
        ok = delete_save(name)
        if not ok: return web.json_response({"error": "save not found"}, status=404)
        return web.json_response({"ok": True})
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

async def handle_options(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    narrative = body.get("narrative", "")
    world = body.get("world", "gi")
    story_state = body.get("story_state", "")
    last_choice = body.get("last_choice", "")
    try:
        options = await generate_options(narrative, world, story_state, last_choice)
        return web.json_response({"options": options or []})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_restore(request):
    global current_game
    try:
        body = await request.json()
        world = body.get("world", "gi")
        character = body.get("character", "旅行者")
        history = body.get("history", [])
        if world not in ("gi", "hsr"): return web.json_response({"error": "invalid world"}, status=400)
        current_game = GameState(world, character)
        for turn in history:
            current_game.add_turn("system", turn.get("narrative", ""))
            current_game.add_turn("user", turn.get("choice", ""))
        return web.json_response({"ok": True})
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)


def create_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/arc-5813f", handle_admin)
    app.router.add_post("/api/adventure/start", handle_start)
    app.router.add_post("/api/adventure/action", handle_action)
    app.router.add_post("/api/adventure/context", handle_context)
    app.router.add_post("/api/adventure/restore", handle_restore)
    app.router.add_post("/api/adventure/options", handle_options)
    app.router.add_get("/api/adventure/saves", handle_saves)
    app.router.add_post("/api/adventure/save", handle_save)
    app.router.add_post("/api/adventure/load", handle_load)
    app.router.add_post("/api/adventure/delete", handle_delete)
    return app


def main():
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    load_ip_quota()
    load_user_config()
    load_characters()
    app = create_app()
    print(f"Server starting at http://{HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, handle_signals=True)


if __name__ == "__main__":
    main()
