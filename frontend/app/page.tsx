import Link from "next/link";
import { api, usd } from "@/lib/api";
import { TopBar } from "@/components/TopBar";
import { ApiError } from "@/components/Notice";
import { ConvictionDots, ThesisChip, Pill } from "@/components/chips";
import type { CompanyRow } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  let rows: CompanyRow[];
  try {
    rows = await api.companies();
  } catch {
    return (
      <>
        <TopBar title="Companies" />
        <ApiError />
      </>
    );
  }

  const covered = rows.filter((r) => r.status !== "none").length;

  return (
    <>
      <TopBar title="Companies" badge={`${covered} of ${rows.length} covered`} />
      <main className="p-7">
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <div className="flex items-center px-5 py-4">
            <h2 className="text-[15px] font-semibold">Coverage</h2>
          </div>
          <div className="grid grid-cols-[minmax(240px,1.6fr)_90px_252px_140px_110px_110px_110px] items-center gap-x-4 border-y border-border bg-[#FAFBFC] px-5 py-2.5 text-[10px] font-semibold uppercase text-tertiary">
            <span>Company</span>
            <span>Thesis</span>
            <span>Our conviction</span>
            <span>Fair value</span>
            <span>Lead coverage</span>
            <span>Peak sales</span>
            <span>Status</span>
          </div>
          {rows.map((r) => (
            <Row key={r.ticker} r={r} />
          ))}
        </div>
      </main>
    </>
  );
}

function Row({ r }: { r: CompanyRow }) {
  const disabled = r.status === "none";
  const inner = (
    <div
      className={`grid grid-cols-[minmax(240px,1.6fr)_90px_252px_140px_110px_110px_110px] items-center gap-x-4 border-b border-sunken px-5 py-3.5 ${
        disabled ? "opacity-60" : "hover:bg-[#FAFBFC]"
      }`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[14px] font-medium">{r.ticker}</span>
          <Pill>{r.phase}</Pill>
        </div>
        <div className="truncate text-[13px] font-medium text-[#3A4351]">{r.name} · {r.lead_asset}</div>
        <div className="truncate text-xs text-tertiary">{r.indication}</div>
      </div>
      <ThesisChip thesis={r.thesis} />
      {disabled ? <span className="text-tertiary">—</span> : <ConvictionDots conviction={r.conviction} />}
      <span className="min-w-0 truncate font-mono text-[14px]">{usd(r.fair_value_usd)} {r.fair_value_usd ? <span className="text-tertiary">rNPV</span> : null}</span>
      <span className="min-w-0 truncate font-mono text-[14px]">{fmtCoverage(r.fair_value_usd, r.market_cap)}</span>
      <span className="min-w-0 truncate font-mono text-[14px]">{usd(r.peak_sales_usd)}</span>
      <span className="min-w-0 truncate text-[13px] font-medium text-secondary capitalize">{r.status === "none" ? "not run" : r.status}</span>
    </div>
  );
  return disabled ? inner : <Link href={`/company/${r.ticker}`}>{inner}</Link>;
}

function fmtCoverage(fv: number | null, mc: number | null): string {
  if (fv == null || !mc) return "—";
  return `${((fv / mc) * 100).toFixed(1)}%`;
}
