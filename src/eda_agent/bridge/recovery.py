# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guided recovery for DelphiScript engine faults (roadmap 1.2).

When the Altium script engine faults, the bridge can already tell *which*
way it failed (stuck handler vs dead polling loop vs corrupt response) from
the progress-heartbeat file. This module turns that diagnosis into concrete,
consistent recovery steps so an LLM client can relay exactly what the user
must do, instead of every call site inventing its own prose, and the
dashboard can render the same banner.

Pure data; no Altium, no bridge state. One source of truth for the recovery
copy, consumed by the bridge exceptions and the web dashboard.
"""

from __future__ import annotations

# Fault kinds the bridge can distinguish.
STUCK_HANDLER = "stuck_handler"      # heartbeat ticking, handler never returns
DEAD_LOOP = "dead_loop"              # no heartbeat, no response
CORRUPT_RESPONSE = "corrupt_response"  # response file present but unparseable

_STOP_STEP = (
    "In Altium's Script IDE toolbar press the red Stop button (Run > Stop, "
    "or Ctrl+F3; use Ctrl+Pause/Break if the script is truly hung)."
)
_RELAUNCH_STEP = (
    "Relaunch the polling loop: call app_recover_script, which does this "
    "step and the one before it, or run `eda-agent relaunch --restart` in "
    "a terminal, or File > Run Script... > Altium_API > Dispatcher.pas > "
    "StartMCPServer > Run."
)

_GUIDANCE = {
    STUCK_HANDLER: {
        "diagnosis": (
            "Altium is alive (it answered keep-alives) but the command's "
            "handler never returned: likely stuck in a loop."
        ),
        "steps": [_STOP_STEP, _RELAUNCH_STEP],
    },
    DEAD_LOOP: {
        "diagnosis": (
            "No response and no progress heartbeat: the polling loop is "
            "probably not running (never started, or halted on an earlier "
            "engine fault)."
        ),
        "steps": [
            "If an Altium error dialog is open, note its text and dismiss it.",
            _STOP_STEP,
            _RELAUNCH_STEP,
        ],
    },
    CORRUPT_RESPONSE: {
        "diagnosis": (
            "The response file was present but unparseable: Altium likely "
            "crashed mid-write."
        ),
        "steps": [
            "Retry the call once (the corrupt file was removed automatically).",
            "If it recurs: " + _STOP_STEP,
            _RELAUNCH_STEP,
        ],
    },
}

# Where a human can read the full story.
_DOCS_HINT = "See README 'Known limitations' > 'Altium DelphiScript engine can crash'."


def recovery_guidance(fault: str) -> dict:
    """Structured recovery steps for a fault kind.

    Returns ``{fault, diagnosis, steps:[...], docs}``. An unknown fault gets
    a generic stop-and-relaunch fallback so callers never get an empty dict.
    """
    entry = _GUIDANCE.get(fault)
    if entry is None:
        entry = {
            "diagnosis": "The Altium script engine is not responding as expected.",
            "steps": [_STOP_STEP, _RELAUNCH_STEP],
        }
    return {
        "fault": fault,
        "diagnosis": entry["diagnosis"],
        "steps": list(entry["steps"]),
        "docs": _DOCS_HINT,
    }


def recovery_message(fault: str) -> str:
    """One-line, numbered recovery steps for embedding in an error message.

    The MCP layer surfaces ``str(exception)``, not the structured details, so
    the actionable steps must live in the message text too. Same source as
    ``recovery_guidance``, no drift.
    """
    g = recovery_guidance(fault)
    numbered = " ".join(f"{i}) {s}" for i, s in enumerate(g["steps"], 1))
    return f"Recovery: {numbered}"
