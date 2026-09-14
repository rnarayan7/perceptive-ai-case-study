"""Vision-capable model client, on the official Anthropic SDK.

Wraps the Anthropic Messages API through the ``anthropic`` SDK. The SDK supplies
the machinery this module used to hand-roll over raw HTTP (retries with backoff,
timeouts, error typing) and, more importantly, **forced tool use**: a schema-bound
call whose reply is guaranteed to be schema-conforming JSON. That is what
:meth:`AnthropicClient.complete_json` uses, so structured reads (the figure layout
parse) never depend on the model formatting its braces correctly.

Credentials come from the environment at run time (``ANTHROPIC_API_KEY``),
matching the case-study execution model. The client is injected into the VLM
extractor and the judge matcher, so both are testable with a fake client offline.

Models are chosen per role, since the three roles have different needs:

* Extraction (reading the charts) is the capability-sensitive part, so it uses
  the most capable general model. Run it at high/xhigh effort.
* Routing (classify figure type) is a one-word image classification, so it uses
  the cheapest model.
* Judge matching (align mismatched labels) is a text task secondary to the
  numbers, so it uses the middle tier.

Each is overridable from the environment (``CLAUDE_MODEL`` sets the default for
the extractor; per-role vars override individually), matching the runtime-supplied
-credentials execution model in the brief.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List, Optional, Protocol

try:
    import anthropic
except ImportError as exc:  # pragma: no cover - environment problem, not logic
    raise ImportError(
        "The 'anthropic' SDK is required. Install it with: pip install anthropic"
    ) from exc

# Per-role model defaults. All current Claude models are vision-capable; these
# trade capability against cost per role. Pricing (input/output per 1M tokens):
# sonnet-5 $2/$10, haiku-4.5 $1/$5. Extraction defaults to sonnet-5: it is the
# capability tier the case study standardizes on, and it reads the charts well at
# a fraction of the top-tier cost. Override per role via the env vars below.
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
EXTRACTOR_MODEL = os.environ.get("CLAUDE_EXTRACTOR_MODEL", DEFAULT_MODEL)
ROUTER_MODEL = os.environ.get("CLAUDE_ROUTER_MODEL", "claude-haiku-4-5")
JUDGE_MODEL = os.environ.get("CLAUDE_JUDGE_MODEL", "claude-sonnet-5")

# USD per 1M tokens (input, output), for cost-of-one-run reporting.
PRICING = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


class VisionClient(Protocol):
    """A model that answers a text prompt, optionally about an image."""

    def complete(self, prompt: str, image: Optional[bytes] = None,
                 media_type: str = "image/png", max_tokens: int = 1024) -> str:
        ...


class AnthropicClient:
    """Calls the Anthropic Messages API via the SDK. Raises if no key is set."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None,
                 timeout: float = 300.0, max_retries: int = 3) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        # Adaptive thinking on a dense figure read can run a couple of minutes,
        # so the read timeout is generous. The SDK retries with backoff on
        # timeouts, 429s and 5xx, and surfaces 4xx immediately.
        self.timeout = timeout
        self.max_retries = max_retries
        self.input_tokens = 0   # accumulated across calls, for cost reporting
        self.output_tokens = 0
        self._client: Optional["anthropic.Anthropic"] = None

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    @property
    def client(self) -> "anthropic.Anthropic":
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Provide credentials at run time "
                    "or inject a different VisionClient."
                )
            self._client = anthropic.Anthropic(
                api_key=self.api_key, timeout=self.timeout,
                max_retries=self.max_retries,
            )
        return self._client

    # -- requests ----------------------------------------------------------

    def _content(self, prompt: str, image: Optional[bytes],
                 media_type: str) -> List[dict]:
        content: List[dict] = []
        if image is not None:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(image).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": prompt})
        return content

    def _record(self, message: Any) -> None:
        usage = getattr(message, "usage", None)
        if usage is not None:
            self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

    def complete(self, prompt: str, image: Optional[bytes] = None,
                 media_type: str = "image/png", max_tokens: int = 1024) -> str:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user",
                       "content": self._content(prompt, image, media_type)}],
        )
        self._record(message)
        return "".join(block.text for block in message.content
                       if getattr(block, "type", None) == "text")

    def complete_json(self, prompt: str, schema: Dict[str, Any],
                      image: Optional[bytes] = None,
                      media_type: str = "image/png",
                      max_tokens: int = 1024,
                      tool_name: str = "emit_result") -> Optional[dict]:
        """Schema-bound structured read: the reply is guaranteed valid JSON.

        Uses forced tool use, so the model must answer by calling a tool whose
        ``input_schema`` is ``schema``. The SDK returns that input already parsed,
        which removes the whole class of malformed-JSON failures that prompting
        for "strict JSON" cannot rule out. Returns ``None`` only if the model
        somehow emitted no tool call.
        """
        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            # Note: `temperature` is deprecated on current models, so run-to-run
            # variation in a structured read is handled downstream instead, by
            # the layout sanity filter and the calibration self-check.
            messages=[{"role": "user",
                       "content": self._content(prompt, image, media_type)}],
            tools=[{
                "name": tool_name,
                "description": "Return the extracted result.",
                "input_schema": schema,
            }],
            tool_choice={"type": "tool", "name": tool_name},
        )
        self._record(message)
        for block in message.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        return None

    def cost(self) -> dict:
        """Accumulated token usage and USD cost for this client's model."""
        cin, cout = PRICING.get(self.model, (0.0, 0.0))
        usd = self.input_tokens / 1e6 * cin + self.output_tokens / 1e6 * cout
        return {"model": self.model, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "usd": round(usd, 4)}


def total_cost(*clients) -> dict:
    """Sum token cost across clients (e.g. extractor + router), for one run."""
    by_model = [c.cost() for c in clients if hasattr(c, "cost")]
    return {
        "total_usd": round(sum(b["usd"] for b in by_model), 4),
        "input_tokens": sum(b["input_tokens"] for b in by_model),
        "output_tokens": sum(b["output_tokens"] for b in by_model),
        "by_model": by_model,
    }


def extract_json(text: str) -> Optional[object]:
    """Pull the first JSON object/array out of a model reply (tolerating fences).

    Still used for free-text replies; structured reads should prefer
    :meth:`AnthropicClient.complete_json`, which cannot return malformed JSON.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    for opener, closer in (("[", "]"), ("{", "}")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


def media_type_for(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
