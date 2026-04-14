"""
main.py — EasyPay v3.0 Application Entry Point

Responsibilities:
  - FastAPI app initialisation with CORS + SlowAPI rate limiting
  - Cloudinary configuration
  - Global exception handlers → standardised error_response envelopes
  - GET /health endpoint
  - Lifespan: seeds admin superuser on startup (Critical Point 17)
    - Checks ADMIN_PHONE existence first to prevent duplicates
    - Password is Bcrypt-hashed (cost=12) — never plaintext (Rule 1)
  - B24: background delivery simulation task (every 30 minutes)
"""
import asyncio
import logging
import secrets
import time
from contextvars import ContextVar
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import cloudinary
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from passlib.context import CryptContext
from slowapi.errors import RateLimitExceeded
from app.core.limiter import limiter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import EasyPayException
from app.core.logging_config import configure_logging, RequestIdFilter
from app.schemas.base import error_response, success_response

# ContextVar so request_id is available anywhere in the call stack
_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class _SafeRequestIdFormatter(logging.Formatter):
    """Formatter that always has request_id — falls back to '-' if not injected."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = "-"  # type: ignore[attr-defined]
        return super().format(record)


_handler = logging.StreamHandler()
_handler.setFormatter(
    _SafeRequestIdFormatter(
        "%(asctime)s  %(levelname)-8s  [%(request_id)s]  %(name)s — %(message)s"
    )
)
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)
configure_logging(_request_id_var)  # B21/B22 — scrubber + request_id injection
logger = logging.getLogger("easypay")

# ─────────────────────────────────────────────────────────────────────────────
# Password context — Bcrypt cost=12 (Rule 1)
# ─────────────────────────────────────────────────────────────────────────────
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# ─────────────────────────────────────────────────────────────────────────────
# Cloudinary configuration
# ─────────────────────────────────────────────────────────────────────────────
cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# Admin superuser seeding  (Critical Point 17)
# ─────────────────────────────────────────────────────────────────────────────
async def _seed_admin(db: AsyncSession) -> None:
    """
    Create the admin superuser from .env if it does not already exist.

    Rules enforced:
      - Check by ADMIN_PHONE before inserting (prevents duplicates).
      - Password is Bcrypt-hashed (bcrypt__rounds=12).
      - Never created via /auth/register — seeded here only.
      - Admin wallet initialised with PKR 0.00 balance.
    """
    if not settings.ADMIN_PHONE or not settings.ADMIN_PASSWORD or not settings.ADMIN_EMAIL:
        logger.warning(
            "Startup: ADMIN_PHONE / ADMIN_PASSWORD / ADMIN_EMAIL not configured — "
            "skipping admin seed."
        )
        return

    # Lazy import to avoid circular dependencies at module level
    from app.models.database import User, Wallet

    result = await db.execute(
        select(User).where(User.phone_number == settings.ADMIN_PHONE)
    )
    if result.scalar_one_or_none() is not None:
        logger.info("Startup: admin account already exists — seed skipped.")
        return

    admin = User(
        phone_number=settings.ADMIN_PHONE,
        email=settings.ADMIN_EMAIL,
        full_name="EasyPay Admin",
        password_hash=_pwd_context.hash(settings.ADMIN_PASSWORD),
        is_verified=True,
        is_active=True,
        is_superuser=True,
        verification_tier=4,
    )
    db.add(admin)
    await db.flush()  # populate admin.id before referencing it in Wallet

    wallet = Wallet(user_id=admin.id)
    db.add(wallet)
    await db.commit()
    logger.info("Startup: admin superuser created successfully (phone=%s).", settings.ADMIN_PHONE,settings.ADMIN_PASSWORD)


# ─────────────────────────────────────────────────────────────────────────────
# B21 — Startup configuration validation
# Aborts if any critical secret is missing or invalid (fail-fast pattern).
# ─────────────────────────────────────────────────────────────────────────────
def _validate_config() -> None:
    """Validate all required settings on startup. Raises RuntimeError on failure."""
    errors: list[str] = []

    # SECRET_KEY must be at least 32 chars
    if len(settings.SECRET_KEY) < 32:
        errors.append("SECRET_KEY must be at least 32 characters long.")

    # ENCRYPTION_KEY must be a valid Fernet key (32 bytes, base64-encoded)
    if not settings.ENCRYPTION_KEY:
        errors.append("ENCRYPTION_KEY is not set.")
    else:
        try:
            Fernet(settings.ENCRYPTION_KEY.encode())
        except (ValueError, InvalidToken, Exception):
            errors.append("ENCRYPTION_KEY is not a valid Fernet key.")

    # ADMIN_SECRET_HEADER must be set and at least 16 chars
    if not settings.ADMIN_SECRET_HEADER or len(settings.ADMIN_SECRET_HEADER) < 16:
        errors.append("ADMIN_SECRET_HEADER must be set and at least 16 characters long.")

    # DEEPSEEK_API_KEY must be set
    if not settings.DEEPSEEK_API_KEY:
        errors.append("DEEPSEEK_API_KEY is not set.")

    if errors:
        for err in errors:
            logger.critical("CONFIG ERROR: %s", err)
        raise RuntimeError(
            "Application startup aborted due to missing/invalid configuration:\n"
            + "\n".join(f"  • {e}" for e in errors)
        )


async def _verify_db_reachable(db: AsyncSession) -> bool:
    """Ping the database with SELECT 1. Returns True on success."""
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Startup: database unreachable — %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Application lifespan
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialise the database engine and seed admin on startup; dispose on shutdown."""
    # B21 — validate all required config before doing anything else
    _validate_config()

    from app.db import base as db_module  # local import keeps circular deps clean

    db_reachable = False
    if settings.async_database_url:
        db_module.init_engine(
            settings.async_database_url,
            echo=(settings.ENVIRONMENT == "development"),
        )
        try:
            async with db_module._session_factory() as db:  # type: ignore[misc]
                db_reachable = await _verify_db_reachable(db)
                if db_reachable:
                    await _seed_admin(db)
        except Exception as exc:
            logger.warning("Startup: admin seeding skipped — %s", exc)
    else:
        logger.warning(
            "Startup: DATABASE_URL is not configured. "
            "Database features will be unavailable."
        )

    # B21 — Startup summary
    logger.info(
        "Startup summary | env=%s | db=%s | cloudinary=%s | deepseek=%s | fcm=%s",
        settings.ENVIRONMENT,
        "✓ reachable" if db_reachable else "✗ unreachable",
        "✓ configured" if settings.CLOUDINARY_API_KEY else "✗ missing",
        "✓ configured" if settings.DEEPSEEK_API_KEY else "✗ missing",
        "✓ configured" if settings.FCM_PROJECT_ID else "✗ missing",
    )

    # B24 — Background delivery simulation (every 30 minutes)
    async def _run_delivery_simulation() -> None:
        """Advance physical card delivery statuses every 30 minutes."""
        from app.db.base import _session_factory  # local import
        from app.services import card_service as _card_svc

        while True:
            await asyncio.sleep(30 * 60)
            try:
                async with _session_factory() as db:  # type: ignore[misc]
                    await _card_svc.simulate_delivery_progress(db)
            except Exception as _exc:
                logger.warning("Delivery simulation error: %s", _exc)

    _delivery_task = asyncio.create_task(_run_delivery_simulation())
    logger.info("Startup: card delivery simulation task started.")

    yield  # ── application runs ──────────────────────────────────────────────

    _delivery_task.cancel()
    if db_module.engine:
        await db_module.engine.dispose()
        logger.info("Shutdown: database engine disposed.")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI application
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="EasyPay API",
    version="3.0.0",
    description="EasyPay v3.0 — Pakistani fintech digital wallet REST API",
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)

