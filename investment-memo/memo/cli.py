"""Command-line entry point for the investment memo system.

Usage::

    python -m memo.cli ingest --source edgar --company KYMR
    python -m memo.cli ingest --source all --company KYMR --company COGT --limit 10

Sources are discovered through :data:`memo.ingestion.REGISTRY`, so new sources
need no CLI changes beyond appearing in that registry.
"""

from __future__ import annotations

import logging
import sys
from typing import List, Tuple

import click

from memo.ingestion import REGISTRY, HttpClient, IngestManifest, Storage

SOURCE_CHOICES = sorted(REGISTRY) + ["all"]


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _selected_sources(source: str) -> List[str]:
    return sorted(REGISTRY) if source == "all" else [source]


def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a local .env into the environment (no dependency).

    Existing environment variables win, so a real shell export is never overridden.
    Supports optional 'export ' prefixes and surrounding quotes; ignores comments.
    """
    import os
    from pathlib import Path

    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@click.group()
@click.version_option(package_name="memo", message="%(version)s")
def cli() -> None:
    """Investment memo system tooling."""
    _load_dotenv()


@cli.command("ingest")
@click.option(
    "--source",
    type=click.Choice(SOURCE_CHOICES),
    required=True,
    help="Which source to ingest, or 'all' for every registered source.",
)
@click.option(
    "--company",
    "companies",
    multiple=True,
    required=True,
    help="Company ticker (e.g. KYMR). Repeatable to ingest several.",
)
@click.option("--limit", type=int, default=None, help="Max documents per source.")
@click.option(
    "--data-root",
    type=click.Path(file_okay=False),
    default="data",
    show_default=True,
    help="Directory the fetched documents are written under.",
)
@click.option("--force", is_flag=True, help="Re-write documents even if unchanged.")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def ingest(
    source: str,
    companies: Tuple[str, ...],
    limit: int,
    data_root: str,
    force: bool,
    verbose: bool,
) -> None:
    """Fetch and store public documents for one or more companies."""
    _configure_logging(verbose)

    sources = _selected_sources(source)
    storage = Storage(root=data_root)
    http = HttpClient()  # shared across sources so rate limiting is global

    options = {}
    if limit is not None:
        options["limit"] = limit

    manifests: List[IngestManifest] = []
    for company in companies:
        for src in sources:
            ingester = REGISTRY[src](http=http, storage=storage)
            manifest = ingester.run(company, force=force, **options)
            manifests.append(manifest)

    _print_summary(manifests)

    if any(m.errors for m in manifests):
        sys.exit(1)


@cli.command("search")
@click.argument("query")
@click.option("--company", required=True, help="Company ticker to search within.")
@click.option("--source", default=None, help="Restrict to one source (edgar, clinicaltrials).")
@click.option("--doc-type", default=None, help="Restrict to one document type (e.g. 10-K).")
@click.option("-k", "top_k", type=int, default=5, show_default=True, help="Results to return.")
@click.option("--since", default=None, help="Only documents on/after this ISO date (YYYY-MM-DD).")
@click.option("--until", default=None, help="Only documents on/before this ISO date (YYYY-MM-DD).")
@click.option(
    "--data-root",
    type=click.Path(file_okay=False),
    default="data",
    show_default=True,
    help="Directory the corpus was ingested into.",
)
def search(query, company, source, doc_type, top_k, since, until, data_root) -> None:
    """Lexically retrieve chunks for QUERY from a company's ingested corpus."""
    from memo.ingestion.base import Storage
    from memo.rag import build_retriever

    retriever = build_retriever(company, storage=Storage(root=data_root))
    results = retriever.retrieve(
        query, k=top_k, company=company, source=source, doc_type=doc_type,
        since=since, until=until,
    )

    if not results:
        click.echo("No results. Has this company been ingested?")
        return

    click.echo(f'Top {len(results)} for "{query}" in {company}:')
    click.echo("-" * 60)
    for rank, result in enumerate(results, 1):
        chunk = result.chunk
        snippet = " ".join(chunk.text.split())[:220]
        click.echo(f"{rank}. [{result.score:.2f}] {chunk.source} {chunk.doc_type}  {chunk.url}")
        click.echo(f"   {snippet}...")


