# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""DelphiScript trap linter for Altium integration scripts.

Every rule encodes a parse-time hazard that has bitten us at least once.
Each finding cites the memory entry that explains the bug so the fix is
one click away. Run standalone (``python lint.py``) or via ``build.py``,
which calls ``run_lint(...)`` before producing ``Altium_MCP.pas``.

Severity:
- ``error``: must fix before deploy; ``build.py`` aborts.
- ``warn``:  likely but not certain; ``build.py`` prints, continues.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PAS_FILES = [
    "Main.pas",
    "Utils.pas",
    "Application.pas",
    "Project.pas",
    "Library.pas",
    "PCBGeneric.pas",
    "PCB.pas",
    "Generic.pas",
    "Audit.pas",
    "StatusForm.pas",
    "Dispatcher.pas",
]


@dataclass
class Finding:
    file: str
    line: int
    col: int
    rule: str
    severity: str
    snippet: str
    memory: str

    def format(self) -> str:
        loc = f"{self.file}:{self.line}:{self.col}"
        return (f"[{self.severity.upper():5}] {loc}  {self.rule}\n"
                f"        {self.snippet.strip()}\n"
                f"        see memory: {self.memory}")


# ---------------------------------------------------------------------------
# Per-line rules: pure regex over a single source line. Comments and string
# literals are stripped before rules run so that '$BADHEX' inside a doc-string
# or a `{ comment with $123 }` doesn't trip the hex-literal check.
# ---------------------------------------------------------------------------

@dataclass
class LineRule:
    name: str
    pattern: re.Pattern
    severity: str
    memory: str
    description: str
    # Optional callable that gets the match and returns True only if the
    # finding is real -- used to suppress false positives that regex alone
    # can't filter out.
    confirm: Optional[Callable[[re.Match, str], bool]] = None


# Empty-string literal as a function call argument -- specifically the
# .Add('') trap on TStringList. Pattern: `.Method('')` where the method
# is one of the mutators we know choke. Comparisons like `S <> ''` are
# not function calls and stay legal.
RULE_EMPTY_LITERAL_ARG = LineRule(
    name="empty-string-literal-arg",
    pattern=re.compile(r"\.(Add|Insert|SetText|LoadFromFile|SaveToFile)\s*\(\s*''\s*[,)]"),
    severity="error",
    memory="delphiscript_tstringlist_no_insert.md",
    description="DelphiScript trips on '' as a call argument; route through a String var.",
)

