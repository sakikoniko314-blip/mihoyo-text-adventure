import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from urllib.parse import quote

import aiofiles
import aiohttp

BASE_URL = "https://agent.zlb.ink"
DATA_DIR = "data"
DOMAINS = ["gi", "hsr"]
MAX_CONCURRENT = 8
REQUEST_DELAY = 0.1
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0
REQUEST_TIMEOUT = 30

shutdown_event = asyncio.Event()


def signal_handler(sig, frame):
    if not shutdown_event.is_set():
        print("\nSIGINT received, saving progress and shutting down...")
        shutdown_event.set()


def load_progress():
    progress_path = Path(DATA_DIR) / "progress.json"
    if progress_path.exists():
        try:
            return json.loads(progress_path.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_progress(data):
    progress_path = Path(DATA_DIR) / "progress.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def url_encode_path(relative_path):
    return "/".join(quote(part, safe="") for part in relative_path.split("/"))


async def fetch_with_retry(session, url):
    for attempt in range(1, MAX_RETRIES + 1):
        if shutdown_event.is_set():
            return None
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as resp:
                if resp.status == 200:
                    return await resp.text()
        except Exception:
            pass
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
    return None


async def download_and_save(session, url, filepath):
    content = await fetch_with_retry(session, url)
    if content is None:
        return False
    filepath.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(str(filepath), "w", encoding="utf-8") as f:
        await f.write(content)
    return True


async def fetch_manifest(session, data_dir):
    url = f"{BASE_URL}/domains/manifest.json"
    filepath = Path(data_dir) / "manifest.json"
    ok = await download_and_save(session, url, filepath)
    if not ok:
        print(f"FATAL: Failed to download manifest from {url}")
        sys.exit(1)
    try:
        return json.loads(filepath.read_text())
    except (json.JSONDecodeError, IOError) as e:
        print(f"FATAL: Failed to parse manifest: {e}")
        sys.exit(1)


async def fetch_index(session, domain, data_dir):
    url = f"{BASE_URL}/domains/{domain}/metadata/index.json"
    filepath = Path(data_dir) / domain / "index.json"
    ok = await download_and_save(session, url, filepath)
    if not ok:
        print(f"FATAL: Failed to download index for {domain} from {url}")
        sys.exit(1)
    try:
        return json.loads(filepath.read_text())
    except (json.JSONDecodeError, IOError) as e:
        print(f"FATAL: Failed to parse index for {domain}: {e}")
        sys.exit(1)


class ProgressTracker:
    def __init__(self):
        self.progress = load_progress()
        self.by_domain = {}
        self.total = 0
        self.done = 0
        self.lock = asyncio.Lock()
        self.start_time = None
        self.last_log_at = 0

    def init_domain(self, domain_id, entries):
        dp = self.progress.setdefault(domain_id, {"downloaded": [], "total": 0})
        dp["total"] = len(entries)
        self.by_domain[domain_id] = dp
        already = set(dp.get("downloaded", []))
        self.total += len(entries)
        self.done += sum(1 for e in entries if e["relativePath"] in already)
        save_progress(self.progress)
        return already

    async def mark_done(self, domain_id, rel_path):
        async with self.lock:
            self.done += 1
            self.by_domain[domain_id]["downloaded"].append(rel_path)
            if self.done - self.last_log_at >= 100:
                self.last_log_at = self.done
                elapsed = time.monotonic() - self.start_time if self.start_time else 1
                rate = self.done / elapsed if elapsed > 0 else 0
                pct = self.done / self.total * 100 if self.total else 0
                print(
                    f"[{domain_id}] {self.done}/{self.total} "
                    f"({pct:.1f}%) - {rate:.1f} docs/s"
                )
                save_progress(self.progress)

    async def finalize_domain(self, domain_id):
        async with self.lock:
            save_progress(self.progress)
            dp = self.by_domain[domain_id]
            print(
                f"Done. Downloaded {len(dp['downloaded'])}/{dp['total']} "
                f"documents for {domain_id}."
            )


async def process_domain(session, domain_id, entries, data_dir, tracker):
    if shutdown_event.is_set():
        return

    already_downloaded = tracker.init_domain(domain_id, entries)
    pending = [
        e for e in entries if e["relativePath"] not in already_downloaded
    ]
    if not pending:
        await tracker.finalize_domain(domain_id)
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def download_one(entry):
        if shutdown_event.is_set():
            return
        encoded = url_encode_path(entry["relativePath"])
        url = f"{BASE_URL}/domains/{domain_id}/docs/{encoded}"
        filepath = Path(data_dir) / domain_id / "docs" / entry["relativePath"]

        await asyncio.sleep(REQUEST_DELAY)
        async with semaphore:
            ok = await download_and_save(session, url, filepath)

        if ok:
            await tracker.mark_done(domain_id, entry["relativePath"])

    tasks = [asyncio.create_task(download_one(e)) for e in pending]
    await asyncio.gather(*tasks)

    await tracker.finalize_domain(domain_id)


async def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    data_dir = Path(DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)

    connector = aiohttp.TCPConnector(limit=0, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        print("Downloading manifest...")
        manifest = await fetch_manifest(session, str(data_dir))
        print(
            f"Manifest: {len(manifest)} domains found "
            f"({', '.join(d['id'] for d in manifest)})"
        )
        if shutdown_event.is_set():
            return

        domain_indexes = {}
        for entry in manifest:
            did = entry["id"]
            if did not in DOMAINS:
                continue
            print(f"Fetching index for {did} ({entry['name']})...")
            domain_indexes[did] = await fetch_index(session, did, str(data_dir))
            print(f"  {did}: {len(domain_indexes[did])} documents")
            if shutdown_event.is_set():
                return

        tracker = ProgressTracker()
        tracker.start_time = time.monotonic()

        tasks = [
            process_domain(session, did, domain_indexes[did], str(data_dir), tracker)
            for did in domain_indexes
        ]
        await asyncio.gather(*tasks)

        elapsed = time.monotonic() - tracker.start_time
        print(f"\nTotal time: {elapsed:.1f}s")
        print(f"Total downloaded: {tracker.done}/{tracker.total}")
        progress = load_progress()
        print(f"Final progress saved to {Path(DATA_DIR) / 'progress.json'}")
        print("All done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted. Progress saved.")
