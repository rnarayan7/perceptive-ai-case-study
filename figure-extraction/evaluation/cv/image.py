"""Thin pixel-access wrapper with color-matching helpers for measurement."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple, Union

from evaluation.cv.png_io import RawImage, read_png

RGB = Tuple[int, int, int]


class Image:
    """A decoded image with helpers for color masks and column/row scans."""

    def __init__(self, raw: RawImage) -> None:
        self._raw = raw
        self.width = raw.width
        self.height = raw.height

    @classmethod
    def load(cls, source: Union[str, Path, bytes]) -> "Image":
        return cls(read_png(source))

    def pixel(self, x: int, y: int) -> RGB:
        return self._raw.pixel(x, y)

    @staticmethod
    def matches(pixel: RGB, target: RGB, tol: int) -> bool:
        """True if every channel is within ``tol`` of ``target``."""
        return (abs(pixel[0] - target[0]) <= tol
                and abs(pixel[1] - target[1]) <= tol
                and abs(pixel[2] - target[2]) <= tol)

    def column_matches(self, x: int, target: RGB, tol: int,
                       y0: int = 0, y1: int = -1) -> List[int]:
        """Rows in column ``x`` (within [y0, y1)) whose pixel matches ``target``."""
        y1 = self.height if y1 < 0 else min(y1, self.height)
        return [y for y in range(max(0, y0), y1)
                if self.matches(self._raw.pixel(x, y), target, tol)]

    def row_matches(self, y: int, target: RGB, tol: int,
                    x0: int = 0, x1: int = -1) -> List[int]:
        x1 = self.width if x1 < 0 else min(x1, self.width)
        return [x for x in range(max(0, x0), x1)
                if self.matches(self._raw.pixel(x, y), target, tol)]
