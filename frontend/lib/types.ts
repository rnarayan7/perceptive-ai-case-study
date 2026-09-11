export type Tier = "high" | "med" | "low";
export type Thesis = "long" | "short" | "neutral" | null;

export interface CompanyRow {
  ticker: string;
  name: string;
  lead_asset: string;
  indication: string;
  phase: string;
  status: string;
  memo_id: string | null;
  thesis: Thesis;
  conviction: Partial<Record<"efficacy" | "approval" | "regulatory" | "market", Tier>>;
  fair_value_usd: number | null;
  peak_sales_usd: number | null;
  market_price: number | null;
  market_cap: number | null;
  upside_pct: number | null;
  recommendation: string | null;
  updated_at: string | null;
}

export interface Metric {
  confidence: Tier;
  takeaway: string;
  points: string[];
  citations: { evidence_id: string; label: string; url: string }[];
}

export interface CompanyDetail {
  ticker: string;
  name: string;
  lead_asset: string;
  indication: string;
  phase: string;
  status: string;
  memo_id: string | null;
  thesis: Thesis;
  recommendation: string | null;
  // The differentiated view: where our read departs from what the price implies.
  variant_view: string | null;
  metrics: Partial<Record<"efficacy" | "approval" | "regulatory" | "market", Metric>>;
  kpis: {
    fair_value_usd: number | null;
    fair_value_per_sh: number | null;
    peak_sales_usd: number | null;
    market_price: number | null;
    market_cap: number | null;
    shares: number | null;
    upside_pct: number | null;
    cash_usd: number | null;
    runway: string | null;
  };
}

export interface Citation {
  marker: string;
  evidence_id: string;
  url: string;
  label: string;
}

export interface MemoSection {
  section_id: string;
  title: string;
  takeaway: string;
  prose: string;
  citations: Citation[];
  figures: { figure_id: string; caption: string; image_ref: string; claim_id: string | null }[];
}

export interface Memo {
  memo_id: string;
  company: string;
  title: string;
  recommendation: string | null;
  thesis: string | null;
  variant_view: string | null;
  status: string;
  sections: MemoSection[];
}

export interface AuditPayload {
  evidence: {
    evidence_id: string;
    doc_id: string;
    source: string;
    doc_type: string;
    url: string;
    quote: string;
    date: string | null;
  };
  claim: {
    claim_id: string;
    section: string;
    module: string;
    statement: string;
    confidence: number;
    value: string | null;
    rationale: string;
  } | null;
}

export interface GenerationRun {
  run_id: string;
  company: string;
  status: string;
  trigger: string;
  output: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  duration_s: number;
  ran_at: string;
}

export interface IngestionSource {
  source: string;
  label: string;
  doc_count: number;
  company_count: number;
  last_ingested: string | null;
  latest_item: string | null;
}

export type FigureOrigin = "company" | "stage1" | "corpus";

export interface FigureRow {
  origin: FigureOrigin;
  company: string;
  figure_id: string;
  caption: string;
  figure_type: string;
  source: string;
  source_url: string | null;
  image_url: string;
  image_ref?: string | null;
  cited: boolean;
}

export interface DocumentRow {
  doc_id: string;
  title: string;
  source: string;
  doc_type: string;
  company: string;
  published: string | null;
  figure_count: number;
  cited_by_count: number;
}

export type FeedbackRole = "analyst" | "agent";
export type Severity = "low" | "med" | "high";

export interface FeedbackMessage {
  role: FeedbackRole;
  text: string;
}

export interface FeedbackSummary {
  issue: string;
  severity: Severity | "";
  section: string;
}

export interface FeedbackChatRequest {
  company: string;
  section_id: string;
  section_label: string;
  section_text: string;
  highlighted_quote?: string | null;
  session_id?: string | null;
  messages: FeedbackMessage[];
}

export interface FeedbackChatResponse {
  session_id: string;
  reply: string;
  captured: boolean;
  summary: FeedbackSummary | null;
}

export interface DocumentDetail {
  document: {
    doc_id: string;
    company: string;
    source: string;
    doc_type: string;
    title: string;
    url: string;
    published: string | null;
    retrieved_at: string;
    metadata: Record<string, unknown>;
    text: string;
    figure_count: number;
  };
  cited_by: { memo_id: string; company: string; section: string }[];
}
