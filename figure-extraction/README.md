# Stage 1 — Figure extraction

Reads specified numeric values off chart and table images and reports each value with the method that produced it and a confidence. Runs over six oncology figures: Kaplan-Meier, waterfall, PK (log scale), forest, table, and spider.

## Requirements
- Python 3.9+
- No install for the model-free path: it is standard library only.
- For the model-backed extractors: `pip install anthropic`, plus an `ANTHROPIC_API_KEY` in `figure-extraction/.env` or the environment.

## Run
From the repo root:

```bash
# Model-free stub over the six gold figures. No key, no install.
scripts/figures.sh
# same as: cd figure-extraction && python3 -m evaluation.run

# Model-backed reads (need ANTHROPIC_API_KEY):
scripts/figures.sh vlm         # vision-model read
scripts/figures.sh combined    # vision read cross-checked against the CV readers

# Score against hand-labeled real FDA/PMC figures (always calls a model):
cd figure-extraction && python3 -m evaluation.corpus_eval --source pmc --type forest
```

Useful flags on `evaluation.run`: `--figure fig04_forest`, `--samples N`, `--verified-only`, `--prefer-vlm`, `--cv-config <file>`.

## Tests
```bash
cd figure-extraction
python3 -m evaluation.tests.test_parse_and_score   # smoke test, no pytest needed
pytest evaluation/tests                            # full suite (CV readers, VLM/judge, corpus)
```

## More
- The approach and results: `docs/stage1-approach.html`
- The evaluation harness in depth: `evaluation/README.md`
- Per-figure extracted values: `docs/extractions-table.md`
