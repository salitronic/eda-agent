# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Generate docs/TOOL_REFERENCE.md from the live tool surface + metadata.

The README's tool tables are hand-maintained and don't carry the discovery
metadata (maturity tier, interaction class) that the ``tools/metadata.py``
registry computes. This generator enumerates the actually-registered tools,
joins each with its metadata and one-line docstring, and emits a searchable
reference grouped by category with maturity/interaction badges: the same
data ``tool_catalog`` serves at runtime, in a browsable form.

Run: ``python scripts/gen_tool_reference.py`` (writes docs/TOOL_REFERENCE.md).
Pure Python; no Altium. ``build_reference()`` returns the markdown string so
tests can assert on it without touching disk.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

_INTERACTION_NOTE = {
    "modal": "⚠ opens a blocking Altium dialog (needs a human click)",
    "partial": "◑ leaves the job incomplete (needs a follow-up tool)",
    "readonly": "read-only",
    "silent": "mutates, no dialog",
}

_MATURITY_NOTE = {
    "offline": "pure Python, CI-tested",
    # Not "simulator-tested": the label means every command the tool
    # sends has a simulator handler, so the call CAN be driven with no
    # Altium. Whether a test does drive it today is a separate claim,
    # and the old wording asserted it for 131 tools that had no handler
    # at all. See _SIMULATOR_TOOLS in tools/metadata.py.
    "simulator": "runs against the simulator, no Altium needed",
    # Not "verified only on live Altium", which was wrong twice over.
    #
    # It claimed VERIFICATION for a label that measures nothing: the
    # classification means the tool needs a running editor, and says
    # nothing about whether anyone has ever run it. Exactly the mistake
    # the simulator label above was already corrected for.
    #
    # And it named ALTIUM for 182 EasyEDA tools. A live EasyEDA session
    # had 64 of 65 reads fail, so "verified" was false for that backend
    # in both halves of the sentence.
    #
    # What IS measured lives in extensions/easyeda/verified.json, per
    # command, written only by a real session.
    "live_only": "needs a running editor; being listed here is not "
                 "evidence that it has been run against one",
}

_CATEGORY_ORDER = [
    # "core" first: it holds the EDA-agnostic main flow (review_design,
    # get_board_info, list_components, list_nets, run_drc, run_erc), which
    # is where a reader starts. Unlisted categories are appended after
    # these, so a new one is never dropped, only badly placed.
    "core", "meta", "parts", "application", "project", "library", "schematic",
    "generic", "pcb", "audit", "design", "simulation", "routing", "kicad",
    "easyeda",
    "other",
]


def _collect() -> list[dict]:
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_backend
    from eda_agent.tools import metadata as M

    # "both" is Altium plus KiCad plus the EDA-agnostic tools. It does NOT
    # include EasyEDA, deliberately: "both" names the two desktop tools a
    # user runs side by side, and widening it would change what an existing
    # setting means.
    #
    # The reference documents every tool that exists, which is a different
    # question from what any one backend advertises, so the EasyEDA-native
    # tools are added on top. They share no names with the others, so
    # nothing is registered twice.
    from eda_agent.tools import register_easyeda_tools

    mcp = FastMCP("gen")
    register_backend(mcp, "both")
    register_easyeda_tools(mcp)
    from eda_agent.tools.folds import call_form

    tools = list(asyncio.run(mcp.list_tools()))
    # Members of a family tool and single-item tools are not advertised;
    # each is listed with the advertised tool that runs it.
    hidden = list((getattr(mcp, "hidden", {}) or {}).values())
    out = []
    for t in tools + hidden:
        md = M.tool_metadata(t.name)
        summary = (t.description or "").strip().split("\n", 1)[0]
        if t in hidden:
            summary = f"(through `{call_form(t.name)}`) {summary}"
            md["hidden"] = True
        md["summary"] = summary
        out.append(md)
    return out


def build_reference() -> str:
    records = _collect()
    by_cat: dict[str, list[dict]] = {}
    for r in records:
        category = r["category"]
        # At RUNTIME the EasyEDA tools carry subject categories, so
        # tool_catalog can be browsed the same way on either backend.
        # This document lists every backend at once, where that would
        # interleave two tool sets under one heading and leave a reader
        # unable to see which surface they are looking at. So the
        # subject becomes a sub-heading of the backend here, the way
        # kicad already reads.
        if r["name"].startswith("easyeda_"):
            category = f"easyeda / {category}"
        by_cat.setdefault(category, []).append(r)

    lines = [
        "# Tool reference",
        "",
        "_Auto-generated by `scripts/gen_tool_reference.py` from the live "
        "tool surface + `tools/metadata.py`. Do not edit by hand._",
        "",
        f"**{sum(1 for r in records if not r.get('hidden'))} tools "
        f"advertised** across {len(by_cat)} categories, and "
        f"{sum(1 for r in records if r.get('hidden'))} more reached through "
        f"a family tool, a bulk tool or a tool's items (marked \"through\").",
        "",
        "**Maturity**: "
        + "; ".join(f"`{k}` = {v}" for k, v in _MATURITY_NOTE.items())
        + ".",
        "",
        "**Interaction**: "
        + "; ".join(f"`{k}` = {v}" for k, v in _INTERACTION_NOTE.items())
        + ".",
        "",
    ]

    ordered_cats = [c for c in _CATEGORY_ORDER if c in by_cat]
    ordered_cats += [c for c in sorted(by_cat) if c not in _CATEGORY_ORDER]

    for cat in ordered_cats:
        rows = sorted(by_cat[cat], key=lambda r: r["name"])
        lines.append(f"## {cat} ({len(rows)})")
        lines.append("")
        lines.append("| Tool | Interaction | Maturity | Summary |")
        lines.append("|---|---|---|---|")
        for r in rows:
            summ = r["summary"].replace("|", "\\|")
            lines.append(
                f"| `{r['name']}` | {r['interaction']} | {r['maturity']} | {summ} |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    out_path = repo_root / "docs" / "TOOL_REFERENCE.md"
    out_path.write_text(build_reference(), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
