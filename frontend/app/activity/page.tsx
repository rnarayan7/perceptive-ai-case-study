import { api } from "@/lib/api";
import { TopBar } from "@/components/TopBar";
import { ApiError } from "@/components/Notice";
import type { GenerationRun, IngestionSource } from "@/lib/types";

export const dynamic = "force-dynamic";

function statusChip(status: string) {
  const s = status === "completed"
    ? "text-conf-high bg-conf-high-soft"
    : status === "refused"
    ? "text-short bg-short-soft"
    : "text-conf-med bg-conf-med-soft";
  return <span className={`rounded-pill px-2 py-0.5 text-[11px] font-semibold ${s}`}>{status}</span>;
}

// Freshness derived from last_ingested, mirroring statusChip so both tables share one chip idiom.
function freshnessChip(lastIngested: string | null) {
  if (!lastIngested) return <span className="text-[13px] text-secondary">—</span>;
  const days = (Date.now() - new Date(lastIngested).getTime()) / 86_400_000;
  const [label, s] = days <= 7
    ? ["fresh", "text-conf-high bg-conf-high-soft"]
    : days <= 30
    ? ["recent", "text-conf-med bg-conf-med-soft"]
    : ["stale", "text-short bg-short-soft"];
  return <span className={`rounded-pill px-2 py-0.5 text-[11px] font-semibold ${s}`}>{label}</span>;
}

const INGESTION_COLS = "grid-cols-[150px_1fr_100px_110px_130px_90px]";

export default async function ActivityPage() {
  let runs: GenerationRun[];
  try {
    runs = await api.generations();
  } catch {
    return (
      <>
        <TopBar title="Activity" />
        <ApiError />
      </>
    );
  }

  // Ingestion is best-effort: a failure here should not hide the generations table.
  let sources: IngestionSource[] = [];
  try {
    sources = await api.ingestion();
  } catch {
    sources = [];
  }

  return (
    <>
      <TopBar title="Activity" badge={`${runs.length} runs`} />
      <main className="p-7">
        {sources.length > 0 && (
          <div className="mb-7 overflow-hidden rounded-lg border border-border bg-surface">
            <div className="flex items-center px-5 py-4">
              <h2 className="text-[15px] font-semibold">Ingested datasets</h2>
            </div>
            <div className={`grid ${INGESTION_COLS} items-center border-y border-border bg-[#FAFBFC] px-5 py-2.5 text-[10px] font-semibold uppercase text-tertiary`}>
              <span>Last pulled</span><span>Dataset</span><span>Coverage</span><span>Documents</span><span>Latest item</span><span>Freshness</span>
            </div>
            {sources.map((s) => (
              <div key={s.source} className={`grid ${INGESTION_COLS} items-center border-b border-sunken px-5 py-3.5`}>
                <span className="text-[13px] text-secondary">
                  {s.last_ingested ? new Date(s.last_ingested).toLocaleString() : "—"}
                </span>
                <span className="font-mono text-[13px] font-medium">{s.label}</span>
                <span className="font-mono text-[13px] text-secondary">{s.company_count} of 5</span>
                <span className="font-mono text-[13px]">{s.doc_count.toLocaleString()}</span>
                <span className="text-[13px] text-secondary">{s.latest_item ?? "—"}</span>
                <span>{freshnessChip(s.last_ingested)}</span>
              </div>
            ))}
            <p className="px-5 py-3 text-xs text-tertiary">Live public sources feeding coverage: trial registries, filings, biomedical literature, and pricing.</p>
          </div>
        )}

        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <div className="flex items-center px-5 py-4">
            <h2 className="text-[15px] font-semibold">Thesis generations</h2>
          </div>
          <div className="grid grid-cols-[150px_90px_140px_1fr_120px_90px_80px] items-center border-y border-border bg-[#FAFBFC] px-5 py-2.5 text-[10px] font-semibold uppercase text-tertiary">
            <span>When</span><span>Company</span><span>Trigger</span><span>Output</span><span>Status</span><span>Cost</span><span>Duration</span>
          </div>
          {runs.length === 0 && <div className="px-5 py-6 text-sm text-secondary">No generations yet.</div>}
          {runs.map((r) => (
            <div key={r.run_id} className="grid grid-cols-[150px_90px_140px_1fr_120px_90px_80px] items-center border-b border-sunken px-5 py-3.5">
              <span className="text-[13px] text-secondary">{new Date(r.ran_at).toLocaleString()}</span>
              <span className="font-mono text-[13px] font-medium">{r.company}</span>
              <span className="text-xs text-secondary">{r.trigger}</span>
              <span className="text-[13px] text-[#3A4351]">{r.output}</span>
              <span>{statusChip(r.status)}</span>
              <span className="font-mono text-[13px]">${r.cost_usd.toFixed(2)}</span>
              <span className="font-mono text-[13px] text-secondary">{Math.round(r.duration_s)}s</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-tertiary">Cost + tokens shown for now as a dev aid; will be internal once analysts use the app.</p>
      </main>
    </>
  );
}
