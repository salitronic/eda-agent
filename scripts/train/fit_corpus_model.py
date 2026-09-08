#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Fit the scorer on corpus pairs, choosing the regulariser by hold-out.

Ten features, several of them collinear (wire length, long wires and
crossings all grow with sprawl), fitted on pairs that share a sheet: a weak
L2 gives weights whose signs flip between runs and mean nothing. So the fit
is run at several L2 strengths and the one with the best HELD-OUT accuracy
is kept, on designs the model never saw. Training accuracy is printed but
is not the number to trust.

    python scripts/train/fit_corpus_model.py PAIRS.jsonl OUT.json
        [--holdout 0.2] [--l2 0.01 0.1 1.0 3.0] [--epochs 2000]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "train_quality_model", HERE / "train_quality_model.py")
_t = importlib.util.module_from_spec(_spec)
sys.modules["train_quality_model"] = _t
_spec.loader.exec_module(_t)


def engine_constant_features(rows) -> frozenset:
    """Features with (near) zero variance on the ENGINE side of the pairs.

    The loser of every corpus pair is an engine candidate. A feature that
    never varies across those cannot rank them and is frozen at 0; free, it
    would fit whatever the human side happens to have.
    """
    import statistics

    frozen = set()
    for name in _t._FEATURE_NAMES:
        vals = []
        for r in rows:
            eng = r["features_b"] if r.get("winner") == "a" else r["features_a"]
            vals.append(float(eng.get(name, 0.0)))
        if len(vals) < 2 or statistics.pstdev(vals) < 1e-9:
            frozen.add(name)
    return frozenset(frozen)


# Features that describe HOW connectivity is drawn rather than how neat the
# drawing is. A human sheet reaches an IC's rail pins with one glyph and a
# short bus, or with labels; the engine puts a glyph on every rail pin. The
# count separates the two sides perfectly and says nothing about tidiness:
# fitted free on 1069 clean pairs it took -14.5 per standard deviation and
# would have made best-of chase the fewest ports, which on the one sheet
# rendered meant a rail trunk with four junctions and three times the
# crossings. Frozen by default; --freeze overrides the list.
_REPRESENTATION_FEATURES = ("port_count",)


# The hand-tuned scorer's weights, as BADNESS per raw unit. They are the
# prior the fit is pulled toward: the corpus can strengthen or weaken a term
# it has evidence about and leaves the rest where the hand put them.
def hand_prior_raw() -> dict[str, float]:
    from eda_agent.design import quality as Q

    return {
        "wire_crossings": Q._W_CROSSINGS,
        "wires_through_bodies": Q._W_THROUGH_BODY,
        "body_overlaps": Q._W_OVERLAP,
        "aspect_ratio_penalty": Q._W_ASPECT,
        "total_wire_length": Q._W_LENGTH,
        "port_count": Q._W_PORTS,
        "alignment_penalty": Q._W_ALIGNMENT,
        "shunt_on_side": 0.0,
        "long_wires": 0.0,
        "row_bands_per_part": 0.0,
    }


def fit(rows, holdout: float, l2: float, epochs: int, seed: int,
        freeze_extra=()):
    train_rows, held_rows = _t._split_by_design(rows, holdout, seed)
    pairs, means, stds = _t._normalise_features(train_rows)
    frozen = engine_constant_features(train_rows) | frozenset(freeze_extra)
    # Bradley-Terry scores GOODNESS, so a badness weight enters negated, and
    # per standard deviation: raw weight times the feature's std.
    hand = hand_prior_raw()
    prior = [-hand[n] * stds[i] for i, n in enumerate(_t._FEATURE_NAMES)]
    w, history = _t._train(pairs, epochs=epochs, lr=0.05, l2=l2, frozen=frozen,
                           prior=prior, all_nonpositive=True)
    train_m = _t._eval_accuracy(pairs, w)
    held_m = (_t._eval_accuracy(_t._apply_normalisation(held_rows, means, stds), w)
              if held_rows else None)
    return w, means, stds, train_m, held_m, len(train_rows), len(held_rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pairs", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--l2", type=float, nargs="+",
                    default=[0.01, 0.1, 0.3, 1.0, 3.0])
    ap.add_argument("--epochs", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260904)
    ap.add_argument("--freeze", nargs="*", default=list(_REPRESENTATION_FEATURES),
                    help="features held at weight 0 besides the engine-constant "
                         "ones (default: the representation features)")
    args = ap.parse_args(argv)

    rows = _t._load_rows(args.pairs)
    problems = _t._corpus_problems(rows)
    designs = len({r.get("plan_hash") for r in rows})
    print(f"{len(rows)} pairs over {designs} designs")
    frozen = engine_constant_features(rows) | frozenset(args.freeze)
    if frozen:
        print(f"frozen at 0 (constant on every engine candidate): "
              f"{', '.join(sorted(frozen))}")
    for p in problems:
        print(f"  corpus problem: {p}")
    if problems:
        return 1

    best = None
    print(f"\n{'l2':>6} {'train acc':>10} {'held-out acc':>13} {'held pairs':>11}")
    for l2 in args.l2:
        w, means, stds, tm, hm, n_tr, n_he = fit(
            rows, args.holdout, l2, args.epochs, args.seed, args.freeze)
        held_acc = hm["accuracy"] if hm else float("nan")
        print(f"{l2:>6g} {tm['accuracy']:>10.1%} {held_acc:>13.1%} {n_he:>11}")
        if best is None or held_acc > best[0]:
            best = (held_acc, l2, w, means, stds, tm, hm)

    held_acc, l2, w, means, stds, tm, hm = best
    names = list(_t._FEATURE_NAMES)
    raw_w = [w[i] / stds[i] for i in range(len(names))]
    intercept = -sum(raw_w[i] * means[i] for i in range(len(names)))
    payload = {
        "version": 1,
        "source": "human corpus pairs",
        "n_pairs": len(rows),
        "n_designs": designs,
        "holdout": args.holdout,
        "l2": l2,
        "frozen_features": sorted(frozen),
        "representation_features": sorted(args.freeze),
        "prior": "hand-tuned quality weights, all weights constrained <= 0",
        "nonpositive_features": sorted(_t._NONPOSITIVE),
        "features": names,
        "weights_normalised": {n: float(w[i]) for i, n in enumerate(names)},
        "weights_raw": {n: float(raw_w[i]) for i, n in enumerate(names)},
        "intercept_raw": float(intercept),
        "feature_means": {n: float(means[i]) for i, n in enumerate(names)},
        "feature_stds": {n: float(stds[i]) for i, n in enumerate(names)},
        "training_metrics": tm,
        "holdout_metrics": hm,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nkept l2={l2:g}: held-out {held_acc:.1%}")
    print("weights per stddev (positive = more of it looks human):")
    for n in names:
        print(f"    {n:24s} {payload['weights_normalised'][n]:+.3f}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
