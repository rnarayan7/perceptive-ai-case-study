import { api, figureSrc, usd } from "@/lib/api";
import { TopBar } from "@/components/TopBar";
import { ApiError, Notice } from "@/components/Notice";
import { ConfidenceChip, ThesisChip, Pill } from "@/components/chips";
import { CitationLink } from "@/components/CitationLink";
import { CommentableSection } from "@/components/CommentableSection";
import type { AuditPayload, Citation, CompanyDetail, Memo, MemoSection, Tier } from "@/lib/types";

export const dynamic = "force-dynamic";

const QUESTIONS: { key: "efficacy" | "approval" | "regulatory" | "market"; label: string }[] = [
  { key: "efficacy", label: "Efficacy" },
  { key: "approval", label: "Approval" },
  { key: "regulatory", label: "Regulatory" },
  { key: "market", label: "Market" },
];

export default async function CompanyPage({
  params,
  searchParams,
}: {
  params: { ticker: string };
  searchParams: { audit?: string };
}) {
  const ticker = params.ticker.toUpperCase();
  let detail: CompanyDetail;
  let memo: Memo | null = null;
  try {
    detail = await api.company(ticker);
    if (detail.memo_id) memo = await api.companyMemo(ticker);
  } catch {
    return (
      <>
        <TopBar title={ticker} />
        <ApiError />
      </>
    );
  }

  let audit: AuditPayload | null = null;
  if (searchParams.audit) {
    try {
      audit = await api.evidence(searchParams.audit);
    } catch {
      audit = null;
    }
  }

  return (
    <>
      <TopBar title={detail.name} badge={`${ticker} · Nasdaq`} />
      <main className="space-y-4 p-7">
        {/* Header: identity strip */}
        <section className="rounded-lg border border-border bg-surface p-5">
          <div className="flex items-center gap-2">
            <Pill>{ticker}</Pill>
            <h2 className="text-xl font-semibold">{detail.name}</h2>
          </div>
          <div className="mt-1 text-sm text-secondary">
            {detail.lead_asset} · {detail.indication} · {detail.phase}
          </div>
        </section>

        {detail.status === "refused" && (
          <Notice title="Memo refused" body={detail.recommendation ?? "A required section lacked grounded evidence."} />
        )}

        {/* The call: recommendation + differentiated view (the executive summary, up top). */}
        {(detail.variant_view?.trim() || detail.recommendation) && (
          <section className="rounded-lg border border-border border-l-2 border-l-accent bg-surface p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2">
                <ThesisChip thesis={detail.thesis} />
                {detail.recommendation && (
                  <span className="text-[15px] font-semibold text-ink">{detail.recommendation}</span>
                )}
              </div>
              <span className="shrink-0 text-[11px] text-tertiary">
                catalyst and risk/reward, not a valuation verdict
              </span>
            </div>
            {detail.variant_view?.trim() && (
              <div className="mt-3">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-tertiary">
                  Our variant view · where we depart from consensus
                </div>
                <p className="mt-1.5 text-[15px] leading-6 text-ink">
                  {(detail.variant_view ?? "").replace(/\s*\[\d+\]/g, "")}
                </p>
              </div>
            )}
          </section>
        )}

        {/* KPIs */}
        <section className="grid grid-cols-4 gap-4">
          <Kpi label="Market price" value={detail.kpis.market_price ? usd(detail.kpis.market_price) : "—"} sub={detail.kpis.market_price ? "per share" : undefined} />
          <Kpi label="Market cap" value={usd(detail.kpis.market_cap)} />
          <Kpi label="Lead-asset rNPV" value={usd(detail.kpis.fair_value_usd)} sub="risk-adjusted, lead indication only" />
          <Kpi label="Lead-asset coverage" value={fmtCoverage(detail.kpis.fair_value_usd, detail.kpis.market_cap)} sub="share of market cap (a floor)" />
        </section>
        {detail.kpis.fair_value_usd != null && detail.kpis.market_cap != null && (
          <p className="-mt-2 text-[11px] text-tertiary">
            How much of the market cap the lead asset&apos;s risk-adjusted value accounts for. The rest
            is cash, the drug&apos;s other indications, the pipeline, and the higher success odds the
            market prices in. It&apos;s a floor on value, not a price target, so under 100% is normal
            and not bearish.
          </p>
        )}

        {/* Metric panels: the four questions, key findings as bullets + sources */}
        <section className="grid grid-cols-2 gap-4">
          {QUESTIONS.map((q) => {
            const m = detail.metrics[q.key];
            const sectionText = [m?.takeaway, ...(m?.points ?? [])]
              .filter(Boolean)
              .join("\n");
            return (
              <div key={q.key} className="relative rounded-lg border border-border bg-surface p-4">
                <CommentableSection
                  company={ticker}
                  sectionId={q.key}
                  sectionLabel={q.label}
                  sectionText={sectionText}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] font-semibold">{q.label}</span>
                    {m && <ConfidenceChip tier={m.confidence as Tier} />}
                  </div>
                  {m?.takeaway && (
                    <p className="mt-1.5 text-[13px] font-medium leading-5 text-ink">{m.takeaway}</p>
                  )}
                  {m?.points?.length ? (
                    <ul className="mt-2 space-y-1">
                      {m.points.map((p, i) => (
                        <li key={i} className="flex gap-1.5 text-xs leading-5 text-secondary">
                          <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-tertiary" />
                          <span>{p}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {m?.citations?.length ? (
                    <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                      <span className="text-[10px] font-semibold uppercase text-tertiary">Sources</span>
                      {m.citations.map((c) => (
                        <CitationLink
                          key={c.evidence_id}
                          ticker={ticker}
                          citation={{ marker: c.label, evidence_id: c.evidence_id, url: c.url, label: c.label }}
                        />
                      ))}
                    </div>
                  ) : null}
                </CommentableSection>
              </div>
            );
          })}
        </section>

        {/* Memo + audit */}
        <section className="flex items-start gap-4">
          <div className="flex-1 space-y-4">
            {memo ? (
              memo.sections
                // The thesis/exec-summary now lives in the call block up top; drop it here.
                .filter((s) => s.section_id !== "thesis" && (s.prose?.trim() || s.takeaway?.trim()))
                .map((s) => (
                  <div
                    key={s.section_id}
                    className="relative rounded-lg border border-border bg-surface p-6"
                  >
                    <CommentableSection
                      company={ticker}
                      sectionId={s.section_id}
                      sectionLabel={s.title}
                      sectionText={[s.takeaway, s.prose].filter(Boolean).join("\n\n")}
                    >
                      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-tertiary">
                        {s.title}
                      </h3>
                      {s.takeaway?.trim() && (
                        <p className="mt-1.5 text-[15px] font-semibold leading-6 text-ink">
                          {s.takeaway}
                        </p>
                      )}
                      {s.prose?.trim() && (
                        <MemoBody prose={s.prose} citations={s.citations} ticker={ticker} />
                      )}
                      <SectionFigures figures={s.figures} />
                    </CommentableSection>
                  </div>
                ))
            ) : (
              <div className="rounded-lg border border-border bg-surface p-6 text-sm text-secondary">
                No memo generated yet.
              </div>
            )}
          </div>

          <aside id="audit-trail" className="w-[380px] shrink-0 self-start sticky top-4 rounded-lg border border-border bg-surface p-5">
            <h3 className="text-sm font-semibold">Audit trail</h3>
            <p className="mt-0.5 text-xs text-tertiary">Click a citation in the memo to trace it.</p>
            {audit ? <AuditPanel audit={audit} /> : <div className="mt-4 text-xs text-tertiary">No claim selected.</div>}
          </aside>
        </section>
      </main>
    </>
  );
}

function Kpi({ label, value, sub, valueClass }: { label: string; value: string; sub?: string; valueClass?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="text-xs font-medium text-tertiary">{label}</div>
      <div className={`mt-1 font-mono text-[18px] ${valueClass ?? ""}`}>{value}</div>
      {sub && <div className="text-[11px] text-tertiary">{sub}</div>}
    </div>
  );
}

function fmtCoverage(fv: number | null, mc: number | null): string {
  if (fv == null || !mc) return "—";
  return `${((fv / mc) * 100).toFixed(1)}%`;
}

function AuditPanel({ audit }: { audit: AuditPayload }) {
  const { evidence, claim } = audit;
  return (
    <div className="mt-4 space-y-3">
      {claim && (
        <div className="rounded-md bg-accent-soft2 p-3">
          <div className="text-[10px] font-semibold text-accent-text">CLAIM</div>
          <div className="mt-1 text-[13px] font-medium leading-5 text-[#26303C]">{claim.statement}</div>
        </div>
      )}
      <div>
        <div className="text-[10px] font-semibold text-tertiary">SOURCE</div>
        <div className="mt-1 flex items-center gap-2 text-[13px] font-medium text-[#26303C]">
          <Pill>{evidence.doc_type}</Pill>
          {evidence.source} {evidence.date ? `· ${evidence.date}` : ""}
        </div>
      </div>
      {evidence.quote && (
        <blockquote className="border-l-2 border-border pl-3 text-[13px] italic leading-5 text-secondary">
          “{evidence.quote.slice(0, 320)}”
        </blockquote>
      )}
      <a
        href={evidence.url}
        target="_blank"
        rel="noreferrer"
        className="inline-flex rounded-md bg-accent px-3 py-2 text-[13px] font-semibold text-white"
      >
        Open source ↗
      </a>
    </div>
  );
}

// Annotated figures for a section: the image the claim rests on, inserted where the claim
// is made, with the extracted feature marked on the picture. Most sections carry none, so
// this renders nothing when empty. Each image is served by the backend figures route and
// carries a small eyebrow + its caption.
function SectionFigures({ figures }: { figures: MemoSection["figures"] }) {
  if (!figures?.length) return null;
  return (
    <div className="mt-3 space-y-3">
      {figures.map((f, i) => (
        <figure
          key={`${f.figure_id}-${i}`}
          className="overflow-hidden rounded-md border border-border bg-canvas"
        >
          <div className="border-b border-border px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-tertiary">
            Figure
          </div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={figureSrc(f.image_ref)}
            alt={f.caption || f.figure_id}
            loading="lazy"
            className="block h-auto w-full max-w-full"
          />
          {f.caption?.trim() && (
            <figcaption className="px-3 py-2 text-[12px] leading-5 text-secondary">
              {f.caption}
            </figcaption>
          )}
        </figure>
      ))}
    </div>
  );
}

// Render a memo section body. Bulleted prose (drafted by list-heavy sections like the
// regulatory path) becomes a real <ul>, one <li> per item with a leading label picked out;
// narrative prose keeps the pre-wrapped paragraph. Both run their text through linkify so
// [n] citation pills work either way.
function MemoBody({ prose, citations, ticker }: { prose: string; citations: Citation[]; ticker: string }) {
  const byMarker = new Map(citations.map((c) => [c.marker, c]));
  const lines = prose.split("\n").map((l) => l.trim()).filter(Boolean);
  const bulletLines = lines.filter((l) => /^[-*•]\s+/.test(l));

  // Treat as a list only when most non-empty lines are bullets.
  if (bulletLines.length >= 2 && bulletLines.length >= lines.length * 0.6) {
    return (
      <ul className="mt-1.5 space-y-1.5">
        {bulletLines.map((line, i) => {
          const text = line.replace(/^[-*•]\s+/, "");
          const sep = text.indexOf(": ");
          const label = sep > 0 ? text.slice(0, sep) : "";
          const useLabel = !!label && label.length <= 48 && !/[.!?]/.test(label);
          return (
            <li key={i} className="flex gap-1.5 text-[13px] leading-6 text-secondary">
              <span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-tertiary" />
              <span>
                {useLabel ? (
                  <>
                    <span className="font-medium text-ink">{label}:</span>{" "}
                    {linkify(text.slice(sep + 2), byMarker, ticker)}
                  </>
                ) : (
                  linkify(text, byMarker, ticker)
                )}
              </span>
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <p className="mt-1.5 whitespace-pre-wrap text-[13px] leading-6 text-secondary">
      {linkify(prose, byMarker, ticker)}
    </p>
  );
}

// Replace inline [n] markers in a text fragment with links to the audit drawer
// (?audit=<evidence_id>), resolving each marker against the section's citation map.
function linkify(text: string, byMarker: Map<string, Citation>, ticker: string) {
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const c = byMarker.get(part);
    if (!c) return <span key={i}>{part}</span>;
    return <CitationLink key={i} citation={c} ticker={ticker} />;
  });
}
