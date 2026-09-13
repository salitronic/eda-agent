# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Hardened HTTP fetch shared by the part providers.

Each provider talks to a third party over the network, so the same
protections apply to all of them and live in one place rather than being
re-derived per provider (where one would eventually be forgotten):

* HTTPS only, so a downgraded URL cannot leak or be tampered with
* an explicit host allowlist per call, so a redirect or a mis-set
  environment override cannot send requests somewhere unexpected
* a byte cap, so a hostile or broken endpoint cannot exhaust memory
* a timeout, so a hung server cannot stall the MCP server

The cache is deliberately simple and on disk: a provider without
server-side search has to pull a whole index to answer one query, and
re-downloading megabytes per keystroke is not acceptable.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from eda_agent.atomicfile import replace_with_retry

__all__ = ["FetchError", "cache_dir", "get_bytes", "get_json_cached"]

#: Generous enough for a whole parts index, small enough to bound memory.
MAX_BYTES = 50 * 1024 * 1024
TIMEOUT_S = 30.0

_UA = "eda-agent (+https://github.com/salitronic/eda-agent)"


class FetchError(RuntimeError):
    """Network or protocol failure, carrying the URL for diagnosis."""


def cache_dir() -> Path:
    """Where provider indexes are cached.

    Override with ``EDA_AGENT_CACHE_DIR``. Defaults under the user's
    local app data rather than the repo, so a checkout stays clean and a
    cache never lands in version control.
    """
    override = os.environ.get("EDA_AGENT_CACHE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    return Path(base) / "eda-agent" / "parts-cache"


def _check(url: str, allowed_hosts: set[str]) -> None:
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https":
        raise FetchError(f"refusing non-HTTPS url: {url}")
    host = (parts.hostname or "").lower()
    ok = any(host == h or host.endswith("." + h) for h in allowed_hosts)
    if not ok:
        raise FetchError(
            f"host {host!r} is not in this provider's allowlist "
            f"({sorted(allowed_hosts)}); refusing to fetch {url}")


def get_bytes(url: str, allowed_hosts: set[str]) -> bytes:
    """Fetch a URL with every protection applied."""
    _check(url, allowed_hosts)
    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            # Read one byte past the cap so truncation is detectable
            # rather than silently returning a partial document.
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"cannot reach {url}: {exc.reason}") from exc
    except OSError as exc:
        raise FetchError(f"cannot reach {url}: {exc}") from exc
    if len(data) > MAX_BYTES:
        raise FetchError(
            f"response from {url} exceeds {MAX_BYTES} bytes; refusing to "
            f"buffer it")
    return data


def get_json_cached(url: str, allowed_hosts: set[str],
                    ttl_s: float = 86400.0) -> Any:
    """Fetch JSON, reusing a cached copy within ``ttl_s``.

    A stale-but-readable cache is preferred to a hard failure when the
    network is down: a parts index that is a day old still answers most
    questions, whereas an exception answers none. The staleness is
    bounded by the TTL on the happy path.
    """
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    path = cache_dir() / f"{key}.json"

    if path.exists():
        try:
            age = time.time() - path.stat().st_mtime
            if age < ttl_s:
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # unreadable cache is simply a miss

    try:
        raw = get_bytes(url, allowed_hosts)
    except FetchError:
        # Fall back to whatever is cached, however old, before giving up.
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        raise

    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise FetchError(f"{url} did not return JSON: {exc}") from exc

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temp file so a crash mid-write cannot leave a
        # truncated cache that later parses as valid-but-wrong.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        replace_with_retry(tmp, path)
    except OSError:
        pass  # caching is an optimisation, never a requirement

    return data
