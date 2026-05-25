"""
米哈游游戏文本检索 - pywebview backend API.
Exposes search, faceted navigation, and document retrieval to the JS frontend.

Database schema (from build_index.py):
  documents      — id, doc_id, domain, doc_type, category, name, path_hash,
                   relative_path, tags_json, content
  documents_fts  — FTS5 virtual table on (name, content, doc_type, category, domain)
                   content=documents, content_rowid=id
"""

import json
import os
import re
import sqlite3

import webview

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "zlb.db")

# ---------------------------------------------------------------------------
# FTS5 query builder
# ---------------------------------------------------------------------------

def build_fts5_query(q):
    """
    Build an FTS5 MATCH query string from raw user input.

    Strips special characters that FTS5 treats as operators (* " ( ) + ^ -),
    removes standalone boolean keywords (AND, OR, NOT, NEAR), and wraps each
    remaining term in double quotes so FTS5 treats them as literal phrases.

    Args:
        q: Raw search query string.

    Returns:
        FTS5-compatible MATCH query, or empty string if no valid terms remain.
    """
    if not q:
        return ""

    # 1. Replace FTS5 syntax characters with spaces
    cleaned = re.sub(r'[*"()+^-]', " ", q)

    # 2. Strip boolean operators when they appear as whole words
    for kw in ("AND", "OR", "NOT", "NEAR"):
        cleaned = re.sub(rf"\b{kw}\b", " ", cleaned, flags=re.IGNORECASE)

    # 3. Wrap remaining terms in double quotes for exact phrase matching
    terms = cleaned.split()
    if not terms:
        return ""

    return " ".join(f'"{t}"' for t in terms)


# ---------------------------------------------------------------------------
# JS API
# ---------------------------------------------------------------------------

