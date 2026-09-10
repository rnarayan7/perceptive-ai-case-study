"""Ledger persistence + tracer contract tests (deterministic, no network/model)."""

from __future__ import annotations

import json

from memo.ledger import (
    ClaimRecord,
    EvidenceRecord,
    FeedbackMessageRecord,
    FeedbackSessionRecord,
    GenerationRunRecord,
    LedgerStore,
    MemoRecord,
    SectionRecord,
)
from memo.trace import JsonlTracer, NullTracer, TraceEvent, new_run_id


def _memo_and_claims():
    memo = MemoRecord(memo_id="KYMR-run1", company="KYMR", status="complete",
                      recommendation="Buy", thesis="Undervalued vs implied PoS.")
    claim = ClaimRecord(
        memo_id="KYMR-run1", section="moa", module="moa",
        statement="KT-474 is a selective IRAK4 degrader", confidence=0.8,
        rationale="clinical PD", value=None,
        evidence=[EvidenceRecord(doc_id="NCT04772885", source="clinicaltrials",
                                 doc_type="study", url="https://x/NCT04772885",
                                 quote="IRAK4 degrader", date="2021-01-01")],
    )
    return memo, [claim]


def test_feedback_session_roundtrip(tmp_path):
    store = LedgerStore(db_path=str(tmp_path / "ledger.db"))
    session = FeedbackSessionRecord(
        company="KYMR", section_id="efficacy", section_label="Efficacy",
        highlighted_quote="60% overall response rate",
    )
    store.write_feedback_session(session)
    store.append_feedback_message(
        session.session_id, FeedbackMessageRecord(role="analyst", text="ORR should be 42%.")
    )
    # A later turn updates the summary and appends the agent reply.
    session.status = "captured"
    session.issue = "ORR overstated"
    session.severity = "high"
    store.write_feedback_session(session)
    store.append_feedback_message(
        session.session_id, FeedbackMessageRecord(role="agent", text="Got it, logged.")
    )

    got = store.get_feedback_session(session.session_id)
    assert got is not None
    assert got.status == "captured" and got.issue == "ORR overstated" and got.severity == "high"
    assert got.highlighted_quote == "60% overall response rate"
    assert [m.role for m in got.messages] == ["analyst", "agent"]

    # Re-upserting the session row must not duplicate the message turns.
    store.write_feedback_session(got)
    assert len(store.get_feedback_session(session.session_id).messages) == 2

    listed = store.list_feedback_sessions("KYMR")
    assert len(listed) == 1 and listed[0].session_id == session.session_id
    assert store.get_feedback_session("fb_missing") is None


def test_ledger_roundtrip(tmp_path):
    store = LedgerStore(db_path=str(tmp_path / "ledger.db"))
    memo, claims = _memo_and_claims()
    memo_id = store.write_memo(memo, claims)

    got = store.get_memo(memo_id)
    assert got is not None and got.company == "KYMR" and got.recommendation == "Buy"

    got_claims = store.get_claims(memo_id, section="moa")
    assert len(got_claims) == 1
    c = got_claims[0]
    assert c.statement.startswith("KT-474")
    assert len(c.evidence) == 1 and c.evidence[0].doc_id == "NCT04772885"

    assert store.latest_memo("KYMR").memo_id == memo_id
    assert [m.memo_id for m in store.list_memos("KYMR")] == [memo_id]
    store.close()


def test_ledger_write_is_transactional_and_replaceable(tmp_path):
    store = LedgerStore(db_path=str(tmp_path / "ledger.db"))
    memo, claims = _memo_and_claims()
    store.write_memo(memo, claims)
    # Re-writing the same memo_id replaces rather than duplicating.
    store.write_memo(memo, claims)
    assert len(store.list_memos("KYMR")) == 1
    store.close()


def test_persists_sections_and_numeric_values(tmp_path):
    """0b: per-section rollups and parsed numeric claim values round-trip."""
    store = LedgerStore(db_path=str(tmp_path / "ledger.db"))
    memo, claims = _memo_and_claims()
    claims[0].value = "$0.4-0.9B"
    claims[0].value_num = 6.5e8
    claims[0].value_unit = "USD"
    sections = [SectionRecord(memo_id=memo.memo_id, section_id="moa", module="moa",
                              confidence=0.8, tier="high", summary="Mechanism holds.")]
    store.write_memo(memo, claims, sections)

    c = store.get_claims(memo.memo_id)[0]
    assert c.value_num == 6.5e8 and c.value_unit == "USD"

    got = store.get_sections(memo.memo_id)
    assert len(got) == 1
    assert got[0].tier == "high" and got[0].summary == "Mechanism holds."
    assert got[0].confidence == 0.8
    store.close()


def test_evidence_reverse_lookup(tmp_path):
    """0b/Tier-2: resolve an evidence id to its row and its claim without a memo_id."""
    store = LedgerStore(db_path=str(tmp_path / "ledger.db"))
    memo, claims = _memo_and_claims()
    store.write_memo(memo, claims)
    eid = claims[0].evidence[0].evidence_id

    ev = store.get_evidence_by_id(eid)
    assert ev is not None and ev.doc_id == "NCT04772885"

    claim = store.get_claim_for_evidence(eid)
    assert claim is not None and claim.statement.startswith("KT-474")
    assert store.get_evidence_by_id("nope") is None
    store.close()


def test_generation_run_roundtrip(tmp_path):
    """Run-level cost/summary persists for the Activity feed."""
    store = LedgerStore(db_path=str(tmp_path / "ledger.db"))
    store.write_generation_run(GenerationRunRecord(
        run_id="KYMR-run1", memo_id="KYMR-run1", company="KYMR", status="completed",
        trigger="manual", output="12 claims · 8 sections", model="claude-opus-5",
        input_tokens=1000, output_tokens=500, cost_usd=0.0175, duration_s=42.5,
    ))
    runs = store.list_generation_runs()
    assert len(runs) == 1
    r = runs[0]
    assert r.company == "KYMR" and r.status == "completed"
    assert r.cost_usd == 0.0175 and r.input_tokens == 1000 and r.output_tokens == 500
    assert "claims" in r.output
    store.close()


def test_new_run_id_is_prefixed_and_unique():
    a, b = new_run_id("KYMR"), new_run_id("KYMR")
    assert a.startswith("KYMR-") and b.startswith("KYMR-") and a != b


def test_null_tracer_is_noop():
    NullTracer().emit(TraceEvent(run_id="r", kind="model", name="x"))  # must not raise


def test_jsonl_tracer_writes_events(tmp_path):
    tracer = JsonlTracer(root=str(tmp_path / "traces"))
    rid = "KYMR-run1"
    with tracer.span(rid, "retrieval", "retrieve", query="cash") as ev:
        ev["n_results"] = 3
    tracer.emit(TraceEvent(run_id=rid, kind="model", name="complete_json",
                           data={"input_tokens": 100}))

    path = tmp_path / "traces" / f"{rid}.jsonl"
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["kind"] == "retrieval" and lines[0]["data"]["n_results"] == 3
    assert lines[0]["duration_ms"] is not None
    assert lines[1]["data"]["input_tokens"] == 100


def test_tracer_span_records_error_then_reraises(tmp_path):
    tracer = JsonlTracer(root=str(tmp_path / "traces"))
    try:
        with tracer.span("r", "model", "boom"):
            raise ValueError("nope")
    except ValueError:
        pass
    line = json.loads((tmp_path / "traces" / "r.jsonl").read_text().splitlines()[0])
    assert line["kind"] == "error" and "nope" in line["data"]["error"]
