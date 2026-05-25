#!/usr/bin/env python3
"""
米哈游游戏文本检索 — 本地桌面全文搜索工具
原神 (gi) + 崩坏星穹铁道 (hsr) 全部游戏内文本，共 25,935 篇文档。
基于 SQLite FTS5 全文索引。

Usage:  python3 gui.py
"""

import json
import os
import re
import sqlite3
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

# ── Paths ──────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "zlb.db")

# ── Color palette (Catppuccin Mocha tweaked) ───────────────

C = {
    "bg":             "#1e1e2e",
    "fg":             "#cdd6f4",
    "input_bg":       "#313244",
    "input_fg":       "#cdd6f4",
    "button_bg":      "#45475a",
    "button_fg":      "#cdd6f4",
    "button_active":  "#585b70",
    "list_bg":        "#181825",
    "list_fg":        "#cdd6f4",
    "accent":         "#cba6f7",
    "highlight":      "#f38ba8",
    "card_bg":        "#313244",
    "badge_gi":       "#a6e3a1",   # 原神 green
    "badge_hsr":      "#89b4fa",   # 星穹铁道 blue
    "mark_bg":        "#f9e2af",
    "mark_fg":        "#1e1e2e",
    "separator":      "#585b70",
    "tag_bg":         "#45475a",
    "tag_fg":         "#cdd6f4",
    "scrollbar_bg":   "#313244",
    "scrollbar_trough": "#1e1e2e",
}

PAGE_SIZE = 50

DOMAIN_LABELS = {"gi": "原神", "hsr": "星穹铁道"}

# ── Database helpers ───────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """Open a read-only connection. Kept open for the app lifetime."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=-8000")
    return conn


def build_fts5_query(user_query: str) -> str:
    """Sanitise user input and build an FTS5 AND query.

    Matches the server.py logic: strip special chars and FTS5 operators,
    wrap each term in double quotes, join with spaces (implicit AND).
    """
    clean = re.sub(r'[*"()+^-]', " ", user_query)
    clean = re.sub(r"\bAND\b",   " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bOR\b",    " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bNOT\b",   " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bNEAR\b",  " ", clean, flags=re.IGNORECASE)
    terms = clean.split()
    if not terms:
        return ""
    return " ".join(f'"{t}"' for t in terms)


def extract_search_terms(user_query: str) -> list[str]:
    """Extract individual search terms from the raw query for fallback highlighting."""
    clean = re.sub(r'[*"()+^-]', " ", user_query)
    clean = re.sub(r"\b(?:AND|OR|NOT|NEAR)\b", " ", clean, flags=re.IGNORECASE)
    return [t for t in clean.split() if t]


# ── Inline text insertion helper ───────────────────────────

def insert_formatted_line(widget: tk.Text, text: str, base_tags: tuple[str, ...] = ()) -> None:
    """Insert one line of text with **bold**, *italic*, _italic_, <mark> markup.

    Links [text](url) are stripped to just ``text``.
    Uses a stack-based tokeniser that correctly handles nested formatting.
    """
    # Strip links: [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    tokens = re.split(r"(</?mark>|\*\*|\*|_)", text)
    bold = False
    italic = False
    mark = False

    def _tags():
        t = list(base_tags)
        if bold:
            t.append("bold")
        if italic:
            t.append("italic")
        if mark:
            t.append("mark")
        return tuple(t)

    for tok in tokens:
        if not tok:
            continue
        if tok == "**":
            bold = not bold
        elif tok == "*":
            italic = not italic
        elif tok == "_":
            italic = not italic
        elif tok == "<mark>":
            mark = True
        elif tok == "</mark>":
            mark = False
        else:
            widget.insert(tk.END, tok, _tags())


# ── Markdown content renderer ──────────────────────────────

