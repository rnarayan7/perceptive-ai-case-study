"""Ledger persistence + tracer contract tests (deterministic, no network/model)."""

from __future__ import annotations

import json

from memo.ledger import ClaimRecord, EvidenceRecord, LedgerStore, MemoRecord
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