class Api:
    """
    JS API exposed to the pywebview frontend via ``js_api=Api()``.

    All public methods return JSON strings (pywebview serialises return values
    through the JS bridge, so returning strings keeps encoding predictable).
    """

    # -- connection ----------------------------------------------------------

    @staticmethod
    def _connect(read_only=True):
        """Create a new thread-safe SQLite connection."""
        if read_only:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    # -- search --------------------------------------------------------------

    def search(self, q, domain="", type="", category="", limit=20, offset=0):
        """
        Full-text search over game documents.

        **FTS5 primary path** — uses ``documents_fts MATCH`` with snippet
        highlights, ranked by relevance.

        **LIKE fallback** — activated when the FTS5 table is missing or the
        MATCH query fails.  Searches ``name`` and ``content`` columns with
        ``LIKE %q%``.

        Args:
            q:        Search terms.
            domain:   Game filter (``"gi"``, ``"hsr"``, or ``""`` for all).
            type:     Document type filter (empty = all).
            category: Category filter (empty = all).
            limit:    Page size (1-200, default 20).
            offset:   Pagination offset.

        Returns:
            JSON string: ``{"total": <int>, "results": [<dict>, ...]}``
        """
        try:
            # --- Normalise inputs ---
            limit = max(1, min(int(limit) if limit else 20, 200))
            offset = max(0, int(offset) if offset else 0)

            conn = self._connect(read_only=True)

            # --- Build optional filter clauses ---
            filters: list[str] = []
            filter_params: list[str] = []

            if domain and str(domain).strip():
                filters.append("d.domain = ?")
                filter_params.append(str(domain).strip())
            if type and str(type).strip():
                filters.append("d.doc_type = ?")
                filter_params.append(str(type).strip())
            if category and str(category).strip():
                filters.append("d.category = ?")
                filter_params.append(str(category).strip())

            filter_sql = " AND ".join(filters) if filters else "1=1"

            # --- FTS5 path ---
            fts_query = build_fts5_query(q) if q else ""
            fts_attempted = False

            if fts_query:
                try:
                    where = "documents_fts MATCH ?"
                    fts_params = [fts_query] + list(filter_params)

                    total_row = conn.execute(
                        f"SELECT COUNT(*) AS c "
                        f"FROM documents_fts f "
                        f"JOIN documents d ON d.id = f.rowid "
                        f"WHERE {where} AND {filter_sql}",
                        fts_params,
                    ).fetchone()

                    total = total_row["c"] if total_row else 0

                    if total > 0:
                        fts_params += [limit, offset]
                        rows = conn.execute(
                            f"SELECT d.*, "
                            f"snippet(documents_fts, 1, '<mark>', '</mark>', '...', 40) AS snippet "
                            f"FROM documents_fts f "
                            f"JOIN documents d ON d.id = f.rowid "
                            f"WHERE {where} AND {filter_sql} "
                            f"ORDER BY rank "
                            f"LIMIT ? OFFSET ?",
                            fts_params,
                        ).fetchall()

                        results = [_row_to_result(r) for r in rows]
                        conn.close()
                        return json.dumps(
                            {"total": total, "results": results},
                            ensure_ascii=False,
                        )

                    fts_attempted = True  # query ran, matched nothing
                except sqlite3.Error:
                    # FTS table missing or malformed query → fall through to LIKE
                    pass

            if fts_attempted:
                conn.close()
                return json.dumps({"total": 0, "results": []}, ensure_ascii=False)

            # --- LIKE fallback ---
            if q and str(q).strip():
                like = f"%{str(q).strip()}%"
                filters.append("(d.name LIKE ? OR d.content LIKE ?)")
                filter_params.extend([like, like])

            filter_sql = " AND ".join(filters) if filters else "1=1"

            # Count
            count_params = list(filter_params)
            total_row = conn.execute(
                f"SELECT COUNT(*) AS c FROM documents d WHERE {filter_sql}",
                count_params,
            ).fetchone()
            total = total_row["c"] if total_row else 0

            # Fetch
            fetch_params = list(filter_params) + [limit, offset]
            rows = conn.execute(
                f"SELECT d.* FROM documents d "
                f"WHERE {filter_sql} "
                f"ORDER BY d.id "
                f"LIMIT ? OFFSET ?",
                fetch_params,
            ).fetchall()

            results = [_row_to_result(r) for r in rows]
            conn.close()

            return json.dumps(
                {"total": total, "results": results},
                ensure_ascii=False,
            )

        except Exception as exc:
            return json.dumps(
                {"total": 0, "results": [], "error": str(exc)},
                ensure_ascii=False,
            )

    # -- facets --------------------------------------------------------------

    def get_facets(self, domain=""):
        """
        Return distinct document-type and category values.

        When *domain* is provided, facets are scoped to that game only.

        Args:
            domain: Game domain filter (``""`` = across all games).

        Returns:
            JSON string: ``{"types": [...], "categories": [...]}``
        """
        try:
            conn = self._connect(read_only=True)

            where = "WHERE 1=1"
            params: list[str] = []
            if domain and str(domain).strip():
                where += " AND domain = ?"
                params = [str(domain).strip()]

            # Domains (global, not scoped by domain filter)
            domain_rows = conn.execute(
                "SELECT DISTINCT domain FROM documents "
                "WHERE domain IS NOT NULL AND domain != '' "
                "ORDER BY domain"
            ).fetchall()
            domains = [r[0] for r in domain_rows]

            type_rows = conn.execute(
                f"SELECT DISTINCT doc_type "
                f"FROM documents {where} "
                f"AND doc_type IS NOT NULL AND doc_type != '' "
                f"ORDER BY doc_type",
                params,
            ).fetchall()
            types = [r[0] for r in type_rows]

            cat_rows = conn.execute(
                f"SELECT DISTINCT category "
                f"FROM documents {where} "
                f"AND category IS NOT NULL AND category != '' "
                f"ORDER BY category",
                params,
            ).fetchall()
            categories = [r[0] for r in cat_rows]

            conn.close()
            return json.dumps(
                {"domains": domains, "types": types, "categories": categories},
                ensure_ascii=False,
            )

        except Exception as exc:
            return json.dumps(
                {"types": [], "categories": [], "error": str(exc)},
                ensure_ascii=False,
            )

    # -- single document -----------------------------------------------------

    def get_doc(self, id):
        """
        Retrieve a single document by its primary key.

        Args:
            id: Document row id.

        Returns:
            JSON string: full document dict, or ``{"error": "not found"}``.
        """
        try:
            conn = self._connect(read_only=True)

            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?", (int(id),)
            ).fetchone()

            conn.close()

            if not row:
                return json.dumps({"error": "not found"}, ensure_ascii=False)

            doc = {
                "id": row["id"],
                "doc_id": row["doc_id"],
                "domain": row["domain"],
                "doc_type": row["doc_type"],
                "category": row["category"],
                "name": row["name"],
                "relative_path": row["relative_path"],
                "tags": json.loads(row["tags_json"] or "{}"),
                "content": row["content"],
            }
            return json.dumps(doc, ensure_ascii=False)

        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_result(row):
    """Convert a ``sqlite3.Row`` to a search-result dict."""
    result = {
        "id": row["id"],
        "doc_id": row["doc_id"],
        "domain": row["domain"],
        "doc_type": row["doc_type"],
        "category": row["category"],
        "name": row["name"],
        "relative_path": row["relative_path"],
        "tags": json.loads(row["tags_json"] or "{}"),
    }
    # snippet column only present in the FTS5 path
    if "snippet" in row.keys():
        result["snippet"] = row["snippet"]
    return result


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Embedded frontend (single-file HTML/CSS/JS)
# ---------------------------------------------------------------------------

