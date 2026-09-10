"""CV tests: forest plot marker and CI measurement, plus axis calibration.

Like the waterfall and KM tests, this draws a synthetic forest plot with KNOWN
hazard ratios and KNOWN CI bounds at controlled pixel coordinates, then checks
the measurement recovers them and that ``crosses_one`` flags exactly the rows
whose CI spans 1. Ground truth we control, no real-slide calibration guesswork.
"""

from __future__ import annotations

from evaluation.cv.calibrate import LinearAxis
from evaluation.cv.image import Image
from evaluation.cv.png_io import read_png, write_png
from evaluation.cv import forest

WHITE = (255, 255, 255)
MARKER = (40, 70, 140)

WIDTH, HEIGHT = 560, 300
# x = 0 at px 100, x = 2 at px 500  ->  200 px per unit, reference x=1 at px 300.
X_AXIS = LinearAxis(px1=100, val1=0.0, px2=500, val2=2.0)

# One row per subgroup: (label, y_center, hazard_ratio, ci_low, ci_high).
# The last field of each tuple decides significance; the two rows whose CI spans
# 1.0 ("Age < 65" and "Female") are the ones crosses_one must return.
ROWS = [
    ("Overall",    45,  0.65, 0.45, 0.90),
    ("Age < 65",   95,  1.15, 0.85, 1.55),
    ("Age >= 65", 145,  0.80, 0.62, 0.98),
    ("Male",      195,  1.40, 1.05, 1.85),
    ("Female",    245,  0.95, 0.70, 1.30),
]
BAND_HALF = 18       # each row is scanned over y_center +/- BAND_HALF
MARKER_R = 6         # diamond radius in pixels
TRUE_CROSSERS = ["Age < 65", "Female"]


def _synth_forest():
    """White image with one diamond marker + horizontal CI whisker per row,
    drawn in MARKER color at the pixels for each row's known HR and CI bounds."""
    rgb = bytearray([255]) * (WIDTH * HEIGHT * 3)

    def put(x, y, color):
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            i = (y * WIDTH + x) * 3
            rgb[i:i + 3] = bytes(color)

    for _label, yc, hr, lo, hi in ROWS:
        lo_px = int(round(X_AXIS.to_pixel(lo)))
        hi_px = int(round(X_AXIS.to_pixel(hi)))
        mx = int(round(X_AXIS.to_pixel(hr)))
        # whisker: a 2px-thick horizontal line spanning the CI
        for xx in range(lo_px, hi_px + 1):
            put(xx, yc, MARKER)
            put(xx, yc + 1, MARKER)
        # diamond marker centered at (mx, yc): a taller blob than the whisker
        for dy in range(-MARKER_R, MARKER_R + 1):
            half = MARKER_R - abs(dy)
            for xx in range(mx - half, mx + half + 1):
                put(xx, yc + dy, MARKER)

    return Image(read_png(write_png(WIDTH, HEIGHT, rgb)))


def _rows_y():
    return [(label, yc - BAND_HALF, yc + BAND_HALF) for label, yc, *_ in ROWS]


def test_linear_axis_maps_both_ways():
    assert abs(X_AXIS.to_value(300) - 1.0) < 1e-9      # reference line
    assert abs(X_AXIS.to_pixel(1.0) - 300) < 1e-9
    assert abs(X_AXIS.value_per_pixel() - (2.0 / 400)) < 1e-9


def test_measure_forest_finds_every_row():
    rows = forest.measure_forest(
        _synth_forest(), MARKER, tol=20, x_axis=X_AXIS,
        rows_y=_rows_y(), x_range=(0, WIDTH),
    )
    assert len(rows) == len(ROWS), f"found {len(rows)} rows, expected {len(ROWS)}"
    assert [r.label for r in rows] == [r[0] for r in ROWS]


def test_measure_forest_recovers_hr_and_ci():
    rows = forest.measure_forest(
        _synth_forest(), MARKER, tol=20, x_axis=X_AXIS,
        rows_y=_rows_y(), x_range=(0, WIDTH),
    )
    by_label = {r.label: r for r in rows}
    for label, _yc, hr, lo, hi in ROWS:
        r = by_label[label]
        assert abs(r.point_value - hr) <= 0.03, f"{label} HR {r.point_value} vs {hr}"
        assert abs(r.ci_low - lo) <= 0.05, f"{label} CI low {r.ci_low} vs {lo}"
        assert abs(r.ci_high - hi) <= 0.05, f"{label} CI high {r.ci_high} vs {hi}"
        assert r.ci_low <= r.point_value <= r.ci_high, f"{label} point outside CI"


def test_crosses_one_flags_exactly_the_spanning_rows():
    rows = forest.measure_forest(
        _synth_forest(), MARKER, tol=20, x_axis=X_AXIS,
        rows_y=_rows_y(), x_range=(0, WIDTH),
    )
    assert forest.crosses_one(rows) == TRUE_CROSSERS
    expected_sig = [r[0] for r in ROWS if r[0] not in TRUE_CROSSERS]
    assert forest.significant(rows) == expected_sig


def test_whisker_color_separate_from_marker():
    """When the whisker is a different color, CI comes from the whisker and the
    point estimate from the marker alone."""
    rgb = bytearray([255]) * (WIDTH * HEIGHT * 3)
    whisker = (200, 40, 40)

    def put(x, y, color):
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            i = (y * WIDTH + x) * 3
            rgb[i:i + 3] = bytes(color)

    yc, hr, lo, hi = 120, 0.70, 0.50, 1.10
    lo_px, hi_px, mx = (int(round(X_AXIS.to_pixel(v))) for v in (lo, hi, hr))
    for xx in range(lo_px, hi_px + 1):
        put(xx, yc, whisker)
        put(xx, yc + 1, whisker)
    for dy in range(-MARKER_R, MARKER_R + 1):
        half = MARKER_R - abs(dy)
        for xx in range(mx - half, mx + half + 1):
            put(xx, yc + dy, MARKER)

    image = Image(read_png(write_png(WIDTH, HEIGHT, rgb)))
    rows = forest.measure_forest(
        image, MARKER, tol=20, x_axis=X_AXIS,
        rows_y=[("Sub", yc - BAND_HALF, yc + BAND_HALF)], x_range=(0, WIDTH),
        whisker_color=whisker,
    )
    assert len(rows) == 1
    r = rows[0]
    assert abs(r.point_value - hr) <= 0.03, f"HR {r.point_value} vs {hr}"
    assert abs(r.ci_low - lo) <= 0.05 and abs(r.ci_high - hi) <= 0.05
    assert forest.crosses_one(rows) == ["Sub"]  # 0.50..1.10 spans 1


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
