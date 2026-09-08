"""Execution tracing / observability.

Distinct from the ledger: the ledger records the *output's* provenance (claim ->
evidence), while a trace records the *execution* (which model/retrieval ran, tokens,
latency, cost, errors). They are correlated by ``run_id`` (which is also the memo id),
so a persisted claim can be tied back to the exact model call and retrieved set that
produced it.

This is deliberately dependency-free: the default is a JSONL sink (one file per run),
and a NullTracer no-ops when tracing is off. The two instrumentation choke points are
``ModelClient.complete_json`` and ``Retriever.retrieve``; both accept a Tracer and emit
an event. A hook is left so an OpenTelemetry / Langfuse exporter could implement Tracer
later without touching callers.
"""

from __future__ import annotations

import abc
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from contextlib import contextmanager


def new_run_id(company: str) -> str:
    """A sortable, unique run/memo id, e.g. 'KYMR-20260907T1230-ab12cd'."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{company}-{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass
class TraceEvent:
    """One execution event. ``data`` holds kind-specific fields."""

    run_id: str
    kind: str  # "model" | "retrieval" | "step" | "error"
    name: str
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: Optional[float] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Tracer(abc.ABC):
    """Sink for execution events. Implementations must be cheap and never raise."""

    @abc.abstractmethod
    def emit(self, event: TraceEvent) -> None:
        raise NotImplementedError

    @contextmanager
    def span(self, run_id: str, kind: str, name: str, **data: Any) -> Iterator[Dict[str, Any]]:
        """Time a block and emit one event on exit.

        Yields a mutable dict the caller can update with result fields (e.g. token
        counts) before the event is emitted. Errors are recorded then re-raised.
        """
        payload: Dict[str, Any] = dict(data)
        start = time.monotonic()
        try:
            yield payload
        except Exception as exc:  # noqa: BLE001 - record then re-raise
            self.emit(TraceEvent(
                run_id=run_id, kind="error", name=name,
                duration_ms=(time.monotonic() - start) * 1000.0,
                data={**payload, "error": f"{type(exc).__name__}: {exc}"},
            ))
            raise
        else:
            self.emit(TraceEvent(
                run_id=run_id, kind=kind, name=name,
                duration_ms=(time.monotonic() - start) * 1000.0, data=payload,
            ))


class NullTracer(Tracer):
    """Default no-op tracer, so tracing is free to leave off."""

    def emit(self, event: TraceEvent) -> None:  # noqa: D102
        return None


class JsonlTracer(Tracer):
    """Append events as JSON lines, one file per run: ``<root>/<run_id>.jsonl``."""

    def __init__(self, root: str = "data/traces") -> None:
        self.root = Path(root)

    def emit(self, event: TraceEvent) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{event.run_id or 'unknown'}.jsonl"
            with path.open("a") as handle:
                handle.write(json.dumps(event.to_dict()) + "\n")
        except OSError:
            # Observability must never break the run.
            return None


#: Shared no-op instance callers can default to.
NULL_TRACER = NullTracer()