HTML_CONTENT = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
/* ── Design Tokens ── */
:root {
  --bg-deep: #06080f;
  --bg-base: #0b0f1a;
  --glass-bg: rgba(255, 255, 255, 0.025);
  --glass-bg-hover: rgba(255, 255, 255, 0.05);
  --glass-bg-active: rgba(139, 92, 246, 0.06);
  --glass-blur: 16px;
  --border-subtle: rgba(255, 255, 255, 0.06);
  --border-default: rgba(255, 255, 255, 0.09);
  --border-accent: rgba(139, 92, 246, 0.45);
  --text-primary: #e8ecf4;
  --text-secondary: #8b95a8;
  --text-muted: #4a5568;
  --accent: #8b5cf6;
  --accent-light: #c4b5fd;
  --accent-glow: rgba(139, 92, 246, 0.18);
  --accent-glow-strong: rgba(139, 92, 246, 0.35);
  --cyan: #22d3ee;
  --green: #34d399;
  --blue: #60a5fa;
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-pill: 20px;
  --shadow-card: 0 2px 8px rgba(0, 0, 0, 0.25), 0 0 1px rgba(255, 255, 255, 0.04);
  --shadow-hover: 0 8px 32px rgba(139, 92, 246, 0.12), 0 2px 8px rgba(0, 0, 0, 0.35);
  --shadow-active: 0 0 0 1px var(--accent), 0 4px 20px rgba(139, 92, 246, 0.2);
  --transition-fast: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  --transition-smooth: 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  --transition-bounce: 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
}

/* ── Reset ── */
* { margin: 0; padding: 0; box-sizing: border-box; }

/* ── Body & Atmosphere ── */
body {
  font: 14px/1.6 "Microsoft YaHei", "PingFang SC", "Segoe UI", system-ui, -apple-system, sans-serif;
  background: var(--bg-deep);
  color: var(--text-primary);
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  background:
    radial-gradient(ellipse 80% 60% at 15% 10%, rgba(139, 92, 246, 0.07) 0%, transparent 60%),
    radial-gradient(ellipse 70% 50% at 85% 85%, rgba(34, 211, 238, 0.04) 0%, transparent 55%),
    radial-gradient(ellipse 60% 40% at 50% 50%, rgba(139, 92, 246, 0.025) 0%, transparent 70%);
  pointer-events: none;
  z-index: 0;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: rgba(139, 92, 246, 0.2);
  border-radius: 10px;
}
::-webkit-scrollbar-thumb:hover {
  background: rgba(139, 92, 246, 0.4);
}

/* ── Header ── */
.header {
  background: rgba(11, 15, 26, 0.75);
  -webkit-backdrop-filter: blur(24px) saturate(1.4);
  backdrop-filter: blur(24px) saturate(1.4);
  border-bottom: 1px solid var(--border-default);
  padding: 16px 20px;
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}

.header::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent-glow), transparent);
}

.header h1 {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.02em;
  background: linear-gradient(135deg, #8b5cf6 0%, #c4b5fd 45%, #22d3ee 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 12px;
}

/* ── Search Row ── */
.search-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.search-row input {
  flex: 1;
  min-width: 200px;
  padding: 10px 16px;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: var(--transition-smooth);
}

.search-row input::placeholder {
  color: var(--text-muted);
}

.search-row input:focus {
  border-color: var(--accent);
  background: var(--glass-bg-hover);
  box-shadow:
    0 0 0 3px var(--accent-glow),
    0 0 24px var(--accent-glow),
    inset 0 1px 0 rgba(255, 255, 255, 0.04);
}

.search-row select {
  padding: 10px 34px 10px 12px;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  outline: none;
  appearance: none;
  cursor: pointer;
  transition: var(--transition-smooth);
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238b95a8' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
}

.search-row select:hover {
  border-color: var(--border-accent);
  background-color: var(--glass-bg-hover);
}

.search-row select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}

