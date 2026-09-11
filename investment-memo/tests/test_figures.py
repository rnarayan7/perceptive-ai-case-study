"""Tests for the figure insertion + annotation pipeline.

Deterministic and offline: no network, no model, no bundled sample images. Fixtures
(a plain PNG and a small in-test manifest) are generated per test, so the suite exercises
the annotation engine, the keyword matcher, and the ``figures_for_claims`` interface the
composition engine calls, without shipping any figure data in the repo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw

from memo.analysis.base import Claim, Evidence
from memo.figures import (
    Annotation,
    annotate_image,
    best_figure,
    figures_for_claims,
    resolve_manifest,
)
from memo.figures.manifest import FigureRecord
from memo.ingestion.base import Storage
from memo.report.render import Figure


def _make_png(path: Path, size: tuple = (240, 140)) -> Path:
    """A small PNG with some content, so an annotation visibly changes its pixels."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, size[0] - 20, size[1] - 20], fill=(200, 210, 230))
    img.save(path)
    return path


def _seed_manifest(root: Path, company: str, figures: List[Dict[str, Any]]) -> None:
    """Write ``<root>/<company>/figures/manifest.json`` + a source PNG per figure."""
    fig_dir = root / company / "figures"
    entries = []
    for fig in figures:
        image = f"{fig['figure_id']}_src.png"
        _make_png(fig_dir / image)
        entries.append({
            "figure_id": fig["figure_id"],
            "image": image,
            "caption": fig["caption"],
            "keywords": fig["keywords"],
            "region": fig.get("region"),
        })
    fig_dir.mkdir(parents=True, exist_ok=True)
    (fig_dir / "manifest.json").write_text(json.dumps({"company": company, "figures": entries}))


def _record(figure_id: str, caption: str, keywords: List[str]) -> FigureRecord:
    return FigureRecord(figure_id=figure_id, image_path=Path("x.png"),
                        caption=caption, keywords=keywords)


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
    src = _make_png(tmp_path / "src.png")
    out = tmp_path / "annotated.png"
    result = annotate_image(
        src, out,
        [
            Annotation(kind="box", coords=[40, 30, 200, 110]),
            Annotation(kind="arrow", coords=[30, 20, 150, 60]),
            Annotation(kind="callout", coords=[10, 10], text="94% knockdown"),
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
    src = _make_png(tmp_path / "src.png")
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

def _two_figures() -> List[FigureRecord]:
    return [
        _record("kt621-stat6-pd", "KT-621 drives dose-dependent STAT6 degradation.",
                ["KT-621", "STAT6", "degradation", "biomarker"]),
        _record("kt474-irak4-pd", "KT-474 produces deep IRAK4 knockdown in HS lesions.",
                ["KT-474", "IRAK4", "knockdown"]),
    ]


def test_matcher_picks_the_asset_specific_figure():
    figures = _two_figures()
    claim = _claim("KT-621 achieves 94% STAT6 degradation at the high dose.")
    match = best_figure(claim, figures)
    assert match is not None and match.figure_id == "kt621-stat6-pd"


def test_matcher_handles_dehyphenated_asset_code():
    figures = _two_figures()
    claim = _claim("KT474 drove deep IRAK4 knockdown in HS lesions.")
    match = best_figure(claim, figures)
    assert match is not None and match.figure_id == "kt474-irak4-pd"


def test_matcher_returns_none_when_nothing_matches():
    claim = _claim("The company reported cash runway into 2027.")
    assert best_figure(claim, _two_figures()) is None


# --- figures_for_claims interface -------------------------------------------

_KYMR_FIGS = [
    {"figure_id": "kt621-stat6-pd", "caption": "KT-621 drives STAT6 degradation.",
     "keywords": ["KT-621", "STAT6", "degradation"]},
    {"figure_id": "kt474-irak4-pd", "caption": "KT-474 IRAK4 knockdown in HS.",
     "keywords": ["KT-474", "IRAK4", "knockdown"]},
]


def test_figures_for_claims_returns_annotated_report_figure(tmp_path):
    _seed_manifest(tmp_path, "KYMR", _KYMR_FIGS)
    storage = Storage(root=tmp_path)
    claims = [
        _claim("KT-621 achieves 94% STAT6 degradation at the high dose.", value="94%"),
        _claim("KT-474 produced 85% IRAK4 knockdown in HS lesions by week 12."),
    ]
    figures = figures_for_claims("KYMR", claims, storage=storage)

    # Each distinct figure is inserted at most once, at the claim it best supports.
    assert figures
    assert len(figures) == len({f.figure_id for f in figures})
    ids = {f.figure_id for f in figures}
    assert "kt621-stat6-pd" in ids and "kt474-irak4-pd" in ids
    for fig in figures:
        assert isinstance(fig, Figure)
        assert fig.caption
        assert fig.image_ref and Path(fig.image_ref).exists()
        assert Path(fig.image_ref).stat().st_size > 0

    # The rendered image is annotated (differs from its source).
    src = tmp_path / "KYMR" / "figures" / "kt621-stat6-pd_src.png"
    annotated = next(f for f in figures if f.figure_id == "kt621-stat6-pd")
    assert src.read_bytes() != Path(annotated.image_ref).read_bytes()


def test_figures_for_claims_no_match_returns_empty(tmp_path):
    _seed_manifest(tmp_path, "KYMR", _KYMR_FIGS)
    storage = Storage(root=tmp_path)
    claims = [_claim("Cash runway extends into 2027 with no financing needed.")]
    assert figures_for_claims("KYMR", claims, storage=storage) == []


def test_figures_for_claims_unknown_company_returns_empty(tmp_path):
    storage = Storage(root=tmp_path)
    assert figures_for_claims("NOPE", [_claim("KT-621 STAT6 degrader")], storage=storage) == []


def test_figures_for_claims_empty_claims_returns_empty(tmp_path):
    _seed_manifest(tmp_path, "KYMR", _KYMR_FIGS)
    storage = Storage(root=tmp_path)
    assert figures_for_claims("KYMR", [], storage=storage) == []


def test_resolve_manifest_none_without_a_seeded_manifest(tmp_path):
    """No bundled fallback: an unseeded company resolves to None."""
    assert resolve_manifest("KYMR", storage=Storage(root=tmp_path)) is None
    assert resolve_manifest("KYMR") is None
