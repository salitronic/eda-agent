# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Run the layout benchmark over a directory of board fixtures.

    python -m eda_agent.layout.bench_run --fixtures DIR --mode route \\
        --jobs 8 --out results.json

Each fixture is a finished board saved as LayoutBoard JSON (``.json`` or
``.json.gz``). Per board, ``bench`` scores the person's board, takes away
what the engine is meant to produce, lets the engine redo it and scores the
result with the same exact DRC:

* ``route``: strip the routing, route on the person's placement.
* ``place``: strip the routing, scramble the movable parts, place them.
* ``place_route``: place as above, then route the engine's placement.

Every board runs in a process of its own, ``--jobs`` at a time, and one
that runs past ``--timeout`` is stopped and recorded as such. The output
holds the code version (``git describe``, whether the tree was dirty, a
hash of this package's sources at the start and at the end, so a run that
straddled an edit says so), every option, one record per board and the
summary printed at the end.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..atomicfile import replace_with_retry
from ..noconsole import no_window
from .bench import (copy_board, detour_stats, hpwl, overlaps, routing_stats, score,
                    scramble_placement, strip_routing, via_clusters)
from .model import LayoutBoard

MODES = ("route", "place", "place_route")
#: What a fixture file is called.
FIXTURE_SUFFIXES = (".json", ".json.gz")
#: How often the runner looks at its workers, seconds.
POLL = 0.2
#: Seconds between writes of the results while boards are running, and how
#: long the last write keeps trying a locked file (see ``_write``).
WRITE_EVERY = 30.0
FINAL_WAIT = 300.0


@dataclass
class BenchOptions:
    mode: str = "route"
    # Routing: planes as the router's own default; stages None for the
    # engine's defaults, False for the router before the stages, or a dict
    # of StageOptions fields.
    planes: bool = True
    stages: Any = None
    # Placement.
    strategy: str | None = None       # None: the placer's DEFAULT_STRATEGY
    fallback: float | None = None     # None: floorplan.FALLBACK_SHARE
    decap_pull: float = 0.0
    spread_density: float = 0.0
    grid_mm: float | None = None
    seed: int = 0                     # the scramble
    # Score only boards the person finished: complete, no violations.
    finished_only: bool = False


def resolved_options(opts: BenchOptions) -> dict:
    """The options with every engine default spelled out, for the record."""
    from .place.floorplan import FALLBACK_SHARE
    from .place.placer import DEFAULT_STRATEGY
    from .route.stages import StageOptions

    out = asdict(opts)
    if opts.mode != "place":
        out["stages_resolved"] = asdict(StageOptions.coerce(opts.stages))
    if opts.mode != "route":
        out["strategy_resolved"] = opts.strategy or DEFAULT_STRATEGY
        out["fallback_resolved"] = FALLBACK_SHARE if opts.fallback is None else opts.fallback
    return out


# ---------------------------------------------------------------------------
# One board
# ---------------------------------------------------------------------------

def finished(human: dict) -> bool:
    """The person's board is one to measure against: every connection
    made, nothing violated, and something to connect."""
    return (human["missing_connections"] == 0 and human["violations"] == 0
            and human["connections_needed"] > 0)


def _ratio(a, b) -> float | None:
    return round(a / b, 3) if a is not None and b else None


def _routed(board: LayoutBoard) -> dict:
    """The routing figures of a board: the exact DRC's verdict and the
    copper laid on its signal layers."""
    from .route.exact import sharp_bends

    s = score(board)
    signal = set(board.signal_layers())
    return {"completion": round(s.completion, 4),
            "fully_routed": s.missing_connections == 0,
            "missing_connections": s.missing_connections,
            "connections_needed": s.connections_needed,
            "violations": s.violations,
            "vias": s.vias, "length": s.length,
            "segments": routing_stats(board)["segments"],
            "sharp_bends": len(sharp_bends([t for t in board.tracks if t.layer in signal],
                                           board.pads, board.vias)),
            "detour": detour_stats(board),
            "via_clusters": via_clusters(board)}


