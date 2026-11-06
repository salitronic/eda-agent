# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The dashboard's engine: the design as one model the page reads at once,
and what the page can do to it.

Everything here answers from what the cockpit already holds (the board in
front, read when it changes, and its checks), so a page request never
waits on Altium. Actions and the two engines (the placer and the route
planner) run on their own threads and report through ``/api/wb/state``.
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from typing import Any, Optional

from flask import Flask, Response, jsonify, request

# -- the model, from the cockpit's snapshot ---------------------------------


def _problem_refs(findings: list[dict]) -> dict[str, list[int]]:
    """Each part and net a problem names, to the problems' indexes."""
    out: dict[str, list[int]] = {}
    for i, f in enumerate(findings):
        for ref in f.get("refs") or []:
            base = str(ref).split(":", 1)[-1].split("#", 1)[0]
            for key in {str(ref), base}:
                out.setdefault(key, []).append(i)
    return out


def parts_index(snap: dict, findings: list[dict], bom: Optional[dict] = None) -> list[dict]:
    """One row per part: where it is, its footprint, its nets, its
    problems, and its BOM fields when the BOM has been read."""
    nets_of: dict[str, set] = {}
    pads_of: dict[str, int] = {}
    for p in snap.get("pads") or []:
        if p.get("comp"):
            pads_of[p["comp"]] = pads_of.get(p["comp"], 0) + 1
            if p.get("net"):
                nets_of.setdefault(p["comp"], set()).add(p["net"])
    probs = _problem_refs(findings)
    rows = []
    for c in snap.get("components") or []:
        ref = c["ref"]
        row = {"ref": ref, "footprint": c.get("footprint", ""), "side": c.get("side", ""),
               "x": c.get("x"), "y": c.get("y"), "rot": c.get("rot"),
               "locked": bool(c.get("locked")), "pads": pads_of.get(ref, 0),
               "nets": sorted(nets_of.get(ref, ())), "problems": len(set(probs.get(ref, ())))}
        if bom and ref in bom:
            row.update({k: bom[ref].get(k, "") for k in ("comment", "lib_ref")})
        rows.append(row)
    rows.sort(key=lambda r: _natural(r["ref"]))
    return rows


def nets_index(snap: dict, findings: list[dict]) -> list[dict]:
    """One row per net: its pads and parts, how far it is routed, the
    copper length and layers, its vias and problems."""
    status = (snap.get("status") or {}).get("unrouted") or {}
    rows: dict[str, dict] = {}

    def row(net):
        return rows.setdefault(net, {"net": net, "pads": 0, "parts": set(), "length": 0.0,
                                     "layers": set(), "vias": 0})
    for p in snap.get("pads") or []:
        if p.get("net"):
            r = row(p["net"])
            r["pads"] += 1
            if p.get("comp"):
                r["parts"].add(p["comp"])
    for t in snap.get("tracks") or []:
        if t.get("net"):
            r = row(t["net"])
            r["length"] += math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"])
            r["layers"].add(t.get("layer", ""))
    for a in snap.get("arcs") or []:
        if a.get("net"):
            r = row(a["net"])
            sweep = abs((a.get("a2", 0) - a.get("a1", 0)) % 360) or 360
            r["length"] += math.radians(sweep) * a.get("r", 0)
            r["layers"].add(a.get("layer", ""))
    for v in snap.get("vias") or []:
        if v.get("net"):
            row(v["net"])["vias"] += 1
    probs = _problem_refs(findings)
    out = []
    for net, r in rows.items():
        state = "single" if r["pads"] < 2 else status.get(net, "routed")
        out.append({"net": net, "pads": r["pads"], "parts": len(r["parts"]),
                    "status": state, "length": round(r["length"], 1),
                    "layers": sorted(x for x in r["layers"] if x), "vias": r["vias"],
                    "problems": len(set(probs.get(net, ())))})
    order = {"open": 0, "partly": 1, "routed": 2, "single": 3}
    out.sort(key=lambda r: (order.get(r["status"], 9), _natural(r["net"])))
    return out


