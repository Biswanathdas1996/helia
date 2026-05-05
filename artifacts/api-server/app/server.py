"""FastAPI application factory."""
from __future__ import annotations

import logging
import os
import time

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import metrics
from app.routes.auth import router as auth_router
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

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        started = time.time()
        path_template = (request.scope.get("route").path  # type: ignore[union-attr]
                         if request.scope.get("route") else request.url.path)
        try:
            response: Response = await call_next(request)
            metrics.HTTP_REQUESTS.labels(
                method=request.method, path=path_template, status=str(response.status_code)
            ).inc()
            metrics.HTTP_LATENCY.labels(method=request.method, path=path_template).observe(
                time.time() - started
            )
            return response
        except Exception:
            metrics.HTTP_REQUESTS.labels(
                method=request.method, path=path_template, status="500"
            ).inc()
            raise

    api_routers = [
        health_router,
        auth_router,
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

    @app.get("/api/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        body, content_type = metrics.render()
        return PlainTextResponse(body, media_type=content_type)

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