def run_board(board: LayoutBoard, opts: BenchOptions) -> dict:
    """One board through the benchmark; the record ``summarize`` reads."""
    t_start = time.perf_counter()
    rec: dict[str, Any] = {"ok": True, "pads": len(board.pads),
                           "components": len(board.components),
                           "layers": len(board.copper_layers())}
    rec["human"] = _routed(board)
    if opts.finished_only and not finished(rec["human"]):
        return {"ok": False, "skipped": "the person's board is not finished",
                "human": rec["human"]}
    work = strip_routing(board)
    if opts.mode != "route":
        work = _place(work, opts, rec)
    if opts.mode != "place":
        _route(work, opts, rec)
    rec["seconds"] = round(time.perf_counter() - t_start, 2)
    return rec


def _place(stripped: LayoutBoard, opts: BenchOptions, rec: dict) -> LayoutBoard:
    from ..units import MILS_PER_MM
    from .drc import run_drc
    from .place.placer import DEFAULT_STRATEGY, Placer

    rec["human"]["hpwl"] = round(hpwl(stripped), 1)
    rec["human"]["overlaps"] = len(overlaps(stripped))
    scrambled = scramble_placement(stripped, seed=opts.seed)
    t0 = time.perf_counter()
    placer = Placer(copy_board(scrambled), decap_pull=opts.decap_pull,
                    spread_density=opts.spread_density,
                    strategy=opts.strategy or DEFAULT_STRATEGY,
                    grid=opts.grid_mm * MILS_PER_MM if opts.grid_mm else None,
                    fallback=opts.fallback)
    rep = placer.run()
    placed = placer.apply()
    seconds = time.perf_counter() - t0
    moved = {p.ref for p in placer.parts if not p.fixed}
    pairs = overlaps(placed)
    rec["place"] = {
        "strategy": rep.get("strategy", placer.strategy),
        "fallback": bool(rep.get("fallback")),
        "movable": len(moved),
        "failed": len(rep.get("failed") or []),
        "body_overlaps": len(rep.get("body_overlaps") or []),
        "flagged_moves": len(rep.get("flagged_moves") or []),
        "hpwl": round(hpwl(placed), 1),
        # Every overlapping pair, and those with a part the engine moved:
        # two fixed parts can overlap on the person's board already.
        "overlaps": len(pairs),
        "overlaps_moved": sum(1 for pair in pairs if moved & set(pair)),
        # Clearance violations of the placed board before any routing.
        "violations": len(run_drc(placed).violations),
        "seconds": round(seconds, 2),
    }
    rec["hpwl_vs_human"] = _ratio(rec["place"]["hpwl"], rec["human"]["hpwl"])
    return placed


def _route(unrouted: LayoutBoard, opts: BenchOptions, rec: dict) -> None:
    from .route.router import route_adaptive

    t0 = time.perf_counter()
    r = route_adaptive(copy_board(unrouted), planes=opts.planes, stages=opts.stages)
    routed = r.apply()
    seconds = time.perf_counter() - t0
    rec["engine"] = {**_routed(routed), "seconds": round(seconds, 2)}
    rec["router"] = {"iterations": r.report.iterations,
                     "released_planes": len(r.released),
                     "stage_fallback": bool(r.stage_report.fallback)}
    rec["vias_vs_human"] = _ratio(rec["engine"]["vias"], rec["human"]["vias"])
    rec["length_vs_human"] = _ratio(rec["engine"]["length"], rec["human"]["length"])


# ---------------------------------------------------------------------------
# Many boards
# ---------------------------------------------------------------------------