@cli.command("trials")
@click.option("--company", required=True, help="Company ticker.")
@click.option(
    "--data-root",
    type=click.Path(file_okay=False),
    default="data",
    show_default=True,
)
def trials(company, data_root) -> None:
    """Show ingested clinical trials for a company (structured lookup, no retrieval)."""
    from memo.ingestion.base import Storage
    from memo.rag import StructuredStore

    records = StructuredStore(company, storage=Storage(root=data_root)).trials()
    if not records:
        click.echo("No trials. Has this company been ingested?")
        return
    click.echo(f"{len(records)} trials for {company}:")
    click.echo("-" * 60)
    for t in records:
        phase = "/".join(t.phases) or "-"
        drug = t.interventions[0] if t.interventions else "-"
        click.echo(f"  {t.nct_id}  {phase:<10} {(t.status or '-'):<12} n={t.enrollment}  {drug}")


@cli.command("filings")
@click.option("--company", required=True, help="Company ticker.")
@click.option("--form", "forms", multiple=True, help="Restrict to form type(s), e.g. 10-K. Repeatable.")
@click.option(
    "--data-root",
    type=click.Path(file_okay=False),
    default="data",
    show_default=True,
)
def filings(company, forms, data_root) -> None:
    """Show ingested EDGAR filings for a company (structured lookup, no retrieval)."""
    from memo.ingestion.base import Storage
    from memo.rag import StructuredStore

    records = StructuredStore(company, storage=Storage(root=data_root)).filings(
        forms=list(forms) or None
    )
    if not records:
        click.echo("No filings. Has this company been ingested?")
        return
    click.echo(f"{len(records)} filings for {company}:")
    click.echo("-" * 60)
    for f in records:
        click.echo(f"  {(f.filing_date or '-'):<12} {f.form:<8} {f.url}")


@cli.command("eval")
@click.option("--type", "eval_type",
              type=click.Choice(["retrieval", "faithfulness", "full", "memo",
                                 "judge-probe", "known-bad"]),
              default="retrieval", show_default=True, help="Which evaluation to run.")
@click.option("--company", default=None, help="Company ticker (not needed for judge-probe).")
@click.option("-k", "top_k", type=int, default=8, show_default=True, help="Retrieval depth.")
@click.option("--module", "module_name", type=click.Choice(["moa", "pos", "regulatory", "peak_sales", "price"]), default="moa",
              show_default=True, help="Analysis module to grade (faithfulness/full).")
@click.option("--judge-model", default=None, help="Claude model id for the faithfulness judge.")
@click.option("--gold-root", type=click.Path(file_okay=False), default="evals", show_default=True)
@click.option("--data-root", type=click.Path(file_okay=False), default="data", show_default=True)
@click.option("--save/--no-save", default=True, show_default=True,
              help="Persist a timestamped JSON report under evals/results/.")
def eval_cmd(eval_type, company, top_k, module_name, judge_model, gold_root, data_root, save) -> None:
    """Run an evaluation and report metrics.

    retrieval: deterministic, no model. faithfulness: grades an analysis module's claims
    with a real Claude judge. full: retrieval + analysis + faithfulness, with cost totals.
    """
    from pathlib import Path

    from memo.eval import format_report, save_report

    if eval_type == "memo":
        _run_memo_eval(company, judge_model, data_root, save)
        return
    if eval_type == "judge-probe":
        _run_judge_probe(judge_model)
        return
    if not company:
        raise click.UsageError(f"--company is required for eval type '{eval_type}'")
    if eval_type == "known-bad":
        _run_known_bad(company, judge_model, data_root)
        return

    reports = []
    if eval_type in ("retrieval", "full"):
        reports.append(_run_retrieval_eval(company, top_k, Path(gold_root), data_root))
    if eval_type in ("faithfulness", "full"):
        reports.append(_run_faithfulness_eval(company, module_name, judge_model, data_root))

    for report in reports:
        click.echo(format_report(report))
        click.echo("")
        if save:
            click.echo(f"saved: {save_report(report)}")
            click.echo("")


def _run_retrieval_eval(company, top_k, gold_root, data_root):
    from memo.eval import RetrievalEvaluator, load_goldset_for
    from memo.ingestion.base import Storage
    from memo.rag import build_retriever

    goldset = load_goldset_for("retrieval", company, root=gold_root)
    retriever = build_retriever(company, storage=Storage(root=data_root))
    return RetrievalEvaluator(goldset, retriever, k=top_k).run()


