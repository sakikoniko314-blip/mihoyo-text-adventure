# Text Adventure Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-page web app where the user plays an AI-driven interactive text adventure set in Genshin Impact / Honkai Star Rail, with RAG-grounded storytelling via FTS5 + DeepSeek.

**Architecture:** Two new files — `adventure_server.py` (aiohttp backend, ~250 lines) and `adventure_index.html` (single-page frontend, ~350 lines). Server holds game state in memory, saves to JSON files in `saves/`. All API calls go through DeepSeek, with FTS5 search for world-grounded context injection. Zero new dependencies.

**Tech Stack:** Python 3, aiohttp, httpx, sqlite3, vanilla HTML/CSS/JS

---

## File Summary

| File | Action | Purpose |
|------|--------|---------|
| `adventure_server.py` | CREATE | Backend server — routes, RAG, DeepSeek, save/load |
| `adventure_index.html` | CREATE | Frontend — UI, state management, API calls |
| `saves/` | CREATE | Directory for JSON save files |

No existing files are modified.

---

### Task 1: Server Skeleton with Static File Serving

**Files:**
- Create: `adventure_server.py`

- [ ] **Step 1: Create the minimal server that serves the HTML and has route stubs**

```python
import asyncio
import json
import os
from pathlib import Path

from aiohttp import web

# We'll use the existing search module from ../retrieval/
import sys
sys.path.insert(0, str(Path(__file__).parent / "retrieval"))

HOST = "127.0.0.1"
PORT = 8888
SAVES_DIR = Path(__file__).parent / "saves"
STATIC_DIR = Path(__file__).parent


async def index(request):
    return web.FileResponse(STATIC_DIR / "adventure_index.html")


async def handle_start(request):
    return web.json_response({"error": "not implemented"}, status=501)


async def handle_action(request):
    return web.json_response({"error": "not implemented"}, status=501)


async def handle_saves(request):
    return web.json_response({"error": "not implemented"}, status=501)


async def handle_save(request):
    return web.json_response({"error": "not implemented"}, status=501)


async def handle_load(request):
    return web.json_response({"error": "not implemented"}, status=501)


async def handle_delete(request):
    return web.json_response({"error": "not implemented"}, status=501)


def create_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/api/adventure/start", handle_start)
    app.router.add_post("/api/adventure/action", handle_action)
    app.router.add_get("/api/adventure/saves", handle_saves)
    app.router.add_post("/api/adventure/save", handle_save)
    app.router.add_post("/api/adventure/load", handle_load)
    app.router.add_post("/api/adventure/delete", handle_delete)
    return app


def main():
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    app = create_app()
    print(f"Server starting at http://{HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, handle_signals=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify server starts**

Run: `python adventure_server.py`

Expected: prints `Server starting at http://127.0.0.1:8888`. Press Ctrl+C to stop.

- [ ] **Step 3: Create the placeholder frontend file so the index route doesn't 404**

Create `adventure_index.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>文字冒险</title>
</head>
<body>
<h1>Hello Adventure</h1>
</body>
</html>
```

- [ ] **Step 4: Verify the HTML is served**

Run: `python adventure_server.py`, then in another terminal run:

```
curl http://127.0.0.1:8888/
```

Expected: 200 with HTML content. Press Ctrl+C to stop the server.

- [ ] **Step 5: Commit**

```bash
git add adventure_server.py adventure_index.html
git commit -m "feat: add adventure server skeleton with route stubs"
```

---

### Task 2: FTS5 RAG Pipeline

**Files:**
- Modify: `adventure_server.py` (add search function)

- [ ] **Step 1: Add the FTS5 search function to adventure_server.py**

Insert after the imports, before `async def index`:

```python
import re
import sqlite3

DB_PATH = str(Path(__file__).parent / "retrieval" / "data" / "zlb.db")


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
```

- [ ] **Step 2: Quick smoke test — start Python and test the function**

Run:

```
python -c "from adventure_server import search_game_text; r = search_game_text('钟离', 'gi', 3); print(len(r), r[0]['name'] if r else 'no results')"
```

