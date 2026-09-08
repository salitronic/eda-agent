# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pairwise preference capture for layout-quality learning.

Workflow:

1. ``present_pair(plan, extractor)`` builds two layout variants of the
   same plan, renders both as SVGs, and returns a ``pair_id`` plus the
   two SVG paths and their feature vectors.
2. The user (or an agent) looks at both SVGs and picks the better one.
3. ``record_preference(pair_id, winner)`` appends the choice to
   ``~/.eda-agent/pairwise_preferences.jsonl``.
4. Offline aggregator (``scripts/train/train_quality_model.py``)
   reads the log and fits a Bradley-Terry model whose latent scores
   replace the heuristic in ``design.quality.score_canvas``.

Why pairwise (not absolute 1-10 scoring): absolute scores drift over
time -- a "7" in week 1 isn't a "7" in week 5. Pairwise preferences
are stable: "A is better than B" depends only on the two layouts in
front of you. Bradley-Terry recovers absolute latent scores from
many pairs.

Pair generation: variant A is the default ``build_best_canvas_from_plan``
output (algorithmic best). Variant B is a different aspect-rescaled
attempt deliberately picked to be PLAUSIBLY different (not just a tiny
perturbation), or sampled from the user's manual placement_hints
history.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from eda_agent.design.canvas import SchematicCanvas
from eda_agent.design.pipeline import (
    PipelineResult,
    build_best_canvas_from_plan,
    build_canvas_from_plan,
)
from eda_agent.design.plan import DesignPlan
from eda_agent.design.quality import LayoutScore, score_canvas
from eda_agent.design.render_svg import render_canvas_svg
from eda_agent.design.symbols import SymbolExtractor

logger = logging.getLogger("eda_agent.design.preferences")


@dataclass
class PendingPair:
    """One pair waiting for a user vote. Stored in-memory + on disk."""

    pair_id: str
    plan_hash: str
    canvas_a: dict  # canvas.to_dict()
    canvas_b: dict
    features_a: dict[str, float]
    features_b: dict[str, float]
    svg_a_path: str
    svg_b_path: str
    ts: float


@dataclass
class PairwiseRecord:
    """One completed pairwise preference observation.

    ``winner`` is "a" / "b" / "tie". Ties are kept (some pairs really
    are indistinguishable) -- the Bradley-Terry fit weights them at 0.5.

    ``features_*`` are the per-canvas feature vectors used at training
    time. We store them at vote time (rather than re-deriving from
    canvas) so a later feature-set change doesn't invalidate old votes.
    """

    pair_id: str
    plan_hash: str
    winner: str  # "a" | "b" | "tie"
    features_a: dict[str, float]
    features_b: dict[str, float]
    user_note: str = ""
    ts: float = field(default_factory=time.time)


# Files / paths --------------------------------------------------------


def _pref_log_path() -> Path:
    """JSONL of completed PairwiseRecords."""
    override = os.environ.get("EDA_AGENT_PREFERENCES_LOG")
    if override:
        return Path(override)
    userprofile = os.environ.get("USERPROFILE")
    base = Path(userprofile) if userprofile else Path.home()
    return base / ".eda-agent" / "pairwise_preferences.jsonl"


def _pending_pairs_dir() -> Path:
    """One JSON file per pending PendingPair, keyed by pair_id.

    Lives next to the preferences log. Files are deleted after the
    user records a vote -- no need to keep them once the SVG paths and
    features have been folded into PairwiseRecord.
    """
    override = os.environ.get("EDA_AGENT_PREFERENCES_LOG")
    if override:
        return Path(override).parent / "pending_pairs"
    userprofile = os.environ.get("USERPROFILE")
    base = Path(userprofile) if userprofile else Path.home()
    return base / ".eda-agent" / "pending_pairs"


def _preview_dir() -> Path:
    """Where to dump the side-by-side SVGs the user previews."""
    override = os.environ.get("EDA_AGENT_PREFERENCES_LOG")
    if override:
        return Path(override).parent / "previews"
    userprofile = os.environ.get("USERPROFILE")
    base = Path(userprofile) if userprofile else Path.home()
    return base / ".eda-agent" / "previews"


