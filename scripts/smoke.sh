#!/usr/bin/env bash
# Post-deploy smoke test: is the live site actually answering questions?
#
#   ./scripts/smoke.sh                       # production URL
#   ./scripts/smoke.sh http://localhost:3000 # local stack
#
# Checks the public surface only (what a user's browser hits):
#   1. GET  /api/health  -> 200 (database + index + embedder are usable)
#   2. POST /api/chat    -> a complete SSE stream with a real answer and at
#                           least one source, for a question that must always
#                           be answerable from the index
# Exits non-zero on the first failure. Sends 2 questions, well under the
# 10/minute rate limit.
set -uo pipefail

BASE="${1:-https://ecen-chatbot-199137295144.us-central1.run.app}"
BASE="${BASE%/}"
fail() { echo "FAIL: $*" >&2; exit 1; }

echo "Smoke test against $BASE"

# Cold starts load two ML models before the backend binds its port.
code=000
for attempt in $(seq 1 12); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/api/health" || true)"
  [[ "$code" == "200" ]] && break
  echo "  health: HTTP $code (attempt $attempt/12), waiting..."
  sleep 10
done
[[ "$code" == "200" ]] || fail "/api/health never returned 200 (last: $code)"
echo "  ok  /api/health"

ask() {
  local question="$1" out
  out="$(curl -sS -N --max-time 180 -X POST "$BASE/api/chat" \
          -H 'Content-Type: application/json' \
          -d "{\"question\": \"$question\"}")" || fail "request failed: $question"
  grep -q '^data: \[DONE\]' <<<"$out" || fail "stream never finished: $question"
  if grep -qiE "reach(ing)? my knowledge base|something went wrong" <<<"$out"; then
    fail "outage message returned for: $question"
  fi
  local answer_chars
  answer_chars="$(grep '^data: ' <<<"$out" | grep -v '^data: \[' | wc -c | tr -d ' ')"
  (( answer_chars > 80 )) || fail "answer too short ($answer_chars chars): $question"
  grep -A1 '^event: sources' <<<"$out" | grep -q '"url"' || fail "no sources cited: $question"
  echo "  ok  \"$question\" ($answer_chars chars)"
}

ask "What MS and PhD programs does TAMU ECE offer?"
ask "What research areas does TAMU ECE specialize in?"

echo "PASS"