def fixture_paths(fixtures: Path, boards: list[str] | None = None,
                  limit: int | None = None) -> list[Path]:
    """The fixtures to run, by file name; ``boards`` narrows them to those
    names (a name that matches no file is an error, not a silent skip)."""
    found = {p.name: p for p in sorted(fixtures.iterdir())
             if p.is_file() and p.name.endswith(FIXTURE_SUFFIXES)}
    if boards is not None:
        missing = [b for b in boards if b not in found]
        if missing:
            raise SystemExit(f"{len(missing)} listed boards are not in {fixtures}, "
                             f"for example {missing[0]}")
        names = sorted(set(boards))
    else:
        names = sorted(found)
    if limit:
        names = names[:limit]
    return [found[n] for n in names]


def read_board_list(path: Path) -> list[str]:
    """Fixture names from a file: one per line (``#`` starts a comment),
    a JSON list, or a JSON object whose keys are the names (an earlier
    result file, or this tool's output, whose names are under "boards")."""
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError:
        return [ln.split("#", 1)[0].strip() for ln in text.splitlines()
                if ln.split("#", 1)[0].strip()]
    if isinstance(data, dict) and isinstance(data.get("boards"), dict):
        data = data["boards"]
    if isinstance(data, dict):
        return list(data)
    if isinstance(data, list):
        return [str(x) for x in data]
    raise SystemExit(f"{path}: not a list of boards")


def load_fixture(path: str | Path) -> tuple[LayoutBoard, int]:
    """A fixture, with the read's corrections that need no live board
    applied (a fixture keeps the read as it was made, and a reader fixed
    since then would read it differently): the board, and how many
    objects were corrected."""
    from .read_altium import repair_pour_nets

    board = LayoutBoard.load(path)
    return board, repair_pour_nets(board)


def run_path(path: str | Path, opts: BenchOptions) -> dict:
    """``run_board`` on a fixture file; a failure is the board's record."""
    try:
        board, repaired = load_fixture(path)
        rec = run_board(board, opts)
        if repaired:
            rec["read_repairs"] = repaired
        return rec
    except Exception as exc:  # noqa: BLE001 - recorded against the board
        import traceback
        where = traceback.extract_tb(exc.__traceback__)[-1]
        return {"ok": False, "error": f"{type(exc).__name__}: {exc} "
                                      f"({Path(where.filename).name}:{where.lineno})"}


def _worker(path: str, opts: BenchOptions, conn) -> None:
    conn.send(run_path(path, opts))
    conn.close()


def run_many(paths: list[Path], opts: BenchOptions, jobs: int = 1,
             timeout: float = 0.0,
             done: Callable[[str, dict], None] | None = None) -> dict[str, dict]:
    """Every board, ``jobs`` processes at a time, largest file first so the
    long boards do not start last. ``jobs`` 0 runs them one after another
    in this process, with no time cap."""
    done = done or (lambda name, rec: None)
    results: dict[str, dict] = {}
    if jobs <= 0:
        for p in paths:
            t0 = time.perf_counter()
            rec = run_path(p, opts)
            rec["wall"] = round(time.perf_counter() - t0, 2)
            results[p.name] = rec
            done(p.name, rec)
        return results
    ctx = mp.get_context("spawn")
    pending = sorted(paths, key=lambda p: -p.stat().st_size)
    running: dict[str, tuple] = {}
    while pending or running:
        while pending and len(running) < jobs:
            p = pending.pop(0)
            recv, send = ctx.Pipe(duplex=False)
            proc = ctx.Process(target=_worker, args=(str(p), opts, send), daemon=True)
            proc.start()
            send.close()
            running[p.name] = (proc, recv, time.monotonic())
        for name, (proc, recv, t0) in list(running.items()):
            wall = time.monotonic() - t0
            if recv.poll():
                try:
                    rec = recv.recv()
                except EOFError:
                    rec = {"ok": False, "error": f"worker exited ({proc.exitcode})"}
                proc.join(10)
            elif not proc.is_alive():
                rec = {"ok": False, "error": f"worker exited ({proc.exitcode})"}
            elif timeout and wall > timeout:
                proc.terminate()
                proc.join(10)
                rec = {"ok": False, "timeout": True, "error": f"over the {timeout:g} s cap"}
            else:
                continue
            recv.close()
            rec["wall"] = round(wall, 2)
            del running[name]
            results[name] = rec
            done(name, rec)
        if running:
            time.sleep(POLL)
    return results


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": round(statistics.fmean(values), 4),
            "median": round(statistics.median(values), 4),
            "min": round(min(values), 4), "max": round(max(values), 4)}