Expected: prints `3` and a document name containing relevant GI lore. (If the DB is missing, it prints `no results` without crashing.)

- [ ] **Step 3: Commit**

```bash
git add adventure_server.py
git commit -m "feat: add FTS5 RAG search pipeline to adventure server"
```

---

### Task 3: GameState and Save/Load System

**Files:**
- Modify: `adventure_server.py` (add GameState class, save/load helpers)

- [ ] **Step 1: Add GameState dataclass and save/load functions**

Insert after the FTS5 search functions, before `async def index`:

```python
from datetime import datetime

GAME_NAMES = {"gi": "原神", "hsr": "崩坏：星穹铁道"}

class GameState:
    def __init__(self, world="gi", character="旅行者"):
        self.world = world
        self.character = character
        self.history: list[dict] = []
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


# In-memory game (single session)
current_game: GameState | None = None


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
```

- [ ] **Step 2: Smoke test save/load**

Run:

```
python -c "from adventure_server import GameState, save_game, load_game, list_saves; g = GameState('gi', 'test'); g.add_turn('system', 'hello'); save_game('test_save'); g2 = load_game('test_save'); print(g2.character, len(g2.history))"
```

Expected: `test 1`

- [ ] **Step 3: Commit**

```bash
git add adventure_server.py
git commit -m "feat: add GameState and save/load system"
```

---

### Task 4: DeepSeek API Caller

**Files:**
- Modify: `adventure_server.py` (add DeepSeek call function)

- [ ] **Step 1: Add DeepSeek API integration**

Insert after the save/load functions:

```python
import httpx

DEEPSEEK_CONFIG = {
    "api_key": "sk-your-key-here",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "temperature": 0.8,
    "max_tokens": 800,
}


def load_user_config():
    cfg_path = Path(__file__).parent / "qq_bot" / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            ds = cfg.get("deepseek", {})
            if ds.get("api_key"):
                DEEPSEEK_CONFIG["api_key"] = ds["api_key"]
                DEEPSEEK_CONFIG["model"] = ds.get("model", DEEPSEEK_CONFIG["model"])
                DEEPSEEK_CONFIG["base_url"] = ds.get("base_url", DEEPSEEK_CONFIG["base_url"]).rstrip("/")
        except Exception:
            pass


async def call_deepseek(messages):
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
```

- [ ] **Step 2: In the `main()` function, add config loading before `create_app()`**

Find the `def main():` function and modify it:

```python
def main():
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    load_user_config()
    app = create_app()
    print(f"Server starting at http://{HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, handle_signals=True)
```

- [ ] **Step 3: Commit**

```bash
git add adventure_server.py
git commit -m "feat: add DeepSeek API caller with config loading"
```

---

### Task 5: Start and Action Endpoints

**Files:**
- Modify: `adventure_server.py` (implement handle_start, handle_action, prompt builder)

- [ ] **Step 1: Add the prompt-building and narration-parsing functions**

Insert before `async def handle_start`:

```python
PROMPT_SYSTEM_TEMPLATE = """你是《{game_name}》世界的AI叙事者（DM）。你的任务是与玩家合作，创作一个设定在{game_name}世界观中的互动文字冒险故事。

## 规则
1. 严格基于游戏世界观，不要编造与原作矛盾的内容（可参考参考资料）
2. 每轮输出：2-4段生动的叙事描写 + 恰好4个行动选项
3. 选项之间要有有意义的差异——不同方向、策略或态度
4. 叙事要有沉浸感：描写场景细节、角色神态、氛围
5. 用小说式的文字，不要用口语化/论坛/攻略风格
6. 选项用"【你的选择】"作为分隔符开始，每个选项独立一行，格式"数字. 内容"

{rag_section}

现在开始游戏。"""


def build_rag_context(query, domain):
    if not os.path.exists(DB_PATH):
        return ""
    docs = search_game_text(query, domain, limit=5)
    if not docs:
        return ""
    lines = ["## 世界观参考资料（来自游戏文本，请严格遵循）"]
    for i, d in enumerate(docs, 1):
        name = d["name"]
        snip = d["snippet"]
        lines.append(f"{i}. [{name}] {snip}")
    return "\n".join(lines)


def build_messages(game, rag_context, extra_user="开始冒险。"):
    world = game.world
    game_name = GAME_NAMES.get(world, "米哈游")
    system_text = PROMPT_SYSTEM_TEMPLATE.format(
        game_name=game_name,
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
        import re as _re
        for line in option_text.strip().split("\n"):
            match = _re.match(r"^(\d+)[.、]?\s*(.*)", line.strip())
            if match:
                options.append(match.group(0).strip())
    if len(options) < 2:
        return narrative, ["1. 继续前进", "2. 仔细观察周围", "3. 转身离开", "4. 试着呼喊"]
    return narrative, options
```

