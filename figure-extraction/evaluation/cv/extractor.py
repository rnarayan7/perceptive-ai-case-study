"""CV extractor and a combined VLM+CV extractor.

:class:`CvExtractor` produces the geometric reads from pixel measurement, driven
by a per-figure config (colors, plot region, baseline, axis calibration). Only
waterfall is fully wired today; other geometric types slot in the same way once
their measurement function and calibration exist.

:class:`CombinedExtractor` runs a VLM extractor and a CV extractor and merges
them so a geometric quantity carries BOTH reads (``{"vlm": ..., "cv": ...}``).
That makes the keystone agreement signal a real cross-check between two
independent methods, not two samples of one.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from evaluation.cv.calibrate import LinearAxis, LogAxis
from evaluation.cv.image import Image
from evaluation.cv import forest as fo
from evaluation.cv import kaplan_meier as km
from evaluation.cv import pk
from evaluation.cv import waterfall as wf
from evaluation.parse import parse_value
from evaluation.types import Family, GoldFigure, Prediction

logger = logging.getLogger("evaluation.cv")


class CvExtractor:
    """Measure geometric quantities from the image, per a figure config."""

    def __init__(self, configs: Dict[str, dict], base_dir: Path = Path(".")) -> None:
        self.configs = configs
        self.base_dir = Path(base_dir)

    def extract(self, figure: GoldFigure) -> List[Prediction]:
        config = self.configs.get(figure.figure_id)
        if not config or not figure.image_path:
            return []
        image = Image.load(self.base_dir / figure.image_path)
        if config.get("figure_type") == "waterfall":
            return self._waterfall(figure, image, config)
        if config.get("figure_type") == "kaplan_meier":
            return self._kaplan_meier(figure, image, config)
        if config.get("figure_type") == "forest":
            return self._forest(figure, image, config)
        if config.get("figure_type") == "pk":
            return self._pk(figure, image, config)
        return []  # unknown geometric type

    def _waterfall(self, figure, image, config) -> List[Prediction]:
        predictions: List[Prediction] = []
        for panel in config.get("panels", []):
            y_axis = _build_axis(panel["y_axis"])
            bars = wf.measure_waterfall(
                image,
                bar_color=tuple(panel["bar_color"]),
                baseline_py=panel["baseline_py"],
                y_axis=y_axis,
                x_range=tuple(panel["x_range"]),
                y_range=tuple(panel["y_range"]),
                tol=panel.get("tol", 40),
                # Densely-packed bars (small inter-bar gaps) need these tuned per
                # figure, else adjacent bars merge into one run and proportions
                # collapse. Configurable, with the same defaults as the function.
                min_run_px=panel.get("min_run_px", 3),
                max_gap_px=panel.get("max_gap_px", 2),
            )
            unc = _axis_uncertainty(y_axis)
            for key, q in panel.get("quantities", {}).items():
                pred = self._quantity(figure, key, q, bars, unc)
                if pred is not None:
                    predictions.append(pred)
        return predictions

    def _quantity(self, figure, key, q, bars, unc) -> Optional[Prediction]:
        metric = q["metric"]
        if metric == "proportion_beyond":
            count, total = wf.proportion_beyond(bars, q["threshold"], q.get("below", True))
            if total == 0:
                return None
            pct = 100.0 * count / total
            return self._pred(figure, key, f"{pct:.0f}% ({count}/{total})", confidence=0.75)
        if metric == "deepest":
            v = wf.deepest(bars)
            return self._pred(figure, key, f"{v:.0f}%", confidence=_conf(unc, v)) if v is not None else None
        if metric == "leftmost":
            bar = wf.leftmost(bars)
            return self._pred(figure, key, f"{bar.value:.0f}%", confidence=_conf(unc, bar.value)) if bar else None
        if metric == "count":
            return self._pred(figure, key, str(len(bars)), confidence=0.7)
        return None

    def _kaplan_meier(self, figure, image, config) -> List[Prediction]:
        predictions: List[Prediction] = []
        x_axis = _build_axis(config["x_axis"])
        y_axis = _build_axis(config["y_axis"])
        x_unc = _axis_uncertainty(x_axis)
        y_unc = _axis_uncertainty(y_axis)
        landmark = config.get("landmark_month", 6)
        for arm in config.get("curves", []):
            suffix = arm["arm_key_suffix"]
            curve = km.trace_curve(
                image,
                curve_color=tuple(arm["curve_color"]),
                tol=arm.get("tol", 40),
                x_range=tuple(config["x_range"]),
                y_range=tuple(config["y_range"]),
            )
            if not curve:
                continue
            median = km.median_crossing(curve, x_axis, y_axis, level=0.5)
            if median is not None:
                predictions.append(self._pred(
                    figure, f"median_pfs.{suffix}", f"{median:.1f} months",
                    confidence=_conf(x_unc, median),
                ))
            landmark_val = km.value_at(curve, x_axis, y_axis, landmark)
            if landmark_val is not None:
                predictions.append(self._pred(
                    figure, f"pfs_6mo.{suffix}", f"{landmark_val:.2f}",
                    confidence=_conf(y_unc, landmark_val),
                ))
        return predictions

    def _forest(self, figure, image, config) -> List[Prediction]:
        """Hazard ratio + CI per row, and which CIs cross 1, off a forest plot."""
        predictions: List[Prediction] = []
        x_axis = _build_axis(config["x_axis"])
        x_unc = _axis_uncertainty(x_axis)
        rows_y = [(r["label"], r["y0"], r["y1"]) for r in config["rows"]]
        forest_rows = fo.measure_forest(
            image,
            marker_color=tuple(config["marker_color"]),
            tol=config.get("tol", 40),
            x_axis=x_axis,
            rows_y=rows_y,
            x_range=tuple(config["x_range"]),
            whisker_color=tuple(config["whisker_color"]) if config.get("whisker_color") else None,
            marker_frac=config.get("marker_frac", 0.5),
        )
        for r in forest_rows:
            predictions.append(self._pred(
                figure, f"hazard_ratio.{r.label}",
                f"{r.point_value:.2f} ({r.ci_low:.2f}-{r.ci_high:.2f})",
                confidence=_conf(x_unc, r.point_value),
            ))
        crossers = fo.crosses_one(forest_rows)
        if forest_rows:
            predictions.append(self._pred(
                figure, "ci_crosses_one",
                ", ".join(crossers) if crossers else "none",
                confidence=0.7,
            ))
        return predictions

    def _pk(self, figure, image, config) -> List[Prediction]:
        """Concentration reads and fold-multiples off a log-scale PK curve."""
        predictions: List[Prediction] = []
        x_axis = _build_axis(config["x_axis"])
        y_axis = _build_axis(config["y_axis"])          # a LogAxis for PK
        y_fold = y_axis.fold_per_pixel() if isinstance(y_axis, LogAxis) else 1.0
        threshold = config.get("threshold_conc")
        for curve_cfg in config.get("curves", []):
            suffix = curve_cfg["curve_key_suffix"]
            curve = pk.trace_curve(
                image,
                curve_color=tuple(curve_cfg["curve_color"]),
                tol=curve_cfg.get("tol", 40),
                x_range=tuple(config["x_range"]),
                y_range=tuple(config["y_range"]),
            )
            if not curve:
                continue
            for day in curve_cfg.get("concentration_days", []):
                conc = pk.concentration_at(curve, x_axis, y_axis, day)
                if conc is not None:
                    predictions.append(self._pred(
                        figure, f"conc_day{day}.{suffix}", f"{conc:.3g} nM",
                        confidence=_fold_conf(y_fold)))
            for pair in curve_cfg.get("fold_pairs", []):
                fold = pk.fold_multiple(curve, x_axis, y_axis, pair[0], pair[1])
                if fold is not None:
                    predictions.append(self._pred(
                        figure, f"fold_d{pair[0]}_d{pair[1]}.{suffix}", f"{fold:.1f}x",
                        confidence=_fold_conf(y_fold)))
            if threshold:
                peak = pk.concentration_at(curve, x_axis, y_axis, curve_cfg.get("peak_day", 0))
                tf = pk.threshold_fold(peak, threshold) if peak is not None else None
                if tf is not None:
                    predictions.append(self._pred(
                        figure, f"threshold_fold.{suffix}", f"{tf:.1f}x",
                        confidence=_fold_conf(y_fold)))
        return predictions

    def _pred(self, figure, key, value_raw, confidence) -> Prediction:
        return Prediction(
            figure_id=figure.figure_id, figure_type=figure.figure_type,
            quantity_key=key, value_raw=value_raw, confidence=confidence,
            method="cv_measure", reads={},
        )


class CombinedExtractor:
    """Merge a VLM extractor and a CV extractor into one, with dual reads."""

    def __init__(self, vlm, cv: CvExtractor, prefer_cv: bool = True) -> None:
        self.vlm = vlm
        self.cv = cv
        self.prefer_cv = prefer_cv  # for geometric keys, which read is primary

    @property
    def routed(self) -> Dict[str, str]:
        """The VLM router's figure-type calls, so the runner can score routing."""
        return getattr(self.vlm, "routed", {})

    def extract(self, figure: GoldFigure) -> List[Prediction]:
        vlm_preds = {p.quantity_key: p for p in self.vlm.extract(figure)}
        cv_preds = {p.quantity_key: p for p in self.cv.extract(figure)}

        merged: List[Prediction] = []
        for key in list(vlm_preds) + [k for k in cv_preds if k not in vlm_preds]:
            v = vlm_preds.get(key)
            c = cv_preds.get(key)
            if v is not None and c is not None:
                merged.append(self._merge(v, c))
            else:
                merged.append(v or c)
        return merged

    def _merge(self, v: Prediction, c: Prediction) -> Prediction:
        primary = c if self.prefer_cv else v
        v_scalar = _scalar(v)
        c_scalar = _scalar(c)
        out = Prediction(
            figure_id=primary.figure_id, figure_type=primary.figure_type,
            quantity_key=primary.quantity_key, value_raw=primary.value_raw,
            unit=primary.unit, interval_low=primary.interval_low,
            interval_high=primary.interval_high,
            confidence=_combine_conf(v.confidence, c.confidence, v_scalar, c_scalar),
            method="vlm+cv",
            reads={"vlm": v_scalar, "cv": c_scalar},
        )
        return out


