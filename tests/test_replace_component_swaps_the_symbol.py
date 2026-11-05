# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_replace_component swaps the symbol, or says it did not.

It rewrote LibReference, DesignItemId and the library name and reported
success, while the symbol and pins on the sheet stayed the old part's
(issue #94). Given a library, it now places the new symbol where the old
one is, compares every pin's connection point, and only then removes the
old part. Without one it relinks as before and says symbol_replaced
false.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.tools import generic as generic_tools

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _fns():
    return load(_GENERIC)


def test_a_library_path_goes_to_the_swap():
    body = _fns()["Gen_ReplaceComponent"]
    assert "ExtractJsonValue(Params, 'library_path')" in body
    swap = body.index("Result := SwapSchSymbol(")
    # After the iterator is destroyed: the swap adds and removes parts.
    assert body.index("SchDoc.SchIterator_Destroy(Iterator);") < swap


def test_the_relink_says_the_symbol_stayed():
    body = _fns()["Gen_ReplaceComponent"]
    assert '"symbol_replaced":false' in body


def test_the_swap_compares_pins_before_removing_the_old_part():
    body = _fns()["SwapSchSymbol"]
    load_at = body.index("SchServer.LoadComponentFromLibrary(")
    pre = body.index("SchBeginEdit(SchDoc")
    old_map = body.index("OldMap := SchPinMap(OldComp);")
    new_map = body.index("NewMap := SchPinMap(NewComp);")
    refuse = body.index("'PIN_MISMATCH'")
    remove_old = body.index("SchDoc.RemoveSchObject(OldComp);")
    stamp = body.index("NewComp.UniqueId := OldId;")
    # Loaded outside the edit; old pins read before; refusal before the
    # old part goes; UniqueId stamped once its old owner is gone.
    assert load_at < pre
    assert old_map < pre < new_map < refuse < remove_old < stamp


def test_the_id_is_checked_again_after_the_commit():
    """Measured live: the in-transaction stamp held on one swap and was
    reminted at PostProcess on another, unlinking the part from the
    board."""
    body = _fns()["SwapSchSymbol"]
    # Committed even inside a bulk call, which holds a plain SchEndEdit.
    post = body.index("SchCommitEdit(SchDoc, 'Edit');")
    again = body.index("If (OldId <> '') And (NewId <> OldId) Then", post)
    assert body.index("NewComp.UniqueId := OldId;", again) < body.index(
        "unique_id_kept", again)


def test_a_refused_swap_takes_the_new_symbol_back_off():
    body = _fns()["SwapSchSymbol"]
    refuse_block = body[body.index("If ((Missing <> ';') Or (Moved <> ';'))"):
                        body.index("'PIN_MISMATCH'")]
    assert "SchDoc.RemoveSchObject(NewComp);" in refuse_block
    assert "AllowPinChanges" in refuse_block


def test_pins_are_compared_at_their_connection_point():
    """Pin.Location is the body end; wires attach at the other end."""
    body = _fns()["SchPinMap"]
    assert "PinEndX(PX, Orient, PLen)" in body
    assert "PinEndY(PY, Orient, PLen)" in body


def test_the_swap_is_defined_before_its_caller():
    text = _GENERIC.read_text(encoding="utf-8")
    assert text.index("Function SwapSchSymbol(") < text.index(
        "Function Gen_ReplaceComponent(")


def test_replicate_reports_success_when_the_id_was_kept():
    """success compared 'true'/'false' with the master's UniqueId, so it
    was false every time."""
    body = _fns()["Gen_ReplicateSchComponent"]
    assert "BoolToJsonStr(Shared = 'true')" in body
    assert "BoolToJsonStr(Shared = MasterId)" not in body


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.params = None

    async def send_command_async(self, command, params=None, timeout=None):
        self.params = dict(params or {})
        return {"success": True}


def _replace(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(generic_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    generic_tools.register_generic_tools(mcp)
    asyncio.run(mcp.tools["proj_replace_component"](**kw))
    return bridge.params


def test_the_tool_sends_the_library_and_the_pin_choice(monkeypatch):
    sent = _replace(monkeypatch, designator="U1", new_lib_ref="LM358",
                    library_path=r"C:\libs\op.SchLib", allow_pin_changes=True)
    assert sent["library_path"] == r"C:\libs\op.SchLib"
    assert sent["allow_pin_changes"] == "true"


def test_without_a_library_nothing_about_a_swap_is_sent(monkeypatch):
    sent = _replace(monkeypatch, designator="U1", new_lib_ref="LM358")
    assert "library_path" not in sent and "allow_pin_changes" not in sent
