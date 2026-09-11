import Link from "next/link";
import { api } from "@/lib/api";
import { TopBar } from "@/components/TopBar";
import { ApiError } from "@/components/Notice";
import { TypeChip } from "@/components/TypeChip";
import { Pill } from "@/components/chips";
import type { DocumentDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

const SECTION_LABEL: Record<string, string> = {
  thesis: "Thesis",
  overview: "Overview",
  moa: "Mechanism",
  pos: "Probability of success",
  regulatory: "Regulatory",
  peak_sales: "Peak sales",
  valuation: "Valuation",
  price: "Valuation",
  risks: "Risks",
  catalysts: "Catalysts",
  sources: "Sources",
};

export default async function DocumentPage({ params }: { params: { docId: string } }) {
  const docId = decodeURIComponent(params.docId);
  let detail: DocumentDetail;
  try {
    detail = await api.document(docId);
  } catch {
    return (
      <>
        <TopBar title="Document" />
        <ApiError />
      </>
    );
  }

  const { document: doc, cited_by } = detail;

  return (
    <>
      <TopBar title="Document" />
      <main className="space-y-4 p-7">
        {/* Breadcrumb */}
        <nav className="flex items-center gap-1.5 text-[13px] text-tertiary">
          <Link href="/data" className="hover:text-secondary">Data</Link>
          <span>/</span>
          <Link href="/data" className="hover:text-secondary">Documents</Link>
          <span>/</span>
          <span className="max-w-[520px] truncate text-secondary">{doc.title}</span>
        </nav>

        {/* Header card */}
        <section className="rounded-lg border border-border bg-surface p-5">
          <div className="flex items-center gap-2">
            <TypeChip docType={doc.doc_type} />
            <Pill>{doc.company}</Pill>
          </div>
          <h2 className="mt-2 text-xl font-semibold leading-tight">{doc.title}</h2>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-secondary">
            <span className="font-mono">{doc.source}</span>
            <span className="text-tertiary">·</span>
            <span className="font-mono">{doc.published ?? "undated"}</span>
            <span className="text-tertiary">·</span>
            <span className="font-mono text-tertiary">{doc.doc_id}</span>
            {doc.url && (
              <>
                <span className="text-tertiary">·</span>
                <a href={doc.url} target="_blank" rel="noreferrer" className="font-medium text-accent-text hover:underline">
                  Open source ↗
                </a>
              </>
            )}
          </div>
        </section>

        <section className="flex items-start gap-4">
          {/* Document text */}
          <div className="min-w-0 flex-1 rounded-lg border border-border bg-surface p-6">
            <h3 className="text-sm font-semibold">Document</h3>
            {doc.text?.trim() ? (
              <pre className="mt-3 max-h-[620px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-canvas p-4 font-sans text-[13px] leading-6 text-[#26303C]">
                {doc.text}
              </pre>
            ) : (
              <p className="mt-3 text-sm text-secondary">No extracted text for this document.</p>
            )}
          </div>

          {/* Cited by */}
          <aside className="w-[340px] shrink-0 rounded-lg border border-border bg-surface p-5">
            <h3 className="text-sm font-semibold">Cited by</h3>
            <p className="mt-0.5 text-xs text-tertiary">
              {cited_by.length ? `${cited_by.length} memo section${cited_by.length === 1 ? "" : "s"} rest on this document.` : "Not yet cited in any memo."}
            </p>
            <div className="mt-4 space-y-2">
              {cited_by.map((c, i) => (
                <Link
                  key={`${c.memo_id}-${c.section}-${i}`}
                  href={`/company/${c.company}`}
                  className="flex items-center justify-between rounded-md border border-border bg-canvas px-3 py-2 hover:border-border-strong"
                >
                  <span className="font-mono text-[13px] font-medium">{c.company}</span>
                  <span className="text-[12px] text-secondary">{SECTION_LABEL[c.section] ?? c.section}</span>
                </Link>
              ))}
            </div>
          </aside>
        </section>
      </main>
    </>
  );
}
