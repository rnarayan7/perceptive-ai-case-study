"""Extractors: the thing under test.

``Extractor`` is the interface the harness runs. :class:`StubExtractor` implements
it by perturbing the gold, so the whole loop and every scorer can be validated
before a real extractor exists. The stub deliberately ties a per-value difficulty
to three things at once: the spread between its two reads, the width of its
interval, and its stated confidence. That makes the keystone relationship
(reads-agree -> low error) hold by construction, which is what we need to prove
the metric's plumbing is correct, not evidence about any real model.
"""

from __future__ import annotations

import random
from typing import List, Optional, Protocol

from evaluation.parse import parse_value
from evaluation.types import Family, GoldFigure, KeyedTruth, Prediction, ValueKind


class Extractor(Protocol):
    """Runs on a figure and returns predictions tagged with quantity keys."""

    def extract(self, figure: GoldFigure) -> List[Prediction]:
        ...


class StubExtractor:
    """Synthesize predictions from gold, family-aware, with tunable noise.

    ``miss_rate`` drops a value entirely (extractor returned nothing).
    ``error_rate`` injects a genuine miss (a large offset, or a denominator/set
    error) so the error-category and calibration machinery sees real failures.
    """

    def __init__(
        self,
        noise: float = 1.0,
        miss_rate: float = 0.05,
        error_rate: float = 0.12,
        seed: Optional[int] = 7,
    ) -> None:
        self.noise = noise
        self.miss_rate = miss_rate
        self.error_rate = error_rate
        self._rng = random.Random(seed)

    def extract(self, figure: GoldFigure) -> List[Prediction]:
        predictions: List[Prediction] = []
        for truth in figure.truths:
            if self._rng.random() < self.miss_rate:
                continue  # extractor missed this value
            pred = self._synthesize(figure, truth)
            if pred is not None:
                predictions.append(pred)
        return predictions

    # -- per-family synthesis ----------------------------------------------

    def _synthesize(self, figure: GoldFigure, truth: KeyedTruth) -> Optional[Prediction]:
        family = truth.family
        if family == Family.INTERPRETIVE:
            return self._prediction(figure, truth, truth.value_raw, confidence=None)
        if family == Family.PROPORTION:
            return self._synth_proportion(figure, truth)
        if family == Family.CATEGORICAL_SET:
            return self._synth_set(figure, truth)
        return self._synth_numeric(figure, truth)

    def _synth_numeric(self, figure, truth) -> Optional[Prediction]:
        parsed = parse_value(truth.value_raw, truth.unit, truth.family)
        if parsed.kind == ValueKind.SENTINEL:
            return self._prediction(figure, truth, truth.value_raw, confidence=0.8)
        if parsed.scalar is None:
            return None
        base = parsed.scalar
        difficulty = self._rng.random()  # 0 easy .. 1 hard
        log_space = truth.family == Family.LOG_SCALE and base > 0

        scale = self._noise_scale(truth, base)
        spread = difficulty * scale * self.noise
        inject = self._rng.random() < self.error_rate

        if log_space:
            import math
            center = math.log10(base)
            r1 = center + self._rng.gauss(0, spread)
            r2 = center + self._rng.gauss(0, spread)
            if inject:
                r1 += 1.0; r2 += 1.0  # a decade off: a real miss
            read1, read2 = 10 ** r1, 10 ** r2
            predicted = 10 ** ((r1 + r2) / 2)
        else:
            r1 = base + self._rng.gauss(0, spread)
            r2 = base + self._rng.gauss(0, spread)
            if inject:
                offset = 4 * scale
                r1 += offset; r2 += offset
            read1, read2 = r1, r2
            predicted = (r1 + r2) / 2

        half = abs(read1 - read2) / 2 + 0.5 * scale
        confidence = _clamp(1.0 - difficulty, 0.1, 0.98)
        value_raw = self._format(predicted, truth)
        return self._prediction(
            figure, truth, value_raw,
            interval=(predicted - half, predicted + half),
            confidence=confidence,
            reads={"vlm": read1, "cv": read2},
            method="stub_numeric",
            ci=(parsed.ci_low, parsed.ci_high),
        )

    def _synth_proportion(self, figure, truth) -> Optional[Prediction]:
        parsed = parse_value(truth.value_raw, truth.unit, Family.PROPORTION)
        if parsed.numerator is None or parsed.denominator is None:
            return None
        num, den = parsed.numerator, parsed.denominator
        roll = self._rng.random()
        if roll < self.error_rate * 0.5:
            den = den + self._rng.choice([-5, 5, 3])  # denominator error
        elif roll < self.error_rate:
            num = max(0, num + self._rng.choice([-3, 2, 4]))  # numerator error
        pct = 100.0 * num / den if den else 0.0
        value_raw = f"{pct:.1f}% ({num}/{den})"
        return self._prediction(figure, truth, value_raw, confidence=0.85, method="stub_proportion")

    def _synth_set(self, figure, truth) -> Optional[Prediction]:
        parsed = parse_value(truth.value_raw, truth.unit, Family.CATEGORICAL_SET)
        items = set(parsed.items or frozenset())
        if self._rng.random() < self.error_rate and items:
            items.pop() if len(items) > 1 else items.add("spurious item")
        value_raw = ", ".join(sorted(items))
        return self._prediction(figure, truth, value_raw, confidence=0.7, method="stub_set")

    # -- assembly -----------------------------------------------------------

    def _prediction(self, figure, truth, value_raw, interval=None, confidence=None,
                    reads=None, method="stub", ci=None) -> Prediction:
        lo, hi = (interval if interval else (None, None))
        raw = value_raw
        if ci and ci[0] is not None and ci[1] is not None and "CI" not in value_raw:
            raw = f"{value_raw} (95% CI {ci[0]:.2f}-{ci[1]:.2f})"
        return Prediction(
            figure_id=figure.figure_id,
            figure_type=figure.figure_type,
            quantity_key=truth.quantity_key,
            value_raw=raw,
            unit=truth.unit,
            interval_low=lo,
            interval_high=hi,
            confidence=confidence,
            method=method,
            reads=reads or {},
        )

    def _noise_scale(self, truth: KeyedTruth, base: float) -> float:
        """A natural noise unit: the tolerance if set, else 10% of the value."""
        from evaluation.keys import spec_for
        spec = spec_for(truth.figure_id, truth.quantity_key)
        if spec and spec.tolerance and truth.family != Family.LOG_SCALE:
            return spec.tolerance
        if truth.family == Family.LOG_SCALE:
            return 0.15  # in log10 units, ~1.4x
        return max(abs(base) * 0.1, 1e-6)

    def _format(self, value: float, truth: KeyedTruth) -> str:
        if truth.family == Family.RATIO_CI:
            return f"{value:.2f}"
        if truth.unit == "months":
            return f"{value:.1f} months"
        if truth.unit == "nM":
            return f"{value:.3g} nM"
        if truth.unit == "probability":
            return f"{value:.2f}"
        if truth.unit == "percent":
            return f"{value:.0f}%"
        if truth.unit == "count":
            return f"{round(value)}"
        if truth.unit == "x":
            return f"{value:.0f}x"
        return f"{value:.3g}"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
