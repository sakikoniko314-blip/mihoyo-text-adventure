#!/usr/bin/env python3

import json
import os
import re
import sqlite3
import time

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "zlb.db")

app = FastAPI(title="ZLB Search")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def build_fts5_query(user_query: str) -> str:
    clean = re.sub(r'[*"()+^-]', " ", user_query)
    clean = re.sub(r"\bAND\b", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bOR\b", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bNOT\b", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bNEAR\b", " ", clean, flags=re.IGNORECASE)
    terms = clean.split()
    if not terms:
        return ""
    return " ".join(f'"{t}"' for t in terms)


def make_snippet(content: str, query_terms: list[str], max_len: int = 80) -> str:
    if not content or not query_terms:
        return ""
    text = content.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()

    best_pos = -1
    best_term = ""
    for term in query_terms:
        pos = text.find(term)
        if pos != -1:
            if best_pos == -1 or pos < best_pos:
                best_pos = pos
                best_term = term
    if best_pos == -1:
        for term in query_terms:
            pos = text.lower().find(term.lower())
            if pos != -1:
                if best_pos == -1 or pos < best_pos:
                    best_pos = pos
                    best_term = text[pos:pos + len(term)]
                break
        if best_pos == -1:
            return text[:max_len * 3]

    half = max_len
    start = max(0, best_pos - half)
    end = min(len(text), best_pos + len(best_term) + half)
    if start > 0:
        while start > 0 and (ord(text[start]) & 0xC0) == 0x80:
            start -= 1
    if end < len(text):
        while end < len(text) and (ord(text[end]) & 0xC0) == 0x80:
            end += 1

    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    fragment = prefix + text[start:end] + suffix

    escaped = re.sub(r"[&<>]", lambda m: "&amp;" if m[0] == "&" else ("&lt;" if m[0] == "<" else "&gt;"), fragment)
    pattern = re.compile("(" + "|".join(re.escape(t) for t in query_terms) + ")", re.IGNORECASE)
    highlighted = pattern.sub(r"<mark>\1</mark>", escaped)
    return highlighted


@app.get("/api/search")
def api_search(
    q: str = Query(..., description="Search query"),
    domain: str = Query("", description="Filter by domain"),
    type: str = Query("", description="Filter by doc type"),
    category: str = Query("", description="Filter by category"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    fts_query = build_fts5_query(q)
    if not fts_query:
        return {"total": 0, "results": []}

    conn = get_db()
    try:
        where = "documents_fts MATCH ?"
        params = [fts_query]

        if domain:
            where += " AND d.domain = ?"
            params.append(domain)
        if type:
            where += " AND d.doc_type = ?"
            params.append(type)
        if category:
            where += " AND d.category = ?"
            params.append(category)

        count_sql = f"SELECT COUNT(*) as cnt FROM documents_fts f JOIN documents d ON d.id = f.rowid WHERE {where}"
        total = conn.execute(count_sql, params).fetchone()["cnt"]

        params += [limit, offset]
        sql = f"""
            SELECT d.id, d.doc_id, d.domain, d.doc_type, d.category, d.name, d.relative_path, d.tags_json,
                   d.content
            FROM documents_fts f
            JOIN documents d ON d.id = f.rowid
            WHERE {where}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(sql, params).fetchall()

        query_terms = [t for t in re.sub(r'[*"()+^-]', " ", q).split() if t]
        results = []
        for row in rows:
            tags = {}
            try:
                tags = json.loads(row["tags_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                pass
            results.append({
                "id": row["id"],
                "doc_id": row["doc_id"],
                "domain": row["domain"],
                "doc_type": row["doc_type"],
                "category": row["category"],
                "name": row["name"],
                "relative_path": row["relative_path"],
                "tags": tags,
                "snippet": make_snippet(row["content"], query_terms),
            })

        return {"total": total, "results": results}
    finally:
        conn.close()


@app.get("/api/facets")
def api_facets(domain: str = Query("", description="Filter by domain")):
    conn = get_db()
    try:
        if domain:
            doc_types = [r[0] for r in conn.execute(
                "SELECT DISTINCT doc_type FROM documents WHERE domain = ? ORDER BY doc_type", [domain]
            )]
            categories = [r[0] for r in conn.execute(
                "SELECT DISTINCT category FROM documents WHERE domain = ? ORDER BY category", [domain]
            )]
        else:
            doc_types = [r[0] for r in conn.execute(
                "SELECT DISTINCT doc_type FROM documents ORDER BY doc_type"
            )]
            categories = [r[0] for r in conn.execute(
                "SELECT DISTINCT category FROM documents ORDER BY category"
            )]
        return {"doc_types": doc_types, "categories": categories}
    finally:
        conn.close()


@app.get("/api/doc/{doc_id}")
def api_doc(doc_id: int):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", [doc_id]
        ).fetchone()
        if not row:
            return JSONResponse(status_code=404, content={"error": "Document not found"})
        tags = {}
        try:
            tags = json.loads(row["tags_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        return {
            "id": row["id"],
            "doc_id": row["doc_id"],
            "domain": row["domain"],
            "doc_type": row["doc_type"],
            "category": row["category"],
            "name": row["name"],
            "path_hash": row["path_hash"],
            "relative_path": row["relative_path"],
            "tags": tags,
            "content": row["content"],
        }
    finally:
        conn.close()


@app.get("/", response_class=HTMLResponse)
def index():
    return _HTML


_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ZLB 文本搜索</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #f5f5f5;
  --card-bg: #ffffff;
  --text: #1a1a1a;
  --text-secondary: #666;
  --border: #ddd;
  --accent: #6366f1;
  --accent-hover: #4f46e5;
  --mark-bg: #fef08a;
  --tag-bg: #eef2ff;
  --tag-text: #4338ca;
  --radius: 8px;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  min-height: 100vh;
}

.container { max-width: 860px; margin: 0 auto; padding: 20px 16px 60px; }

header { text-align: center; margin-bottom: 28px; }
header h1 { font-size: 1.6rem; font-weight: 700; color: var(--accent); letter-spacing: -0.02em; }

.search-section { background: var(--card-bg); border-radius: var(--radius); padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }

.search-row {
  display: flex; gap: 8px; align-items: center; margin-bottom: 12px;
}
.search-row input { flex: 1; }
.search-row button {
  padding: 10px 24px; background: var(--accent); color: #fff; border: none;
  border-radius: var(--radius); font-size: 0.95rem; font-weight: 500; cursor: pointer;
  white-space: nowrap; transition: background 0.15s;
}
.search-row button:hover { background: var(--accent-hover); }
.search-row button:disabled { opacity: 0.6; cursor: not-allowed; }

input, select {
  padding: 10px 12px; border: 1.5px solid var(--border); border-radius: var(--radius);
  font-size: 0.95rem; font-family: inherit; background: #fff; color: var(--text);
  transition: border-color 0.15s;
}
input:focus, select:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(99,102,241,0.12); }

.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.filters select { flex: 1; min-width: 120px; }

.stats { margin: 16px 0 4px; font-size: 0.82rem; color: var(--text-secondary); }

.result-card {
  background: var(--card-bg); border-radius: var(--radius); padding: 16px 20px;
  margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  cursor: pointer; transition: box-shadow 0.15s; border: 1.5px solid transparent;
}
.result-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.result-card.active { border-color: var(--accent); }

.result-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.result-name { font-weight: 600; font-size: 1.02rem; }
.badges { display: flex; gap: 6px; flex-wrap: wrap; }
.badge {
  font-size: 0.72rem; padding: 2px 8px; border-radius: 99px;
  background: var(--tag-bg); color: var(--tag-text); font-weight: 500;
}

.result-meta { display: flex; gap: 12px; font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 6px; }

.result-snippet { font-size: 0.9rem; line-height: 1.7; word-break: break-word; }
.result-snippet mark { background: var(--mark-bg); color: inherit; border-radius: 2px; padding: 0 1px; }

.expanded { margin-top: 12px; padding-top: 12px; border-top: 1.5px solid var(--border); }
.full-content { font-size: 0.88rem; line-height: 1.8; white-space: pre-wrap; word-break: break-word; max-height: 400px; overflow-y: auto; }

.pagination { display: flex; justify-content: center; gap: 10px; margin-top: 24px; }
.pagination button {
  padding: 8px 20px; background: var(--card-bg); border: 1.5px solid var(--border);
  border-radius: var(--radius); font-size: 0.9rem; cursor: pointer; color: var(--text);
  transition: all 0.15s;
}
.pagination button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
.pagination span { padding: 8px 4px; font-size: 0.9rem; color: var(--text-secondary); }

.no-results { text-align: center; padding: 60px 20px; color: var(--text-secondary); font-size: 0.95rem; }

.loading { text-align: center; padding: 40px; color: var(--text-secondary); }

@media (max-width: 600px) {
  .container { padding: 12px 10px 40px; }
  .search-section { padding: 14px; }
  .search-row { flex-wrap: wrap; }
  .search-row button { width: 100%; }
  .filters select { min-width: 0; }
  .result-card { padding: 12px 14px; }
}
</style>
</head>
<body>
<div class="container">
<header><h1>ZLB 文本搜索</h1></header>

<div class="search-section">
  <div class="search-row">
    <input type="text" id="q" placeholder="输入搜索关键词..." autofocus
           onkeydown="if(event.key==='Enter') doSearch(0)">
    <button id="searchBtn" onclick="doSearch(0)">搜索</button>
  </div>
  <div class="filters">
    <select id="domain" onchange="onDomainChange()">
      <option value="">全部游戏</option>
      <option value="gi">原神</option>
      <option value="hsr">崩坏星穹铁道</option>
    </select>
    <select id="type"><option value="">全部类型</option></select>
    <select id="category"><option value="">全部分类</option></select>
  </div>
  <div class="stats" id="stats"></div>
</div>

<div id="results"></div>
<div class="pagination" id="pagination"></div>
</div>

<script>
const PAGE_SIZE = 20;
let currentOffset = 0;
let totalResults = 0;
let allFacets = {doc_types: [], categories: []};

function $(id) { return document.getElementById(id); }

async function loadFacets() {
  const domain = $('domain').value;
  try {
    const resp = await fetch('/api/facets?domain=' + encodeURIComponent(domain));
    const data = await resp.json();
    allFacets = data;
    populateSelect('type', data.doc_types);
    populateSelect('category', data.categories);
  } catch(e) { console.error(e); }
}

function populateSelect(id, values) {
  const sel = $(id);
  const current = sel.value;
  sel.innerHTML = (id === 'type' ? '<option value="">全部类型</option>' : '<option value="">全部分类</option>');
  for (const v of values) {
    sel.innerHTML += '<option value="' + escapeHtml(v) + '">' + escapeHtml(v) + '</option>';
  }
  sel.value = current || '';
}

function onDomainChange() {
  currentOffset = 0;
  loadFacets().then(() => doSearch(0));
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

async function doSearch(offset) {
  const q = $('q').value.trim();
  if (!q) return;

  $('searchBtn').disabled = true;
  $('results').innerHTML = '<div class="loading">搜索中...</div>';
  $('pagination').innerHTML = '';
  currentOffset = offset;

  const params = new URLSearchParams({
    q: q,
    domain: $('domain').value,
    type: $('type').value,
    category: $('category').value,
    limit: PAGE_SIZE,
    offset: offset
  });

  try {
    const resp = await fetch('/api/search?' + params.toString());
    const data = await resp.json();
    totalResults = data.total;
    renderResults(data);
    renderPagination();
  } catch(e) {
    $('results').innerHTML = '<div class="no-results">搜索出错，请重试</div>';
  } finally {
    $('searchBtn').disabled = false;
  }
}

function renderResults(data) {
  $('stats').textContent = '找到 ' + data.total + ' 条结果';
  if (!data.results.length) {
    $('results').innerHTML = '<div class="no-results">未找到匹配结果</div>';
    return;
  }
  let html = '';
  for (const r of data.results) {
    const tagsSlice = Object.keys(r.tags).length ? Object.entries(r.tags).slice(0, 4) : [];
    const tagBadges = tagsSlice.map(([k,v]) => '<span class="badge">' + escapeHtml(k) + ': ' + escapeHtml(v) + '</span>').join('');
    html += '<div class="result-card" onclick="onResultClick(this, ' + r.id + ')">'
      + '<div class="result-header">'
      + '<span class="result-name">' + escapeHtml(r.name) + '</span>'
      + '</div>'
      + '<div class="result-meta">'
      + '<span>' + escapeHtml(r.doc_type) + '</span>'
      + '<span>' + escapeHtml(r.category) + '</span>'
      + '<span>' + escapeHtml(r.domain.toUpperCase()) + '</span>'
      + '</div>'
      + (tagBadges ? '<div class="badges">' + tagBadges + '</div>' : '')
      + '<div class="result-snippet">' + (r.snippet || '') + '</div>'
      + '<div class="expanded" id="exp-' + r.id + '" style="display:none"></div>'
      + '</div>';
  }
  $('results').innerHTML = html;
}

async function onResultClick(card, docId) {
  const exp = $('exp-' + docId);
  if (exp.style.display === 'none') {
    card.classList.add('active');
    exp.innerHTML = '<div class="loading">加载中...</div>';
    exp.style.display = 'block';
    try {
      const resp = await fetch('/api/doc/' + docId);
      const doc = await resp.json();
      exp.innerHTML = '<div class="full-content">' + escapeHtml(doc.content) + '</div>';
    } catch(e) {
      exp.innerHTML = '<div class="no-results">加载失败</div>';
    }
  } else {
    card.classList.remove('active');
    exp.style.display = 'none';
  }
}

function renderPagination() {
  const totalPages = Math.ceil(totalResults / PAGE_SIZE);
  const currentPage = Math.floor(currentOffset / PAGE_SIZE) + 1;
  let html = '';
  html += '<button onclick="doSearch(0)" ' + (currentOffset === 0 ? 'disabled' : '') + '>首页</button>';
  html += '<button onclick="doSearch(' + Math.max(0, currentOffset - PAGE_SIZE) + ')" ' + (currentOffset === 0 ? 'disabled' : '') + '>上一页</button>';
  html += '<span>' + currentPage + ' / ' + totalPages + '</span>';
  html += '<button onclick="doSearch(' + (currentOffset + PAGE_SIZE) + ')" ' + (currentOffset + PAGE_SIZE >= totalResults ? 'disabled' : '') + '>下一页</button>';
  html += '<button onclick="doSearch(' + (totalPages - 1) * PAGE_SIZE + ')" ' + (currentOffset + PAGE_SIZE >= totalResults ? 'disabled' : '') + '>末页</button>';
  $('pagination').innerHTML = html;
}

loadFacets();
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