def summary(snap: Optional[dict], findings: list[dict]) -> dict[str, Any]:
    """The status strip: what is left before the board is done."""
    if not snap:
        return {}
    st = snap.get("status") or {}
    sev = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        sev[f.get("severity", "info")] = sev.get(f.get("severity", "info"), 0) + 1
    counts = snap.get("counts") or {}
    return {"board": snap.get("board", ""), "parts": counts.get("components", 0),
            "nets": st.get("nets", 0), "routed": st.get("routed", 0),
            "partly": st.get("partly", 0), "not_started": st.get("not_started", 0),
            "missing_connections": st.get("missing_connections", 0),
            "errors": sev["error"], "warnings": sev["warning"], "info": sev["info"]}


def _natural(text: str):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(text))]


# -- the engines: placer and route planner, previewed then applied ----------


class Engine:
    """One placer or route-planner run: started, watched, previewed, then
    applied or dropped. One at a time."""

    def __init__(self, call=None, jobs=None) -> None:
        self.run: dict[str, Any] = {}
        self._call = call
        self._jobs = jobs

    def call(self, tool: str, args: dict) -> Any:
        return (self._call or _call)(tool, args)

    def jobs(self):
        if self._jobs is not None:
            return self._jobs
        from ..design.jobs import get_job_store
        return get_job_store()

    def busy(self) -> bool:
        return self.run.get("state") in ("running", "applying")

    def snapshot(self) -> dict[str, Any]:
        out = {k: v for k, v in self.run.items() if k not in ("result",)}
        if self.run.get("job_id") and self.run.get("state") == "running":
            st = self.jobs().status(self.run["job_id"]) or {}
            out["job"] = {"status": st.get("status"), "started": st.get("started")}
            if st.get("status") == "done":
                self._finish()
                out = {k: v for k, v in self.run.items() if k not in ("result", "preview")}
            elif st.get("status") == "error" or not st:
                self.run.update(state="failed", error=st.get("error") or "the job was lost")
                out = {k: v for k, v in self.run.items() if k not in ("result", "preview")}
        out.pop("preview", None)
        out["seconds"] = round(time.time() - self.run["started"], 1) if self.run else 0
        return out

    def _finish(self) -> None:
        res = self.jobs().result(self.run["job_id"]) or {}
        result = res.get("result") or {}
        self.run["result"] = result
        self.run["state"] = "proposed"
        s = result.get("summary") or {}
        if self.run["kind"] == "place":
            moves = result.get("moves") or []
            self.run["preview"] = {"moves": moves}
            self.run["summary"] = {
                "moves": len(moves), "wirelength_before": s.get("hpwl_before"),
                "wirelength_after": s.get("hpwl_after"), "failed": s.get("failed") or [],
                "overlaps": len(s.get("overlaps") or []), "violations": s.get("violations"),
                "drc": s.get("drc_status"), "seconds": s.get("seconds"),
                "notes": result.get("notes") or []}
        else:
            tracks = result.get("tracks") or []
            vias = result.get("vias") or []
            self.run["preview"] = {
                "tracks": [{"x1": t["x1"], "y1": t["y1"], "x2": t["x2"], "y2": t["y2"],
                            "w": t.get("width", 8), "layer": t.get("layer", ""),
                            "net": t.get("net_name", "")} for t in tracks],
                "vias": [{"x": v["x"], "y": v["y"], "d": v.get("size", 20),
                          "net": v.get("net", "")} for v in vias]}
            self.run["summary"] = {
                "tracks": len(tracks), "vias": len(vias),
                "completion": s.get("completion"), "unrouted_nets": s.get("unrouted_nets"),
                "missing_connections": s.get("missing_connections"),
                "violations": s.get("violations"), "drc": s.get("drc_status"),
                "sharp_bends": s.get("sharp_bends"), "seconds": s.get("seconds"),
                "unrouted": result.get("unrouted"), "notes": result.get("notes") or []}

    def start(self, kind: str, board_file: str, parts=None, nets=None,
              compact: bool = False) -> dict[str, Any]:
        if self.busy():
            return {"ok": False, "reason": f"the {self.run.get('kind')} run is still busy"}
        args: dict[str, Any] = {"expect_file": board_file}
        if kind == "place":
            tool = "pcb_autoplace"
            if parts:
                args["parts"] = list(parts)
            if compact:
                args["compact"] = True
        elif kind == "route":
            tool = "pcb_autoroute"
            if nets:
                args["nets"] = list(nets)
        else:
            return {"ok": False, "reason": "kind is place or route"}
        reply = self.call(tool, args)
        job = reply.get("job_id") if isinstance(reply, dict) else None
        if not job:
            return {"ok": False, "reason": str((reply or {}).get("error") or reply)}
        self.run = {"kind": kind, "state": "running", "job_id": job, "board": board_file,
                    "started": time.time(), "scope": list(parts or nets or [])}
        return {"ok": True, "job_id": job}

    def apply(self) -> dict[str, Any]:
        if self.run.get("state") != "proposed":
            return {"ok": False, "reason": "there is no proposal to apply"}
        tool = "pcb_autoplace_apply" if self.run["kind"] == "place" else "pcb_autoroute_apply"
        self.run["state"] = "applying"
        run = self.run

        def work():
            reply = self.call(tool, {"job_id": run["job_id"], "expect_file": run["board"]})
            run["applied"] = reply
            run["state"] = "applied" if not (isinstance(reply, dict) and reply.get("error")) \
                else "failed"
            if self._call is None:
                from .cockpit import get_cockpit
                get_cockpit().load(run["board"])
        threading.Thread(target=work, name="engine-apply", daemon=True).start()
        return {"ok": True}

    def drop(self) -> dict[str, Any]:
        if self.busy() and self.run.get("state") == "applying":
            return {"ok": False, "reason": "it is being applied"}
        self.run = {}
        return {"ok": True}