def _quality(routed: list[dict]) -> dict:
    """Detours and via clusters, the engine's and the person's, summed over
    the boards whose records carry them (results written before they were
    measured do not)."""
    out: dict[str, Any] = {}
    for who in ("engine", "human"):
        det = [r[who]["detour"] for r in routed
               if r[who].get("detour", {}).get("connections")]
        clu = [r[who]["via_clusters"] for r in routed if "via_clusters" in r[who]]
        tag = "" if who == "engine" else "human_"
        if det:
            out[tag + "detour"] = {
                "boards": len(det),
                "connections": sum(d["connections"] for d in det),
                "over_1_5": sum(d["over_1_5"] for d in det),
                "over_2": sum(d["over_2"] for d in det),
                # Per board, then the median over the boards.
                "median": _stats([d["median"] for d in det]).get("median"),
                "p95": _stats([d["p95"] for d in det]).get("median"),
                "pieces_median": _stats([d["pieces_median"] for d in det]).get("median"),
            }
        if clu:
            out[tag + "via_clusters"] = {"clusters": sum(c["clusters"] for c in clu),
                                         "vias": sum(c["vias"] for c in clu),
                                         "largest": max(c["largest"] for c in clu)}
    return out


def summarize(records: dict[str, dict]) -> dict:
    """Aggregates over the boards that ran to the end. Completion means
    are over those boards; timeouts and errors are counted apart."""
    recs = list(records.values())
    ok = [r for r in recs if r.get("ok")]
    out: dict[str, Any] = {
        "boards": len(recs), "scored": len(ok),
        "skipped": sum(1 for r in recs if r.get("skipped")),
        "timeouts": sum(1 for r in recs if r.get("timeout")),
        "errors": sum(1 for r in recs if not r.get("ok") and not r.get("skipped")
                      and not r.get("timeout")),
        "human_fully_routed": sum(1 for r in ok if r["human"]["fully_routed"]),
    }
    routed = [r for r in ok if "engine" in r]
    if routed:
        eng = [r["engine"] for r in routed]
        out["routing"] = {
            "completion": _stats([e["completion"] for e in eng]),
            "fully_routed": sum(1 for e in eng if e["fully_routed"]),
            "drc_clean": sum(1 for e in eng if e["violations"] == 0),
            "violations": sum(e["violations"] for e in eng),
            "vias": sum(e["vias"] for e in eng),
            "human_vias": sum(r["human"]["vias"] for r in routed),
            "length": round(sum(e["length"] for e in eng), 1),
            "human_length": round(sum(r["human"]["length"] for r in routed), 1),
            "vias_vs_human": _stats([r["vias_vs_human"] for r in routed
                                     if r.get("vias_vs_human") is not None]),
            "length_vs_human": _stats([r["length_vs_human"] for r in routed
                                       if r.get("length_vs_human") is not None]),
            "sharp_bends": sum(e["sharp_bends"] for e in eng),
            "segments": sum(e["segments"] for e in eng),
            "human_segments": sum(r["human"]["segments"] for r in routed),
            "stage_fallbacks": sum(1 for r in routed if r["router"]["stage_fallback"]),
            "seconds": _stats([e["seconds"] for e in eng]),
        }
        out["routing"].update(_quality(routed))
    placed = [r for r in ok if "place" in r]
    if placed:
        pl = [r["place"] for r in placed]
        out["placement"] = {
            "overlap_free": sum(1 for p in pl if p["overlaps"] == 0),
            "no_overlap_with_a_moved_part": sum(1 for p in pl if p["overlaps_moved"] == 0),
            "human_overlap_free": sum(1 for r in placed if r["human"]["overlaps"] == 0),
            "violations_free": sum(1 for p in pl if p["violations"] == 0),
            "parts_failed": sum(p["failed"] for p in pl),
            "body_overlaps": sum(p["body_overlaps"] for p in pl),
            "fallbacks": sum(1 for p in pl if p["fallback"]),
            "strategies": {s: sum(1 for p in pl if p["strategy"] == s)
                           for s in sorted({p["strategy"] for p in pl})},
            "hpwl_vs_human": _stats([r["hpwl_vs_human"] for r in placed
                                     if r.get("hpwl_vs_human") is not None]),
            "seconds": _stats([p["seconds"] for p in pl]),
        }
    walls = [r["wall"] for r in recs if "wall" in r]
    out["board_seconds"] = round(sum(walls), 1)
    return out