- [ ] **Step 2: Implement handle_start**

Replace the `handle_start` stub:

```python
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
    rag = build_rag_context(character, world)
    messages = build_messages(current_game, rag)

    try:
        raw = await call_deepseek(messages)
    except Exception as e:
        return web.json_response({"error": f"DeepSeek call failed: {e}"}, status=500)

    narrative, options = parse_narrative(raw)
    current_game.add_turn("system", raw)
    current_game.add_turn("user", character)

    return web.json_response({"narrative": narrative, "options": options})
```

- [ ] **Step 3: Implement handle_action**

Replace the `handle_action` stub:

```python
async def handle_action(request):
    global current_game
    if current_game is None:
        return web.json_response({"error": "no active game, start first"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    choice = body.get("choice", "").strip()
    if not choice:
        return web.json_response({"error": "choice is required"}, status=400)

    current_game.add_turn("user", choice)

    keyword = choice
    if current_game.history:
        recent_user = [m["content"] for m in current_game.history if m["role"] == "user"]
        keyword = " ".join(recent_user[-3:])

    rag = build_rag_context(keyword, current_game.world)

    prev_narration = ""
    if current_game.history:
        for m in reversed(current_game.history):
            if m["role"] == "system":
                prev_narration = m["content"]
                break

    extra = f"我的选择：{choice}"
    if prev_narration:
        prev_summary = prev_narration[:300]
        extra = f"上一轮故事概要：{prev_summary}\n\n我的选择：{choice}"

    messages = build_messages(current_game, rag, extra)

    try:
        raw = await call_deepseek(messages)
    except Exception as e:
        return web.json_response({"error": f"DeepSeek call failed: {e}"}, status=500)

    narrative, options = parse_narrative(raw)
    current_game.add_turn("system", raw)

    return web.json_response({"narrative": narrative, "options": options})
```

- [ ] **Step 4: Manual smoke test — start a game**

Start server: `python adventure_server.py`

In another terminal:

```
curl -X POST http://127.0.0.1:8888/api/adventure/start -H "Content-Type: application/json" -d "{\"world\":\"gi\",\"character\":\"旅行者\"}"
```

Expected: JSON response with `narrative` (a Chinese text paragraph) and `options` (an array of 4 option strings). If DeepSeek API key is not configured, it will return an error — that's OK.

- [ ] **Step 5: Manual smoke test — send an action**

```
curl -X POST http://127.0.0.1:8888/api/adventure/action -H "Content-Type: application/json" -d "{\"choice\":\"1. 走进璃月港\"}"
```

Expected: Another JSON with `narrative` and `options`.

- [ ] **Step 6: Commit**

```bash
git add adventure_server.py
git commit -m "feat: implement start and action adventure endpoints"
```

---

### Task 6: Save/Load/Saves/Delete Endpoints

**Files:**
- Modify: `adventure_server.py` (implement the 4 stub handlers)

- [ ] **Step 1: Implement handle_saves**

Replace the stub:

```python
async def handle_saves(request):
    saves = list_saves()
    return web.json_response({"saves": saves})
```

- [ ] **Step 2: Implement handle_save**

Replace the stub:

```python
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
```

- [ ] **Step 3: Implement handle_load**

Replace the stub:

```python
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
```

- [ ] **Step 4: Implement handle_delete**

Replace the stub:

```python
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
```

- [ ] **Step 5: Smoke test save/load cycle**

