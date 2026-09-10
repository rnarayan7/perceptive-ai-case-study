"""Market-data adapter (spec §7.4).

A small `MarketDataProvider` interface with an `AlphaVantageProvider` backed by
Alpha Vantage's free tier, and a `StubProvider` that always returns None. The
provider fetches latest price (GLOBAL_QUOTE) and market cap + shares (OVERVIEW)
over HTTPS with the stdlib only.

The free tier allows ~25 requests/day, so we cache aggressively: a JSON snapshot
file keyed by ticker persists on disk, and a ticker is only re-fetched once its
cached snapshot is older than ~6 hours. Rate-limit / empty responses (Alpha
Vantage signals these with "Note" / "Information" keys) fall back to whatever is
cached, or None. The app must always serve, so nothing here raises.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_API_BASE = "https://www.alphavantage.co/query"
_TIMEOUT_S = 12
_CACHE_TTL_S = 6 * 60 * 60  # re-fetch a ticker whose snapshot is older than ~6h
# Alpha Vantage throttles rapid bursts (returns an "Information" note); the two
# endpoint calls for one ticker are spaced apart and OVERVIEW retried once. Only
# happens on a real refresh, so the cost is bounded by the 6h cache.
_INTERCALL_DELAY_S = 1.2
_RETRY_DELAY_S = 12.0
_KEY_ENV = "ALPHAVANTAGE_API_KEY"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_float(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # Alpha Vantage uses "None"/"-" strings for missing OVERVIEW fields.
    if f != f:  # NaN
        return None
    return f


def _to_int(value: Any) -> Optional[int]:
    f = _to_float(value)
    return int(f) if f is not None else None


def _parse_env_file(path: Path) -> Dict[str, str]:
    """Tiny KEY=VALUE parser (no dependency on python-dotenv)."""
    out: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


def load_api_key() -> Optional[str]:
    """Resolve the Alpha Vantage key: environment first, then repo .env files."""
    key = os.environ.get(_KEY_ENV)
    if key:
        return key.strip()
    # backend/app/marketdata.py -> repo root is parents[2]
    repo_root = Path(__file__).resolve().parents[2]
    for candidate in (repo_root / "investment-memo" / ".env", repo_root / ".env"):
        if candidate.is_file():
            val = _parse_env_file(candidate).get(_KEY_ENV)
            if val:
                return val
    return None


class MarketDataProvider:
    """Interface.

    `get(ticker)` reads the last cached snapshot (no network) — the app calls this on
    every request. `refresh(tickers)` does the actual fetching and is called only by the
    daily job, so user traffic never touches Alpha Vantage and the 25/day budget holds.
    """

    def get(self, ticker: str) -> Optional[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def refresh(self, tickers) -> Dict[str, Any]:
        return {"refreshed": [], "kept": [], "missing": [t.upper() for t in tickers]}


class StubProvider(MarketDataProvider):
    """Stands in when no key is configured; every field is None."""

    def get(self, ticker: str) -> Optional[Dict[str, Any]]:
        return {
            "market_price": None,
            "market_cap": None,
            "shares": None,
            "as_of": _now_iso(),
        }


class AlphaVantageProvider(MarketDataProvider):
    def __init__(
        self,
        api_key: str,
        cache_path: Path,
        ttl_s: int = _CACHE_TTL_S,
        timeout_s: int = _TIMEOUT_S,
        intercall_delay_s: float = _INTERCALL_DELAY_S,
        retry_delay_s: float = _RETRY_DELAY_S,
    ) -> None:
        self._key = api_key
        self._cache_path = Path(cache_path)
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._intercall_delay_s = intercall_delay_s
        self._retry_delay_s = retry_delay_s

    # -- cache ------------------------------------------------------------
    def _load_cache(self) -> Dict[str, Any]:
        try:
            with self._cache_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_cache(self, cache: Dict[str, Any]) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(cache, fh, indent=2, sort_keys=True)
            tmp.replace(self._cache_path)
        except OSError:
            pass  # never let a cache-write failure break serving

    @staticmethod
    def _is_fresh(entry: Dict[str, Any], ttl_s: int) -> bool:
        dt = _parse_iso(entry.get("as_of", ""))
        if dt is None:
            return False
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return 0 <= age < ttl_s

    # -- http -------------------------------------------------------------
    def _call(self, function: str, symbol: str) -> Optional[Dict[str, Any]]:
        params = urllib.parse.urlencode(
            {"function": function, "symbol": symbol, "apikey": self._key}
        )
        url = f"{_API_BASE}?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "perceptive-research-os/1.0"})
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            # network error, timeout, bad JSON: treat as unavailable
            return None
        if not isinstance(payload, dict):
            return None
        # Rate-limit / informational responses carry these keys instead of data.
        if any(k in payload for k in ("Note", "Information", "Error Message")):
            return None
        return payload

    def _fetch(self, ticker: str) -> Optional[Dict[str, Any]]:
        symbol = ticker.strip().upper()
        price: Optional[float] = None
        market_cap: Optional[float] = None
        shares: Optional[int] = None

        quote = self._call("GLOBAL_QUOTE", symbol)
        if quote:
            gq = quote.get("Global Quote") or quote.get("Global Quote ") or {}
            if isinstance(gq, dict):
                price = _to_float(gq.get("05. price"))

        # Space the second call out; retry once on a throttle note.
        if self._intercall_delay_s:
            time.sleep(self._intercall_delay_s)
        overview = self._call("OVERVIEW", symbol)
        if overview is None and self._retry_delay_s:
            time.sleep(self._retry_delay_s)
            overview = self._call("OVERVIEW", symbol)
        if overview:
            market_cap = _to_float(overview.get("MarketCapitalization"))
            shares = _to_int(overview.get("SharesOutstanding"))

        if price is None and market_cap is None and shares is None:
            return None
        return {
            "market_price": price,
            "market_cap": market_cap,
            "shares": shares,
            "as_of": _now_iso(),
        }

    # -- public -----------------------------------------------------------
    def get(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Read the last cached snapshot for a ticker. NEVER fetches (see refresh())."""
        cached = self._load_cache().get(ticker.strip().upper())
        return cached if isinstance(cached, dict) else None

    def refresh(self, tickers) -> Dict[str, Any]:
        """Fetch each ticker once and persist to the cache file. For the daily job.

        Merges per-field: a value that comes back empty (e.g. price throttled by the
        rate limit) keeps its prior cached value instead of overwriting with None, so a
        partial day never wipes good data. ~2 calls/ticker, so the five names cost ~10 of
        the 25/day budget.
        """
        cache = self._load_cache()
        summary: Dict[str, Any] = {"refreshed": [], "kept": [], "missing": []}
        for ticker in tickers:
            symbol = ticker.strip().upper()
            prior = cache.get(symbol) if isinstance(cache.get(symbol), dict) else {}
            fresh = self._fetch(symbol)
            if fresh is None:
                (summary["kept"] if prior else summary["missing"]).append(symbol)
                continue
            cache[symbol] = {
                "market_price": fresh.get("market_price") or prior.get("market_price"),
                "market_cap": fresh.get("market_cap") or prior.get("market_cap"),
                "shares": fresh.get("shares") or prior.get("shares"),
                "as_of": fresh["as_of"],
            }
            self._write_cache(cache)
            summary["refreshed"].append(symbol)
            time.sleep(self._intercall_delay_s)
        return summary


def build_provider(data_root: Path) -> MarketDataProvider:
    """Construct the configured provider, degrading to a stub with no key."""
    key = load_api_key()
    cache_path = Path(data_root) / "price_snapshots.json"
    if key:
        return AlphaVantageProvider(api_key=key, cache_path=cache_path)
    return StubProvider()


def refresh_all(data_root: Path, tickers=None) -> Dict[str, Any]:
    """Run the daily market-data refresh for the coverage universe (the job/CLI entry)."""
    from app.registry import ORDER
    provider = build_provider(data_root)
    return provider.refresh(list(tickers) if tickers else ORDER)


if __name__ == "__main__":
    # Daily refresh: `python -m app.marketdata` (schedule once/day, ~10 of 25 calls).
    from app.deps import DATA_ROOT
    print(f"market-data refresh @ {_now_iso()}: {refresh_all(DATA_ROOT)}")