def _run_faithfulness_eval(company, module_name, judge_model, data_root):
    from memo.analysis import REGISTRY as ANALYSIS_REGISTRY
    from memo.analysis import AnalysisContext, AnthropicModelClient
    from memo.eval import FaithfulnessEvaluator
    from memo.ingestion.base import Storage
    from memo.trace import RunSession

    storage = Storage(root=data_root)
    # One session over both the generator and the judge, so the run's tokens (and the
    # approximate dollar figure) cover the whole eval, not just generation.
    session = RunSession()
    generator = AnthropicModelClient()
    generator.session = session
    session.model = generator.model
    context = AnalysisContext.for_company(company, model=generator, storage=storage)
    analysis = ANALYSIS_REGISTRY[module_name]().analyze(context)
    click.echo(f"generated {len(analysis.claims)} {module_name} claims "
               f"(analysis tokens: {analysis.usage})")

    judge = AnthropicModelClient(model=judge_model) if judge_model else AnthropicModelClient()
    judge.session = session
    report = FaithfulnessEvaluator(analysis, judge).run()
    click.echo(session.summary())
    return report


def _run_memo_eval(company, judge_model, data_root, save) -> None:
    """Grade a company's latest memo (from the ledger) for faithfulness + quality per module.

    Judge-only, no re-generation: reconstructs each module section's claims from the ledger.
    Faithfulness is graded over grounded claims (evidence-backed); quality over all claims.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from memo.analysis.base import AnalysisResult, Claim, Evidence
    from memo.analysis.model import AnthropicModelClient
    from memo.eval import FaithfulnessEvaluator, QualityEvaluator
    from memo.ledger import LedgerStore

    ledger = LedgerStore(db_path=str(Path(data_root) / "ledger.db"))
    memo = ledger.latest_memo(company)
    if memo is None:
        click.echo(f"no memo for {company}; run `compose --company {company}` first")
        return

    judge = (AnthropicModelClient(model=judge_model) if judge_model
             else AnthropicModelClient(model="claude-sonnet-5"))
    # (memo section id, analysis module name)
    modules = [("moa", "moa"), ("pos", "pos"), ("regulatory", "regulatory"),
               ("peak_sales", "peak_sales"), ("valuation", "price")]

    def to_analysis(section, module, grounded_only):
        claims = []
        for c in ledger.get_claims(memo.memo_id, section):
            if grounded_only and not c.evidence:
                continue
            ev = [Evidence(doc_id=e.doc_id, source=e.source, doc_type=e.doc_type, url=e.url,
                           quote=e.quote, date=e.date, chunk_id=e.chunk_id) for e in c.evidence]
            claims.append(Claim(statement=c.statement, confidence=c.confidence,
                                rationale=c.rationale, evidence=ev, value=c.value))
        return AnalysisResult(company=company, module=module, summary="", claims=claims)

    def fmt(x):
        return f"{x:.3f}" if x is not None else "n/a"

    click.echo(f"grading memo {memo.memo_id} ({memo.status}) with judge {judge.model}")
    click.echo(f"{'module':12}{'faith':>8}{'supp':>8}{'contra':>8}{'quality':>9}{'graded':>8}")
    click.echo("-" * 52)
    rows = []
    jin = jout = 0
    for section, module in modules:
        grounded = to_analysis(section, module, True)
        full = to_analysis(section, module, False)
        fr = FaithfulnessEvaluator(grounded, judge).run() if grounded.claims else None
        qr = QualityEvaluator(full, judge).run()
        for params in ([fr.params] if fr else []) + [qr.params]:
            jin += params.get("judge_input_tokens", 0)
            jout += params.get("judge_output_tokens", 0)
        faith = fr.aggregate.get("faithfulness") if fr else None
        supp = fr.aggregate.get("supported") if fr else None
        contra = fr.aggregate.get("contradicted") if fr else None
        qual = qr.aggregate.get("quality")
        click.echo(f"{module:12}{fmt(faith):>8}{fmt(supp):>8}{fmt(contra):>8}{fmt(qual):>9}"
                   f"{len(grounded.claims):>8}")
        rows.append({"module": module, "faithfulness": faith, "supported": supp,
                     "contradicted": contra, "quality": qual, "graded": len(grounded.claims)})
    click.echo("-" * 52)
    click.echo(f"judge tokens: in={jin} out={jout}")

    if save:
        root = Path("evals") / "results"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = root / f"memo_{company}_{stamp}.json"
        path.write_text(json.dumps({
            "company": company, "memo_id": memo.memo_id, "status": memo.status,
            "judge": judge.model, "rows": rows, "judge_tokens": {"in": jin, "out": jout},
        }, indent=2))
        click.echo(f"saved: {path}")


@cli.command("analyze")
@click.option("--module", "module_name", type=click.Choice(["moa", "pos", "regulatory", "peak_sales", "price"]), default="moa",
              show_default=True, help="Which analysis module to run.")
@click.option("--company", required=True, help="Company ticker to analyze.")
@click.option("--model", default=None, help="Override the Claude model id.")
@click.option("--data-root", type=click.Path(file_okay=False), default="data", show_default=True)
def analyze(module_name, company, model, data_root) -> None:
    """Run an analysis module against a company (calls a real Claude model)."""
    from memo.analysis import REGISTRY as ANALYSIS_REGISTRY
    from memo.analysis import AnalysisContext, AnthropicModelClient
    from memo.ingestion.base import Storage

    client = AnthropicModelClient(model=model) if model else AnthropicModelClient()
    context = AnalysisContext.for_company(company, model=client, storage=Storage(root=data_root))
    result = ANALYSIS_REGISTRY[module_name]().analyze(context)

    click.echo(f"{result.module} analysis | {result.company} | confidence {result.confidence:.2f}")
    click.echo("=" * 64)
    click.echo(result.summary)
    click.echo("")
    for i, claim in enumerate(result.claims, 1):
        click.echo(f"{i}. [{claim.confidence:.2f}] {claim.statement}")
        for ev in claim.evidence:
            click.echo(f"     - {ev.source} {ev.doc_type} {ev.url}")
    if result.notes:
        click.echo("")
        click.echo("Notes:")
        for note in result.notes:
            click.echo(f"  ! {note}")
    if result.usage:
        click.echo("")
        click.echo(f"tokens: {result.usage}")


@cli.command("compose")
@click.option("--company", required=True, help="Company ticker to write a memo for.")
@click.option("--model", default=None, help="Override the Claude model id (default opus).")
@click.option("--data-root", type=click.Path(file_okay=False), default="data", show_default=True)
@click.option("--markdown/--no-markdown", default=False,
              help="Also print the rendered memo as Markdown.")
def compose(company, model, data_root, markdown) -> None:
    """Compose a full memo for a company (runs analysis + drafting + thesis via Claude).

    Writes the memo to the ledger and a rendered-memo JSON, and an execution trace under
    <data-root>/traces/. This makes real (paid) model calls.
    """
    from pathlib import Path

    from memo.analysis import AnthropicModelClient
    from memo.compose import compose_memo, render_markdown_for
    from memo.ledger import LedgerStore
    from memo.ingestion.base import Storage
    from memo.trace import JsonlTracer, RunSession

    storage = Storage(root=data_root)
    ledger = LedgerStore(db_path=str(Path(data_root) / "ledger.db"))
    tracer = JsonlTracer(root=str(Path(data_root) / "traces"))
    client = AnthropicModelClient(model=model) if model else AnthropicModelClient()
    session = RunSession()

    memo_id = compose_memo(company, client, storage=storage, ledger=ledger,
                           tracer=tracer, session=session)

    memo = ledger.get_memo(memo_id)
    claims = ledger.get_claims(memo_id)
    click.echo("")
    click.echo(f"memo_id:        {memo_id}")
    click.echo(f"status:         {memo.status if memo else 'unknown'}")
    if memo and memo.recommendation:
        click.echo(f"recommendation: {memo.recommendation}")
    click.echo(f"claims:         {len(claims)}")
    click.echo(f"artifact:       {data_root}/memos/{memo_id}.json")
    click.echo(f"ledger:         {data_root}/ledger.db")
    click.echo(f"trace:          {data_root}/traces/{memo_id}.jsonl")
    click.echo(session.summary())
    if memo and memo.notes:
        click.echo(f"notes:          {memo.notes}")
    if markdown:
        click.echo("")
        click.echo(render_markdown_for(memo_id, storage))


def _run_judge_probe(judge_model) -> None:
    """Validate the faithfulness judge on known-answer probes."""
    from memo.analysis import AnthropicModelClient
    from memo.eval import load_probes, run_probes

    probes = load_probes("evals/judge_probes/faithfulness.json")
    judge = (AnthropicModelClient(model=judge_model) if judge_model
             else AnthropicModelClient(model="claude-sonnet-5"))
    click.echo(f"judge-probe: {len(probes)} known-answer cases, judge {judge.model}")
    r = run_probes(judge, probes)
    click.echo(f"exact-verdict accuracy:      {r['exact_accuracy']:.2f}")
    click.echo(f"binary faithful/unfaithful:  {r['binary_accuracy']:.2f}")
    misses = [row for row in r["rows"] if not row["exact"]]
    if misses:
        click.echo("misses:")
        for row in misses:
            flag = "" if row["binary"] else "   [BINARY MISS]"
            click.echo(f"  {row['id']}: expected {row['expected']}, got {row['got']}{flag}")


def _run_known_bad(company, judge_model, data_root, cap: int = 24) -> None:
    """Discrimination test: real faithfulness vs a corrupted (evidence-shuffled) memo."""
    from pathlib import Path

    from memo.analysis import AnthropicModelClient
    from memo.analysis.base import AnalysisResult, Claim, Evidence
    from memo.eval import known_bad_baseline
    from memo.ledger import LedgerStore

    ledger = LedgerStore(db_path=str(Path(data_root) / "ledger.db"))
    memo = ledger.latest_memo(company)
    if memo is None:
        click.echo(f"no memo for {company}; run `compose --company {company}` first")
        return
    claims = []
    for sec in ("moa", "pos", "regulatory", "valuation"):
        for c in ledger.get_claims(memo.memo_id, sec):
            if c.evidence:
                ev = [Evidence(doc_id=e.doc_id, source=e.source, doc_type=e.doc_type, url=e.url,
                               quote=e.quote, date=e.date, chunk_id=e.chunk_id) for e in c.evidence]
                claims.append(Claim(statement=c.statement, confidence=c.confidence,
                                    rationale=c.rationale, evidence=ev))
    claims = claims[:cap]
    judge = (AnthropicModelClient(model=judge_model) if judge_model
             else AnthropicModelClient(model="claude-sonnet-5"))
    click.echo(f"known-bad: {len(claims)} grounded claims from {memo.memo_id}, judge {judge.model}")
    r = known_bad_baseline(AnalysisResult(company, "mixed", "", claims), judge)
    click.echo(f"real faithfulness:      {r['real']:.3f}")
    click.echo(f"corrupted faithfulness: {r['corrupted']:.3f}")
    click.echo(f"gap: {r['gap']:.3f}   discriminates (>0.2): {r['discriminates']}")


@cli.command("calibrate")
@click.option("--companies", default="KYMR,ABVX,IMVT,PRAX,COGT", show_default=True,
              help="Companies to sample claims from.")
@click.option("-n", "n", type=int, default=40, show_default=True, help="Claims to sample.")
@click.option("--out", default="evals/calibration/sample.json", show_default=True)
@click.option("--score", "score_path", default=None,
              help="Instead of sampling, score a labeled file (judge vs human).")
@click.option("--judge-model", default=None)
@click.option("--data-root", type=click.Path(file_okay=False), default="data", show_default=True)
def calibrate(companies, n, out, score_path, judge_model, data_root) -> None:
    """Prepare a human-labeling sample of claims, or score a labeled file for judge agreement."""
    import json
    from pathlib import Path

    from memo.eval import sample_calibration, score_calibration
    from memo.ledger import LedgerStore

    if score_path:
        from memo.analysis import AnthropicModelClient
        labeled = json.loads(Path(score_path).read_text())
        judge = (AnthropicModelClient(model=judge_model) if judge_model
                 else AnthropicModelClient(model="claude-sonnet-5"))
        r = score_calibration(labeled, judge)
        kappa = f"{r['kappa']:.2f}" if r["kappa"] is not None else "n/a"
        click.echo(f"labeled: {r['n']}   judge-vs-human agreement: {r['agreement']:.2f}   "
                   f"kappa: {kappa}")
        return

    ledger = LedgerStore(db_path=str(Path(data_root) / "ledger.db"))
    pool = []
    for co in [c.strip() for c in companies.split(",") if c.strip()]:
        memo = ledger.latest_memo(co)
        if not memo:
            continue
        for c in ledger.get_claims(memo.memo_id):
            if c.evidence:
                pool.append({"claim_id": c.claim_id, "company": co, "module": c.module,
                             "statement": c.statement, "evidence": [e.quote for e in c.evidence]})
    sample = sample_calibration(pool, n)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(sample, indent=2))
    click.echo(f"wrote {len(sample)} claims to {out}")
    click.echo("Fill each 'human_verdict' (supported|partial|unsupported|contradicted), "
               f"then run: python -m memo.cli calibrate --score {out}")


def _print_summary(manifests: List[IngestManifest]) -> None:
    click.echo("")
    click.echo("Ingestion summary")
    click.echo("-" * 48)
    for m in manifests:
        status = "ok" if not m.errors else f"{len(m.errors)} error(s)"
        click.echo(f"  {m.company:<8} {m.source:<16} {m.document_count:>4} docs  [{status}]")
        for err in m.errors:
            click.echo(f"      ! {err}")
    total = sum(m.document_count for m in manifests)
    click.echo("-" * 48)
    click.echo(f"  total: {total} documents across {len(manifests)} run(s)")


if __name__ == "__main__":
    cli()
