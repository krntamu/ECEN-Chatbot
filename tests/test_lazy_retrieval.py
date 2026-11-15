"""
Retrieval must only run for questions whose answer is built from it.

The roster paths (faculty by area, all faculty, research-area list, degree
list) build their context from the knowledge graph. Until 2026-11-15 the
router ran the full retrieval pass first anyway and the roster paths discarded
it: ~11 seconds of cross-encoding per question in production, on the four most
clicked example questions.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_outage_end_to_end import app_module  # noqa: E402,F401  (fixture)


@pytest.fixture
def counted(app_module, monkeypatch):
    calls = []

    async def fake_retrieve(question, section_filter=None):
        calls.append(question)
        return [{"url": "https://engineering.tamu.edu/electrical/x.html", "title": "X",
                 "section": "about", "text": "some text", "rerank_score": 5.0}]

    monkeypatch.setattr(app_module, "retrieve_async", fake_retrieve)
    monkeypatch.setattr(app_module, "_people_by_area", lambda topic: [])
    return calls


async def _run(main, question):
    req = main.ChatRequest(question=question)
    search_req, route, prefetched = await main._route(req)
    assert prefetched is None, "the router must not retrieve"
    chunks = await main._prepare_chunks(search_req, route, prefetched=prefetched)
    return route, chunks


@pytest.mark.parametrize("question", [
    "What research areas does TAMU ECE specialize in?",
    "What MS and PhD programs does TAMU ECE offer?",
    "List all the faculty members",
])
async def test_roster_questions_never_run_retrieval(app_module, counted, question):
    _, chunks = await _run(app_module, question)
    assert chunks, "roster path should have produced context"
    assert chunks[0]["section"] == "graph"
    assert counted == [], f"retrieval ran for a roster question: {counted}"


async def test_faculty_by_area_never_runs_retrieval(app_module, counted):
    route, chunks = await _run(app_module, "Who are the AI & machine learning researchers?")
    assert route["intent"] == "people_by_area"
    if chunks and chunks[0]["url"] == "research-area-roster":
        assert counted == []


@pytest.mark.parametrize("question", ["Hi", "Thanks!", "Thanks for your help!",
                                      "thank you so much"])
async def test_small_talk_never_runs_retrieval(app_module, counted, question):
    await _run(app_module, question)
    assert counted == []


async def test_open_ended_questions_retrieve_exactly_once(app_module, counted):
    _, chunks = await _run(app_module, "What scholarships are available for ECE students?")
    assert len(counted) == 1
    assert any(c["url"].endswith("x.html") for c in chunks)


@pytest.mark.parametrize("question", [
    "thanks, also who teaches ECEN 314?",
    "Thank you. What are the PhD deadlines?",
    "thanks for the help with admissions, what about scholarships",
])
def test_a_thank_you_followed_by_a_real_question_is_not_small_talk(app_module, question):
    assert not app_module._CHITCHAT_RE.match(question)
