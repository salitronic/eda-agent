# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""``IPCB_ComponentBody`` has no ``Rotation``, and assigning it is fatal.

MEASURED on Altium 26.9.1.9: ``lib_link_3d_model`` with ``rotation_z``
set raised the modal "Undeclared identifier: Rotation", never returned,
and left the polling loop dead. The whole bridge had to be restarted by
hand.

What makes this worth a guard rather than a comment is that the code
LOOKED defended. The assignment sat inside a ``Try ... Except`` with a
``DidRotation`` flag, so it read as an adjustment that would simply
report false if the API refused. An undeclared identifier is not an
exception in DelphiScript, it is a compile fault raised when the
function is first CALLED, and no ``Except`` can catch it. The guard
could never have fired, and because DelphiScript compiles a function
lazily the fault stayed invisible until someone passed a rotation.

Same family as the ``ObjectIDToObjectName`` fault: an identifier nobody
verified, dormant for as long as its branch went unexercised.

The rotation is reachable, but on the MODEL and before it is attached
(``Model.SetState(RotX, RotY, RotZ, Dz)``, as the SDK declares it and
AutoSTEPplacer.pas uses it).
"""

from __future__ import annotations

import pathlib
import re

PASCAL = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "altium"
LIBRARY_PY = (pathlib.Path(__file__).resolve().parents[1] / "src"
              / "eda_agent" / "tools" / "library.py")


def _sources():
    return sorted(PASCAL.glob("*.pas"))


def test_no_pascal_assigns_rotation_on_a_component_body():
    """The exact statement that took the bridge down."""
    offenders = []
    for path in _sources():
        for n, line in enumerate(path.read_text(
                encoding="utf-8", errors="replace").splitlines(), 1):
            code = line.split("{")[0]
            if re.search(r"\bBody\s*\.\s*Rotation\s*:=", code):
                offenders.append(f"{path.name}:{n}: {line.strip()}")

    assert not offenders, (
        "assigning Body.Rotation raises an uncatchable Undeclared "
        "identifier fault that kills the polling loop:\n"
        + "\n".join(offenders))


def test_the_rotation_goes_on_the_model_before_it_is_bound():
    """The docstring once promised 'sets the body's Rotation', and a
    caller who believed it paid with the bridge. The rotation is set on
    the MODEL, IPCB_Model.SetState(RotX, RotY, RotZ, Dz) as the SDK
    declares it, before Body.Model takes it; the contract says it is
    written and not read back."""
    text = LIBRARY_PY.read_text(encoding="utf-8")
    start = text.index("def lib_link_3d_model")
    doc = text[start:start + 4000]
    assert "sets the body's Rotation" not in doc
    assert "model before it is bound to the body" in " ".join(doc.split())
    pas = (PASCAL / "Library.pas").read_text(encoding="utf-8-sig")
    body = pas[pas.index("Function Lib_Link3DModel("):]
    body = body[:body.index("\nEnd;")]
    assert body.index("Model.SetState(RotX, RotY, RotZ, 0)") < body.index("Body.Model := Model")
