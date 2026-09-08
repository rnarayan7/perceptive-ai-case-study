"""Regenerate the bundled sample figures.

These stand in for figures that would otherwise be scraped from a company's IR deck
(a follow-up, see ``memo/figures/manifest.py``). Run from anywhere:

    python3 -m memo.figures.samples._make_samples

It writes deterministic PNGs next to this file so the pipeline has real images to match
and annotate in tests and demos. The images are intentionally chart-like but synthetic;
they are not real Kymera figures.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _bar_chart(path: Path, title: str, bars, ymax: float, unit: str) -> None:
    """A simple titled bar chart on a white ground."""
    w, h = 640, 420
    img = Image.new("RGB", (w, h), "#ffffff")
    d = ImageDraw.Draw(img)
    title_font, label_font = _font(22), _font(15)

    d.text((24, 18), title, fill="#111827", font=title_font)

    left, right = 70, w - 30
    top, bottom = 70, h - 60
    d.line([(left, top), (left, bottom)], fill="#374151", width=2)
    d.line([(left, bottom), (right, bottom)], fill="#374151", width=2)

    n = len(bars)
    slot = (right - left) / n
    bar_w = slot * 0.55
    for i, (label, value) in enumerate(bars):
        x0 = left + slot * i + (slot - bar_w) / 2
        x1 = x0 + bar_w
        bh = (value / ymax) * (bottom - top)
        y0 = bottom - bh
        d.rectangle([x0, y0, x1, bottom], fill="#2563eb")
        d.text((x0, bottom + 8), label, fill="#374151", font=label_font)
        vtext = f"{value:g}{unit}"
        d.text((x0, y0 - 20), vtext, fill="#111827", font=label_font)

    img.save(path)


def main() -> None:
    _bar_chart(
        HERE / "kymr_kt621_pd.png",
        "KT-621: STAT6 degradation (blood, by dose)",
        [("Placebo", 4), ("Low", 62), ("Mid", 88), ("High", 94)],
        ymax=100,
        unit="%",
    )
    _bar_chart(
        HERE / "kymr_kt474_pd.png",
        "KT-474: IRAK4 knockdown in HS lesions",
        [("Baseline", 0), ("Wk 4", 55), ("Wk 8", 78), ("Wk 12", 85)],
        ymax=100,
        unit="%",
    )
    print("wrote sample figures to", HERE)


if __name__ == "__main__":
    main()