Start server, then:

```
curl -X POST http://127.0.0.1:8888/api/adventure/start -H "Content-Type: application/json" -d "{\"world\":\"gi\"}"

curl -X POST http://127.0.0.1:8888/api/adventure/save -H "Content-Type: application/json" -d "{\"name\":\"mygame\"}"

curl http://127.0.0.1:8888/api/adventure/saves

curl -X POST http://127.0.0.1:8888/api/adventure/load -H "Content-Type: application/json" -d "{\"name\":\"mygame\"}"
```

Expected: save returns `{"ok": true}`, saves returns `{"saves": ["mygame"]}`, load returns full game state JSON.

- [ ] **Step 6: Commit**

```bash
git add adventure_server.py
git commit -m "feat: implement save/load/list/delete adventure endpoints"
```

---

### Task 7: Frontend — HTML Structure and CSS

**Files:**
- Modify: `adventure_index.html` (full rewrite)

- [ ] **Step 1: Write the complete frontend HTML/CSS/JS**

Replace `adventure_index.html` entirely:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>提瓦特/银河文字冒险</title>
<style>
:root {
  --bg-deep: #06080f;
  --bg-base: #0b0f1a;
  --glass-bg: rgba(255,255,255,0.025);
  --glass-bg-hover: rgba(255,255,255,0.05);
  --border-default: rgba(255,255,255,0.09);
  --border-accent: rgba(139,92,246,0.45);
  --text-primary: #e8ecf4;
  --text-secondary: #8b95a8;
  --text-muted: #4a5568;
  --accent: #8b5cf6;
  --accent-light: #c4b5fd;
  --accent-glow: rgba(139,92,246,0.18);
  --cyan: #22d3ee;
  --green: #34d399;
  --radius-md: 10px;
  --radius-lg: 14px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font: 15px/1.8 "Microsoft YaHei","PingFang SC","Segoe UI",sans-serif;
  background: var(--bg-deep);
  color: var(--text-primary);
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
body::before {
  content: '';
  position: fixed; inset: 0;
  background: radial-gradient(ellipse 80% 60% at 15% 10%, rgba(139,92,246,0.07) 0%, transparent 60%),
              radial-gradient(ellipse 70% 50% at 85% 85%, rgba(34,211,238,0.04) 0%, transparent 55%);
  pointer-events: none; z-index: 0;
}
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(139,92,246,0.2); border-radius: 10px; }
.header {
  background: rgba(11,15,26,0.75);
  backdrop-filter: blur(24px);
  border-bottom: 1px solid var(--border-default);
  padding: 14px 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
  position: relative; z-index: 10;
}
.header h1 {
  font-size: 18px; font-weight: 700;
  background: linear-gradient(135deg, var(--accent), var(--accent-light), var(--cyan));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.header-controls { display: flex; gap: 8px; align-items: center; }
.header-controls select, .header-controls button {
  padding: 7px 14px;
  background: var(--glass-bg);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: 13px; font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
}
.header-controls button:hover, .header-controls select:hover {
  border-color: var(--border-accent);
  background: var(--glass-bg-hover);
}
.header-controls button:disabled {
  opacity: 0.4; cursor: not-allowed;
}
.main {
  flex: 1; overflow-y: auto; padding: 24px 28px;
  position: relative; z-index: 1;
}
.narrative-block {
  margin-bottom: 20px;
  animation: fadeIn 0.5s ease-out;
}
.narrative-text {
  color: var(--text-primary);
  line-height: 2;
  letter-spacing: 0.03em;
}
.options-bar {
  position: sticky; bottom: 0;
  background: rgba(11,15,26,0.92);
  backdrop-filter: blur(20px);
  border-top: 1px solid var(--border-default);
  padding: 16px 28px;
  display: flex; gap: 10px;
  flex-wrap: wrap;
  z-index: 10;
}
.option-btn {
  flex: 1; min-width: 140px;
  padding: 12px 18px;
  background: var(--glass-bg);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: 14px; font-family: inherit;
  cursor: pointer;
  transition: all 0.25s;
  text-align: left;
  line-height: 1.5;
}
.option-btn:hover:not(:disabled) {
  border-color: var(--accent);
  background: var(--glass-bg-hover);
  box-shadow: 0 0 20px var(--accent-glow);
  transform: translateY(-1px);
}
.option-btn:disabled {
  opacity: 0.35; cursor: not-allowed;
}
.option-btn .num {
  color: var(--accent-light); font-weight: 600; margin-right: 6px;
}
@keyframes fadeIn {
  from { opacity:0; transform:translateY(8px); }
  to { opacity:1; transform:translateY(0); }
}
.loading-spinner {
  display: inline-block; width: 16px; height: 16px;
  border: 2px solid var(--border-default);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  margin-right: 6px; vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }
.toast {
  position: fixed; bottom: 24px; left: 50%;
  transform: translateX(-50%);
  background: var(--accent); color: #fff;
  padding: 10px 24px; border-radius: var(--radius-md);
  font-size: 13px; z-index: 9999;
  box-shadow: 0 4px 24px rgba(139,92,246,0.35);
  animation: fadeIn 0.3s;
}
.save-dialog {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.6);
  display: flex; align-items: center; justify-content: center;
  z-index: 100;
}
.save-dialog-content {
  background: var(--bg-base);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg); padding: 24px;
  min-width: 300px;
}
.save-dialog h3 { margin-bottom: 16px; }
.save-dialog input {
  width: 100%; padding: 10px;
  background: var(--glass-bg); color: var(--text-primary);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  font-size: 14px; font-family: inherit;
  margin-bottom: 12px;
}
.save-dialog button {
  padding: 8px 20px;
  background: var(--glass-bg);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  cursor: pointer; font-family: inherit;
  margin-right: 8px;
}
.save-dialog button:hover { border-color: var(--accent); }
.empty-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  height: 60vh; color: var(--text-muted);
}
.empty-state p { margin-top: 12px; font-size: 14px; }
.save-list-item {
  padding: 10px 14px; margin: 6px 0;
  background: var(--glass-bg);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  cursor: pointer;
  display: flex; justify-content: space-between; align-items: center;
}
.save-list-item:hover { border-color: var(--accent); }
.save-list-item .name { font-weight: 600; }
.save-list-item .actions { display: flex; gap: 6px; }
.save-list-item .actions button {
  padding: 4px 10px; font-size: 12px;
  background: transparent; border: 1px solid var(--border-default);
  border-radius: 4px; color: var(--text-secondary); cursor: pointer;
}
.save-list-item .actions button:hover { border-color: #ef4444; color: #ef4444; }
.hidden { display: none !important; }
</style>
</head>
<body>

<div class="header">
  <h1>提瓦特/银河文字冒险</h1>
  <div class="header-controls">
    <select id="world-select">
      <option value="gi">原神 (GI)</option>
      <option value="hsr">崩坏：星穹铁道 (HSR)</option>
    </select>
    <button id="btn-new" onclick="startGame()">新游戏</button>
    <button id="btn-save" onclick="showSaveDialog()" disabled>保存</button>
    <button id="btn-load" onclick="showLoadDialog()">加载</button>
  </div>
</div>

<div class="main" id="story-area">
  <div class="empty-state" id="empty-state">
    <p>选择一个世界，开始你的冒险</p>
  </div>
</div>

<div class="options-bar hidden" id="options-bar">
</div>

<div class="save-dialog hidden" id="save-dialog" onclick="if(event.target===this)hideSaveDialog()">
  <div class="save-dialog-content">
    <h3>保存进度</h3>
    <input type="text" id="save-name" placeholder="存档名称...">
    <button onclick="doSave()">保存</button>
    <button onclick="hideSaveDialog()">取消</button>
  </div>
</div>

<div class="save-dialog hidden" id="load-dialog" onclick="if(event.target===this)hideLoadDialog()">
  <div class="save-dialog-content" style="min-width:400px;">
    <h3>加载存档</h3>
    <div id="save-list"></div>
    <button onclick="hideLoadDialog()" style="margin-top:12px;">取消</button>
  </div>
</div>

<script>
const API = '';
let gameState = { world: null, character: null, saveName: null, turnCount: 0, isLoading: false };

function showToast(msg) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2500);
}

