"""
Tests for degree-title stripping in `retriever._people_area_topic`.

Background (2026-08-09, found in a live 3-turn conversation):

    turn 1  "What MS and PhD programs does TAMU ECE offer?"          -> good
    turn 2  "Tell me about the Microelectronics and Semiconductors
             MS program?"                                            -> good
    turn 3  "Can you tell me about the faculty involved in this
             program?"                                               -> FAILED

Turn 3 returned "the context does not include specific details about the
faculty" and cited college-wide study-abroad pages ("Faculty-led Programs",
"For Faculty", "Research Intern Requirements") — nothing to do with ECE
microelectronics.

Follow-up resolution was NOT the bug: the answer itself named "the Master of
Science in Microelectronics and Semiconductors program", so the referent was
substituted correctly. The bug was downstream. `_people_area_topic` strips
scaffolding to get topic words, and _SCAFFOLD contained "faculty" and
"involved" but not "master", "science", "program". The topic came out as

    ["master", "science", "microelectronics", "semiconductors", "program"]

Only 2 of 5 words name the real subject. `build_area_roster` requires >=60%
overlap with a research area, 2/5 = 40% fails it, `_people_by_area` then did an
exact-phrase match on the whole polluted string and found nothing, and the query
fell through to generic dense+BM25 — where "faculty" and "program" pull in
college-wide pages.

Two fixes, both covered here:
  1. `_DEGREE_TITLE_RE` strips degree titles as PHRASES before topic extraction,
     and degree scaffolding ("program", "degree", demonstratives) joins
     _SCAFFOLD. Phrase-stripping matters: "science" must survive as a topic word
     for the real area "Information Science and Learning Systems".
  2. "Microelectronics and Semiconductors" is a degree program, not one of the
     11 research areas, so even a clean topic had nothing to route to. Its
     vocabulary now maps onto the "chip manufacturing" signal set.
"""
import os
import sys
import types

import pytest


# ── Stub the heavy imports so retriever loads without a DB or torch ──────────
# Same pattern as tests/test_db_pool_resilience.py; guarded so whichever test
# module imports first wins and the other no-ops.

def _install_stubs():
    if "psycopg2" in sys.modules and hasattr(sys.modules["psycopg2"], "_is_stub"):
        return

    psycopg2 = types.ModuleType("psycopg2")
    psycopg2._is_stub = True

    class OperationalError(Exception):
        pass

    class InterfaceError(Exception):
        pass

    class PoolError(Exception):
        pass

    psycopg2.OperationalError = OperationalError
    psycopg2.InterfaceError = InterfaceError

    pool_mod = types.ModuleType("psycopg2.pool")
    pool_mod.PoolError = PoolError

    class ThreadedConnectionPool:  # pragma: no cover - not exercised here
        def __init__(self, *a, **kw):
            pass

    pool_mod.ThreadedConnectionPool = ThreadedConnectionPool
    psycopg2.pool = pool_mod

    extras = types.ModuleType("psycopg2.extras")
    extras.execute_values = lambda *a, **kw: None
    psycopg2.extras = extras

    sys.modules["psycopg2"] = psycopg2
    sys.modules["psycopg2.pool"] = pool_mod
    sys.modules["psycopg2.extras"] = extras

    pgvector = types.ModuleType("pgvector")
    pgv_psycopg2 = types.ModuleType("pgvector.psycopg2")
    pgv_psycopg2.register_vector = lambda conn: None
    pgvector.psycopg2 = pgv_psycopg2
    sys.modules["pgvector"] = pgvector
    sys.modules["pgvector.psycopg2"] = pgv_psycopg2

    rank_bm25 = types.ModuleType("rank_bm25")
    rank_bm25.BM25Okapi = object
    sys.modules["rank_bm25"] = rank_bm25

    st = types.ModuleType("sentence_transformers")
    st.CrossEncoder = object
    st.SentenceTransformer = object
    sys.modules["sentence_transformers"] = st


_install_stubs()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import retriever as r  # noqa: E402


