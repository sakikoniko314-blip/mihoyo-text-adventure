#!/usr/bin/env python3

import json
import os
import sqlite3
import sys
import time


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "..", "data")
    if not os.path.isdir(data_dir):
        data_dir = "/home/misak/zlb-scraper/data"

    if not os.path.isdir(data_dir):
        print(f"Error: data directory not found at {data_dir}", file=sys.stderr)
        sys.exit(1)

    db_dir = os.path.join(script_dir, "data")
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "zlb.db")

    if os.path.exists(db_path):
        os.remove(db_path)

    start_time = time.time()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-20000")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER NOT NULL,
            domain TEXT NOT NULL,
            doc_type TEXT,
            category TEXT,
            name TEXT,
            path_hash TEXT,
            relative_path TEXT NOT NULL,
            tags_json TEXT,
            content TEXT,
            UNIQUE(domain, relative_path)
        )
    """)

    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            name, content, doc_type, category, domain,
            content=documents,
            content_rowid=id,
            tokenize='unicode61'
        )
    """)

    manifest_path = os.path.join(data_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        domains = json.load(f)

    total_docs = 0

    for domain_entry in domains:
        domain = domain_entry["id"]
        index_path = os.path.join(data_dir, domain, "index.json")

        print(f"Loading index for [{domain}]...")
        with open(index_path, "r", encoding="utf-8") as f:
            entries = json.load(f)

        domain_start = time.time()
        inserted = 0
        errors = 0

        for i, entry in enumerate(entries):
            if (i + 1) % 5000 == 0:
                elapsed = time.time() - domain_start
                rps = (i + 1) / elapsed if elapsed > 0 else 0
                print(f"  [{domain}] {i + 1}/{len(entries)} ({rps:.0f} docs/s)")

            relative_path = entry["relativePath"]
            md_path = os.path.join(data_dir, domain, "docs", relative_path)

            content = ""
            try:
                if os.path.exists(md_path):
                    with open(md_path, "r", encoding="utf-8") as f:
                        content = f.read()
            except Exception:
                errors += 1
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO documents
                (doc_id, domain, doc_type, category, name, path_hash, relative_path, tags_json, content)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["id"],
                    domain,
                    entry.get("type", ""),
                    entry.get("category", ""),
                    entry.get("name", ""),
                    entry.get("pathHash", ""),
                    relative_path,
                    json.dumps(entry.get("tags", {}), ensure_ascii=False),
                    content,
                ),
            )
            inserted += 1

        elapsed = time.time() - domain_start
        print(f"  [{domain}] {inserted} documents in {elapsed:.1f}s"
              f" ({errors} errors)" if errors else f"  [{domain}] {inserted} documents in {elapsed:.1f}s")
        total_docs += inserted

    print("Populating FTS index...")
    fts_start = time.time()
    conn.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
    print(f"FTS index built in {time.time() - fts_start:.1f}s")

    print("Creating standard indexes...")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_domain ON documents(domain)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(doc_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category)")
    conn.commit()
    conn.close()

    elapsed = time.time() - start_time
    print(f"\nDone. {total_docs} documents indexed in {elapsed:.1f}s")
    print(f"Database: {db_path}")
    db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
    print(f"Size: {db_size_mb:.1f} MB")


if __name__ == "__main__":
    main()
