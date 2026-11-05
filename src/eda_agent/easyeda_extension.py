# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Where the EasyEDA extension source is, and building it from a wheel.

A checkout has it in ``extensions/easyeda``. A wheel carries the same
source files under ``eda_agent/easyeda_extension``, without the build
output, because the build needs Node to prove the entry parses. Every
reader of the extension asks :func:`source_dir` rather than assuming
the checkout layout, which is why an installed wheel used to report the
extension as absent.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .noconsole import no_window

#: The files the wheel carries, the tracked source of the extension.
SOURCE_FILES = (
    "main.js",
    "extension.json",
    "build.py",
    "capabilities.json",
    "iframe_template.html",
    "README.md",
    "BUILD_STAMP.json",
)

_CHECKOUT = Path(__file__).resolve().parents[2] / "extensions" / "easyeda"
_PACKAGED = Path(__file__).resolve().parent / "easyeda_extension"


def source_dir() -> Optional[Path]:
    """The extension source: the checkout's, else the wheel's, else None."""
    for candidate in (_CHECKOUT, _PACKAGED):
        if (candidate / "main.js").is_file():
            return candidate
    return None


def is_packaged(path: Optional[Path] = None) -> bool:
    """Whether ``path`` (default the source in use) is the wheel's copy."""
    path = source_dir() if path is None else path
    return path is not None and path.resolve() == _PACKAGED.resolve()


def default_build_dir() -> Path:
    """Where a wheel's extension is built: beside the default workspace."""
    from .config import _default_workspace_dir

    return _default_workspace_dir().parent / "easyeda-extension"


def build(out_dir: Optional[str] = None) -> tuple[int, str]:
    """Build the .eext and return ``(exit code, message)``.

    A checkout builds in place, as ``python extensions/easyeda/build.py``
    does. The wheel's copy sits in site-packages, so it is copied to
    ``out_dir`` (default :func:`default_build_dir`) and built there.
    """
    source = source_dir()
    if source is None:
        return 1, "no EasyEDA extension source in this install"
    target = source
    if out_dir or is_packaged(source):
        target = Path(out_dir) if out_dir else default_build_dir()
        target.mkdir(parents=True, exist_ok=True)
        for name in SOURCE_FILES:
            if (source / name).is_file():
                shutil.copy2(source / name, target / name)
    if shutil.which("node") is None:
        return 1, ("the build checks the entry parses the way EasyEDA "
                   "loads it, and that needs Node.js on the PATH; install "
                   f"Node and run this again. The source is in {target}")
    done = subprocess.run([sys.executable, "build.py"], cwd=str(target),
                          capture_output=True, text=True, **no_window())
    output = (done.stdout + done.stderr).strip()
    return done.returncode, output