def _call(tool: str, args: dict) -> Any:  # pragma: no cover - needs the server
    """A registered tool on the board it names (``expect_file``), in one
    turn: that board brought to the front, the tool run, and the document
    that was in front given back."""
    from ..bridge.altium_bridge import following_front, get_bridge, tool_call
    from .board_actions import _tool
    board = str(args.get("expect_file") or "")
    with following_front(background=False), tool_call():
        bridge = get_bridge()
        prev = ""
        if board:
            front = bridge.send_command("application.get_active_document", {"front": "true"},
                                        timeout=15.0)
            prev = front.get("file_path", "") if isinstance(front, dict) else ""
            bridge.send_command("pcb.focus_board", {"board_path": board}, timeout=30.0)
        try:
            return asyncio.run(_tool(tool)(**args))
        finally:
            if board and prev and not _same_file(prev, board):
                try:
                    bridge.send_command("application.set_active_document",
                                        {"file_path": prev}, timeout=30.0)
                except Exception:                         # noqa: BLE001
                    pass


def _board_rules(board: str) -> dict:  # pragma: no cover - needs Altium
    """The rules of BOARD, read in one turn that gives the front back."""
    from ..bridge.altium_bridge import following_front, get_bridge, tool_call
    with following_front(background=False), tool_call():
        bridge = get_bridge()
        front = bridge.send_command("application.get_active_document", {"front": "true"},
                                    timeout=15.0)
        prev = front.get("file_path", "") if isinstance(front, dict) else ""
        bridge.send_command("pcb.focus_board", {"board_path": board}, timeout=30.0)
        try:
            return bridge.send_command("pcb.get_design_rules", {}, timeout=30.0)
        finally:
            if prev and not _same_file(prev, board):
                try:
                    bridge.send_command("application.set_active_document",
                                        {"file_path": prev}, timeout=30.0)
                except Exception:                         # noqa: BLE001
                    pass


def rules_report(board: str, got: Any) -> dict:
    """What the page shows of a board's rules."""
    from ..export import drc_rules
    rules = (got or {}).get("rules") if isinstance(got, dict) else None
    if not isinstance(rules, list):
        return {"ok": False, "reason": "the rules could not be read"}
    baseline = drc_rules.read_baseline(board)
    out = {"ok": True, "board": board, "count": len(rules),
           "fingerprint": drc_rules.fingerprint(rules),
           "sheet": drc_rules.sheet(rules),
           "not_checked": drc_rules.not_checked(rules, None),
           "approved": baseline is not None}
    if baseline:
        out.update(approved_at=baseline.get("approved_at"),
                   approved_note=baseline.get("note", ""),
                   changes=drc_rules.diff(baseline["rules"], rules),
                   logged=drc_rules.read_log(board, since=str(baseline.get("approved_at") or "")))
    else:
        out.update(changes=[], logged=drc_rules.read_log(board))
    return out


