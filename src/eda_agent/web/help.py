# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The help page of the bridge's window in Altium.

The window's Help button writes ``help:`` to the same link file the
Dashboard button writes a URL to; ``help:<setting>`` names a section to
open on. The process that takes the file opens the help page.

The page is a file shipped with the package, so it opens whether or not the
dashboard runs. A ``#fragment`` on a file link is dropped by Windows on its
way to the browser, so the page is copied into the workspace with the
setting to jump to written into it and its pictures drawn in, and that
copy is opened.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ..atomicfile import replace_with_retry

#: The help page as shipped.
PAGE = Path(__file__).parent / "dashboard_static" / "help" / "config.html"

#: What the panel writes before the setting's id.
PREFIX = "help:"

_SETTING = re.compile(r"[a-z0-9-]{1,40}")


def link_target(text: str, workspace_dir: Path) -> Optional[str]:
    """The address to open for what the link file held: a web address as
    it is, ``help:<setting>`` as the help page on that setting, anything
    else None."""
    text = (text or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    if not text.startswith(PREFIX):
        return None
    setting = text[len(PREFIX):].strip().lower()
    if setting and not _SETTING.fullmatch(setting):
        return None
    return page_for(setting, workspace_dir)


def page_html() -> str:
    """The help page with its labelled pictures of the window drawn in,
    each picture inlined, so the page stands alone."""
    from .help_figures import FIGURES, svg

    html = PAGE.read_text(encoding="utf-8")
    for name in FIGURES:
        fig = svg(name)
        if fig:
            html = html.replace(f"<!-- figure:{name} -->", fig)
    return html


def page_for(setting: str, workspace_dir: Path) -> Optional[str]:
    """A file address of the help page that opens on ``setting`` (the top
    when empty or unknown)."""
    try:
        html = page_html()
    except OSError:
        return None
    if setting and f'id="{setting}"' in html:
        html = html.replace("<html lang=\"en\">",
                            f"<html lang=\"en\" data-jump=\"{setting}\">", 1)
    out = Path(workspace_dir) / "help" / "config.html"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".html.tmp")
        tmp.write_text(html, encoding="utf-8")
        replace_with_retry(tmp, out)
    except OSError:
        return PAGE.as_uri()
    return out.as_uri()


__all__ = ["PAGE", "PREFIX", "link_target", "page_for", "page_html"]
