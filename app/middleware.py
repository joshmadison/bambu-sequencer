"""Guards for public deployments: rate limits, concurrency caps, security headers.

The app has no accounts, so the only way to keep a public instance usable is to
limit how much work one client (and the whole instance) can trigger. Everything
here is in-process: run a single uvicorn worker, otherwise each worker keeps its
own counters.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


# Requests per IP inside the window. 0 disables the limit.
RATE_LIMIT_REQUESTS = _env_int("RATE_LIMIT_REQUESTS", 120)
RATE_LIMIT_WINDOW_S = _env_int("RATE_LIMIT_WINDOW_S", 600)

# Simultaneous POST /api/* requests. 0 disables the cap.
MAX_CONCURRENT_REQUESTS = _env_int("MAX_CONCURRENT_REQUESTS", 4)

_MAX_TRACKED_IPS = 10_000

_lock = threading.Lock()
_hits: dict[str, deque[float]] = {}
_active = 0


def reset_limits() -> None:
    """Drop all counters. Used by tests and after a configuration reload."""
    global _active
    with _lock:
        _hits.clear()
        _active = 0


def _check_rate(ip: str, now: float) -> tuple[bool, int]:
    if RATE_LIMIT_REQUESTS <= 0 or RATE_LIMIT_WINDOW_S <= 0:
        return True, 0

    with _lock:
        hits = _hits.get(ip)
        if hits is None:
            hits = deque()
            _hits[ip] = hits
        cutoff = now - RATE_LIMIT_WINDOW_S
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_REQUESTS:
            retry_after = max(1, int(hits[0] + RATE_LIMIT_WINDOW_S - now) + 1)
            return False, retry_after
        hits.append(now)
        if len(_hits) > _MAX_TRACKED_IPS:
            _prune_locked(now)
    return True, 0


def _prune_locked(now: float) -> None:
    cutoff = now - RATE_LIMIT_WINDOW_S
    stale = [ip for ip, hits in _hits.items() if not hits or hits[-1] <= cutoff]
    for ip in stale:
        del _hits[ip]
    while len(_hits) > _MAX_TRACKED_IPS:
        _hits.pop(next(iter(_hits)))


def _acquire_slot() -> bool:
    global _active
    with _lock:
        if MAX_CONCURRENT_REQUESTS > 0 and _active >= MAX_CONCURRENT_REQUESTS:
            return False
        _active += 1
        return True


def _release_slot() -> None:
    global _active
    with _lock:
        if _active > 0:
            _active -= 1


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _error(status: int, detail: str, retry_after: int) -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=status,
        headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
    )


# The SPA uses inline scripts, inline styles and inline event handlers, so the
# CSP cannot be strict yet. Revisit this when the frontend is split into files.
CSP = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "form-action 'self'"
)


def _add_security_headers(request: Request, response: Response) -> Response:
    headers = response.headers
    if "content-security-policy" not in headers:
        headers["Content-Security-Policy"] = CSP
    if "x-content-type-options" not in headers:
        headers["X-Content-Type-Options"] = "nosniff"
    if "referrer-policy" not in headers:
        headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if "x-frame-options" not in headers:
        headers["X-Frame-Options"] = "DENY"
    if "permissions-policy" not in headers:
        headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if request.url.scheme == "https" and "strict-transport-security" not in headers:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


class PublicGuardsMiddleware(BaseHTTPMiddleware):
    """Rate-limit and cap upload/build requests, then add security headers."""

    async def dispatch(self, request: Request, call_next):
        response = await self._dispatch(request, call_next)
        return _add_security_headers(request, response)

    async def _dispatch(self, request: Request, call_next):
        # GETs are cheap. The expensive work is in the POST endpoints that read
        # and parse uploads, so only those consume a rate-limit slot or build slot.
        guarded = request.method == "POST" and request.url.path.startswith("/api/")
        if not guarded:
            return await call_next(request)

        allowed, retry_after = _check_rate(_client_ip(request), time.monotonic())
        if not allowed:
            return _error(
                429,
                "Too many requests - please wait a moment and try again.",
                retry_after,
            )
        if not _acquire_slot():
            return _error(
                503,
                "The server is busy building another file - please retry shortly.",
                5,
            )
        try:
            return await call_next(request)
        finally:
            _release_slot()
