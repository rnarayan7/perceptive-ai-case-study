"""Classical CV measurement for the geometric figures.

The second, independent read for figures whose values are geometric (waterfall
bar depths, KM crossings, forest markers, PK curve heights). Pixel measurement
against a calibrated axis, paired with the VLM read so their agreement is the
keystone triage signal.

Dependency-free: a small stdlib PNG codec (:mod:`evaluation.cv.png_io`) reads the
image, and measurement is plain Python. Validated on synthetic images with known
ground truth; applying it to a real slide needs that slide's axis calibration.
"""

from evaluation.cv.calibrate import AxisCalibration, LinearAxis, LogAxis
from evaluation.cv.image import Image
from evaluation.cv.png_io import read_png, write_png

__all__ = ["Image", "read_png", "write_png", "AxisCalibration", "LinearAxis", "LogAxis"]
