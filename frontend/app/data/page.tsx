import Link from "next/link";
import { api, figureImageSrc } from "@/lib/api";
import { TopBar } from "@/components/TopBar";
import { ApiError } from "@/components/Notice";
import { TypeChip } from "@/components/TypeChip";
import type { DocumentRow, FigureOrigin, FigureRow } from "@/lib/types";

export const dynamic = "force-dynamic";

const COMPANIES = ["ABVX", "KYMR", "PRAX", "IMVT", "COGT"];
const DOC_TYPES = [
  "10-K", "10-Q", "20-F", "8-K", "8-K/A", "6-K", "424B3", "424B5", "424B7",
  "xbrl_fact", "study", "article", "preprint", "compound", "asp_price", "epi", "prevalence",
];

const COLS = "grid grid-cols-[minmax(320px,2fr)_110px_90px_120px_90px_90px] items-center";

const ORIGIN_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "company", label: "Company" },
  { value: "stage1", label: "Stage-1" },
  { value: "corpus", label: "Corpus" },
];

const ORIGIN_GROUPS: { origin: FigureOrigin; label: string }[] = [
  { origin: "company", label: "Company memo figures" },
  { origin: "stage1", label: "Stage-1 brief figures" },
  { origin: "corpus", label: "Harvested corpus figures" },
];

