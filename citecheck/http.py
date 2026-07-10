"""A tiny, polite HTTP helper built only on urllib.

Adds a descriptive User-Agent (Crossref "polite pool"), per-host rate limiting,
retry-with-backoff, and JSON/XML/text conveniences. No third-party deps.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from citecheck import __version__

# Contact email surfaces citecheck in API logs and unlocks Crossref's polite pool.
# Override with the CITECHECK_MAILTO environment variable.
import os

_MAILTO = os.environ.get("CITECHECK_MAILTO", "citecheck@example.com")
USER_AGENT = f"citecheck/{__version__} (https://github.com/; mailto:{_MAILTO})"

# Minimum seconds between requests to the same host (be a good API citizen).
_MIN_INTERVAL = float(os.environ.get("CITECHECK_MIN_INTERVAL", "0.34"))
_last_call: Dict[str, float] = {}


class HttpError(Exception):
    def __init__(self, status: Optional[int], message: str):
        super().__init__(message)
        self.status = status


def _throttle(host: str) -> None:
    now = time.monotonic()
    prev = _last_call.get(host, 0.0)
    wait = _MIN_INTERVAL - (now - prev)
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def _request(
    url: str,
    *,
    accept: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 15.0,
    retries: int = 3,
) -> str:
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)

    host = urlparse(url).netloc
    last_exc: Optional[Exception] = None

    for attempt in range(retries):
        _throttle(host)
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": accept}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as e:
            # 404 is a definitive "not found" for identifier lookups — don't retry.
            if e.code == 404:
                raise HttpError(404, f"404 Not Found: {url}")
            # 429 / 5xx are transient; back off and retry.
            last_exc = HttpError(e.code, f"HTTP {e.code}: {url}")
            if e.code not in (429, 500, 502, 503, 504):
                raise last_exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = HttpError(None, f"connection error: {e}")

        # Exponential backoff before the next attempt.
        if attempt < retries - 1:
            time.sleep(min(8.0, 0.75 * (2 ** attempt)))

    assert last_exc is not None
    raise last_exc


def get_json(url: str, *, params: Optional[Dict[str, Any]] = None,
             timeout: float = 15.0, retries: int = 3) -> Any:
    body = _request(url, accept="application/json", params=params,
                    timeout=timeout, retries=retries)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise HttpError(None, f"invalid JSON from {url}: {e}")


def get_text(url: str, *, accept: str = "text/plain",
             params: Optional[Dict[str, Any]] = None,
             timeout: float = 15.0, retries: int = 3) -> str:
    return _request(url, accept=accept, params=params,
                    timeout=timeout, retries=retries)
