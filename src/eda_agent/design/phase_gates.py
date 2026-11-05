# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The stages a board passes on its way to production, and the checks that
close each one.

    feasibility  Can it be made, for the money, from parts on sale?
    prototype    Is there a board, and does it power up?
    evt          Does the design meet its electrical specification?
    dvt          Does it meet all of it: environment, safety, supply?
    pvt          Can the factory build it the same way every time?

Clearing pvt is production; there is nothing after it to check.

Each check states the question, why it is asked at this stage rather than
later, what counts as done, and which tools of this server answer it.
This module never calls those tools. The client calls them and writes the
outcome to the product's ledger, so a stage can be reviewed with Altium
closed and every answer shows how old it is.

An answer stops counting when it is older than its check allows, or when
it was given for another revision of the board, and the review says
which. A waiver without a reason is refused.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

STAGES = ("feasibility", "prototype", "evt", "dvt", "pvt")
DONE = "production"
TITLES = {
    "feasibility": "Feasibility",
    "prototype": "Prototype build",
    "evt": "Engineering validation (EVT)",
    "dvt": "Design validation (DVT)",
    "pvt": "Production validation (PVT)",
    DONE: "Production",
}
_OTHER_SPELLINGS = {
    "feasability": "feasibility", "concept": "feasibility",
    "proto": "prototype", "prototype build": "prototype",
    "engineering validation": "evt", "engineering validation test": "evt",
    "design validation": "dvt", "design validation test": "dvt",
    "production validation": "pvt", "production validation test": "pvt",
}

MET, UNMET, WAIVED, OPEN = "met", "unmet", "waived", "open"
OUTCOMES = (MET, UNMET, WAIVED, OPEN)


def stage_from(text: str) -> str:
    """A stage from the ways people write it ("EVT", "design validation")."""
    key = " ".join(re.split(r"[-_\s]+", (text or "").strip().lower()))
    key = _OTHER_SPELLINGS.get(key, key)
    if key not in STAGES:
        raise ValueError(f"no stage {text!r}; the stages are {', '.join(STAGES)}")
    return key


@dataclass(frozen=True)
class Check:
    key: str                      # "<stage>:<name>"
    asks: str
    reason: str                   # why at this stage and not a later one
    done_when: str
    tools: tuple[str, ...] = ()   # in the order to call them
    required: bool = True         # False: reported, never holds the stage
    good_for_days: Optional[int] = 30   # None: a fact that does not age

    @property
    def stage(self) -> str:
        return self.key.split(":", 1)[0]


def _checks(stage: str, *rows: tuple) -> list[Check]:
    """Rows of (name, asks, reason, done_when[, tools[, options]])."""
    out = []
    for name, asks, reason, done_when, *rest in rows:
        tools = tuple(rest[0]) if rest else ()
        options = rest[1] if len(rest) > 1 else {}
        out.append(Check(f"{stage}:{name}", asks, reason, done_when, tools, **options))
    return out


