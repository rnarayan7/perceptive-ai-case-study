"use client";

import Link from "next/link";
import type { Citation } from "@/lib/types";

export function CitationLink({ citation, ticker }: { citation: Citation; ticker: string }) {
  return (
    <span className="group relative inline-block align-baseline">
      <Link
        href={`/company/${ticker}?audit=${citation.evidence_id}#audit-trail`}
        className="mx-0.5 rounded bg-accent-soft2 px-1 text-[11px] font-semibold text-accent-text align-baseline"
      >
        {citation.marker}
      </Link>
      <span
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 hidden w-[260px] -translate-x-1/2 rounded-md border border-border bg-surface p-2.5 text-left shadow-lg group-hover:block"
        role="tooltip"
      >
        <span className="block text-[12px] font-semibold text-ink">{citation.label}</span>
        <span className="mt-1 block truncate font-mono text-[11px] text-tertiary">{citation.url}</span>
        <span className="mt-1.5 block text-[11px] text-secondary">Click to view source</span>
      </span>
    </span>
  );
}
