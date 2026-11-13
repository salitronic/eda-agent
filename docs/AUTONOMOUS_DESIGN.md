# Autonomous design with eda-agent

How to drive a full spec-to-board run with any MCP client. The workflow is
the same everywhere; only the entry point differs by client.

## The idea

The design harness splits work three ways so client model quality affects
design *quality*, never pipeline *integrity*:

- **You (the LLM client)** make the judgment calls: requirement capture,
  part choice, repair decisions.
- **Deterministic engines** (placement, routing, value math, rule synthesis)
  do the execution.
- **A server-side state machine** owns sequencing, entry/exit gates, bounded
  retries, and durable journaling.

So you don't memorize a 13-stage workflow. You loop: ask the server what's
next, do it, log the result.

## Entry points by client

- **Claude Code**: the `/autodesign` skill loads the protocol when the task
  matches, once copied from `skills/autodesign/` into `.claude/skills/`
  (see [skills/README.md](../skills/README.md)).
- **Prompt-capable clients (e.g. Codex)**: invoke the `autonomous_design`
  MCP prompt, optionally with a `requirement` argument.
- **Any client**: call the `design_autonomy_guide` tool. It returns the
  loop, all 13 stages with their tools and exit gates, and the constraints,
  as structured data. This is the canonical, always-current source (it is
  generated from the same stage playbooks the state machine enforces, so it
  never drifts).

## The loop (all clients)

1. `design_get_discipline`: hard rules + the DesignPlan schema (once).
2. `design_session_start(requirement)` → keep the `session_id`. Open
   `design_live_view` so the board can be watched as it changes, and log
   each decision before acting on it with `design_live_note`.
3. `app_checkpoints(action="make", args={"label": "before autonomous run"})`
   if a project will be modified.
4. Repeat `design_next_action(session_id)`:
   - `proceed` / `retry` → do the stage with its `suggested_tools`, meet the
     `exit_gate`, then `design_session_log(event="stage_result",
     stage=…, status="ok")`. The placement, routing, pours and verification
     gates are measured: pass `pcb_layout_audit`'s result as `data=` and
     the harness sends the stage back if a number fails.
   - `blocked` → ask the user `open_question`, then
     `design_session_log(event="resolved", text=…)`.
   - `complete` → `design_session_report` writes the design report, then
     review outputs.
5. Checkpoint before `sch_to_pcb`, `routing`, `pours_tuning`.
6. Long runs → `design_job_start` + poll `design_job_status`.

## Resuming

Runs are durable. A fresh client session calls
`design_session_resume(session_id)` and continues from recorded state: the
journal is the source of truth, not the conversation.

## Constraints

Datasheet-first (cite the manufacturer PDF), NDA isolation, no third-party
routing engines or account-gated APIs, verify render-and-look, no
unverifiable safety tables. The full text lives in `design_get_discipline`.