def render_markdown(widget: tk.Text, content: str) -> None:
    """Parse *content* as game-text markdown and insert into *widget* with tags.

    Supports: #/##/### headings, **bold**, *italic*, <mark>, bullet lists,
    horizontal rules, paragraph spacing.
    """
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)

    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Blank line → paragraph break
        if line.strip() == "":
            widget.insert(tk.END, "\n", "para_break")
            i += 1
            continue

        # Horizontal rule
        if line.strip() == "---":
            widget.insert(tk.END, "─" * 60 + "\n", "hr")
            i += 1
            continue

        # Headings
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            tag = f"h{level}"
            insert_formatted_line(widget, m.group(2), (tag,))
            widget.insert(tk.END, "\n")
            i += 1
            continue

        # Bullet list
        if re.match(r"^-\s+", line):
            widget.insert(tk.END, "  • ", "bullet_marker")
            insert_formatted_line(widget, re.sub(r"^-\s+", "", line), ("bullet",))
            widget.insert(tk.END, "\n")
            i += 1
            continue

        # Table row (markdown pipe table) — render as plain mono text
        if line.lstrip().startswith("|") and "|" in line.lstrip()[1:]:
            # Collect consecutive table lines
            table_lines = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            for tl in table_lines:
                insert_formatted_line(widget, tl, ("table_row",))
                widget.insert(tk.END, "\n")
            continue

        # Regular paragraph — collect consecutive non-empty, non-block lines
        para_parts = []
        while i < len(lines) and lines[i].strip() != "":
            stripped = lines[i].strip()
            # Stop when we hit a block-level element (headings, bullets, tables, hr)
            if (re.match(r"^#{1,3}\s", stripped)
                    or stripped == "---"
                    or re.match(r"^-\s+", stripped)
                    or (stripped.startswith("|") and "|" in stripped[1:])):
                break
            para_parts.append(lines[i])
            i += 1
        if para_parts:
            para = " ".join(para_parts)
            insert_formatted_line(widget, para, ("para",))
            widget.insert(tk.END, "\n\n", "para_end")

    widget.configure(state=tk.DISABLED)