_ALL: list[Check] = [
    *_checks(
        "feasibility",
        ("spec-closed", "Is the requirement written, with no open questions?",
         "Supply range and temperature limits feed every later number.",
         "design_validate_requirement passes and lists nothing open",
         ["design_validate_requirement"], {"good_for_days": None}),
        ("key-parts-buyable", "Are the critical parts in stock and in production?",
         "An unbuyable part found now changes the plan; after EVT it is a new board.",
         "each critical part shows stock and is neither EOL nor NRND",
         ["part_search", "part_fetch"]),
        ("lead-times", "Which parts have long lead times, and what is the plan?",
         "The build date follows the slowest part, not the cheapest.",
         "parts above the lead-time limit are listed with their lead time and a plan",
         ["part_fetch"]),
        ("cost-target", "Does the rough BOM cost meet the target at volume?",
         "Volume cost picks the layer count and the packages.",
         "the costed BOM is inside target, or the gap has an agreed plan"),
        ("stack-up", "Is the stack-up chosen, and can it reach every impedance?",
         "An impedance found unreachable during routing means laying out again.",
         "each impedance class has a width and gap the fab can make",
         ["design_list_stackups", "pcb_calc_trace_width_for_impedance",
          "pcb_calc_impedance"]),
        ("power-budget", "Does the power budget close with margin?",
         "Rails, connectors, copper and cooling are all sized from it.",
         "the worst-case load, dissipation included, fits every rail with margin"),
        ("outline-agreed", "Are outline, heights and connector positions agreed?",
         "Late outlines cause most mechanical respins.",
         "mechanical signed off outline, keep-outs, heights and connector places"),
        ("approvals-known", "Which safety and EMC standards apply?",
         "Creepage, isolation and EMC margin are layout rules that cannot be "
         "added to a finished board.",
         "the standards, insulation class and pollution degree are named"),
    ),
    *_checks(
        "prototype",
        ("erc", "Does the schematic compile with ERC clean or each item waived?",
         "Bring-up time is wasted on what the compiler already reported.",
         "no error-level violation without a waiver",
         ["proj_force_recompile", "proj_run_erc", "proj_get_erc_violations"]),
        ("footprints", "Does every part have a footprint that matches its datasheet?",
         "A footprint checked on paper costs minutes; checked at assembly, a build.",
         "every part has a footprint and each one audited matches",
         ["lib_audit_footprint_vs_datasheet", "lib_check_pcblib"]),
        ("drc", "Is the board fully routed and DRC clean?",
         "With an open net or a short there is no board yet.",
         "nothing unrouted and no DRC violation without a waiver",
         ["pcb_run_drc", "pcb_get_unrouted_nets"]),
        ("fab-data", "Are the fab and assembly files made and read back?",
         "Files can generate with a layer missing; reading them back finds it "
         "before the fab does.",
         "the package generates and its layers, outline and drills agree with the board",
         ["proj_generate_fab_package", "pcb_get_layer_stackup", "pcb_get_drill_pairs"]),
        ("orderable-bom", "Can every BOM line be ordered?",
         "Otherwise purchasing finds out.",
         "each fitted line has a part number a distributor stocks",
         ["design_generate_bom", "part_search"]),
        ("fits-enclosure", "Does the 3D board fit the enclosure?",
         "The first check against the real board; part height fails most often.",
         "the STEP sits in the enclosure without interference",
         ["proj_export_step", "pcb_get_components"]),
        ("probe-points", "Can every rail, reset, clock and strap be probed?",
         "A rail with no probe point makes a measurement a rework job.",
         "each has a test point or a reachable pad"),
        ("bring-up-plan", "Is there a bring-up plan with power-on order and limits?",
         "First boards without a sequence and a current limit tend not to survive.",
         "the plan gives the order, the expected currents and when to stop"),
    ),
    *_checks(
        "evt",
        ("prototype-still-clear", "Does the prototype stage still clear on this revision?",
         "Answers for an older revision describe a board that is gone.",
         "the prototype stage clears for this revision", ["design_phase_gate"]),
        ("net-classes", "Is every net in a class, and every class ruled?",
         "Nets outside a class route to defaults nobody chose, and DRC stays quiet.",
         "no net without a class; each class has width, clearance and via rules",
         ["pcb_get_net_classes", "design_synthesize_rules"]),
        ("impedance", "Do controlled-impedance nets follow a profile?",
         "The stack-up promising an impedance and the routing meeting it are "
         "separate facts.",
         "every impedance class maps to a profile within tolerance",
         ["pcb_get_impedance_profiles", "pcb_calc_trace_width_for_impedance"]),
        ("pairs", "Are differential pairs matched, with their gap kept?",
         "A pair that splits passes DRC and closes the eye.",
         "skew inside and between pairs is within budget",
         ["pcb_get_differential_pairs", "pcb_get_diff_pair_rules",
          "pcb_get_trace_lengths", "pcb_calc_length_match"]),
        ("decoupling", "Is the datasheet decoupling fitted and close to its pins?",
         "Capacitors drawn but placed far away pass ERC and DRC alike.",
         "each supply pin has its decoupling within the agreed distance",
         ["audit_find_missing_decoupling", "design_find_loops"]),
        ("return-paths", "Do fast nets keep an unbroken reference plane?",
         "A clock over a split fails radiated emissions in the chamber, at "
         "chamber rates.",
         "no fast net crosses a split or a void",
         ["design_find_loops", "pcb_layout_audit"]),
        ("heat", "Does every hot part have its copper and vias?",
         "Copper and vias are layout; adding them at DVT moves parts.",
         "each dissipating part has the area and via count its junction needs",
         ["pcb_calc_thermal_vias", "pcb_get_fab_stats"]),
        ("tests-traced", "Has each EVT test run, traced to a requirement?",
         "Untraced tests prove nothing and untested requirements stay untested.",
         "every functional requirement has a passing test or a recorded deviation"),
        ("issues-owned", "Are the prototype's issues closed or owned?",
         "Issues carried forward without an owner ship.",
         "each is closed, waived with a reason, or owned with a target stage"),
    ),
    *_checks(
        "dvt",
        ("evt-still-clear", "Does EVT still clear on this revision?",
         "Changes at DVT are often made in a hurry.",
         "the EVT stage clears for this revision", ["design_phase_gate"]),
        ("dfm", "Is DFM clean for the fab that will build it?",
         "Minimum track, ring and drill differ per fab.",
         "nothing is outside the chosen fab's capability at the quoted class",
         ["design_load_fab_profile", "pcb_get_fab_stats", "pcb_get_drill_pairs"]),
        ("spacing", "Do creepage and clearance meet the working voltage?",
         "A safety limit, and often read from the wrong table column.",
         "measured spacing meets the standard for the voltage, insulation and "
         "pollution degree",
         ["pcb_get_clearance_violations", "pcb_layout_audit"]),
        ("second-source", "Do critical parts have a second source or an accepted risk?",
         "Single sourcing should be decided, not inherited.",
         "each critical line has a qualified alternate or a signed acceptance",
         ["design_generate_bom", "part_search"]),
        ("lifecycle", "Is every part still in production?",
         "Lifecycles change while validation runs; the feasibility check is old.",
         "no fitted line is EOL or NRND", ["design_generate_bom", "part_fetch"]),
        ("silkscreen", "Is the silkscreen readable, with polarity and pin-1 marks?",
         "Text under a part is unreadable, and a missing pin-1 mark reverses parts.",
         "no silk on pads; polarity or pin 1 shown on every polarised part",
         ["pcb_layout_audit", "audit_find_designator_collisions"]),
        ("environment", "Have the environmental tests run over the full range?",
         "Heat, humidity, vibration and ESD find what a room-temperature bench "
         "does not.",
         "the plan has run over the specified range and the results are recorded"),
        ("emc-prescan", "Did the EMC pre-scan pass with margin?",
         "It finds emitters while the fix is still a layout change.",
         "radiated and conducted scans keep the agreed margin"),
        ("panel", "Is the panel agreed with the assembler?",
         "Rails, tooling holes and break-off fix the board edge.",
         "the assembler accepted the panel drawing and break-off method"),
    ),
    *_checks(
        "pvt",
        ("dvt-still-clear", "Does DVT still clear, with the design frozen?",
         "PVT measures the process; a design still moving makes yield meaningless.",
         "the DVT stage clears and the revision is under change control",
         ["design_phase_gate"]),
        ("assembly-files", "Are placement, paste and assembly drawing complete?",
         "The line loads them directly; a missing paste layer stops a run.",
         "pick and place, paste and drawing generate and agree with the board",
         ["proj_generate_fab_package", "pcb_export_coordinates"]),
        ("fiducials", "Are there global fiducials, and local ones at fine pitch?",
         "Fine-pitch placement depends on them and the panel leaves no room later.",
         "three global fiducials not in a line; local pairs at fine-pitch parts",
         ["pcb_get_components", "pcb_layout_audit"]),
        ("test-coverage", "Is test coverage measured, with access for the method?",
         "Coverage decides how many faults reach customers.",
         "nodal coverage meets target with reachable probe points",
         ["pcb_layout_audit", "pcb_get_pad_properties"]),
        ("released-parts", "Is every part released in the component library?",
         "A board of unreleased items cannot be rebuilt the same way.",
         "every component is a released item in an approved state",
         ["altium365_list_components", "altium365_list_projects"]),
        ("released-docs", "Is the documentation released at one revision?",
         "What is outside revision control is not part of the build.",
         "schematic, fab, assembly, BOM and test documents share one released revision",
         ["proj_generate_fab_package", "altium365_get_project"]),
        ("pilot-yield", "Does the pilot run meet its yield, with failures explained?",
         "Yield without causes is only a number.",
         "yield at or above target; each failure has a cause and an action"),
        ("process-proven", "Are reflow, AOI and test programs validated?",
         "A process that works for one operator is not yet a process.",
         "reflow profile, AOI and test programs are recorded and repeatable"),
    ),
]
CHECKS: dict[str, Check] = {c.key: c for c in _ALL}


