# Workspace

## Overview

pnpm workspace monorepo with a mixed-language stack: the React frontend and OpenAPI-driven shared libs are TypeScript; the API server (`artifacts/api-server`) is Python (FastAPI).

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24 (frontend + shared libs)
- **Package manager**: pnpm (JS), uv / pip (Python)
- **TypeScript version**: 5.9
- **API framework**: FastAPI (Python 3.12) under uvicorn. Auth is custom JWT + bcrypt; Mongo via `motor`. The OpenAPI contract under `/api/*` is unchanged for existing endpoints.
- **Database**: MongoDB Atlas (single cluster used for app data and retrieval). `MONGODB_URI` env var. Numeric IDs are minted via a `counters` collection (`next_id(name)` in `app/db.py`). Retrieval uses Mongo `$text` (BM25-ish) AND — when configured — Atlas Vector Search over `chunks.embedding`.
- **Validation**: Zod (`zod/v4`) on the client; Pydantic v2 on the server
- **API codegen**: Orval (from OpenAPI spec) — frontend hooks unchanged for the existing endpoints. `/api/chat/conversations/{id}/messages/stream` and `/api/tickets/active-summary` are not yet covered by the spec; they are exposed for future client work.
- **LLM**: PwC GenAI gateway. Chat = `vertex_ai.gemini-2.5-flash-image-image`, embeddings = `vertex_ai.gemini-embedding`. The gateway is OpenAI-compatible at the wire level (`/chat/completions`, `/embeddings`).
- **Vector search**: Atlas Vector Search over 768-dim Gemini embeddings (`MONGODB_VECTOR_SEARCH=true` after creating the index — see `infra/atlas-vector-index.json`). Falls back gracefully to BM25-only when not configured.
- **Cache**: Redis when `REDIS_URL` is set; in-process LRU otherwise. Used for retrieval and rate-limiting.
- **Observability**: `/api/metrics` (Prometheus), structured JSON-ish stdout logs, Mongo `audit_logs` collection.
- **Tickets**: created locally, then mirrored to Zoho Desk when `ZOHO_*` env vars are set. Open-ticket status is auto-refreshed by `/api/tickets/active-summary`.

## Key Commands

- `pnpm run typecheck` — full typecheck across all JS/TS packages
- `pnpm run build` — typecheck + build all JS/TS packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from OpenAPI spec
- API server runs via the workflow: `cd artifacts/api-server && uvicorn app.server:app --host 0.0.0.0 --port 8080`
- Full local stack: `docker compose up --build` (api at 8080, web at 8081, redis sidecar)

## Retrieval pipeline (`app/retrieval.py`)

1. Query rewrite + intent detection (LLM, JSON-mode)
2. Hybrid search — BM25 (`$text`) ∪ Atlas Vector Search
3. Jaccard dedup of redundant chunks
4. Reciprocal-rank fusion + LLM rerank of the top-N
5. Top-K (default 5) returned with citation metadata

Disable individual stages with `DISABLE_QUERY_REWRITE=true` / `DISABLE_RERANK=true` to trade quality for latency.

## Ingestion pipeline (`app/routes/documents.py`)

1. PII detect + mask (`app/pii.py`)
2. Chunk (token-windowed, ~180 words with 30-word overlap)
3. Embedding generation (when LLM provider supports it)
4. Cross-document dedup (cosine when embeddings exist, else Jaccard)
5. Persist to `documents` + `chunks` (with `embedding` field)
6. Detailed `ingestionReport` saved on the document
7. Admin review → approve/reject before retrieval picks up the chunks

## API server layout (`artifacts/api-server`)

```
app/
  server.py        # FastAPI factory; metrics middleware; /api/metrics
  auth.py          # JWT cookie auth + role logic (admin/user)
  db.py            # motor client + index init + next_id counter
  pii.py           # PII detection + masking
  text.py          # tokenize / chunk / jaccard
  llm.py           # thin wrapper exposing chat / chat_stream / embed (PwC)
  pwc_ai.py        # PwC GenAI gateway client (chat + embeddings + extract)
  embeddings.py    # batched embed_batch + cosine + Atlas $vectorSearch
  retrieval.py     # hybrid RAG pipeline (rewrite→hybrid→dedup→rerank→assemble)
  cache.py         # Redis-or-in-memory cache
  rate_limit.py    # sliding-window rate limiter
  zoho.py          # Zoho Desk OAuth + ticket mirror
  audit.py         # audit_logs writes
  metrics.py       # Prometheus counters/histograms
  schemas.py       # Pydantic request bodies
  serialize.py     # response shaping
  routes/          # health, auth, me, admin, documents, extract, chat, messages, tickets
```

## Required environment variables

Minimum:

- `MONGODB_URI`
- `SESSION_SECRET`
- `PWC_AI_API_KEY` + `PWC_TOKEN`

Optional (see `.env.example` for the full list):

- `REDIS_URL` — production cache + cross-replica rate limiting
- `MONGODB_VECTOR_SEARCH=true` — enable Atlas Vector Search retrieval leg
- `ZOHO_*` — mirror tickets to Zoho Desk

## Bootstrap behaviour

The first user to register is auto-promoted to `admin`. Subsequent registrations get `user`.

## What's intentionally not built yet

- A frontend client for the streaming `/messages/stream` endpoint (the existing non-streaming `POST /messages` continues to work; orval regen is needed before hooking the SSE flow into React).
- An automated test suite (CI scaffolds `pytest` but the `tests/` directory is empty).
- A managed Prometheus / Grafana stack — the app exposes `/api/metrics`, but scraping infra is environment-specific.

See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details.