def _plan_hash(plan: DesignPlan) -> str:
    """Stable short id for the design topology."""
    parts = []
    for p in sorted(plan.parts, key=lambda x: x.refdes):
        parts.append(f"{p.refdes}:{p.lib_ref}")
    nets = []
    for n in sorted(plan.nets, key=lambda x: x.name):
        pin_keys = sorted(f"{pr.refdes}.{pr.pin}" for pr in n.pins)
        nets.append(f"{n.name}:{','.join(pin_keys)}")
    sig = "||".join(parts + nets)
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:12]


def _features_from_score(score: LayoutScore) -> dict[str, float]:
    """The feature dict a pair is logged with: ``quality.raw_features``.

    One definition shared with the scorer and the corpus pair miner, so the
    model is never fitted on one set of features and applied to another.
    """
    from eda_agent.design.quality import raw_features

    return raw_features(score)


# Pair generation + storage -------------------------------------------


def present_pair(
    plan: DesignPlan,
    extractor: SymbolExtractor,
    *,
    plan_json_dict: Optional[dict] = None,
    rng_seed: Optional[int] = None,
) -> dict[str, Any]:
    """Build two distinct layout variants, render SVGs, register a pending pair.

    Returns a dict with the data the user/agent needs to make a choice:
    ``pair_id``, ``svg_a_path``, ``svg_b_path``, plus the features and
    score totals so a heuristic-savvy caller can see what they're
    comparing.

    Variant generation: we sample from a pool of ~20 candidate hint
    profiles (different anchor-bump directions, anchor rotations,
    second-anchor offsets). On each call we pick 2 RANDOM profiles
    and produce their layouts. This guarantees a different pair each
    refresh, which is what the pairwise-preference workflow needs --
    otherwise the user votes on the same two layouts forever.

    ``rng_seed``: pin the RNG for tests; production calls use entropy.
    """
    pair_id = uuid.uuid4().hex[:12]
    plan_hash = _plan_hash(plan)
    rng = random.Random(rng_seed) if rng_seed is not None else random.Random()

    # Build the base layout once so we know the canonical anchor parts.
    # strict_shorts=False so even bad layouts get shown to the user --
    # they vote them down, the model learns to avoid them.
    base_result = build_best_canvas_from_plan(
        plan, extractor, strict_shorts=False,
    )
    if not base_result.ok or not base_result.canvas.instances:
        return {
            "ok": False,
            "pair_id": pair_id,
            "notes": [f.text for f in base_result.failures],
            "error": "base pipeline failed",
        }

    profiles = _variant_profiles(plan, base_result, rng=rng)
    if len(profiles) < 2:
        # Fall back to the original A=best / B=contrast pairing.
        a_result = base_result
        b_result = _generate_contrast_variant(plan, extractor, base_result)
    else:
        # Sampling strategy: instead of always-the-best-vs-near-rival
        # (which made both variants look similar), sample ~20 profiles
        # randomly, score each, then pick a HIGH-scoring one and a
        # LOW-scoring one. Maximises visual difference + training
        # signal: user always sees a clearly better/worse comparison.
        sample_n = min(20, len(profiles))
        sampled = rng.sample(profiles, sample_n)
        candidates = []
        for profile in sampled:
            result = _build_with_profile(plan, extractor, profile, base_result)
            if not result.ok or not result.canvas.instances:
                continue
            s = score_canvas(result.canvas, plan)
            candidates.append((s.total, profile["name"], result))
        if len(candidates) < 2:
            a_result = base_result
            b_result = _generate_contrast_variant(plan, extractor, base_result)
        else:
            candidates.sort(key=lambda c: c[0])
            # Variant A: random pick from BEST third (so the user sees
            # a model-approved layout, but it varies between refreshes
            # for visual diversity).
            best_pool = candidates[: max(1, len(candidates) // 3)]
            # Variant B: random pick from WORST third (clear contrast).
            worst_pool = candidates[-max(1, len(candidates) // 3):]
            _, _, a_result = rng.choice(best_pool)
            _, _, b_result = rng.choice(worst_pool)
            # 50/50 chance of swapping so the user can't rely on "A is
            # always the model's pick" -- avoids position bias.
            if rng.random() < 0.5:
                a_result, b_result = b_result, a_result

    if not a_result.ok or not a_result.canvas.instances:
        return {
            "ok": False, "pair_id": pair_id,
            "notes": [f.text for f in a_result.failures],
            "error": "variant A pipeline failed",
        }
    if not b_result.ok or not b_result.canvas.instances:
        return {
            "ok": False, "pair_id": pair_id,
            "notes": [f.text for f in b_result.failures],
            "error": "variant B pipeline failed",
        }
    a_score = score_canvas(a_result.canvas, plan)
    b_score = score_canvas(b_result.canvas, plan)

    preview_dir = _preview_dir()
    preview_dir.mkdir(parents=True, exist_ok=True)
    svg_a_path = preview_dir / f"{pair_id}_A.svg"
    svg_b_path = preview_dir / f"{pair_id}_B.svg"
    svg_a_path.write_text(render_canvas_svg(a_result.canvas), encoding="utf-8")
    svg_b_path.write_text(render_canvas_svg(b_result.canvas), encoding="utf-8")

    pending = PendingPair(
        pair_id=pair_id,
        plan_hash=plan_hash,
        canvas_a=a_result.canvas.to_dict(),
        canvas_b=b_result.canvas.to_dict(),
        features_a=_features_from_score(a_score),
        features_b=_features_from_score(b_score),
        svg_a_path=str(svg_a_path),
        svg_b_path=str(svg_b_path),
        ts=time.time(),
    )
    _save_pending(pending)

    return {
        "ok": True,
        "pair_id": pair_id,
        "plan_hash": plan_hash,
        "svg_a_path": str(svg_a_path),
        "svg_b_path": str(svg_b_path),
        "score_a": a_score.total,
        "score_b": b_score.total,
        "features_a": pending.features_a,
        "features_b": pending.features_b,
        "instructions": (
            "Look at both SVGs. Call design_record_preference(pair_id, "
            "winner='a' | 'b' | 'tie') with your choice."
        ),
    }


def _variant_profiles(
    plan: DesignPlan,
    base_result: PipelineResult,
    *,
    rng: Optional[random.Random] = None,
) -> list[dict[str, Any]]:
    """Generate ~60 visibly different placement-hint profiles.

    The pool covers:
    - empty (algorithmic baseline)
    - top anchor at 9 grid positions x 4 rotations = 36
    - top anchor + second anchor in random sheet quadrants (15)
    - random per-refdes offsets on all parts (10 fully-randomized layouts)

    The last category is the key for "feels random" perception: each
    profile randomly perturbs every part's position by +/- 1000 mils
    and randomly rotates a third of them. Two refreshes of the same
    plan never look identical.

    ``rng`` lets the caller seed for tests; production uses entropy.
    """
    rng = rng or random.Random()
    instances = sorted(
        base_result.canvas.instances,
        key=lambda i: len(i.symbol.pins),
        reverse=True,
    )
    if not instances:
        return [{"name": "base", "hints": {}}]
    sheet = base_result.canvas.sheets[0] if base_result.canvas.sheets else None
    sw = sheet.width_mils if sheet else 11500
    sh = sheet.height_mils if sheet else 7600
    cx = sw // 2
    cy = sh // 2
    anchor = instances[0]
    second = instances[1] if len(instances) > 1 else None

    profiles: list[dict[str, Any]] = [
        {"name": "base", "hints": {}},
    ]

    # 1. Anchor on a 3x3 grid x 4 rotations = 36 layouts.
    for col_idx, ax_frac in enumerate([0.25, 0.5, 0.75]):
        for row_idx, ay_frac in enumerate([0.25, 0.5, 0.75]):
            for rot in (0, 90, 180, 270):
                profiles.append({
                    "name": f"a-c{col_idx}r{row_idx}-rot{rot}",
                    "hints": {
                        anchor.refdes: {
                            "x": int(sw * ax_frac),
                            "y": int(sh * ay_frac),
                            "rotation": rot,
                        },
                    },
                })

    # 2. Anchor + second-anchor in random sheet quadrants (15 layouts).
    if second is not None:
        for i in range(15):
            ax = rng.choice([sw // 4, sw // 2, 3 * sw // 4])
            ay = rng.choice([sh // 4, sh // 2, 3 * sh // 4])
            bx = rng.choice([sw // 4, sw // 2, 3 * sw // 4])
            by = rng.choice([sh // 4, sh // 2, 3 * sh // 4])
            if (ax, ay) == (bx, by):
                bx = sw - bx
            profiles.append({
                "name": f"pair{i}",
                "hints": {
                    anchor.refdes: {
                        "x": ax, "y": ay,
                        "rotation": rng.choice([0, 90, 180, 270]),
                    },
                    second.refdes: {
                        "x": bx, "y": by,
                        "rotation": rng.choice([0, 90, 180, 270]),
                    },
                },
            })

    # 3. Fully randomized layouts: random hints on every part.
    # These are where the *real* diversity comes from -- every refresh
    # samples fresh random positions for every refdes, so consecutive
    # refreshes produce visibly different scenes.
    for i in range(10):
        hints: dict[str, dict[str, int]] = {}
        for inst in instances:
            hints[inst.refdes] = {
                "x": rng.randint(sw // 8, 7 * sw // 8),
                "y": rng.randint(sh // 8, 7 * sh // 8),
                # Rotate ~1/3 of parts; leave the rest at their default.
                "rotation": rng.choice([0, 0, 0, 90, 180, 270]),
            }
        profiles.append({"name": f"scatter{i}", "hints": hints})

    return profiles


def _build_with_profile(
    plan: DesignPlan,
    extractor: SymbolExtractor,
    profile: dict[str, Any],
    base_result: PipelineResult,
) -> PipelineResult:
    """Build a canvas using the profile's placement hints.

    Uses ``strict_shorts=False`` so layouts with routing shorts are
    shown to the user rather than rejected -- the user's vote against
    them is exactly the signal the learner needs.
    """
    if not profile["hints"]:
        return base_result
    return build_best_canvas_from_plan(
        plan, extractor,
        placement_hints=profile["hints"],
        strict_shorts=False,
    )


def _generate_contrast_variant(
    plan: DesignPlan,
    extractor: SymbolExtractor,
    reference: PipelineResult,
) -> PipelineResult:
    """Build a layout that's PLAUSIBLY different from the reference.

    Picks a few high-pin-count parts (likely anchors) and shifts them
    by 1500-mil offsets, then re-runs the pipeline so the rest of the
    layout rebalances. We pick anchors rather than passives because
    moving an anchor cascades through the layout, producing genuinely
    different topology rather than tiny perturbations.
    """
    ref_instances = sorted(
        reference.canvas.instances,
        key=lambda i: len(i.symbol.pins),
        reverse=True,
    )
    if not ref_instances:
        return reference
    # Take the top anchor (highest pin count) and nudge it diagonally.
    anchor = ref_instances[0]
    # Bump by ~1500 mils in a direction inferred from current position
    # (toward the centre of the sheet so we don't push off-edge).
    sheet = reference.canvas.sheets[0] if reference.canvas.sheets else None
    cx = sheet.width_mils // 2 if sheet else 5000
    cy = sheet.height_mils // 2 if sheet else 4000
    dx = 1500 if anchor.x < cx else -1500
    dy = 1500 if anchor.y < cy else -1500
    hints = {
        anchor.refdes: {
            "x": anchor.x + dx,
            "y": anchor.y + dy,
            "rotation": anchor.rotation,
        }
    }
    return build_best_canvas_from_plan(
        plan, extractor, placement_hints=hints,
    )


def _save_pending(pending: PendingPair) -> None:
    path = _pending_pairs_dir() / f"{pending.pair_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(pending), indent=2), encoding="utf-8")


def _load_pending(pair_id: str) -> Optional[PendingPair]:
    path = _pending_pairs_dir() / f"{pair_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PendingPair(**data)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("could not load pending pair %s: %s", pair_id, exc)
        return None


def record_preference(
    pair_id: str,
    winner: str,
    *,
    user_note: str = "",
    log_path: Optional[Path] = None,
    auto_train_min_pairs: int = 5,
) -> dict[str, Any]:
    """Convert a pending pair + user vote into a permanent JSONL row.

    Args:
        pair_id: Returned by ``present_pair``.
        winner: ``"a"`` / ``"b"`` / ``"tie"``.
        user_note: Optional free-text comment ("A is more compact",
            "B has overlapping caps").
        log_path: Where to append the JSONL row. Defaults to
            ``~/.eda-agent/pairwise_preferences.jsonl`` (overrideable
            via ``EDA_AGENT_PREFERENCES_LOG``).

    Returns dict with ``ok`` + ``log_path`` + ``record``.
    """
    winner = winner.lower().strip()
    if winner not in ("a", "b", "tie"):
        return {
            "ok": False,
            "error": f"winner must be 'a' / 'b' / 'tie', got {winner!r}",
        }
    pending = _load_pending(pair_id)
    if pending is None:
        return {
            "ok": False,
            "error": (
                f"no pending pair {pair_id!r}. Did you call "
                f"present_pair first? Pending files live in {_pending_pairs_dir()}."
            ),
        }
    record = PairwiseRecord(
        pair_id=pair_id,
        plan_hash=pending.plan_hash,
        winner=winner,
        features_a=pending.features_a,
        features_b=pending.features_b,
        user_note=user_note,
    )
    log_path = log_path or _pref_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record)) + "\n")
    # Clean up the pending file -- we don't need it anymore.
    try:
        (_pending_pairs_dir() / f"{pair_id}.json").unlink()
    except FileNotFoundError:
        pass

    # Auto-train after each vote, hot-reload the model. This is what
    # makes every vote tighten the layout the user sees next. The
    # trainer is fast (sub-second on small datasets) and the reload
    # is in-process. No restart needed.
    n_records = _count_records(log_path)
    trained = None
    if n_records >= auto_train_min_pairs:
        trained = _retrain_inline(log_path)
    return {
        "ok": True,
        "log_path": str(log_path),
        "record": asdict(record),
        "n_records": n_records,
        "auto_train": trained,
    }


def _retrain_inline(log_path: Path) -> Optional[dict[str, Any]]:
    """Run the BT trainer in-process and hot-reload the model.

    Returns a small summary dict, or None on failure (logged).
    """
    try:
        # Import lazily so the trainer's heavyweight math doesn't
        # load on every preferences-module import.
        from importlib import util
        repo = Path(__file__).resolve().parents[3]
        trainer_path = repo / "scripts" / "train" / "train_quality_model.py"
        spec = util.spec_from_file_location("train_quality_model", trainer_path)
        if spec is None or spec.loader is None:
            return {"ok": False, "error": "could not import trainer module"}
        module = util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from eda_agent.design.quality import (
            _quality_model_path,
            reset_model_cache,
        )
        rc = module.main([
            "--in", str(log_path),
            "--out", str(_quality_model_path()),
            "--min-pairs", "2",
        ])
        if rc != 0:
            return {"ok": False, "error": f"trainer exit code {rc}"}
        reset_model_cache()
        return {"ok": True, "model_path": str(_quality_model_path())}
    except Exception as exc:
        logger.warning("auto-train failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _count_records(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    return sum(1 for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip())


@dataclass
class TournamentCandidate:
    """One variant in a best-of-N tournament round."""

    candidate_id: str  # short hex
    canvas: dict  # canvas.to_dict()
    features: dict[str, float]
    score: float
    svg_path: str
    is_champion: bool  # True iff this is the previous round's winner
    profile_name: str


def present_tournament(
    plan: DesignPlan,
    extractor: SymbolExtractor,
    *,
    champion_canvas: Optional[dict] = None,
    n: int = 6,
    rng_seed: Optional[int] = None,
) -> dict[str, Any]:
    """Build N variants for the tournament UI.

    First round (``champion_canvas`` is None): N visibly-different
    scratch layouts (anchor-grid + scatter profiles).

    Later rounds: one slot is the persistent champion (the user's
    previous pick); the other N-1 are mutations of the champion
    (small random perturbations of 1-3 part positions / rotations).
    This makes the search incremental and visible -- every click
    moves the champion toward what the user likes.

    Returns:
        ``{"ok": True, "round_id": ..., "candidates": [TournamentCandidate...]}``
        where ``round_id`` ties N-1 pairwise records together when the
        user clicks one variant.
    """
    rng = random.Random(rng_seed) if rng_seed is not None else random.Random()
    round_id = uuid.uuid4().hex[:12]
    plan_hash = _plan_hash(plan)

    base_result = build_best_canvas_from_plan(
        plan, extractor, strict_shorts=False,
    )
    if not base_result.ok or not base_result.canvas.instances:
        return {
            "ok": False, "round_id": round_id,
            "error": "base pipeline failed",
            "notes": [f.text for f in base_result.failures],
        }

    preview_dir = _preview_dir()
    preview_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[TournamentCandidate] = []

    if champion_canvas is not None:
        # Rebuild the champion as a PipelineResult so we can render its
        # SVG. Champion has no hints -- it's exactly the canvas from
        # the previous round's winner.
        champion_inst = _canvas_dict_to_hints(champion_canvas)
        champion_result = build_best_canvas_from_plan(
            plan, extractor,
            placement_hints=champion_inst,
            strict_shorts=False,
        )
        if champion_result.ok and champion_result.canvas.instances:
            cand = _build_candidate(
                champion_result, "champion", round_id,
                preview_dir, plan, is_champion=True,
            )
            candidates.append(cand)
        # Fill the remaining slots with mutations of the champion.
        attempts = 0
        while len(candidates) < n and attempts < n * 4:
            attempts += 1
            hints = _mutate_champion(champion_canvas, rng)
            result = build_best_canvas_from_plan(
                plan, extractor,
                placement_hints=hints, strict_shorts=False,
            )
            if not result.ok or not result.canvas.instances:
                continue
            cand = _build_candidate(
                result, f"mut{len(candidates)}",
                round_id, preview_dir, plan, is_champion=False,
            )
            candidates.append(cand)
    else:
        # First round: N diverse scratch layouts.
        profiles = _variant_profiles(plan, base_result, rng=rng)
        # Always include the base.
        profiles.insert(0, {"name": "base-first", "hints": {}})
        for profile in profiles:
            if len(candidates) >= n:
                break
            result = _build_with_profile(plan, extractor, profile, base_result)
            if not result.ok or not result.canvas.instances:
                continue
            cand = _build_candidate(
                result, profile["name"], round_id,
                preview_dir, plan, is_champion=False,
            )
            candidates.append(cand)

    if len(candidates) < 2:
        return {
            "ok": False, "round_id": round_id,
            "error": "could not produce enough variants",
        }

    # Persist the round so record_tournament can look up the candidates.
    pending = {
        "round_id": round_id,
        "plan_hash": plan_hash,
        "ts": time.time(),
        "candidates": [asdict(c) for c in candidates],
    }
    _save_pending_round(round_id, pending)

    return {
        "ok": True, "round_id": round_id, "plan_hash": plan_hash,
        "candidates": [asdict(c) for c in candidates],
        "n_records": _count_records(_pref_log_path()),
    }


def record_tournament(
    round_id: str,
    winner_id: str,
    *,
    log_path: Optional[Path] = None,
    auto_train_min_pairs: int = 5,
) -> dict[str, Any]:
    """Record N-1 pairwise records (winner vs each loser).

    Returns the winner's canvas dict so the caller can persist it as
    the next round's champion.
    """
    pending = _load_pending_round(round_id)
    if pending is None:
        return {"ok": False, "error": f"no pending round {round_id!r}"}
    cands = pending["candidates"]
    winner = next((c for c in cands if c["candidate_id"] == winner_id), None)
    if winner is None:
        return {"ok": False, "error": f"unknown winner_id {winner_id!r}"}
    losers = [c for c in cands if c["candidate_id"] != winner_id]
    log_path = log_path or _pref_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    with log_path.open("a", encoding="utf-8") as f:
        for loser in losers:
            record = PairwiseRecord(
                pair_id=f"{round_id}_{winner_id}_{loser['candidate_id']}",
                plan_hash=pending["plan_hash"],
                winner="a",
                features_a=winner["features"],
                features_b=loser["features"],
                user_note=f"tournament round {round_id}",
            )
            f.write(json.dumps(asdict(record)) + "\n")
            n_written += 1
    # Clean up.
    try:
        (_pending_pairs_dir() / f"round_{round_id}.json").unlink()
    except FileNotFoundError:
        pass

    n_records = _count_records(log_path)
    trained = None
    if n_records >= auto_train_min_pairs:
        trained = _retrain_inline(log_path)

    return {
        "ok": True,
        "n_records_written": n_written,
        "n_records_total": n_records,
        "winner_canvas": winner["canvas"],
        "auto_train": trained,
    }


def _save_pending_round(round_id: str, payload: dict) -> None:
    path = _pending_pairs_dir() / f"round_{round_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_pending_round(round_id: str) -> Optional[dict]:
    path = _pending_pairs_dir() / f"round_{round_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _build_candidate(
    result: PipelineResult,
    profile_name: str,
    round_id: str,
    preview_dir: Path,
    plan: DesignPlan,
    *,
    is_champion: bool,
) -> TournamentCandidate:
    score = score_canvas(result.canvas, plan)
    candidate_id = uuid.uuid4().hex[:8]
    svg_path = preview_dir / f"{round_id}_{candidate_id}.svg"
    svg_path.write_text(
        render_canvas_svg(result.canvas), encoding="utf-8",
    )
    return TournamentCandidate(
        candidate_id=candidate_id,
        canvas=result.canvas.to_dict(),
        features=_features_from_score(score),
        score=score.total,
        svg_path=str(svg_path),
        is_champion=is_champion,
        profile_name=profile_name,
    )


def _canvas_dict_to_hints(canvas_dict: dict) -> dict[str, dict[str, int]]:
    """Pin every instance in the canvas at its recorded position.

    Used to reconstruct a champion canvas: every refdes gets a
    placement_hint set to its exact previous position.
    """
    return {
        i["refdes"]: {
            "x": int(i["x"]), "y": int(i["y"]),
            "rotation": int(i["rotation"]),
        }
        for i in canvas_dict.get("instances", [])
    }


def _mutate_champion(
    canvas_dict: dict, rng: random.Random,
) -> dict[str, dict[str, int]]:
    """Build a mutation of the champion: champion-with-small-changes.

    Randomly chooses ONE mutation strategy per call:
      - "shift": move 1-2 parts by +/- 600 mils
      - "rotate": rotate 1 part by 90 deg
      - "swap": swap two parts' (x, y) positions
      - "scatter": pick 2-3 parts and place them randomly on the sheet
    Returns full placement_hints so non-mutated parts keep their
    champion positions exactly.
    """
    base = _canvas_dict_to_hints(canvas_dict)
    instances = list(base.keys())
    if not instances:
        return base
    strategy = rng.choice(["shift", "shift", "rotate", "swap", "scatter"])
    if strategy == "shift":
        n_move = rng.randint(1, min(2, len(instances)))
        for refdes in rng.sample(instances, n_move):
            dx = rng.choice([-800, -500, 500, 800])
            dy = rng.choice([-800, -500, 500, 800])
            base[refdes]["x"] += dx
            base[refdes]["y"] += dy
    elif strategy == "rotate":
        refdes = rng.choice(instances)
        base[refdes]["rotation"] = (base[refdes]["rotation"] + 90) % 360
    elif strategy == "swap" and len(instances) >= 2:
        a, b = rng.sample(instances, 2)
        ax, ay = base[a]["x"], base[a]["y"]
        bx, by = base[b]["x"], base[b]["y"]
        base[a]["x"], base[a]["y"] = bx, by
        base[b]["x"], base[b]["y"] = ax, ay
    elif strategy == "scatter":
        n_scatter = rng.randint(2, min(3, len(instances)))
        # Use modest fractions of typical sheet bounds.
        for refdes in rng.sample(instances, n_scatter):
            base[refdes]["x"] = rng.randint(1500, 9500)
            base[refdes]["y"] = rng.randint(1500, 6000)
    return base


def load_preferences(
    log_path: Optional[Path] = None,
) -> list[PairwiseRecord]:
    log_path = log_path or _pref_log_path()
    if not log_path.exists():
        return []
    out: list[PairwiseRecord] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            out.append(PairwiseRecord(**data))
        except (json.JSONDecodeError, TypeError):
            continue
    return out
