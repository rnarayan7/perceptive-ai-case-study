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


# --------------------------------------------------------------------------- usage

# USD per million tokens (input, output), per-model list price. Approximate on purpose:
# these are list rates, they ignore prompt caching and batch discounts, and Anthropic
# gives no per-request billed cost. Tokens (summed from resp.usage) are the exact figure;
# dollars here are a convenience. Authoritative spend is the Anthropic Console / Cost API.
_PRICING = {
    "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0), "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
}
_DEFAULT_PRICE = (5.0, 25.0)  # unknown models priced at opus rates

_TOKEN_FIELDS = (
    "input_tokens", "output_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
)


def _usage_field(usage: Any, name: str) -> int:
    """Read one token count from an Anthropic usage object or a plain dict."""
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
    return int(value or 0)


class RunSession:
    """Per-run accumulator of token usage across model calls.

    Tokens are the primary output: exact, free, summed straight from each response's
    ``usage``. ``cost`` is an approximate convenience derived from :data:`_PRICING`.
    The two cache fields are recorded but not priced (see the pricing note). One session
    spans a whole run so ``summary`` reports the run's total, not a single call.
    """

    def __init__(self, run_id: str = "", model: str = "", tracer: Tracer = NULL_TRACER) -> None:
        self.run_id = run_id
        self.model = model  # default model for pricing; the run's primary caller
        self.tracer = tracer
        self.calls = 0
        self._tokens: Dict[str, int] = {name: 0 for name in _TOKEN_FIELDS}

    def record(self, usage: Any) -> None:
        """Add one call's token counts (Anthropic usage object or dict) to the totals."""
        if usage is None:
            return
        self.calls += 1
        for name in _TOKEN_FIELDS:
            self._tokens[name] += _usage_field(usage, name)

    @property
    def tokens(self) -> Dict[str, int]:
        """Accumulated token totals. Cache fields appear only when nonzero."""
        base = {"input_tokens": self._tokens["input_tokens"],
                "output_tokens": self._tokens["output_tokens"]}
        for name in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            if self._tokens[name]:
                base[name] = self._tokens[name]
        return base

    def cost(self, model: Optional[str] = None) -> float:
        """Approximate USD at list price (input + output only). See _PRICING for caveats."""
        price_in, price_out = _PRICING.get(model or self.model, _DEFAULT_PRICE)
        return (self._tokens["input_tokens"] * price_in
                + self._tokens["output_tokens"] * price_out) / 1e6

    def summary(self, model: Optional[str] = None) -> str:
        """One-line run total: calls, in/out tokens, and the approximate dollar figure."""
        return (
            f"usage: {self.calls} calls, {self._tokens['input_tokens']:,} in / "
            f"{self._tokens['output_tokens']:,} out tokens, ~${self.cost(model):.2f} "
            f"(approx list price, ignores caching; authoritative spend is the Cost API)"
        )


class NullRunSession(RunSession):
    """No-op session so calls made outside a tracked run record nothing."""

    def record(self, usage: Any) -> None:  # noqa: D102
        return None


#: Shared no-op instance model clients default to when no run session is attached.
NULL_SESSION = NullRunSession()
