"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TICKERS = ["ABVX", "KYMR", "PRAX", "IMVT", "COGT"];

function Section({ label }: { label: string }) {
  return <div className="px-2.5 pt-4 pb-1 text-[10px] font-semibold tracking-wide text-secondary">{label}</div>;
}

export function Sidebar() {
  const path = usePathname();
  const onCompanies = path === "/" || path.startsWith("/company");
  return (
    <aside className="flex w-[220px] shrink-0 flex-col bg-sidebar px-4 py-5 text-sidebar-text">
      <div className="flex items-center gap-2.5">
        <div className="flex h-[26px] w-[26px] items-center justify-center rounded-md bg-accent">
          <span className="block h-2.5 w-2.5 rotate-45 rounded-[2px] bg-white" />
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-semibold text-white">Perceptive</div>
          <div className="text-[11px] font-medium text-tertiary">Research OS</div>
        </div>
      </div>

      <Section label="WORKSPACE" />
      <NavItem href="/" label="Companies" active={onCompanies} />
      {onCompanies && (
        <div className="mt-0.5 flex flex-col">
          <SubItem href="/" label="Coverage" active={path === "/"} />
          {TICKERS.map((t) => (
            <SubItem key={t} href={`/company/${t}`} label={t} mono active={path === `/company/${t}`} />
          ))}
        </div>
      )}
      <NavItem href="/data" label="Data" active={path.startsWith("/data")} />
      <NavItem href="/activity" label="Activity" active={path.startsWith("/activity")} />

      <div className="mt-auto flex items-center gap-2.5 pt-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-white">
          RN
        </div>
        <div className="leading-tight">
          <div className="text-[13px] font-medium text-white">Roshan Narayan</div>
          <div className="text-[11px] text-tertiary">Analyst</div>
        </div>
      </div>
    </aside>
  );
}

function NavItem({ href, label, active }: { href: string; label: string; active: boolean }) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-semibold ${
        active ? "bg-sidebar-hover text-white" : "text-sidebar-text hover:bg-sidebar-hover/60"
      }`}
    >
      {label}
    </Link>
  );
}

function SubItem({ href, label, active, mono }: { href: string; label: string; active: boolean; mono?: boolean }) {
  return (
    <Link
      href={href}
      className={`rounded-md py-1.5 pl-10 pr-2.5 text-[13px] ${mono ? "font-mono" : ""} ${
        active ? "bg-sidebar-hover font-semibold text-white" : "text-sidebar-text/80 hover:text-white"
      }`}
    >
      {label}
    </Link>
  );
}