# --- helpers ---------------------------------------------------------------

def _build_axis(spec: dict):
    if spec.get("type") == "log":
        return LogAxis(spec["px1"], spec["val1"], spec["px2"], spec["val2"])
    return LinearAxis(spec["px1"], spec["val1"], spec["px2"], spec["val2"])


def _axis_uncertainty(y_axis) -> float:
    try:
        return y_axis.value_per_pixel()
    except AttributeError:
        return 0.0


def _conf(unc: float, value: Optional[float]) -> float:
    if not value:
        return 0.5
    rel = 3 * unc / abs(value)
    return max(0.3, min(0.98, 1 - rel))


def _fold_conf(fold_per_pixel: float) -> float:
    """Confidence from a log axis's per-pixel fold: near 1.0 fold -> high."""
    rel = 3 * abs(fold_per_pixel - 1.0)
    return max(0.3, min(0.98, 1 - rel))


def _scalar(pred: Prediction) -> Optional[float]:
    parsed = parse_value(pred.value_raw, pred.unit)
    if parsed.scalar is not None:
        return parsed.scalar
    if parsed.numerator is not None and parsed.denominator:
        return 100.0 * parsed.numerator / parsed.denominator
    return None


def _combine_conf(cv1, cv2, s1, s2) -> Optional[float]:
    """When both reads agree, confidence rises; when they diverge, it falls."""
    base = [c for c in (cv1, cv2) if c is not None]
    conf = sum(base) / len(base) if base else 0.5
    if s1 is not None and s2 is not None and max(abs(s1), abs(s2)) > 0:
        disagree = abs(s1 - s2) / max(abs(s1), abs(s2))
        conf *= max(0.3, 1 - disagree)
    return round(conf, 3)