# Attach limiter state BEFORE adding middleware
app.state.limiter = limiter

# ─── CORS ────────────────────────────────────────────────────────────────────
_origins = (
    ["*"]
    if settings.ALLOWED_ORIGINS.strip() == "*"
    else [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
)
_allow_headers = [
    "Authorization",
    "Content-Type",
    "X-Admin-Key",
    "X-Request-ID",
    "Bypass-Tunnel-Reminder",
    "Accept",
    "Origin",
]
if ["*"] == _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=_allow_headers,
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=_allow_headers,
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Middleware — X-Request-ID + X-Process-Time
# Generates a unique request identifier and measures handler latency.
# Both values are injected as response headers for observability.
# ─────────────────────────────────────────────────────────────────────────────
@app.middleware("http")
async def _request_context_middleware(request: Request, call_next):
    request_id = f"req_{secrets.token_hex(4)}"
    _request_id_var.set(request_id)      # B22 — visible to all loggers this request
    start = time.perf_counter()

    # ── Log incoming request ──────────────────────────────────────────────────
    qs = f"?{request.url.query}" if request.url.query else ""
    logger.info("→ %s %s%s  [%s]", request.method, request.url.path, qs, request_id)

    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # ── Log outgoing response ─────────────────────────────────────────────────
    status = response.status_code
    level = logging.WARNING if status >= 400 else logging.INFO
    logger.log(level, "← %s %s  %d  %.0fms  [%s]",
               request.method, request.url.path, status, elapsed_ms, request_id)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{elapsed_ms:.2f}ms"
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Middleware — Security Headers
# Injects OWASP-recommended headers on every response.
# ─────────────────────────────────────────────────────────────────────────────
@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Middleware — X-Admin-Key guard for all /admin/* routes  (B21)
# Defence-in-depth: even if a route forgets get_current_admin, this rejects it.
# ─────────────────────────────────────────────────────────────────────────────
@app.middleware("http")
async def _admin_key_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    if request.url.path.startswith("/api/v1/admin"):
        provided = request.headers.get("X-Admin-Key", "")
        if not secrets.compare_digest(provided, settings.ADMIN_SECRET_HEADER):
            return JSONResponse(
                status_code=403,
                content=error_response(
                    "AUTH_INSUFFICIENT_PERMISSION",
                    "Valid X-Admin-Key header is required for admin routes.",
                ),
            )
    return await call_next(request)


# ─────────────────────────────────────────────────────────────────────────────
# Global exception handlers — all errors return the v3.0 envelope (Rule 10)
# ─────────────────────────────────────────────────────────────────────────────
@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=error_response(
            "RATE_LIMIT_EXCEEDED",
            "Too many requests. Please slow down and try again.",
        ),
    )


@app.exception_handler(EasyPayException)
async def _easypay_exception_handler(
    request: Request, exc: EasyPayException
) -> JSONResponse:
    """Convert domain exceptions to the v3.0 error envelope (Rule 10)."""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(exc.error_code, exc.detail),
    )


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    # If detail is already our structured error envelope (raised by dependencies
    # or route handlers via HTTPException(detail=_err(...))), pass it through
    # unchanged to avoid double-wrapping.
    if isinstance(exc.detail, dict) and exc.detail.get("success") is False:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    # Map common HTTP status codes to standardised error codes
    _code_map: dict[int, str] = {
        400: "BAD_REQUEST",
        401: "AUTH_TOKEN_MISSING",
        403: "AUTH_INSUFFICIENT_PERMISSION",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_SERVER_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }
    code = _code_map.get(exc.status_code, f"HTTP_{exc.status_code}")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(code, str(exc.detail)),
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # exc.errors() may contain non-serializable objects (e.g. ValueError);
    # convert each error's 'ctx' values to strings to ensure JSON safety.
    safe_errors = [
        {
            **{k: (str(v) if k == "ctx" and not isinstance(v, (dict, list, str, int, float, bool, type(None))) else v)
               for k, v in err.items()}
        }
        for err in exc.errors()
    ]
    # Sanitize nested ctx dicts too
    sanitized = []
    for err in safe_errors:
        if isinstance(err.get("ctx"), dict):
            err["ctx"] = {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                          for k, v in err["ctx"].items()}
        sanitized.append(err)
    return JSONResponse(
        status_code=422,
        content=error_response(
            "VALIDATION_ERROR",
            "Request validation failed. Check the 'details' field for per-field errors.",
            {"errors": sanitized},
        ),
    )


@app.exception_handler(Exception)
async def _general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=error_response(
            "INTERNAL_SERVER_ERROR",
            "An unexpected error occurred. Our team has been notified.",
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Health check — used by Railway healthcheckPath and UptimeRobot (Point 16)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    return success_response(
        "EasyPay API is running",
        {"status": "healthy", "version": "3.0", "environment": settings.ENVIRONMENT},
    )


# ─────────────────────────────────────────────────────────────────────────────
# B22 — Detailed health check: tests DB + DeepSeek + Cloudinary + FCM
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health/detailed", tags=["Health"])
async def health_detailed() -> dict:
    """
    Probes each external dependency and reports individual status.
    Returns 200 only if ALL services are reachable; 503 otherwise.
    """
    import httpx
    from app.db import base as db_module

    results: dict[str, str] = {}
    all_ok = True

    # ── Database ──────────────────────────────────────────────────────────────
    try:
        async with db_module._session_factory() as db:  # type: ignore[misc]
            await db.execute(text("SELECT 1"))
        results["database"] = "ok"
    except Exception as exc:
        results["database"] = f"error: {exc}"
        all_ok = False

    # ── DeepSeek API ─────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.DEEPSEEK_BASE_URL}/v1/models",
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            )
        results["deepseek"] = "ok" if resp.status_code < 500 else f"http_{resp.status_code}"
    except Exception as exc:
        results["deepseek"] = f"error: {exc}"
        all_ok = False

    # ── Cloudinary ────────────────────────────────────────────────────────────
    try:
        import cloudinary.api
        ping = cloudinary.api.ping()
        results["cloudinary"] = "ok" if ping.get("status") == "ok" else "error"
    except Exception as exc:
        results["cloudinary"] = f"error: {exc}"
        all_ok = False

    # ── FCM (Firebase) ────────────────────────────────────────────────────────
    # We just verify the service-account JSON is loadable — actual push
    # would require a device token which we don't have here.
    try:
        account = settings.fcm_service_account
        results["fcm"] = "ok" if account.get("project_id") else "missing project_id"
    except Exception as exc:
        results["fcm"] = f"error: {exc}"
        all_ok = False

    status_code = 200 if all_ok else 503
    body = success_response("Health check complete", results) if all_ok else error_response(
        "SERVICE_UNAVAILABLE", "One or more services are unreachable.", results
    )
    return JSONResponse(status_code=status_code, content=body)


# ─────────────────────────────────────────────────────────────────────────────
# Router registration — all v1 routes via the combined api_router
# ─────────────────────────────────────────────────────────────────────────────
from app.api.v1.router import api_router  # noqa: E402 — must follow app creation

app.include_router(api_router)


# ─────────────────────────────────────────────────────────────────────────────
# Admin dashboard — served from the same origin to avoid CORS issues
# ─────────────────────────────────────────────────────────────────────────────
import os as _os  # noqa: E402

@app.get("/admin", include_in_schema=False)
async def serve_admin_dashboard():
    path = _os.path.join(_os.path.dirname(__file__), "admin_dashboard.html")
    return FileResponse(path, media_type="text/html")
