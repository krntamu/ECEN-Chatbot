"""
obs.py — logging and request correlation.

Two things the service was missing in production:

1. Cloud Logging only understands severity when a log line is JSON with a
   `severity` field. With plain-text lines every entry lands as DEFAULT, so
   "show me errors" filters and error-rate alerts see nothing. On Cloud Run
   (K_SERVICE is set) or with LOG_FORMAT=json we emit one JSON object per line.
   Locally the old human-readable format is kept.

2. A request ID. One question produces a dozen log lines (routing, timings,
   retrieved chunks, AUDIT). Without a shared ID two concurrent users' lines
   interleave and can't be separated. The ID is taken from X-Request-ID when
   the caller sends one, else generated, and is echoed back in the response
   header so a user-reported problem can be matched to its logs.

Lines such as `AUDIT {...}` / `FEEDBACK {...}` keep their text form in
`message` (existing greps still work) and their payload is ALSO lifted into a
structured `data` field so it can be queried in Logs Explorer.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import time
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="")

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_EVENT_RE = re.compile(r"^(AUDIT|FEEDBACK) (\{.*\})$", re.DOTALL)


def current_request_id() -> str:
    return request_id_var.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, in the shape Cloud Logging parses natively."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        entry: dict = {
            "severity": record.levelname,
            "message": message,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                    + f".{int(record.msecs):03d}Z",
            "logger": record.name,
        }
        rid = getattr(record, "request_id", "") or request_id_var.get()
        if rid and rid != "-":
            entry["request_id"] = rid
        m = _EVENT_RE.match(message)
        if m:
            try:
                entry["event"] = m.group(1).lower()
                entry["data"] = json.loads(m.group(2))
            except ValueError:
                pass
        if record.exc_info:
            entry["message"] = message + "\n" + self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def use_json_logs() -> bool:
    fmt = os.getenv("LOG_FORMAT", "").lower()
    if fmt:
        return fmt == "json"
    return bool(os.getenv("K_SERVICE"))  # set by Cloud Run


def setup_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    if use_json_logs():
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(request_id)s] %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def _pick_request_id(headers: list) -> str:
    for name, value in headers:
        if name == b"x-request-id":
            candidate = value.decode("latin-1", "replace").strip()
            # Only accept a well-formed ID: this value is written to logs and
            # echoed in a response header, so it must not carry newlines or
            # arbitrary attacker-chosen text.
            if _SAFE_ID_RE.match(candidate):
                return candidate
    return uuid.uuid4().hex[:16]


class RequestIdMiddleware:
    """Pure ASGI middleware (not BaseHTTPMiddleware, which wraps the response
    body and has a history of interfering with long-lived SSE streams)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _pick_request_id(scope.get("headers") or [])
        token = request_id_var.set(rid)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", rid.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)
