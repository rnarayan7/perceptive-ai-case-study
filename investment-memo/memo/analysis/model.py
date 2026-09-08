"""Model client: a thin, provider-agnostic wrapper for structured reasoning.

Modules call ``context.model.complete_json(...)`` and never import a vendor SDK
directly, so the provider is swappable in one place. The default and only implementation
today is Claude (Anthropic); an OpenAI-backed client would implement the same
:class:`ModelClient` interface. Real models only, no mocks (see MEMORY).
"""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from memo.trace import NULL_SESSION, NULL_TRACER, NullTracer, RunSession, Tracer

DEFAULT_MODEL = "claude-opus-5"


@dataclass
class ModelResponse:
    """A parsed model response plus token usage for cost reporting."""

    data: Dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def usage(self) -> Dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


class ModelClient(abc.ABC):
    """Interface for a reasoning backend that returns schema-validated JSON."""

    @abc.abstractmethod
    def complete_json(
        self,
        system: str,
        user: str,
        schema: Dict[str, Any],
        max_tokens: int = 16000,
    ) -> ModelResponse:
        """Return JSON matching ``schema``, produced from ``system`` + ``user``."""
        raise NotImplementedError


class AnthropicModelClient(ModelClient):
    """Claude-backed model client using the official Anthropic SDK.

    Uses adaptive thinking and structured outputs (``output_config.format``) so the
    response is guaranteed to be schema-valid JSON. The SDK resolves credentials from
    the environment (``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        client: Optional[Any] = None,
        tracer: Tracer = NULL_TRACER,
        run_id: Optional[str] = None,
        session: RunSession = NULL_SESSION,
    ) -> None:
        self.model = model
        self.effort = effort
        self._client = client  # injectable; lazily constructed otherwise
        self.tracer = tracer
        self.run_id = run_id or ""
        self.session = session  # per-run token accumulator; no-op unless attached

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic  # imported lazily so the package loads without the SDK

            self._client = anthropic.Anthropic()
        return self._client

    def complete_json(
        self,
        system: str,
        user: str,
        schema: Dict[str, Any],
        max_tokens: int = 16000,
    ) -> ModelResponse:
        # Full capture (prompt, response, reasoning) is opt-in: only when a real tracer is
        # attached. That keeps untraced calls (judges, tests) and their output unchanged,
        # and only requests thinking summaries where we intend to record them.
        tracing = not isinstance(self.tracer, NullTracer)
        thinking: Dict[str, Any] = {"type": "adaptive"}
        if tracing:
            thinking["display"] = "summarized"

        with self.tracer.span(self.run_id, "model", "complete_json",
                              model=self.model, effort=self.effort) as event:
            if tracing:
                event["system"] = system
                event["prompt"] = user

            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                thinking=thinking,
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                system=system,
                messages=[{"role": "user", "content": user}],
            )

            event["stop_reason"] = getattr(response, "stop_reason", None)
            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                category = getattr(detail, "category", None)
                raise ModelRefusal(f"model refused (category={category})")

            text = next((b.text for b in response.content if b.type == "text"), "")
            if not text:
                raise ValueError("model returned no text block to parse")

            usage = response.usage
            self.session.record(usage)  # accumulate this call's tokens into the run
            result = ModelResponse(
                data=json.loads(text),
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            )
            event["input_tokens"] = result.input_tokens
            event["output_tokens"] = result.output_tokens
            if tracing:
                event["response"] = text
                event["thinking"] = "\n".join(
                    getattr(b, "thinking", "") for b in response.content
                    if b.type == "thinking"
                )
            return result


class ModelRefusal(RuntimeError):
    """Raised when the model declines to answer (safety refusal)."""


def default_model_client() -> ModelClient:
    """The default reasoning backend (Claude)."""
    return AnthropicModelClient()
