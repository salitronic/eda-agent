---
name: autodesign
description: Drive an autonomous spec-to-board PCB design with the eda-agent MCP server (Altium Designer or EasyEDA Pro). Apply when the user asks to design a board/schematic from a requirement, wants an end-to-end autonomous design run, or mentions the design harness, design_next_action, design sessions, or spec-to-board. Requires the eda-agent MCP server connected to a running Altium Designer or EasyEDA Pro; the KiCad backend does not register the design harness.
---

# Autonomous PCB design (eda-agent)

Drive a full spec-to-board design by looping the server-side state machine.
The server owns sequencing, gates, and durable state, so you never memorize
the workflow: you call `design_next_action`, do what it says, log the
result, and repeat. A weaker model produces a plainer board, never a broken
pipeline, because the integrity lives server-side.

## Before you start

- Confirm the editor is actually answering, with `app_ping` on Altium or
  `easyeda_ping` on EasyEDA. Ping, not `app_get_status`: status reports
  that the process exists and that something once called attach, and
  neither of those proves the bridge replies. If it does not answer, tell
  the user how to start it; don't guess.
- Read `design_get_discipline` once: the hard rules and the DesignPlan
  schema. Or call `design_autonomy_guide` for the full protocol + the 13
  stages with their tools and exit gates.

## The loop

1. `design_session_start(requirement)` opens the durable journal. Keep the
   returned `session_id`; every later call takes it. Then `design_live_view`
   so the user can watch the board change, and record each decision with
   its reason before acting on it (`design_live_note`).
2. If a project is open or will be modified, checkpoint first so the whole
   run is revertible in one step: `app_checkpoint("before autonomous run")`
   on Altium, `easyeda_checkpoint` on EasyEDA.
3. Loop: `design_next_action(session_id)` and act on `status`:
   - **proceed / retry**: do the stage using its `suggested_tools` until the
     `exit_gate` is met, then
     `design_session_log(event="stage_result", stage=<stage>, status="ok")`.
     Where the gate names numbers (placement, routing, pours, verification),
     measure them with `pcb_layout_audit` and pass the result as `data=`:
     the harness checks them and sends the stage back if one fails.
     If you cannot finish without the user, log `status="blocked"` with a
     question and stop.
   - **blocked**: put `open_question` to the user; when answered,
     `design_session_log(event="resolved", text=<answer>)` and continue.
   - **complete**: the 13 stages are done; write the design report with
     `design_session_report` and review outputs with the user.
4. Checkpoint again before each high-risk mutating stage: `sch_to_pcb`,
   `routing`, `pours_tuning`.
5. Long engine runs (routing a dense board) can exceed the tool timeout;
   start them with `design_job_start` and poll `design_job_status` /
   `design_job_result`.

Bounded retries: a stage that fails 3 times escalates to a human question
automatically. Don't loop past it; surface it.

## How the board is laid out

The discipline's "Board layout method" is the standard: floorplan before
placement (fixed skeleton, critical corridors, sections, one rectangle per
sub-block in signal-flow order), each block laid out to a pattern, the
legaliser's moves read rather than accepted, routing in stages (pairs,
plane fan-out with dog-bones, buses as a whole, then the rest), and one
small checked step at a time. A scattered placement means the method is
wrong; no parameter fixes it.

## Resuming

A run survives context loss. In a fresh session, call
`design_session_resume(session_id)` (or `design_next_action`) and pick up
from recorded state: the journal, not the chat history, is the source of
truth.

## Hard constraints (non-negotiable)

- **Datasheet-first**: every device fact fetched and cited from the
  manufacturer datasheet; never fabricated. Use WebSearch/WebFetch.
- **NDA isolation**: never mine or reference other client designs.
- **No third-party routing engines or account-gated APIs** in the design
  loop: the in-house router is the only routing engine.
- **Verify render-and-look**, not by score alone; the visual rubric is the
  shipping bar.
- **No unverifiable safety tables**: ship only sourced/verified values.

## Discovering tools

The surface is several hundred tools. Use `tool_catalog(category=, maturity=,
interaction=, query=)` to find the right one without loading every schema;
`interaction="modal"` and `"partial"` flag tools that need a human or leave
work incomplete. Run a discovered tool by name with `tool_invoke`.
