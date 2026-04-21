# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Static lint for Altium DelphiScript .pas files.

DelphiScript under Altium is a RemObjects PascalScript variant with specific
behaviours that differ from Free Pascal or Delphi. These regressions kept
landing on users and costing debugging sessions, so they are pinned here:

1.  ``Inc(arr[i])`` — parse error ")" expected. DelphiScript only accepts
    Inc/Dec on plain identifiers. Expand to ``arr[i] := arr[i] + 1``.

2.  ``{`` or ``}`` inside a ``{ ... }`` comment — the inner brace closes the
    comment early and the tail of the line is parsed as code, usually
    yielding "Unterminated string" somewhere unrelated.

3.  ``Try ... .UseMetricUnit ... Except`` — ISch_Document.UseMetricUnit is
    undeclared. Try/Except can NOT guard compile-time undeclared identifiers
    in DelphiScript; the whole file fails to compile. Use UnitSystem instead.

4.  ``Try ... .MemberCount ... Except`` / ``Try ... .MemberName[...]`` —
    IPCB_ObjectClass has no per-member enumeration surface; these are
    compile-time errors.

The linter walks ``scripts/altium/*.pas`` excluding the legacy monolithic
``Altium_MCP.pas`` (not in the active PrjScr) and fails any test whose rule
matches. Adding a new rule is a one-line regex in BAD_PATTERNS.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts" / "altium"

# Files that are part of the active build. Altium_MCP.pas is a legacy
# pre-split monolithic dump kept for reference only and is not in
# Altium_API.PrjScr; it should not gate the lint.
EXCLUDED = {"Altium_MCP.pas"}


def _live_pas_files() -> list[Path]:
    if not SCRIPTS_DIR.is_dir():
        return []
    return [
        p for p in sorted(SCRIPTS_DIR.iterdir())
        if p.suffix.lower() == ".pas" and p.name not in EXCLUDED
    ]


def _strip_string_literals(src: str) -> str:
    """Replace single-quoted string literals with spaces so lint patterns
    don't match text inside strings. Pascal escapes '' inside strings.

    Apostrophes inside ``{ ... }`` and ``// ...`` comments are preserved
    (left as-is) — they are not Pascal string delimiters there and
    treating them as such caused an earlier version to swallow whole
    files after an ``aren't`` in a comment.
    """
    out = []
    i = 0
    n = len(src)
    depth_brace = 0  # { ... } comment depth
    in_line_comment = False
    while i < n:
        c = src[i]
        if c == "\n":
            out.append(c)
            in_line_comment = False
            i += 1
            continue
        if in_line_comment:
            out.append(c)
            i += 1
            continue
        if depth_brace == 0 and c == "/" and i + 1 < n and src[i + 1] == "/":
            out.append("/")
            out.append("/")
            in_line_comment = True
            i += 2
            continue
        if c == "{":
            out.append(c)
            depth_brace += 1
            i += 1
            continue
        if c == "}":
            out.append(c)
            if depth_brace > 0:
                depth_brace -= 1
            i += 1
            continue
        if depth_brace > 0:
            # Inside a block comment: preserve verbatim.
            out.append(c)
            i += 1
            continue
        if c == "'":
            # String literal — replace with spaces, keeping newlines.
            out.append(" ")
            i += 1
            while i < n:
                if src[i] == "'":
                    out.append(" ")
                    i += 1
                    if i < n and src[i] == "'":
                        out.append(" ")
                        i += 1
                        continue
                    break
                if src[i] == "\n":
                    out.append("\n")
                else:
                    out.append(" ")
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _iter_lines_outside_comments(src: str):
    """Yield (lineno, line) for lines not inside a { ... } comment block.

    This is approximate — it treats {...} as a block comment region and
    ignores // line comments for the bad-pattern grep. Good enough for
    catching DelphiScript foot-guns; not a full Pascal parser.
    """
    depth = 0
    for lineno, raw in enumerate(src.splitlines(), start=1):
        # Compute the portion of this line that is NOT inside a comment.
        kept = []
        line = raw
        i = 0
        while i < len(line):
            if depth == 0 and line[i] == "{":
                depth += 1
                i += 1
            elif depth > 0 and line[i] == "}":
                depth -= 1
                i += 1
            elif depth == 0 and line[i:i + 2] == "//":
                break
            elif depth == 0:
                kept.append(line[i])
                i += 1
            else:
                i += 1
        yield lineno, "".join(kept)


# Each rule: (regex, human description, example of the wrong form)
BAD_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"\bInc\s*\(\s*\w+\s*\["),
        "Inc() on an array/record element — DelphiScript parse error",
        "Inc(arr[i])  →  arr[i] := arr[i] + 1",
    ),
    (
        re.compile(r"\bDec\s*\(\s*\w+\s*\["),
        "Dec() on an array/record element — DelphiScript parse error",
        "Dec(arr[i])  →  arr[i] := arr[i] - 1",
    ),
    (
        re.compile(r"\bUseMetricUnit\b"),
        "ISch_Document.UseMetricUnit is undeclared; use UnitSystem (TUnitSystem enum)",
        "SchDoc.UseMetricUnit  →  SchDoc.UnitSystem = eMetric",
    ),
    (
        re.compile(r"IPCB_ObjectClass.*\.MemberCount|\.MemberName\s*\["),
        "IPCB_ObjectClass has no per-member enumeration surface",
        "ObjClass.MemberCount / ObjClass.MemberName[I]  →  iterate nets and group",
    ),
    (
        re.compile(r"\beElectricBiDir\b"),
        "eElectricBiDir is undeclared; Altium spells the bidirectional "
        "pin electrical type eElectricIO",
        "Pin.Electrical := eElectricBiDir  →  Pin.Electrical := eElectricIO",
    ),
]


def _scan_file_for_bad_patterns(path: Path) -> list[str]:
    """Return a list of human-readable violation messages for the given file."""
    src = path.read_text(encoding="utf-8", errors="replace")
    code_only = _strip_string_literals(src)
    violations: list[str] = []
    for lineno, fragment in _iter_lines_outside_comments(code_only):
        for pattern, desc, hint in BAD_PATTERNS:
            if pattern.search(fragment):
                violations.append(f"{path.name}:{lineno}  {desc}\n    hint: {hint}")
    return violations


def _check_comment_braces(path: Path) -> list[str]:
    """Flag `{` or `}` inside a `{ ... }` comment body.

    Pascal treats any `}` inside a block comment as the comment's terminator,
    so a literal `}` inside the comment text closes it early and turns the
    rest into code. Catches the JSON-shape-in-comment foot-gun.

    String literals and ``//`` line comments are ignored — braces inside
    them are harmless.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Strip string literals first so braces inside 'foo{bar}baz' don't trip
    # the check. Line numbers and column positions are preserved because
    # _strip_string_literals replaces each stripped char with a space.
    src = _strip_string_literals(raw)
    violations: list[str] = []
    depth = 0
    line = 1
    col = 0
    in_line_comment = False
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            col = 0
            in_line_comment = False
            i += 1
            continue
        col += 1
        if in_line_comment:
            i += 1
            continue
        if depth == 0 and c == "/" and i + 1 < n and src[i + 1] == "/":
            in_line_comment = True
            i += 2
            continue
        if c == "{":
            if depth > 0:
                violations.append(
                    f"{path.name}:{line}:{col}  "
                    "literal '{' inside a `{{ ... }}` comment — will corrupt the parse"
                )
            depth += 1
        elif c == "}":
            if depth > 0:
                depth -= 1
        i += 1
    return violations


class TestDelphiScriptLint:
    """Static lint rules for live DelphiScript sources."""

    @pytest.mark.parametrize("pas_file", _live_pas_files(), ids=lambda p: p.name)
    def test_no_forbidden_patterns(self, pas_file: Path) -> None:
        violations = _scan_file_for_bad_patterns(pas_file)
        assert not violations, (
            "DelphiScript anti-patterns detected:\n" + "\n".join(violations)
        )

    @pytest.mark.parametrize("pas_file", _live_pas_files(), ids=lambda p: p.name)
    def test_comment_braces_balanced(self, pas_file: Path) -> None:
        violations = _check_comment_braces(pas_file)
        assert not violations, (
            "Pascal comment brace issues:\n" + "\n".join(violations)
        )

    def test_lint_has_files_to_check(self) -> None:
        """Sanity: the fixture should have found at least the core .pas set."""
        files = _live_pas_files()
        names = {p.name for p in files}
        # Files that are definitely part of the build. If any is missing the
        # layout has drifted and the lint is silently scanning nothing.
        for required in ("Main.pas", "Dispatcher.pas", "PCB.pas", "Generic.pas"):
            assert required in names, (
                f"{required} missing from scripts/altium/ — the lint would "
                f"silently pass"
            )