def checks_of(stage: str) -> list[Check]:
    return [c for c in _ALL if c.stage == stage]


@dataclass(frozen=True)
class Answer:
    key: str
    outcome: str
    when: datetime
    source: str = ""      # the tool, or the person, that answered
    detail: str = ""      # the evidence: a count, a figure, a document
    revision: str = ""


@dataclass
class Line:
    check: Check
    answer: Optional[Answer] = None
    ignored_because: str = ""     # empty when the answer counts

    @property
    def outcome(self) -> str:
        if self.answer is None or self.ignored_because:
            return OPEN
        return self.answer.outcome

    @property
    def holding(self) -> bool:
        return self.check.required and self.outcome in (UNMET, OPEN)


@dataclass
class StageReview:
    stage: str
    revision: str
    lines: list[Line] = field(default_factory=list)

    @property
    def cleared(self) -> bool:
        return not self.holding()

    def holding(self) -> list[Line]:
        return [line for line in self.lines if line.holding]

    def tally(self) -> dict[str, int]:
        counts = {o: 0 for o in OUTCOMES}
        for line in self.lines:
            counts[line.outcome] += 1
        return counts

    def tools_to_run(self) -> list[str]:
        """Tools answering the checks that hold the stage, first use first,
        each named by the advertised tool that runs it."""
        from ..tools.folds import call_form

        seen: dict[str, None] = {}
        for line in self.holding():
            seen.update(dict.fromkeys(call_form(t) for t in line.check.tools))
        return list(seen)

    def for_people(self) -> list[str]:
        """Holding checks no tool answers."""
        return [line.check.key for line in self.holding() if not line.check.tools]


