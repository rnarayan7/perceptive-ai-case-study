import type { Thesis, Tier } from "@/lib/types";

const TIER_DOT: Record<Tier, string> = {
  high: "bg-conf-high",
  med: "bg-conf-med",
  low: "bg-conf-low",
};

const TIER_CHIP: Record<Tier, string> = {
  high: "text-conf-high bg-conf-high-soft",
  med: "text-conf-med bg-conf-med-soft",
  low: "text-conf-low bg-conf-low-soft",
};

const TIER_LABEL: Record<Tier, string> = { high: "High", med: "Med", low: "Low" };

export function ConfidenceChip({ tier }: { tier: Tier }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold ${TIER_CHIP[tier]}`}>
      {TIER_LABEL[tier]}
    </span>
  );
}

const QUESTIONS: { key: "efficacy" | "approval" | "regulatory" | "market"; label: string }[] = [
  { key: "efficacy", label: "Efficacy" },
  { key: "approval", label: "Approval" },
  { key: "regulatory", label: "Regulatory" },
  { key: "market", label: "Market" },
];

export function ConvictionDots({ conviction }: { conviction: Partial<Record<string, Tier>> }) {
  return (
    <div className="flex items-start gap-3.5">
      {QUESTIONS.map((q) => {
        const tier = conviction[q.key];
        return (
          <div key={q.key} className="flex flex-col items-center gap-1">
            <span className={`h-[9px] w-[9px] rounded-full ${tier ? TIER_DOT[tier] : "bg-border-strong"}`} />
            <span className="text-[10px] font-medium text-tertiary">{q.label}</span>
          </div>
        );
      })}
    </div>
  );
}

const THESIS_CHIP: Record<Exclude<Thesis, null>, string> = {
  long: "text-long bg-long-soft",
  short: "text-short bg-short-soft",
  neutral: "text-conf-low bg-conf-low-soft",
};
const THESIS_LABEL: Record<Exclude<Thesis, null>, string> = {
  long: "Long",
  short: "Short",
  neutral: "Neutral",
};

export function ThesisChip({ thesis }: { thesis: Thesis }) {
  if (!thesis) return <span className="text-tertiary">—</span>;
  return (
    <span className={`inline-flex items-center rounded-pill px-2 py-1 text-xs font-semibold ${THESIS_CHIP[thesis]}`}>
      {THESIS_LABEL[thesis]}
    </span>
  );
}

export function Pill({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-md bg-sunken px-2 py-0.5 text-[11px] font-semibold text-secondary">
      {children}
    </span>
  );
}
