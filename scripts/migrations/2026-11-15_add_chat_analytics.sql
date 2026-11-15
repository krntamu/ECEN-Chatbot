-- Durable question/feedback log (see backend/analytics.py).
-- The app creates these on first use; run this by hand only if the app's
-- database role is not allowed to CREATE TABLE.
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