def _same_file(a: str, b: str) -> bool:
    return bool(a) and str(a).replace("/", "\\").lower() == str(b).replace("/", "\\").lower()


_DOCS: dict[str, Any] = {"at": 0.0, "rows": []}


_ENGINE = Engine()
_BOM: dict[str, Any] = {"file": "", "rows": None, "at": 0.0}


def _bom(project_hint: str) -> Optional[dict]:
    """The BOM by designator, read once per board and kept."""
    if _BOM["file"] == project_hint and _BOM["rows"] is not None:
        return _BOM["rows"]
    from ..bridge.altium_bridge import following_front, get_bridge
    try:
        with following_front():
            reply = get_bridge().send_command("project.get_bom", {"limit": "100000"},
                                              timeout=120.0)
    except Exception:                                     # noqa: BLE001
        return None
    rows = {r["designator"]: r for r in (reply or {}).get("components") or []
            if isinstance(r, dict) and r.get("designator")}
    _BOM.update(file=project_hint, rows=rows, at=time.time())
    return rows


# -- the API ----------------------------------------------------------------


def register(app: Flask) -> None:
    from .board_actions import get_runner, plan
    from .cockpit import get_cockpit

    def cockpit():
        c = get_cockpit()
        c.touch()
        return c

    @app.route("/api/wb/state")
    def wb_state() -> Response:
        c = cockpit()
        snap = c.view
        return jsonify({"ok": True, "cockpit": c.snapshot(),
                        "summary": summary(snap, c.findings),
                        "action": get_runner().snapshot(),
                        "engine": _ENGINE.snapshot()})

    @app.route("/api/wb/board")
    def wb_board() -> Response:
        c = cockpit()
        since = request.args.get("since", type=int)
        if c.view is None:
            return jsonify({"ok": True, "version": 0, "empty": True})
        if since is not None and since == c.view_version:
            return jsonify({"ok": True, "version": c.view_version, "unchanged": True})
        return jsonify({"ok": True, "version": c.view_version, "board": c.view,
                        "diff": c.memo.diff if c.memo else None})

    @app.route("/api/wb/problems")
    def wb_problems() -> Response:
        c = cockpit()
        return jsonify({"ok": True, "version": c.checks_version,
                        "pending": c.checks_pending,
                        "problems": [dict(f, id=i) for i, f in enumerate(c.findings)]})

    @app.route("/api/wb/parts")
    def wb_parts() -> Response:
        c = cockpit()
        if c.view is None:
            return jsonify({"ok": True, "parts": []})
        bom = _BOM["rows"] if _BOM["file"] == (c.memo.file if c.memo else "") else None
        if request.args.get("bom") == "1" and c.memo:
            bom = _bom(c.memo.file)
        return jsonify({"ok": True, "bom": bom is not None,
                        "parts": parts_index(c.view, c.findings, bom)})

    @app.route("/api/wb/nets")
    def wb_nets() -> Response:
        c = cockpit()
        return jsonify({"ok": True, "nets": nets_index(c.view, c.findings) if c.view else []})

    @app.route("/api/wb/inspect")
    def wb_inspect() -> Response:
        """A part or a net with everything the page knows of it."""
        c = cockpit()
        kind, ident = request.args.get("kind", ""), request.args.get("id", "")
        if c.view is None:
            return jsonify({"ok": False, "reason": "no board read yet"})
        probs = _problem_refs(c.findings)
        if kind == "part":
            row = next((p for p in parts_index(c.view, c.findings,
                                               _BOM["rows"]) if p["ref"] == ident), None)
            if row is None:
                return jsonify({"ok": False, "reason": f"{ident} is not on this board"})
            pads = [p for p in c.view.get("pads") or [] if p.get("comp") == ident]
            row["pins"] = [{"pad": p["name"], "net": p.get("net", "")} for p in pads]
            if _BOM["rows"] and ident in _BOM["rows"]:
                row["bom"] = _BOM["rows"][ident]
        elif kind == "net":
            row = next((n for n in nets_index(c.view, c.findings) if n["net"] == ident), None)
            if row is None:
                return jsonify({"ok": False, "reason": f"no net {ident} on this board"})
            row["pins"] = [f"{p['comp']}.{p['name']}" for p in c.view.get("pads") or []
                           if p.get("net") == ident and p.get("comp")]
        else:
            return jsonify({"ok": False, "reason": "kind is part or net"})
        row["problem_ids"] = sorted(set(probs.get(ident, ())))
        return jsonify({"ok": True, "kind": kind, "item": row})

    def _shown(c) -> Optional[str]:
        """The board the page shows, or None before one is read."""
        return c.memo.file if c.memo else (c.pinned or None)

    @app.route("/api/wb/documents")
    def wb_documents() -> Response:
        """The boards open in Altium, the one shown and the one in front."""
        c = cockpit()
        now = time.time()
        if now - _DOCS["at"] > 5 or request.args.get("fresh") == "1":
            try:
                reply = c.send("application.get_open_documents", {}, 15.0, urgent=True)
            except Exception as e:                        # noqa: BLE001
                return jsonify({"ok": False, "reason": f"Altium did not answer: {e}"})
            rows = reply if isinstance(reply, list) else (reply or {}).get("documents") or []
            _DOCS.update(at=now, rows=[
                {"file": d.get("file_path", ""), "name": d.get("file_name", ""),
                 "modified": bool(d.get("modified"))}
                for d in rows if isinstance(d, dict)
                and str(d.get("document_kind", "")).upper() == "PCB"])
        shown = _shown(c) or ""
        return jsonify({"ok": True, "shown": shown, "front": c.front, "follow": c.follow,
                        "boards": [dict(d, shown=_same_file(d["file"], shown),
                                        front=_same_file(d["file"], c.front))
                                   for d in _DOCS["rows"]]})

    @app.route("/api/wb/open", methods=["POST"])
    def wb_open() -> Response:
        """Show this board (``file``) and stay on it; it is read now, in
        one turn that gives the front back to the document that had it."""
        c = cockpit()
        body = request.get_json(silent=True) or {}
        file = str(body.get("file") or "")
        if not file:
            return jsonify({"ok": False, "reason": "file is required"})
        c.follow = False
        return jsonify(c.load(file))

    @app.route("/api/wb/follow", methods=["POST"])
    def wb_follow() -> Response:
        """``on``: show whichever board is in front, as it changes."""
        c = cockpit()
        c.follow = bool((request.get_json(silent=True) or {}).get("on"))
        return jsonify({"ok": True, "follow": c.follow})

    @app.route("/api/wb/action", methods=["POST"])
    def wb_action() -> Response:
        """One action on the board the page shows, whatever is in front:
        it is brought forward, its selection read, the action run and the
        front given back, all in one turn."""
        c = cockpit()
        body = request.get_json(silent=True) or {}
        runner = get_runner()
        if runner.busy():
            return jsonify({"ok": False, "reason": "another action is still running"})
        board = _shown(c)
        if not board:
            return jsonify({"ok": False, "reason": "choose a board first"})
        action = str(body.get("action") or "")
        args = body.get("args") or {}
        view = c.view
        try:
            plan(action, args, {"parts": ["?"] * 3, "nets": ["?"]}, view, board)
        except ValueError as e:
            if "no action" in str(e) or "mode is" in str(e):
                return jsonify({"ok": False, "reason": str(e)})
        from .board_actions import after

        def done(_name):
            # Read the board again, whether or not it is in front now.
            c.load(board)
        return jsonify(runner.start(action, lambda sel: plan(action, args, sel, view, board),
                                    done, board=board))

    @app.route("/api/wb/engine", methods=["POST"])
    def wb_engine() -> Response:
        """Start the placer or the route planner (``kind`` place / route) on
        the board shown; ``apply`` or ``drop`` its proposal. Each runs in
        one turn that brings the board forward and gives the front back."""
        c = cockpit()
        body = request.get_json(silent=True) or {}
        what = str(body.get("do") or "start")
        if what == "apply":
            return jsonify(_ENGINE.apply())
        if what == "drop":
            return jsonify(_ENGINE.drop())
        board = _shown(c)
        if not board:
            return jsonify({"ok": False, "reason": "choose a board first"})
        sel = ((c.state or {}).get("selected") or {}) if body.get("selection") else {}
        kind = str(body.get("kind") or "")
        parts = body.get("parts") or sel.get("parts") or None
        nets = body.get("nets") or sel.get("nets") or None
        if body.get("selection") and not (parts if kind == "place" else nets):
            return jsonify({"ok": False, "reason": "nothing of that kind is selected in Altium"})
        return jsonify(_ENGINE.start(kind, board, parts=parts, nets=nets,
                                     compact=bool(body.get("compact"))))

    @app.route("/api/wb/focus", methods=["POST"])
    def wb_focus() -> Response:
        """Bring the board the page shows to the front in Altium."""
        c = cockpit()
        board = _shown(c)
        if not board:
            return jsonify({"ok": False, "reason": "no board has been read yet"})
        try:
            reply = c.send("pcb.focus_board", {"board_path": board}, 30.0, urgent=True)
        except Exception as e:                            # noqa: BLE001
            return jsonify({"ok": False, "reason": f"{type(e).__name__}: {e}"})
        if isinstance(reply, dict) and (reply.get("error") or reply.get("success") is False):
            return jsonify({"ok": False, "reason": str(reply.get("error") or reply)})
        return jsonify({"ok": True, "board": board})

    @app.route("/api/wb/library")
    def wb_library() -> Response:
        from .library_view import get_library_view
        return jsonify({"ok": True, **get_library_view().snapshot()})

    @app.route("/api/wb/library/read", methods=["POST"])
    def wb_library_read() -> Response:
        from .library_view import get_library_view
        c = cockpit()
        return jsonify(get_library_view().read(c.memo.file if c.memo else ""))

    @app.route("/api/wb/library/update", methods=["POST"])
    def wb_library_update() -> Response:
        """``designators``: refresh those placed parts from their PcbLib on
        the board shown. ``target`` schematic or pcb instead: Altium's
        Update From Libraries over the whole project."""
        c = cockpit()
        body = request.get_json(silent=True) or {}
        runner = get_runner()
        if runner.busy():
            return jsonify({"ok": False, "reason": "another action is still running"})
        des = [str(d) for d in body.get("designators") or [] if d]
        target = str(body.get("target") or "")
        board = ""
        if des:
            board = _shown(c) or ""
            if not board:
                return jsonify({"ok": False, "reason": "choose a board first"})
            calls = [("pcb_update_footprints_from_library",
                      {"designators": des, "board_path": board})]
            name = "update footprints"
        elif target in ("schematic", "pcb"):
            calls = [("proj_update_from_libraries", {"target": target})]
            name = "update from libraries"
        else:
            return jsonify({"ok": False, "reason": "give designators, or target schematic "
                            "or pcb"})
        return jsonify(runner.start(name, calls,
                                    lambda _n: c.load(board) if board else c.ask_refresh(),
                                    board=board))

    @app.route("/api/wb/rules")
    def wb_rules() -> Response:
        """The design rules of the board shown and how they stand against
        the approved set."""
        board = _shown(cockpit()) or ""
        if not board:
            return jsonify({"ok": False, "reason": "choose a board first"})
        try:
            got = _board_rules(board)
        except Exception as exc:                          # noqa: BLE001
            return jsonify({"ok": False, "reason": f"Altium did not answer: {exc}"})
        return jsonify(rules_report(board, got))

    @app.route("/api/wb/rules/approve", methods=["POST"])
    def wb_rules_approve() -> Response:
        """The person approves the rules the board holds now."""
        from ..export import drc_rules
        board = _shown(cockpit()) or ""
        if not board:
            return jsonify({"ok": False, "reason": "choose a board first"})
        body = request.get_json(silent=True) or {}
        try:
            got = _board_rules(board)
        except Exception as exc:                          # noqa: BLE001
            return jsonify({"ok": False, "reason": f"Altium did not answer: {exc}"})
        rules = (got or {}).get("rules")
        if not isinstance(rules, list):
            return jsonify({"ok": False, "reason": "the rules could not be read"})
        if body.get("fingerprint") and body["fingerprint"] != drc_rules.fingerprint(rules):
            return jsonify({"ok": False, "reason": "the rules changed since you read them; "
                            "read them again before approving"})
        done = drc_rules.write_baseline(board, rules, note=str(body.get("note") or "approved on the dashboard"))
        return jsonify(dict(rules_report(board, got), approved_now=done))

    @app.route("/api/wb/engine/preview")
    def wb_engine_preview() -> Response:
        return jsonify({"ok": True, "kind": _ENGINE.run.get("kind"),
                        "state": _ENGINE.run.get("state"),
                        "preview": _ENGINE.run.get("preview")})
