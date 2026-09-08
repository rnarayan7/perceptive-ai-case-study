"""Labeling core: turn harvested figures into verified (image, answer) pairs.

Shared library behind the local web review app (:mod:`corpus.label_server`). It
holds the pieces that touch data: progress state, writing verified values into a
figure's ``record.json``, and an optional VLM suggester. No UI lives here, so it
stays unit-testable and the server is a thin HTTP layer over these functions.

Verified values are written as ``method="manual", verified=True`` so
``corpus.cli stats`` and the eval pick them up. Re-saving a figure replaces its
manual values (idempotent) while leaving auto-extracted candidates untouched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from corpus.base import DEFAULT_DATA_ROOT, FigureStorage, safe_name

STATE_DIR = DEFAULT_DATA_ROOT / "_labels"
STATE_PATH = STATE_DIR / "state.json"


# -- progress state ---------------------------------------------------------

def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(state: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def set_status(figure_id: str, status: str) -> None:
    state = load_state()
    state[figure_id] = {"status": status, "at": datetime.now(timezone.utc).isoformat()}
    save_state(state)


# -- record read/write ------------------------------------------------------

def record_dir(storage: FigureStorage, source: str, figure_id: str) -> Path:
    return storage.source_dir(source) / safe_name(figure_id)


def set_labels(record_path: Path, rows: List[Dict[str, Any]]) -> int:
    """Replace a record's manual verified values with ``rows``; return the count.

    Rows are ``{quantity, value, source_text?}``. Empty values are dropped. Prior
    ``method="manual"`` entries are removed first, so re-saving a figure updates
    rather than duplicates; non-manual entries (regex candidates) are preserved.
    """
    record = json.loads(record_path.read_text())
    gt = record.get("ground_truth") or []
    kept = [g for g in gt if g.get("method") != "manual"]
    for r in rows:
        value = str(r.get("value", "")).strip()
        if not value:
            continue
        kept.append({
            "quantity": (r.get("quantity") or "value").strip(), "value": value,
            "unit": None, "population_n": None, "method": "manual",
            "source_text": r.get("source_text"), "verified": True,
        })
    record["ground_truth"] = kept
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return sum(1 for g in kept if g.get("method") == "manual")


# -- VLM suggestions --------------------------------------------------------

# Per-type guidance for the suggester. It only needs to propose a useful starting
# point; the human verifies against the image, so a fast model is fine.
_SUGGEST_HINTS = {
    "kaplan_meier": "For each arm: median survival in months, and the survival probability at any marked landmark.",
    "forest": "For each row: the hazard ratio with its 95% CI; and list which rows' CI cross 1.",
    "waterfall": "The proportion of bars beyond key thresholds (e.g. -30%, -50%), the deepest and leftmost bar values, and the number of bars.",
    "pk_logscale": "The concentration at key timepoints for each dose, and any marked threshold value.",
    "table": "The key efficacy/safety cells with their values and the denominator each is over.",
    "spider": "Any implied response or PFS summary readable from the trajectories (median, landmark).",
}


def vlm_suggestions(image_path: Path, figure_type: str, context: str, client) -> List[Dict[str, Any]]:
    """Ask a vision model to propose readable values. Returns row dicts.

    Degrades to an empty list on any failure (missing key, API error), so the app
    still works without a model. ``client`` is injected for testability.
    """
    from evaluation.llm import extract_json, media_type_for  # lazy: only the --vlm path needs it

    try:
        image = image_path.read_bytes()
    except OSError:
        return []
    hint = _SUGGEST_HINTS.get(figure_type, "List any labeled numeric values readable from the figure.")
    prompt = (
        f"You are reading a {figure_type} figure from an oncology presentation. {hint}\n"
        "Read values directly from the graphic. Return ONLY a JSON array; each "
        'element {"quantity": "<short label>", "value": "<value with unit>"}. '
        "Omit anything not readable; return [] if nothing is readable."
    )
    try:
        reply = client.complete(prompt, image=image,
                                media_type=media_type_for(image_path.suffix), max_tokens=1200)
    except Exception:  # noqa: BLE001 - any failure: no suggestions, app still works
        return []
    data = extract_json(reply)
    out: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("quantity") and item.get("value") is not None:
                out.append({"quantity": str(item["quantity"]), "value": str(item["value"]),
                            "source_text": "VLM suggestion"})
    return out