def format_summary(s: dict) -> str:
    lines = [f"boards {s['boards']}: scored {s['scored']}, timeouts {s['timeouts']}, "
             f"errors {s['errors']}, skipped {s['skipped']}"]
    r = s.get("routing")
    if r:
        c = r["completion"]
        lines.append(
            f"routing: mean completion {c['mean']:.2%}, median {c['median']:.2%}; "
            f"fully routed {r['fully_routed']}/{c['n']} (person {s['human_fully_routed']}); "
            f"DRC-clean {r['drc_clean']}/{c['n']}")
        v, ln = r["vias_vs_human"], r["length_vs_human"]
        lines.append(
            f"         vias/person median {v.get('median', math.nan):.3f}, "
            f"length/person median {ln.get('median', math.nan):.3f}; "
            f"total vias {r['vias']} (person {r['human_vias']}), "
            f"length {r['length'] / 1000:.0f} in (person {r['human_length'] / 1000:.0f} in); "
            f"sharp bends {r['sharp_bends']}")
        segs = (f"track pieces {r['segments']} (person {r['human_segments']}"
                + (f", {r['segments'] / r['human_segments']:.2f}x)" if r["human_segments"] else ")"))
        d, hd = r.get("detour"), r.get("human_detour")
        if d:
            def det(x):
                return (f"median {x['median']:.3f}, p95 {x['p95']:.2f}, over 1.5x "
                        f"{x['over_1_5']}/{x['connections']}, over 2x {x['over_2']}, "
                        f"pieces {x['pieces_median']:g}")
            segs += f"; detour {det(d)}" + (f" (person {det(hd)})" if hd else "")
        lines.append("         " + segs)
        c, hc = r.get("via_clusters"), r.get("human_via_clusters")
        if c:
            lines.append(f"         via clusters {c['clusters']} ({c['vias']} vias)"
                         + (f", person {hc['clusters']} ({hc['vias']} vias)" if hc else ""))
        t = r["seconds"]
        lines.append(f"         route time median {t['median']:.1f} s, max {t['max']:.0f} s, "
                     f"stage fallbacks {r['stage_fallbacks']}")
    p = s.get("placement")
    if p:
        h = p["hpwl_vs_human"]
        lines.append(
            f"placement: overlap-free {p['overlap_free']} (person {p['human_overlap_free']}), "
            f"no overlap with a moved part {p['no_overlap_with_a_moved_part']}; "
            f"HPWL/person median {h.get('median', math.nan):.3f}; "
            f"fallbacks {p['fallbacks']}; strategies {p['strategies']}")
        t = p["seconds"]
        lines.append(f"           place time median {t['median']:.1f} s, max {t['max']:.0f} s")
    lines.append(f"board time {s['board_seconds']:.0f} s")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Code version
# ---------------------------------------------------------------------------

