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

            where = ""
            params: list[str] = []
            if domain and str(domain).strip():
                where = "WHERE domain = ?"
                params = [str(domain).strip()]

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
                {"types": types, "categories": categories},
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

if __name__ == "__main__":
    window = webview.create_window(
        title="米哈游游戏文本检索",
        url="index.html",
        js_api=Api(),
        width=1280,
        height=800,
    )
    webview.start()
