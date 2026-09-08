"""Minimal vision-capable model client (stdlib only).

Wraps the Anthropic Messages API over raw HTTP so a fresh clone needs no SDK
install. Credentials come from the environment at run time (``ANTHROPIC_API_KEY``),
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
import socket
import time
import urllib.error
import urllib.request
from typing import List, Optional, Protocol

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Per-role model defaults. All current Claude models are vision-capable; these
# trade capability against cost per role. Pricing (input/output per 1M tokens):
# opus-5 $5/$25, sonnet-5 $2/$10, haiku-4.5 $1/$5.
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")
EXTRACTOR_MODEL = os.environ.get("CLAUDE_EXTRACTOR_MODEL", DEFAULT_MODEL)
ROUTER_MODEL = os.environ.get("CLAUDE_ROUTER_MODEL", "claude-haiku-4-5")
JUDGE_MODEL = os.environ.get("CLAUDE_JUDGE_MODEL", "claude-sonnet-5")


class VisionClient(Protocol):
    """A model that answers a text prompt, optionally about an image."""

    def complete(self, prompt: str, image: Optional[bytes] = None,
                 media_type: str = "image/png", max_tokens: int = 1024) -> str:
        ...


class AnthropicClient:
    """Calls the Anthropic Messages API. Raises if no API key is configured."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None,
                 timeout: float = 300.0, max_retries: int = 3) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        # Adaptive thinking on a dense figure read can run a couple of minutes,
        # so the read timeout is generous.
        self.timeout = timeout
        self.max_retries = max_retries

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, prompt: str, image: Optional[bytes] = None,
                 media_type: str = "image/png", max_tokens: int = 1024) -> str:
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Provide credentials at run time "
                "or inject a different VisionClient."
            )
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
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }).encode("utf-8")
        payload = self._post(body)
        parts = payload.get("content") or []
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")

    def _post(self, body: bytes) -> dict:
        """POST with retries on timeouts, rate limits, and 5xx.

        ``socket.timeout`` is caught explicitly: on Python 3.9 it is not a
        subclass of ``TimeoutError`` (that unification landed in 3.10), so a bare
        ``except TimeoutError`` would miss a read timeout and crash the run.
        """
        req = urllib.request.Request(
            ANTHROPIC_URL, data=body, method="POST",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                # 4xx (except 429) will not improve on retry; surface immediately.
                if exc.code != 429 and 400 <= exc.code < 500:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"Anthropic API {exc.code}: {detail}") from exc
                last_error = exc
            except (socket.timeout, TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(min(2.0 ** attempt, 20.0))
        raise RuntimeError(f"Anthropic API call failed after {self.max_retries} attempts: {last_error}")


def extract_json(text: str) -> Optional[object]:
    """Pull the first JSON object/array out of a model reply (tolerating fences)."""
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