# ── The reported failure ─────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    # Bare resolved form.
    "faculty involved in the Master of Science in Microelectronics "
    "and Semiconductors program",
    # Verbatim turn 3 as the follow-up resolver produces it — the conversational
    # wrapper ("Can you tell me about…") must not leak "you"/"about" into the
    # topic, or a 2-word subject scores 2/4 = 50% against build_area_roster's
    # 60% overlap threshold and the roster path bails.
    "Can you tell me about the faculty involved in the Master of Science "
    "in Microelectronics and Semiconductors program?",
])
def test_resolved_degree_followup_yields_only_subject_words(q):
    """The exact turn-3 query, after follow-up resolution."""
    topic = r._people_area_topic(q)
    assert topic is not None, "should still route to the faculty-by-area path"
    assert set(topic) == {"microelectronics", "semiconductors"}, (
        f"degree title/scaffolding leaked into the topic: {topic}"
    )


def test_resolved_degree_followup_routes_to_a_real_area():
    """A clean topic is not enough — it must reach a curated signal set.

    Without the chip-manufacturing mapping this falls to the generic fallback,
    where each word weighs 1.0 and a profile needs BOTH plural forms to clear
    MIN_SIGNAL_SCORE (2.0) — which almost none do.
    """
    sigs = r._signal_terms(["microelectronics", "semiconductors"])
    terms = {t for t, _ in sigs}
    assert "semiconductor manufacturing" in terms, (
        f"expected the curated chip-manufacturing signals, got {sorted(terms)}"
    )
    assert max(w for _, w in sigs) >= 3.0


@pytest.mark.parametrize("q,expected", [
    ("who are the faculty in the MS in Microelectronics and Semiconductors?",
     {"microelectronics", "semiconductors"}),
    ("which professors teach in the PhD in Computer Engineering program?",
     {"computer", "engineering"}),
    ("faculty in the Bachelor of Science in Electrical Engineering degree",
     {"electrical", "engineering"}),
    ("professors involved in the online MS program in power systems",
     {"power", "systems"}),
])
def test_degree_titles_stripped_across_phrasings(q, expected):
    topic = r._people_area_topic(q)
    assert topic is not None, f"should route: {q!r}"
    assert set(topic) == expected, f"{q!r} -> {topic}"


# ── Must not over-strip ──────────────────────────────────────────────────────

def test_science_survives_outside_a_degree_title():
    """'science' is load-bearing for 'Information Science and Learning Systems'.

    This is why the fix strips "Master of Science" as a bound phrase instead of
    adding "science" to _SCAFFOLD.
    """
    topic = r._people_area_topic("which faculty work in information science?")
    assert topic is not None
    assert "science" in topic, f"'science' was over-stripped: {topic}"


def test_information_science_still_routes_to_its_own_area():
    sigs = r._signal_terms(["information", "science", "learning", "systems"])
    terms = {t for t, _ in sigs}
    assert "information theory" in terms, sorted(terms)


@pytest.mark.parametrize("q", [
    "which professors research machine learning",
    "who works on power systems",
    "faculty in analog and mixed signals",
    "AI/ML researchers",
])
def test_existing_area_queries_are_unchanged(q):
    """Regression guard: the fix must not disturb queries with no degree title."""
    assert r._people_area_topic(q) is not None, f"stopped routing: {q!r}"


@pytest.mark.parametrize("q", [
    "who are the faculty",
    "list all professors",
    "tell me about professor Enjeti",
])
def test_topicless_and_single_person_queries_still_return_none(q):
    """These must keep falling through to the full-roster / normal paths."""
    assert r._people_area_topic(q) is None, f"wrongly hijacked: {q!r}"


# ── The degree-title regex in isolation ──────────────────────────────────────

@pytest.mark.parametrize("text,gone_tokens", [
    ("master of science in microelectronics", {"master", "of", "science"}),
    ("doctor of philosophy in computer engineering", {"doctor", "of", "philosophy"}),
    ("bachelor of science in electrical engineering", {"bachelor", "of", "science"}),
    ("the phd in security", {"phd"}),
    ("ph.d. in security", {"ph.d."}),
    # Token comparison, not substring: "ms" lives inside "systems", so
    # `"ms" not in result` would fail even on a correct strip.
    ("an ms in power systems", {"ms"}),
])
def test_degree_title_regex_removes_the_title(text, gone_tokens):
    remaining = set(r._DEGREE_TITLE_RE.sub(" ", text).split())
    assert not (remaining & gone_tokens), f"{text!r} -> {sorted(remaining)}"


@pytest.mark.parametrize("text", [
    "information science and learning systems",
    "biomedical imaging and sensing",
    "materials science",
])
def test_degree_title_regex_leaves_topics_intact(text):
    assert r._DEGREE_TITLE_RE.sub(" ", text).split() == text.split()
