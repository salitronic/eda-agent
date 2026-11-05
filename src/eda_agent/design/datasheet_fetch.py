# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Download a datasheet PDF once and keep it, for the client to read.

The client reads the pages itself; this only fetches and caches the file
so every later look at the same datasheet is the same bytes. Standard
library only (``urllib``), no PDF parser:

* http and https URLs only;
* a size cap and a timeout, so a broken or hostile server cannot fill
  the disk or hang the server;
* the bytes must carry the ``%PDF`` header, so an HTML login or cookie
  page saved under a .pdf name is refused rather than cached;
* stored under ``<workspace>/datasheets`` by a hash of the URL, with a
  JSON sidecar recording the URL, part number, SHA-256, size and fetch
  time. A cached copy whose bytes no longer match its sidecar's hash is
  fetched again.

The page count is read by counting page objects in the file, which is
cheap and usually right, and is reported as approximate: a PDF that packs
its objects into compressed streams hides them, and then the count falls
back to the page tree's ``/Count``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from eda_agent.atomicfile import replace_with_retry

__all__ = ["MAX_BYTES", "TIMEOUT_S", "datasheet_dir", "fetch_datasheet",
           "count_pages"]

#: Largest datasheet accepted, bytes. Big enough for a full family
#: reference manual, small enough to bound memory and disk.
MAX_BYTES = 50 * 1024 * 1024
#: Seconds to wait on the server for each step (connect, each read). Per
#: step, so a slow download that keeps moving still finishes; at 60 a host
#: that never answers (st.com) cost a minute on every try.
TIMEOUT_S = 20.0
#: How long a host that failed to answer is answered from memory.
HOST_FAILURE_MEMORY_S = 600.0
_failed_hosts: dict[str, tuple[float, str]] = {}
#: The header is looked for in the first kilobyte rather than at byte 0,
#: so a server that prepends a few bytes of whitespace still passes.
_HEADER_WINDOW = 1024
_CHUNK = 1024 * 1024
_UA = "eda-agent datasheet fetch (+https://github.com/salitronic/eda-agent)"

_PAGE_RE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")
_COUNT_RE = re.compile(rb"/Type\s*/Pages\b(?:(?!>>).)*?/Count\s+(\d+)", re.S)
_COUNT_RE2 = re.compile(rb"/Count\s+(\d+)(?:(?!>>).)*?/Type\s*/Pages\b", re.S)


def datasheet_dir() -> Path:
    """``<workspace>/datasheets``, the workspace the bridge already uses."""
    from eda_agent.config import get_config
    return get_config().workspace_dir / "datasheets"


def count_pages(data: bytes) -> tuple[Optional[int], str]:
    """Approximate page count and how it was found."""
    n = len(_PAGE_RE.findall(data))
    if n:
        return n, "page objects counted in the file"
    counts = [int(m.group(1)) for m in _COUNT_RE.finditer(data)]
    counts += [int(m.group(1)) for m in _COUNT_RE2.finditer(data)]
    if counts:
        return max(counts), "the page tree's /Count (page objects are compressed)"
    return None, "not found without a PDF parser (compressed object streams)"


def _refuse(reason: str, **extra: Any) -> dict:
    out = {"ok": False, "reason": reason}
    out.update(extra)
    return out


def _check_url(url: str) -> str:
    parts = urllib.parse.urlparse(url)
    if parts.scheme.lower() not in ("http", "https"):
        return (f"only http and https URLs are fetched, not "
                f"{parts.scheme or 'a URL with no scheme'!r}")
    if not parts.hostname:
        return "the URL names no host"
    return ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _reply(meta: dict, path: Path, side: Path, cache: str) -> dict:
    return {
        "ok": True, "path": str(path), "sidecar": str(side), "cache": cache,
        "url": meta.get("url"), "final_url": meta.get("final_url"),
        "part_number": meta.get("part_number", ""),
        "sha256": meta.get("sha256"), "size": meta.get("size"),
        "fetched_at": meta.get("fetched_at"),
        "content_type": meta.get("content_type", ""),
        "pages": meta.get("pages"), "pages_approximate": True,
        "pages_method": meta.get("pages_method", ""),
        "next_step": (f"Read {path} (the pages that hold the package drawing "
                      "and the recommended land pattern), and cite this file's "
                      "URL, revision and page numbers in the package spec"),
    }


