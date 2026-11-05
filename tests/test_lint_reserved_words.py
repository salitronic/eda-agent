# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Unit tests for the reserved-keyword lint rule.

Proves the deny-list actually catches each reserved word in both the
single-line parameter form and the multi-line Var-block form. Keeps the
26-word set self-validating: if a future PR drops a keyword from
RESERVED_AS_NAME, these tests fail.

Background: DelphiScript reports "Expression expected but <Word> found"
when a reserved keyword is used as a parameter or local-variable name.
The error points at the function header, not the misuse, so it's a
costly bug to chase. The lint is the only way to catch it pre-runtime.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LINT_PATH = REPO_ROOT / "scripts" / "altium" / "lint.py"


def _load_lint_module():
    """Load scripts/altium/lint.py as a module (it's outside the package)."""
    spec = importlib.util.spec_from_file_location("_altium_lint", LINT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_altium_lint"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lint():
    return _load_lint_module()


def test_reserved_set_matches_memory(lint):
    """The deny-list contains the 26 keywords documented in
    delphiscript_reserved_words.md. If memory documents more, sync them.
    """
    expected = {
        "Label", "Type", "Class", "Object", "Record", "Array", "Set",
        "String", "File", "Unit", "Function", "Procedure", "Const", "Var",
        "End", "Begin", "If", "Then", "Else", "Goto", "With", "In", "Is",
        "As", "Of", "Out", "Index", "Reference",
    }
    missing = expected - lint.RESERVED_AS_NAME
    extra = lint.RESERVED_AS_NAME - expected
    assert not missing, (
        f"Memory documents these reserved words but lint doesn't block "
        f"them: {sorted(missing)}. Sync RESERVED_AS_NAME with "
        f"memory/delphiscript_reserved_words.md."
    )
    assert not extra, (
        f"Lint blocks these but they aren't documented in memory: "
        f"{sorted(extra)}. Either add to memory or remove from lint."
    )


@pytest.mark.parametrize("kw", sorted({
    "Label", "Type", "Class", "Object", "Record", "Array", "Set",
    "String", "File", "Unit", "Function", "Procedure", "Const", "Var",
    "End", "Begin", "If", "Then", "Else", "Goto", "With", "In", "Is",
    "As", "Of", "Out",
}))
def test_reserved_word_caught_in_parameter_list(lint, kw):
    """RULE_RESERVED_IDENT fires when the keyword is used in a parameter
    list like ``Procedure F(Label : String)``.
    """
    pattern = lint.RULE_RESERVED_IDENT.pattern
    # Match the exact failure mode the rule was built for
    bad_line = f"Procedure F({kw} : String);"
    assert pattern.search(bad_line), (
        f"RULE_RESERVED_IDENT failed to flag {kw!r} in a parameter list. "
        f"Pattern: {pattern.pattern!r}, line: {bad_line!r}"
    )


# Block-marker keywords are intentionally NOT tested against the Var-block
# scanner: a line starting with `Var`, `Begin`, `End`, `Function`,
# `Procedure`, `Type`, or `Const` is a structural marker, not a local
# declaration, so the scanner treats them as block boundaries (correct
# behaviour). Their realistic misuse is in parameter lists, covered by
# the single-line test above. Same reasoning for `If`/`Then`/`Else` --
# they're control flow, not declarable.
_VAR_BLOCK_REALISTIC = sorted({
    "Label", "Class", "Object", "Record", "Array", "Set", "String", "File",
    "Unit", "Goto", "With", "In", "Is", "As", "Of", "Out",
})


@pytest.mark.parametrize("kw", _VAR_BLOCK_REALISTIC)
def test_reserved_word_caught_in_var_block(lint, kw):
    """_scan_reserved_in_var_block fires when the keyword is declared as
    a local on its own line inside a Var block. Only realistic-mistake
    keywords (non-block-markers) are tested here.
    """
    lines = [
        "Function F : Integer;\n",
        "Var\n",
        f"    {kw} : String;\n",  # the violation
        "Begin\n",
        "    Result := 0;\n",
        "End;\n",
    ]
    findings = lint._scan_reserved_in_var_block("test.pas", lines)
    rules_fired = {f.rule for f in findings}
    assert "reserved-word-in-var-block" in rules_fired, (
        f"_scan_reserved_in_var_block failed to flag {kw!r} on a Var-block "
        f"declaration. Got findings: {findings}"
    )


def test_a_reserved_word_later_in_the_name_list_is_caught(lint):
    """The declaration that actually failed Altium's compile, 2026-09-24.

    ``Index`` was not first on its line, and the rule used to look only at
    the first name, so it walked straight past this.
    """
    lines = [
        "Function F : String;\n",
        "Var\n",
        "    Index, Emitted, I : Integer;\n",
        "Begin\n",
        "End;\n",
    ]
    findings = lint._scan_reserved_in_var_block("test.pas", lines)
    assert [f.line for f in findings] == [3]
    assert lint.RULE_RESERVED_IDENT.pattern.search(
        "Procedure F(A, Index : Integer);")


def test_casing_does_not_hide_a_reserved_word(lint):
    """DelphiScript is case-insensitive, so ``index`` fails like ``Index``."""
    lines = ["Var\n", "    index : Integer;\n", "Begin\n"]
    assert lint._scan_reserved_in_var_block("test.pas", lines)
    assert lint.RULE_RESERVED_IDENT.pattern.search("Procedure F(label : String);")


def test_names_that_merely_contain_a_reserved_word_are_fine(lint):
    lines = ["Var\n", "    Idx, IndexOf, ReIndex, Indexed : Integer;\n",
             "    Labels, StringList : Integer;\n", "Begin\n"]
    assert not lint._scan_reserved_in_var_block("test.pas", lines)
    assert not lint.RULE_RESERVED_IDENT.pattern.search(
        "Procedure F(Idx, Labels : Integer);")


def test_safe_identifier_not_caught(lint):
    """Sanity: legitimate names like Comp, Pin, Doc don't false-positive."""
    safe_lines = [
        "Procedure F(Comp : IPCB_Component);\n",
        "Procedure F(Pin : ISch_Pin; Doc : ISch_Document);\n",
        "Var\n",
        "    Comp : IPCB_Component;\n",
        "    Iter : IPCB_BoardIterator;\n",
        "Begin\n",
    ]
    findings = lint._scan_reserved_in_var_block("test.pas", safe_lines)
    assert not findings, f"False positive on safe names: {findings}"
    # Also check single-line rule
    for line in safe_lines:
        assert not lint.RULE_RESERVED_IDENT.pattern.search(line), (
            f"False positive on safe line: {line!r}"
        )
