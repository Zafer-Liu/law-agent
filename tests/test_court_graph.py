# -*- coding: utf-8 -*-
"""Tests for the LangGraph refactor of the mock-courtroom engine.

Targets:
  legalweb/courtroom/graph.py         (MAX_JUROR_QUESTIONS)
  legalweb/courtroom/graph_engine.py  (GraphTrialEngine)

Run from repo root:
  E:/Tool/Python313/python.exe -m pytest tests/test_court_graph.py -x -q
or standalone:
  E:/Tool/Python313/python.exe -I tests/test_court_graph.py
"""

import hashlib
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from legalweb.courtroom import cases  # noqa: E402
from legalweb.llm import LLMError  # noqa: E402

CASE = list(cases.CASES.values())[0]
OPT_A = CASE["options"][0]
OPT_A_ID = OPT_A["id"]
OPT_A_LABEL = OPT_A["label"]

# The graph checkpointer DB path must be pinned BEFORE graph_engine is
# imported, so all imports of the module under test are lazy.
_TMPDIR = tempfile.mkdtemp(prefix="court_graph_test_")
os.environ.setdefault("LAW_COURT_GRAPH_DB", os.path.join(_TMPDIR, "graph.db"))

_ENGINE_MOD = None
_GRAPH_MOD = None


def engine_mod():
    global _ENGINE_MOD
    if _ENGINE_MOD is None:
        _ENGINE_MOD = __import__("legalweb.courtroom.graph_engine", fromlist=["GraphTrialEngine"])
    return _ENGINE_MOD


def graph_mod():
    global _GRAPH_MOD
    if _GRAPH_MOD is None:
        _GRAPH_MOD = __import__("legalweb.courtroom.graph", fromlist=["MAX_JUROR_QUESTIONS"])
    return _GRAPH_MOD


# ---------------------------------------------------------------------------
# Deterministic stub LLM (shared with legacy engine in the determinism test)
# ---------------------------------------------------------------------------

_POOL = [
    "审判长，根据卷宗内的转账记录，本案事实清楚，证据之间能够相互印证。",
    "我对起诉书指控的主要事实没有异议，但当时的情况与指控所述略有出入。",
    "辩护人认为，现有证据只能证明部分事实，尚不能排除合理怀疑。",
    "公诉人指出的事实确实存在，但被告人到案后如实供述，依法可以从轻处罚。",
    "证人当时距离现场较远，其辨认结论的准确性存疑，请合议庭审慎采信。",
    "我深刻认识到自己的行为给他人造成的伤害，恳请法庭从轻处理。",
]


