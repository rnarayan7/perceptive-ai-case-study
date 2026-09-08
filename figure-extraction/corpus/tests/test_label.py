"""Tests for the labeling core: verified-value writing and VLM suggestions."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import corpus.label as label
from corpus.label import set_labels


def _write_record(tmp: Path, ground_truth):
    rec = {"source": "test", "figure_id": "f1", "figure_type": "forest",
           "ground_truth": ground_truth}
    p = tmp / "record.json"
    p.write_text(json.dumps(rec))
    return p


def test_set_labels_writes_verified_manual():
    with tempfile.TemporaryDirectory() as d:
        p = _write_record(Path(d), [])
        n = set_labels(p, [{"quantity": "median PFS", "value": "5.5 months"}])
        assert n == 1
        gt = json.loads(p.read_text())["ground_truth"]
        assert gt[0]["method"] == "manual" and gt[0]["verified"] is True and gt[0]["value"] == "5.5 months"


def test_set_labels_replaces_prior_manual_and_keeps_candidates():
    with tempfile.TemporaryDirectory() as d:
        p = _write_record(Path(d), [
            {"quantity": "hr", "value": "0.48", "method": "regex_candidate", "verified": False},
            {"quantity": "old", "value": "1.0", "method": "manual", "verified": True},
        ])
        set_labels(p, [{"quantity": "hr", "value": "0.50 (95% CI 0.33-0.72)"}])
        gt = json.loads(p.read_text())["ground_truth"]
        methods = sorted(g["method"] for g in gt)
        assert methods == ["manual", "regex_candidate"]  # old manual gone, candidate kept
        manual = next(g for g in gt if g["method"] == "manual")
        assert manual["value"].startswith("0.50") and manual["verified"] is True


def test_set_labels_skips_empty_values():
    with tempfile.TemporaryDirectory() as d:
        p = _write_record(Path(d), [])
        n = set_labels(p, [{"quantity": "x", "value": "  "}, {"quantity": "y", "value": "3"}])
        assert n == 1


class _FakeClient:
    def __init__(self, reply=None, raises=False):
        self._reply, self._raises = reply, raises

    def complete(self, prompt, image=None, media_type="image/png", max_tokens=1024):
        if self._raises:
            raise RuntimeError("no key")
        return self._reply


def test_vlm_suggestions_parses_reply():
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "image.png"
        img.write_bytes(b"\x89PNG stub")
        client = _FakeClient(reply='[{"quantity":"HR","value":"0.48 (95% CI 0.33-0.72)"}]')
        out = label.vlm_suggestions(img, "forest", "", client)
        assert len(out) == 1 and out[0]["value"].startswith("0.48")
        assert out[0]["source_text"] == "VLM suggestion"


def test_vlm_suggestions_degrades_on_failure():
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "image.png"
        img.write_bytes(b"stub")
        assert label.vlm_suggestions(img, "forest", "", _FakeClient(raises=True)) == []


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
