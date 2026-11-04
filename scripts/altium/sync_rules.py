# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Write the command rules from safety.py into StatusForm.pas.

    python scripts/altium/sync_rules.py          # rewrite the region
    python scripts/altium/sync_rules.py --check  # exit 1 if it is stale

The rules live in src/eda_agent/safety.py. StatusForm.pas carries a
generated copy between PASCAL_RULES_BEGIN and PASCAL_RULES_END; nothing
else in the file is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATUS_FORM = HERE / "StatusForm.pas"
sys.path.insert(0, str(HERE.parents[1] / "src"))

from eda_agent.safety import (  # noqa: E402
    PASCAL_RULES_BEGIN, PASCAL_RULES_END, pascal_command_rules)


def synced(text: str) -> str:
    """The StatusForm.pas text with the rules region regenerated."""
    start = text.index(PASCAL_RULES_BEGIN)
    end = text.index(PASCAL_RULES_END, start) + len(PASCAL_RULES_END)
    return text[:start] + pascal_command_rules() + text[end:]


def main(argv: list[str]) -> int:
    raw = STATUS_FORM.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    want = synced(text)
    if want == text:
        return 0
    if "--check" in argv:
        print("StatusForm.pas command rules are stale; run "
              "python scripts/altium/sync_rules.py", file=sys.stderr)
        return 1
    STATUS_FORM.write_bytes(want.replace("\n", newline).encode("utf-8"))
    print("StatusForm.pas command rules rewritten from safety.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