def source_hash() -> str:
    """A hash of this package's Python sources: equal hashes, same engine."""
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def code_version() -> dict:
    """``git describe``, the commit, and the source files changed from it,
    when this package runs from a checkout. Read-only git only."""
    root = Path(__file__).resolve().parents[3]
    out: dict[str, Any] = {"source_hash": source_hash()}
    if not (root / "pyproject.toml").is_file():
        return out

    def git(*args) -> str | None:
        try:
            return subprocess.run(["git", "--no-optional-locks", *args], cwd=root,
                                  capture_output=True, text=True, timeout=60,
                                  check=True, **no_window()).stdout
        except (OSError, subprocess.SubprocessError):
            return None

    out["describe"] = (git("describe", "--always", "--dirty", "--tags") or "").strip() or None
    out["commit"] = (git("rev-parse", "HEAD") or "").strip() or None
    status = git("status", "--porcelain", "--", "src")
    if status is not None:
        out["src_changes"] = [ln[3:] for ln in status.splitlines() if ln.strip()]
        out["dirty"] = bool(out["src_changes"]) or (out["describe"] or "").endswith("-dirty")
    return out


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def _value(text: str):
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("none", "inf"):
        return None if low == "none" else math.inf
    try:
        return float(text) if any(c in text for c in ".e") else int(text)
    except ValueError:
        return text


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="python -m eda_agent.layout.bench_run",
        description="Benchmark the layout engine on a directory of finished boards "
                    "saved as LayoutBoard JSON.")
    ap.add_argument("--fixtures", type=Path, required=True,
                    help="directory of board fixtures (*.json, *.json.gz)")
    ap.add_argument("--boards", type=Path,
                    help="run only these fixtures: a file with one name per line, a "
                         "JSON list, or a JSON object keyed by name (an earlier result)")
    ap.add_argument("--limit", type=int, help="only the first N fixtures by name")
    ap.add_argument("--finished", action="store_true",
                    help="score only boards the person finished (complete, no violations)")
    ap.add_argument("--mode", choices=MODES, default="route")
    ap.add_argument("--legacy-router", action="store_true",
                    help="route with the router before the planned stages")
    ap.add_argument("--stage", action="append", default=[], metavar="NAME=VALUE",
                    help="a routing stage option (StageOptions field), repeatable")
    ap.add_argument("--no-planes", action="store_true",
                    help="do not use inner layers covered by a pour as planes")
    ap.add_argument("--strategy", choices=("blocks", "analytic"),
                    help="placement strategy (default: the placer's default)")
    ap.add_argument("--fallback", type=float,
                    help="share of flagged parts past which blocks falls back to "
                         "analytic (0: never)")
    ap.add_argument("--decap-pull", type=float, default=0.0)
    ap.add_argument("--spread-density", type=float, default=0.0,
                    help="spread over this share of the area (0: the whole board)")
    ap.add_argument("--grid-mm", type=float, help="blocks lattice, mm")
    ap.add_argument("--seed", type=int, default=0, help="scramble seed")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2),
                    help="boards at a time, each in its own process (0: in this "
                         "process, no time cap)")
    ap.add_argument("--timeout", type=float, default=1800.0,
                    help="seconds a board may take before it is stopped (0: no cap)")
    ap.add_argument("--out", type=Path, help="write the results here (JSON)")
    ap.add_argument("--label", default="", help="a name for this run, kept in the output")
    ap.add_argument("--quiet", action="store_true", help="no line per board")
    args = ap.parse_args(argv)
    if args.legacy_router and args.stage:
        ap.error("--legacy-router and --stage do not mix")
    return args


def options_from(args: argparse.Namespace) -> BenchOptions:
    stages: Any = None
    if args.legacy_router:
        stages = False
    elif args.stage:
        stages = {}
        for item in args.stage:
            name, sep, text = item.partition("=")
            if not sep:
                raise SystemExit(f"--stage takes NAME=VALUE, got {item!r}")
            stages[name.strip()] = _value(text.strip())
    return BenchOptions(mode=args.mode, planes=not args.no_planes, stages=stages,
                        strategy=args.strategy, fallback=args.fallback,
                        decap_pull=args.decap_pull, spread_density=args.spread_density,
                        grid_mm=args.grid_mm, seed=args.seed, finished_only=args.finished)


