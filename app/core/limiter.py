"""
app/core/limiter.py — EasyPay v3.0 Shared SlowAPI Rate Limiter

Defined here (not in main.py) so route modules can safely import it
without creating circular dependencies.

Usage in routes:
    from app.core.limiter import limiter
    from fastapi import Request

    @router.post("/some-path")
    @limiter.limit("3/hour")
    async def my_route(request: Request, ...):
        ...

The limiter instance is also referenced in main.py:
    from app.core.limiter import limiter
    app.state.limiter = limiter
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Single shared limiter instance — default 200 req/min per IP
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
