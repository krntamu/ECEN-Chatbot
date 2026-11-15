"""
Logging, request IDs, and the durable analytics log.

The properties that matter:
  * Cloud Logging gets a `severity` it can filter and alert on.
  * A caller can't inject arbitrary text into logs via X-Request-ID.
  * Analytics can never break or slow a chat response: every failure is
    swallowed, and it stays out of the way when the database is struggling.
"""
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_db_pool_resilience import _install_stubs  # noqa: E402

_install_stubs()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import analytics  # noqa: E402
import obs  # noqa: E402


def _record(msg, level=logging.INFO):
    return logging.LogRecord("t", level, __file__, 1, msg, None, None)


def test_json_formatter_emits_severity_and_request_id():
    token = obs.request_id_var.set("abc123")
    try:
        out = json.loads(obs.JsonFormatter().format(_record("hello", logging.ERROR)))
    finally:
        obs.request_id_var.reset(token)
    assert out["severity"] == "ERROR"
    assert out["message"] == "hello"
    assert out["request_id"] == "abc123"


def test_json_formatter_lifts_audit_payload_but_keeps_it_greppable():
    payload = {"question": "who teaches ECEN 314?", "intent": "course"}
    out = json.loads(obs.JsonFormatter().format(_record("AUDIT " + json.dumps(payload))))
    assert out["event"] == "audit"
    assert out["data"] == payload
    assert out["message"].startswith("AUDIT ")


def test_json_formatter_survives_malformed_event_payload():
    out = json.loads(obs.JsonFormatter().format(_record("AUDIT {not json}")))
    assert "data" not in out


@pytest.mark.parametrize("header,accepted", [
    (b"req-42_a.b", True),
    (b"x" * 64, True),
    (b"x" * 65, False),
    (b"evil\r\nSet-Cookie: a=b", False),
    (b"has space", False),
    (b"", False),
])
def test_request_id_header_is_only_trusted_when_well_formed(header, accepted):
    rid = obs._pick_request_id([(b"x-request-id", header)])
    assert (rid == header.decode()) is accepted
    assert obs._SAFE_ID_RE.match(rid)


def test_json_logs_default_on_for_cloud_run_only(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert not obs.use_json_logs()
    monkeypatch.setenv("K_SERVICE", "ecen-chatbot")
    assert obs.use_json_logs()
    monkeypatch.setenv("LOG_FORMAT", "text")
    assert not obs.use_json_logs()


class _Cursor:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        if self.fail:
            raise RuntimeError("permission denied for schema public")
        self.log.append((" ".join(sql.split())[:40], params))


class _Conn:
    def __init__(self, fail=False):
        self.executed, self.committed, self.fail = [], False, fail
    def cursor(self): return _Cursor(self.executed, self.fail)
    def commit(self): self.committed = True


@pytest.fixture
def fake_db(monkeypatch):
    """Route analytics at an in-memory connection and run writes inline."""
    import contextlib
    import retriever
    conn = _Conn()

    @contextlib.contextmanager
    def _conn():
        yield conn

    monkeypatch.setattr(retriever, "_conn", _conn)
    monkeypatch.setattr(retriever, "_breaker_state",
                        lambda: {"open": False, "consecutive_failures": 0,
                                 "cooldown_remaining_s": 0})
    monkeypatch.setattr(analytics, "_submit",
                        lambda sql, params: analytics._write(sql, params))
    monkeypatch.setattr(analytics, "_schema_ready", False)
    monkeypatch.setattr(analytics, "_last_prune", 0.0)
    monkeypatch.setattr(analytics, "ENABLED", True)
    return conn


def _chat(**over):
    row = dict(request_id="r1", ip_hash="h", question="q", resolved=None,
               intent="general", flagged=None, sources=["https://x"],
               answer_chars=10, answer_preview="a", latency_ms=1200)
    row.update(over)
    analytics.record_chat(**row)


def test_chat_row_is_written_and_committed(fake_db):
    _chat()
    statements = [s for s, _ in fake_db.executed]
    assert any(s.startswith("INSERT INTO chat_log") for s in statements)
    # retriever._conn() rolls back on exit, so without an explicit commit every
    # row would be silently discarded.
    assert fake_db.committed


def test_schema_is_created_once_not_per_question(fake_db):
    _chat(); _chat()
    creates = [s for s, _ in fake_db.executed if s.startswith("CREATE TABLE")]
    assert len(creates) == 1


def test_old_rows_are_pruned_for_retention(fake_db):
    _chat()
    deletes = [p for s, p in fake_db.executed if s.startswith("DELETE FROM chat_")]
    assert len(deletes) == 2 and deletes[0] == (analytics.RETENTION_DAYS,)


def test_write_failure_is_swallowed(fake_db):
    fake_db.fail = True
    _chat()  # must not raise
    assert not fake_db.committed


@pytest.mark.parametrize("flag", ["routing_error", "retrieval_error"])
def test_no_write_when_the_database_is_what_failed(fake_db, flag):
    _chat(flagged=flag)
    assert fake_db.executed == []


def test_no_write_while_the_breaker_has_failures(fake_db, monkeypatch):
    import retriever
    monkeypatch.setattr(retriever, "_breaker_state",
                        lambda: {"open": False, "consecutive_failures": 1,
                                 "cooldown_remaining_s": 0})
    _chat()
    assert fake_db.executed == []


def test_feedback_row_is_written(fake_db):
    analytics.record_feedback(request_id="r", ip_hash="h", rating="down",
                              question="q", answer_preview="a")
    assert any(s.startswith("INSERT INTO chat_feedback") for s, _ in fake_db.executed)


def test_rows_are_dropped_not_queued_forever_when_the_backlog_is_full(monkeypatch):
    submitted = []

    class _Exec:
        def submit(self, fn, *a):
            submitted.append(a)  # never runs: simulates a stuck database

    monkeypatch.setattr(analytics, "_executor", _Exec())
    monkeypatch.setattr(analytics, "ENABLED", True)
    monkeypatch.setattr(analytics, "MAX_BACKLOG", 3)
    monkeypatch.setattr(analytics, "_backlog", 0)
    for _ in range(10):
        analytics._submit("INSERT", ())
    assert len(submitted) == 3


def test_uvicorn_loggers_use_the_same_handler(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    root_before = logging.getLogger().handlers[:]
    try:
        obs.setup_logging()
        root_handler = logging.getLogger().handlers[0]
        for name in ("uvicorn.error", "uvicorn.access"):
            assert logging.getLogger(name).handlers == [root_handler]
    finally:
        logging.getLogger().handlers[:] = root_before