# .Insert(N, ...) on plain TStringList is undeclared. TMemo.Lines.Insert
# (TStrings via a VCL property) is the working pattern -- same exception
# class we make for .Clear. Heuristic: the chain segment before `.Insert`
# is captured; if it's a known TStrings accessor (Lines / Items / Strings)
# the call is fine, otherwise warn.
RULE_INSERT_INDEX = LineRule(
    name="tstringlist-insert",
    pattern=re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.Insert\s*\(\s*\d+\s*,"),
    severity="warn",
    memory="delphiscript_tstringlist_no_insert.md",
    description="TStringList.Insert is undeclared; route through TMemo.Lines or shift.",
    confirm=lambda m, line: m.group(1) not in _STRINGS_ACCESSORS,
)

# .Clear on a TStringList is undeclared. TMemo.Lines.Clear works.
# Heuristic: warn on `Name.Clear` where Name starts uppercase, BUT skip
# the well-known TStrings accessor names (Lines, Items) which delegate
# through a VCL property where Clear is exposed.
_STRINGS_ACCESSORS = {"Lines", "Items", "Strings"}
RULE_CLEAR_ON_STRINGLIST = LineRule(
    name="tstringlist-clear",
    pattern=re.compile(r"\b([A-Z][A-Za-z0-9_]*)\.Clear\b"),
    severity="warn",
    memory="delphiscript_tstringlist_no_clear.md",
    description="Plain TStringList.Clear is undeclared; truncate with a Delete loop.",
    confirm=lambda m, line: m.group(1) not in _STRINGS_ACCESSORS,
)

# Cardinal(x) / Integer(x) / Byte(x) / Word(x) / Int64(x) as expression
# typecasts. The same names are valid in Var declarations -- skip those.
RULE_TYPECAST = LineRule(
    name="invalid-typecast",
    pattern=re.compile(r"(?<![A-Za-z_])(Cardinal|Integer|Byte|Word|Int64)\s*\("),
    severity="error",
    memory="delphiscript_no_typecasts.md",
    description="DelphiScript rejects Cardinal(x)/Integer(x) as expression typecasts.",
    confirm=lambda m, line: (
        # Skip declarations like `Var X : Cardinal;` (no parens at all)
        # and skip `Function F : Cardinal;` -- those don't match anyway
        # because the regex requires `(` after the type name.
        # Real call-site cast: usually preceded by `:=`, an operator,
        # `Then`, `Else`, `If`, `Begin`, `(`, `,`, or start-of-line.
        # If the previous non-space char is alphanumeric, it's probably
        # someone's own function named Integer/Cardinal -- skip.
        not _preceded_by_alnum(m, line)
    ),
)

# Malformed hex literal: `$` followed by something that isn't an 8-digit
# Cardinal or a 4-digit Word. 7-digit literals silently abort the unit.
# We allow common short literals like `$FF`, `$00`, `$1234` (length 1-4)
# and require 8 digits for anything 5+.
RULE_BAD_HEX = LineRule(
    name="malformed-hex-literal",
    pattern=re.compile(r"\$([0-9A-Fa-f]+)\b"),
    severity="error",
    memory="delphiscript_malformed_hex_literal.md",
    description="Hex literal length must be 1-4 or exactly 8; 5-7 digits aborts the unit.",
    confirm=lambda m, line: not (len(m.group(1)) <= 4 or len(m.group(1)) == 8),
)

# Inc on array element -- the DelphiScript parser refuses `Inc(arr[i])`.
# Subscripting the RESULT of a call. DelphiScript accepts an index only on
# a variable, so `UpperCase(Ch)[1]` is rejected by the compiler with
# ") or ] expected" rather than failing at run time. It reached a user's
# editor as a broken deploy, which is exactly what this gate exists to
# stop. Assign to a local first, then index the local.
#
# The interface PROPERTIES that legitimately take an index are written
# `Obj.Prop[i]` with a dot before the name, and the leading (?<![.\w])
# keeps those out. A bare `Foo(x)[1]` is the shape that breaks.
RULE_INDEX_ON_CALL_RESULT = LineRule(
    name="index-on-call-result",
    pattern=re.compile(r"(?<![.\w])[A-Za-z_][A-Za-z0-9_]*\s*\([^()]*\)\s*\["),
    severity="error",
    memory="delphiscript_record_field_write_needs_materialized.md",
    description=("indexing a function result is a compile error; assign to "
                 "a local first, then index the local."),
)

RULE_INC_ARRAY = LineRule(
    name="inc-on-array-element",
    pattern=re.compile(r"\bInc\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\["),
    severity="error",
    memory="delphiscript_inc_array.md",
    description="Inc(arr[i]) parses as `) expected`; expand to arr[i] := arr[i] + 1.",
)

# DelphiScript silently ignores `{$I ...}` include directives. Detected
# via multi-line scan against the raw text because the strip pass blanks
# all `{...}` comments before per-line rules see them.

# Reserved Delphi keywords used as parameter/local names. Multi-line scan
# below handles the `Var` block case where the keyword sits on its own
# line. This single-line rule catches the in-parens parameter form like
# `Procedure F(Label : String)`. Synced with memory:
# delphiscript_reserved_words.md -- update both together.
RESERVED_AS_NAME = {"Label", "Type", "Class", "Object", "Record", "Array",
                    "Set", "String", "File", "Unit", "Function", "Procedure",
                    "Const", "Var", "End", "Begin", "If", "Then", "Else",
                    "Goto", "With", "In", "Is", "As", "Of", "Out",
                    # Measured 2026-09-24: `Index, Emitted, I : Integer;` in a
                    # Var block failed Altium's compile with "Error in
                    # declaration block". Delphi reads index as a property
                    # directive.
                    "Index",
                    # Measured 2026-10-28 on AD 26.11: a local declared
                    # `Cu, Reference : IPCB_ElectricalLayer;` stopped the
                    # compile with "Unknown identifier: Reference".
                    "Reference"}
_RESERVED_ALT = "|".join(sorted(RESERVED_AS_NAME))
RULE_RESERVED_IDENT = LineRule(
    name="reserved-word-as-identifier",
    # Anywhere in a parameter's name list, not only first: `(A, Index : X)`.
    # Case-insensitive, because DelphiScript is.
    pattern=re.compile(
        r"(?:\(|;)\s*(?:\w+\s*,\s*)*\b(" + _RESERVED_ALT + r")\b\s*(?:,|:)",
        re.IGNORECASE),
    severity="error",
    memory="delphiscript_reserved_words.md",
    description="Reserved keyword used as parameter name; rename it.",
)

# Typed constants and Var-with-initializer (Const cPi : Double = 3.14, or
# Var X : Integer = 5). DelphiScript only accepts UNTYPED constants
# (Const cPi = 3.14) and uninitialised Var declarations. The compile
# error is the cryptic "Typed constants aren't supported" with no line
# number that points at the right place. Drop the type annotation, OR
# move the initialisation into a Begin..End assignment.
RULE_TYPED_CONSTANT = LineRule(
    name="typed-constant",
    pattern=re.compile(
        r"^\s*[A-Za-z_]\w*\s*:\s*"
        r"(Double|Integer|Cardinal|Boolean|String|Byte|Word|Int64"
        r"|Single|Extended|Real|Char|LongInt|ShortInt|SmallInt)\s*=",
        re.IGNORECASE,
    ),
    severity="error",
    memory="delphiscript_typed_constants.md",
    description=(
        "DelphiScript does not support typed constants or "
        "initialised Var declarations. Drop the type annotation, or "
        "move the assignment into a Begin..End block."
    ),
)

# ISch_Component.CurrentFootprintModelName is a read-only property.
RULE_FOOTPRINT_NAME_WRITE = LineRule(
    name="footprint-name-readonly-write",
    pattern=re.compile(r"\.CurrentFootprintModelName\s*:="),
    severity="error",
    memory="delphiscript_current_footprint_model_name_readonly.md",
    description="CurrentFootprintModelName is read-only; edit Implementations.ModelName instead.",
)

# Standard Delphi RTL constants that DelphiScript does NOT predefine.
# `MaxInt` (and the Long/Min siblings) raise "Undeclared identifier" at
# runtime, uncatchable by Try/Except. Use a literal or the MAX_INT const
# declared in Main.pas. The negative lookbehind skips member accesses
# (`.MaxInt`) and longer identifiers (`MyMaxInt`); `MAX_INT` (underscore)
# never matches.
RULE_UNDECLARED_DELPHI_CONST = LineRule(
    name="undeclared-delphi-constant",
    pattern=re.compile(r"(?<![A-Za-z0-9_.])(MaxInt|MinInt|MaxLongInt|MinLongInt)\b"),
    severity="error",
    memory="delphiscript_undeclared_maxint.md",
    description="DelphiScript doesn't predefine MaxInt/MinInt/MaxLongInt; use a literal or the MAX_INT const.",
)

# Subtype-only PCB property read/written on a base IPCB_Primitive variable.
# DelphiScript resolves members against the DECLARED type, so Obj.X1 (only on
# IPCB_Track) is "Undeclared identifier" when Obj : IPCB_Primitive. The
# codebase names base primitives Obj / Prim and narrows subtype locals as
# Track / Arc / Pad / Via / Comp / Txt. Flag the base names touching a member
# that exists only on a PCB subtype (no schematic equivalent, so no SCH false
# positive). Fix: narrow via ObjectId into a typed local first.
RULE_PCB_SUBTYPE_ON_BASE = LineRule(
    name="pcb-subtype-prop-on-base",
    pattern=re.compile(
        # PCB-exclusive members only: no schematic interface has these, so a
        # match on a base-named var is unambiguously a PCB narrowing bug.
        # Width / XCenter / Radius / StartAngle / EndAngle are deliberately
        # NOT here -- they exist on schematic subtypes too, and GetSchProperty
        # reads Obj.Width on a working ISch base.
        r"\b(?:Obj|Prim|Prim1|Prim2)\.(x1|y1|x2|y2|StartX|StartY|EndX|EndY"
        r"|TopXSize|TopYSize|TopShape|HoleSize|Pattern|SourceDesignator"
        r"|SizeOnLayer|IsPadRemoved|IntersectLayer)\b",
        re.IGNORECASE,
    ),
    severity="error",
    memory="delphiscript_interface_narrowing.md",
    description="Subtype-only PCB property on a base IPCB_Primitive var; narrow to a typed local (Track/Arc/Pad/...) via ObjectId.",
)

# A PCB arc's stroke is LineWidth; IPCB_Arc has no Width. Arc.Width stops the
# script with "Undeclared identifier" and Try/Except does not catch it (seen on
# a live board 2026-09-14, fixed in 2d1630b). Schematic objects do have Width,
# which is why the subtype rule above leaves it out and this one keys on the
# Arc local name.
RULE_ARC_WIDTH = LineRule(
    name="pcb-arc-width",
    pattern=re.compile(r"\bArc\.Width\b"),
    severity="error",
    memory="delphiscript_interface_narrowing.md",
    description="IPCB_Arc has no Width; read Arc.LineWidth instead.",
)

# Pascal binds Or, And and Xor tighter than =, <> and the other comparisons, so
# `A = 'x' Or B = 'y'` reads as `A = ('x' Or B) = 'y'`, which is a type error
# or a wrong test, never what was meant. Every comparison beside one has to be
# bracketed. Found as an unbracketed line in a property allow-list.
_UNBRACKETED_COMPARISON = re.compile(
    r"(?<![(\w.'])[A-Za-z_][\w.]*(?:\[[^\]]*\])?\s*(?:=|<>|<=|>=|<|>)\s*"
    r"(?:'[^']*'|[\w.]+(?:\([^()]*\))?)\s+(?:Or|And|Xor)\b"
    r"|\b(?:Or|And|Xor)\s+(?!\(|Not\b)[A-Za-z_][\w.]*(?:\[[^\]]*\])?\s*"
    r"(?:=|<>|<=|>=|<|>)\s*", re.IGNORECASE)


def _blank_comments_and_strings(text: str) -> str:
    """``text`` with every comment and string body blanked, across lines.

    Unlike the per-line stripper this tracks a brace comment over several
    lines, so prose inside one is never read as code. Newlines are kept.
    """
    def blank(chunk: str) -> str:
        return "".join("\n" if c == "\n" else " " for c in chunk)

    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            end = text.find("}", i)
            end = n if end == -1 else end + 1
            out.append(blank(text[i:end]))
            i = end
        elif text.startswith("(*", i):
            end = text.find("*)", i)
            end = n if end == -1 else end + 2
            out.append(blank(text[i:end]))
            i = end
        elif text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end == -1 else end
            out.append(blank(text[i:end]))
            i = end
        elif ch == "'":
            end = i + 1
            while end < n:
                if text[end] == "'":
                    if text[end + 1:end + 2] == "'":
                        end += 2
                        continue
                    break
                end += 1
            out.append("'" + blank(text[i + 1:end]) + "'")
            i = end + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _scan_unbracketed_comparison(path: str, text: str) -> list[Finding]:
    """A comparison beside Or, And or Xor with no brackets around it."""
    findings: list[Finding] = []
    raw_lines = text.split("\n")
    clean_lines = _blank_comments_and_strings(text).split("\n")
    for line_no, clean in enumerate(clean_lines, 1):
        m = _UNBRACKETED_COMPARISON.search(clean)
        if m:
            findings.append(Finding(
                file=path, line=line_no, col=m.start() + 1,
                rule="unbracketed-comparison", severity="error",
                snippet=raw_lines[line_no - 1],
                memory="delphiscript_api_quirks.md",
            ))
    return findings

# IPCB_Rule.Priority is a read-only function, not a property.
RULE_RULE_PRIORITY_WRITE = LineRule(
    name="rule-priority-readonly-write",
    pattern=re.compile(r"\.Priority\s*:="),
    severity="error",
    memory="altium_rule_priority_readonly.md",
    description="IPCB_Rule.Priority is read-only; assigning crashes the script engine.",
)

# ISch_Probe.Text or .NetName assignments are silently bogus.
RULE_PROBE_TEXT_WRITE = LineRule(
    name="probe-text-write",
    pattern=re.compile(r"\b(Probe|\w*Probe\w*)\.(Text|NetName)\s*:="),
    severity="warn",
    memory="delphiscript_isch_probe_text_unsettable.md",
    description="ISch_Probe.Text/NetName aren't settable; probe auto-picks the net.",
)

# Known-wrong Altium enum identifier names. The SCH/PCB SDK exposes hundreds
# of e* constants so a positive whitelist is unmanageable, but every time
# the script blows up at runtime with "Undeclared identifier: eXxx" we add
# the offender here so the next regression is caught at lint time, not
# after a five-minute Altium restart cycle. Map: wrong name -> what to use
# instead. Extend whenever the script engine surfaces another bogus name.
KNOWN_WRONG_E_IDENTS = {
    # eSchDoc was typed in a SchDoc/SchLib type-guard. There is no
    # eSchDoc constant; SchServer.GetCurrentSchDocument only ever returns
    # a SchDoc or SchLib (or Nil), so the second arm of the check is
    # redundant -- drop it, or test only for eSchLib when you specifically
    # need the library variant.
    "eSchDoc": "drop the check (Nil-guard is enough) or test eSchLib for library docs",
    # eSchDocument: similar mistake; the SDK does not expose this name.
    "eSchDocument": "drop the check or test eSchLib for library docs",
    # ePcbDoc: there is no ObjectId constant for the board itself; if you
    # need to verify you're on a PCB, check IPCB_Board <> Nil.
    "ePcbDoc": "no such constant; verify with PCBServer.GetCurrentPCBBoard <> Nil",
    # eOffSheetConnector: not a real object-id. Altium's "connect to the same
    # net on another sheet" object is the cross-sheet connector. Confirmed
    # Undeclared identifier at runtime in Altium Designer.
    "eOffSheetConnector": "use eCrossSheetConnector; the off-sheet connector is the cross-sheet connector",
    # ePolygonPourOver_Same: not a real value. The TPolygonPourOver enum is
    # ePolygonPourOver_None / _SameNet / _SameNetPolygon(s).
    "ePolygonPourOver_Same": "use ePolygonPourOver_SameNet (or _None / _SameNetPolygon)",
    # eHatchStyle*: not real. The TPolyHatchStyle enum (IPCB_Polygon.PolyHatchStyle)
    # is ePolySolid / ePolyNoHatch / ePolyHatch45 / ePolyHatch90 only.
    "eHatchStyleNone": "use ePolySolid or ePolyNoHatch",
    "eHatchStyle45Degree": "use ePolyHatch45",
    "eHatchStyle90Degree": "use ePolyHatch90",
    "eHatchStyleHorizontal": "no such value; TPolyHatchStyle is Solid/NoHatch/Hatch45/Hatch90",
    "eHatchStyleVertical": "no such value; TPolyHatchStyle is Solid/NoHatch/Hatch45/Hatch90",
    # CoordToMms is a typo for the Utils.pas helper CoordToMM (no trailing 's').
    "CoordToMms": "typo -- the helper is CoordToMM (no trailing 's')",
}
RULE_KNOWN_WRONG_E_IDENT = LineRule(
    name="known-wrong-altium-enum",
    pattern=re.compile(
        r"\b(" + "|".join(re.escape(k) for k in KNOWN_WRONG_E_IDENTS) + r")\b"),
    severity="error",
    memory="delphiscript_altium_enum_typos.md",
    description=(
        "Altium SCH/PCB enum identifier does not exist; the script "
        "engine reports Undeclared identifier at runtime."),
)

# Sibling rule: known-wrong METHOD names on Altium interfaces. Same
# failure mode as the enum typos (Undeclared identifier at runtime,
# uncatchable by Try/Except) but the offender is the .Method bit of
# a member-access expression. Map: wrong name -> right name. Pattern
# requires a leading dot so we don't match unrelated locals.
KNOWN_WRONG_METHOD_NAMES = {
    # ISch_Polyline / ISch_Polygon / ISch_Bezier / ISch_Wire / ISch_Bus
    # expose the vertex-count getter as plural "Vertices". The singular
    # form is what trips people up because the per-vertex getter IS
    # singular: GetState_Vertex(i).
    "GetState_VertexCount": "GetState_VerticesCount (plural)",
    # IClient does not expose a document-count accessor in DelphiScript.
    # To enumerate loaded documents, walk the project DM:
    # `For I := 0 To Project.DM_LogicalDocumentCount - 1`, then resolve
    # each `Project.DM_LogicalDocuments(I).DM_FullPath` via
    # `Client.GetDocumentByPath`.
    "GetDocumentCount":     "walk Project.DM_LogicalDocumentCount + Client.GetDocumentByPath",
    "GetDocument":          "walk Project.DM_LogicalDocuments(I) + Client.GetDocumentByPath",
    # ISch_Document.ClearAllSelection and .ClearSelection both raise
    # "Undeclared identifier" at runtime in this Altium version
    # (uncatchable by Try/Except). The PCB sibling IPCB_Board.ClearSelection
    # IS real -- the false-cognate from there is what tripped this.
    # Sch-side: use RunProcess('Sch:DeSelect') with Scope=All instead.
    # NOTE: existing call sites in Generic.pas:1323/3622/3649 will also
    # fail if those code paths ever fire; left in place because they're
    # not currently exercised and removing them is out of scope here.
    "ClearAllSelection":    "RunProcess('Sch:DeSelect') with Scope=All",
    # IPCB_Polygon's hatch-style property is PolyHatchStyle, not HatchStyle.
    # .HatchStyle raises Undeclared identifier at runtime. (.PolyHatchStyle has
    # the dot before "Poly", so this pattern does not false-match it.)
    "HatchStyle":           "use .PolyHatchStyle (IPCB_Polygon)",
    # IPCB_Board has no GetNetByName; iterate eNetObject. The FindNetByName /
    # EnsureNet helpers in PCB.pas do this. Raised "Undeclared identifier:
    # GetNetByName" at runtime in PCB_PlacePad and five sibling handlers.
    "GetNetByName":         "undeclared on IPCB_Board; use FindNetByName (iterate eNetObject)",
}
RULE_KNOWN_WRONG_METHOD = LineRule(
    name="known-wrong-altium-method",
    pattern=re.compile(
        r"\.(" + "|".join(re.escape(k) for k in KNOWN_WRONG_METHOD_NAMES) + r")\b"),
    severity="error",
    memory="delphiscript_altium_enum_typos.md",
    description=(
        "Altium interface method name is wrong; the script engine "
        "reports Undeclared identifier at runtime."),
)

# Third sibling: qualified property accesses that look right but aren't.
# Same family of bug as the bare-method rule but the offending property
# IS valid on a different interface in the SDK, so we can't blanket-ban
# it -- we deny-list the specific Variable.Property pairs we've hit.
# Each entry: ("VarName.Property", "the right name on this type"). The
# matcher is whole-word for both halves so `MyPad.XSize` won't match
# (which is fine; we add `MyPad.XSize` separately if it shows up).
KNOWN_WRONG_PROPERTY_ACCESSES = {
    # IPCB_Pad has layer-specific dimensions, not a bare XSize/YSize.
    # The default top-layer pair is TopXSize / TopYSize; for inner layers
    # use MidXSize / MidYSize, for bottom BotXSize / BotYSize.
    "Pad.XSize": "Pad.TopXSize (top layer) or per-layer Mid/Bot variant",
    "Pad.YSize": "Pad.TopYSize (top layer) or per-layer Mid/Bot variant",
    # IPCB_Polygon.GeometricPolygon is undeclared in this Altium script
    # binding (raises at runtime) despite being in the API docs: use
    # AreaSize (outline area) + BoundingRectangle, or the geometric polygon
    # of each REGION the pour holds. IPCB_Region.GeometricPolygon IS
    # declared (read and written through a typed IPCB_Region local,
    # measured on AD 25.8), so only the polygon names are listed here.
    "Polygon.GeometricPolygon": "on IPCB_Region only; use .AreaSize or its regions",
    "Poly.GeometricPolygon": "on IPCB_Region only; use .AreaSize or its regions",
    "Pour.GeometricPolygon": "on IPCB_Region only; use .AreaSize or its regions",
}
RULE_KNOWN_WRONG_PROPERTY = LineRule(
    name="known-wrong-altium-property",
    pattern=re.compile(
        r"\b(" + "|".join(
            re.escape(k).replace(r"\.", r"\.") for k in KNOWN_WRONG_PROPERTY_ACCESSES
        ) + r")\b"),
    severity="error",
    memory="delphiscript_altium_enum_typos.md",
    description=(
        "Altium interface property is wrong on this variable's type; "
        "the script engine reports Undeclared identifier at runtime."),
)

# Wrong Altium server process strings. RunProcess accepts any string
# silently, so a typo means the call is a no-op at runtime with no
# error indication -- exactly the case that bit DRC. Per TR0124 Server
# Process Reference, the documented process names are exact. Add
# common-typo entries as we discover them.
KNOWN_WRONG_PROCESS_NAMES = {
    "PCB:RunDRC": "PCB:DesignRuleCheck (per TR0124, PCB:RunDRC is not a real process)",
}
RULE_KNOWN_WRONG_PROCESS = LineRule(
    name="known-wrong-altium-process",
    pattern=re.compile(
        r"['\"](" + "|".join(re.escape(k) for k in KNOWN_WRONG_PROCESS_NAMES)
        + r")['\"]"),
    severity="error",
    memory="delphiscript_altium_enum_typos.md",
    description=(
        "Altium server process name not in the TR0124 reference -- "
        "RunProcess will silently no-op."),
)


# AddFilter_LayerSet expects a MkSet(...) value; an IPCB_LayerSet OBJECT
# (LayerSet.SignalLayers / LayerSet.AllLayers) must go through
# AddFilter_IPCB_LayerSet instead. Passing the object to AddFilter_LayerSet
# raises EVariantTypeCastError (Dispatch->String) at runtime and halts the
# loop. Bit us four times across audit + diff-pair length helpers.
RULE_LAYERSET_OBJECT_FILTER = LineRule(
    name="layerset-object-to-addfilter-layerset",
    pattern=re.compile(r"\.AddFilter_LayerSet\s*\(\s*LayerSet\."),
    severity="error",
    memory="delphiscript_interface_narrowing.md",
    description=(
        "AddFilter_LayerSet(LayerSet.*) passes an IPCB_LayerSet object where a "
        "set is expected -> EVariantTypeCastError. Use AddFilter_IPCB_LayerSet."),
)


LINE_RULES = [
    RULE_EMPTY_LITERAL_ARG,
    RULE_LAYERSET_OBJECT_FILTER,
    RULE_INSERT_INDEX,
    RULE_CLEAR_ON_STRINGLIST,
    RULE_TYPECAST,
    RULE_BAD_HEX,
    RULE_INC_ARRAY,
    RULE_INDEX_ON_CALL_RESULT,
    RULE_RESERVED_IDENT,
    RULE_TYPED_CONSTANT,
    RULE_UNDECLARED_DELPHI_CONST,
    RULE_PCB_SUBTYPE_ON_BASE,
    RULE_FOOTPRINT_NAME_WRITE,
    RULE_RULE_PRIORITY_WRITE,
    RULE_ARC_WIDTH,
    RULE_PROBE_TEXT_WRITE,
    RULE_KNOWN_WRONG_E_IDENT,
    RULE_KNOWN_WRONG_METHOD,
    RULE_KNOWN_WRONG_PROPERTY,
    RULE_KNOWN_WRONG_PROCESS,
]


def _preceded_by_alnum(m: re.Match, line: str) -> bool:
    """Was the match preceded by an alphanumeric -- i.e., it's a longer ident?"""
    if m.start() == 0:
        return False
    prev = line[m.start() - 1]
    return prev.isalnum() or prev == "_"


# ---------------------------------------------------------------------------
# Multi-line / context-sensitive rules.
# ---------------------------------------------------------------------------

# Fixed-size local array inside a Function -- silently corrupts Result.
# Walks blocks and flags `Array[0..N] Of <T>` Var declarations whose
# enclosing block-header is `Function` (not `Procedure`).
_FN_HEADER = re.compile(r"^\s*(Function|Procedure)\b", re.IGNORECASE)
_ARRAY_LOCAL = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*Array\s*\[\s*\d+\s*\.\.\s*\d+\s*\]\s*Of\b",
    re.IGNORECASE,
)


