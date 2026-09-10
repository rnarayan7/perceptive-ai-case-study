"""Offline figure ingestion: process a company's figures into the data layer.

This is an *ingestion* step, run ahead of time, not at memo runtime. For each
registered figure it:

1. writes the source image to ``data/<COMPANY>/figures/sources/`` (a real image
   fetched offline, or a clearly-labeled synthetic stand-in when no clean public
   figure is available),
2. reads the figure with a vision model once to derive the region the claim
   depends on, and caches that region into the manifest (deterministic at compose
   time: the model is never called during a memo run),
3. pre-renders an annotated preview into ``data/<COMPANY>/figures/annotated/``, and
4. writes ``data/<COMPANY>/figures/manifest.json``.

The memo pipeline reads that manifest and the annotated PNGs off disk at the next
compose (see :mod:`memo.figures.pipeline`), so nothing here happens live. The
output contract the composed memo depends on is unchanged: a company manifest of
``figure_id / image / caption / source_url / keywords / region`` and annotated
PNGs under ``data/<COMPANY>/figures/annotated/``.

Run it::

    python -m memo.figures.ingest --company ABVX
    python -m memo.figures.ingest --company all --no-vlm   # skip the model read

``--no-vlm`` (or a missing ``ANTHROPIC_API_KEY``) falls back to each figure's
hand-seeded region, so ingestion always produces a manifest even offline.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from memo.figures.annotate import Annotation, annotate_image

logger = logging.getLogger("memo.figures.ingest")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
# The capability tier the case study standardizes on. Never opus; never mocked.
DEFAULT_MODEL = os.environ.get("CLAUDE_FIGURE_MODEL", "claude-sonnet-5")


# --------------------------------------------------------------------------- model

class VisionClient:
    """Minimal vision-capable Anthropic client (stdlib only).

    A separate, tiny client keeps this module dependency-free and self-contained;
    credentials come from ``ANTHROPIC_API_KEY`` at run time. Only used offline
    during ingestion to read a region off a figure, never at memo runtime.
    """

    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None,
                 timeout: float = 180.0, max_retries: int = 3) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.timeout = timeout
        self.max_retries = max_retries

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, prompt: str, image: bytes, media_type: str = "image/png",
                 max_tokens: int = 512) -> str:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.b64encode(image).decode("ascii")}},
            {"type": "text", "text": prompt},
        ]
        body = json.dumps({"model": self.model, "max_tokens": max_tokens,
                           "messages": [{"role": "user", "content": content}]}).encode("utf-8")
        req = urllib.request.Request(
            ANTHROPIC_URL, data=body, method="POST",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
        )
        last: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read())
                    parts = payload.get("content") or []
                    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and 400 <= exc.code < 500:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"Anthropic API {exc.code}: {detail}") from exc
                last = exc
            except (socket.timeout, TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                last = exc
            if attempt < self.max_retries:
                time.sleep(min(2.0 ** attempt, 20.0))
        raise RuntimeError(f"Anthropic API call failed after {self.max_retries} attempts: {last}")


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_box(box: object, w: int, h: int) -> Optional[List[int]]:
    """Clamp a raw ``[x0, y0, x1, y1]`` to the image, or None if unusable."""
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (int(round(float(v))) for v in box)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((max(0, min(x0, w)), max(0, min(x1, w))))
    y0, y1 = sorted((max(0, min(y0, h)), max(0, min(y1, h))))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return [x0, y0, x1, y1]


def read_feature_region(
    image_path: Path,
    feature: str,
    client: VisionClient,
) -> Optional[Dict[str, object]]:
    """Ask the model for a tight bounding box around ``feature`` on the figure.

    Returns a manifest region ``{"kind": "box", "coords": [x0, y0, x1, y1], ...}``
    read from the actual pixels, or ``None`` if the read fails or is out of range
    (so the caller falls back to a hand-seeded region).
    """
    with Image.open(image_path) as img:
        w, h = img.size
    data = image_path.read_bytes()
    media = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    prompt = (
        f"This is a chart image, {w} pixels wide and {h} pixels tall. The origin "
        "(0,0) is the top-left corner; x increases to the right, y increases "
        "downward. Locate this feature on the chart:\n"
        f"  {feature}\n"
        "Reply with ONLY a JSON object giving a tight pixel bounding box around "
        'that feature: {"box": [x0, y0, x1, y1]} with 0 <= x0 < x1 <= '
        f"{w} and 0 <= y0 < y1 <= {h}. No prose."
    )
    try:
        reply = client.complete(prompt, image=data, media_type=media)
    except Exception as exc:  # noqa: BLE001 - a failed read falls back, never aborts
        logger.warning("region read failed for %s: %s", image_path.name, exc)
        return None
    parsed = _extract_json(reply)
    coords = _normalize_box(parsed.get("box") if parsed else None, w, h)
    if coords is None:
        logger.warning("region read returned no usable box for %s: %r", image_path.name, reply[:120])
        return None
    return {"kind": "box", "coords": coords, "text": "", "method": "vlm_read"}


# --------------------------------------------------------------------------- charts

def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def build_bar_chart(path: Path, title: str, bars: List[Tuple[str, float]],
                    ymax: float, unit: str, subtitle: str = "") -> None:
    """Render a titled bar chart to ``path``. Deterministic, synthetic stand-in."""
    w, h = 720, 460
    img = Image.new("RGB", (w, h), "#ffffff")
    d = ImageDraw.Draw(img)
    title_font, sub_font, label_font = _font(24), _font(15), _font(16)

    d.text((28, 18), title, fill="#0f172a", font=title_font)
    top_pad = 56
    if subtitle:
        d.text((28, 48), subtitle, fill="#64748b", font=sub_font)
        top_pad = 78

    left, right = 84, w - 36
    top, bottom = top_pad, h - 74
    d.line([(left, top), (left, bottom)], fill="#334155", width=2)
    d.line([(left, bottom), (right, bottom)], fill="#334155", width=2)

    n = max(1, len(bars))
    slot = (right - left) / n
    bar_w = slot * 0.5
    for i, (label, value) in enumerate(bars):
        x0 = left + slot * i + (slot - bar_w) / 2
        x1 = x0 + bar_w
        bh = (value / ymax) * (bottom - top) if ymax else 0
        y0 = bottom - bh
        d.rectangle([x0, y0, x1, bottom], fill="#2563eb")
        for j, line in enumerate(label.split("\n")):
            d.text((x0 - 6, bottom + 8 + j * 16), line, fill="#334155", font=label_font)
        d.text((x0, y0 - 22), f"{value:g}{unit}", fill="#0f172a", font=label_font)

    img.save(path)


# --------------------------------------------------------------------------- specs

@dataclass
class FigureSpec:
    """One figure to ingest for a company."""

    figure_id: str
    caption: str
    source_url: str
    keywords: List[str]
    feature: str  # what the model should locate; also the annotation intent
    # A builder renders a synthetic stand-in; a source_image points at a real file.
    builder: Optional[Callable[[Path], None]] = None
    source_image: Optional[Path] = None
    synthetic: bool = True
    note: str = ""
    # Hand-seeded fallback region used when the model read is unavailable/invalid.
    fallback_region: Optional[Dict[str, object]] = field(default=None)


@dataclass
class CompanyFigures:
    company: str
    note: str
    figures: List[FigureSpec]


def _abvx() -> CompanyFigures:
    """Abivax (obefazimod, oral miR-124 enhancer) in ulcerative colitis.

    No clean public chart image is downloadable (the ABTECT results are disclosed
    as press-release tables), so these are synthetic stand-ins built from the real
    reported placebo-adjusted clinical remission numbers, labeled synthetic and
    sourced to the Abivax releases.
    """
    induction = FigureSpec(
        figure_id="abvx-obefazimod-abtect-induction-remission",
        caption=("Obefazimod Phase 3 ABTECT induction: Week-8 placebo-adjusted clinical "
                 "remission (50 mg once daily). ABTECT-1 +19.3% (p<0.0001), ABTECT-2 "
                 "+13.4% (p=0.0001), pooled +16.4% (p<0.0001)."),
        source_url="https://ir.abivax.com/news-releases/news-release-details/abivax-announces-positive-phase-3-results-both-abtect-8-week/",
        keywords=["obefazimod", "ABX464", "clinical remission", "remission", "induction",
                  "ulcerative colitis", "UC", "week 8", "ABTECT", "50 mg", "placebo-adjusted",
                  "miR-124", "primary endpoint"],
        feature=("the pooled Week-8 placebo-adjusted clinical remission bar, labeled "
                 "'Pooled' with value 16.4%"),
        builder=lambda p: build_bar_chart(
            p,
            "Obefazimod ABTECT induction (Week 8)",
            [("ABTECT-1", 19.3), ("ABTECT-2", 13.4), ("Pooled", 16.4)],
            ymax=25, unit="%",
            subtitle="Placebo-adjusted clinical remission, 50 mg QD (synthetic stand-in of reported values)",
        ),
        note="Synthetic stand-in chart of the reported ABTECT induction remission deltas.",
        fallback_region={"kind": "box", "coords": [530, 150, 620, 405], "text": ""},
    )
    maintenance = FigureSpec(
        figure_id="abvx-obefazimod-abtect-maintenance-remission",
        caption=("Obefazimod Phase 3 ABTECT maintenance: Week-44 placebo-adjusted clinical "
                 "remission. 25 mg +39.3%, 50 mg +40.3%; primary endpoint met at both doses."),
        source_url="https://ir.abivax.com/news-releases/news-release-details/abivax-announces-landmark-phase-3-abtect-maintenance-trial/",
        keywords=["obefazimod", "ABX464", "clinical remission", "remission", "maintenance",
                  "ulcerative colitis", "UC", "week 44", "ABTECT", "25 mg", "50 mg",
                  "placebo-adjusted", "durability"],
        feature=("the 50 mg Week-44 placebo-adjusted clinical remission bar, labeled "
                 "'50 mg' with value 40.3%"),
        builder=lambda p: build_bar_chart(
            p,
            "Obefazimod ABTECT maintenance (Week 44)",
            [("25 mg", 39.3), ("50 mg", 40.3)],
            ymax=50, unit="%",
            subtitle="Placebo-adjusted clinical remission (synthetic stand-in of reported values)",
        ),
        note="Synthetic stand-in chart of the reported ABTECT maintenance remission deltas.",
        fallback_region={"kind": "box", "coords": [430, 120, 560, 405], "text": ""},
    )
    return CompanyFigures(
        company="ABVX",
        note=("Synthetic stand-in figures for obefazimod (ABX464). Built from Abivax's "
              "reported ABTECT placebo-adjusted clinical remission numbers; no clean public "
              "chart image was available to fetch. Regions are model-derived at ingestion."),
        figures=[induction, maintenance],
    )


def _cogt() -> CompanyFigures:
    fig = FigureSpec(
        figure_id="cogt-bezuclastinib-summit-tss",
        caption=("Bezuclastinib Phase 2 SUMMIT (nonadvanced systemic mastocytosis): mean "
                 "reduction in total symptom score (TSS) vs placebo at the primary readout."),
        source_url="https://www.cogentbio.com/",
        keywords=["bezuclastinib", "CGT9486", "SUMMIT", "systemic mastocytosis", "TSS",
                  "total symptom score", "KIT", "D816V", "nonadvanced"],
        feature="the bezuclastinib arm bar (the larger symptom-score reduction)",
        builder=lambda p: build_bar_chart(
            p,
            "Bezuclastinib SUMMIT (nonadvanced SM)",
            [("Placebo", 15.0), ("Bezuclastinib", 24.3)],
            ymax=30, unit="",
            subtitle="Mean TSS reduction, illustrative synthetic stand-in",
        ),
        note="Illustrative synthetic stand-in; not a transcription of published values.",
        fallback_region={"kind": "box", "coords": [430, 120, 560, 405], "text": ""},
    )
    return CompanyFigures(
        company="COGT",
        note=("Synthetic stand-in figure for bezuclastinib. Illustrative, clearly synthetic; "
              "swap for a real IR figure when one is available. Region is model-derived."),
        figures=[fig],
    )


def _imvt() -> CompanyFigures:
    fig = FigureSpec(
        figure_id="imvt-batoclimab-igg-reduction",
        caption=("Batoclimab (anti-FcRn) dose-dependent serum IgG reduction from baseline, "
                 "the pharmacodynamic basis for the IMVT-1402 program."),
        source_url="https://www.immunovant.com/",
        keywords=["batoclimab", "IMVT-1402", "FcRn", "IgG", "autoantibody", "reduction",
                  "pharmacodynamic", "myasthenia gravis", "Graves"],
        feature="the high-dose IgG-reduction bar (the deepest reduction)",
        builder=lambda p: build_bar_chart(
            p,
            "Batoclimab: serum IgG reduction by dose",
            [("Placebo", 4.0), ("Low", 41.0), ("Mid", 65.0), ("High", 78.0)],
            ymax=100, unit="%",
            subtitle="IgG reduction from baseline, illustrative synthetic stand-in",
        ),
        note="Illustrative synthetic stand-in; not a transcription of published values.",
        fallback_region={"kind": "box", "coords": [560, 120, 660, 405], "text": ""},
    )
    return CompanyFigures(
        company="IMVT",
        note=("Synthetic stand-in figure for batoclimab/IMVT-1402. Illustrative, clearly "
              "synthetic. Region is model-derived at ingestion."),
        figures=[fig],
    )


def _prax() -> CompanyFigures:
    fig = FigureSpec(
        figure_id="prax-ulixacaltamide-essential-tremor",
        caption=("Ulixacaltamide (PRAX-944) in essential tremor: improvement in the composite "
                 "tremor score vs placebo (Essential3 program)."),
        source_url="https://www.praxismedicines.com/",
        keywords=["ulixacaltamide", "PRAX-944", "essential tremor", "Essential3", "T-type",
                  "calcium channel", "tremor", "composite score", "TETRAS"],
        feature="the ulixacaltamide arm bar (the larger tremor-score improvement)",
        builder=lambda p: build_bar_chart(
            p,
            "Ulixacaltamide in essential tremor",
            [("Placebo", 12.0), ("Ulixacaltamide", 27.0)],
            ymax=35, unit="%",
            subtitle="Composite tremor-score improvement, illustrative synthetic stand-in",
        ),
        note="Illustrative synthetic stand-in; not a transcription of published values.",
        fallback_region={"kind": "box", "coords": [430, 120, 560, 405], "text": ""},
    )
    return CompanyFigures(
        company="PRAX",
        note=("Synthetic stand-in figure for ulixacaltamide. Illustrative, clearly synthetic. "
              "Region is model-derived at ingestion."),
        figures=[fig],
    )


#: Companies this ingester knows how to seed. ABVX is the reference company.
REGISTRY: Dict[str, Callable[[], CompanyFigures]] = {
    "ABVX": _abvx,
    "COGT": _cogt,
    "IMVT": _imvt,
    "PRAX": _prax,
}


# --------------------------------------------------------------------------- run

def _region_to_annotation(region: Dict[str, object], image_size: Tuple[int, int]) -> Annotation:
    kind = str(region.get("kind", "box")).lower()
    coords = region.get("coords")
    text = str(region.get("text", "") or "")
    point_to = region.get("point_to")
    if point_to is not None:
        point_to = tuple(point_to)  # type: ignore[arg-type]
    if coords is None:
        w, h = image_size
        coords = [int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)]
    return Annotation(kind=kind, coords=coords, text=text, point_to=point_to)  # type: ignore[arg-type]


def ingest_company(
    company: str,
    data_root: Path = Path("data"),
    use_vlm: bool = True,
    model: str = DEFAULT_MODEL,
) -> Dict[str, object]:
    """Process one company's figures into ``data/<COMPANY>/figures/``.

    Writes source images, a manifest, and pre-rendered annotated previews. When
    ``use_vlm`` and a key are present, the annotation region is read from the image
    once and cached into the manifest; otherwise the hand-seeded fallback is used.
    Returns a summary dict.
    """
    company = company.upper()
    if company not in REGISTRY:
        raise KeyError(f"no figure spec registered for {company!r}")
    spec = REGISTRY[company]()

    fig_dir = Path(data_root) / company / "figures"
    sources_dir = fig_dir / "sources"
    annotated_dir = fig_dir / "annotated"
    for d in (sources_dir, annotated_dir):
        d.mkdir(parents=True, exist_ok=True)

    client: Optional[VisionClient] = None
    if use_vlm and VisionClient.available():
        client = VisionClient(model=model)
    elif use_vlm:
        logger.warning("ANTHROPIC_API_KEY not set; falling back to hand-seeded regions")

    manifest_figures: List[dict] = []
    reads_provenance: List[dict] = []
    for fig in spec.figures:
        # 1. materialize the source image
        src_path = sources_dir / f"{fig.figure_id}.png"
        if fig.builder is not None:
            fig.builder(src_path)
        elif fig.source_image is not None:
            Image.open(fig.source_image).convert("RGB").save(src_path)
        else:
            raise ValueError(f"{fig.figure_id}: no builder or source_image")

        with Image.open(src_path) as img:
            size = img.size

        # 2. derive the annotation region by reading the image (cached to manifest)
        region: Optional[Dict[str, object]] = None
        read_method = "fallback"
        if client is not None:
            region = read_feature_region(src_path, fig.feature, client)
            if region is not None:
                read_method = "vlm_read"
        if region is None:
            region = dict(fig.fallback_region) if fig.fallback_region else {
                "kind": "box", "coords": None, "text": ""}
        reads_provenance.append({
            "figure_id": fig.figure_id, "feature": fig.feature,
            "method": read_method, "region": region,
        })

        # 3. pre-render an annotated preview into the annotated/ layout
        annotation = _region_to_annotation(region, size)
        preview = annotated_dir / f"{fig.figure_id}.png"
        annotate_image(src_path, preview, [annotation])

        # 4. manifest entry (image path relative to the manifest dir)
        manifest_figures.append({
            "figure_id": fig.figure_id,
            "image": f"sources/{src_path.name}",
            "caption": fig.caption,
            "source_url": fig.source_url,
            "keywords": fig.keywords,
            "region": region,
            "synthetic": fig.synthetic,
            "note": fig.note,
            "region_method": read_method,
        })

    manifest = {
        "company": company,
        "note": spec.note,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "region_source": "vlm_read" if client is not None else "fallback",
        "figures": manifest_figures,
    }
    (fig_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (fig_dir / "region_reads.json").write_text(json.dumps(reads_provenance, indent=2))

    return {
        "company": company,
        "figures": len(manifest_figures),
        "vlm_reads": sum(1 for r in reads_provenance if r["method"] == "vlm_read"),
        "manifest": str(fig_dir / "manifest.json"),
        "annotated_dir": str(annotated_dir),
    }


def _load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="memo.figures.ingest", description=__doc__)
    parser.add_argument("--company", action="append", required=True,
                        help="Company ticker (repeatable), or 'all'.")
    parser.add_argument("--data-root", default="data", help="Data layer root (default: data).")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Vision model for the region read.")
    parser.add_argument("--no-vlm", action="store_true",
                        help="Skip the model read; use hand-seeded fallback regions.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    _load_dotenv()

    requested = {c.upper() for c in args.company}
    companies = sorted(REGISTRY) if "ALL" in requested else [c.upper() for c in args.company]

    for company in companies:
        summary = ingest_company(company, data_root=Path(args.data_root),
                                  use_vlm=not args.no_vlm, model=args.model)
        print(f"{summary['company']}: {summary['figures']} figure(s), "
              f"{summary['vlm_reads']} model-derived region(s) -> {summary['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
