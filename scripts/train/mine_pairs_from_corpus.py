#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Turn human-drawn schematics into pairwise preferences for the scorer.

The Bradley-Terry trainer (``train_quality_model.py``) fits scorer weights
from pairs of layouts and a preference. Its intended source is a person
voting between two engine variants, which is the right signal and a scarce
one. A human-drawn sheet is a preference already expressed: for the same
plan, the human's canvas beats every candidate the engine generated for it.

Each corpus sheet therefore yields one pair per engine candidate, with the
human canvas as the winner. Two things keep that from being a degenerate
corpus:

  - the human is put on side A or B by a seeded coin, so the trainer's
    one-sided-votes check (which exists to catch position bias in a vote
    UI) is satisfied honestly rather than bypassed with --force;
  - every candidate is used, not just the engine's winner, so the model
    sees the spread of what the engine can produce and learns which of it
    lies closest to the human, rather than a single contrast per sheet.

Features come from ``quality.raw_features`` on ``score_canvas`` output, the
same function the scorer applies the fitted weights through, so the model
is never trained on one feature definition and applied to another.

What this cannot teach: anything the features do not measure. The point of
adding the human-law features (alignment, upright shunt parts, long wires,
row banding) was exactly that the six original ones could not express what
separates the two sides.

Usage:
    python scripts/train/mine_pairs_from_corpus.py CORPUS_DIR OUT.jsonl
        [--shard I --of N] [--budget SECONDS] [--max-parts 40]
        [--max-nets 25] [--max-candidates 24]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from eda_agent.design import pipeline as pipe  # noqa: E402
from eda_agent.design.canvas import (  # noqa: E402
    NetLabel, PowerPort, SchematicCanvas, Sheet as CanvasSheet, SymbolInstance,
    WireSegment,
)
from eda_agent.design.human_benchmark import (  # noqa: E402
    human_canvas_from_sheet, plan_from_sheet,
)
from eda_agent.design.kicad_sheet_reader import (  # noqa: E402
    read_sheet, symbols_from_sheet, wire_nets,
)
from eda_agent.design.plan import DesignPlan  # noqa: E402
from eda_agent.design.quality import raw_features, score_canvas  # noqa: E402
from eda_agent.design.symbols import SymbolExtractor  # noqa: E402


class _SheetExtractor(SymbolExtractor):
    """Serve the symbols read off the sheet itself."""

    def __init__(self, models):
        self._m = models

    def extract_one(self, lib_path, lib_ref):
        return self._m.get(lib_ref) or self._m.get((lib_path, lib_ref))

    def extract_many(self, refs):
        out = {}
        for lp, lr in refs:
            m = self.extract_one(lp, lr)
            if m is not None:
                out[(lp, lr)] = m
        return out


def human_canvas(sheet, plan, symbols):
    cv, _ = human_canvas_from_sheet(sheet, plan, symbols)
    return cv


def engine_candidates(plan, extractor, max_candidates, rng):
    """Every canvas best-of built for this plan, sampled if there are many.

    ``build_best_canvas_from_plan`` returns only the winner; the candidates
    are captured by wrapping ``build_canvas_from_plan`` for the duration of
    the call. Restored in a ``finally`` so a failure cannot leave the
    module patched.
    """
    seen = []
    real = pipe.build_canvas_from_plan

    def spy(*a, **k):
        res = real(*a, **k)
        if res.canvas.instances:
            seen.append(res)
        return res

    pipe.build_canvas_from_plan = spy
    try:
        winner = pipe.build_best_canvas_from_plan(plan, extractor)
    finally:
        pipe.build_canvas_from_plan = real
    if not winner.canvas.instances:
        return []
    # The winner always goes in; the rest are a seeded sample so a sheet
    # with 112 candidates does not outvote one with 8.
    others = [r for r in seen if r is not winner]
    rng.shuffle(others)
    return [winner] + others[:max(0, max_candidates - 1)]


def pairs_for_sheet(sp, args, rng, ts):
    txt = sp.read_text(encoding="utf-8", errors="replace")
    sheet = read_sheet(txt, str(sp))
    payload = plan_from_sheet(sheet)
    if not payload:
        return [], ts
    plan = DesignPlan.model_validate(payload)
    if not (2 <= len(plan.parts) <= args.max_parts):
        return [], ts
    if len(plan.nets) > args.max_nets:
        return [], ts
    symbols = symbols_from_sheet(txt)
    hcv = human_canvas(sheet, plan, symbols)
    if hcv is None:
        return [], ts
    human_feat = raw_features(score_canvas(hcv, plan))
    cands = engine_candidates(plan, _SheetExtractor(symbols),
                              args.max_candidates, rng)
    plan_hash = hashlib.sha1(sp.name.encode("utf-8")).hexdigest()[:16]
    rows = []
    for res in cands:
        eng_feat = raw_features(score_canvas(res.canvas, plan))
        ts += 5.0                      # spaced past the fast-vote check
        human_is_a = rng.random() < 0.5
        rows.append({
            "pair_id": f"{plan_hash}-{len(rows)}",
            "plan_hash": plan_hash,
            "source": "human_corpus",
            "sheet": sp.name,
            "ts": ts,
            "features_a": human_feat if human_is_a else eng_feat,
            "features_b": eng_feat if human_is_a else human_feat,
            "winner": "a" if human_is_a else "b",
            "engine_ok": bool(res.ok),
        })
    return rows, ts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("corpus", type=pathlib.Path)
    ap.add_argument("out", type=pathlib.Path)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    ap.add_argument("--budget", type=float, default=1e9,
                    help="seconds of engine build time before stopping")
    ap.add_argument("--max-parts", type=int, default=40)
    ap.add_argument("--max-nets", type=int, default=25)
    ap.add_argument("--max-candidates", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20260904)
    args = ap.parse_args(argv)

    rng = random.Random(args.seed + args.shard)
    ts = 1_700_000_000.0 + args.shard * 1e6
    spent = 0.0
    n_sheets = n_pairs = 0
    files = sorted(args.corpus.glob("*.kicad_sch"))
    with args.out.open("a", encoding="utf-8") as out:
        for idx, sp in enumerate(files):
            if idx % args.of != args.shard or spent > args.budget:
                continue
            t0 = time.time()
            try:
                rows, ts = pairs_for_sheet(sp, args, rng, ts)
            except Exception:
                continue
            spent += time.time() - t0
            if not rows:
                continue
            n_sheets += 1
            n_pairs += len(rows)
            for r in rows:
                out.write(json.dumps(r) + "\n")
            out.flush()
    print(f"shard {args.shard}/{args.of}: {n_sheets} sheets, {n_pairs} pairs, "
          f"{spent:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
