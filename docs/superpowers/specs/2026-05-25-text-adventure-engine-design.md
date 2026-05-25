# Text Adventure Engine — Design Spec

**Date:** 2026-05-25
**Status:** Draft

---

## 1. Overview

A single-page web app that turns scraped Genshin Impact / Honkai: Star Rail lore text into an AI-driven interactive text adventure. The user picks a world, the AI (DeepSeek) acts as Dungeon Master, and every narrative turn is grounded in real game text via RAG (FTS5).

---

## 2. Architecture

```
Browser (HTML/CSS/JS)
    │
    │  HTTP (fetch)
    ▼
Python aiohttp Server (port 8888)
    │
    ├── /api/adventure/start    POST → start new game
    ├── /api/adventure/action   POST → submit choice, get next turn
    ├── /api/adventure/saves    GET  → list saved games
    ├── /api/adventure/save     POST → save current game
    ├── /api/adventure/load     POST → load a saved game
    └── /api/adventure/delete   POST → delete a save
    │
    ├── FTS5 SQLite DB (retrieval/data/zlb.db)
    └── DeepSeek API
```

All state lives on the server in memory (current session) or on disk (JSON saves). No database for game state — JSON files in `saves/` are sufficient.

---

## 3. Backend

### 3.1 Server

Single Python file `adventure_server.py`, placed at project root. Uses `aiohttp` (already in use by the QQ bot). Serves the frontend HTML/CSS/JS as static files and exposes the adventure API.

### 3.2 Game State (in-memory)

```python
@dataclass
class GameState:
    world: str           # "gi" or "hsr"
    character: str       # player character name
    history: list[dict]  # [{"role": "system"|"user"|"assistant", "content": "..."}]
    created_at: str      # ISO timestamp
```

A single `GameState` instance per server process. Only one game at a time (single-player).

### 3.3 RAG Pipeline

On each player action:

1. Extract keywords from the last 2-3 user choices (concatenate, strip common words)
2. Build FTS5 query using existing `build_fts5_query()` function (reuse from `retrieval/mhy_search.py`)
3. Search FTS5 for top 5 matching documents, filtered by `domain = world`
4. Format results as world-reference snippets, inject into system prompt

### 3.4 Prompt Engineering

**System prompt:**
```
你是《{game_name}》世界的AI叙事者。你的任务是与玩家合作，创作一个
设定在{world_name}世界观中的互动文字冒险故事。

规则：
1. 严格基于游戏世界观，不要编造与原作矛盾的内容
2. 每次输出：2-4段叙事 + 4个行动选项
3. 选项之间要有有意义的差异（不同方向/策略/态度）
4. 叙事要有沉浸感——描写场景、角色、氛围
5. 选项格式：严格用"【你的选择】"作为分隔符，每个选项"数字. 内容"

【世界观参考资料】
{rag_context}
```

**User message format:**
```
上一轮你写道：
{last_narrative_summary}

我的选择：{player_choice}

请继续推进故事。
```

The user message includes the last AI narrative (truncated to ~300 chars) for continuity, since DeepSeek is stateless.

### 3.5 History Management

- Full conversation history kept in `GameState.history`
- When calling DeepSeek, only send: system message + last 6 messages (3 rounds)
- This keeps context window small and reduces API cost
- The full history is only used for saving/loading

### 3.6 Save System

Files in `saves/` directory:

```
saves/
  some-name.json
  another-name.json
```

Each file contains the full `GameState` serialized. Save name derived from filename.

---

## 4. Frontend

### 4.1 Layout

```
┌──────────────────────────────────────────┐
│  🌌 提瓦特/银河 文字冒险                   │
│  [新游戏 ▼] [存档: 旅人日志] [保存] [加载] │
├──────────────────────────────────────────┤
│                                          │
│  你推开璃月港客栈的木门，潮湿的海风夹着    │
│  咸腥味扑面而来。掌柜的是个花白胡子的老头  │
│  ，正在柜台后面打盹。                      │
│                                          │
│  "客官打尖还是住店？" 他眼皮都没抬。       │
│                                          │
│  ───────────────────────────────────────  │
│                                          │
│  [1. 要一间上房，顺带打听消息]             │
│  [2. 点一壶桂花酒，坐下来观察周围]          │
│  [3. 说自己只是路过，马上就走]              │
│  [4. 直接问老头最近有没有可疑的人来过]      │
│                                          │
└──────────────────────────────────────────┘
```

### 4.2 Style

Reuse design tokens from existing `mhy_search.py` (dark theme, purple accent, glass blur). Single CSS file, no framework.

### 4.3 State

Vanilla JS, no framework. State held in a global object:
```javascript
let state = {
  world: null,
  character: null,
  saveName: null,
  turnCount: 0,
  isLoading: false,
}
```

### 4.4 Flow

1. **Start:** User selects world from dropdown → POST `/api/adventure/start` → receive first narrative + options → render
2. **Action:** User clicks an option → disable buttons (loading) → POST `/api/adventure/action` with choice text → receive new narrative + options → render
3. **Save:** User clicks Save → if new game, prompt for name → POST `/api/adventure/save` → confirm
4. **Load:** User clicks Load → GET `/api/adventure/saves` → show list → click one → POST `/api/adventure/load` → restore game

### 4.5 Rendering

- Narrative text: typed into `.narrative` div as-is (plain text or basic markdown)
- Options: rendered as buttons below separator
- Loading: options greyed out, spinner shown
- History: scrollable, auto-scroll to bottom on new turn
- On load: full history rendered as conversation

---

## 5. API Contract

### POST /api/adventure/start

```json
// Request
{"world": "gi", "character": "旅行者"}

// Response
{
  "narrative": "...",
  "options": ["1. ...", "2. ...", "3. ...", "4. ..."]
}
```

### POST /api/adventure/action

```json
// Request
{"choice": "1. 要一间上房，顺带打听消息"}

// Response
{
  "narrative": "...",
  "options": ["1. ...", "2. ...", "3. ...", "4. ..."]
}
```

### GET /api/adventure/saves

```json
// Response
{"saves": ["旅人日志", "test"]}
```

### POST /api/adventure/save

```json
// Request
{"name": "旅人日志"}

// Response
{"ok": true}
```

### POST /api/adventure/load

```json
// Request
{"name": "旅人日志"}

// Response
{
  "world": "gi",
  "character": "旅行者",
  "history": [...]  // full narrative history for rendering
}
```

### POST /api/adventure/delete

```json
// Request
{"name": "旅人日志"}

// Response
{"ok": true}
```

---

## 6. Error Handling

- DeepSeek API failure: return error to frontend, show toast, keep game state intact for retry
- FTS5 DB missing: warn in UI, run without RAG (AI still works, just less grounded)
- Save failure: toast error, don't lose in-memory state
- Timeout: 60s timeout on DeepSeek calls

---

## 7. Files

```
zlb-scraper/
  adventure_server.py     # NEW - main server
  adventure_index.html    # NEW - frontend
  saves/                  # NEW - save files directory
  retrieval/data/zlb.db   # EXISTING - reused
```

All-new code. No existing files modified. ~400 lines total (server 200 + HTML/CSS/JS 200).

---

## 8. Dependencies

No new dependencies. `aiohttp` and `httpx` already in the project. Reuse `build_fts5_query` from `retrieval/mhy_search.py`.

---

## 9. Future (explicitly out of scope)

- Multi-character or party system
- Combat/stat mechanics
- Inventory/items
- Multiplayer
- Image generation
- Deployment/hosting
- User accounts
