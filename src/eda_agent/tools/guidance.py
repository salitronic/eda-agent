# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Task-oriented guidance: which tool, on which document, and what bites.

``tool_catalog`` answers "what is this tool called". It cannot answer
"what is the right approach for this document kind", and that is the
question that has actually been getting wrong answers.

The failures this exists for were all the same shape: a capability was
reported ABSENT when it was present, or a board tool was aimed at a
library. Both produce a confident wrong answer rather than an error, so
neither is self-correcting.

The three answers a caller needs are kept distinct, because collapsing
them is what makes the wrong ones convincing:

* here is the tool, and here is what it needs first
* the tool you were reaching for acts on a DIFFERENT document
* this is genuinely not possible, and here is why

The third matters as much as the first. Without it there is no way to
distinguish "you missed it" from "it does not exist", so the same dead
end gets investigated again every time it comes up.

Tool names here are checked against the live surface by
``tests/test_tool_guide_names_real_tools.py``. A recipe that names a
tool which has been renamed or dropped fails there rather than sending
a caller somewhere empty.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: Document kinds a recipe can apply to. ``any`` means it does not turn
#: on which document is in front.
DOCUMENT_KINDS = ("library", "board", "schematic", "project", "any")


#: Each recipe: what you are trying to do, the tools that do it, and the
#: tools that LOOK like they do it but act on another document.
#:
#: ``avoid`` is the load-bearing field. Naming the right tool only helps
#: a caller who is already looking here; naming the wrong one by the
#: reason it is wrong is what makes a recipe findable from the mistake.
_RECIPES: tuple[dict[str, Any], ...] = (
    {
        "task": "Delete primitives inside a footprint",
        "keywords": ("delete", "primitive", "footprint", "silkscreen",
                     "track", "clear", "library"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_delete_footprint_primitives"],
        "avoid": [
            ("obj_delete", "resolves a BOARD, not the library in front"),
            ("pcb_delete_object", "resolves a BOARD, not the library"),
        ],
        "note": (
            "Both board tools open the first PcbDoc an open project holds "
            "when none is focused, so aimed at a library they do not fail. "
            "They remove primitives from a board you never named. Pads are "
            "excluded unless include_pads is set, since removing one "
            "changes the pinout rather than the drawing."),
    },
    {
        "task": "Set mechanical layer names and kinds on a BOARD",
        "keywords": ("mechanical", "layer", "mechkind", "kind", "rename",
                     "pair", "paired", "board"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_mech_layer_names", "pcb_set_mech_layers",
                "pcb_set_mech_layer_kind"],
        "avoid": [
            ("lib_set_mech_layers", "refuses a PcbDoc: it is the LIBRARY "
                                    "one. Use pcb_set_mech_layers here"),
        ],
        "note": (
            "Read the board first with pcb_get_mech_layer_names: it reports "
            "what each layer actually HOLDS, which a PcbDoc header does not "
            "carry, so kinds can be moved with the geometry in view. Paired "
            "kinds come from a SECOND enumeration with no side suffix and "
            "are written on the pair, not on either layer. Writing a paired "
            "kind as though it were a layer kind silently does nothing."),
    },
    {
        "task": "Set mechanical layer names and kinds in a LIBRARY",
        "keywords": ("mechanical", "layer", "mechkind", "kind", "rename",
                     "pair", "paired", "library", "pcblib"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_set_mech_layers", "lib_run_across"],
        "avoid": [
            ("pcb_set_mech_layers", "acts on a BOARD. In a library use "
                                    "lib_set_mech_layers"),
        ],
        "note": (
            "lib_run_across applies the same layer setup to every library "
            "in a folder in one call. Paired kinds are written on the pair "
            "rather than on either layer here too."),
    },
    {
        "task": "Delete a parameter from a library symbol",
        "keywords": ("parameter", "delete", "remove", "symbol", "schlib",
                     "library"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["obj_delete"],
        "avoid": [],
        "note": (
            "Scope it with lib_component:NAME. A parameter is owned by its "
            "component, so a document-level delete is a silent no-op, and a "
            "SchLib holds no PLACED components for a component walk to "
            "find. Scoping resolves the symbol first, which is what makes "
            "the delete reach it."),
    },
    {
        "task": "Read or change one part of a multi-part symbol",
        "keywords": ("multi-part", "multipart", "part", "gate", "section",
                     "symbol", "pins", "electrical"),
        # gate and section were keywords before; these make them trigger
        # the recipe on their own again, as the table always intended.
        "when": (("gate",), ("section",), ("multi", "part"), ("multipart",)),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["obj_query", "obj_modify", "lib_get_pin_list",
                "sch_set_component_part_id"],
        "avoid": [],
        "note": (
            "Suffix the scope with @N, as lib_component:NAME@2, for both "
            "obj_query and obj_modify. A SchLib iterator only ever yields "
            "the part the editor is DISPLAYING, so without the suffix a read "
            "or a write reaches whichever part happens to be showing, not "
            "part 1. lib_get_pin_list reads every part at once and is the "
            "check to run afterwards. Changing a pin's electrical type on a "
            "part the editor is not showing is obj_modify with the @N scope "
            "and a filter on the pin's Designator; it does not need a "
            "rebuild."),
    },
    {
        "task": "Place a part from a library onto a schematic",
        "keywords": ("place", "part", "component", "symbol", "library",
                     "intlib", "schlib", "sheet", "schematic"),
        "when": (("place", "library"), ("place", "intlib"),
                 ("place", "schlib"), ("place", "part")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_place_components", "design_execute_plan",
                "lib_get_components"],
        "avoid": [
            ("app_run_ui_command", "driving Altium's own dialogs through UI automation is a last resort, not a route; it is how placement ended in a crash"),
            ("app_click_menu", "driving Altium's own dialogs through UI automation is a last resort, not a route; it is how placement ended in a crash"),
        ],
        "note": (
            "Each placement is a dict with library_path, lib_reference, x, "
            "y and optionally designator, rotation, footprint. The key is "
            "library_path: source_library and lib_ref are not read, and "
            "the call now refuses them by name rather than failing with "
            "LOAD_FAILED. library_path may be the .SchLib or the .IntLib; "
            "an .IntLib is resolved to its source .SchLib, including the "
            "library-package layout where it compiles into a Project "
            "Outputs folder. Adding parts to a sheet that exists is this "
            "tool; drawing a whole sheet is design_execute_plan, which "
            "places and routes to measured conventions."),
    },
    {
        "task": "Delete an object placed on a schematic",
        "keywords": ("delete", "text", "frame", "textframe", "note",
                     "label", "wire", "junction", "rectangle", "image",
                     "probe", "object"),
        "when": (("delete", "text"), ("delete", "frame"),
                 ("delete", "note"), ("delete", "label"),
                 ("delete", "wire"), ("delete", "object")),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["obj_delete", "obj_query"],
        "avoid": [
            ("sch_place_text_frame", "CREATES a text frame; obj_delete "
                                     "with object_type eTextFrame removes one"),
            ("sch_place_note", "CREATES a note; obj_delete with "
                               "object_type eNote removes one"),
            ("app_canvas", "driving Altium's own dialogs through UI automation is a last resort, not a route; it is how placement ended in a crash"),
        ],
        "note": (
            "obj_delete with object_type and a filter, e.g. eTextFrame, "
            "eNote, eNetLabel, eWire, eJunction, eImage, eProbe. Every "
            "type a sch_place_ tool can create is also addressable here. "
            "Query first with obj_query to see what the filter will match, "
            "because a delete reports how many it removed, not which."),
    },
    {
        "task": "Fix Not Found parts, or set Design Item ID across a library",
        "keywords": ("design", "item", "id", "designitemid", "found",
                     "relink", "provenance", "source", "stale", "sync"),
        "when": (("item", "id"), ("not", "found"), ("found", "library"),
                 ("relink",)),
        "document_kind": "any",
        "backends": ("altium",),
        "use": ["lib_clear_source_library", "sch_clear_source_library",
                "lib_normalize_implementations"],
        "avoid": [
            ("lib_rename_component", "changes LibReference, which placed "
                                     "designs link through, and recreates "
                                     "the Not Found state it was meant to fix"),
            ("lib_batch_rename", "changes LibReference; same trap as "
                                 "lib_rename_component"),
            ("app_set_dialog_control", "the Properties panel's Design Item ID "
                                       "field is one symbol at a time and may "
                                       "rename the component; driving Altium's own dialogs through UI automation is a last resort, not a route; it is how placement ended in a crash"),
        ],
        "note": (
            "DesignItemId is the library item a placed part re-matches "
            "against, and a stale one after a re-link is what shows as Not "
            "Found. lib_clear_source_library sets DesignItemId to the "
            "LibReference on every symbol in a library in one call, and "
            "leaves LibReference untouched, so placed designs keep "
            "linking. sch_clear_source_library does the same on placed "
            "parts, sheet by sheet. Both always clear SourceLibraryName. "
            "Run it on a copy of the library first, and check total in the "
            "reply before trusting the synced count."),
    },
    {
        "task": "Mirror or flip a placed schematic component",
        "keywords": ("mirror", "mirrored", "ismirrored", "flip",
                     "horizontal", "horizontally"),
        "when": (("mirror",),),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_mirror_component"],
        "avoid": [
            ("obj_modify", "setting IsMirrored alone moves nothing: the "
                           "canvas draws from the primitives' coordinates, "
                           "and the flag reads back as set while the part "
                           "stays unmirrored"),
        ],
        "note": (
            "sch_mirror_component reflects the primitives about the "
            "component anchor, flips pin orientation, moves the designator "
            "and comment, and sets the flag, which is what the editor's own "
            "mirror does. Calling it twice returns the part to where it "
            "started. Arcs are reported in skipped_kinds, not moved."),
    },
    {
        "task": "Run DRC or ERC",
        "keywords": ("drc", "erc", "rules", "check", "violations"),
        "document_kind": "any",
        "backends": ("altium", "kicad", "easyeda"),
        "use": ["run_drc", "run_erc"],
        # Scoped: app_run_menu is an Altium tool, and offering it as a
        # trap on a backend that has no such tool teaches a distinction
        # that does not exist there.
        "avoid": [
            ("app_run_menu", "the menu path reports success for a run that "
                             "did nothing, and returns no violations",
             ("altium",)),
        ],
        "note": (
            "The dedicated tools validate the document context and return "
            "the violation list. run_drc and run_erc are the EDA-agnostic "
            "pair and work on every backend."),
    },
    {
        "task": "Suppress or restore stencil paste on Not-Fitted parts",
        # Not "variant": one keyword is enough to offer a recipe, so this
        # one led the answer to every variant question, including "delete
        # a project variant". A real stencil question says paste, stencil,
        # DNP or fitted.
        "keywords": ("paste", "stencil", "dnp", "not-fitted", "notfitted",
                     "aperture", "restore"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["audit_variant_not_fitted", "pcb_apply_dnp_paste_exclusion"],
        "avoid": [],
        "note": (
            "Restore refuses to guess: call it with the SAME designators you "
            "applied. Resolving the list from the current variant means you "
            "can exclude under one variant, switch, and leave a component "
            "that is fitted in the new one with its aperture still "
            "suppressed. Nothing reports that. The call succeeds, the pad "
            "looks ordinary in the editor, and the part comes back from "
            "assembly unsoldered."),
    },
    {
        "task": "Place a power port for a rail",
        "keywords": ("power", "port", "rail", "vcc", "gnd", "ground",
                     "orientation", "symbol"),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_place_power_port", "audit_power_port_orientation"],
        "avoid": [],
        "note": (
            "Pass orientation explicitly for a rail drawn with style=bar. "
            "The style-based default groups bar and wave with the grounds, "
            "so a VCC bar comes out pointing DOWN and reads as a ground "
            "symbol. orientation=1 is what a rail wants."),
    },
    {
        "task": "Rename a PCB component designator",
        "keywords": ("designator", "rename", "annotate", "renumber"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["proj_annotate"],
        "avoid": [
            ("obj_modify", "writing a designator on a PCB component from a "
                           "script CRASHES Altium"),
        ],
        "note": (
            "Annotation is the supported route and keeps the schematic and "
            "board in step, which a direct rename would not."),
    },
)


#: Proven not possible, with the reason. Kept separate from the recipes
#: because "no tool exists" and "no route exists" call for different
#: responses: the first is a gap worth filling, the second is a dead end
#: worth remembering.
_NOT_POSSIBLE: tuple[dict[str, Any], ...] = (
    {
        "capability": "Rename a PCB designator directly from a script",
        "backends": ("altium",),
        "why": "Writing the designator on a PCB component crashes Altium.",
        "do_instead": ["proj_annotate"],
    },
    {
        "capability": "Apply an ECO without a human click",
        "backends": ("altium",),
        "why": ("The Engineering Change Order dialog is non-suppressible by "
                "design. It can be launched, not completed."),
        "do_instead": [],
    },
    {
        "capability": "Use hierarchical sheets in a schematic",
        "backends": ("easyeda",),
        "why": ("There is no hierarchy. Schematic PAGES are siblings with "
                "an order, and no class exposes a sheet symbol, a sheet "
                "entry or a parent link. A netlist here is flat because "
                "nothing is nested, not because anything was flattened, so "
                "a report describing an EasyEDA design as hierarchical is "
                "wrong. Reuse happens through circuit blocks instead."),
        "do_instead": ["easyeda_list_schematic_pages",
                       "easyeda_search_circuit_blocks"],
    },
    {
        "capability": "Place a reusable circuit block onto a schematic",
        "backends": ("easyeda",),
        "why": ("Documented and not present. lib_Cbb is fully available at "
                "runtime, so blocks can be found and managed, but "
                "sch_PrimitiveComponent.placeCbbSchematicPage is absent "
                "from the captured surface of editor 2.2.47.7 even though "
                "the reference specifies it. A newer build may carry it: "
                "this is a build gap, not an API gap."),
        "do_instead": ["easyeda_search_circuit_blocks"],
    },
    {
        "capability": "Add or remove teardrops",
        "backends": ("easyeda",),
        "why": ("No teardrop method exists. Checked in BOTH sources, "
                "because neither alone is conclusive: absent from the "
                "official class reference, and absent from a 675-method "
                "runtime capture. The only mention anywhere is a gerber "
                "export option, which is a rendering choice rather than "
                "an edit to the board."),
        "do_instead": [],
    },
    {
        "capability": "Place thieving copper",
        "backends": ("easyeda",),
        "why": ("No thieving method in the class reference or in the "
                "675-method runtime capture."),
        "do_instead": [],
    },
    {
        "capability": "Tune track length with a serpentine",
        "backends": ("easyeda",),
        "why": ("No length-tuning method in either source. Length "
                "MATCHING exists as a CONSTRAINT through pcb_Drc's "
                "equal-length net groups, which is the rule rather than "
                "the detour: the editor can be told which nets must "
                "match, and cannot be asked to draw the serpentine that "
                "makes them."),
        "do_instead": ["easyeda_create_length_match_group",
                       "easyeda_add_nets_to_length_match_group"],
    },
    {
        "capability": "Create test points on the board",
        "backends": ("easyeda",),
        "why": ("Only the EXPORT exists: getTestPointFile writes the "
                "flying-probe file for whatever the board already has. "
                "Nothing creates a test point, in either source."),
        "do_instead": ["easyeda_export_test_points"],
    },
    {
        "capability": "Delete a whole project",
        "backends": ("easyeda",),
        "why": ("EasyEDA's API has no project delete. dmt_Project offers "
                "create, open, move and the info reads, and no other class "
                "deletes one. The document tree can remove a board, folder, "
                "panel, PCB, schematic or schematic page, never the project."),
        "do_instead": ["easyeda_delete_schematic", "easyeda_delete_pcb",
                       "easyeda_delete_panel"],
    },
    {
        "capability": "Run DRC or ERC over the IPC API",
        "backends": ("kicad",),
        "why": ("KiCad's IPC API does not expose them. They run through "
                "kicad-cli instead, which this server drives for you."),
        "do_instead": ["run_drc", "run_erc"],
    },
)


#: Words that carry no subject. Without this a question matches any
#: entry whose prose happens to contain "the", which turned "calibrate
#: the coffee machine" into a hit on an unrelated dead end. A guide that
#: answers questions it was not asked is worse than one that misses,
#: because the answer looks authoritative.
_STOPWORDS = frozenset((
    "the", "and", "for", "with", "from", "into", "onto", "that", "this",
    "how", "can", "you", "was", "are", "does", "did", "any", "all",
    "get", "set", "use", "using", "make", "want", "need", "should",
    "many", "every", "each", "some", "without", "after", "before", "new",
    "one", "its", "their", "have", "has", "not",
    # Two-letter function words. "id" and "3d" are deliberately absent:
    # both are real subjects ("design item id", "3d body").
    "to", "of", "on", "in", "is", "it", "by", "at", "or", "an", "as",
    "be", "if", "my", "me", "do", "so", "up", "we", "us",
))


#: Words that name the WORLD rather than the task. Every question about a
#: library mentions "library"; every question about a board mentions
#: "board". Letting one of these alone qualify a curated recipe is what
#: made "place a 3d step model on the board" answer with the board
#: mechanical-layers recipe, and "set the design item id on many library
#: components" answer with the library one. They still count as a
#: tie-break, never as the reason a recipe is offered.
_GENERIC = frozenset((
    "board", "library", "schlib", "pcblib", "intlib", "symbol", "part",
    "component", "sheet", "schematic", "document", "project", "design",
    "place", "delete", "remove", "add", "create", "change", "edit",
    "update", "name", "rename", "kind", "type", "pin", "property",
    "object", "tool", "file", "value",
))


#: Words a caller uses for something the tools call differently. Every
#: entry is justified by a question that missed. Adding a word to pass a
#: held-out paraphrase is how a guide learns its test instead of its job,
#: so when put, reflect and duplicate were added for three held-out
#: misses, those questions moved into the tuned set and a NEW held-out
#: set was written first, and scored, before the change.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "put": ("place",),
    "reflect": ("mirror",),
    "duplicate": ("copy",),
    "flip": ("mirror",),
    "erase": ("delete",),
    "remove": ("delete",),
    "note": ("text", "frame"),
    "textbox": ("text", "frame"),
    "notfound": ("not", "found"),
    "relink": ("link", "library"),
    # "bulk change a property on every symbol" missed every batch_ tool,
    # because the tools say batch and the caller said bulk.
    "bulk": ("batch",),
}


def _stem(word: str) -> str:
    """Fold the plurals that decide matches: parts, symbols, libraries."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> set[str]:
    """Whole words, camelCase and snake_case split, plurals folded.

    WHOLE WORDS, because substring matching found "name" inside "rename"
    and offered the mechanical-layers recipe for copying a symbol under a
    new name. CAMELCASE SPLIT, because docstrings say ``DesignItemId``
    and a caller types "design item id", and the two never met.
    """
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    out: set[str] = set()
    for raw in re.split(r"[^A-Za-z0-9]+", text.lower()):
        if len(raw) < 2 or raw in _STOPWORDS:
            continue
        out.add(_stem(raw))
    return out


def _query_tokens(task: str) -> set[str]:
    """The caller's words, plus the words the tools use for the same thing."""
    words = _tokens(task)
    joined = re.sub(r"[^a-z0-9]+", "", (task or "").lower())
    for key, extra in _SYNONYMS.items():
        if key in words or (len(key) > 6 and key in joined):
            words.update(extra)
    return words


_DELETE_VERBS = frozenset(("delete", "remove", "erase", "clear"))
_CREATE_VERBS = frozenset(("place", "add", "create", "put", "insert"))


def _direction_penalty(query: set[str], tool_words: set[str]) -> int:
    """A question about removing something is not answered by a creator.

    Asked to delete a text frame, the docstring search ranked
    sch_place_text_frame first: it matched "text" and "frame" better than
    anything that deletes. The verb decides which way the answer faces.
    """
    wants_delete = bool(query & _DELETE_VERBS)
    wants_create = bool(query & _CREATE_VERBS)
    if wants_delete and not wants_create and tool_words & _CREATE_VERBS:
        return 1
    if wants_create and not wants_delete and tool_words & _DELETE_VERBS:
        return 1
    return 0


def _score(recipe: dict, task: str, document_kind: str) -> int:
    """How much of the question this recipe accounts for. 0 means no.

    Ranked rather than first-match: "delete a component parameter" in a
    library hits both the parameter recipe and the footprint-primitive
    one on the word "delete", and returning whichever comes first in the
    table produced a confident WRONG answer, which is the exact failure
    this module exists to prevent. The subject words have to outweigh
    the verb, so a keyword hit counts for more than a tool-name hit.
    """
    if document_kind and recipe["document_kind"] not in (document_kind, "any"):
        return 0
    if not task:
        return 1
    words = _query_tokens(task)
    if not words:
        return 1

    # A DEAD END is matched on its title, generic words included: it has
    # no keyword list, and "Delete a whole project" is made of nothing
    # but world words. It needs two title words AND at least half the
    # title, because a spurious dead end is the most damaging answer this
    # guide can give: it tells the caller that something possible is not.
    if recipe.get("dead_end"):
        title = _tokens(recipe["task"])
        overlap = words & title
        if len(overlap) >= 2 and len(overlap) * 2 >= len(title):
            return 3 * len(overlap)
        return 0

    keywords: set[str] = set()
    for k in recipe["keywords"]:
        keywords |= _tokens(k)
    title = _tokens(recipe["task"])

    # A recipe is OFFERED only on a word that is specific to it. A
    # generic word ("library", "board", "place") shared with the question
    # used to be enough, and produced confident wrong answers: the
    # mechanical-layers recipe for placing a part, for copying a symbol,
    # and for setting a Design Item ID.
    specific = {w for w in words if w not in _GENERIC}
    specific_hits = specific & (keywords | title)

    # ``when`` patterns are for tasks that are made ENTIRELY of world
    # words. "Place a part from a library" contains nothing specific, so
    # the rule above could never offer it; a pattern names the
    # combination that does mean it, which one generic word never does.
    by_pattern = any({_stem(w) for w in pattern} <= words
                     for pattern in recipe.get("when", ()))

    if not by_pattern and not specific_hits:
        return 0

    score = 3 * len(specific_hits & title) + 3 * len(specific_hits & keywords)
    # Generic words break ties between recipes that both qualified.
    score += len((words - specific) & (keywords | title))
    if by_pattern:
        score += 6
    return score


def _matches(recipe: dict, task: str, document_kind: str) -> bool:
    return _score(recipe, task, document_kind) > 0


def avoid_entries(recipe: dict, backend: str = ""):
    """The ``avoid`` list for one recipe, narrowed to a backend.

    An entry may carry its own backend tuple as a third element. Without
    that narrowing a trap belonging to one editor is presented on every
    editor, which is the same wrong-tool-for-this-context error the
    guide exists to prevent.
    """
    out = []
    for entry in recipe["avoid"]:
        name, why = entry[0], entry[1]
        scope = entry[2] if len(entry) > 2 else recipe["backends"]
        if backend and backend not in scope:
            continue
        out.append({"tool": name, "why": why})
    return out


#: Phrases a docstring uses to send the reader somewhere else. Each is
#: a recipe that somebody already wrote next to the code, which is the
#: only place a recipe cannot go stale: the redirect and the tool it
#: redirects to are edited together or not at all.
_REDIRECT_MARKERS = (
    "use this rather than",
    "use this instead of",
    "prefer ",
    " instead of ",
    " rather than ",
    "this is the library tool",
    "acts on an open",
)

#: Written like a tool name. Used to pull the target out of a redirect
#: sentence rather than guessing which words are tools.
_TOOLNAME = re.compile(r"(?<![a-z0-9_])((?:app|lib|pcb|sch|obj|proj|design|audit|run|part"
                       r"|easyeda|kicad|sim|route|tool)_[a-z0-9_]+)")


def derived_recipes(task: str, docs: dict[str, str],
                    limit: int = 6) -> list[dict[str, Any]]:
    """Recipes read off the tool docstrings, for what the table misses.

    THE HAND-WRITTEN TABLE IS TEN ENTRIES AGAINST SEVEN HUNDRED TOOLS,
    and an empty answer from it reads as "no such capability" however
    carefully the note says otherwise. That reading has been made and
    reported more than once.

    Docstrings already carry the redirects: "use this rather than
    lib_link_3d_model", "this is the LIBRARY tool", "acts on an open
    .PcbDoc". They are maintained because they sit next to the code
    they describe, which is exactly what a separate list of ten is not.

    So a miss falls back to reading them. This finds fewer, vaguer
    answers than a curated recipe, and it finds them for every tool
    rather than for ten.
    """
    words = _query_tokens(task)
    if not words or not docs:
        return []
    specific = {w for w in words if w not in _GENERIC}

    out: list[tuple[int, str, dict[str, Any]]] = []
    for name, doc in docs.items():
        if not doc:
            continue
        name_words = _tokens(name)
        summary = _tokens((doc.strip().splitlines() or [""])[0])
        body = _tokens(doc)
        # The name counts most, the first line next, the body least: a
        # tool called pcb_place_3d_body answers "place a 3d body" better
        # than one whose tenth paragraph mentions it. Specific words
        # count double, so "library" cannot outvote "mirror".
        score = 0
        for w in words:
            weight = 2 if w in specific else 1
            if w in name_words:
                score += 4 * weight
            elif w in summary:
                score += 2 * weight
            elif w in body:
                score += 1 * weight
        # Divided, not subtracted. A fixed deduction lost to a strong name
        # match: sch_place_text_frame kept first place for "delete a text
        # frame" because "text" and "frame" outweighed any constant.
        if _direction_penalty(words, name_words):
            score //= 3
        if score <= 0:
            continue

        redirects: list[str] = []
        for line in doc.splitlines():
            ll = line.lower()
            if any(m in ll for m in _REDIRECT_MARKERS):
                for other in _TOOLNAME.findall(line):
                    if other != name and other not in redirects:
                        redirects.append(other)
        out.append((score, name, {
            "use": [name],
            "summary": (doc.strip().splitlines() or [""])[0].strip(),
            "see_also": redirects,
        }))

    out.sort(key=lambda t: (-t[0], t[1]))
    return [r for _s, _n, r in out[:limit]]


def guidance_for(task: str = "", document_kind: str = "",
                 backend: str = "",
                 docs: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """The recipes and dead ends matching one question. Pure, testable."""
    task = (task or "").strip()
    document_kind = (document_kind or "").strip().lower()
    backend = (backend or "").strip().lower()

    if document_kind and document_kind not in DOCUMENT_KINDS:
        return {"ok": False, "reason": (
            f"document_kind must be one of {', '.join(DOCUMENT_KINDS)}")}

    def on_backend(entry) -> bool:
        return not backend or backend in entry["backends"]

    ranked = sorted(
        ((_score(r, task, document_kind), i, r)
         for i, r in enumerate(_RECIPES) if on_backend(r)),
        key=lambda t: (-t[0], t[1]))
    recipes = []
    for score, _i, r in ranked:
        if score <= 0:
            continue
        # Copied, not mutated: the module-level table is shared by every
        # caller and narrowing it in place would leak one request's
        # backend filter into the next.
        recipes.append(dict(r, avoid=avoid_entries(r, backend)))
    scored_dead = []
    for i, d in enumerate(_NOT_POSSIBLE):
        if not on_backend(d):
            continue
        shim = {"task": d["capability"], "keywords": (),
                "use": d["do_instead"], "document_kind": "any",
                "dead_end": True}
        score = _score(shim, task, "")
        if score > 0:
            scored_dead.append((score, i, d))
    scored_dead.sort(key=lambda t: (-t[0], t[1]))
    dead_ends = [d for _s, _i, d in scored_dead]

    # ALWAYS, not only when the curated table missed. The old rule was
    # sound in principle and wrong in practice: a curated recipe sharing
    # one common word counted as a hit, so the docstring search almost
    # never ran, and the tool that actually answered the question was
    # never shown. Curated recipes still come first in the reply, which
    # is what "a hand-written recipe is the better answer" requires; they
    # just no longer hide everything else. Tools already named by a
    # curated recipe are not repeated.
    derived: list[dict[str, Any]] = []
    if task and docs:
        named = {t for r in recipes for t in r.get("use", [])}
        derived = [d for d in derived_recipes(task, docs)
                   if not set(d.get("use", [])) <= named]

    return {
        "ok": True,
        "matched": len(recipes),
        "recipes": recipes,
        "not_possible": dead_ends,
        "derived": derived,
        # An empty result is a real answer and must not read as an error.
        # It means this file has nothing on the subject, NOT that the
        # server cannot do it: fall back to tool_catalog.
        "note": ("No curated recipe covers this. `derived` is read off "
                 "the tool docstrings and is the next best thing; "
                 "tool_catalog searches the whole surface."
                 if derived else
                 ("No recipe covers this; search the surface with "
                  "tool_catalog before concluding a tool does not exist."
                  if not recipes and not dead_ends else "")),
    }


def register_guidance_tools(mcp):
    """Register the guidance tool. Backend-agnostic, like the meta pair."""

    @mcp.tool()
    async def tool_guide(task: str = "", document_kind: str = "",
                         backend: str = "") -> dict[str, Any]:
        """How to do something correctly, and what is genuinely impossible.

        USE THIS BEFORE CONCLUDING A CAPABILITY IS MISSING. The recorded
        failures are not "could not find the tool name", which
        ``tool_catalog`` already solves. They are aiming a board tool at
        a library, and reporting a capability absent when it was
        present. Both return a confident wrong answer rather than an
        error.

        Answers three distinct things, kept apart on purpose: the tool
        and its prerequisites, the tool you were probably reaching for
        and why it acts on another document, and the short list of
        things that are proven impossible with the reason.

        An empty result means this guide has nothing on the subject. It
        is not evidence that the server cannot do it: fall back to
        ``tool_catalog``.

        Search here for help when a tool seems missing, when you are not
        sure which tool to use, when an operation seems not supported,
        or when a call failed because the wrong document was open.
        (``tool_catalog`` matches on this description, and searching the
        obvious word, help, used to return four other tools and not this
        one. The vocabulary of being stuck has to appear here, not only
        the vocabulary of the answer.)

        Args:
            task: What you are trying to do, in your own words, e.g.
                "delete silkscreen from a footprint". Matched on
                keywords, so a rough phrase is fine.
            document_kind: Narrow to one of library, board, schematic,
                project, any. Optional.
            backend: altium, kicad or easyeda. Optional; omit to see
                every backend's answer.

        Returns:
            Dict with ``recipes`` (each with ``use``, ``avoid`` and a
            ``note``), ``not_possible``, and ``matched``.
        """
        # The docstrings are read from the live registry rather than a
        # copy, so a tool renamed or re-described is reflected the moment
        # it is, with nothing to update here.
        docs: dict[str, str] = {}
        try:
            for spec in await mcp.list_tools():
                docs[spec.name] = getattr(spec, "description", "") or ""
        except Exception:            # noqa: BLE001
            docs = {}
        return guidance_for(task, document_kind, backend, docs)
