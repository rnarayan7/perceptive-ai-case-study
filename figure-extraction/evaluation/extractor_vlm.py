"""A real VLM-based extractor with a figure-type router.

This is the first real extractor behind the :class:`Extractor` interface. It:

1. Routes the image to a figure type (a cheap VLM classification), recorded so
   router accuracy can be measured against the known type.
2. Asks the model, per figure type, for the requested quantities, returning a
   value, interval, and confidence tagged with the canonical ``quantity_key``.
3. Samples the model twice; the two independent reads give the agreement signal
   the keystone metric needs. (A CV measurement is the planned second read for
   the geometric figures; two VLM samples are the honest first cut.)

The model is injected as a :class:`~evaluation.llm.VisionClient`, so this is
testable offline with a fake client. It needs credentials only when run for real.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from evaluation.keys import FIGURE_REQUESTS
from evaluation.llm import (
    EXTRACTOR_MODEL,
    ROUTER_MODEL,
    AnthropicClient,
    VisionClient,
    extract_json,
    media_type_for,
)
from evaluation.parse import parse_value
from evaluation.types import Family, GoldFigure, Prediction, QuantitySpec

logger = logging.getLogger("evaluation.vlm")

_FIGURE_TYPES = ["kaplan_meier", "waterfall", "forest", "pk_logscale", "table", "spider"]

_ROUTER_PROMPT = (
    "Classify this scientific figure into exactly one of these types and reply "
    "with only that one word:\n" + ", ".join(_FIGURE_TYPES)
)


class VlmExtractor:
    """Extract requested quantities from a figure image with a vision model."""

    def __init__(
        self,
        client: Optional[VisionClient] = None,
        router_client: Optional[VisionClient] = None,
        base_dir: Path = Path("."),
        samples: int = 2,
        route: bool = True,
    ) -> None:
        # Extraction is the capability-sensitive read, so it defaults to the most
        # capable model; routing is a cheap classification on its own model. Pass
        # a single fake client for both in tests.
        self.client = client or AnthropicClient(model=EXTRACTOR_MODEL)
        self.router_client = router_client or client or AnthropicClient(model=ROUTER_MODEL)
        self.base_dir = Path(base_dir)
        self.samples = max(1, samples)
        self.route = route
        #: figure_id -> routed type, for a router-accuracy check by the caller.
        self.routed: Dict[str, str] = {}

    def extract(self, figure: GoldFigure) -> List[Prediction]:
        specs = FIGURE_REQUESTS.get(figure.figure_id, [])
        if not specs:
            return []
        image = self._load_image(figure)
        media_type = media_type_for(Path(figure.image_path or "x.png").suffix)

        if self.route:
            self.routed[figure.figure_id] = self._route(image, media_type)

        # Sample the extraction prompt `samples` times for independent reads.
        prompt = self._build_prompt(figure.figure_type, specs)
        samples: List[Dict[str, dict]] = []
        for _ in range(self.samples):
            reply = self.client.complete(prompt, image=image, media_type=media_type,
                                         max_tokens=8000)  # room for thinking + JSON; 1500 truncated
            samples.append(self._parse_sample(reply))

        return self._assemble(figure, specs, samples)

    # -- steps --------------------------------------------------------------

    def _route(self, image: bytes, media_type: str) -> str:
        reply = self.router_client.complete(_ROUTER_PROMPT, image=image, media_type=media_type,
                                            max_tokens=16)
        word = (reply or "").strip().lower().split()[0] if reply.strip() else "unknown"
        return word if word in _FIGURE_TYPES else "unknown"

    def _build_prompt(self, figure_type: str, specs: List[QuantitySpec]) -> str:
        lines = [
            f"You are reading a {figure_type} figure from an oncology presentation.",
            "Read each requested quantity directly from the graphic. Do not guess "
            "from prior knowledge; read what the figure shows.",
            "Return ONLY a JSON array. Each element:",
            '  {"quantity_key": <key>, "value": <string with unit>, '
            '"interval_low": <number or null>, "interval_high": <number or null>, '
            '"confidence": <0..1>, "method": <short string>}',
            "interval_low/high are your uncertainty bounds on the value. "
            "confidence is how sure you are. Requested quantities:",
        ]
        for spec in specs:
            unit = f" [{spec.unit}]" if spec.unit else ""
            lines.append(f'  - {spec.key}{unit}: {spec.description}')
        return "\n".join(lines)

    def _parse_sample(self, reply: str) -> Dict[str, dict]:
        data = extract_json(reply)
        out: Dict[str, dict] = {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("quantity_key"):
                    out[str(item["quantity_key"])] = item
        return out

    def _assemble(self, figure, specs, samples) -> List[Prediction]:
        predictions: List[Prediction] = []
        for spec in specs:
            entries = [s[spec.key] for s in samples if spec.key in s]
            if not entries:
                continue  # model did not return this quantity
            primary = entries[0]
            reads = self._numeric_reads(entries, spec)
            predictions.append(Prediction(
                figure_id=figure.figure_id,
                figure_type=figure.figure_type,
                quantity_key=spec.key,
                value_raw=str(primary.get("value", "")),
                unit=spec.unit,
                interval_low=_num(primary.get("interval_low")),
                interval_high=_num(primary.get("interval_high")),
                confidence=_num(primary.get("confidence")),
                method=str(primary.get("method", "vlm")),
                reads=reads,
            ))
        return predictions

    def _numeric_reads(self, entries: List[dict], spec: QuantitySpec) -> Dict[str, Optional[float]]:
        """Parse each sample's value to a scalar for the agreement signal.

        Only meaningful for scalar families; proportions/sets carry no numeric
        agreement, so their reads stay empty.
        """
        if spec.family not in Family.NUMERIC:
            return {}
        reads: Dict[str, Optional[float]] = {}
        for i, entry in enumerate(entries[:2]):
            parsed = parse_value(str(entry.get("value", "")), spec.unit, spec.family)
            reads[f"sample_{i}"] = parsed.scalar
        return reads

    def _load_image(self, figure: GoldFigure) -> bytes:
        if not figure.image_path:
            raise RuntimeError(f"{figure.figure_id} has no image_path")
        path = self.base_dir / figure.image_path
        if not path.exists():
            raise FileNotFoundError(f"image not found: {path}")
        return path.read_bytes()


def _num(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
