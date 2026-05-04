"""FastAPI application factory.

Mounts all routes under /api so the shared workspace proxy (paths=['/api'])
forwards traffic without rewriting the path — same contract as the previous
Express server.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.clerk_proxy import router as clerk_proxy_router
from app.routes.admin import router as admin_router
from app.routes.chat import router as chat_router
from app.routes.documents import router as documents_router
from app.routes.extract import router as extract_router
from app.routes.health import router as health_router
from app.routes.me import router as me_router
from app.routes.messages import router as messages_router
from app.routes.tickets import router as tickets_router

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
log = logging.getLogger("api-server")


def create_app() -> FastAPI:
    app = FastAPI(title="Api", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

    # Match the previous Express behaviour: `cors({ origin: true, credentials: true })`
    # — reflect the request Origin and allow cookies (needed for Clerk session cookies).
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if not os.environ.get("CLERK_SECRET_KEY"):
        log.warning(
            "Clerk environment variables are missing; auth-protected routes will stay "
            "unavailable in local startup."
        )

    # Clerk Frontend API proxy (production only). Mounted at /api/__clerk/*.
    app.include_router(clerk_proxy_router)

    # All API routes live under /api — the workspace proxy forwards paths=['/api']
    # without rewriting, so handlers must include the prefix.
    api_routers = [
        health_router,
        me_router,
        admin_router,
        extract_router,
        documents_router,
        chat_router,
        messages_router,
        tickets_router,
    ]
    for r in api_routers:
        app.include_router(r, prefix="/api")

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail or "Error", "status": exc.status_code},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid request body", "status": 400, "details": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def unhandled_exc_handler(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": str(exc) or "Internal server error", "status": 500},
        )

    return app


app = create_app()
