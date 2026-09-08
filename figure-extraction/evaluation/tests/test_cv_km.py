"""CV tests: Kaplan-Meier curve tracing, median crossing, and landmark read.

Like the waterfall test, this builds a synthetic KM step curve with a KNOWN
median and a KNOWN survival at a landmark month, then checks the measurement
recovers both. Ground truth we control, no real-slide calibration guesswork. A
censoring tick is drawn on the curve to prove tracing walks past it.
"""

from __future__ import annotations

from evaluation.cv.calibrate import LinearAxis
from evaluation.cv.image import Image
from evaluation.cv.png_io import read_png, write_png
from evaluation.cv import kaplan_meier as km

WHITE = (255, 255, 255)
CURVE = (200, 30, 40)

# Plot region: x = 50px..350px maps to 0..12 months (25 px/month);
# y = 30px..230px maps to survival 1.0..0.0 (200 px per unit).
X_AXIS = LinearAxis(px1=50, val1=0.0, px2=350, val2=12.0)
Y_AXIS = LinearAxis(px1=30, val1=1.0, px2=230, val2=0.0)

# A descending step curve: (month_start, month_end, survival).
# It steps 1.0 -> 0.7 at month 3, then drops through 0.5 to 0.45 at month 5
# (so the median is 5.0), and holds 0.45 across the 6-month landmark.
STEPS = [
    (0, 3, 1.0),
    (3, 5, 0.7),
    (5, 8, 0.45),
    (8, 12, 0.3),
]
TRUE_MEDIAN = 5.0
LANDMARK_MONTH = 6
TRUE_LANDMARK = 0.45


def _synth_km():
    """White image with the STEPS curve drawn as horizontal runs joined by
    vertical drops, plus one censoring tick, in CURVE color."""
    width, height = 400, 260
    rgb = bytearray([255]) * (width * height * 3)

    def put(x, y, color):
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 3
            rgb[i:i + 3] = bytes(color)

    prev_py = None
    for m0, m1, surv in STEPS:
        x_start = int(round(X_AXIS.to_pixel(m0)))
        x_end = int(round(X_AXIS.to_pixel(m1)))
        py = int(round(Y_AXIS.to_pixel(surv)))
        # vertical drop from the previous segment's level to this one
        if prev_py is not None:
            lo, hi = sorted((prev_py, py))
            for yy in range(lo, hi + 1):
                put(x_start, yy, CURVE)
        # horizontal segment
        for xx in range(x_start, x_end + 1):
            put(xx, py, CURVE)
            put(xx, py + 1, CURVE)  # 2px thick line
        prev_py = py

    # censoring tick at month 6.5: a short vertical mark straddling the line.
    tick_x = int(round(X_AXIS.to_pixel(6.5)))
    tick_py = int(round(Y_AXIS.to_pixel(0.45)))
    for yy in range(tick_py - 5, tick_py + 6):
        put(tick_x, yy, CURVE)

    return Image(read_png(write_png(width, height, rgb)))


def test_trace_curve_is_monotone_and_dense():
    curve = km.trace_curve(_synth_km(), CURVE, tol=20,
                           x_range=(50, 351), y_range=(20, 240))
    xs = sorted(curve)
    # curve present across essentially the whole plotted x-range
    assert xs[0] <= 55 and xs[-1] >= 345, f"span {xs[0]}..{xs[-1]}"
    # survival (row y) never decreases going right: a descending KM curve
    ys = [curve[x] for x in xs]
    assert all(b >= a for a, b in zip(ys, ys[1:])), "curve rose (non-monotone)"


def test_median_crossing_recovers_known_median():
    curve = km.trace_curve(_synth_km(), CURVE, tol=20,
                           x_range=(50, 351), y_range=(20, 240))
    median = km.median_crossing(curve, X_AXIS, Y_AXIS, level=0.5)
    assert median is not None, "median not found"
    assert abs(median - TRUE_MEDIAN) <= 0.3, f"median {median} vs {TRUE_MEDIAN}"


def test_value_at_recovers_landmark_and_ignores_tick():
    curve = km.trace_curve(_synth_km(), CURVE, tol=20,
                           x_range=(50, 351), y_range=(20, 240))
    surv = km.value_at(curve, X_AXIS, Y_AXIS, LANDMARK_MONTH)
    assert surv is not None
    assert abs(surv - TRUE_LANDMARK) <= 0.03, f"landmark {surv} vs {TRUE_LANDMARK}"


def test_median_none_when_never_reached():
    # A flat curve that stays above 0.5 never yields a median.
    width, height = 400, 260
    rgb = bytearray([255]) * (width * height * 3)
    py = int(round(Y_AXIS.to_pixel(0.8)))
    for xx in range(50, 351):
        i = (py * width + xx) * 3
        rgb[i:i + 3] = bytes(CURVE)
    image = Image(read_png(write_png(width, height, rgb)))
    curve = km.trace_curve(image, CURVE, tol=20,
                           x_range=(50, 351), y_range=(20, 240))
    assert km.median_crossing(curve, X_AXIS, Y_AXIS, level=0.5) is None


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