def fetch_datasheet(url: str, part_number: str = "", *,
                    directory: Optional[Path] = None,
                    max_bytes: int = MAX_BYTES,
                    timeout: float = TIMEOUT_S) -> dict:
    """Fetch ``url`` into the datasheet cache, or answer from the cache.

    Returns ``{ok, path, sidecar, cache ('hit' or 'miss'), url, sha256,
    size, pages, pages_approximate, pages_method, fetched_at, ...}`` or
    ``{ok: False, reason}``. Never raises for a network or content fault.
    """
    url = str(url or "").strip()
    problem = _check_url(url)
    if problem:
        return _refuse(problem, url=url)
    folder = Path(directory) if directory is not None else datasheet_dir()
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    path = folder / f"{key}.pdf"
    side = folder / f"{key}.json"

    stale = ""
    if path.exists() and side.exists():
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
            if meta.get("sha256") == _sha256(path):
                if part_number and not meta.get("part_number"):
                    meta["part_number"] = str(part_number)
                    _write_json(side, meta)
                return _reply(meta, path, side, "hit")
            stale = "the cached copy no longer matched its recorded hash"
        except (OSError, ValueError):
            stale = "the cached sidecar could not be read"

    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    seen = _failed_hosts.get(host)
    if seen and time.monotonic() - seen[0] < HOST_FAILURE_MEMORY_S:
        return _refuse(
            f"{host} did not answer {int(time.monotonic() - seen[0])} s ago "
            f"({seen[1]}), so it was not tried again. Download the datasheet "
            "in a browser and read the local file, or try another source",
            url=url, host_unreachable=True)

    request = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "application/pdf,*/*;q=0.8"})
    chunks: list[bytes] = []
    total = 0
    final_url = url
    content_type = ""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            try:
                final_url = response.geturl() or url
            except Exception:                           # noqa: BLE001
                final_url = url
            try:
                content_type = str(response.headers.get("Content-Type") or "")
            except Exception:                           # noqa: BLE001
                content_type = ""
            while True:
                block = response.read(min(_CHUNK, max_bytes + 1 - total))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
                if total > max_bytes:
                    return _refuse(
                        f"the file is larger than {max_bytes} bytes; refusing "
                        "to store it", url=url)
    except urllib.error.HTTPError as exc:
        return _refuse(f"HTTP {exc.code} from {url}", url=url)
    except urllib.error.URLError as exc:
        _failed_hosts[host] = (time.monotonic(), str(exc.reason))
        return _refuse(f"cannot reach {url}: {exc.reason}", url=url)
    except (OSError, ValueError) as exc:
        if isinstance(exc, (TimeoutError, ConnectionError)):
            _failed_hosts[host] = (time.monotonic(), str(exc) or type(exc).__name__)
        return _refuse(f"cannot fetch {url}: {exc}", url=url)

    problem = _check_url(final_url)
    if problem:
        return _refuse(f"the server redirected to {final_url!r}: {problem}",
                       url=url)
    data = b"".join(chunks)
    if not data:
        return _refuse(f"{url} answered with no content", url=url)
    if b"%PDF" not in data[:_HEADER_WINDOW]:
        head = data[:200].lstrip().lower()
        kind = "an HTML page" if head.startswith((b"<!doctype", b"<html")) \
            else "something that is not a PDF"
        return _refuse(
            f"{url} returned {kind} (Content-Type {content_type or 'not given'}"
            "), not a PDF; the site may want a login, a cookie or a click "
            "through. Download it in a browser and read the local file instead",
            url=url, content_type=content_type)

    pages, method = count_pages(data)
    meta = {
        "url": url, "final_url": final_url, "part_number": str(part_number or ""),
        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
        "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"),
        "content_type": content_type, "pages": pages, "pages_method": method,
    }
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".pdf.tmp")
        tmp.write_bytes(data)
        replace_with_retry(tmp, path)
        _write_json(side, meta)
    except OSError as exc:
        return _refuse(f"could not store the datasheet under {folder}: {exc}",
                       url=url)
    out = _reply(meta, path, side, "miss")
    if stale:
        out["note"] = f"fetched again: {stale}"
    return out


def _write_json(path: Path, meta: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    replace_with_retry(tmp, path)
