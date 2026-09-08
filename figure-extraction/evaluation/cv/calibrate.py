"""Pixel <-> data-coordinate calibration.

An axis is defined by two reference points (a pixel position and the data value
there). :class:`LinearAxis` maps linearly; :class:`LogAxis` maps linearly in
log10 space, for log-scaled axes like the PK plot. :class:`AxisCalibration`
bundles an x and a y axis so a measurement in pixels becomes a value in data
units, with the uncertainty from one pixel of slop reported alongside.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class LinearAxis:
    """Two anchors (pixel, value) define a linear pixel<->value mapping."""

    px1: float
    val1: float
    px2: float
    val2: float

    def to_value(self, px: float) -> float:
        return self.val1 + (px - self.px1) * (self.val2 - self.val1) / (self.px2 - self.px1)

    def to_pixel(self, value: float) -> float:
        return self.px1 + (value - self.val1) * (self.px2 - self.px1) / (self.val2 - self.val1)

    def value_per_pixel(self) -> float:
        return abs((self.val2 - self.val1) / (self.px2 - self.px1))


@dataclass(frozen=True)
class LogAxis:
    """Linear in log10(value): anchors give the pixel for two known values."""

    px1: float
    val1: float
    px2: float
    val2: float

    def _lin(self) -> LinearAxis:
        return LinearAxis(self.px1, math.log10(self.val1), self.px2, math.log10(self.val2))

    def to_value(self, px: float) -> float:
        return 10 ** self._lin().to_value(px)

    def to_pixel(self, value: float) -> float:
        return self._lin().to_pixel(math.log10(value))

    def fold_per_pixel(self) -> float:
        """The multiplicative uncertainty from one pixel (a fold-factor)."""
        return 10 ** self._lin().value_per_pixel()


@dataclass(frozen=True)
class AxisCalibration:
    """An x and y axis together, mapping a pixel to a data point."""

    x_axis: object  # LinearAxis or LogAxis
    y_axis: object

    def to_data(self, px: float, py: float) -> Tuple[float, float]:
        return self.x_axis.to_value(px), self.y_axis.to_value(py)

    def y_uncertainty(self) -> float:
        """One-pixel uncertainty on the y read (value units, or fold for log)."""
        if isinstance(self.y_axis, LogAxis):
            return self.y_axis.fold_per_pixel()
        return self.y_axis.value_per_pixel()