def _write(path: Path, data: dict, wait: float = 0.0) -> bool:
    """Write the results; False when the file stayed locked for ``wait``
    seconds.

    In a synced folder the sync client opens the file each time it
    changes, and a rename onto it fails while it does: one such failure
    stopped a run of 160 boards an hour in. A write while boards are still
    running may fail, since the next one carries the same results; the last
    write keeps trying."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    deadline = time.monotonic() + wait
    while True:
        try:
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            replace_with_retry(tmp, path)
            return True
        except OSError as exc:
            if time.monotonic() >= deadline:
                print(f"could not write {path}: {exc}", file=sys.stderr, flush=True)
                return False
            time.sleep(2.0)


def _line(i: int, n: int, name: str, rec: dict) -> str:
    head = f"[{i}/{n}] {name}"
    if not rec.get("ok"):
        return f"{head}: {rec.get('skipped') or rec.get('error')} ({rec.get('wall', 0):.0f} s)"
    parts = []
    if "place" in rec:
        parts.append(f"hpwl/person {rec['hpwl_vs_human']} overlaps {rec['place']['overlaps']}")
    if "engine" in rec:
        e = rec["engine"]
        parts.append(f"routed {e['completion']:.2%} violations {e['violations']}")
    return f"{head}: {', '.join(parts)} ({rec.get('wall', 0):.0f} s)"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    opts = options_from(args)
    boards = read_board_list(args.boards) if args.boards else None
    paths = fixture_paths(args.fixtures, boards, args.limit)
    if not paths:
        raise SystemExit(f"no fixtures in {args.fixtures}")
    if args.jobs > 1:
        # One numeric thread per worker: the workers are the parallelism.
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ.setdefault(var, "1")
    import numpy
    import scipy

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    run: dict[str, Any] = {
        "tool": "eda_agent.layout.bench_run",
        "label": args.label,
        "mode": opts.mode,
        "started": started.isoformat(timespec="seconds"),
        "code": code_version(),
        "options": resolved_options(opts),
        "run": {"fixtures": len(paths), "jobs": args.jobs, "timeout": args.timeout,
                "boards_file": args.boards.name if args.boards else None,
                "limit": args.limit},
        "environment": {"python": platform.python_version(), "numpy": numpy.__version__,
                        "scipy": scipy.__version__, "platform": platform.platform(),
                        "cpus": os.cpu_count(),
                        "threads": {v: os.environ.get(v) for v in
                                    ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                                     "MKL_NUM_THREADS")}},
        "boards": {},
    }
    count = [0]
    last_write = [0.0]

    def done(name: str, rec: dict) -> None:
        count[0] += 1
        run["boards"][name] = rec
        if not args.quiet:
            print(_line(count[0], len(paths), name, rec), flush=True)
        # The results so far, so a run that dies keeps them; not after every
        # board, which keeps a sync client busy with the file.
        if args.out and time.monotonic() - last_write[0] > WRITE_EVERY:
            _write(args.out, run)
            last_write[0] = time.monotonic()

    results = run_many(paths, opts, jobs=args.jobs, timeout=args.timeout, done=done)
    run["boards"] = dict(sorted(results.items()))
    run["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run["wall_seconds"] = round(time.perf_counter() - t0, 1)
    end_hash = source_hash()
    run["code"]["source_hash_end"] = end_hash
    run["code"]["changed_during_run"] = end_hash != run["code"]["source_hash"]
    run["summary"] = summarize(run["boards"])
    if args.out and not _write(args.out, run, FINAL_WAIT):
        stamp = datetime.now().strftime("%H%M%S")
        other = args.out.with_name(f"{args.out.stem}.{stamp}{args.out.suffix}")
        if _write(other, run, FINAL_WAIT):
            print(f"written to {other} instead")
    print(format_summary(run["summary"]))
    print(f"wall {run['wall_seconds']:.0f} s with {args.jobs} jobs"
          + ("; THE SOURCES CHANGED DURING THE RUN" if run["code"]["changed_during_run"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
