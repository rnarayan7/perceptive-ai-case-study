"""Image annotation engine.

The memo does not just insert a figure at the claim it supports; it *marks* the
feature the argument depends on so a reader sees, on the image itself, why the figure
is there. This module draws three primitives on an image with Pillow:

- an **arrow** pointing at a feature,
- a **box** framing a region, and
- a **callout** label (text on a filled background, optionally with a pointer).

Everything is deterministic: a fixed default font (Pillow's built-in bitmap font, so
no font files are needed), integer coordinates, no randomness. :func:`annotate_image`
opens a source image, applies a list of :class:`Annotation` marks, and writes a new
file, leaving the input untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

# A high-contrast red reads as an annotation over most slide/chart backgrounds.
DEFAULT_COLOR = "#e02424"

Coords = Sequence[float]
PathLike = Union[str, Path]


@dataclass
class Annotation:
    """One mark to draw on a figure.

    ``kind`` selects the primitive and dictates how ``coords`` is read:

    - ``"box"``   -> ``(x0, y0, x1, y1)`` rectangle corners.
    - ``"arrow"`` -> ``(x0, y0, x1, y1)`` tail then head; the arrowhead sits at (x1, y1).
    - ``"callout"`` -> ``(x, y)`` top-left of the label; if ``point_to`` is set an arrow
      is drawn from the label to that point.

    ``text`` labels a callout (ignored by box/arrow). ``color`` overrides the default.
    """

    kind: str
    coords: Coords
    text: str = ""
    color: Optional[str] = None
    width: int = 4
    point_to: Optional[Tuple[float, float]] = None

    def __post_init__(self) -> None:
        kind = self.kind.lower()
        if kind not in ("box", "arrow", "callout"):
            raise ValueError(f"unknown annotation kind: {self.kind!r}")
        self.kind = kind


def _font(size: int = 16) -> ImageFont.ImageFont:
    """Load a truetype font at ``size`` if the platform has one, else the bitmap font.

    The bitmap fallback ignores size but is always present, keeping annotation possible
    on a bare environment with no font files.
    """
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def draw_box(
    draw: ImageDraw.ImageDraw,
    coords: Coords,
    color: str = DEFAULT_COLOR,
    width: int = 4,
) -> None:
    """Draw a rectangle outline around ``(x0, y0, x1, y1)`` (corners in any order)."""
    x0, y0, x1, y1 = coords
    box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    draw.rectangle(box, outline=color, width=max(1, int(width)))


def draw_arrow(
    draw: ImageDraw.ImageDraw,
    coords: Coords,
    color: str = DEFAULT_COLOR,
    width: int = 4,
) -> None:
    """Draw a line from the tail ``(x0, y0)`` to the head ``(x1, y1)`` with an arrowhead."""
    x0, y0, x1, y1 = (float(v) for v in coords)
    width = max(1, int(width))
    draw.line([(x0, y0), (x1, y1)], fill=color, width=width)

    # Arrowhead: two barbs swept back from the tip along the line's heading.
    angle = math.atan2(y1 - y0, x1 - x0)
    head = max(12.0, width * 4.0)
    spread = math.radians(28)
    left = (x1 - head * math.cos(angle - spread), y1 - head * math.sin(angle - spread))
    right = (x1 - head * math.cos(angle + spread), y1 - head * math.sin(angle + spread))
    draw.polygon([(x1, y1), left, right], fill=color)


def draw_callout(
    draw: ImageDraw.ImageDraw,
    coords: Coords,
    text: str,
    color: str = DEFAULT_COLOR,
    width: int = 4,
    point_to: Optional[Tuple[float, float]] = None,
    font: Optional[ImageFont.ImageFont] = None,
) -> None:
    """Draw a filled label with ``text`` at ``(x, y)``, optionally pointing at a feature."""
    x, y = (float(v) for v in coords)
    font = font or _font()
    pad = 6

    # Measure the text box (textbbox is available across modern Pillow versions).
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        tw, th = right - left, bottom - top
    except AttributeError:  # very old Pillow
        tw, th = draw.textsize(text, font=font)

    if point_to is not None:
        # Arrow from the label's edge to the feature, drawn first so the box sits on top.
        anchor = (x + tw / 2 + pad, y + th / 2 + pad)
        draw_arrow(draw, (anchor[0], anchor[1], point_to[0], point_to[1]), color=color, width=width)

    rect = [x, y, x + tw + 2 * pad, y + th + 2 * pad]
    draw.rectangle(rect, fill=color)
    draw.text((x + pad, y + pad), text, fill="#ffffff", font=font)


def _apply(draw: ImageDraw.ImageDraw, ann: Annotation, font: ImageFont.ImageFont) -> None:
    color = ann.color or DEFAULT_COLOR
    if ann.kind == "box":
        draw_box(draw, ann.coords, color=color, width=ann.width)
    elif ann.kind == "arrow":
        draw_arrow(draw, ann.coords, color=color, width=ann.width)
    else:  # callout
        draw_callout(
            draw, ann.coords, ann.text, color=color, width=ann.width,
            point_to=ann.point_to, font=font,
        )


def annotate_image(
    input_path: PathLike,
    output_path: PathLike,
    annotations: Sequence[Annotation],
    font_size: int = 16,
) -> str:
    """Apply ``annotations`` to the image at ``input_path`` and save to ``output_path``.

    Returns the output path as a string. The input file is never modified; the output
    directory is created if needed. Raises ``FileNotFoundError`` if the input is missing.
    """
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"no such image: {src}")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as image:
        canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    font = _font(font_size)
    for ann in annotations:
        _apply(draw, ann, font)
    canvas.save(out)
    return str(out)
