"""Tests for the label server's data logic (no HTTP needed).

Points the server's module globals at a temp corpus, then checks index building
and the JSON payloads the handler returns.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import corpus.label as label
import corpus.label_server as srv
from corpus.base import FigureRecord, FigureStorage, FigureType, GroundTruthValue


def _seed(tmp: Path) -> FigureStorage:
    storage = FigureStorage(root=tmp)
    storage.write_figure(FigureRecord(
        source="pmc", figure_id="PMC1_fig1", figure_type=FigureType.FOREST,
        title="A forest plot", context="benefit HR 0.48", image_ext="png",
        image_bytes=b"\x89PNG\r\n\x1a\nDATA",
        ground_truth=[
            GroundTruthValue(quantity="hr.idh", value="0.5", method="manual", verified=True),
            GroundTruthValue(quantity="hr (candidate)", value="0.48", method="regex_candidate"),
        ],
    ))
    return storage


def test_build_index_and_detail():
    with tempfile.TemporaryDirectory() as d:
        srv.STORAGE = _seed(Path(d))
        srv.build_index(None, None)
        assert [f["id"] for f in srv.INDEX] == ["PMC1_fig1"]

        detail = srv._figure_detail("PMC1_fig1")
        assert detail["type"] == FigureType.FOREST
        assert detail["rows"] == [{"quantity": "hr.idh", "value": "0.5"}]      # manual only
        assert detail["candidates"] == [{"quantity": "hr (candidate)", "value": "0.48"}]
        assert detail["image_url"] == "/image?id=PMC1_fig1"


def test_list_payload_counts(tmp_state=True):
    with tempfile.TemporaryDirectory() as d:
        srv.STORAGE = _seed(Path(d))
        srv.build_index(None, None)
        label.STATE_PATH = Path(d) / "state.json"  # avoid touching real progress state
        payload = srv._list_payload()
        assert payload["counts"]["total"] == 1
        assert payload["counts"]["verified_values"] == 1  # one manual verified value
        assert payload["figures"][0]["status"] == "todo"


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
