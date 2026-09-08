"""COCHRANEFOREST: annotated forest plots from Cochrane systematic reviews.

CochraneForest is the dataset introduced by Pronesti et al., "Query-driven
Document-level Scientific Evidence Extraction from Biomedical Studies"
(ACL 2025; arXiv:2505.06186). It comprises 202 forest-plot images drawn from 48
open-access Cochrane medical systematic reviews, covering 263 unique studies and
923 annotated records.

IMPORTANT - what the dataset actually ships, and what it does NOT
----------------------------------------------------------------
The placeholder version of this ingester assumed each figure came with per-row
numeric effect estimates and confidence intervals. It does not. Reading the
paper (Section 3, Appendix A) the released ground truth for each
``(research question, study)`` record is a **directional conclusion label**:

    "favours <left intervention>" | "favours <right intervention>" | "no difference"

plus the two intervention names taken from the plot axes and the research
question. The per-study point estimate and 95% CI are *visual content of the
plot*; the annotators used an auto-extracted CI only to pre-select the label,
and the numeric values are not a released annotation field. The effect measure
in these medical reviews is mean difference / odds ratio / risk ratio - never
the hazard ratio our oncology forest figures use.

Availability: the dataset is built by mining the Cochrane Library through the
Wiley API, which the paper notes is "permitted for non-commercial research"
only. The authors publish the paper (arXiv non-exclusive license) and a project
page, but do NOT publish the annotation files or images - there is no GitHub /
Hugging Face / Zenodo / OSF release, because the underlying Cochrane content is
not redistributable. So this ingester cannot download the corpus itself; it
reads files you have obtained under those non-commercial terms and point it at.

Because of that, the ingester maps each study record to a verified
``GroundTruthValue`` carrying the **conclusion / direction of effect** (the
dataset's real trustworthy label), and additionally emits a numeric effect+CI
value *only if* a particular export happens to include those columns.

Point it at obtained data with either:

* ``manifest_json=<path>`` - a single JSON/JSONL where each entry describes one
  forest plot (an image reference plus its study records), or
* ``local_dir=<path>`` / ``query`` = a directory of images each paired with a
  sibling annotation file (JSON or CSV) sharing the stem, or
* ``archive_url=<url>`` to download+extract a ``.zip``/``.tar.gz`` first.

Adjust :data:`FIELD_ALIASES` if your export names its columns differently.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from corpus.base import BaseFigureIngester, FigureRecord, FigureType, GroundTruthValue

logger = logging.getLogger("corpus.cochraneforest")

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
_ANNOTATION_SUFFIXES = (".json", ".csv")

# Cochrane content is CC-BY-NC and mined under the Wiley API's non-commercial
# research terms; the dataset is not publicly redistributable (see module note).
DATASET_LICENSE = (
    "Cochrane Library / Wiley - non-commercial research use only "
    "(CochraneForest, Pronesti et al. ACL 2025; not publicly redistributable)"
)

# Column/key name candidates, lowercased, mapped to our fields. The primary
# verified answer is `conclusion` (the direction-of-effect label). `effect` and
# the CI bounds are optional and only present in exports that keep the numbers.
FIELD_ALIASES: Dict[str, List[str]] = {
    "label": ["study", "study_label", "study_name", "study_id", "label", "row", "subgroup", "name"],
    "conclusion": ["conclusion", "conclusion_label", "label_text", "verdict",
                   "direction", "outcome_label", "answer", "gold_label"],
    "question": ["research_question", "question", "query", "rq"],
    "measure": ["effect_measure", "measure", "statistic", "metric", "outcome_measure"],
    "effect": ["effect", "estimate", "point_estimate", "effect_size",
               "mean_difference", "md", "smd", "or", "rr", "hr", "value"],
    "ci_low": ["ci_low", "ci_lower", "lower", "lcl", "low", "lb"],
    "ci_high": ["ci_high", "ci_upper", "upper", "ucl", "high", "ub"],
    "n": ["n", "total", "sample_size", "n_total", "participants", "denominator"],
    "left": ["left_intervention", "intervention_left", "favours_left", "left", "arm_left"],
    "right": ["right_intervention", "intervention_right", "favours_right", "right", "arm_right"],
    "image": ["image", "image_file", "figure", "figure_file", "plot", "plot_file",
              "filename", "file", "path", "image_path"],
    "records": ["studies", "records", "rows", "annotations", "data", "conclusions"],
    "figure_id": ["forest_plot_id", "plot_id", "figure_id", "id", "review_id", "comparison_id"],
}


class CochraneForestIngester(BaseFigureIngester):
    """Ingest annotated forest plots from a local COCHRANEFOREST export."""

    source = "cochraneforest"

    def fetch(self, query: str, **options: Any) -> Iterable[FigureRecord]:
        manifest = options.get("manifest_json") or (
            query if query and query.lower().endswith((".json", ".jsonl")) else None
        )
        if manifest:
            return list(self._records_from_manifest(Path(manifest)))

        root = self._resolve_root(query, options)
        if root is None or not root.exists():
            raise RuntimeError(
                "No dataset. CochraneForest is not publicly downloadable; obtain it "
                "under Cochrane/Wiley non-commercial terms, then pass manifest_json="
                "<file>, local_dir=<dir>, or archive_url=<url>."
            )
        logger.info("cochraneforest: reading %s", root)
        return list(self._records_from_directory(root))

    # -- manifest (single JSON/JSONL describing all plots) ------------------

    def _records_from_manifest(self, path: Path) -> Iterable[FigureRecord]:
        base = path.parent
        for entry in self._load_manifest_entries(path):
            image_ref = self._pick(self._lower(entry), "image")
            image_path = self._resolve_image(base, image_ref) if image_ref else None
            if image_path is None:
                logger.warning("cochraneforest: manifest entry has no resolvable image; skipping")
                continue
            rows = self._entry_rows(entry)
            record = self._build_record(image_path, rows, context=json.dumps(entry),
                                        figure_id=self._entry_figure_id(entry, image_path),
                                        annotation_name=path.name)
            if record is not None:
                yield record

    def _load_manifest_entries(self, path: Path) -> List[Dict[str, Any]]:
        text = path.read_text()
        if path.suffix.lower() == ".jsonl":
            entries = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            payload = json.loads(text)
            entries = self._rows_from_json(payload)
        return [e for e in entries if isinstance(e, dict)]

    def _entry_rows(self, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Study records live under a nested key, or the entry IS one record."""
        nested = self._pick(self._lower(entry), "records")
        if isinstance(nested, list):
            return [r for r in nested if isinstance(r, dict)]
        return [entry]

    def _entry_figure_id(self, entry: Dict[str, Any], image_path: Path) -> str:
        return str(self._pick(self._lower(entry), "figure_id") or image_path.stem)

    def _resolve_image(self, base: Path, ref: Any) -> Optional[Path]:
        candidate = Path(str(ref))
        for path in (candidate, base / candidate, base / candidate.name):
            if path.exists() and path.suffix.lower() in _IMAGE_SUFFIXES:
                return path
        return None

    # -- directory of image + sibling-annotation pairs ----------------------

    def _records_from_directory(self, root: Path) -> Iterable[FigureRecord]:
        for image_path in self._iter_images(root):
            annotation_path = self._find_annotation(image_path)
            rows = self._parse_annotation(annotation_path) if annotation_path else []
            record = self._build_record(
                image_path, rows,
                context=annotation_path.read_text() if annotation_path else "",
                figure_id=image_path.stem,
                annotation_name=annotation_path.name if annotation_path else None,
            )
            if record is not None:
                yield record

    def _iter_images(self, root: Path) -> Iterable[Path]:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
                yield path

    def _find_annotation(self, image_path: Path) -> Optional[Path]:
        for suffix in _ANNOTATION_SUFFIXES:
            candidate = image_path.with_suffix(suffix)
            if candidate.exists():
                return candidate
        return None

    def _parse_annotation(self, path: Path) -> List[Dict[str, Any]]:
        try:
            if path.suffix.lower() == ".json":
                return self._rows_from_json(json.loads(path.read_text()))
            with path.open(newline="") as fh:
                return list(csv.DictReader(fh))
        except (OSError, ValueError) as exc:
            logger.warning("cochraneforest: bad annotation %s: %s", path.name, exc)
            return []

    # -- shared record assembly --------------------------------------------

    def _build_record(
        self,
        image_path: Path,
        rows: List[Dict[str, Any]],
        context: str,
        figure_id: str,
        annotation_name: Optional[str],
    ) -> Optional[FigureRecord]:
        ground_truth: List[GroundTruthValue] = []
        for row in rows:
            ground_truth.extend(self._row_to_values(row))
        return FigureRecord(
            source=self.source,
            figure_id=figure_id,
            figure_type=FigureType.FOREST,
            title=figure_id,
            context=context,
            image_ext=image_path.suffix.lstrip(".").lower() or "png",
            license=DATASET_LICENSE,
            image_bytes=image_path.read_bytes(),
            ground_truth=ground_truth,
            metadata={
                "dataset": "CochraneForest",
                "paper": "arXiv:2505.06186 (ACL 2025)",
                "annotation_file": annotation_name,
                "record_count": len(rows),
            },
        )

    @staticmethod
    def _rows_from_json(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            for key in ("forest_plots", "plots", "records", "rows", "data", "annotations", "studies"):
                if isinstance(payload.get(key), list):
                    return [r for r in payload[key] if isinstance(r, dict)]
            return [payload]
        return []

    def _row_to_values(self, row: Dict[str, Any]) -> List[GroundTruthValue]:
        """Map one study record to its verified ground-truth value(s).

        The dataset's real, trustworthy label is the direction-of-effect
        conclusion. A numeric effect+CI value is emitted only when the export
        actually carries those columns.
        """
        lower = self._lower(row)
        study = str(self._pick(lower, "label") or "study").strip() or "study"
        out: List[GroundTruthValue] = []

        conclusion = self._pick(lower, "conclusion")
        if conclusion is not None:
            out.append(GroundTruthValue(
                quantity=f"conclusion (direction of effect), {study}",
                value=str(conclusion),
                method="annotated_dataset",
                source_text=str(row),
                verified=True,
            ))

        effect = self._pick(lower, "effect")
        if effect is not None:
            ci_low = self._pick(lower, "ci_low")
            ci_high = self._pick(lower, "ci_high")
            measure = str(self._pick(lower, "measure") or "effect estimate")
            value = str(effect)
            if ci_low is not None and ci_high is not None:
                value = f"{effect} (95% CI {ci_low}-{ci_high})"
            n = self._pick(lower, "n")
            out.append(GroundTruthValue(
                quantity=f"{measure}, {study}",
                value=value,
                population_n=int(n) if str(n).isdigit() else None,
                method="annotated_dataset",
                source_text=str(row),
                verified=True,
            ))
        return out

    # -- locating the data --------------------------------------------------

    def _resolve_root(self, query: str, options: Dict[str, Any]) -> Optional[Path]:
        local = options.get("local_dir") or (
            query if query and not query.startswith("http") else None
        )
        if local:
            return Path(local)
        archive_url = options.get("archive_url") or (
            query if query and query.startswith("http") else None
        )
        if archive_url:
            return self._download_and_extract(
                archive_url, options.get("extract_to", "data/corpus/_cache/cochraneforest")
            )
        return None

    def _download_and_extract(self, url: str, extract_to: str) -> Path:
        target = Path(extract_to)
        target.mkdir(parents=True, exist_ok=True)
        logger.info("cochraneforest: downloading %s", url)
        blob = self.http.get_bytes(url)
        if url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                zf.extractall(target)
        elif url.endswith((".tar.gz", ".tgz")):
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
                tf.extractall(target)
        else:
            raise RuntimeError(f"unsupported archive type for {url}")
        return target

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _lower(row: Dict[str, Any]) -> Dict[str, Any]:
        return {str(k).lower(): v for k, v in row.items()}

    @staticmethod
    def _pick(row: Dict[str, Any], field: str) -> Any:
        for alias in FIELD_ALIASES[field]:
            if alias in row and row[alias] not in (None, ""):
                return row[alias]
        return None
