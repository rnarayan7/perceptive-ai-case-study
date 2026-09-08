"""CV tests: PNG round-trip, axis calibration, and waterfall measurement.

The waterfall test generates a synthetic image with KNOWN bar values and checks
the measurement recovers them. This is the honest way to validate CV: exact
ground truth we control, no real-slide calibration guesswork.
"""

from __future__ import annotations

from evaluation.cv.calibrate import LinearAxis, LogAxis
from evaluation.cv.image import Image
from evaluation.cv.png_io import read_png, write_png
from evaluation.cv import waterfall as wf

WHITE = (255, 255, 255)
BAR = (30, 60, 120)


def test_png_roundtrip():
    rgb = bytearray([255, 0, 0, 0, 255, 0, 0, 0, 255, 10, 20, 30, 40, 50, 60, 70, 80, 90])
    img = read_png(write_png(3, 2, rgb))
    assert img.width == 3 and img.height == 2
    assert img.pixel(0, 0) == (255, 0, 0) and img.pixel(2, 1) == (70, 80, 90)


def test_linear_axis_maps_both_ways():
    ax = LinearAxis(px1=200, val1=0, px2=300, val2=-100)  # 1 unit per pixel, downward
    assert abs(ax.to_value(250) - (-50)) < 1e-9
    assert abs(ax.to_pixel(-50) - 250) < 1e-9
    assert abs(ax.value_per_pixel() - 1.0) < 1e-9


def test_log_axis_maps_in_log_space():
    ax = LogAxis(px1=100, val1=100.0, px2=400, val2=0.1)  # 100 -> 0.1 over 300px
    assert abs(ax.to_value(100) - 100.0) < 1e-6
    assert abs(ax.to_value(400) - 0.1) < 1e-6
    mid = ax.to_value(250)  # halfway in log space between 100 and 0.1 is ~3.16
    assert 3.0 < mid < 3.3


def _synth_waterfall(values, baseline_py=200):
    """White image with dark bars of known signed values; 1 unit/pixel."""
    width, height = 400, 320
    rgb = bytearray([255]) * (width * height * 3)

    def put(x, y, color):
        i = (y * width + x) * 3
        rgb[i:i + 3] = bytes(color)

    ax = LinearAxis(px1=baseline_py, val1=0, px2=baseline_py + 100, val2=-100)
    x = 40
    for v in values:
        tip_py = int(round(ax.to_pixel(v)))
        y_lo, y_hi = sorted((baseline_py, tip_py))
        for xx in range(x, x + 12):
            for yy in range(y_lo, y_hi + 1):
                put(xx, yy, BAR)
        x += 24  # bar width 12 + gap 12
    return Image(read_png(write_png(width, height, rgb))), ax


def test_waterfall_recovers_known_values():
    truth = [60, -30, -55, -70, -95]
    image, y_axis = _synth_waterfall(truth)
    bars = wf.measure_waterfall(
        image, bar_color=BAR, baseline_py=200, y_axis=y_axis,
        x_range=(0, 400), y_range=(0, 320), tol=20,
    )
    assert len(bars) == len(truth), f"found {len(bars)} bars, expected {len(truth)}"
    measured = [round(b.value) for b in bars]
    for got, want in zip(measured, truth):
        assert abs(got - want) <= 2, f"bar {got} vs truth {want}"

    # derived quantities the brief asks for
    count, total = wf.proportion_beyond(bars, threshold=-50, below=True)
    assert (count, total) == (3, 5)              # -55, -70, -95 beyond -50
    assert round(wf.deepest(bars)) == -95
    assert round(wf.leftmost(bars).value) == 60


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
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
