"""
Application entrypoint. Run with:
    uvicorn app.main:app --reload          (local dev)
    gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4   (production)
"""
import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.api.v1.endpoints import health
from app.api.v1.router import api_router
from app.core.cache import close_cache_client
from app.core.config import settings
from app.middlewares.rate_limit import limiter

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app")

if settings.SENTRY_DSN:
    sentry_sdk.init(dsn=settings.SENTRY_DSN, environment=settings.ENVIRONMENT, traces_sample_rate=0.1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up (environment=%s)", settings.ENVIRONMENT)
    yield
    logger.info("Shutting down -- closing cache connections")
    await close_cache_client()


app = FastAPI(
    title="InfoSenior Care API",
    version="1.0.0",
    lifespan=lifespan,
    # Hide interactive docs in production -- avoids exposing the full API
    # surface/schema publicly; enable explicitly if you want a staging
    # environment with docs on.
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Rate limit exceeded -- please slow down and try again shortly."},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Invalid request data", "errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak internal error details (stack traces, DB errors, etc) to
    # clients -- log the full detail server-side (Sentry captures it too),
    # return a generic message.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred. Please try again."},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Health checks live at the root (no /api/v1 prefix) -- conventional path
# for load balancer / orchestrator probes.
app.include_router(health.router)
app.include_router(api_router, prefix="/api/v1")