def review(stage: str, answers: Iterable[Answer], revision: str = "",
           today: Optional[datetime] = None) -> StageReview:
    """One stage scored against the latest answer to each of its checks."""
    today = today or datetime.now()
    latest: dict[str, Answer] = {}
    for answer in sorted(answers, key=lambda a: a.when):
        latest[answer.key] = answer
    result = StageReview(stage, revision)
    for check in checks_of(stage):
        answer = latest.get(check.key)
        why = ""
        if answer is not None:
            if check.good_for_days is not None and \
                    today - answer.when > timedelta(days=check.good_for_days):
                why = (f"answered {answer.when.date()}, more than "
                       f"{check.good_for_days} days ago; check again")
            elif revision and answer.revision and answer.revision != revision:
                why = f"answered for revision {answer.revision!r}; check again on this one"
        result.lines.append(Line(check, answer, why))
    return result


def standing(answers: Iterable[Answer], revision: str = "",
             today: Optional[datetime] = None) -> tuple[str, list[StageReview]]:
    """The stage a product is in: the first one that does not clear."""
    given = list(answers)
    reviews = [review(s, given, revision, today) for s in STAGES]
    for r in reviews:
        if not r.cleared:
            return r.stage, reviews
    return DONE, reviews


class Ledger:
    """A product's answers, one JSON line each, only ever appended.

    A later answer replaces an earlier one in a review but both stay in the
    file, so a superseded waiver can still be read; a line torn by a crash
    loses only itself.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def add(self, key: str, outcome: str, source: str = "", detail: str = "",
            revision: str = "", when: Optional[datetime] = None) -> Answer:
        if key not in CHECKS:
            raise ValueError(f"no check {key!r}; design_phase_gate lists a stage's checks")
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome is one of {', '.join(OUTCOMES)}")
        if outcome == WAIVED and not detail.strip():
            raise ValueError("a waiver needs a detail: why the check does not apply, "
                             "or who accepted the risk")
        answer = Answer(key, outcome, when or datetime.now(), source, detail, revision)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {"key": answer.key, "outcome": answer.outcome,
               "when": answer.when.isoformat(), "source": answer.source,
               "detail": answer.detail, "revision": answer.revision}
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        return answer

    def answers(self) -> list[Answer]:
        if not self.path.is_file():
            return []
        found = []
        for text in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(text)
                found.append(Answer(row["key"], row["outcome"],
                                    datetime.fromisoformat(row["when"]),
                                    row.get("source", ""), row.get("detail", ""),
                                    row.get("revision", "")))
            except (ValueError, KeyError, TypeError):
                continue
        return found


def ledger_name(product: str) -> str:
    """A product name as a safe file name ("../x" becomes "x")."""
    name = re.sub(r"[^A-Za-z0-9._-]", "", product or "")
    name = re.sub(r"\.{2,}", ".", name).strip("._-")
    return f"{name or 'default'}.jsonl"
