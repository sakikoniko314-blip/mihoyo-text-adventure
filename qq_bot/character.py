"""
Character persona loader with DS-analyzed prompt.
"""
import json, logging, os
from pathlib import Path

logger = logging.getLogger("qq_bot.character")
GAME_NAMES = {"gi": "原神", "hsr": "崩坏：星穹铁道"}

DS_PROMPT = Path(__file__).parent / "ds_prompt.txt"


class CharacterPersona:
    def __init__(self, name: str):
        self.name = name
        self._data: dict = {}
        self._path = Path(__file__).parent / "characters" / f"{name}.json"
        self._mtime: float = 0.0
        self._load()

    @property
    def data(self) -> dict:
        self.reload_if_changed()
        return self._data

    def _load(self):
        if not self._path.exists():
            self._data = self._default()
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._mtime = os.path.getmtime(self._path)
        except (json.JSONDecodeError, OSError):
            self._data = self._default()

    def reload_if_changed(self):
        try:
            mtime = os.path.getmtime(self._path)
        except OSError:
            return
        if mtime > self._mtime:
            self._load()

    @staticmethod
    def _to_str(val):
        if isinstance(val, list):
            return "\n".join(val)
        return str(val) if val else ""

    def _default(self):
        return {"name": self.name, "game": "gi", "greeting": "你好呀！"}

    def get_persona(self) -> str:
        d = self.data
        game = GAME_NAMES.get(d.get("game", ""), "米哈游游戏")
        extras = []
        if d.get("path"):
            extras.append(f"命途：{d['path']}")
        if d.get("faction"):
            extras.append(f"阵营：{d['faction']}")
        tag = f"（{' / '.join(extras)}）" if extras else ""

        base = f"你是《{game}》的角色「{d['name']}」{tag}。\n\n"

        if DS_PROMPT.exists():
            return base + DS_PROMPT.read_text(encoding="utf-8")

        pers = self._to_str(d.get("personality", ""))
        return base + pers + "\n\n像真实的人一样聊天。不多想，不表演。"

    def get_greeting(self) -> str:
        return self.data.get("greeting", "你好呀！")

    def get_farewell(self) -> str:
        return self.data.get("farewell", "再见啦～")
