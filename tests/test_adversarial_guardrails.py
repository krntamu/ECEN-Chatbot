"""
Regression tests for the 2026-08-09 adversarial eval run (32/36 passed).

Locks in the three product fixes made in response. The fourth failure (H.4) was
a bad assertion, not a bug, and is fixed in scripts/eval_cases_100.py.

  11.3  "Who is professor John Doesnotexist and what does he teach?"
        The bot did not refuse. Retrieval surfaced "John Dennison" from a news
        article and it answered: "there might be a little confusion with the
        name... the professor mentioned is actually John Dennison, who teaches
        the fundamentals of circuit design." Substituting a different REAL
        person for a nonexistent one is worse than refusing or inventing —
        it reads as a helpful correction, so a student would act on it.
        Dumbledore / Tony Stark / Sherlock Holmes all passed; the failure mode
        needs a fake name that shares a common first name with the corpus.

  R.1   "Hi" returned an empty answer in 0.3s. ChatRequest declared
        min_length=3, so the two-character greeting 422'd before the router
        ran — even though _CHITCHAT_RE explicitly lists "hi".

  R.3   "who r the machne lerning profs" dead-ended in the no-info fallback.
        The faculty-by-area path builds exact ILIKE patterns, so "machne"
        matched nothing. ENABLE_FUZZY=1 could not fix this (it adds a pg_trgm
        arm during retrieval, which this query never reaches) and measured
        ~1s of extra db_arms plus 10-15 more cross-encoder pairs.
"""
import os
import sys
import types

import pytest


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

    class ThreadedConnectionPool:  # pragma: no cover
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

# `main` reads these at import time and caches them as module constants, and
# whichever test module imports it first wins for the whole session. Set the
# same values test_outage_end_to_end.py's fixture uses, so importing `main`
# here can't strand that module with an empty ADMIN_TOKEN (its admin tests fail
# in a full-suite run otherwise, while passing when run alone).
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("CHAT_RATE_LIMIT", "1000/minute")
os.environ.setdefault("DISABLE_SCHEDULER", "1")

import graph_retriever as gr  # noqa: E402
import retriever as r  # noqa: E402


# ── 11.3: unknown titled person ──────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "Who is professor John Doesnotexist and what does he teach?",   # the failure
    "What is Professor Albus Dumbledore's research area in ECE?",
    "What is the email address of Dr. Tony Stark in the ECE department?",
    "Tell me about Professor Sherlock Holmes's lab in the ECE department.",
])
def test_unknown_titled_person_triggers_the_notice(q):
    notice = gr.unknown_person_notice(q)
    assert notice, f"guardrail did not fire: {q!r}"
    assert "Do NOT answer about a different person" in notice


def test_notice_names_the_person_asked_about():
    notice = gr.unknown_person_notice(
        "Who is professor John Doesnotexist and what does he teach?")
    assert "John Doesnotexist" in notice


# ── Must not fire on real faculty ────────────────────────────────────────────
# A false positive here tells a student a real professor doesn't exist, which is
# worse than the bug being fixed — so matching is generous on purpose.

def test_real_faculty_are_never_flagged():
    graph = gr._load_graph()
    names = list(graph["nodes"]["faculty"])
    assert len(names) > 50, "graph looks empty; test would pass vacuously"
    for name in names:
        for phrasing in (f"Tell me about professor {name}.",
                         f"What does Dr. {name.split()[-1]} research?"):
            assert gr.unknown_person_notice(phrasing) is None, (
                f"flagged a real faculty member: {phrasing!r}")


@pytest.mark.parametrize("q", [
    # No title word — bare capitalized tokens are usually topics or buildings.
    "Who works on Machine Learning?",
    "Where is the Wisenbaker Engineering Building?",
    "What research areas does TAMU ECE offer?",
    # Title word but no name following it.
    "How many professors are in the department?",
    "Which professors research power systems?",
])
def test_guardrail_does_not_fire_without_an_unknown_named_person(q):
    assert gr.unknown_person_notice(q) is None, f"wrongly fired: {q!r}"


# ── R.3: typo-tolerant topic matching ────────────────────────────────────────

@pytest.mark.parametrize("typo,expected", [
    ("machne", "machine"),
    ("lerning", "learning"),
    ("artifical", "artificial"),
    ("inteligence", "intelligence"),
    ("securty", "security"),
])
def test_topic_typos_snap_to_department_vocabulary(typo, expected):
    assert r._correct_topic_word(typo) == expected


@pytest.mark.parametrize("word", [
    "quantum", "photonics", "microelectronics", "robotics", "blockchain",
])
def test_unknown_but_correctly_spelled_words_pass_through(word):
    """Novel topics must still reach the generic fallback unchanged."""
    assert r._correct_topic_word(word) == word


def test_typod_query_reaches_the_ai_ml_signal_set():
    """The R.3 failure, end to end through the pure functions."""
    topic = r._people_area_topic("who r the machne lerning profs")
    assert topic is not None
    terms = {t for t, _ in r._signal_terms(topic)}
    assert "machine learning" in terms, sorted(terms)
    assert "artificial intelligence" in terms


def test_correction_is_deterministic():
    """Ties must not depend on set iteration order across processes."""
    assert len({r._correct_topic_word("machne") for _ in range(20)}) == 1


# ── R.1: "Hi" must survive request validation ────────────────────────────────

def _import_main():
    """main.py imports the scheduler, which needs apscheduler. It isn't a test
    dependency, and this file only passed when another test module happened to
    install the stub first: run on its own, these three tests errored."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_outage_end_to_end import _install_scheduler_stub
    _install_scheduler_stub()
    os.environ.setdefault("DISABLE_SCHEDULER", "1")
    import main  # noqa: E402  (imported late: pulls the FastAPI app)
    return main


def test_two_letter_greeting_is_accepted_by_the_request_model():
    main = _import_main()
    field = main.ChatRequest.model_fields["question"]
    constraints = {type(m).__name__: getattr(m, "min_length", None)
                   for m in field.metadata}
    assert any(v == 1 for v in constraints.values()), (
        f"question min_length must be 1 so 'Hi' reaches _CHITCHAT_RE; got {constraints}"
    )
    assert main.ChatRequest(question="Hi").question == "Hi"


def test_chitchat_regex_still_claims_the_greeting():
    main = _import_main()
    assert main._CHITCHAT_RE.match("Hi")
    assert main._fast_route(main.ChatRequest(question="Hi"))["intent"] == "chitchat"


def test_empty_question_is_still_rejected():
    main = _import_main()
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        main.ChatRequest(question="")
