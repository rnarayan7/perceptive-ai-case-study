// Colored chip for a document type. Colors group the source families (SEC filings,
// clinical/scientific, market/epi) using the existing token palette; unknown types fall
// back to a neutral slate so a new source never renders broken.

const FILINGS = new Set(["10-K", "10-Q", "20-F", "6-K", "8-K", "8-K/A", "424B3", "424B5", "424B7", "xbrl_fact"]);
const SCIENCE = new Set(["study", "article", "preprint", "compound"]);
const MARKET = new Set(["asp_price", "epi", "prevalence"]);

function chipClass(docType: string): string {
  if (FILINGS.has(docType)) return "text-accent-text bg-accent-soft";
  if (SCIENCE.has(docType)) return "text-conf-high bg-conf-high-soft";
  if (MARKET.has(docType)) return "text-conf-med bg-conf-med-soft";
  return "text-conf-low bg-conf-low-soft";
}

export function TypeChip({ docType }: { docType: string }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 font-mono text-[11px] font-semibold ${chipClass(docType)}`}>
      {docType}
    </span>
  );
}