.search-row select option {
  background: #12162a;
  color: var(--text-primary);
}

#status {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 8px;
  letter-spacing: 0.01em;
}

/* ── Main Layout ── */
.main {
  display: flex;
  flex: 1;
  overflow: hidden;
  position: relative;
  z-index: 1;
}

/* ── Results Panel ── */
.results-panel {
  width: 380px;
  min-width: 280px;
  border-right: 1px solid var(--border-default);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: rgba(11, 15, 26, 0.45);
  -webkit-backdrop-filter: blur(12px);
  backdrop-filter: blur(12px);
}

.results-header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-default);
  font-size: 13px;
  color: var(--text-secondary);
  letter-spacing: 0.01em;
}

.results-header b {
  color: var(--accent-light);
  font-weight: 600;
}

.results-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

/* ── Card Entrance Animation ── */
@-webkit-keyframes cardSlideIn {
  from { opacity: 0; -webkit-transform: translateY(10px) scale(0.98); }
  to   { opacity: 1; -webkit-transform: translateY(0) scale(1); }
}
@keyframes cardSlideIn {
  from { opacity: 0; transform: translateY(10px) scale(0.98); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}

/* ── Result Cards ── */
.card {
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: var(--transition-smooth);
  -webkit-animation: cardSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1) both;
  animation: cardSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1) both;
  position: relative;
  overflow: hidden;
}

.card::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  opacity: 0;
  transition: opacity var(--transition-smooth);
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.08), rgba(34, 211, 238, 0.04));
  pointer-events: none;
}

.card:hover {
  border-color: var(--border-accent);
  background: var(--glass-bg-hover);
  -webkit-transform: translateY(-2px);
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
}

.card:hover::before {
  opacity: 1;
}

.card.active {
  border-color: var(--accent);
  background: var(--glass-bg-active);
  box-shadow: var(--shadow-active);
}

.card.active::before {
  opacity: 1;
}

.card-name {
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--text-primary);
  position: relative;
}

.card-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

/* ── Badges ── */
.badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  font-weight: 500;
  letter-spacing: 0.02em;
}

.badge-gi {
  background: rgba(52, 211, 153, 0.1);
  color: #34d399;
  border: 1px solid rgba(52, 211, 153, 0.22);
}

.badge-hsr {
  background: rgba(96, 165, 250, 0.1);
  color: #60a5fa;
  border: 1px solid rgba(96, 165, 250, 0.22);
}

.badge-type {
  background: rgba(139, 92, 246, 0.1);
  color: var(--accent-light);
  border: 1px solid rgba(139, 92, 246, 0.22);
}

.card-snippet {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 8px;
  line-height: 1.55;
}

.card-snippet mark {
  background: rgba(139, 92, 246, 0.22);
  color: var(--accent-light);
  padding: 1px 4px;
  border-radius: 3px;
}

/* ── Detail Panel ── */
.detail-panel {
  flex: 1;
  overflow-y: auto;
  padding: 28px 32px;
  background: rgba(11, 15, 26, 0.25);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
}

.detail-panel h2 {
  font-size: 22px;
  font-weight: 700;
  margin-bottom: 10px;
  letter-spacing: -0.01em;
  color: var(--text-primary);
}

.detail-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}

/* ── Markdown Content ── */
.detail-content {
  line-height: 1.8;
  color: var(--text-primary);
}

.detail-content h1,
.detail-content h2,
.detail-content h3 {
  margin: 24px 0 12px;
  color: var(--text-primary);
}

.detail-content h1 {
  font-size: 20px;
  font-weight: 700;
  border-bottom: 1px solid var(--border-default);
  padding-bottom: 8px;
  position: relative;
}

.detail-content h1::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  width: 48px;
  height: 2px;
  background: linear-gradient(90deg, var(--accent), transparent);
  border-radius: 1px;
}

.detail-content h2 {
  font-size: 17px;
  font-weight: 600;
  color: var(--accent-light);
}

.detail-content h3 {
  font-size: 15px;
  font-weight: 600;
  color: #a5b4fc;
}

.detail-content p {
  margin: 8px 0;
}

