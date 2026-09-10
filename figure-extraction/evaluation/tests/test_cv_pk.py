"""CV tests: PK concentration-time curve tracing on a LOG y-axis.

Like the KM test, this builds a synthetic figure with KNOWN values, then checks
the measurement recovers them. Here the y-axis is logarithmic: a decaying curve
is drawn through sample points at KNOWN concentrations on KNOWN days, plus fat
marker dots to prove tracing stays on the line and steps past them. Tolerances
are stated as FOLDS, since a log axis is coarse in pixels (one pixel is a
fold-step, ``LogAxis.fold_per_pixel()``).
"""

from __future__ import annotations

from evaluation.cv.calibrate import LinearAxis, LogAxis
from evaluation.cv.image import Image
from evaluation.cv.png_io import read_png, write_png
from evaluation.cv import pk

WHITE = (255, 255, 255)
CURVE = (30, 90, 200)

# y is LOGARITHMIC: 100 nM at px 100, 0.01 nM at px 500 -> 4 decades / 400px.
# x is linear: day 0 at px 100, day 14 at px 700.
Y_AXIS = LogAxis(px1=100, val1=100.0, px2=500, val2=0.01)
X_AXIS = LinearAxis(px1=100, val1=0.0, px2=700, val2=14.0)

# Sample points (day, concentration nM) of a decaying curve. The drawn line
# between consecutive points is straight in pixels, i.e. log-linear in value,
# so a read at a sampled day recovers that point's concentration.
SAMPLES = [
    (0, 100.0),
    (2, 50.0),
    (4, 20.0),
    (7, 5.0),
    (10, 1.0),
    (14, 0.1),
]

WIDTH, HEIGHT = 760, 560


def _synth_pk() -> Image:
    """White image with the SAMPLES curve drawn as joined straight segments in
    pixel space, plus a fat marker dot at each sample point, in CURVE color."""
    rgb = bytearray([255]) * (WIDTH * HEIGHT * 3)

    def put(x, y, color):
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            i = (y * WIDTH + x) * 3
            rgb[i:i + 3] = bytes(color)

    pts = [(X_AXIS.to_pixel(d), Y_AXIS.to_pixel(c)) for d, c in SAMPLES]

    # Connect consecutive points with a dense straight line (step along x,
    # thicken by 1px so no column is missed).
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        steps = int(round(x1 - x0)) or 1
        for s in range(steps + 1):
            t = s / steps
            x = int(round(x0 + t * (x1 - x0)))
            y = int(round(y0 + t * (y1 - y0)))
            put(x, y, CURVE)
            put(x, y + 1, CURVE)

    # Fat marker dots (radius 3) centered on each sample point.
    for x0, y0 in pts:
        cx, cy = int(round(x0)), int(round(y0))
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                if dx * dx + dy * dy <= 9:
                    put(cx + dx, cy + dy, CURVE)

    return Image(read_png(write_png(WIDTH, HEIGHT, rgb)))


def _traced():
    return pk.trace_curve(_synth_pk(), CURVE, tol=25,
                          x_range=(100, 701), y_range=(80, 520))


def test_log_axis_round_trips():
    for value in (100.0, 10.0, 1.0, 0.01):
        px = Y_AXIS.to_pixel(value)
        back = Y_AXIS.to_value(px)
        assert abs(back / value - 1.0) < 1e-6, f"{back} vs {value}"
    # x-axis anchors round-trip too.
    assert abs(X_AXIS.to_value(X_AXIS.to_pixel(7.0)) - 7.0) < 1e-6


def test_trace_curve_spans_and_decays():
    curve = _traced()
    xs = sorted(curve)
    # present across essentially the whole plotted x-range
    assert xs[0] <= 110 and xs[-1] >= 690, f"span {xs[0]}..{xs[-1]}"
    # concentration falls monotonically -> row y increases going right
    ys = [curve[x] for x in xs]
    assert all(b >= a - 1 for a, b in zip(ys, ys[1:])), "curve rose (not decaying)"


def test_concentration_at_recovers_known_points():
    curve = _traced()
    for day, true_conc in SAMPLES:
        got = pk.concentration_at(curve, X_AXIS, Y_AXIS, day)
        assert got is not None
        fold = max(got / true_conc, true_conc / got)
        assert fold <= 1.3, f"day {day}: {got:.4g} vs {true_conc} (fold {fold:.2f})"


def test_fold_multiple_recovers_known_ratio():
    curve = _traced()
    # day 0 -> day 4 is a known 100/20 = 5x drop.
    got = pk.fold_multiple(curve, X_AXIS, Y_AXIS, 0, 4)
    assert got is not None
    assert abs(got / 5.0 - 1.0) <= 0.2, f"fold {got:.3f} vs 5.0"
    # day 2 -> day 10 is a known 50/1 = 50x drop.
    got2 = pk.fold_multiple(curve, X_AXIS, Y_AXIS, 2, 10)
    assert abs(got2 / 50.0 - 1.0) <= 0.2, f"fold {got2:.3f} vs 50.0"


def test_threshold_fold_helper():
    peak = pk.concentration_at(_traced(), X_AXIS, Y_AXIS, 0)
    fold = pk.threshold_fold(peak, 1.0)  # folds over a 1 nM threshold line
    assert fold is not None
    assert abs(fold / 100.0 - 1.0) <= 0.3, f"threshold fold {fold:.2f} vs 100"


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
