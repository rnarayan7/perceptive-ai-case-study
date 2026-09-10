"""Feedback-intake chat service.

An analyst flags whether an analysis section is accurate by chatting briefly with a terse
intake agent. Each turn: one analyst message in, one short reply out, non-streaming. The
whole conversation is persisted to the ledger (feedback_sessions + feedback_messages) and
every turn emits an execution trace event, so feedback is queryable over time.

Reuses the memo package's Anthropic client (memo.analysis.model) and tracer (memo.trace),
so the API key and tracing come through the same path as memo generation. The agent also
emits a small structured summary ({issue, severity}) stored on the session row.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from memo.analysis.model import AnthropicModelClient
from memo.ledger import FeedbackMessageRecord, FeedbackSessionRecord, LedgerStore
from memo.ledger.schema import new_id
from memo.trace import JsonlTracer, RunSession, TraceEvent

from app.deps import DATA_ROOT

MODEL = "claude-sonnet-5"

# Terse feedback-intake behaviour. This is not a Q&A or defense agent: it captures whether
# a section is accurate, nothing more.
_SYSTEM = """You field analyst feedback on whether one section of a biotech investment
analysis is accurate. You are an intake agent, not a Q&A or defense agent.

Rules, in order of importance:
- Be terse. Never write more than 1-2 short sentences per turn.
- Acknowledge the analyst's point plainly. Do not argue, defend the analysis, or lecture.
- Ask at most one short clarifying question, and only if you cannot tell what the specific
  inaccuracy is. If the point is already clear, do not ask anything.
- Once you have captured the specific inaccuracy, set captured to true and confirm plainly
  in one sentence (e.g. "Got it, logged."). Do not keep the conversation going after that.
- Never ask more than one question total. Never repeat a question already answered.

Also maintain a running summary of the issue for tracking:
- issue: one short phrase naming the specific inaccuracy (empty until you know it).
- severity: low, med, or high, your best estimate of how material the inaccuracy is.
- section: the section label you were given."""

_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "The terse reply shown to the analyst."},
        "captured": {
            "type": "boolean",
            "description": "True once the specific inaccuracy has been captured.",
        },
        "summary": {
            "type": "object",
            "properties": {
                "issue": {"type": "string"},
                "severity": {"type": "string", "enum": ["low", "med", "high"]},
                "section": {"type": "string"},
            },
            "required": ["issue", "severity", "section"],
            "additionalProperties": False,
        },
    },
    "required": ["reply", "captured", "summary"],
    "additionalProperties": False,
}


def _ensure_anthropic_key() -> None:
    """Load ANTHROPIC_API_KEY from the repo .env files if not already in the environment.

    The Anthropic SDK reads the key from the environment at client construction; this
    mirrors how marketdata.load_api_key resolves the Alpha Vantage key from the same files.
    A real shell export always wins (setdefault).
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    from app.marketdata import _parse_env_file

    repo_root = Path(__file__).resolve().parents[2]
    for candidate in (repo_root / "investment-memo" / ".env", repo_root / ".env"):
        if candidate.is_file():
            val = _parse_env_file(candidate).get("ANTHROPIC_API_KEY")
            if val:
                os.environ.setdefault("ANTHROPIC_API_KEY", val)
                return


def _tracer() -> JsonlTracer:
    return JsonlTracer(root=str(Path(DATA_ROOT) / "traces"))


def _build_prompt(
    section_label: str,
    section_text: str,
    highlighted_quote: Optional[str],
    messages: List[FeedbackMessageRecord],
) -> str:
    """Serialize the section context and conversation so far into one user message."""
    lines = [
        f"Section under review: {section_label}",
        "",
        "Section text:",
        section_text.strip() or "(none provided)",
    ]
    if highlighted_quote:
        lines += ["", f"The analyst highlighted this specific passage: \"{highlighted_quote.strip()}\""]
    lines += ["", "Conversation so far:"]
    for m in messages:
        who = "Analyst" if m.role == "analyst" else "You"
        lines.append(f"{who}: {m.text.strip()}")
    lines += ["", "Write your next reply."]
    return "\n".join(lines)


