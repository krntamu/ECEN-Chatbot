"""
analytics.py — durable record of questions and feedback.

Until now usage lived in two places, both lossy:
  * `_STATS` counters in main.py: in memory, reset on every restart, and
    separate per Cloud Run instance, so /admin/stats under-reported.
  * AUDIT / FEEDBACK log lines: complete, but only queryable by grepping Cloud
    Logging, and gone when log retention expires.

That made the most useful loop impossible: "show me last week's thumbs-down
answers and the questions we couldn't answer" so they can become eval cases
and content fixes. This module writes one row per question and per feedback
click to Postgres (the same database as the index).

Design rules:
  * Never on the request path. Writes run in a worker thread after the answer
    has been sent, and every failure is swallowed. Analytics must not be able
    to slow down or break a chat response.
  * Never make an outage worse. If the database breaker has recorded failures
    we skip the write instead of spending a connect timeout on a log row.
  * Privacy: no raw IP (hashed upstream), question and answer are truncated,
    and rows older than ANALYTICS_RETENTION_DAYS are deleted.

Disable with ANALYTICS_DB=0. Tables are created on first use; the same DDL is
in scripts/migrations/2026-11-15_add_chat_analytics.sql for managed setups
where the app role can't CREATE TABLE.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

ENABLED = os.getenv("ANALYTICS_DB", "1") != "0"
RETENTION_DAYS = int(os.getenv("ANALYTICS_RETENTION_DAYS", "180"))

# Outcomes where the database is the thing that just failed — don't try to
# write the record of the failure to the database that failed.
_DB_DOWN_FLAGS = {"routing_error", "retrieval_error"}

DDL = """
CREATE TABLE IF NOT EXISTS chat_log (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id     TEXT,
    ip_hash        TEXT,
    question       TEXT NOT NULL,
    resolved       TEXT,
    intent         TEXT,
    flagged        TEXT,
    sources        JSONB,
    answer_chars   INTEGER,
    answer_preview TEXT,
    latency_ms     INTEGER
);
CREATE INDEX IF NOT EXISTS chat_log_ts_idx ON chat_log (ts);
CREATE INDEX IF NOT EXISTS chat_log_flagged_idx ON chat_log (flagged) WHERE flagged IS NOT NULL;
CREATE TABLE IF NOT EXISTS chat_feedback (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id     TEXT,
    ip_hash        TEXT,
    rating         TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    question       TEXT,
    answer_preview TEXT
);
CREATE INDEX IF NOT EXISTS chat_feedback_ts_idx ON chat_feedback (ts);
"""

# One small dedicated worker: writes are queued here so they can't occupy the
# default thread pool that retrieval (asyncio.to_thread) depends on.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analytics")
_lock = threading.Lock()
_schema_ready = False
_last_prune = 0.0
_last_warn = 0.0
_PRUNE_EVERY_S = 24 * 3600


def _warn(msg: str, *args) -> None:
    """Rate-limited warning: a broken analytics table shouldn't write one log
    line per question."""
    global _last_warn
    now = time.monotonic()
    if now - _last_warn > 300:
        _last_warn = now
        log.warning(msg, *args)


def _db_looks_down() -> bool:
    try:
        import retriever
        return retriever._breaker_state()["consecutive_failures"] > 0
    except Exception:  # noqa: BLE001
        return True


def _ensure_schema(cur) -> None:
    global _schema_ready
    if _schema_ready:
        return
    cur.execute(DDL)
    _schema_ready = True


def _maybe_prune(cur) -> None:
    global _last_prune
    if RETENTION_DAYS <= 0:
        return
    now = time.monotonic()
    if _last_prune and now - _last_prune < _PRUNE_EVERY_S:
        return
    _last_prune = now
    for table in ("chat_log", "chat_feedback"):
        cur.execute(
            f"DELETE FROM {table} WHERE ts < now() - make_interval(days => %s)",
            (RETENTION_DAYS,))


def _write(sql: str, params: tuple) -> None:
    if _db_looks_down():
        return
    try:
        import retriever
        with _lock, retriever._conn() as conn, conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute(sql, params)
            _maybe_prune(cur)
            # retriever._conn() rolls back on exit (it serves read queries), so
            # the write has to be committed explicitly inside the block.
            conn.commit()
    except Exception as e:  # noqa: BLE001
        _warn("Analytics write skipped (%s: %s)", type(e).__name__, str(e)[:200])


# Writes waiting for the single worker. If the database is slow, rows are
# dropped past this point instead of piling up in memory without limit.
MAX_BACKLOG = int(os.getenv("ANALYTICS_MAX_BACKLOG", "500"))
_backlog = 0
_backlog_lock = threading.Lock()


def _run(sql: str, params: tuple) -> None:
    global _backlog
    try:
        _write(sql, params)
    finally:
        with _backlog_lock:
            _backlog -= 1


def _submit(sql: str, params: tuple) -> None:
    global _backlog
    if not ENABLED:
        return
    with _backlog_lock:
        if _backlog >= MAX_BACKLOG:
            _warn("Analytics backlog full (%d); dropping rows", _backlog)
            return
        _backlog += 1
    try:
        _executor.submit(_run, sql, params)
    except Exception:  # noqa: BLE001  (executor shut down at interpreter exit)
        with _backlog_lock:
            _backlog -= 1


def _clean(value, limit: int):
    """Make a user-supplied string safe to store whatever the caller did:
    Postgres text cannot hold NUL (psycopg2 raises on it, which would silently
    lose the row), and nothing unbounded goes into the table."""
    if value is None:
        return None
    return str(value).replace("\x00", "")[:limit]


def record_chat(*, request_id: str, ip_hash: str, question: str, resolved: str | None,
                intent: str | None, flagged: str | None, sources: list,
                answer_chars: int, answer_preview: str,
                latency_ms: int | None) -> None:
    if flagged in _DB_DOWN_FLAGS:
        return
    _submit(
        "INSERT INTO chat_log (request_id, ip_hash, question, resolved, intent, "
        "flagged, sources, answer_chars, answer_preview, latency_ms) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (_clean(request_id, 64) or None, _clean(ip_hash, 64),
         _clean(question, 1000), _clean(resolved, 1000), _clean(intent, 64),
         _clean(flagged, 64),
         json.dumps([_clean(u, 500) for u in (sources or [])][:10]),
         answer_chars, _clean(answer_preview, 2000), latency_ms))


def record_feedback(*, request_id: str, ip_hash: str, rating: str,
                    question: str, answer_preview: str) -> None:
    _submit(
        "INSERT INTO chat_feedback (request_id, ip_hash, rating, question, "
        "answer_preview) VALUES (%s, %s, %s, %s, %s)",
        (_clean(request_id, 64) or None, _clean(ip_hash, 64), rating,
         _clean(question, 1000), _clean(answer_preview, 2000)))


def summary(days: int = 7) -> dict:
    """Durable usage numbers for /admin/stats. Raises on database errors — the
    caller decides how to present that."""
    import retriever
    days = max(1, min(int(days), 365))
    out: dict = {"window_days": days}
    with retriever._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.chat_log')")
        if cur.fetchone()[0] is None:
            return {**out, "questions": 0, "note": "no analytics recorded yet"}
        win = "ts > now() - make_interval(days => %s)"
        cur.execute(f"SELECT count(*), count(DISTINCT ip_hash), "
                    f"percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
                    f"percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) "
                    f"FROM chat_log WHERE {win}", (days,))
        n, users, p50, p95 = cur.fetchone()
        out.update(questions=n, distinct_users=users,
                   latency_ms={"p50": p50 and round(p50), "p95": p95 and round(p95)})
        cur.execute(f"SELECT coalesce(intent, 'fallback'), count(*) FROM chat_log "
                    f"WHERE {win} GROUP BY 1 ORDER BY 2 DESC", (days,))
        out["intents"] = dict(cur.fetchall())
        cur.execute(f"SELECT flagged, count(*) FROM chat_log WHERE {win} "
                    f"AND flagged IS NOT NULL GROUP BY 1 ORDER BY 2 DESC", (days,))
        out["flags"] = dict(cur.fetchall())
        cur.execute(f"SELECT rating, count(*) FROM chat_feedback WHERE {win} "
                    f"GROUP BY 1", (days,))
        out["feedback"] = dict(cur.fetchall())
        # The two lists worth reading every week.
        cur.execute(f"SELECT ts, question FROM chat_log WHERE {win} "
                    f"AND flagged = 'no_reliable_context' ORDER BY ts DESC LIMIT 50",
                    (days,))
        out["unanswered"] = [{"ts": t.isoformat(), "question": q}
                             for t, q in cur.fetchall()]
        cur.execute(f"SELECT ts, question, answer_preview FROM chat_feedback "
                    f"WHERE {win} AND rating = 'down' ORDER BY ts DESC LIMIT 50",
                    (days,))
        out["thumbs_down"] = [{"ts": t.isoformat(), "question": q, "answer_preview": a}
                              for t, q, a in cur.fetchall()]
    return out
