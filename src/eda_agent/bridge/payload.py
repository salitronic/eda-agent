# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""One sanitiser for the batch-payload wire format.

Lives beside the bridge because the grammar is the IPC
contract, not a detail of any one tool: tools/, design/ and
anything else that hands a payload to Pascal need the same
rules, and design/ importing from tools/ to get them would be
the wrong dependency.

Every bulk tool sends the same grammar: operations split on ``~~``,
fields on ``;``, key from value on the FIRST ``=``. That is parsed by
``NextBatchOp`` / ``GetBatchField`` in Main.pas, and the Python side
must never emit a value that reshapes it.

Kept in one place because it was not. ``library.py`` had this function
and ``generic.py`` had a near-copy that replaced ``~~`` with two spaces
instead of collapsing tilde runs, so the same input produced different
output depending on which tool sent it.

The rules are narrow on purpose:

* ``;`` becomes ``,`` -- it ends a field, so an unescaped one both
  truncates the value and turns the remainder into a live ``key=value``.
* runs of two or more ``~`` collapse to one -- ``~~`` ends an operation
  and would fabricate an extra pin or pad.
* ``=`` is left ALONE. Only the first ``=`` splits, so ``EN=1`` and
  ``OC=OUT`` parse correctly and escaping them would corrupt real names.
* a lone ``~`` is left ALONE. It is KiCad's overbar marker (``~{RESET}``)
  and mangling it renames every active-low pin on the part. A value
  ending in one, last in its operation, meets the separator as ``~~~``;
  NextBatchOp reads exactly three tildes as the value's then the
  separator, so it stays.

Substitution rather than escaping is forced: the deployed Pascal parser
has no escape syntax to escape TO.
"""

from __future__ import annotations

import re

__all__ = ["payload_safe", "unsendable_chars"]

#: Two or more consecutive tildes. A single one is data.
#:
#: ``~+`` would behave identically -- collapsing a run of one to
#: one is a no-op -- so the two spellings are interchangeable here
#: and a mutation between them proves nothing. What must NOT change
#: is that a lone tilde survives at all: stripping it renames every
#: active-low pin, since ``~{RESET}`` is KiCad's overbar syntax.
_MULTI_TILDE = re.compile(r"~{2,}")


def payload_safe(value: object) -> str:
    """Neutralise batch-payload delimiters inside a free-text field.

    Verified against the real tool: a pin named ``A;x=99`` injected an
    ``x=99`` field ahead of the true coordinate, placing the pin
    somewhere else, and ``B~~designator=99`` split its operation in two
    and fabricated an extra pin. Both are silent, because the payload
    stays syntactically valid either way.
    """
    text = str(value).replace(";", ",")
    return _MULTI_TILDE.sub("~", text)


def unsendable_chars(value: object) -> str:
    """Characters in ``value`` the bridge cannot carry, first-seen order.

    Altium's DelphiScript strings are single-byte. ``UnescapeJsonString``
    in Main.pas emits ``Chr(Code)`` for a codepoint up to 255 and a
    literal ``?`` for anything above, so text crossing the wire is
    silently flattened rather than rejected: ``10`` + the ohm sign
    arrives as ``10?``.

    The boundary is exactly U+00FF, not "non-ASCII". The micro sign, the
    degree sign and accented Latin all survive; the ohm sign and any CJK
    text do not. Returning the offending characters rather than a
    boolean lets a caller name them, which matters because the damage is
    invisible in the result: the call succeeds and the field is simply
    wrong.

    Returns "" when the text survives intact.
    """
    seen: set[str] = set()
    out: list[str] = []
    for ch in str(value):
        if ord(ch) > 255 and ch not in seen:
            seen.add(ch)
            out.append(ch)
    return "".join(out)
