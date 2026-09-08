# Evaluation harness

Scores the figure extractor against a gold set. Numeric scoring is deterministic;
an LLM judge is confined to two linguistic seams (fuzzy matching, interpretive
figures) and never touches the numeric path.

## The loop
```
gold figure -> extractor -> matcher -> parse both sides -> scorer -> aggregate -> report
```

## Run it
```bash
python -m evaluation.run                 # stub extractor over all six figures
python -m evaluation.run --figure fig04_forest
python -m evaluation.run --verified-only # score only verified gold
python -m evaluation.tests.test_parse_and_score   # unit tests, no pytest needed
```

## Pieces
| File | Job |
|------|-----|
| `keys.py` | The quantity-key contract: canonical keys per figure, with family + tolerance. Both sides conform to it. |
| `types.py` | Data records (`Prediction`, `KeyedTruth`, `ScoreResult`, ...). |
| `parse.py` | Value string -> typed `ParsedValue`. Shared by predictions and gold. |
| `scorers.py` | Deterministic numeric scorers, one per family. `SCORERS` registry. |
| `match.py` | `Matcher` seam: `ExactKeyMatcher` (default), `LLMJudgeMatcher` (fuzzy). |
| `extractors.py` | `Extractor` interface + `StubExtractor`. A real extractor is a drop-in. |
| `metrics.py` | Aggregation: error by type, calibration/ECE, coverage, keystone, error categories. |
| `run.py` | The loop + CLI. |
| `gold/reference_figures.json` | Keyed gold values (the manual reads). `verified` gates scoring. |

## Reading the report
- **within_tol / MAE** per figure type: accuracy.
- **Calibration (ECE)**: is stated confidence honest.
- **Keystone (read-agreement vs error)**: does read-disagreement predict error, the signal we would triage on in production. Only catches *random* error; systematic bias (both reads wrong together) evades it by design.
- **Error categories**: kinds of miss (denominator, unit, set), not just magnitude.

## Adding a real extractor
Implement `extract(figure) -> list[Prediction]` (see `Extractor` in `extractors.py`),
tag each prediction with a `quantity_key` from `keys.py`, and pass it to
`evaluate()`. Every metric lights up unchanged.
