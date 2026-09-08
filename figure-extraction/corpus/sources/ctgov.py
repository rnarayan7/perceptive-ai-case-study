"""ClinicalTrials.gov as a ground-truth source.

ClinicalTrials.gov stores a trial's *results* as structured data (outcome
measures with point estimates, CIs, and participant counts), not as figures. So
it is not a figure source; it is an answer-key source. This ingester fetches a
study's results section and emits a ground-truth-only :class:`FigureRecord`
(figure_type ``table``, no image, ``metadata["ground_truth_only"] = True``) whose
``ground_truth`` values are ``verified`` and carry ``method="structured"``.

The eval harness pairs these against real table/figure records by NCT id, giving
figure type 5 (raster tables) a regulator-grade reference without manual reading.

Only the standard library and the v2 REST API are used.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional

from corpus.base import BaseFigureIngester, FigureRecord, FigureType, GroundTruthValue

logger = logging.getLogger("corpus.ctgov")

STUDY_URL = "https://clinicaltrials.gov/api/v2/studies/{nct}"
PUBLIC_URL = "https://clinicaltrials.gov/study/{nct}"


class ClinicalTrialsTruthIngester(BaseFigureIngester):
    """Emit verified ground-truth records from ClinicalTrials.gov results.

    ``fetch`` takes one NCT id or a comma-separated list as ``query`` (e.g.
    ``"NCT02773849"`` or ``"NCT02773849,NCT04165317"``) and returns one record
    per study, each holding the trial's reported outcome numbers as verified
    ground truth.

    Options:

    ``max_outcomes``
        Cap on outcome measures distilled per study (default 40).
    """

    source = "ctgov"

    def fetch(self, query: str, **options: Any) -> Iterable[FigureRecord]:
        max_outcomes = int(options.get("max_outcomes") or 40)
        nct_ids = [n.strip().upper() for n in query.split(",") if n.strip()]
        records: List[FigureRecord] = []
        for nct in nct_ids:
            try:
                study = self._fetch_study(nct)
            except Exception as exc:  # noqa: BLE001 - skip a bad id, keep going
                logger.warning("ctgov: failed to fetch %s: %s", nct, exc)
                continue
            record = self._to_record(nct, study, max_outcomes)
            if record is not None:
                records.append(record)
        return records

    def _fetch_study(self, nct: str) -> Dict[str, Any]:
        url = STUDY_URL.format(nct=urllib.parse.quote(nct))
        payload = self.http.get_json(url)
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected response for {nct}: {type(payload)}")
        return payload

    def _to_record(
        self, nct: str, study: Dict[str, Any], max_outcomes: int
    ) -> Optional[FigureRecord]:
        protocol = study.get("protocolSection") or {}
        results = study.get("resultsSection") or {}
        ident = protocol.get("identificationModule") or {}
        title = ident.get("briefTitle") or ident.get("officialTitle") or nct

        ground_truth = self._distill_outcomes(results, max_outcomes)
        if not ground_truth:
            logger.info("ctgov: %s has no posted results; recording metadata only", nct)

        context = self._render_text(title, ground_truth)
        return FigureRecord(
            source=self.source,
            figure_id=nct,
            figure_type=FigureType.TABLE,
            title=title,
            context=context,
            url=PUBLIC_URL.format(nct=nct),
            image_ext="none",
            ground_truth=ground_truth,
            metadata={
                "ground_truth_only": True,
                "nct_id": nct,
                "has_results": bool(results),
            },
            raw=json.dumps(study, ensure_ascii=False),
        )

    def _distill_outcomes(
        self, results: Dict[str, Any], max_outcomes: int
    ) -> List[GroundTruthValue]:
        """Turn posted outcome measures into verified ground-truth values.

        The v2 shape is ``outcomeMeasures[].classes[].categories[].measurements[]``,
        with one measurement per arm group. We record each measurement as a value,
        tagging the arm and recovering the group's denominator when present.
        """
        module = results.get("outcomeMeasuresModule") or {}
        measures = module.get("outcomeMeasures") or []
        group_sizes = self._group_sizes(results)

        values: List[GroundTruthValue] = []
        for measure in measures:
            measure_title = measure.get("title") or "outcome"
            unit = measure.get("unitOfMeasure")
            groups = {g.get("id"): g.get("title") for g in (measure.get("groups") or [])}
            for cls in measure.get("classes") or []:
                class_title = cls.get("title") or ""
                for category in cls.get("categories") or []:
                    cat_title = category.get("title") or ""
                    for meas in category.get("measurements") or []:
                        group_id = meas.get("groupId")
                        arm = groups.get(group_id) or group_id or ""
                        value = meas.get("value")
                        if value is None:
                            continue
                        ci = ""
                        if meas.get("lowerLimit") and meas.get("upperLimit"):
                            ci = f" (95% CI {meas['lowerLimit']}-{meas['upperLimit']})"
                        quantity = ", ".join(
                            p for p in (measure_title, class_title, cat_title, arm) if p
                        )
                        values.append(GroundTruthValue(
                            quantity=quantity,
                            value=f"{value}{ci}",
                            unit=unit,
                            population_n=group_sizes.get(group_id),
                            method="structured",
                            source_text=f"ClinicalTrials.gov outcome measure: {measure_title}",
                            verified=True,
                        ))
                        if len(values) >= max_outcomes:
                            return values
        return values

    @staticmethod
    def _group_sizes(results: Dict[str, Any]) -> Dict[str, int]:
        """Map arm-group id to its participant count from the flow module."""
        flow = results.get("participantFlowModule") or {}
        sizes: Dict[str, int] = {}
        for group in flow.get("groups") or []:
            gid = group.get("id")
            if gid:
                sizes[gid] = None  # populated below if a period reports counts
        for period in flow.get("periods") or []:
            for milestone in period.get("milestones") or []:
                if milestone.get("type") not in ("STARTED", "COMPLETED"):
                    continue
                for achievement in milestone.get("achievements") or []:
                    gid = achievement.get("groupId")
                    count = achievement.get("numSubjects")
                    if gid and count is not None and sizes.get(gid) is None:
                        try:
                            sizes[gid] = int(count)
                        except (TypeError, ValueError):
                            pass
        return {k: v for k, v in sizes.items() if v is not None}

    @staticmethod
    def _render_text(title: str, ground_truth: List[GroundTruthValue]) -> str:
        lines = [f"Trial: {title}", ""]
        for gt in ground_truth:
            denom = f" [n={gt.population_n}]" if gt.population_n else ""
            unit = f" {gt.unit}" if gt.unit else ""
            lines.append(f"- {gt.quantity}: {gt.value}{unit}{denom}")
        return "\n".join(lines)
