export function Notice({ title, body }: { title: string; body?: string }) {
  return (
    <div className="m-7 rounded-lg border border-border bg-surface p-8 text-center">
      <div className="text-sm font-semibold text-ink">{title}</div>
      {body && <div className="mt-1 text-sm text-secondary">{body}</div>}
    </div>
  );
}

export function ApiError() {
  return (
    <Notice
      title="API unavailable"
      body="Start the backend: cd backend && uvicorn app.main:app --reload --port 8000"
    />
  );
}