export default async function DataPage({
  searchParams,
}: {
  searchParams: { company?: string; doc_type?: string; q?: string; view?: string; origin?: string };
}) {
  const company = searchParams.company || "";
  const docType = searchParams.doc_type || "";
  const q = searchParams.q || "";
  const origin = searchParams.origin || "";
  const view = searchParams.view === "figures" ? "figures" : "documents";

  if (view === "figures") {
    let figures: FigureRow[];
    try {
      figures = await api.figures({ company, origin });
    } catch {
      return (
        <>
          <TopBar title="Data" />
          <ApiError />
        </>
      );
    }
    const groups = ORIGIN_GROUPS.map((g) => ({
      ...g,
      rows: figures.filter((f) => f.origin === g.origin),
    })).filter((g) => g.rows.length > 0);
    return (
      <>
        <TopBar title="Data" badge={`${figures.length} figures`} />
        <main className="space-y-4 p-7">
          <Toggle view="figures" company={company} />
          <div className="flex flex-wrap items-center gap-3">
            <div className="inline-flex rounded-md border border-border bg-surface p-0.5">
              {ORIGIN_FILTERS.map((o) => {
                const params = new URLSearchParams({ view: "figures" });
                if (o.value) params.set("origin", o.value);
                if (company) params.set("company", company);
                const active = origin === o.value;
                return (
                  <Link
                    key={o.value || "all"}
                    href={`/data?${params.toString()}`}
                    className={
                      active
                        ? "rounded-[6px] bg-accent px-3 py-1.5 text-[13px] font-semibold text-white"
                        : "rounded-[6px] px-3 py-1.5 text-[13px] font-semibold text-tertiary hover:text-secondary"
                    }
                  >
                    {o.label}
                  </Link>
                );
              })}
            </div>
            {company && (
              <Link
                href={`/data?view=figures${origin ? `&origin=${origin}` : ""}`}
                className="text-[13px] font-medium text-secondary hover:text-primary"
              >
                Clear {company}
              </Link>
            )}
          </div>

          {figures.length === 0 ? (
            <div className="rounded-lg border border-border bg-surface px-5 py-6 text-sm text-secondary">
              No figures for this filter.
            </div>
          ) : (
            <div className="space-y-6">
              {groups.map((g) => (
                <section key={g.origin} className="space-y-3">
                  <h2 className="flex items-baseline gap-2 text-[13px] font-semibold text-primary">
                    {g.label}
                    <span className="font-mono text-[11px] font-medium text-tertiary">{g.rows.length}</span>
                  </h2>
                  <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
                    {g.rows.map((f) => (
                      <FigureCard key={`${f.origin}-${f.company}-${f.figure_id}`} f={f} />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </main>
      </>
    );
  }

  let rows: DocumentRow[];
  try {
    rows = await api.documents({ company, doc_type: docType, q });
  } catch {
    return (
      <>
        <TopBar title="Data" />
        <ApiError />
      </>
    );
  }

  return (
    <>
      <TopBar title="Data" badge={`${rows.length} documents`} />
      <main className="space-y-4 p-7">
        <Toggle view="documents" company={company} />

        {/* Filters */}
        <form method="get" className="flex flex-wrap items-center gap-2">
          <Select name="company" value={company} placeholder="All companies" options={COMPANIES} />
          <Select name="doc_type" value={docType} placeholder="All types" options={DOC_TYPES} />
          <input
            name="q"
            defaultValue={q}
            placeholder="Search title…"
            className="h-9 w-64 rounded-md border border-border bg-surface px-3 text-[13px] outline-none focus:border-accent"
          />
          <button
            type="submit"
            className="h-9 rounded-md bg-accent px-3 text-[13px] font-semibold text-white"
          >
            Filter
          </button>
          {(company || docType || q) && (
            <Link href="/data" className="h-9 rounded-md border border-border px-3 text-[13px] font-medium leading-9 text-secondary">
              Clear
            </Link>
          )}
        </form>

        {/* Table */}
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <div className={`${COLS} border-b border-border bg-[#FAFBFC] px-5 py-2.5 text-[10px] font-semibold uppercase text-tertiary`}>
            <span>Source</span>
            <span>Type</span>
            <span>Company</span>
            <span>Date</span>
            <span>Figures</span>
            <span>Cited by</span>
          </div>
          {rows.length === 0 && (
            <div className="px-5 py-6 text-sm text-secondary">No documents match these filters.</div>
          )}
          {rows.map((r) => (
            <Link
              key={r.doc_id}
              href={`/data/document/${encodeURIComponent(r.doc_id)}`}
              className={`${COLS} border-b border-sunken px-5 py-3.5 hover:bg-[#FAFBFC]`}
            >
              <div className="min-w-0 pr-4">
                <div className="truncate text-[13px] font-medium text-[#26303C]">{r.title}</div>
                <div className="truncate font-mono text-[11px] text-tertiary">{r.source} · {r.doc_id}</div>
              </div>
              <span><TypeChip docType={r.doc_type} /></span>
              <span className="font-mono text-[13px] font-medium">{r.company}</span>
              <span className="font-mono text-[13px] text-secondary">{r.published ?? "—"}</span>
              <span className="font-mono text-[13px] text-tertiary">{r.figure_count || "—"}</span>
              <span className="font-mono text-[13px] text-secondary">{r.cited_by_count || "—"}</span>
            </Link>
          ))}
        </div>
      </main>
    </>
  );
}

function Toggle({ view, company }: { view: "documents" | "figures"; company: string }) {
  const docHref = company ? `/data?company=${company}` : "/data";
  const figHref = company ? `/data?view=figures&company=${company}` : "/data?view=figures";
  const active = "rounded-[6px] bg-accent px-3 py-1.5 text-[13px] font-semibold text-white";
  const inactive = "rounded-[6px] px-3 py-1.5 text-[13px] font-semibold text-tertiary hover:text-secondary";
  return (
    <div className="inline-flex rounded-md border border-border bg-surface p-0.5">
      {view === "documents" ? (
        <span className={active}>Documents</span>
      ) : (
        <Link href={docHref} className={inactive}>Documents</Link>
      )}
      {view === "figures" ? (
        <span className={active}>Figures</span>
      ) : (
        <Link href={figHref} className={inactive}>Figures</Link>
      )}
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  company: "Memo",
  stage1: "Stage-1",
  fda: "FDA",
  pmc: "PMC",
};

function FigureCard({ f }: { f: FigureRow }) {
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-border bg-surface">
      <div className="border-b border-sunken bg-canvas p-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={figureImageSrc(f.image_url)}
          alt={f.caption || f.figure_id}
          loading="lazy"
          className="mx-auto h-auto max-h-[220px] w-full object-contain"
        />
      </div>
      <div className="flex flex-1 flex-col gap-1.5 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-pill bg-sunken px-2 py-0.5 font-mono text-[11px] font-semibold text-secondary">
            {f.company}
          </span>
          <span className="rounded-pill border border-border px-2 py-0.5 text-[10px] font-semibold uppercase text-tertiary">
            {SOURCE_LABEL[f.source] ?? f.source}
          </span>
          {f.figure_type && (
            <span className="font-mono text-[11px] text-tertiary">{f.figure_type}</span>
          )}
          {f.cited && (
            <span className="rounded-pill bg-conf-high-soft px-2 py-0.5 text-[10px] font-semibold text-conf-high">
              In memo
            </span>
          )}
        </div>
        {f.caption && <p className="text-[12px] leading-5 text-secondary">{f.caption}</p>}
        {f.source_url && (
          <a
            href={f.source_url}
            target="_blank"
            rel="noreferrer"
            className="mt-auto inline-block max-w-full truncate pt-1 font-mono text-[11px] text-accent-text hover:underline"
          >
            Source ↗
          </a>
        )}
      </div>
    </div>
  );
}

function Select({
  name,
  value,
  placeholder,
  options,
}: {
  name: string;
  value: string;
  placeholder: string;
  options: string[];
}) {
  return (
    <select
      name={name}
      defaultValue={value}
      className="h-9 rounded-md border border-border bg-surface px-2.5 text-[13px] text-secondary outline-none focus:border-accent"
    >
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
}
