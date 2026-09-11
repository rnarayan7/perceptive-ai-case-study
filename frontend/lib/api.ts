import type {
  AuditPayload,
  CompanyDetail,
  CompanyRow,
  DocumentDetail,
  DocumentRow,
  FigureRow,
  FeedbackChatRequest,
  FeedbackChatResponse,
  GenerationRun,
  IngestionSource,
  Memo,
} from "@/lib/types";

// Server components fetch the FastAPI directly. Set NEXT_PUBLIC_API_BASE in prod.
// Use 127.0.0.1 (not "localhost"): Node's fetch prefers IPv6 ::1, which uvicorn's
// default IPv4 bind refuses. Port 8001 (8000 is taken by the Stage-1 label server).
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8001";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  companies: () => get<CompanyRow[]>("/api/companies"),
  company: (t: string) => get<CompanyDetail>(`/api/companies/${t}`),
  companyMemo: (t: string) => get<Memo>(`/api/companies/${t}/memo`),
  evidence: (id: string) => get<AuditPayload>(`/api/evidence/${id}`),
  generations: () => get<GenerationRun[]>("/api/activity/generations"),
  ingestion: () => get<IngestionSource[]>("/api/activity/ingestion"),
  documents: (params?: {
    company?: string;
    doc_type?: string;
    q?: string;
    date_from?: string;
    date_to?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params?.company) qs.set("company", params.company);
    if (params?.doc_type) qs.set("doc_type", params.doc_type);
    if (params?.q) qs.set("q", params.q);
    if (params?.date_from) qs.set("date_from", params.date_from);
    if (params?.date_to) qs.set("date_to", params.date_to);
    const query = qs.toString();
    return get<DocumentRow[]>(`/api/documents${query ? `?${query}` : ""}`);
  },
  document: (id: string) => get<DocumentDetail>(`/api/documents/${encodeURIComponent(id)}`),
  figures: (params?: { company?: string; origin?: string }) => {
    const qs = new URLSearchParams();
    if (params?.company) qs.set("company", params.company);
    if (params?.origin) qs.set("origin", params.origin);
    const query = qs.toString();
    return get<FigureRow[]>(`/api/figures${query ? `?${query}` : ""}`);
  },
  feedbackChat: (body: FeedbackChatRequest) =>
    post<FeedbackChatResponse>("/api/feedback/chat", body),
};

// A figure row's image_url is already an absolute API path (e.g. /api/figures/...); prefix
// it with the API base so the browser can fetch it.
export function figureImageSrc(imageUrl: string): string {
  return `${BASE}${imageUrl.startsWith("/") ? "" : "/"}${imageUrl}`;
}

// Resolve a memo figure's image_ref to a browser-reachable URL on the figures route.
// image_ref looks like "data/<COMPANY>/figures/annotated/<name>.png"; we pull out the
// company and the path under figures/ and hand both to GET /api/figures/{company}/{ref}.
export function figureSrc(imageRef: string): string {
  const [before, after] = imageRef.split("/figures/");
  if (after === undefined) return `${BASE}${imageRef.startsWith("/") ? "" : "/"}${imageRef}`;
  const company = before.split("/").filter(Boolean).pop() ?? "";
  const ref = after
    .split("/")
    .map(encodeURIComponent)
    .join("/");
  return `${BASE}/api/figures/${encodeURIComponent(company)}/${ref}`;
}

// Formatters shared across screens.
export function usd(n: number | null | undefined): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${n.toFixed(0)}`;
}
