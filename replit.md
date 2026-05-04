# Workspace

## Overview

pnpm workspace monorepo with a mixed-language stack: the React frontend and OpenAPI-driven shared libs are TypeScript; the API server (`artifacts/api-server`) is Python (FastAPI).

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24 (frontend + shared libs)
- **Package manager**: pnpm (JS), uv / pip (Python)
- **TypeScript version**: 5.9
- **API framework**: FastAPI (Python 3.12) running under uvicorn. Implements the same OpenAPI contract as before at `/api/*`. Auth via Clerk (`clerk-backend-api`); Mongo via `motor`.
- **Database**: MongoDB Atlas (single cluster used for app data and retrieval). `MONGODB_URI` env var. Numeric IDs are minted via a `counters` collection (`next_id(name)` in `app/db.py`). Retrieval uses a Mongo `$text` index on `chunks.content` (no embeddings — Replit OpenAI integration does not expose an embeddings endpoint).
- **Validation**: Zod (`zod/v4`) on the client; Pydantic v2 on the server
- **API codegen**: Orval (from OpenAPI spec) — frontend hooks unchanged
- **LLM**: PwC AI gateway (Gemini 2.5 Flash via OpenAI-compatible chat completions)

## Key Commands

- `pnpm run typecheck` — full typecheck across all JS/TS packages
- `pnpm run build` — typecheck + build all JS/TS packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from OpenAPI spec
- API server runs via the workflow: `cd artifacts/api-server && uvicorn app.server:app --host 0.0.0.0 --port 8080`

## API server layout (`artifacts/api-server`)

```
app/
  server.py        # FastAPI factory; mounts all routers under /api
  auth.py          # Clerk authenticate_request + role logic (ADMIN_EMAILS or first-user bootstrap)
  clerk_proxy.py   # /api/__clerk/* → frontend-api.clerk.dev (production only)
  db.py            # motor client + index init + next_id counter
  pii.py           # PII detection + masking
  text.py          # tokenize / chunk / jaccard
  pwc_ai.py        # PwC AI gateway client
  schemas.py       # Pydantic request bodies
  serialize.py     # response shaping
  routes/          # health, me, admin, documents, extract, chat, messages, tickets
```

## Required environment variables

- `MONGODB_URI` — MongoDB connection string
- `CLERK_SECRET_KEY` — Clerk backend secret
- `VITE_CLERK_PUBLISHABLE_KEY` (or `CLERK_PUBLISHABLE_KEY`) — Clerk publishable key
- `PWC_AI_API_KEY`, `PWC_TOKEN` — PwC AI gateway credentials
- `ADMIN_EMAILS` (optional, comma-separated) — admin allow-list. If unset, the oldest Clerk user is the admin.

See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details.