async function api(path, body) {
  const opts = { headers: { 'Content-Type': 'application/json' } };
  if (body) { opts.method = 'POST'; opts.body = JSON.stringify(body); }
  const resp = await fetch(API + path, opts);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

function renderNarrative(text) {
  const div = document.createElement('div');
  div.className = 'narrative-block';
  const p = document.createElement('div');
  p.className = 'narrative-text';
  // Split paragraphs on double newlines
  const parts = text.split('\n\n').filter(s => s.trim());
  p.innerHTML = parts.map(s => `<p style="margin-bottom:12px;">${s.replace(/\n/g, '<br>')}</p>`).join('');
  div.appendChild(p);
  return div;
}

function renderOptions(options) {
  const bar = document.getElementById('options-bar');
  bar.innerHTML = '';
  if (gameState.isLoading) {
    bar.innerHTML = '<span style="color:var(--text-secondary)"><span class="loading-spinner"></span> AI 正在书写故事...</span>';
    bar.classList.remove('hidden');
    return;
  }
  options.forEach((opt, i) => {
    const btn = document.createElement('button');
    btn.className = 'option-btn';
    btn.disabled = gameState.isLoading;
    btn.innerHTML = `<span class="num">${i+1}.</span>${opt.replace(/^\d+[.、]?\s*/, '')}`;
    btn.onclick = () => sendAction(opt);
    bar.appendChild(btn);
  });
  bar.classList.remove('hidden');
}

async function startGame() {
  if (gameState.isLoading) return;
  gameState.isLoading = true;
  document.getElementById('btn-new').disabled = true;
  document.getElementById('btn-save').disabled = true;
  const world = document.getElementById('world-select').value;
  const storyArea = document.getElementById('story-area');
  const empty = document.getElementById('empty-state');
  storyArea.innerHTML = '';
  storyArea.appendChild(empty);
  renderOptions([]);

  try {
    const data = await api('/api/adventure/start', { world, character: world === 'gi' ? '旅行者' : '开拓者' });
    gameState.world = world;
    gameState.turnCount = 1;
    empty.classList.add('hidden');
    storyArea.appendChild(renderNarrative(data.narrative));
    storyArea.scrollTop = storyArea.scrollHeight;
    renderOptions(data.options);
    document.getElementById('btn-save').disabled = false;
  } catch (e) {
    showToast('启动失败: ' + e.message);
  }
  gameState.isLoading = false;
  document.getElementById('btn-new').disabled = false;
}

async function sendAction(choice) {
  if (gameState.isLoading) return;
  gameState.isLoading = true;
  document.getElementById('btn-save').disabled = true;
  renderOptions([]);

  try {
    const data = await api('/api/adventure/action', { choice });
    const storyArea = document.getElementById('story-area');
    storyArea.appendChild(renderNarrative(data.narrative));
    storyArea.scrollTop = storyArea.scrollHeight;
    gameState.turnCount++;
    renderOptions(data.options);
    document.getElementById('btn-save').disabled = false;
  } catch (e) {
    showToast('请求失败: ' + e.message);
    renderOptions(['1. 重试', '2. 回到开头']);
  }
  gameState.isLoading = false;
}

function showSaveDialog() {
  document.getElementById('save-dialog').classList.remove('hidden');
  document.getElementById('save-name').value = gameState.saveName || '';
  document.getElementById('save-name').focus();
}

function hideSaveDialog() {
  document.getElementById('save-dialog').classList.add('hidden');
}

async function doSave() {
  const name = document.getElementById('save-name').value.trim();
  if (!name) { showToast('请输入存档名称'); return; }
  hideSaveDialog();
  try {
    await api('/api/adventure/save', { name });
    gameState.saveName = name;
    showToast('已保存: ' + name);
  } catch (e) {
    showToast('保存失败: ' + e.message);
  }
}

async function showLoadDialog() {
  const dialog = document.getElementById('load-dialog');
  const list = document.getElementById('save-list');
  dialog.classList.remove('hidden');
  list.innerHTML = '<span style="color:var(--text-secondary)">正在加载...</span>';
  try {
    const data = await api('/api/adventure/saves');
    if (data.saves.length === 0) {
      list.innerHTML = '<p style="color:var(--text-muted)">暂无存档</p>';
      return;
    }
    list.innerHTML = data.saves.map(name =>
      `<div class="save-list-item">
        <span class="name">${name}</span>
        <span class="actions">
          <button onclick="loadGame('${name}')">加载</button>
          <button onclick="deleteSave('${name}')">删除</button>
        </span>
      </div>`
    ).join('');
  } catch (e) {
    list.innerHTML = '<p style="color:#ef4444">加载失败: ' + e.message + '</p>';
  }
}

function hideLoadDialog() {
  document.getElementById('load-dialog').classList.add('hidden');
}

async function loadGame(name) {
  hideLoadDialog();
  if (gameState.isLoading) return;
  gameState.isLoading = true;
  document.getElementById('btn-save').disabled = true;
  try {
    const data = await api('/api/adventure/load', { name });
    gameState.world = data.world;
    gameState.character = data.character;
    gameState.saveName = name;
    gameState.turnCount = 0;
    const storyArea = document.getElementById('story-area');
    storyArea.innerHTML = '';
    document.getElementById('empty-state').classList.add('hidden');
    document.getElementById('btn-save').disabled = false;
    const history = data.history || [];
    for (const msg of history) {
      if (msg.role === 'system') {
        const res = parseDisplay(msg.content);
        storyArea.appendChild(renderNarrative(res.narrative));
        gameState.turnCount++;
      }
    }
    storyArea.scrollTop = storyArea.scrollHeight;
    if (history.length > 0) {
      const last = history[history.length - 1];
      if (last.role === 'system') {
        const res = parseDisplay(last.content);
        renderOptions(res.options);
      }
    }
    showToast('已加载: ' + name);
  } catch (e) {
    showToast('加载失败: ' + e.message);
  }
  gameState.isLoading = false;
}

async function deleteSave(name) {
  if (!confirm('确定删除存档 "' + name + '" 吗？')) return;
  try {
    await api('/api/adventure/delete', { name });
    showToast('已删除: ' + name);
    showLoadDialog();
  } catch (e) {
    showToast('删除失败: ' + e.message);
  }
}

function parseDisplay(text) {
  const parts = text.split('【你的选择】');
  const narrative = parts[0] ? parts[0].trim() : text.trim();
  let options = [];
  if (parts.length > 1) {
    const lines = parts[1].trim().split('\n');
    for (const line of lines) {
      const m = line.trim().match(/^(\d+)[.、]?\s*(.*)/);
      if (m) options.push(m[0].trim());
    }
  }
  if (options.length === 0) options = ['1. 继续前进', '2. 观察四周', '3. 大声呼喊', '4. 等待时机'];
  return { narrative, options };
}
</script>
</body>
</html>
```

- [ ] **Step 2: Verify the HTML is served**

Run: `python adventure_server.py`

Open browser to `http://127.0.0.1:8888/`

Expected: dark page with title "提瓦特/银河文字冒险", world dropdown, and three buttons (新游戏 / 保存 / 加载).

- [ ] **Step 3: Commit**

```bash
git add adventure_index.html
git commit -m "feat: build adventure frontend with full game flow UI"
```

---

### Task 8: End-to-End Verification

**Files:** None new

- [ ] **Step 1: Full flow test**

1. Start server: `python adventure_server.py`
2. Open `http://127.0.0.1:8888/` in browser
3. Click "新游戏" — verify first narrative appears with 4 options
4. Click an option — verify a new narrative block appears above, 4 new options below
5. Click "保存" — enter a name, verify toast "已保存"
6. Refresh the page, click "加载" — verify save appears in list
7. Click "加载" on the save — verify full history restores
8. Click "新游戏" again — verify new game starts fresh

- [ ] **Step 2: Error case — DB missing**

Rename the DB temporarily and start a game — verify the game still works (AI generates without RAG, no crash).

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: final verification, everything wired up"
```
