# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A child process that writes STEP models with CadQuery.

The first model written inside the server took 124 s and 224 s on two
takes (every later one 0.1 s): CadQuery's import, which a fresh process
does in about 2 s. So the server never imports CadQuery itself. This
worker imports it once, writes each model it is sent, and exits when the
server goes away or after a quarter of an hour unused, giving its memory
(some 350 MB) back.

Protocol: one JSON object per line on stdin, ``{"plan": ModelPlan.to_dict(),
"path": ..., "name": ...}``; one JSON line back, ``{"ok": true}`` or
``{"ok": false, "error": ...}``. The first line written is ``{"ready": true}``
once CadQuery has loaded, or ``{"ready": false, "error": ...}``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

IDLE_EXIT_S = 900.0


def _plan(data: dict):
    from eda_agent.design.models3d import ModelPlan, Solid

    solids = [Solid(**s) for s in data.get("solids", [])]
    return ModelPlan(data.get("family", ""), solids, list(data.get("assumed", [])),
                     float(data.get("height", 0.0)), dict(data.get("sources", {})),
                     list(data.get("uncited", [])))


def main() -> int:
    out = sys.stdout
    try:
        import cadquery  # noqa: F401
        from pathlib import Path

        from eda_agent.design.models3d import _write_cadquery_here
    except Exception as exc:                    # noqa: BLE001
        out.write(json.dumps({"ready": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
        out.flush()
        return 1
    out.write(json.dumps({"ready": True}) + "\n")
    out.flush()

    last = [time.monotonic()]

    def idle_watch():
        while True:
            time.sleep(30)
            if time.monotonic() - last[0] > IDLE_EXIT_S:
                os._exit(0)

    threading.Thread(target=idle_watch, daemon=True).start()
    for line in sys.stdin:
        last[0] = time.monotonic()
        try:
            req = json.loads(line)
            _write_cadquery_here(_plan(req["plan"]), Path(req["path"]),
                                 str(req.get("name") or "part"))
            reply = {"ok": True}
        except Exception as exc:                # noqa: BLE001 - reported
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
        out.write(json.dumps(reply) + "\n")
        out.flush()
        last[0] = time.monotonic()
    return 0


if __name__ == "__main__":
    sys.exit(main())
