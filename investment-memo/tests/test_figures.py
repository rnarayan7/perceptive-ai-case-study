"""Tests for the figure insertion + annotation pipeline.

Deterministic and offline: no network, no model. They exercise the annotation engine,
the keyword matcher, and the ``figures_for_claims`` interface the composition engine
calls, against the bundled sample images.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PIL import Image

from memo.analysis.base import Claim, Evidence
from memo.figures import (
    Annotation,
    annotate_image,
    best_figure,
    figures_for_claims,
    resolve_manifest,
)
from memo.figures.manifest import SAMPLES_DIR
from memo.report.render import Figure


def _sample_image() -> Path:
    path = SAMPLES_DIR / "kymr_kt621_pd.png"
    assert path.exists(), "bundled sample image is missing"
    return path


def _claim(statement: str, value: Optional[str] = None) -> Claim:
    """A grounded claim (evidence attached so it mirrors real analysis output)."""
    return Claim(
        statement=statement,
        confidence=0.8,
        rationale="test",
        value=value,
        evidence=[Evidence(
            doc_id="D1", source="test", doc_type="deck",
            url="https://example.com", quote="q",
        )],
    )


# --- annotation engine -------------------------------------------------------

def test_annotate_image_produces_a_different_file(tmp_path):
    src = _sample_image()
    out = tmp_path / "annotated.png"
    result = annotate_image(
        src, out,
        [
            Annotation(kind="box", coords=[500, 105, 588, 360]),
            Annotation(kind="arrow", coords=[300, 60, 540, 95]),
            Annotation(kind="callout", coords=[40, 40], text="94% knockdown"),
        ],
    )

    assert Path(result) == out
    assert out.exists()
    assert out.stat().st_size > 0

    # The annotated image differs from the input (marks were actually drawn).
    assert src.read_bytes() != out.read_bytes()
    with Image.open(src) as a, Image.open(out) as b:
        assert a.size == b.size  # annotation preserves dimensions
        assert list(a.convert("RGB").getdata()) != list(b.convert("RGB").getdata())


def test_annotate_leaves_input_untouched(tmp_path):
    src = _sample_image()
    before = src.read_bytes()
    annotate_image(src, tmp_path / "out.png", [Annotation(kind="box", coords=[10, 10, 50, 50])])
    assert src.read_bytes() == before


def test_annotate_missing_input_raises(tmp_path):
    try:
        annotate_image(tmp_path / "nope.png", tmp_path / "out.png", [])
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError for a missing input image")


# --- keyword matcher ---------------------------------------------------------

def test_matcher_picks_the_asset_specific_figure():
    manifest = resolve_manifest("KYMR")
    assert manifest is not None and len(manifest) >= 2

    # A claim mentioning KT-621/STAT6 should match the KT-621 figure, not the KT-474 one.
    claim = _claim("KT-621 achieves 94% STAT6 degradation at the high dose.")
    match = best_figure(claim, list(manifest.figures))
    assert match is not None
    assert match.figure_id == "kymr-kt621-stat6-pd"


def test_matcher_handles_dehyphenated_asset_code():
    manifest = resolve_manifest("KYMR")
    claim = _claim("KT474 drove deep IRAK4 knockdown in HS lesions.")
    match = best_figure(claim, list(manifest.figures))
    assert match is not None
    assert match.figure_id == "kymr-kt474-irak4-pd"


def test_matcher_returns_none_when_nothing_matches():
    manifest = resolve_manifest("KYMR")
    claim = _claim("The company reported cash runway into 2027.")
    assert best_figure(claim, list(manifest.figures)) is None


# --- figures_for_claims interface -------------------------------------------

def test_figures_for_claims_returns_annotated_report_figure(tmp_path):
    from memo.ingestion.base import Storage

    storage = Storage(root=tmp_path)
    claims = [
        _claim("KT-621 achieves 94% STAT6 degradation at the high dose.", value="94%"),
        _claim("KT-474 produced 85% IRAK4 knockdown in HS lesions by week 12."),
    ]
    figures = figures_for_claims("KYMR", claims, storage=storage)

    assert len(figures) == 2
    for fig in figures:
        assert isinstance(fig, Figure)
        assert fig.figure_id
        assert fig.caption
        assert fig.image_ref and Path(fig.image_ref).exists()
        assert Path(fig.image_ref).stat().st_size > 0

    # Right figure went to the right claim.
    assert figures[0].figure_id == "kymr-kt621-stat6-pd"
    assert figures[1].figure_id == "kymr-kt474-irak4-pd"

    # The rendered image is annotated (differs from the source it came from).
    src = SAMPLES_DIR / "kymr_kt621_pd.png"
    assert src.read_bytes() != Path(figures[0].image_ref).read_bytes()


def test_figures_for_claims_no_match_returns_empty(tmp_path):
    from memo.ingestion.base import Storage

    storage = Storage(root=tmp_path)
    claims = [_claim("Cash runway extends into 2027 with no financing needed.")]
    assert figures_for_claims("KYMR", claims, storage=storage) == []


def test_figures_for_claims_unknown_company_returns_empty():
    assert figures_for_claims("NOPE", [_claim("KT-621 STAT6 degrader")]) == []


def test_figures_for_claims_empty_claims_returns_empty():
    assert figures_for_claims("KYMR", []) == []


# --- offline figure ingestion (memo.figures.ingest) --------------------------

def test_normalize_box_clamps_and_orders():
    from memo.figures.ingest import _normalize_box

    # Out-of-range and reversed corners are clamped and reordered.
    assert _normalize_box([620, 405, 583, 187], 720, 460) == [583, 187, 620, 405]
    assert _normalize_box([-10, -10, 800, 500], 720, 460) == [0, 0, 720, 460]
    # Degenerate / malformed boxes are rejected.
    assert _normalize_box([100, 100, 101, 101], 720, 460) is None
    assert _normalize_box([1, 2, 3], 720, 460) is None
    assert _normalize_box("nope", 720, 460) is None


def test_extract_json_tolerates_prose_and_fences():
    from memo.figures.ingest import _extract_json

    assert _extract_json('here it is: {"box": [1, 2, 3, 4]} done') == {"box": [1, 2, 3, 4]}
    assert _extract_json("no json here") is None


def test_ingest_company_offline_writes_manifest_and_annotated(tmp_path):
    """The --no-vlm path produces a manifest + annotated previews with no model."""
    from memo.figures.ingest import ingest_company

    summary = ingest_company("ABVX", data_root=tmp_path, use_vlm=False)
    assert summary["figures"] == 2
    assert summary["vlm_reads"] == 0  # fallback regions, no model call

    fig_dir = tmp_path / "ABVX" / "figures"
    manifest = json.loads((fig_dir / "manifest.json").read_text())
    assert manifest["company"] == "ABVX"
    assert manifest["region_source"] == "fallback"
    assert len(manifest["figures"]) == 2
    for entry in manifest["figures"]:
        # Synthetic stand-ins must be labeled as such (as KYMR does).
        assert entry["synthetic"] is True
        assert (fig_dir / entry["image"]).exists()
        annotated = fig_dir / "annotated" / f"{entry['figure_id']}.png"
        assert annotated.exists() and annotated.stat().st_size > 0


def test_ingested_manifest_is_picked_up_by_pipeline(tmp_path):
    """A memo compose reads the ingested manifest off disk and annotates from it."""
    from memo.figures.ingest import ingest_company
    from memo.ingestion.base import Storage

    ingest_company("ABVX", data_root=tmp_path, use_vlm=False)
    storage = Storage(root=tmp_path)

    manifest = resolve_manifest("ABVX", storage=storage)
    assert manifest is not None and len(manifest) == 2

    claim = _claim(
        "Obefazimod delivered a pooled +16.4% placebo-adjusted clinical remission "
        "at Week 8 in the ABTECT induction trials.",
        value="16.4%",
    )
    figures = figures_for_claims("ABVX", [claim], storage=storage)
    # Each distinct figure is inserted at most once (no per-claim duplication).
    assert figures
    assert len(figures) == len({f.figure_id for f in figures})
    assert "abvx-obefazimod-abtect-induction-remission" in {f.figure_id for f in figures}
    for fig in figures:
        assert Path(fig.image_ref).exists()
