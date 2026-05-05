"""Create the Atlas Vector Search index on `chunks` and poll until queryable.

Run from repo root:  python scripts/ensure_vector_index.py
"""
from __future__ import annotations

import os
import sys
import time
from itertools import cycle
from pathlib import Path
from urllib.parse import urlparse

from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_dotenv(ROOT / ".env")

URI = os.environ.get("MONGODB_URI")
if not URI:
    print("ERROR: MONGODB_URI not set", file=sys.stderr)
    sys.exit(2)

INDEX_NAME = os.environ.get("MONGODB_VECTOR_INDEX", "chunks_vector_index")
EMBED_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))
POLL_SECONDS = int(os.environ.get("VECTOR_INDEX_POLL_SECONDS", "10"))
CREATE_RETRY_SECONDS = int(os.environ.get("VECTOR_INDEX_CREATE_RETRY_SECONDS", "30"))

# Determine the database name from the URI path (e.g. /helia_app)
db_name = urlparse(URI.replace("mongodb+srv://", "https://")).path.lstrip("/") or "helia"
db_name = db_name.split("?", 1)[0] or "helia"

print(f"Connecting to Atlas (db={db_name}) ...")
client = MongoClient(URI, serverSelectionTimeoutMS=15000)
db = client[db_name]

# Sanity: server reachable
client.admin.command("ping")
print("  connected.")

definition = {
    "fields": [
        {"type": "vector", "path": "embedding", "numDimensions": EMBED_DIM, "similarity": "cosine"},
        {"type": "filter", "path": "documentId"},
    ]
}


def find_index() -> dict | None:
    try:
        for idx in db.chunks.list_search_indexes():
            if idx.get("name") == INDEX_NAME:
                return idx
    except Exception as err:
        print(f"  list_search_indexes error: {err}")
    return None


existing = find_index()

next_create_attempt_at = 0.0
spinner = cycle(["|", "/", "-", "\\"])
poll_count = 0
start_time = time.time()
if existing is None:
    print(f"Index '{INDEX_NAME}' not found — will create and keep retrying until available.")
else:
    print(f"Index '{INDEX_NAME}' already exists — polling state.")

# Poll until queryable (no hard timeout by default)
last_state = None
while True:
    poll_count += 1
    idx = find_index()
    now = time.time()
    elapsed = int(now - start_time)
    indicator = next(spinner)
    if idx is None:
        sys.stdout.write(
            f"\r[{indicator}] checking index status ... not visible yet (poll #{poll_count}, {elapsed}s)"
        )
        sys.stdout.flush()
        if now >= next_create_attempt_at:
            print()
            print(f"  creating search index '{INDEX_NAME}' (numDimensions={EMBED_DIM}) ...")
            model = SearchIndexModel(definition=definition, name=INDEX_NAME, type="vectorSearch")
            try:
                db.chunks.create_search_index(model=model)
                print("  create_search_index submitted.")
            except Exception as err:
                print(f"  create_search_index failed: {err}")
            next_create_attempt_at = now + max(CREATE_RETRY_SECONDS, POLL_SECONDS)
    else:
        state = idx.get("status") or idx.get("state")
        queryable = bool(idx.get("queryable"))
        sys.stdout.write(
            f"\r[{indicator}] checking index status ... state={state} queryable={queryable} (poll #{poll_count}, {elapsed}s)"
        )
        sys.stdout.flush()
        if state != last_state:
            print()
            print(f"  state={state}  queryable={queryable}")
            last_state = state
        if queryable:
            print()
            print(f"\nSUCCESS — '{INDEX_NAME}' is queryable.")
            print("Set MONGODB_VECTOR_SEARCH=true in .env to enable hybrid retrieval.")
            sys.exit(0)
    time.sleep(max(POLL_SECONDS, 1))