def _scan_fixed_array_in_function(path: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    in_function = False
    in_var = False
    for i, raw in enumerate(lines, 1):
        line = strip_comments_and_strings(raw)
        m = _FN_HEADER.match(line)
        if m:
            in_function = m.group(1).lower() == "function"
            in_var = False
            continue
        if re.match(r"^\s*Var\b", line, re.IGNORECASE):
            in_var = True
            continue
        if re.match(r"^\s*Begin\b", line, re.IGNORECASE):
            in_var = False
            continue
        if in_function and in_var and _ARRAY_LOCAL.match(line):
            findings.append(Finding(
                file=path, line=i, col=1,
                rule="fixed-array-in-function",
                severity="error",
                snippet=raw,
                memory="delphiscript_fixed_string_array_bug.md",
            ))
    return findings


# Freeing a TInterfaceList that held Altium interface refs releases those
# refs through the COM marshaller and faults in oleaut32 (read of FFFFFFFF).
# Working tools (PCB_Scale, CollectSelectedPCBPrims callers) leave the list
# to the script host. Track locals declared `: TInterfaceList` and flag any
# `.Free` on them. Declarations reset at each Function/Procedure header.
_IFACE_LIST_DECL = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:,\s*[A-Za-z_][A-Za-z0-9_]*\s*)*:\s*TInterfaceList\b")


