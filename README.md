# Perceptive AI Hire — Technical Case Study

Two systems against the two briefs in [`docs/`](docs):

- **Stage 1 — figure extraction** ([`figure-extraction/`](figure-extraction)): reads specified
  numerical values off six figures, each value carrying the method that produced it and a
  confidence. A VLM read and an independent computer-vision pixel measurement are run against
  each other; where they disagree, the value is routed to an analyst.
- **Stage 2 — investment memo** ([`investment-memo/`](investment-memo)): produces an
  institutional investment memorandum on one of ABVX, KYMR, PRAX, IMVT or COGT from public
  sources, with no human intervention between initiation and finished output.

A [research workstation web app](docs/webapp-build-spec.md) ([`backend/`](backend) +
[`frontend/`](frontend)) reads what the memo engine produces. It is not a deliverable of
either brief.

## Quick start

```bash
scripts/doctor.sh              # what is missing before anything is run
scripts/stage1-eval.sh         # score the figure extractor, and report the run's cost
scripts/run-all.sh KYMR        # ingest -> compose the memo -> evaluate
```

Everything below is a thin wrapper over `python -m memo.cli` and `python -m evaluation.run`.
The CLIs remain the interface; the scripts exist so a fresh clone has an obvious front door.

| Script | What it does | Needs a key? | Needs network? |
|---|---|---|---|
| `scripts/doctor.sh` | Preflight: interpreter, venv, disk, credentials, and reachability of all 18 data hosts | no | checks it |
| `scripts/stage1-eval.sh` | Scores the figure extractor against the gold set; prints the cost of the run | yes¹ | model API only |
| `scripts/stage2-ingest.sh TICKER` | Builds the document corpus from every registered source | no | yes |
| `scripts/stage2-memo.sh TICKER` | Composes the memorandum and collects the artifacts | yes | model API only |
| `scripts/stage2-eval.sh TICKER` | Runs the evaluation suite (`--judged` adds the model-graded tier) | only with `--judged` | no |
| `scripts/run-all.sh TICKER` | Preflight → ingest → compose → evaluate | yes | yes |

¹ `--extractor stub` runs the whole harness with no model calls and no key, which is the
fastest way to confirm the scoring path works.

## Requirements

- Python 3.9+. The scripts create `.venv/` on first use and install the engine into it.
- `ANTHROPIC_API_KEY`, exported or in a `.env` at the repo root.
- Outbound access to the public data APIs. `scripts/doctor.sh` names any that are blocked —
  on a sandboxed runner this is usually an egress policy rather than a fault. Ingestion
  returns empty for a blocked source, and the memo engine refuses rather than inventing.

Stage 1 needs neither a venv nor any installed package: the extractor is standard-library
only by design, including its PNG codec and CV measurement, so `scripts/stage1-eval.sh` runs
against the system interpreter.

## Output

Runs write to `investment-memo/data/` (the working corpus and ledger, gitignored) and copy
the artifacts worth keeping to `submission/`:

```
submission/stage1/eval_report.json        scored figure extractions + the run's cost
submission/stage2/<TICKER>/memo.md        the memorandum as prose
submission/stage2/<TICKER>/memo.json      the rendered memo: claims, sections, citations
submission/stage2/<TICKER>/figures/       the figures as the system annotated them
submission/stage2/<TICKER>/trace.jsonl    the execution trace
submission/stage2/<TICKER>/compose-run.log  the full run log, including cost
```

## Where the detail is

| | |
|---|---|
| Stage 1 design and confidence model | [`figure-extraction/docs/stage1-design.md`](figure-extraction/docs/stage1-design.md) |
| Stage 1 extractions | [`figure-extraction/docs/extractions-table.md`](figure-extraction/docs/extractions-table.md) |
| Stage 1 evaluation | [`figure-extraction/docs/evaluation-summary.md`](figure-extraction/docs/evaluation-summary.md) |
| Stage 1 triage and accuracy ceiling | [`figure-extraction/docs/analysis-triage-accuracy.md`](figure-extraction/docs/analysis-triage-accuracy.md) |
| Stage 2 architecture | [`investment-memo/docs/investment-memo-architecture.md`](investment-memo/docs/investment-memo-architecture.md) |
| Stage 2 source feasibility | [`investment-memo/docs/source-ingestion-feasibility.md`](investment-memo/docs/source-ingestion-feasibility.md) |
| Deferred work, with reasons | [`investment-memo/docs/follow-ups.md`](investment-memo/docs/follow-ups.md) |

## Tests

```bash
cd investment-memo && ../.venv/bin/python -m pytest -q   # run from the package directory
cd figure-extraction && python3 -m evaluation.tests.test_parse_and_score
```

Some Stage 2 tests resolve fixtures by relative path, so they must be run from
`investment-memo/`. Tests suffixed `_live` call public APIs and fail on a sandboxed runner
with no egress.
