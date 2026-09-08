#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Aggregate placement edits into a relative-anchor priors file.

Reads the JSONL log produced by ``design_learn_from_layout`` and
collapses each (part_role, anchor_role) pair into a single
(dx, dy, rotation_delta, n_samples) record using medians + mode. The
output JSON is the ML-free "model" the pipeline biases toward.

Usage:
    python scripts/train/build_placement_priors.py
    python scripts/train/build_placement_priors.py --in custom_edits.jsonl --out priors.json

Defaults:
- ``--in``  = ``$EDA_AGENT_PLACEMENT_LOG`` if set, else
              ``%USERPROFILE%/.eda-agent/placement_edits.jsonl``
- ``--out`` = ``<repo>/src/eda_agent/design/placement_priors.json``
- ``--min-samples`` = 2 (skip pairs with fewer observations to reduce noise)

The output is committed back into the package so the priors ship with
the next release. End users get them automatically via
``pip install eda-agent``.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


REPO = Path(__file__).resolve().parents[2]
#: The token _key uses when a role is missing. Such a bucket is counted
#: and reported, never emitted: apply_placement_priors skips a part whose
#: role is empty, so nothing can ever look one up.
UNKNOWN = "_unknown_"

DEFAULT_OUT = REPO / "src" / "eda_agent" / "design" / "placement_priors.json"


def _default_input_path() -> Path:
    override = os.environ.get("EDA_AGENT_PLACEMENT_LOG")
    if override:
        return Path(override)
    userprofile = os.environ.get("USERPROFILE")
    base = Path(userprofile) if userprofile else Path.home()
    return base / ".eda-agent" / "placement_edits.jsonl"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Skip corrupt lines silently; the user's session
            # shouldn't fail because one row got truncated.
            continue
    return rows


def _key(part_role: str, anchor_role: str) -> str:
    """Stable (part_role|anchor_role) join used as the JSON key.

    Empty roles get a special "_unknown_" token so we can still aggregate
    edits made on parts the planner didn't label -- they won't help the
    role-based priors but they ARE useful for diagnostics.
    """
    p = part_role or UNKNOWN
    a = anchor_role or UNKNOWN
    return f"{p}|{a}"


def _mode_or_zero(values: list[int]) -> int:
    """Pick the most frequent value; ties go to the smaller magnitude.

    Rotation deltas are categorical (the user picked 0, 90, -90, 180,
    etc.), so a median makes no sense. The mode reflects the dominant
    preference; ties prefer 0 because "leave it alone" is the safest
    default.
    """
    if not values:
        return 0
    counts = Counter(values).most_common()
    top_count = counts[0][1]
    top_values = sorted(v for v, c in counts if c == top_count)
    return min(top_values, key=lambda v: (abs(v), v))


def aggregate(rows: list[dict[str, Any]], min_samples: int) -> dict[str, Any]:
    """Build the priors dict from raw edit rows.

    Returns:
        {"version", "trained_at", "n_edits", "n_pairs", "priors": {
            "<part_role>|<anchor_role>": {
                "dx": int, "dy": int, "rotation": int, "n_samples": int,
                "part_role": str, "anchor_role": str,
            }
        }}
    """
    by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _key(row.get("part_role", ""), row.get("anchor_role", ""))
        by_pair[key].append(row)

    priors: dict[str, dict[str, Any]] = {}
    unkeyed = 0
    for key, pair_rows in by_pair.items():
        if len(pair_rows) < min_samples:
            continue
        # A BUCKET THAT CAN NEVER MATCH MUST NOT REACH THE ARTIFACT.
        # apply_placement_priors skips any part whose role is empty, so a
        # prior keyed on '_unknown_' is unreachable by construction. The
        # docstring on _key calls these useful for diagnostics, and they
        # were being written into the shipped file anyway, where they
        # inflate n_pairs and describe a preference nothing can apply.
        # MEASURED: 27 edits produced exactly one prior, '_unknown_|
        # _unknown_', and every stage reported success.
        if UNKNOWN in key.split("|"):
            unkeyed += len(pair_rows)
            continue
        dxs = [int(r.get("dx_mils", 0)) for r in pair_rows]
        dys = [int(r.get("dy_mils", 0)) for r in pair_rows]
        rots = [int(r.get("rot_delta_deg", 0)) for r in pair_rows]
        priors[key] = {
            "dx": int(statistics.median(dxs)),
            "dy": int(statistics.median(dys)),
            "rotation": _mode_or_zero(rots),
            "n_samples": len(pair_rows),
            "part_role": pair_rows[0].get("part_role", ""),
            "anchor_role": pair_rows[0].get("anchor_role", ""),
        }

    return {
        "version": 1,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_edits": len(rows),
        "n_pairs": len(priors),
        "min_samples": min_samples,
        # Reported rather than hidden: an aggregator that quietly drops
        # most of its input looks identical to one with little input.
        "edits_unkeyed": unkeyed,
        "priors": priors,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--in", dest="input_path", type=Path, default=_default_input_path(),
        help="Path to placement_edits.jsonl (default: user-profile log)",
    )
    parser.add_argument(
        "--out", dest="output_path", type=Path, default=DEFAULT_OUT,
        help=f"Where to write placement_priors.json (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--min-samples", type=int, default=2,
        help="Skip (part_role, anchor_role) pairs with fewer observations "
             "(default: 2)",
    )
    args = parser.parse_args(argv)

    rows = _load_rows(args.input_path)
    if not rows:
        print(
            f"No edits found at {args.input_path}. Run "
            "design_learn_from_layout after editing a schematic to "
            "accumulate data.",
            file=sys.stderr,
        )
        return 1

    payload = aggregate(rows, min_samples=args.min_samples)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"Wrote {args.output_path} with {payload['n_pairs']} role-pair priors "
        f"from {payload['n_edits']} edits."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