def chat(
    store: LedgerStore,
    company: str,
    section_id: str,
    section_label: str,
    section_text: str,
    user_text: str,
    highlighted_quote: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Handle one feedback turn: persist the analyst message, get a terse reply, persist it.

    Returns {session_id, reply, captured, summary}. Never raises to the caller: a model or
    persistence failure degrades to a graceful reply so the client never sees a 500.
    """
    _ensure_anthropic_key()
    now = _now()

    # Load or create the session row.
    session: Optional[FeedbackSessionRecord] = None
    if session_id:
        session = store.get_feedback_session(session_id)
    if session is None:
        session = FeedbackSessionRecord(
            company=company,
            section_id=section_id,
            section_label=section_label,
            highlighted_quote=highlighted_quote,
            session_id=session_id or new_id("fb"),
        )
        store.write_feedback_session(session)

    tracer = _tracer()

    # Persist the analyst turn before the model call, so the transcript survives a failure.
    analyst_msg = FeedbackMessageRecord(role="analyst", text=user_text, ts=now)
    try:
        store.append_feedback_message(session.session_id, analyst_msg)
    except Exception:  # noqa: BLE001 - persistence must not break the turn
        pass
    session.messages = list(session.messages) + [analyst_msg]

    reply, captured, summary = _run_model(
        tracer, session, section_text, section_label, highlighted_quote
    )

    # Persist the agent turn + the refreshed summary.
    agent_msg = FeedbackMessageRecord(role="agent", text=reply, ts=_now())
    session.updated_at = _now()
    session.issue = (summary or {}).get("issue") or session.issue
    session.severity = (summary or {}).get("severity") or session.severity
    if captured:
        session.status = "captured"
    try:
        store.append_feedback_message(session.session_id, agent_msg)
        store.write_feedback_session(session)
    except Exception:  # noqa: BLE001
        pass

    _emit(tracer, session, captured)

    return {
        "session_id": session.session_id,
        "reply": reply,
        "captured": captured,
        "summary": {
            "issue": session.issue or "",
            "severity": session.severity or "",
            "section": section_label,
        },
    }


def _run_model(
    tracer: JsonlTracer,
    session: FeedbackSessionRecord,
    section_text: str,
    section_label: str,
    highlighted_quote: Optional[str],
):
    """Call the model for one reply. On any failure, return a graceful fallback."""
    client = AnthropicModelClient(
        model=MODEL,
        effort="low",  # terse intake needs no deep reasoning
        tracer=tracer,
        run_id=session.session_id,
        session=RunSession(run_id=session.session_id, model=MODEL, tracer=tracer),
    )
    prompt = _build_prompt(section_label, section_text, highlighted_quote, session.messages)
    try:
        resp = client.complete_json(_SYSTEM, prompt, _SCHEMA, max_tokens=1000)
        data = resp.data or {}
        reply = str(data.get("reply") or "").strip() or _FALLBACK_REPLY
        captured = bool(data.get("captured"))
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        return reply, captured, summary
    except Exception as exc:  # noqa: BLE001 - degrade, never surface a stack
        tracer.emit(TraceEvent(
            run_id=session.session_id, kind="error", name="feedback_chat",
            data={"section_id": session.section_id, "error": f"{type(exc).__name__}: {exc}"},
        ))
        return _FALLBACK_REPLY, False, {}


def _emit(tracer: JsonlTracer, session: FeedbackSessionRecord, captured: bool) -> None:
    """Emit a session-level trace event so feedback activity shows up in the trace."""
    try:
        tracer.emit(TraceEvent(
            run_id=session.session_id, kind="step", name="feedback_turn",
            data={
                "company": session.company,
                "section_id": session.section_id,
                "section_label": session.section_label,
                "status": session.status,
                "captured": captured,
                "issue": session.issue,
                "severity": session.severity,
                "turns": len(session.messages) + 1,
            },
        ))
    except Exception:  # noqa: BLE001
        pass


_FALLBACK_REPLY = "Couldn't capture that just now. Try sending it again."


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
