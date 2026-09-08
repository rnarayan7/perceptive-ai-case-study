"""Flask test-client tests for the memo audit app, against the seeded fixture.

No network, no model calls: everything runs off a temp ledger + rendered artifact created
by ``app.seed``. Asserts the four surfaces from the brief — list, memo view, claim audit,
and Markdown export.
"""

from __future__ import annotations

import pytest

from app.seed import seed
from app.server import create_app


@pytest.fixture()
def client_and_memo(tmp_path):
    db_path = str(tmp_path / "ledger.db")
    memos_dir = str(tmp_path / "memos")
    memo_id = seed(db_path, memos_dir)
    app = create_app(db_path=db_path, memos_dir=memos_dir)
    app.config["TESTING"] = True
    return app.test_client(), memo_id


def test_index_lists_seeded_memo(client_and_memo):
    client, memo_id = client_and_memo
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "KYMR" in html
    assert memo_id in html
    assert "Buy" in html  # recommendation
    assert f"/memo/{memo_id}" in html  # links through to the memo


def test_memo_view_renders_sections_prose_and_citation_link(client_and_memo):
    client, memo_id = client_and_memo
    resp = client.get(f"/memo/{memo_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # section titles from MEMO_SECTIONS
    assert "Mechanism of Action" in html
    assert "Probability of Success" in html
    assert "Valuation &amp; Price vs Thesis" in html or "Valuation" in html
    # prose
    assert "selective IRAK4 degrader" in html
    # thesis / recommendation up top
    assert "IRAK4 degrader mechanism" in html
    # a citation marker linkified into an audit link
    assert f"/memo/{memo_id}/audit/" in html
    assert 'class="cite"' in html
    # a figure is shown
    assert "kt474_knockdown.png" in html


def test_memo_view_orders_sections_by_memo_sections(client_and_memo):
    client, memo_id = client_and_memo
    html = client.get(f"/memo/{memo_id}").get_data(as_text=True)
    # thesis before moa before pos before valuation (document order)
    assert (
        html.index("Executive Summary")
        < html.index("Mechanism of Action")
        < html.index("Probability of Success")
        < html.index("Valuation")
    )


def test_audit_route_returns_evidence_quote_and_source_url(client_and_memo):
    client, memo_id = client_and_memo
    # find an evidence id from the ledger the same way the app does
    from memo.ledger import LedgerStore

    store = LedgerStore(db_path=client.application.config["DB_PATH"])
    claims = store.get_claims(memo_id, section="moa")
    ev = claims[0].evidence[0]
    store.close()

    resp = client.get(f"/memo/{memo_id}/audit/{ev.evidence_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "oral small-molecule degrader of IRAK4" in html  # the quoted span
    assert "clinicaltrials.gov/study/NCT04772885" in html    # the source url
    assert "ClinicalTrials.gov" in html                      # source
    assert "trial_record" in html                            # doc_type
    assert claims[0].statement in html                       # the claim it backs


def test_audit_route_404_for_unknown_evidence(client_and_memo):
    client, memo_id = client_and_memo
    assert client.get(f"/memo/{memo_id}/audit/ev_does_not_exist").status_code == 404


def test_export_returns_markdown(client_and_memo):
    client, memo_id = client_and_memo
    resp = client.get(f"/memo/{memo_id}/export.md")
    assert resp.status_code == 200
    assert resp.mimetype == "text/markdown"
    md = resp.get_data(as_text=True)
    assert md.startswith("# KYMR")
    assert "## Mechanism of Action" in md
    assert "selective IRAK4 degrader" in md
    assert "_Sources:" in md  # citation appendix from render_markdown


def test_unknown_memo_404(client_and_memo):
    client, _ = client_and_memo
    assert client.get("/memo/nope").status_code == 404
    assert client.get("/memo/nope/export.md").status_code == 404