def _scan_interfacelist_free(path: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    iface_vars: set[str] = set()
    in_var = False
    for i, raw in enumerate(lines, 1):
        line = strip_comments_and_strings(raw)
        if re.match(r"^\s*(Function|Procedure)\b", line, re.IGNORECASE):
            iface_vars = set()
            in_var = False
            continue
        if re.match(r"^\s*Var\b", line, re.IGNORECASE):
            in_var = True
            continue
        if re.match(r"^\s*Begin\b", line, re.IGNORECASE):
            in_var = False
            # fall through: Begin can't declare, but Free checks continue
        # Only Var-block locals count. A TInterfaceList received as a
        # PARAMETER is caller-owned -- freeing it would be the caller's
        # bug, and flagging it here would be a false positive.
        if in_var:
            decl = _IFACE_LIST_DECL.search(line)
            if decl:
                names = line.split(":", 1)[0]
                for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", names):
                    iface_vars.add(name)
                continue
        m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\.Free\b", line)
        if m and m.group(1) in iface_vars:
            findings.append(Finding(
                file=path, line=i, col=m.start(1) + 1,
                rule="interfacelist-free",
                severity="error",
                snippet=raw,
                memory="altium_wrong_api_identifier_family.md",
            ))
    return findings


# Case ... Of with eXxx identifiers -- DelphiScript only allows string or
# integer-literal arms. Detect `Case <var> Of` followed within ~20 lines
# by an arm starting with `e[A-Z]`.
def _scan_case_on_enum(path: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    case_start: Optional[int] = None
    for i, raw in enumerate(lines, 1):
        line = strip_comments_and_strings(raw)
        if re.match(r"^\s*Case\b.*\bOf\b", line, re.IGNORECASE):
            case_start = i
            continue
        if case_start is not None:
            if re.match(r"^\s*End\b", line, re.IGNORECASE) or i - case_start > 60:
                case_start = None
                continue
            arm = re.match(r"^\s*(e[A-Z][A-Za-z0-9_]*)\s*:", line)
            if arm:
                findings.append(Finding(
                    file=path, line=i, col=arm.start(1) + 1,
                    rule="case-on-enum-constant",
                    severity="error",
                    snippet=raw,
                    memory="delphiscript_case_on_enum.md",
                ))
                case_start = None
    return findings


# Brace inside another `{ ... }` comment -- closes the comment early and
# breaks surrounding code. Pascal block comments DO NOT nest, so the state
# is binary: either in a `{...}` comment or not. We walk char-by-char,
# masking `(* ... *)` comments, `// ...` line comments, and 'strings' so
# their contents can't trigger.
def _scan_comment_brace(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.split("\n")
    in_brace = False
    in_paren_star = False
    in_string = False
    in_line_comment = False
    line_no = 1
    col = 1
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\n":
            line_no += 1
            col = 1
            in_line_comment = False
            i += 1
            continue
        if in_line_comment:
            i += 1; col += 1
            continue
        if in_paren_star:
            if ch == "*" and nxt == ")":
                in_paren_star = False
                i += 2; col += 2
                continue
            i += 1; col += 1
            continue
        if in_string:
            if ch == "'":
                in_string = False
            i += 1; col += 1
            continue
        if in_brace:
            if ch == "{":
                snippet = lines[line_no - 1] if line_no - 1 < len(lines) else ""
                findings.append(Finding(
                    file=path, line=line_no, col=col,
                    rule="brace-inside-comment",
                    severity="error",
                    snippet=snippet,
                    memory="delphiscript_comment_braces.md",
                ))
            elif ch == "}":
                in_brace = False
            i += 1; col += 1
            continue
        # Outside any comment/string.
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2; col += 2
            continue
        if ch == "(" and nxt == "*":
            in_paren_star = True
            i += 2; col += 2
            continue
        if ch == "{":
            in_brace = True
            i += 1; col += 1
            continue
        if ch == "'":
            in_string = True
            i += 1; col += 1
            continue
        i += 1; col += 1
    return findings


# `If <expr> Then Try ...` -- only dangerous when the Try has more than one
# statement before Except. Single-statement inline forms like
# ``If X Then Try DoOne; Except End;`` compile fine. Count `;` between
# `Try` and the matching `Except`/`End` on the SAME line; flag only when
# more than one statement is between them, or when the block spans lines
# (multi-line inline Try inside an If-Then body is always dangerous).
_IF_THEN_TRY = re.compile(r"\bIf\b.*\bThen\s+Try\b", re.IGNORECASE)


# `{$I file.pas}` and friends: must scan raw text because strip blanks
# block comments. Compiler directives sit INSIDE `{ ... }`, so the
# strip-then-rule pipeline never sees them. Matches `{$I` then non-`}`
# content; reports the line where the `{` opens.
_DOLLAR_I_RE = re.compile(r"\{\s*\$I\b")


def _scan_dollar_i(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    line_no = 1
    pos = 0
    for raw in text.split("\n"):
        m = _DOLLAR_I_RE.search(raw)
        if m:
            findings.append(Finding(
                file=path, line=line_no, col=m.start() + 1,
                rule="dollar-i-include",
                severity="error",
                snippet=raw,
                memory="delphiscript_dollar_i_not_supported.md",
            ))
        line_no += 1
    return findings


# Reserved keyword on its own line inside a `Var` or `Const` block. The
# in-parens parameter form is caught by RULE_RESERVED_IDENT.
_VAR_OR_CONST_HEADER = re.compile(r"^\s*(Var|Const)\b", re.IGNORECASE)
_BLOCK_TERMINATOR = re.compile(
    r"^\s*(Begin|Function|Procedure|Type)\b", re.IGNORECASE)
# Anywhere in the declared name list, not only first: the declaration that
# failed was `Index, Emitted, I : Integer;`, which a first-name-only match
# walked straight past.
_RESERVED_DECL = re.compile(
    r"^\s*(?:\w+\s*,\s*)*\b(" + _RESERVED_ALT + r")\b\s*(?:,|:)",
    re.IGNORECASE)


def _scan_reserved_in_var_block(path: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    in_block = False
    for i, raw in enumerate(lines, 1):
        line = strip_comments_and_strings(raw)
        if _VAR_OR_CONST_HEADER.match(line):
            in_block = True
            continue
        if _BLOCK_TERMINATOR.match(line):
            in_block = False
            continue
        if in_block:
            m = _RESERVED_DECL.match(line)
            if m:
                findings.append(Finding(
                    file=path, line=i, col=m.start(1) + 1,
                    rule="reserved-word-in-var-block",
                    severity="error",
                    snippet=raw,
                    memory="delphiscript_reserved_words.md",
                ))
    return findings


def _scan_if_then_try(path: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for i, raw in enumerate(lines, 1):
        line = strip_comments_and_strings(raw)
        m = _IF_THEN_TRY.search(line)
        if not m:
            continue
        # Find the substring from `Try` to `Except` or `End` on this line.
        tail = line[m.end():]
        except_pos = re.search(r"\b(Except|End)\b", tail, re.IGNORECASE)
        if not except_pos:
            # Multi-line Try inside an If-Then body is always dangerous.
            findings.append(Finding(
                file=path, line=i, col=1,
                rule="if-then-try-multiline",
                severity="error",
                snippet=raw,
                memory="delphiscript_if_then_try.md",
            ))
            continue
        # Count statement terminators inside the Try body.
        body = tail[:except_pos.start()]
        stmts = body.count(";")
        if stmts > 1:
            findings.append(Finding(
                file=path, line=i, col=1,
                rule="if-then-try-multi-stmt",
                severity="error",
                snippet=raw,
                memory="delphiscript_if_then_try.md",
            ))
    return findings


# Call-before-definition within a single unit. DelphiScript resolves
# identifiers strictly top-down and has NO `Forward;` directive (documented
# in Project.pas), so calling a locally-defined Function/Procedure on a line
# above its definition raises "Undeclared identifier" -- and that only
# surfaces after a full Altium restart + recompile cycle. The JSON prop
# builders calling EscapeJsonString (defined lower in Utils.pas) is the
# regression this guards. Cross-unit order is governed by the .PrjScr unit
# list and is out of scope here; we only flag same-file forward references.
_DEF_RE = re.compile(r"^\s*(?:Function|Procedure)\s+([A-Za-z_]\w*)", re.IGNORECASE)

# Delphi/DelphiScript RTL routines that the scripts sometimes RE-DEFINE
# locally (e.g. a defensive StrToIntDef). A call above the local definition
# binds to the built-in, not the not-yet-declared local, so it does NOT
# raise "Undeclared identifier" -- flagging it would be a false positive.
# Lowercased; extend if a redefined built-in trips the call-before-def rule.
_DELPHI_BUILTINS = {
    "length", "copy", "pos", "delete", "insert", "trim", "trimleft",
    "trimright", "uppercase", "lowercase", "stringreplace", "format",
    "inttostr", "strtoint", "strtointdef", "floattostr", "strtofloat",
    "strtofloatdef", "booltostr", "strtobool", "inttohex", "chr", "ord",
    "concat", "comparetext", "comparestr", "sametext", "quotedstr",
    "vartostr", "floattostrf", "abs", "round", "trunc", "int", "frac",
    "sqrt", "sqr", "power", "min", "max", "random", "assigned", "high",
    "low", "sizeof", "varisnull", "vartype", "val",
}


def _scan_call_before_definition(path: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    stripped = [strip_comments_and_strings(l) for l in lines]

    # First definition line per local routine (case-insensitive: Pascal
    # identifiers are case-folded). Store the lowercased name -> (line, orig).
    def_line: dict[str, int] = {}
    for i, line in enumerate(stripped, 1):
        m = _DEF_RE.match(line)
        if m:
            key = m.group(1).lower()
            if key not in def_line:
                def_line[key] = i
    if not def_line:
        return findings

    call_re = re.compile(
        r"(?<![.\w])(" + "|".join(re.escape(n) for n in def_line) + r")\s*\(",
        re.IGNORECASE,
    )
    for i, line in enumerate(stripped, 1):
        header = _DEF_RE.match(line)
        header_key = header.group(1).lower() if header else None
        for m in call_re.finditer(line):
            key = m.group(1).lower()
            if key == header_key:
                continue  # the definition header itself, not a call
            if key in _DELPHI_BUILTINS:
                continue  # early call binds to the built-in, not the local
            if i < def_line[key]:
                findings.append(Finding(
                    file=path, line=i, col=m.start(1) + 1,
                    rule="call-before-definition",
                    severity="error",
                    snippet=lines[i - 1],
                    memory="delphiscript_call_before_definition.md",
                ))
    return findings


# ---------------------------------------------------------------------------
# Comment / string stripping. Cheap state machine: handles `{...}` block
# comments, `(* ... *)` block comments, `// ...` line comments, and
# 'single-quoted' string literals (Pascal escapes doubled-quotes only).
# ---------------------------------------------------------------------------

def strip_comments_and_strings(line: str) -> str:
    """Return ``line`` with comment and string bodies blanked.

    Length is preserved so column numbers stay accurate. We blank with
    spaces inside strings/comments; the rule patterns then can't match
    inside them.
    """
    out = list(line)
    i = 0
    n = len(line)
    in_brace = False
    in_paren_star = False
    in_string = False
    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ""
        if in_brace:
            if ch == "}":
                in_brace = False
            else:
                out[i] = " "
            i += 1
            continue
        if in_paren_star:
            if ch == "*" and nxt == ")":
                in_paren_star = False
                out[i] = " "
                out[i + 1] = " "
                i += 2
                continue
            out[i] = " "
            i += 1
            continue
        if in_string:
            if ch == "'":
                in_string = False
                # Leave the closing quote so a `''` literal still parses
                # textually (we WANT to detect it via the regex).
                i += 1
                continue
            out[i] = " "
            i += 1
            continue
        if ch == "/" and nxt == "/":
            for k in range(i, n):
                out[k] = " "
            break
        if ch == "{":
            in_brace = True
            out[i] = " "
            i += 1
            continue
        if ch == "(" and nxt == "*":
            in_paren_star = True
            out[i] = " "
            out[i + 1] = " "
            i += 2
            continue
        if ch == "'":
            in_string = True
            i += 1
            continue
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def lint_file(path: str) -> list[Finding]:
    rel = os.path.relpath(path, SCRIPT_DIR)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    raw_lines = text.split("\n")
    stripped = [strip_comments_and_strings(l) for l in raw_lines]

    findings: list[Finding] = []
    for i, raw in enumerate(raw_lines, 1):
        clean = stripped[i - 1]
        for rule in LINE_RULES:
            for m in rule.pattern.finditer(clean):
                if rule.confirm and not rule.confirm(m, clean):
                    continue
                findings.append(Finding(
                    file=rel, line=i, col=m.start() + 1,
                    rule=rule.name, severity=rule.severity,
                    snippet=raw, memory=rule.memory,
                ))

    findings.extend(_scan_fixed_array_in_function(rel, raw_lines))
    findings.extend(_scan_interfacelist_free(rel, raw_lines))
    findings.extend(_scan_case_on_enum(rel, raw_lines))
    findings.extend(_scan_if_then_try(rel, raw_lines))
    findings.extend(_scan_reserved_in_var_block(rel, raw_lines))
    findings.extend(_scan_param_redeclared_as_local(rel, raw_lines))
    findings.extend(_scan_dollar_i(rel, text))
    findings.extend(_scan_unbracketed_comparison(rel, text))
    findings.extend(_scan_call_before_definition(rel, raw_lines))
    findings.extend(_scan_undeclared_local(rel, raw_lines))
    findings.extend(_scan_local_shadows_enum(rel, raw_lines))
    # Comment-brace check uses raw text (it tracks comment depth itself).
    findings.extend(_scan_comment_brace(rel, text))

    return findings


def _scan_call_order_across_files(targets: list) -> list:
    """Calls that precede their definition IN THE CONCATENATION.

    The per-file rule only knows the routines defined in the file it is
    reading, so a call into a file that comes LATER in the build order is
    invisible to it: the name is simply not in its table and the line
    passes. That is not a theoretical gap. Library.pas called
    ObjectTypeFromStringPCB, which lived in PCBGeneric.pas, one file later
    in PAS_FILES. Every file linted clean, the build succeeded, and the
    deployed script took the scripting engine down with an access
    violation rather than a compile error, because DelphiScript has no
    forward declarations and an unresolved identifier is only discovered
    when the line runs.

    Altium compiles the concatenation, so that is what this checks. Line
    numbers are reported against the ORIGINAL file, since that is where
    the fix goes.
    """
    order = []
    for path in targets:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        rel = os.path.basename(path)
        for i, raw in enumerate(lines, 1):
            order.append((rel, i, raw, strip_comments_and_strings(raw)))

    def_at = {}
    for pos, (rel, i, raw, line) in enumerate(order):
        m = _DEF_RE.match(line)
        if m:
            key = m.group(1).lower()
            if key not in def_at:
                def_at[key] = pos
    if not def_at:
        return []

    call_re = re.compile(
        r"(?<![.\w])(" + "|".join(re.escape(n) for n in def_at) + r")\s*\(",
        re.IGNORECASE,
    )
    findings = []
    for pos, (rel, i, raw, line) in enumerate(order):
        header = _DEF_RE.match(line)
        header_key = header.group(1).lower() if header else None
        for m in call_re.finditer(line):
            key = m.group(1).lower()
            if key == header_key or key in _DELPHI_BUILTINS:
                continue
            if pos < def_at[key]:
                where = order[def_at[key]][0]
                if where == rel:
                    continue          # the per-file rule already reports it
                findings.append(Finding(
                    file=rel, line=i, col=m.start(1) + 1,
                    rule="call-before-definition-across-files",
                    severity="error",
                    snippet=raw.rstrip() + f"   [{key} is defined in {where}]",
                    memory="delphiscript_call_before_definition.md",
                ))
    return findings


def _scan_duplicate_definitions(targets: list) -> list:
    """A routine defined twice in the concatenation.

    The script compiles with both, and every call goes to the later one.
    A new Proj_GenerateOutput added beside the old one took over its
    command that way: the old export answered with the new routine's
    reply and nothing reported an error.
    """
    first: dict = {}
    findings = []
    for path in targets:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        rel = os.path.basename(path)
        for i, raw in enumerate(lines, 1):
            m = _DEF_RE.match(strip_comments_and_strings(raw))
            if not m:
                continue
            key = m.group(1).lower()
            if key in first:
                where, at = first[key]
                findings.append(Finding(
                    file=rel, line=i, col=1,
                    rule="duplicate-definition",
                    severity="error",
                    snippet=raw.rstrip() + f"   [also defined at {where}:{at}]",
                    memory="bridge_same_command_name_two_handlers.md",
                ))
            else:
                first[key] = (rel, i)
    return findings


def run_lint(files: Optional[Iterable[str]] = None, verbose: bool = True) -> int:
    """Lint every Pascal file. Returns the count of error-level findings."""
    targets = list(files) if files else [
        os.path.join(SCRIPT_DIR, f) for f in PAS_FILES
    ]
    errors = 0
    warns = 0
    by_file: dict[str, list[Finding]] = {}
    for path in targets:
        if not os.path.exists(path):
            continue
        for f in lint_file(path):
            by_file.setdefault(f.file, []).append(f)
            if f.severity == "error":
                errors += 1
            else:
                warns += 1

    for f in (_scan_call_order_across_files(targets)
              + _scan_duplicate_definitions(targets)):
        by_file.setdefault(f.file, []).append(f)
        errors += 1

    if verbose:
        for fname in sorted(by_file):
            for f in by_file[fname]:
                print(f.format())
        scanned = len([t for t in targets if os.path.exists(t)])
        print(f"\nlint: scanned {scanned} files, "
              f"{errors} error(s), {warns} warning(s)")
    return errors


#: A routine's own parameter, declared again as a local.
#:
#: DelphiScript rejects this outright with "Identifier redeclared", and it
#: happens naturally when a function grows a parameter it used to compute
#: for itself: the Var line that used to hold it is easy to leave behind.
#: The compiler catches it, but only after a deploy and a script reload, so
#: the cost is a whole round trip through Altium for a one-word fix.
_ROUTINE_HEAD = re.compile(
    r"^\s*(?:Function|Procedure)\s+\w+\s*\(", re.IGNORECASE)


def _scan_param_redeclared_as_local(path: str, lines: list[str]) -> list[Finding]:
    """Parameters that the routine also declares in its Var block."""
    findings: list[Finding] = []
    i = 0
    while i < len(lines):
        if not _ROUTINE_HEAD.match(strip_comments_and_strings(lines[i])):
            i += 1
            continue

        # The signature can wrap, so collect until the closing paren.
        head, depth, j = "", 0, i
        while j < len(lines):
            piece = strip_comments_and_strings(lines[j])
            head += piece
            depth += piece.count("(") - piece.count(")")
            j += 1
            if depth <= 0 and "(" in head:
                break

        inner = head[head.index("(") + 1:head.rindex(")")] if ")" in head else ""
        params = set()
        for part in inner.split(";"):
            for name in part.split(":")[0].split(","):
                name = name.strip().lstrip("&")
                # `Var`/`Const` prefixes on a by-reference parameter.
                name = re.sub(r"^(?:var|const|out)\s+", "", name,
                              flags=re.IGNORECASE).strip()
                if name.isidentifier():
                    params.add(name.lower())
        if not params:
            i = j
            continue

        # Locals, up to the routine's own Begin.
        in_vars = False
        while j < len(lines):
            line = strip_comments_and_strings(lines[j])
            if re.match(r"^\s*Begin\b", line, re.IGNORECASE):
                break
            if _VAR_OR_CONST_HEADER.match(line):
                in_vars = True
                j += 1
                continue
            if in_vars and ":" in line:
                for name in line.split(":")[0].split(","):
                    name = name.strip()
                    if name.lower() in params:
                        findings.append(Finding(
                            file=path, line=j + 1,
                            col=line.lower().index(name.lower()) + 1,
                            rule="parameter-redeclared-as-local",
                            severity="error",
                            snippet=lines[j],
                            memory="delphiscript_reserved_words.md",
                        ))
            j += 1
        i = j
    return findings


#: Language builtins. A routine that declares a local with one of these
#: names shadows the builtin, which is legal and happens here: Library.pas
#: uses `Length` as a variable in two routines. Without this exclusion the
#: undeclared-local rule reported every OTHER routine that calls the real
#: Length(), which is the opposite of the mistake being hunted.
_BUILTIN_NAMES = {
    "length", "copy", "pos", "trim", "result", "inc", "dec", "round",
    "abs", "chr", "ord", "high", "low", "sqrt", "exit", "break",
    "continue", "assigned", "uppercase", "lowercase", "inttostr",
    "strtoint", "floattostr", "strtofloat", "format", "delete",
    "insert", "concat", "setlength", "sizeof", "true", "false", "nil",
}


def _blank_block_comments(lines: list[str]) -> list:
    """Lines with `{ ... }` comment text replaced by spaces.

    strip_comments_and_strings works a line at a time, so a brace comment
    spanning several lines is only stripped on its first line and the rest
    reads as code. Every remaining false positive from the undeclared-local
    rule was prose inside such a comment being taken for an identifier.

    Line structure and column positions are preserved so a finding still
    points at the right place.
    """
    out, depth = [], 0
    for line in lines:
        buf = []
        i = 0
        while i < len(line):
            ch = line[i]
            if depth == 0 and ch == "{":
                depth += 1
                buf.append(" ")
            elif depth > 0:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                buf.append(" ")
            elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                buf.append(" " * (len(line) - i))
                break
            else:
                buf.append(ch)
            i += 1
        out.append("".join(buf))
    return out


def _routine_spans(lines: list[str]) -> list:
    """(name, first_line_index, begin_index, end_index) for each routine.

    Reuses the same head-matching as the other scans so a routine whose
    signature wraps across lines is still found.
    """
    # NOT _ROUTINE_HEAD: that one requires a '(' because its own caller
    # needs a parameter list. A parameterless routine was therefore not a
    # boundary here, so each span ran on into the next routine and every
    # local of that routine looked undeclared. That was 203 of the first
    # 203 findings.
    #
    # ONLY TOP-LEVEL ROUTINES, and only ones that do not nest. DelphiScript
    # allows a routine inside a routine, and then the OUTER routine's Var
    # block sits after the inner routine rather than before it, so a flat
    # start-to-next-head model attributes the outer's locals to the inner
    # and reports every one of them as undeclared. Representing that
    # properly needs Begin/End depth parsing, and a linter that is wrong
    # about scope is worse than one that says less: a nesting routine is
    # skipped rather than guessed at.
    top_pattern = re.compile(
        r"^(?:Function|Procedure)\s+(\w+)\s*[(;:]", re.IGNORECASE)
    nested_pattern = re.compile(
        r"^\s+(?:Function|Procedure)\s+\w+\s*[(;:]", re.IGNORECASE)
    head_pattern = top_pattern
    spans = []
    heads = [k for k, line in enumerate(lines)
             if top_pattern.match(strip_comments_and_strings(line))]
    for n, start in enumerate(heads):
        stop = heads[n + 1] if n + 1 < len(heads) else len(lines)
        begin = None
        for k in range(start, stop):
            if re.match(r"^\s*Begin\b", strip_comments_and_strings(lines[k]),
                        re.IGNORECASE):
                begin = k
                break
        if begin is None:
            continue
        if any(nested_pattern.match(strip_comments_and_strings(lines[k]))
               for k in range(start, stop)):
            continue
        named = head_pattern.match(strip_comments_and_strings(lines[start]))
        spans.append((named.group(1) if named else "?", start, begin, stop))
    return spans


def _declared_names(lines: list[str], start: int, begin: int) -> set:
    """Parameter and Var-block names visible inside one routine."""
    names = set()
    head = ""
    depth = 0
    for k in range(start, begin + 1):
        piece = strip_comments_and_strings(lines[k])
        head += piece
        depth += piece.count("(") - piece.count(")")
        if depth <= 0 and "(" in head:
            break
    if "(" in head and ")" in head:
        inner = head[head.index("(") + 1:head.rindex(")")]
        for part in inner.split(";"):
            for name in part.split(":")[0].split(","):
                name = re.sub(r"^(?:var|const|out)\s+", "", name.strip(),
                              flags=re.IGNORECASE).strip().lstrip("&")
                if name.isidentifier():
                    names.add(name.lower())

    def take(text: str) -> None:
        for name in text.split(":")[0].split(","):
            name = name.strip()
            if name.isidentifier():
                names.add(name.lower())

    in_vars = False
    for k in range(start, begin):
        line = strip_comments_and_strings(lines[k])
        if _VAR_OR_CONST_HEADER.match(line):
            in_vars = True
            # `Var dx, dy, tt : Double;` puts the declarations on the
            # SAME line as the keyword. Skipping the whole line lost them
            # and reported every use of dx as undeclared.
            rest = re.sub(r"^\s*(?:Var|Const)\b", "", line,
                          flags=re.IGNORECASE)
            if ":" in rest:
                take(rest)
            continue
        if in_vars and ":" in line:
            take(line)
    return names



def _scan_local_shadows_enum(path: str, lines: list[str]) -> list[Finding]:
    """A local or parameter whose name is an enum constant in other case.

    DelphiScript names are case-insensitive, so a local ``EArc`` IS
    ``eArc`` inside its routine. Gen_MirrorSchComponent declared
    ``EArc : ISch_EllipticalArc`` and compared ``Kind = eArc``: the
    comparison read the unassigned local (0), never matched, and every
    arc of a mirrored symbol stayed where it was while the call reported
    it skipped as kind 8. Nothing raises; the constant is simply gone in
    that routine.

    Flags a use spelled like an enum constant (``e`` then a capital) whose
    name the routine declares in a different case. A local declared with
    the same spelling as its uses is the local, and is left alone.

    Line by line, not through _blank_block_comments: that one reads the
    brace in a string such as '{"designator":' as a comment opening and
    blanked the very lines this rule has to see.
    """
    findings: list[Finding] = []
    for _name, start, begin, stop in _routine_spans(lines):
        declared = _declared_names(lines, start, begin)
        if not declared:
            continue
        header = " ".join(strip_comments_and_strings(lines[k])
                          for k in range(start, begin))
        spelled = set(re.findall(r"\b\w+\b", header))
        for k in range(begin, stop):
            text = strip_comments_and_strings(lines[k])
            for m in re.finditer(r"\be[A-Z]\w*", text):
                tok = m.group(0)
                if tok.lower() in declared and tok not in spelled:
                    findings.append(Finding(
                        file=path, line=k + 1, col=m.start() + 1,
                        rule="local-shadows-enum", severity="error",
                        snippet=lines[k],
                        memory="delphiscript_reserved_words.md",
                    ))
    return findings

def _scan_undeclared_local(path: str, lines: list[str]) -> list[Finding]:
    """An identifier used as a local that this routine never declares.

    THE FAILURE IS SILENT UNTIL IT RUNS. DelphiScript resolves late, so
    an undeclared identifier compiles and then faults the moment the line
    executes, taking the polling loop with it.

    MEASURED: Lib_AddFootprintPads passed bare `Board` to ResolveLayerId
    while every other call site in the same file passed `PcbLib.Board`.
    The routine never declared `Board`, the linter was silent, and the
    script halted on that line in Altium.

    THE RULE IS DELIBERATELY NARROW. It only considers a name that this
    same file declares as a local in at least two OTHER routines, which
    makes it a name the file clearly treats as routine-scoped rather than
    a global or an Altium builtin. That keeps it to the real confusion
    (a variable someone forgot to declare or assign here) and away from
    the late-bound API surface, where deciding what exists is not
    something a linter can do.
    """
    findings: list[Finding] = []
    # Block comments blanked first: prose inside a multi-line `{ ... }`
    # was being read as code and was every remaining false positive.
    lines = _blank_block_comments(lines)
    spans = _routine_spans(lines)
    if not spans:
        return findings

    declared_by = {}
    per_routine = {}
    for name, start, begin, stop in spans:
        names = _declared_names(lines, start, begin)
        per_routine[(name, start)] = (names, begin, stop)
        for n in names:
            declared_by.setdefault(n, set()).add((name, start))

    # Names this file treats as routine-scoped, minus anything that is
    # also a language builtin. Two routines in Library.pas declare a
    # variable called `Length`, which shadows the builtin, and without
    # this every other routine that calls Length(...) was reported.
    local_names = {n for n, owners in declared_by.items()
                   if len(owners) >= 2 and n not in _BUILTIN_NAMES}
    if not local_names:
        return findings

    for key, (names, begin, stop) in per_routine.items():
        routine, start = key
        missing = local_names - names
        if not missing:
            continue
        for k in range(begin, stop):
            line = strip_comments_and_strings(lines[k])
            for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", line):
                word = m.group(0)
                low = word.lower()
                if low not in missing:
                    continue
                # THE POSITION OF THIS MATCH, not the first place the
                # substring appears. Using find() located 'Name' inside
                # 'ThisName', read the preceding character as 's' rather
                # than '.', and reported 401 findings that were almost
                # all this one mistake.
                idx = m.start()
                # `Something.Board` is a property access, not this local.
                # `$F` is a hex literal, not an identifier called F.
                # `&Type` is an escaped reserved word.
                if idx > 0 and line[idx - 1] in ".$&":
                    continue
                findings.append(Finding(
                    file=path, line=k + 1, col=idx + 1,
                    rule="undeclared-local",
                    severity="error",
                    snippet=lines[k].rstrip(),
                    memory="altium_wrong_api_identifier_family.md",
                ))
                break
    return findings


if __name__ == "__main__":
    sys.exit(1 if run_lint() > 0 else 0)
