export function TopBar({
  title,
  badge,
  right,
}: {
  title: string;
  badge?: string;
  right?: React.ReactNode;
}) {
  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-surface px-7">
      <h1 className="text-lg font-semibold text-ink">{title}</h1>
      {badge && (
        <span className="rounded-pill bg-sunken px-2 py-0.5 text-xs font-medium text-secondary">{badge}</span>
      )}
      <div className="flex-1" />
      {right}
      <span className="flex items-center gap-1.5 rounded-md border border-border bg-canvas px-2.5 py-2 text-xs font-medium text-secondary">
        <span className="h-2 w-2 rounded-full bg-conf-high" />
        Synced 2h ago
      </span>
    </header>
  );
}
