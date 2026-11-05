# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Run KiCad's bundled ``kicad-cli`` for exports and outputs.

``kicad-cli`` is KiCad's own command-line tool (not a third-party engine); it
plots Gerbers, drill files, STEP/GLB, PDF/SVG, position files, BOM and netlist
from the board and schematic on disk. This module is the thin subprocess layer;
the tools build the argument lists and choose output locations.

Everything runs on the files on disk, so results reflect the last save.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

from ..noconsole import no_window

_CLI_TIMEOUT = 600.0


async def run_cli(cli_path: str, args: list[str],
                  timeout: float = _CLI_TIMEOUT) -> dict[str, Any]:
    """Run ``kicad-cli <args>`` and return exit code, stdout and stderr."""
    if not os.path.exists(cli_path):
        raise RuntimeError(f"kicad-cli not found at {cli_path}")
    proc = await asyncio.create_subprocess_exec(
        cli_path, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **no_window())
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("kicad-cli timed out") from None
    return {
        "returncode": proc.returncode,
        "stdout": (out or b"").decode(errors="replace").strip(),
        "stderr": (err or b"").decode(errors="replace").strip(),
    }


def default_output_dir(source_path: str, tag: str) -> str:
    """A fresh output directory beside the OS temp root, named by tag+time.

    Exports are deliverables the user wants to keep, but writing into their
    project directory is intrusive; this returns a discoverable temp location
    and the tool reports the absolute path. Pass an explicit output_dir to
    place them elsewhere.
    """
    import tempfile
    base = os.path.join(tempfile.gettempdir(), "eda_agent_exports")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(base, f"{tag}_{stamp}")
    os.makedirs(out, exist_ok=True)
    return out


def resolve_output(output_dir: Optional[str], source_path: str,
                   tag: str) -> str:
    d = output_dir or default_output_dir(source_path, tag)
    os.makedirs(d, exist_ok=True)
    return d


def summarize(result: dict[str, Any], produced: str) -> dict[str, Any]:
    """Turn a run_cli result + an output path into a tool response."""
    ok = result["returncode"] == 0
    resp: dict[str, Any] = {"ok": ok, "output": produced}
    if os.path.isdir(produced):
        try:
            resp["files"] = sorted(os.listdir(produced))
        except OSError:
            resp["files"] = []
    if not ok:
        resp["reason"] = result.get("stderr") or result.get("stdout") \
            or f"kicad-cli exited {result['returncode']}"
    if result.get("stderr") and ok:
        resp["warnings"] = result["stderr"]
    return resp