# ── Main Application ───────────────────────────────────────

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("米哈游游戏文本检索")
        self.root.geometry("1100x750")
        self.root.minsize(800, 500)
        self.root.configure(bg=C["bg"])

        # State
        self._db: sqlite3.Connection | None = None
        self._all_results: list[sqlite3.Row] = []   # current search results (appended)
        self._total_count = 0
        self._current_offset = 0
        self._search_terms: list[str] = []
        self._current_doc_id: int | None = None
        self._result_lines: list[tuple[int, int]] = []  # (line_start, line_end) per card
        self._result_ids: list[int] = []                 # doc id per card
        self._search_running = False

        # DB
        self._db = get_db()

        # Fonts
        self._default_font = tkfont.nametofont("TkDefaultFont")
        self._default_size = self._default_font.cget("size")
        self._fonts: dict[str, tkfont.Font] = {}

        self._init_fonts()
        self._setup_styles()
        self._build_ui()
        self._load_facets()

        # Window close handler
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Fonts ──────────────────────────────────────────────

    def _init_fonts(self) -> None:
        family = self._default_font.cget("family")
        sz = self._default_size
        self._fonts = {
            "default":    tkfont.Font(family=family, size=sz),
            "h1":         tkfont.Font(family=family, size=sz + 6, weight="bold"),
            "h2":         tkfont.Font(family=family, size=sz + 4, weight="bold"),
            "h3":         tkfont.Font(family=family, size=sz + 2, weight="bold"),
            "bold":       tkfont.Font(family=family, size=sz, weight="bold"),
            "italic":     tkfont.Font(family=family, size=sz, slant="italic"),
            "mono":       tkfont.Font(family="TkFixedFont", size=sz - 1),
            "badge":      tkfont.Font(family=family, size=sz - 2, weight="bold"),
            "name":       tkfont.Font(family=family, size=sz, weight="bold"),
            "result_name": tkfont.Font(family=family, size=sz + 1, weight="bold"),
            "snippet":    tkfont.Font(family=family, size=sz - 1),
            "card_meta":  tkfont.Font(family=family, size=sz - 2),
        }

    # ── Styles ─────────────────────────────────────────────

    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        # Combobox styling
        style.configure(
            "Dark.TCombobox",
            fieldbackground=C["input_bg"],
            background=C["button_bg"],
            foreground=C["input_fg"],
            arrowcolor=C["fg"],
            selectbackground=C["accent"],
            selectforeground=C["bg"],
            bordercolor=C["separator"],
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", C["input_bg"])],
            foreground=[("readonly", C["input_fg"])],
        )

        # Button styling
        style.configure(
            "Dark.TButton",
            background=C["button_bg"],
            foreground=C["button_fg"],
            borderwidth=0,
            focusthickness=0,
            font=self._fonts["default"],
        )
        style.map(
            "Dark.TButton",
            background=[("active", C["button_active"]), ("pressed", C["accent"])],
            foreground=[("active", C["fg"])],
        )

        # Accent button
        style.configure(
            "Accent.TButton",
            background=C["accent"],
            foreground=C["bg"],
            borderwidth=0,
            focusthickness=0,
            font=self._fonts["default"],
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#d4b5ff"), ("pressed", "#b38ef0")],
        )

        # Frame
        style.configure("Dark.TFrame", background=C["bg"])

    # ── UI construction ────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Search bar (top) ──
        search_frame = ttk.Frame(self.root, style="Dark.TFrame", padding=(12, 10, 12, 4))
        search_frame.pack(fill=tk.X)

        search_row = ttk.Frame(search_frame, style="Dark.TFrame")
        search_row.pack(fill=tk.X)

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._on_search_input())
        self._entry = tk.Entry(
            search_row,
            textvariable=self._search_var,
            font=self._fonts["default"],
            bg=C["input_bg"],
            fg=C["input_fg"],
            insertbackground=C["fg"],
            relief=tk.FLAT,
            bd=8,
            highlightthickness=0,
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self._entry.bind("<Return>", lambda e: self._do_search())
        self._entry.focus_set()

        self._search_btn = ttk.Button(
            search_row,
            text="搜索",
            style="Accent.TButton",
            command=self._do_search,
        )
        self._search_btn.pack(side=tk.LEFT)

        # Result count label
        self._count_label = tk.Label(
            search_row,
            text="",
            font=self._fonts["card_meta"],
            bg=C["bg"],
            fg=C["separator"],
        )
        self._count_label.pack(side=tk.LEFT, padx=(12, 0))

        # ── Filter bar ──
        filter_frame = ttk.Frame(search_frame, style="Dark.TFrame")
        filter_frame.pack(fill=tk.X, pady=(8, 2))

        # Domain
        tk.Label(
            filter_frame, text="游戏", font=self._fonts["card_meta"],
            bg=C["bg"], fg=C["separator"],
        ).pack(side=tk.LEFT, padx=(0, 4))

        self._domain_var = tk.StringVar(value="")
        self._domain_combo = ttk.Combobox(
            filter_frame, textvariable=self._domain_var,
            values=["全部", "原神", "星穹铁道"],
            state="readonly", width=10, style="Dark.TCombobox",
            font=self._fonts["default"],
        )
        self._domain_combo.set("全部")
        self._domain_combo.pack(side=tk.LEFT, padx=(0, 8))
        self._domain_combo.bind("<<ComboboxSelected>>", lambda e: self._on_domain_change())

        # Type
        tk.Label(
            filter_frame, text="类型", font=self._fonts["card_meta"],
            bg=C["bg"], fg=C["separator"],
        ).pack(side=tk.LEFT, padx=(0, 4))

        self._type_var = tk.StringVar(value="")
        self._type_combo = ttk.Combobox(
            filter_frame, textvariable=self._type_var,
            values=["全部类型"], state="readonly", width=14, style="Dark.TCombobox",
            font=self._fonts["default"],
        )
        self._type_combo.set("全部类型")
        self._type_combo.pack(side=tk.LEFT, padx=(0, 8))
        self._type_combo.bind("<<ComboboxSelected>>", lambda e: self._do_search())

        # Category
        tk.Label(
            filter_frame, text="分类", font=self._fonts["card_meta"],
            bg=C["bg"], fg=C["separator"],
        ).pack(side=tk.LEFT, padx=(0, 4))

        self._cat_var = tk.StringVar(value="")
        self._cat_combo = ttk.Combobox(
            filter_frame, textvariable=self._cat_var,
            values=["全部分类"], state="readonly", width=14, style="Dark.TCombobox",
            font=self._fonts["default"],
        )
        self._cat_combo.set("全部分类")
        self._cat_combo.pack(side=tk.LEFT)
        self._cat_combo.bind("<<ComboboxSelected>>", lambda e: self._do_search())

        # ── Main content area (PanedWindow) ──
        self._paned = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL,
            bg=C["separator"], sashwidth=2,
        )
        self._paned.pack(fill=tk.BOTH, expand=True, padx=12, pady=(2, 12))

        # ── Left panel: results list ──
        left_frame = ttk.Frame(self._paned, style="Dark.TFrame")
        # 40% of 1100 ≈ 440 px
        self._paned.add(left_frame, width=440, minsize=280)

        left_inner = ttk.Frame(left_frame, style="Dark.TFrame")
        left_inner.pack(fill=tk.BOTH, expand=True)

        # Results Text widget + scrollbar
        results_container = tk.Frame(left_inner, bg=C["list_bg"])
        results_container.pack(fill=tk.BOTH, expand=True)

        self._results_text = tk.Text(
            results_container,
            font=self._fonts["default"],
            bg=C["list_bg"],
            fg=C["list_fg"],
            wrap=tk.WORD,
            cursor="hand2",
            relief=tk.FLAT,
            bd=0,
            padx=11,
            pady=6,
            state=tk.DISABLED,
            highlightthickness=0,
            selectbackground=C["accent"],
            selectforeground=C["bg"],
        )
        self._results_scroll = tk.Scrollbar(
            results_container,
            orient=tk.VERTICAL,
            command=self._results_text.yview,
            bg=C["scrollbar_bg"],
            troughcolor=C["scrollbar_trough"],
            activebackground=C["separator"],
            bd=0,
            highlightthickness=0,
        )
        self._results_text.configure(yscrollcommand=self._results_scroll.set)
        self._results_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._results_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tag configuration for results text
        self._results_text.tag_configure("card_bg", background=C["card_bg"],
                                          lmargin1=0, lmargin2=0, rmargin=0,
                                          spacing1=4, spacing3=4)
        self._results_text.tag_configure("result_name", font=self._fonts["result_name"],
                                          foreground=C["fg"], spacing1=6, spacing3=0)
        self._results_text.tag_configure("badge_gi", font=self._fonts["badge"],
                                          foreground=C["badge_gi"],
                                          background="#1a2e1a",
                                          spacing1=1, spacing3=1)
        self._results_text.tag_configure("badge_hsr", font=self._fonts["badge"],
                                          foreground=C["badge_hsr"],
                                          background="#1e2a3a",
                                          spacing1=1, spacing3=1)
        self._results_text.tag_configure("card_meta", font=self._fonts["card_meta"],
                                          foreground=C["separator"],
                                          spacing1=2, spacing3=2)
        self._results_text.tag_configure("card_snippet", font=self._fonts["snippet"],
                                          foreground=C["fg"],
                                          spacing1=2, spacing3=4)
        self._results_text.tag_configure("mark", background=C["mark_bg"],
                                          foreground=C["mark_fg"])
        self._results_text.tag_configure("card_sep", foreground=C["separator"],
                                          font=self._fonts["card_meta"],
                                          spacing1=0, spacing3=0,
                                          lmargin1=4, lmargin2=4)

        # Bindings
        self._results_text.bind("<Button-1>", self._on_result_click)
        self._results_text.bind("<Double-Button-1>", self._on_result_double_click)
        self._results_text.bind("<Return>", self._on_result_return)
        self._results_text.bind("<Up>", self._on_result_up)
        self._results_text.bind("<Down>", self._on_result_down)

        # "Load more" button (hidden initially)
        self._load_more_btn = ttk.Button(
            left_inner, text="加载更多", style="Dark.TButton",
            command=self._load_more,
        )

        # Placeholder label
        self._results_placeholder = tk.Label(
            results_container, text="输入关键词搜索米哈游游戏文本",
            font=self._fonts["snippet"], bg=C["list_bg"], fg=C["separator"],
        )
        self._results_placeholder.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # ── Right panel: document viewer ──
        right_frame = ttk.Frame(self._paned, style="Dark.TFrame")
        self._paned.add(right_frame, width=660, minsize=360)

        # Metadata header
        self._meta_frame = tk.Frame(right_frame, bg=C["card_bg"], padx=14, pady=10)
        self._meta_frame.pack(fill=tk.X)

        self._doc_title = tk.Label(
            self._meta_frame, text="", font=self._fonts["h2"],
            bg=C["card_bg"], fg=C["fg"], anchor=tk.W, justify=tk.LEFT,
        )
        self._doc_title.pack(fill=tk.X)

        self._doc_meta = tk.Label(
            self._meta_frame, text="", font=self._fonts["card_meta"],
            bg=C["card_bg"], fg=C["separator"], anchor=tk.W, justify=tk.LEFT,
        )
        self._doc_meta.pack(fill=tk.X)

        self._doc_tags_frame = tk.Frame(self._meta_frame, bg=C["card_bg"])
        self._doc_tags_frame.pack(fill=tk.X, pady=(4, 0))

        # Content viewer
        content_container = tk.Frame(right_frame, bg=C["bg"])
        content_container.pack(fill=tk.BOTH, expand=True, padx=0, pady=(2, 0))

        self._content_text = tk.Text(
            content_container,
            font=self._fonts["default"],
            bg=C["bg"],
            fg=C["fg"],
            wrap=tk.WORD,
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=10,
            state=tk.DISABLED,
            highlightthickness=0,
            cursor="arrow",
        )
        self._content_scroll = tk.Scrollbar(
            content_container,
            orient=tk.VERTICAL,
            command=self._content_text.yview,
            bg=C["scrollbar_bg"],
            troughcolor=C["scrollbar_trough"],
            activebackground=C["separator"],
            bd=0,
            highlightthickness=0,
        )
        self._content_text.configure(yscrollcommand=self._content_scroll.set)
        self._content_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._content_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Content text tags
        self._content_text.tag_configure("h1", font=self._fonts["h1"], foreground=C["accent"],
                                          spacing1=12, spacing3=4)
        self._content_text.tag_configure("h2", font=self._fonts["h2"], foreground=C["fg"],
                                          spacing1=10, spacing3=3)
        self._content_text.tag_configure("h3", font=self._fonts["h3"], foreground=C["fg"],
                                          spacing1=8, spacing3=2)
        self._content_text.tag_configure("bold", font=self._fonts["bold"])
        self._content_text.tag_configure("italic", font=self._fonts["italic"])
        self._content_text.tag_configure("mark", background=C["mark_bg"], foreground=C["mark_fg"])
        self._content_text.tag_configure("bullet", lmargin1=20, lmargin2=35)
        self._content_text.tag_configure("bullet_marker", foreground=C["accent"], font=self._fonts["default"])
        self._content_text.tag_configure("hr", foreground=C["separator"],
                                          spacing1=8, spacing3=8,
                                          font=self._fonts["card_meta"])
        self._content_text.tag_configure("para", lmargin1=0, spacing1=2, spacing3=2)
        self._content_text.tag_configure("para_end", spacing3=6)
        self._content_text.tag_configure("para_break", spacing1=4, spacing3=4)
        self._content_text.tag_configure("table_row", font=self._fonts["mono"],
                                          foreground=C["separator"],
                                          lmargin1=10)

    # ── Facets ─────────────────────────────────────────────

    def _load_facets(self) -> None:
        """Populate type/category dropdowns from the database."""
        if self._db is None:
            return
        domain_value = self._get_domain_filter()

        if domain_value:
            types = [r[0] for r in self._db.execute(
                "SELECT DISTINCT doc_type FROM documents WHERE domain=? ORDER BY doc_type",
                [domain_value],
            )]
            cats = [r[0] for r in self._db.execute(
                "SELECT DISTINCT category FROM documents WHERE domain=? ORDER BY category",
                [domain_value],
            )]
        else:
            types = [r[0] for r in self._db.execute(
                "SELECT DISTINCT doc_type FROM documents ORDER BY doc_type",
            )]
            cats = [r[0] for r in self._db.execute(
                "SELECT DISTINCT category FROM documents ORDER BY category",
            )]

        self._update_combo(self._type_combo, types, "全部类型")
        self._update_combo(self._cat_combo, cats, "全部分类")

    def _update_combo(self, combo: ttk.Combobox, values: list[str], default: str) -> None:
        """Safely update a combobox dropdown while preserving the current selection."""
        current = combo.get()
        combo["values"] = [default] + values
        if current in [default] + values:
            combo.set(current)
        else:
            combo.set(default)

    def _get_domain_filter(self) -> str:
        """Return DB domain value for the selected dropdown entry."""
        v = self._domain_var.get()
        if v == "原神":
            return "gi"
        if v == "星穹铁道":
            return "hsr"
        return ""

    def _on_domain_change(self) -> None:
        """Domain filter changed — reload facets then search."""
        self._load_facets()
        self._do_search()

    def _on_search_input(self) -> None:
        """User typed in the search box — debounce to avoid flooding."""
        # Debounce: we don't auto-search on every keystroke; only on Enter/button.
        pass

    # ── Search logic ──────────────────────────────────────

    def _do_search(self) -> None:
        """Execute search against FTS5 (with LIKE fallback)."""
        if self._search_running:
            return
        query = self._search_var.get().strip()
        if not query:
            self._count_label.configure(text="")
            return

        self._search_running = True
        self.root.config(cursor="watch")
        self._search_btn.configure(state=tk.DISABLED)
        self._results_text.configure(cursor="watch")
        self.root.update_idletasks()

        try:
            domain = self._get_domain_filter()
            doc_type = "" if self._type_var.get() in ("全部类型", "") else self._type_var.get()
            category = "" if self._cat_var.get() in ("全部分类", "") else self._cat_var.get()

            self._search_terms = extract_search_terms(query)
            fts_query = build_fts5_query(query)

            results: list[sqlite3.Row] = []
            total = 0
            use_fts = bool(fts_query)

            if use_fts:
                total, results = self._fts_search(fts_query, domain, doc_type, category, 0, PAGE_SIZE)

            # FTS returned 0 — fallback to LIKE
            if not results or total == 0:
                total, results = self._like_search(self._search_terms, domain, doc_type, category, 0, PAGE_SIZE)

            self._all_results = results
            self._total_count = total
            self._current_offset = len(results)

            self._render_results()

        except Exception as exc:
            self._show_error(f"搜索出错: {exc}")
        finally:
            self._search_running = False
            self.root.config(cursor="")
            self._results_text.configure(cursor="hand2")
            self._search_btn.configure(state=tk.NORMAL)

    def _fts_search(self, fts_query: str, domain: str, doc_type: str, category: str,
                    offset: int, limit: int) -> tuple[int, list[sqlite3.Row]]:
        """Run FTS5 MATCH search."""
        if self._db is None:
            return 0, []
        where = "documents_fts MATCH ?"
        params: list = [fts_query]
        if domain:
            where += " AND d.domain = ?"
            params.append(domain)
        if doc_type:
            where += " AND d.doc_type = ?"
            params.append(doc_type)
        if category:
            where += " AND d.category = ?"
            params.append(category)

        count_sql = f"SELECT COUNT(*) AS cnt FROM documents_fts f JOIN documents d ON d.id = f.rowid WHERE {where}"
        total = self._db.execute(count_sql, params).fetchone()["cnt"]

        sql = f"""
            SELECT d.id, d.doc_id, d.domain, d.doc_type, d.category, d.name,
                   d.relative_path, d.tags_json, d.content,
                   snippet(documents_fts, 1, '<mark>', '</mark>', '...', 48) AS snippet
            FROM documents_fts f
            JOIN documents d ON d.id = f.rowid
            WHERE {where}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        rows = self._db.execute(sql, params + [limit, offset]).fetchall()
        return total, list(rows)

    def _like_search(self, terms: list[str], domain: str, doc_type: str, category: str,
                     offset: int, limit: int) -> tuple[int, list[sqlite3.Row]]:
        """Fallback: LIKE search on name and content."""
        if self._db is None or not terms:
            return 0, []
        clauses = []
        params: list = []
        for t in terms:
            p = f"%{t}%"
            clauses.append("(d.name LIKE ? OR d.content LIKE ?)")
            params.extend([p, p])

        where = "(" + " AND ".join(clauses) + ")"
        if domain:
            where += " AND d.domain = ?"
            params.append(domain)
        if doc_type:
            where += " AND d.doc_type = ?"
            params.append(doc_type)
        if category:
            where += " AND d.category = ?"
            params.append(category)

        count_sql = f"SELECT COUNT(*) AS cnt FROM documents d WHERE {where}"
        total = self._db.execute(count_sql, params).fetchone()["cnt"]

        sql = f"""
            SELECT d.id, d.doc_id, d.domain, d.doc_type, d.category, d.name,
                   d.relative_path, d.tags_json, d.content,
                   NULL AS snippet
            FROM documents d
            WHERE {where}
            ORDER BY d.id
            LIMIT ? OFFSET ?
        """
        rows = self._db.execute(sql, params + [limit, offset]).fetchall()
        return total, list(rows)

    def _make_snippet(self, content: str, max_len: int = 80) -> str:
        """Build snippet with <mark> highlights for LIKE fallback results."""
        if not content or not self._search_terms:
            return content[:max_len * 2].replace("\n", " ")

        text = content.replace("\n", " ").replace("\r", " ")
        text = re.sub(r"\s+", " ", text).strip()

        # Find first occurrence of any search term
        best_pos = -1
        best_len = 0
        for term in self._search_terms:
            pos = text.find(term)
            if pos != -1:
                if best_pos == -1 or pos < best_pos:
                    best_pos = pos
                    best_len = len(term)
        if best_pos == -1:
            for term in self._search_terms:
                pos = text.lower().find(term.lower())
                if pos != -1:
                    if best_pos == -1 or pos < best_pos:
                        best_pos = pos
                        best_len = len(term)
                    break
        if best_pos == -1:
            return text[:max_len * 2]

        half = max_len
        start = max(0, best_pos - half)
        end = min(len(text), best_pos + best_len + half)

        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""
        fragment = prefix + text[start:end] + suffix

        # Highlight all terms
        escaped = fragment  # no HTML escaping needed for Text widget
        pattern = re.compile(
            "(" + "|".join(re.escape(t) for t in self._search_terms) + ")",
            re.IGNORECASE,
        )
        highlighted = pattern.sub(r"<mark>\1</mark>", escaped)
        return highlighted

    # ── Result rendering ──────────────────────────────────

    def _render_results(self) -> None:
        """Render the current result set in the left panel."""
        self._results_text.configure(state=tk.NORMAL)
        self._results_text.delete("1.0", tk.END)

        # Hide placeholder
        self._results_placeholder.place_forget()

        total = self._total_count
        self._count_label.configure(text=f"找到 {total:,} 条结果")

        self._result_lines = []
        self._result_ids = []

        if not self._all_results:
            self._results_text.insert(tk.END, "\n\n  未找到匹配结果\n", "card_meta")
            self._results_text.configure(state=tk.DISABLED)
            self._load_more_btn.pack_forget()
            return

        for idx, row in enumerate(self._all_results):
            start_line = int(self._results_text.index(tk.END).split(".")[0])

            # ── Row 1: Name + badges ──
            line_start = self._results_text.index(tk.END)
            name = row["name"] if row["name"] else "(未命名)"
            self._results_text.insert(tk.END, f" {name}  ", "result_name")

            # Domain badge
            domain = row["domain"]
            domain_label = DOMAIN_LABELS.get(domain, domain)
            badge_tag = "badge_gi" if domain == "gi" else "badge_hsr"
            self._results_text.insert(tk.END, f" {domain_label} ", badge_tag)

            # Doc type badge
            if row["doc_type"]:
                self._results_text.insert(tk.END, f" {row['doc_type']} ", "card_meta")

            self._results_text.insert(tk.END, "\n")

            # ── Row 2: Meta (category + domain) ──
            meta_parts = []
            if row["category"]:
                meta_parts.append(row["category"])
            meta_parts.append(DOMAIN_LABELS.get(row["domain"], row["domain"]))
            if row["doc_type"]:
                meta_parts.append(row["doc_type"])
            if meta_parts:
                self._results_text.insert(tk.END, "  " + " · ".join(meta_parts) + "\n", "card_meta")

            # ── Row 3: Snippet ──
            snippet = row["snippet"] if row["snippet"] else self._make_snippet(row["content"] or "")
            if snippet:
                self._results_text.insert(tk.END, "  ", "card_snippet")
                insert_formatted_line(self._results_text, snippet, ("card_snippet",))

            self._results_text.insert(tk.END, "\n")

            # ── Separator line ──
            self._results_text.insert(tk.END, "  " + "─" * 55 + "\n", "card_sep")

            end_line = int(self._results_text.index(tk.END).split(".")[0])
            self._result_lines.append((start_line, end_line))
            self._result_ids.append(row["id"])

        self._results_text.configure(state=tk.DISABLED)

        # Show/hide load more button
        if self._current_offset < self._total_count:
            self._load_more_btn.pack(fill=tk.X, padx=4, pady=(4, 0))
        else:
            self._load_more_btn.pack_forget()

    def _load_more(self) -> None:
        """Load next page of results and append."""
        if self._search_running or self._db is None:
            return

        query = self._search_var.get().strip()
        if not query:
            return

        self._search_running = True
        self.root.config(cursor="watch")
        self._load_more_btn.configure(state=tk.DISABLED)
        self.root.update_idletasks()

        try:
            domain = self._get_domain_filter()
            doc_type = "" if self._type_var.get() in ("全部类型", "") else self._type_var.get()
            category = "" if self._cat_var.get() in ("全部分类", "") else self._cat_var.get()

            fts_query = build_fts5_query(query)

            if fts_query:
                _, new_results = self._fts_search(
                    fts_query, domain, doc_type, category,
                    self._current_offset, PAGE_SIZE,
                )
            else:
                _, new_results = self._like_search(
                    self._search_terms, domain, doc_type, category,
                    self._current_offset, PAGE_SIZE,
                )

            if new_results:
                self._all_results.extend(new_results)
                self._current_offset += len(new_results)
                self._append_results(new_results)

        except Exception as exc:
            self._show_error(f"加载更多出错: {exc}")
        finally:
            self._search_running = False
            self.root.config(cursor="")
            self._load_more_btn.configure(state=tk.NORMAL)
            if self._current_offset >= self._total_count:
                self._load_more_btn.pack_forget()

    def _append_results(self, new_results: list[sqlite3.Row]) -> None:
        """Append new result cards to the results text widget."""
        self._results_text.configure(state=tk.NORMAL)

        for row in new_results:
            start_line = int(self._results_text.index(tk.END).split(".")[0])

            name = row["name"] if row["name"] else "(未命名)"
            self._results_text.insert(tk.END, f" {name}  ", "result_name")

            domain = row["domain"]
            domain_label = DOMAIN_LABELS.get(domain, domain)
            badge_tag = "badge_gi" if domain == "gi" else "badge_hsr"
            self._results_text.insert(tk.END, f" {domain_label} ", badge_tag)

            if row["doc_type"]:
                self._results_text.insert(tk.END, f" {row['doc_type']} ", "card_meta")

            self._results_text.insert(tk.END, "\n")

            meta_parts = []
            if row["category"]:
                meta_parts.append(row["category"])
            meta_parts.append(DOMAIN_LABELS.get(row["domain"], row["domain"]))
            if row["doc_type"]:
                meta_parts.append(row["doc_type"])
            if meta_parts:
                self._results_text.insert(tk.END, "  " + " · ".join(meta_parts) + "\n", "card_meta")

            snippet = row["snippet"] if row["snippet"] else self._make_snippet(row["content"] or "")
            if snippet:
                self._results_text.insert(tk.END, "  ", "card_snippet")
                insert_formatted_line(self._results_text, snippet, ("card_snippet",))

            self._results_text.insert(tk.END, "\n")
            self._results_text.insert(tk.END, "  " + "─" * 55 + "\n", "card_sep")

            end_line = int(self._results_text.index(tk.END).split(".")[0])
            self._result_lines.append((start_line, end_line))
            self._result_ids.append(row["id"])

        self._results_text.configure(state=tk.DISABLED)

        if self._current_offset >= self._total_count:
            self._load_more_btn.pack_forget()

    # ── Result interaction ────────────────────────────────

    def _get_result_index_at(self, line: int) -> int | None:
        """Return the result index for a given line number, or None."""
        for idx, (sl, el) in enumerate(self._result_lines):
            if sl <= line < el:
                return idx
        return None

    def _on_result_click(self, event: tk.Event) -> None:
        """Single-click on a result — select and show doc."""
        idx = self._get_result_index_at(int(
            self._results_text.index(f"@{event.x},{event.y}").split(".")[0]
        ))
        if idx is not None and idx < len(self._result_ids):
            self._show_document(self._result_ids[idx])

    def _on_result_double_click(self, event: tk.Event) -> None:
        """Double-click — same as single click for doc display."""
        self._on_result_click(event)

    def _on_result_return(self, event: tk.Event) -> None:
        """Enter key shows the currently focused document, or the first result."""
        # Try to get which result the cursor is near
        cursor_line = int(self._results_text.index(tk.INSERT).split(".")[0])
        idx = self._get_result_index_at(cursor_line)
        if idx is not None and idx < len(self._result_ids):
            self._show_document(self._result_ids[idx])
        return "break"

    def _on_result_up(self, event: tk.Event) -> None:
        """Arrow Up — move cursor to previous result card."""
        cursor_line = int(self._results_text.index(tk.INSERT).split(".")[0])
        current_idx = self._get_result_index_at(cursor_line)
        if current_idx is None:
            return
        prev_idx = max(0, current_idx - 1)
        if prev_idx < len(self._result_lines):
            target_line = self._result_lines[prev_idx][0]
            self._results_text.mark_set(tk.INSERT, f"{target_line}.0")
            self._results_text.see(f"{target_line}.0")
            self._results_text.focus_set()
        return "break"

    def _on_result_down(self, event: tk.Event) -> None:
        """Arrow Down — move cursor to next result card."""
        cursor_line = int(self._results_text.index(tk.INSERT).split(".")[0])
        current_idx = self._get_result_index_at(cursor_line)
        if current_idx is None:
            # If not on a result, go to first
            if self._result_lines:
                target_line = self._result_lines[0][0]
                self._results_text.mark_set(tk.INSERT, f"{target_line}.0")
                self._results_text.see(f"{target_line}.0")
                self._results_text.focus_set()
            return "break"
        next_idx = current_idx + 1
        if next_idx < len(self._result_lines):
            target_line = self._result_lines[next_idx][0]
            self._results_text.mark_set(tk.INSERT, f"{target_line}.0")
            self._results_text.see(f"{target_line}.0")
            self._results_text.focus_set()
        return "break"

    # ── Document viewer ────────────────────────────────────

    def _show_document(self, doc_id: int) -> None:
        """Load and display a full document in the right panel."""
        if self._db is None:
            return

        self._current_doc_id = doc_id
        row = self._db.execute("SELECT * FROM documents WHERE id=?", [doc_id]).fetchone()
        if not row:
            return

        # ── Metadata header ──
        self._doc_title.configure(text=row["name"] or "(未命名)")

        domain = DOMAIN_LABELS.get(row["domain"], row["domain"])
        meta_text = f"{domain}"
        if row["doc_type"]:
            meta_text += f"  ·  {row['doc_type']}"
        if row["category"]:
            meta_text += f"  ·  {row['category']}"
        if row["relative_path"]:
            meta_text += f"\n{row['relative_path']}"
        self._doc_meta.configure(text=meta_text)

        # Tags
        for w in self._doc_tags_frame.winfo_children():
            w.destroy()

        tags: dict[str, str] = {}
        try:
            tags = json.loads(row["tags_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        if tags:
            for k, v in tags.items():
                tag_frame = tk.Frame(self._doc_tags_frame, bg=C["tag_bg"], padx=6, pady=1)
                tag_frame.pack(side=tk.LEFT, padx=(0, 4), pady=2)
                tk.Label(
                    tag_frame, text=f"{k}: {v}",
                    font=self._fonts["badge"], bg=C["tag_bg"], fg=C["fg"],
                ).pack()

        # ── Content ──
        content = row["content"] or ""

        # If we have search terms, add <mark> tags to the content for highlighting
        if self._search_terms:
            pattern = re.compile(
                "(" + "|".join(re.escape(t) for t in self._search_terms) + ")",
                re.IGNORECASE,
            )
            # Only mark words that are not already inside markdown syntax
            content = pattern.sub(r"<mark>\1</mark>", content)

        render_markdown(self._content_text, content)

    # ── Helpers ────────────────────────────────────────────

    def _show_error(self, message: str) -> None:
        """Show error in the results area."""
        self._results_text.configure(state=tk.NORMAL)
        self._results_text.delete("1.0", tk.END)
        self._results_text.insert(tk.END, f"\n\n  {message}\n", "card_meta")
        self._results_text.configure(state=tk.DISABLED)
        self._count_label.configure(text="")

    def _on_close(self) -> None:
        """Clean up and exit."""
        if self._db:
            self._db.close()
            self._db = None
        self.root.destroy()


# ── Entry point ────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