.detail-content ul,
.detail-content ol {
  margin: 8px 0;
  padding-left: 24px;
}

.detail-content li {
  margin: 4px 0;
}

.detail-content blockquote {
  border-left: 3px solid var(--accent);
  padding: 8px 16px;
  margin: 12px 0;
  color: var(--text-secondary);
  background: var(--glass-bg);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
}

.detail-content code {
  background: rgba(139, 92, 246, 0.1);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 13px;
  color: var(--accent-light);
  border: 1px solid rgba(139, 92, 246, 0.12);
}

.detail-content pre {
  background: rgba(11, 15, 26, 0.75);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 16px;
  overflow-x: auto;
  margin: 12px 0;
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
}

.detail-content pre code {
  background: none;
  padding: 0;
  color: var(--text-primary);
  border: none;
}

.detail-content table {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.detail-content td,
.detail-content th {
  border: 1px solid var(--border-default);
  padding: 8px 12px;
  text-align: left;
}

.detail-content th {
  background: var(--glass-bg-hover);
  font-weight: 600;
  color: var(--accent-light);
}

.detail-content strong {
  color: var(--accent-light);
  font-weight: 600;
}

.detail-content hr {
  border: none;
  border-top: 1px solid var(--border-default);
  margin: 16px 0;
}

.detail-content a {
  color: var(--accent);
  text-decoration: none;
  transition: color var(--transition-fast);
}

.detail-content a:hover {
  color: var(--accent-light);
}

/* ── Load More ── */
.load-more {
  text-align: center;
  padding: 12px;
}

.load-more button {
  padding: 10px 28px;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  transition: var(--transition-smooth);
}

.load-more button:hover {
  border-color: var(--accent);
  color: var(--accent-light);
  box-shadow: 0 0 20px var(--accent-glow);
  background: var(--glass-bg-hover);
}

/* ── Empty State ── */
.empty {
  text-align: center;
  color: var(--text-muted);
  padding: 80px 20px;
}

.empty svg {
  width: 56px;
  height: 56px;
  margin-bottom: 16px;
  opacity: 0.2;
  stroke: var(--text-secondary);
}

.empty p {
  font-size: 14px;
  color: var(--text-muted);
  letter-spacing: 0.02em;
}

/* ── Toast ── */
.toast {
  position: fixed;
  bottom: 24px;
  left: 50%;
  -webkit-transform: translateX(-50%);
  transform: translateX(-50%);
  background: linear-gradient(135deg, var(--accent), #6d28d9);
  color: white;
  padding: 10px 24px;
  border-radius: var(--radius-md);
  font-size: 13px;
  z-index: 9999;
  box-shadow: 0 4px 24px rgba(139, 92, 246, 0.35);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
}
</style>
</head>
<body>

<div class="header">
    <h1>MHY Text Database</h1>
    <div class="search-row">
        <input id="q" type="text" placeholder="搜索游戏文本..." autofocus>
        <select id="fdomain"><option value="">全部游戏</option></select>
        <select id="ftype"><option value="">全部类型</option></select>
        <select id="fcat"><option value="">全部分类</option></select>
    </div>
    <div id="status" style="color:#f85149">JS未执行</div>
    <script>document.getElementById('status').textContent='内联JS已执行';document.getElementById('status').style.color='lime';</script>
</div>

<div class="main">
    <div class="results-panel">
        <div class="results-header">找到 <b id="rcount">0</b> 条结果</div>
        <div class="results-list" id="rlist"></div>
        <div class="load-more" id="loadmore" style="display:none">
            <button onclick="loadMore()">加载更多</button>
        </div>
    </div>
    <div class="detail-panel" id="detail">
        <div class="empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
            <p>选择左侧文档查看详情</p>
        </div>
    </div>
</div>

<script src="app.js"></script>
</body>
</html>
"""


if __name__ == "__main__":
    import tempfile
    import pathlib
    import shutil
    tmp = pathlib.Path(tempfile.gettempdir())
    html_path = tmp / "mhy_search.html"
    html_path.write_text(HTML_CONTENT, encoding="utf-8")
    # Copy app.js alongside
    js_src = pathlib.Path(__file__).parent / "app.js"
    shutil.copy(js_src, tmp / "app.js")
    window = webview.create_window(
        title="米哈游游戏文本检索",
        url=str(html_path),
        text_select=True,
        js_api=Api(),
        width=1280,
        height=800,
    )
    webview.start()