class StubLLM:
    """Deterministic: same messages in, same text out, for both engines."""

    def __init__(self, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at  # raise LLMError on the Nth call (1-based) and after

    def complete(self, messages, temperature=0.3, max_tokens=8000):
        self.calls += 1
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise LLMError("模型请求失败或超时。")
        blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        idx = int(hashlib.md5(blob.encode("utf-8")).hexdigest(), 16) % len(_POOL)
        return _POOL[idx]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def collect(iterator):
    return list(iterator)


def events_of_type(events, etype):
    return [e for e in events if e.get("type") == etype]


def phases(events):
    return [e.get("phase") for e in events_of_type(events, "phase")]


def final_event(events):
    assert events, "engine yielded no events"
    return events[-1]


def checkpoint_of(events):
    cps = events_of_type(events, "checkpoint")
    assert cps, "no checkpoint event in stream: %r" % (events[-3:],)
    return cps[-1]


def line_texts(events, role=None):
    return [e["text"] for e in events_of_type(events, "line")
            if role is None or e.get("role") == role]


def has_line_containing(events, fragment):
    return any(fragment in t for t in line_texts(events))


def ask_question(engine, text, target="defendant"):
    """Send one juror question; return (events, final checkpoint-or-await)."""
    events = collect(engine.continue_({"question": text, "target": target}))
    return events


def run_juror_to_mid_review(engine):
    """start() then empty question -> mid_review checkpoint."""
    events = collect(engine.start())
    cp = checkpoint_of(events)
    assert cp["kind"] == "juror_question", cp
    assert cp["remaining"] == 3, cp
    ev2 = collect(engine.continue_({"question": "", "target": ""}))
    cp2 = checkpoint_of(ev2)
    assert cp2["kind"] == "mid_review", cp2
    return events + ev2


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_max_juror_questions_constant():
    assert graph_mod().MAX_JUROR_QUESTIONS == 3


def test_juror_full_path():
    GraphTrialEngine = engine_mod().GraphTrialEngine
    engine = GraphTrialEngine(CASE, StubLLM(), player_role="juror",
                              thread_id="juror-full")

    # --- start: opening + investigation, first checkpoint ---
    ev1 = collect(engine.start())
    assert "opening" in phases(ev1) and "investigation" in phases(ev1)
    cp1 = checkpoint_of(ev1)
    assert cp1["kind"] == "juror_question"
    assert cp1["remaining"] == 3
    assert engine.pending_checkpoint is not None

    # --- one question: judge line, answer turn, record line, re-checkpoint ---
    ev2 = ask_question(engine, "卷宗中的转账记录是谁经手的？")
    assert has_line_containing(ev2, "人民陪审员向")
    assert has_line_containing(ev2, "发问")
    assert has_line_containing(ev2, "请当庭作答。")
    assert events_of_type(ev2, "turn_start") and events_of_type(ev2, "turn_end")
    assert events_of_type(ev2, "delta")
    assert has_line_containing(ev2, "陪审员的发问与回答已记录在案。")
    assert has_line_containing(ev2, "如仍有疑问")
    cp2 = checkpoint_of(ev2)
    assert cp2["kind"] == "juror_question" and cp2["remaining"] == 2, cp2

    # --- stop asking: mid_review checkpoint with options ---
    ev3 = collect(engine.continue_({"question": "", "target": ""}))
    assert has_line_containing(ev3, "人民陪审员表示不再发问")
    cp3 = checkpoint_of(ev3)
    assert cp3["kind"] == "mid_review", cp3
    assert cp3.get("allow_question") is False
    assert cp3.get("options"), "mid_review checkpoint must carry options"

    # --- poll (investigation): clerk records, debate runs, poll checkpoint ---
    ev4 = collect(engine.continue_({"poll": OPT_A_ID}))
    assert has_line_containing(ev4, "书记员记录")
    assert "debate" in phases(ev4)
    cp4 = checkpoint_of(ev4)
    assert cp4["kind"] == "poll", cp4
    assert cp4.get("previous_label") == OPT_A_LABEL, cp4

    # --- poll (debate): statement phase, then await_deliberation ---
    other = next(o for o in CASE["options"] if o["id"] != OPT_A_ID)
    ev5 = collect(engine.continue_({"poll": other["id"]}))
    assert "statement" in phases(ev5)
    last = final_event(ev5)
    assert last["type"] == "await_deliberation", last
    assert engine.pending_checkpoint is None
    assert engine.pending_next_stage == "await_deliberation"

    # --- export_state ---
    state = engine.export_state()
    assert state["engine"] == "langgraph"
    assert state["player_role"] == "juror"
    assert state["thread_id"]
    polls = state["polls"]
    assert len(polls) == 2
    by_stage = {p["stage"]: p for p in polls}
    assert by_stage["investigation"]["label"] == OPT_A_LABEL
    assert by_stage["debate"]["label"] == other["label"]
    assert len(state["questions"]) == 1
    assert state["questions"][0]["target"] == "defendant"

    transcript = engine.transcript
    assert transcript
    defendants = [t for t in transcript if t.get("role") == "defendant"]
    assert defendants, "defendant answer must land in transcript"
    assert any(t["text"] in _POOL for t in defendants)


def test_fourth_question_rejected():
    GraphTrialEngine = engine_mod().GraphTrialEngine
    engine = GraphTrialEngine(CASE, StubLLM(), player_role="juror",
                              thread_id="juror-cap")

    collect(engine.start())
    # Ask the maximum of 3 questions.
    for i in range(3):
        ev = ask_question(engine, "第%d个问题：证据链是否完整？" % (i + 1))
        cp = checkpoint_of(ev)
        if i < 2:
            assert cp["kind"] == "juror_question", cp
            assert cp["remaining"] == 2 - i, cp
        else:
            # After the 3rd answer there must be NO further juror_question
            # interrupt: the next checkpoint is mid_review (asking is over).
            assert cp["kind"] == "mid_review", cp
            assert cp.get("allow_question") is False

    # A 4th juror_question payload is out-of-band here: the engine must not
    # accept it as another question round.
    try:
        ev = collect(engine.continue_({"question": "第四个问题", "target": "defendant"}))
    except ValueError:
        pass
    else:
        # Tolerant variant: if it streams instead of raising, it must not
        # produce another juror_question checkpoint nor record a 4th question.
        cps = events_of_type(ev, "checkpoint")
        assert not any(c["kind"] == "juror_question" for c in cps), cps
        assert len(engine.export_state()["questions"]) == 3

    assert len(engine.export_state()["questions"]) == 3


def test_invalid_poll_id_raises():
    GraphTrialEngine = engine_mod().GraphTrialEngine
    engine = GraphTrialEngine(CASE, StubLLM(), player_role="juror",
                              thread_id="juror-badpoll")
    run_juror_to_mid_review(engine)
    try:
        collect(engine.continue_({"poll": "NOT_A_REAL_ID"}))
    except ValueError:
        pass
    else:
        raise AssertionError("invalid poll id must raise ValueError")


def test_role_path_defender():
    GraphTrialEngine = engine_mod().GraphTrialEngine
    engine = GraphTrialEngine(CASE, StubLLM(), player_role="defender",
                              thread_id="defender-path")

    ev1 = collect(engine.start())
    cp1 = checkpoint_of(ev1)
    assert cp1["kind"] == "role_input", cp1
    assert cp1["stage"] == "investigation", cp1
    assert cp1.get("options"), cp1

    statement = "辩" * 20
    ev2 = collect(engine.continue_({"statement": statement, "stage": "investigation"}))
    player_lines = [e for e in events_of_type(ev2, "line") if e.get("player")]
    assert player_lines, "player statement must be echoed as a player line"
    assert any(e["text"] == statement for e in player_lines)
    assert has_line_containing(ev2, "发言已记录在案")
    assert "debate" in phases(ev2)
    cp2 = checkpoint_of(ev2)
    assert cp2["kind"] == "role_input" and cp2["stage"] == "debate", cp2

    ev3 = collect(engine.continue_({"statement": "辩" * 15, "stage": "debate"}))
    assert "statement" in phases(ev3)
    last = final_event(ev3)
    assert last["type"] == "await_deliberation", last
    state = engine.export_state()
    assert state["player_role"] == "defender"
    assert len(state["contributions"]) == 2
    assert engine.pending_next_stage == "await_deliberation"


def test_statement_too_short_raises():
    GraphTrialEngine = engine_mod().GraphTrialEngine
    engine = GraphTrialEngine(CASE, StubLLM(), player_role="defender",
                              thread_id="defender-short")
    collect(engine.start())
    try:
        collect(engine.continue_({"statement": "太短", "stage": "investigation"}))
    except ValueError:
        pass
    else:
        raise AssertionError("short statement (<10 chars) must raise ValueError")


def test_audience_runs_straight_through():
    GraphTrialEngine = engine_mod().GraphTrialEngine
    engine = GraphTrialEngine(CASE, StubLLM(), player_role="audience",
                              thread_id="audience-path")
    events = collect(engine.start())
    assert events_of_type(events, "checkpoint") == []
    assert phases(events) == ["opening", "investigation", "debate", "statement"]
    last = final_event(events)
    assert last["type"] == "await_deliberation", last
    assert last.get("player_role") == "audience"
    assert engine.pending_checkpoint is None
    assert engine.pending_next_stage == "await_deliberation"


def test_determinism_vs_legacy():
    """Same stub LLM + same inputs -> graph and legacy streams agree."""
    engine_mod_ = engine_mod()
    GraphTrialEngine = engine_mod_.GraphTrialEngine
    from legalweb.courtroom.engine import TrialEngine

    # ---- legacy run ----
    legacy = TrialEngine(CASE, StubLLM(), player_role="juror")
    legacy_events = list(legacy.run("opening"))
    while legacy_events and legacy_events[-1]["type"] == "checkpoint":
        cp = legacy_events[-1]
        kind = cp["kind"]
        if kind == "juror_question":
            legacy_events += legacy.apply_checkpoint(
                kind, {"question": "转账记录为何没有原件？", "target": "defendant"})
            # first round: asked once; then stop asking
            if legacy_events[-1]["type"] == "checkpoint" \
                    and legacy_events[-1]["kind"] == "juror_question":
                legacy_events += legacy.apply_checkpoint(
                    "juror_question", {"question": "", "target": ""})
            if legacy_events[-1]["type"] != "checkpoint":
                legacy_events += legacy.run("mid_review")
        elif kind == "mid_review":
            legacy_events += legacy.apply_checkpoint("mid_review", {"poll": OPT_A_ID})
            legacy_events += legacy.run("debate")
        elif kind == "poll":
            legacy_events += legacy.apply_checkpoint("poll", {"poll": OPT_A_ID})
            legacy_events += legacy.run("statement")
        else:
            break

    # ---- graph run, same inputs ----
    g = GraphTrialEngine(CASE, StubLLM(), player_role="juror",
                         thread_id="determinism")
    graph_events = collect(g.start())
    graph_events += collect(g.continue_(
        {"question": "转账记录为何没有原件？", "target": "defendant"}))
    graph_events += collect(g.continue_({"question": "", "target": ""}))
    graph_events += collect(g.continue_({"poll": OPT_A_ID}))
    graph_events += collect(g.continue_({"poll": OPT_A_ID}))
    assert graph_events, "graph engine yielded nothing"
    assert graph_events[-1]["type"] == "await_deliberation", graph_events[-1]
    assert legacy_events[-1]["type"] == "await_deliberation", legacy_events[-1]

    # ---- signature comparison ----
    def signature(e):
        if e["type"] == "line":
            return ("line", e.get("role"), e.get("name"), e["text"])
        if e["type"] == "phase":
            return ("phase", e.get("phase"))
        if e["type"] in ("turn_start", "turn_end"):
            return (e["type"], e.get("role"))
        if e["type"] == "checkpoint":
            return ("checkpoint", e.get("kind"))
        return (e["type"],)

    sig_legacy = [signature(e) for e in legacy_events]
    sig_graph = [signature(e) for e in graph_events]
    assert sig_legacy == sig_graph, _first_diff(sig_legacy, sig_graph)

    # Judge / clerk line text must be identical (program-controlled lines).
    for a, b in zip(legacy_events, graph_events):
        if a["type"] == "line" and a.get("role") in ("judge", "clerk"):
            assert a["text"] == b["text"], (a["text"], b["text"])


def _first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return "first differing event #%d: legacy=%r graph=%r" % (i, x, y)
    if len(a) != len(b):
        return "length mismatch: legacy=%d graph=%d" % (len(a), len(b))
    return "identical"


def test_crash_rollback():
    GraphTrialEngine = engine_mod().GraphTrialEngine
    thread_id = "crash-rollback"

    # A stub that dies on the 2nd LLM call (mid opening/investigation).
    engine = GraphTrialEngine(CASE, StubLLM(fail_at=2), player_role="juror",
                              thread_id=thread_id)
    try:
        collect(engine.start())
    except LLMError:
        pass
    else:
        raise AssertionError("LLMError must propagate out of start()")

    # Nothing committed beyond the failed superstep: no usable checkpoint.
    assert engine.pending_checkpoint is None or \
        engine.pending_checkpoint.get("kind") is None, engine.pending_checkpoint
    state = engine.export_state()
    assert len(state["questions"]) == 0
    assert len(state["polls"]) == 0

    # A retry with a healthy stub on the SAME thread completes cleanly.
    fresh = GraphTrialEngine(CASE, StubLLM(), player_role="juror",
                             thread_id=thread_id)
    events = collect(fresh.start())
    last = final_event(events)
    assert last["type"] in ("checkpoint", "await_deliberation"), last
    if last["type"] == "checkpoint":
        assert last["kind"] in ("juror_question", "mid_review",
                                "poll", "role_input"), last


# ---------------------------------------------------------------------------
# Standalone runner (no pytest required): python -I tests/test_court_graph.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _TESTS = [
        test_max_juror_questions_constant,
        test_juror_full_path,
        test_fourth_question_rejected,
        test_invalid_poll_id_raises,
        test_role_path_defender,
        test_statement_too_short_raises,
        test_audience_runs_straight_through,
        test_determinism_vs_legacy,
        test_crash_rollback,
    ]
    failed = 0
    for _t in _TESTS:
        try:
            _t()
            print("PASS %s" % _t.__name__)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            print("FAIL %s: %r" % (_t.__name__, exc))
            traceback.print_exc()
    sys.exit(1 if failed else 0)
