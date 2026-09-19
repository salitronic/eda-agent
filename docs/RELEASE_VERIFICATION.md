# Release verification: 2026.09.19.4

Everything below is Pascal that FPC and the linter have checked and that
**Altium's DelphiScript engine has never executed**. The two are not the
same: each accepts identifiers the other rejects, and an undeclared one
faults at runtime where `Try/Except` cannot catch it, halting the
polling loop.

Work top to bottom. Step 1 needs nothing but Altium and takes seconds,
and is the step most likely to catch a compile-level problem. If it
fails, stop and fix that before running the rest.

If there is only time for some of it, the risk is not evenly spread.
What separates the steps is whether the Altium property being written
is already written somewhere in shipped code, because a property that
works elsewhere cannot be an undeclared identifier:

| Step | Property | Written elsewhere? | Risk |
|---|---|---|---|
| 5, 3D placement | `StandoffHeight` | only by step 15, itself unverified | highest |
| 5, 3D placement | `Rotation` on a body | other object types only | high |
| 2, pin edges | `Symbol_OuterEdge`, `Symbol_InnerEdge` | no, nowhere | high, and can stop the loop |
| 7, enum words | `StrToPinElectrical` | yes, `Lib_AddPins` | medium |
| 4, DNP paste | `PasteMaskExpansion` | yes, `PCB_MakePasteGrid` | low, but it edits the board |
| 3, filled body | `AreaColor`, `IsSolid` | yes, `Generic.pas` | low |
| 5, 3D placement | `MoveByXY` | yes, `PCB_ReplicateLayout` | low |
| 11, copy and rename | `LibReference` | yes, `Lib_CreateSymbol` | low |
| 12, delete variant | `DM_RemoveProjectVariant` | no, nowhere | highest, and it destroys work |
| 13, sheet symbol filename | `Text` on a sheet symbol's sub-object | yes, on other objects | medium |
| 14, polygon pour options | `RemoveDead`, `RemoveNarrowNecks`, `RemoveIslandsByArea` | new here, and in two places at once | high, and it can stop the loop |
| 15, 3D body on a board | `StandoffHeight` on a free body | only by step 5, itself unverified | highest, shared with step 5 |
| 16, region kind | `Kind` on a region | read by four reference scripts, written by none | high, and it can stop the loop |
| 17, component UniqueId | `UniqueId` on a placed component | no, nowhere | highest, and a wrong id costs the next Update PCB |
| 18, pin owner part | `OwnerPartId` | yes, by two independent reference scripts | medium, behavioural not declarative |
| 6, mirrored text | `MirrorFlag` | yes, `PCB.pas` | lowest |

Steps 5 and 2 are the ones that justify a live session. The bottom rows
write properties this codebase already exercises, so they are checking
the new call site rather than the API.

Step 5 appears three times because its properties do not share a risk.
`MoveByXY` is inherited from `IPCB_Primitive` and `PCB_ReplicateLayout`
already calls it, so it cannot be an undeclared identifier and a failure
there would be behavioural: whether moving a group moves its children.
`Rotation` is written on pads, texts, fills and components but never on
a body, and DelphiScript resolves a property against the object in hand,
so another interface accepting it proves nothing here. Only
`StandoffHeight` is entirely unexercised.

Two steps now write `StandoffHeight` and neither has run, so they do not
vouch for each other: steps 5 and 15 share one unproven identifier. Run
whichever is easier and the other drops to a behavioural check.

### What this release adds, and why it is a different kind of risk

The steps above check whether an identifier exists. This release
carries almost none of that risk: every Altium property and method it
touches is already written somewhere in shipped code, so an undeclared
identifier is close to ruled out by construction. The cross-document
hints touch no Altium API at all, being string handling only.

`Proj_UpdatePCB` now reads `DM_FocusedDocument` and `DM_DocumentKind`
before comparing, and refuses while a schematic is focused. Both
identifiers are already used across several units, so neither is new
exposure. Everything else is string handling: the reply fields that
used to overstate what had been checked, and the removal of the
`Client:RunMenu` fallback in `App_ExecuteMenu`, which now refuses an
unmapped path instead of guessing a MenuID.

One thing here cannot be settled by running the steps below. The
refusal is deliberately absent from `Proj_UpdateSchematic`, the
opposite direction, because the focus Altium wants there has never been
measured. If you exercise that direction, record what it does.

Footprint height is the same shape of risk, which is to say almost
none. `Lib_SetFootprintHeight` writes `Footprint.Height`, which the
height sweep beside it has always written, and reads the library
through the same iterator thirty-odd other handlers use. What is new is
a DIRECTION: the sweep can now lower a height to the model, not only
raise it. Check it by setting a footprint absurdly tall by hand, running
the sweep in `match` mode, and confirming it comes back down. Confirm
the reverse too, that `raise` leaves it alone, because a mode that
ignores its argument would pass the first test on its own.

The case worth being careful about is a footprint with NO 3D body. It
must not be written at all. Writing the 0 that an absent model implies
does not relax placement-collision DRC, it disables it for that part,
and doing that across every unmodelled footprint would switch the rule
off wholesale while reporting a clean sweep. Run `match` on a library
where at least one footprint has no model, and check that footprint's
height is untouched and that its name comes back under
`without_model_names`.

What it adds instead is **logic that decides what to touch**, which
fails silently rather than loudly:

| Added | What could go wrong | How you would know |
|---|---|---|
| `GetPCBBoardForMutation` | An edit refuses when it should proceed, or proceeds when two boards are open and none focused | Open two PcbDocs, focus neither, run `pcb_delete_object`. It must refuse and name both |
| Board mechanical layers | A paired kind is written on the layer rather than the pair, which silently does nothing | Set a paired kind, then read it back with `pcb_get_mech_layer_names` |
| `lib_delete_footprint_primitives` | Removes from the wrong footprint, or takes pads with it | Probe the footprint first, delete one layer, probe again and compare pad count |
| Library parameter delete | Matches zero and reports success | Delete a named parameter from a library symbol, then read the symbol's parameters |
| Multi-part scope suffix | Returns part one's pins under another part's name | `lib_get_pin_list` on a multi-part symbol with `@2` and `@3`, and compare the counts |
| Cross-document hints | The hint is appended to an error it does not apply to, or doubles up on a message that already names a tool | Focus a SchDoc and run `pcb_delete_object`. The refusal must name the PCB tool once, and read as one sentence |

None of these can halt the polling loop the way an undeclared
identifier does, so they are safe to run in any order and safe to run
last. The cost of getting one wrong is a wrong answer, not a dead
bridge.

### Carried into 2026.09.19.4: five fixes whose symptom was silence

These came out of one live session and a bug report, and they share a
shape: the tool reported success, the board or sheet did not agree, and
nothing anywhere said so. A guard test covers each one against the
source, which is not the same as having watched it work.

| Fixed | What it did before | How you would know it is fixed |
|---|---|---|
| Component movers use `MoveByXY` | Assigning `Comp.x` moved the record and left every pad where it was, so the pour kept clearing the old footprint and the DRC kept measuring it | Move a placed component with `pcb_move_components`, then `pcb_repour_polygons`. The copper must clear the NEW position and nothing at the old one |
| Placed copper joins its net | `Prim.Net := N` sets a reference, but the net keeps its own collection and connectivity, the ratsnest and the pour all walk that one. Copper had a net, reported that net, and was invisible to everything downstream | `pcb_place_via` on a named net, then `pcb_get_unrouted_nets`. The via must count as connected, and a pour on that net must give it thermal relief |
| Schematic writes mark the document | A write that does not set the modified flag is invisible to `SmartCompile`, which checks `ProjectHasDirtyDocs` and skips. Symptom: ERC and the netlist keep reporting the state before your edit until the sheet is reopened | Place something with `sch_place_no_erc`, then `app_context`. The sheet must appear in `unsaved`, and `proj_run_erc` must see the edit without a reopen |
| Position reads resolve per type | `GetPCBProperty` read `Obj.x` off the declared `IPCB_Primitive`. A type that does not publish it raised "Undeclared identifier: x", which the engine shows as a modal before any `Try/Except` runs, stopping the polling loop | `obj_query` a text object's position, then an arc's. Both must answer, and a track must come back `unreadable` rather than with one of its ends |
| `app_context` can see unsaved work | It filtered the open-document list on a `modified` key the handler never emitted, so the list was always empty and every session opened reporting itself clean | Edit any sheet without saving, then `app_context`. `unsaved` must name it |

`pcb_set_via_soldermask_relief` refuses on this build rather than being
fixed. The write raises an access violation inside
`ScriptingSystem.DLL` on AD 26.10.1.6, measured twice on a scratch
board with three vias and nothing else, once through `BeginModify` and
once through `SendMessageToRobots`. Because the fault lands between
`PreProcess` and `PostProcess` it also leaves an open transaction
behind it.

The refusal is in the PYTHON tool, and that is the half to check.
Reaching the handler means putting the command on the wire, and any
session running a deployed script from before the refusal landed still has the
crashing write in it. Confirm the tool answers `NOT_SCRIPTABLE`
instantly and that `bridge_trace.log` shows no request for it.

### Three new design-rule kinds, and these DO carry identifier risk

Unlike everything else in this release, these write Altium symbols this
codebase has never used. Run them before anything else in a session you
mind losing, because an undeclared identifier here stops the polling
loop rather than returning an error.

| `rule_type` | Symbols | Evidence they exist | How to check |
|---|---|---|---|
| `paste_mask_expansion` | `eRule_PasteMaskExpansion`, `IPCB_PasteMaskExpansionRule.Expansion` | `NofittedNoPaste.pas` in the reference corpus creates one exactly this way | Create one, then read it back with `pcb_get_design_rules` and look at `descriptor`, not `rule_kind`, which is a raw enum ordinal |
| `solder_mask_expansion` | `eRule_SolderMaskExpansion`, `IPCB_SolderMaskExpansionRule.Expansion` | enum used by three reference scripts; interface and property in the SDK reference. The FACTORY call is unproven | Create with `scope="IsVia"` and a negative value, then look at the mask layer over a via |
| `vias_under_smd` | `eRule_ViasUnderSMD`, `IPCB_ViasUnderSMDConstraint.Allowed` | same: enum used by three reference scripts, interface and property documented, factory call unproven | Create with `allowed=false`, place a via inside an SMD pad, run DRC |

`paste_mask_expansion` is the one to run first. It is the only one of
the three whose factory call is demonstrated by working published code,
so if it fails the problem is this handler rather than the symbol, and
if it succeeds the other two are down to their own enum values.

These close the gap the via-relief refusal used to point at: tenting is
a Solder Mask Expansion rule scoped `IsVia`, and via-in-pad is caught
by Vias Under SMD. Both were previously "set it in the dialog".

### Close without saving, and OutJob runs that wrote nothing

Two handler changes. Neither adds an identifier this codebase has not
already called, so the risk is behavioural rather than a halted loop.
Run both on a scratch project, never a client one: the first one is
built to throw edits away.

| Changed | What is unmeasured | How to check |
|---|---|---|
| `Proj_Close` with `save=false` clears each loaded member's modified flag before closing, without reading it | MEASURED on this build, local scratch project: the tab showed the sheet modified, the close raised no prompt, `attempts` was 1, and the file on disk was byte-identical afterwards. On the previous build the clear was gated on a read that returned False, and the close prompted. A managed project is untested | On a managed project, edit a sheet without saving, then `proj_close(project_path=..., save=False)`. Record whether a prompt appears |
| If a prompt appears anyway, `proj_close` answers it: Save None, then OK once the title reads "Confirm Not Saving" | The sequence was MEASURED on the previous build with `app_invoke_element`. The tool running it has not run live, because on this build the prompt did not appear | Only reachable where the step above still prompts. The reply then carries `prompts_answered` |
| It presses nothing when the prompt lists a document outside the project | Unmeasured | Do not arrange it on a project with real work open |
| `App_GetActiveDocument` and `Proj_GetDocuments` put a path in `file_path` | MEASURED: the previous build reported the bare file name from both, and this build reports absolute paths from both | Done |
| A close without saving that stays open is retried once | MEASURED on the previous build, and misread at first: `attempts: 2` on an unmodified scratch project was not the retry doing its job. The first close had closed a different project, the focused one | See the next row |
| `Proj_Close` focuses the named project before closing, refuses when it cannot, and stops without retrying when anything else closes | MEASURED on the previous build: `CloseObject` given a project's full path closed the FOCUSED project instead, and nothing reported it. This build's guard is unmeasured | Open two scratch projects, each with a loaded sheet. Focus a sheet of the first, then `proj_close` the second: only the second may close, and `closed_instead` must be empty. Then `proj_close` a project with no loaded document: it must refuse and close nothing |
| `Proj_RunOutJob` replies `process_issued`, and the Python tools decide `success` from the output folder | Nothing Altium-side | Run a container with its outputs switched off: `proj_run_outjob` must return `success: false` with a `reason`. Run one that writes: `files_written` must be above zero |

---

## Gather these before you start

Each step wants a particular document open or a particular file on
disk, and none of it is interesting to discover halfway through. Step 5
in particular needs a STEP model, which is not something to go looking
for mid-session.

| Step | What must be open, or on disk |
|---|---|
| 0, 1 | Altium running with `Altium_API` loaded and the loop started |
| 2 | a SchLib, with a component selected |
| 3 | a SchLib |
| 4 | a PCB whose active variant has Not-Fitted parts |
| 5 | a PcbLib with a footprint, plus a `.step` file |
| 6 | a PcbLib, plus a KiCad `.kicad_mod` carrying `B.SilkS` text |
| 7 | a SchLib for the pins, and a schematic sheet for the power ports |

Steps 4 and 5 write to the design. Take an `app_checkpoint` first, or
work on a copy. Everything else either reads or builds new library
objects you can delete afterwards.

---

## 0. Confirm what is actually loaded

```
app_ping
```

Expect `altium_script_version` = `2026.09.19.4`, `version_match` =
`true`, and `mcp_server_version` = `0.6.1`.

Those are two different versions and they fail differently.
`altium_script_version` is the Pascal that Altium compiled;
`mcp_server_version` is the Python package answering the call. A wrong
Pascal version means a stale deploy. A wrong package version means the
installed wheel is not this tree, so the tools themselves differ from
what this document describes.

A mismatch means Altium is still running an older compiled copy.
Reload the script project (**File > Run Script**, pick the project,
or close and reopen it) and ping again. Do not interpret any later
result until this matches: a stale script produces failures that look
like defects in the new code.

**No answer at all is a different failure with a different fix.** A
mismatch means the wrong script is running; a timeout usually means no
script is running. Altium's process being alive proves nothing here,
since the polling loop can be stopped while Altium itself is fine. That
was the state of this machine while this document was written.

Read `last_fault.json` in the workspace directory rather than guessing.
The bridge writes a diagnosis and numbered steps there, and tells the
three cases apart:

| `fault` | What it means | Fix |
|---|---|---|
| `dead_loop` | no response and no heartbeat | dismiss any Altium error dialog, Stop, relaunch |
| `stuck_handler` | keep-alives answered, one command never returned | Stop, relaunch |
| `corrupt_response` | Altium crashed mid-write | retry once, the bad file is already removed |

Stop is the red button in the Script IDE (**Run > Stop**, `Ctrl+F3`,
or `Ctrl+Pause` if it is truly hung). Relaunch is **File > Run
Script... > Altium_API > Dispatcher.pas > StartMCPServer**.

Leftover `request_*.json` files in the workspace are a symptom of this,
not a cause: they are calls the loop never picked up. They are harmless
and the next healthy loop consumes them.

---

## 1. Pure logic, no document needed

`RunSelfTest` is no longer listed in the Run Script dialog. It carries a
dummy argument like every other internal routine, so the dialog offers
the one thing a user does: start the bridge. To run the self test,
temporarily drop the argument from `Procedure RunSelfTest(Dummy : Integer)`
in `SelfTest.pas`, reload the project, and pick it from the dialog.

It also ends in `ShowMessage`, so it cannot be reached over the bridge
without splitting the summary out first.

Expect `Failed: 0`. The log is written to the workspace directory.

This runs 27 assertions over `StrToIeeeSymbol`, `IeeeSymbolToStr` and
`StripChar` inside Altium's own engine. The values match what
`tests/test_cross_validate.py` pins against the FPC-compiled originals,
so a failure here means DelphiScript disagrees with Free Pascal. That
is the gap this step exists to close.

**If it fails:** the log names the assertion. Report the text; the
converters are in `scripts/altium/Utils.pas`.

---

## 2. Pin edge decorations (task #20)

Needs a SchLib open with a component selected.

```
lib_add_pins(pins=[
  {"designator": "1", "name": "RESET", "x": -300, "y": 0,
   "symbol_outer_edge": "dot"},
  {"designator": "2", "name": "CLK",   "x": -300, "y": -100,
   "symbol_inner_edge": "clock"},
])
```

Then **look at the symbol**. Pin 1 must carry an inversion bubble at its
outer end; pin 2 a clock wedge at the body end.

The returned `added` count says how many pins were created, not how they
were drawn, so it cannot confirm this. Render or view it.

**What is actually being tested:** whether `Symbol_OuterEdge` /
`Symbol_InnerEdge` are settable on a pin from `SchObjectFactory`
*before* `AddSchObject`, and whether assigning an Integer to those
enum-typed properties behaves the way `Pin.Orientation` does. Step 1
already proved the value mapping.

**The two failure shapes, and why the assignment is deliberately not
wrapped in `Try/Except`:**

* the property exists but rejects the value: the dispatcher catches it
  and answers `INTERNAL_ERROR: Unhandled exception processing:
  library.add_pins`. The loop keeps running.
* the property name is not real: an undeclared identifier faults where
  `Try/Except` cannot reach, and the polling loop stops. Recover with
  Detach and `StartMCPServer`.

Guarding the assignment would turn both into a silent success that adds
undecorated pins, which is the one outcome this step could not tell
apart from working. That is also why this step is early: it is the
first thing here that can halt the loop.

---

## 3. Filled IC bodies (task #28)

```
lib_create_ic_symbol(...)          # any part
```

The body rectangle must be **filled Altium light-yellow**, not a bare
outline. This is discipline rule 17, and it has been silently unmet:
the tool sent `AreaColor 8454143` and the bridge discarded it because
`IsSolid` was pinned False.

Then confirm the no-fill path still works:

```
lib_create_passive_symbol(kind="resistor", ...)
```

That sends `fill_color=-1`, the documented no-fill sentinel, and its
body must stay **unfilled**. `-1` is also the parameter's default, so it
arrives on nearly every call; treating it as a colour would turn every
symbol rectangle solid.

---

## 4. DNP paste exclusion (task #26)

**This edits the board.** Work on a copy, or `app_checkpoint` first.

Needs a PCB with a variant that has Not-Fitted parts.

```
pcb_apply_dnp_paste_exclusion(dry_run=True)
```

Check the designator list matches `audit_variant_not_fitted`. Then:

```
pcb_apply_dnp_paste_exclusion()
pcb_get_pad_properties(designator="<one of them>")
```

`paste_mask_expansion` must be negative.

A negative expansion is necessary but not sufficient. Only the artwork
proves the fab outcome:

```
proj_generate_fab_package(...)
```

Open the paste layer and confirm those apertures are **absent**. Then
restore, passing back the designators the apply reported in `items`:

```
pcb_apply_dnp_paste_exclusion(designators=["<from the apply reply>"],
                              restore=True)
```

Regenerate and confirm the apertures come back.

**Restore will refuse a bare `restore=True`**, and that refusal is the
behaviour to verify, not a defect. Resolving a restore from the current
variant is only correct while the variant has not changed since the
apply; when it has, a component excluded under the old variant keeps
its aperture suppressed and nothing says so. Check that the refusal
names both the way forward and `use_current_variant`, which is the
explicit opt-in to the old resolve-from-the-variant behaviour.

After a restore, `paste_mask_expansion` reads **0**, which is what a
successful restore looks like and not a separate failure. The field is
always present; `PCB_GetPadProperties` initialises it to 0 and only
substitutes the real value when the pad cache is manual. Restore sets
the cache back to invalid, so the design rule drives the aperture and
the reported number falls to 0.

A pad that was never excluded also reads 0, so this distinguishes
applied from not-applied, not restored from never-touched. The artwork
is what settles that.

Check a **bottom-side** DNP part too if the board has one:
`Pad.TopXSize` is the top-layer size and drives the expansion for both
sides.

---

## 5. 3D model placement (task #27)

The highest-risk item. All three properties are documented on
`IPCB_ComponentBody`, but only `MoveByXY` has ever been exercised here,
and on a different object type. See the note under the risk table for
which of the three is actually unproven.

```
lib_link_3d_model(component_name="<fp>", model_path="<...>.step",
                  offset_z=40, rotation_z=90, offset_x=10)
```

Read the `applied` object in the reply first: `standoff_height`,
`rotation_z`, `offset_xy`. Each assignment is individually guarded, so
one property failing does not fail the call.

All three must read `true` for the call above. They mean "this was
applied", not "this was accepted": the handler skips a property whose
value is 0, so a `false` is either a rejection or a value you did not
pass. Keep every argument non-zero while verifying, or the two cases
are indistinguishable. `offset_xy` covers `offset_x` and `offset_y`
together and reads `true` if either is non-zero.

Then open the 3D view: the body should be lifted, turned and nudged.

**Units:** the tool documents mils and applies `MilsToCoord`. A body
that moves 25.4x too far means the property wanted different units.

`rotation_x` / `rotation_y` are accepted and deliberately not applied,
because the API gives the body a planar rotation only. If a live session
finds an X/Y tilt property, revisit the docstring then.

---

## 6. Mirrored bottom-side text (task #21)

Lowest risk of the set. `MirrorFlag` is already written by `PCB.pas` and
read by `Audit.pas`; only the call site is new.

Import a footprint carrying `B.SilkS` text, then:

```
audit_find_mirrored_pcb_text
```

Expect **zero** violations. Before this change the importer produced
`bottom_overlay_text_is_not_mirrored` on every such item, i.e. output
this server's own audit rejected.

---

## 7. Enumerated words map to the right Altium enum (task #33)

This covers what `tests/test_enum_vocabularies.py` cannot. That test
proves every advertised spelling has a branch in the `StrTo*` converter.
It cannot prove the branch assigns the enum member the word names,
because the ordinals only exist inside Altium.

The failure is silent. Each converter ends in an `Else` that picks a
default, so a wrong or missing branch yields Passive, or a supply Bar,
with no error reported.

Place four pins with different electrical types, then read them back:

```
lib_add_pins(pins=[
  {"designator": "1", "name": "VIN",  "x": -300, "y":    0,
   "electrical_type": "power"},
  {"designator": "2", "name": "NRST", "x": -300, "y": -100,
   "electrical_type": "open_collector"},
  {"designator": "3", "name": "SDA",  "x": -300, "y": -200,
   "electrical_type": "io"},
  {"designator": "4", "name": "OUT",  "x": -300, "y": -300,
   "electrical_type": "output"},
])
lib_get_component_details(...)
```

Each pin must report the type it was given. All four reading `passive`
means the string never matched and every one took the default;
`open_collector` alone reading `passive` means the underscore handling
is the part that broke.

Then one power port per glyph:

```
sch_place_power_port(text="GND",  style="gnd_signal", x=1000, y=1000)
sch_place_power_port(text="+3V3", style="bar", x=1400, y=1000,
                     orientation=1)
```

`orientation=1` on the rail is required. The style-based default sends
`bar` and `wave` down with the grounds, so a VCC bar drawn without it
points down and looks like a ground symbol.

Look at the sheet. A signal-ground glyph and a rail bar are visually
distinct; two identical bars mean `gnd_signal` fell through to the
`ePowerBar` default.

---

## 8. ERC violations name the objects they are about

**`proj_run_erc()`, then `proj_get_erc_violations()`**

On a project that reports violations, every entry should now carry
`related_objects`, each with a `kind`, a `document` and a `cross_probe`
string.

This is the step that decides whether the tool is usable at all. A
violation reported as a category and a sheet name cannot be acted on:
the only safe response to "floating input pin, somewhere on this sheet"
is to do nothing, because a NoERC marker placed by guesswork silently
suppresses a real disconnection and is worse than the warning it clears.
`cross_probe` is what Altium itself uses to jump to an object, so it is
what identifies the specific pin or net.

Compare against the Messages panel: the objects listed there for a given
violation should match `related_objects` for the same index.

**Expect `related_object_count` to be non-zero** for floating-pin and
unconnected-object violations. Zero across every violation means
`DM_RelatedObjects` returned nothing in this build, which is a different
outcome from the call faulting, and is why the count is reported
separately from the list.

**If it fails:** the risk here is `DM_PrimaryCrossProbeString`. It is
declared on `IDMObject`, the base every related object implements, so it
should be safe on all of them, but that is reasoned from the reference
rather than measured. An undeclared identifier faults where `Try/Except`
cannot catch it and halts the polling loop, so a dead loop right after
calling this tool points at that call. `Gen_GetErcViolations` is in
`scripts/altium/Generic.pas`.

---

## 9. Reading a symbol's pins no longer faults (task #34)

Needs a SchLib open.

```
lib_get_pin_list(component_name="<a symbol in that library>")
```

Expect a pin list. Then call it again with no `component_name` and a
symbol selected in the editor, which must also work.

**This one is a fix for an observed crash, not a new feature.** The
deployed script answered
`Undeclared identifier: SchIterator_Create` and stopped the polling
loop. The identical call appears ten times in `Library.pas` and works
everywhere else; the difference was where the component came from.
Every working reader fetches it through `GetState_SchComponentByLibRef`
or a SchLib iterator, while this one used the editor's
`CurrentSchComponent` directly. DelphiScript narrows an interface at
iterator-return, and a component obtained any other way does not carry
the methods.

So this step is really asking one question: does resolving through the
library make the iterator available? If it does, the explanation holds.

`component_name` is the other half of the fix. Reading a symbol's pins
used to depend on, and disturb, whatever the editor had selected, which
is why exporting one symbol could change which symbol later calls saw.

**If it fails with the same identifier**, the narrowing explanation is
wrong. Say so rather than trying variations: that reasoning came from
comparing call sites, not from proving the mechanism, and the next step
would be to instrument rather than guess again.

**If it fails with a different identifier**, that is a second undeclared
name in the same function and the message will say which.

---

## Step 9: the multi-part scope suffix actually switches part

Reported in GH #11 against a 4-part TPS23881B, with numbers. The `@N`
suffix parsed and reached the part-switch code, and the switch itself
did nothing: `Component.CurrentPartID := N` takes the value, the
editor's part spinner does not move, and the SchLib iterator follows
the DISPLAYED part. So `obj_query` returned part 1's pins whatever the
scope said, and with the spinner moved by hand the suffix was ignored
outright. Nothing errored in either direction.

The fix drives the editor's own command, `SCH:NextComponentPart`, and
reads `GetState_CurrentSchComponentPartId` back after each step. Both
appear in two independent scripts under `reference/`, so neither is a
guess, but neither has run from this codebase.

Open a multi-part SchLib and, with the editor showing part 1:

    obj_query  scope lib_component:<NAME>@2  kind ePin
    obj_query  scope lib_component:<NAME>@3  kind ePin

Each must return that part's own pin count, and every returned pin must
carry the matching `OwnerPartId`. Then the sharper test: switch the
spinner to part 3 by hand and query `@1`. It must return part 1.

**Two ways this fails quietly.** If
`GetState_CurrentSchComponentPartId` is undeclared, the polling loop
halts, which is loud. If it returns -1 instead, the stepping is skipped
by design and the behaviour is exactly the bug being fixed: the same
wrong answer, no error. So a run that still returns part 1 is not
evidence the command is wrong, it is evidence the part id could not be
read. Report which.

The loop is bounded by `PartCount` because the command wraps at the
last part. A target that can never be reached leaves the editor moved
but not where asked, so check the spinner afterwards.

---

## 10. UNC paths survive the trip (task #44)

This release deletes the vestigial second unescape
(`StringReplace(x, '\\', '\', -1)`) from all 94 path-taking handlers.
`ExtractJsonValue` already unescapes the JSON, so the second pass was a
no-op for local paths and stripped one leading backslash from UNC
paths: `\\server\share\lib.SchLib` arrived as
`\server\share\lib.SchLib` and failed as a missing file.

No new identifiers are involved, only deletions, so the compile risk
is nil; what needs proving is the behaviour. From a machine with any
reachable share (an admin share like `\\localhost\C$\...` works):

    lib_get_components  library_path \\localhost\C$\<path-to-any>.SchLib

Before the fix this fails with a file-not-found flavoured error;
after it, the library opens and lists components. Local absolute paths
must keep working unchanged, which step 9's queries already exercise.

`tests/test_no_double_unescape.py` pins the site count at zero from
now on, so this is a one-time verification, not a recurring step.

## 11. Copy and rename actually change the library

Reported against the previous script build: `lib_copy_component` and
`lib_rename_component` both answered `success:true` while the component
count did not move, the copy's `new_name` resolved nowhere, and the
renamed part was still there under its old name.

`AddSchComponent` overrides `LibReference` with an auto-generated
`Component_<N>` on the second and later additions to a SchLib in one
session, so an assignment made before the add does not survive it.
`Lib_CreateSymbol` already re-asserts after the add for this reason;
these two did not. No new identifiers are involved, so the compile risk
is nil, and what needs proving is the behaviour.

On a scratch library, with a symbol that is not the first added this
session:

    lib_copy_component    source_name <existing>  new_name COPY_PROBE
    lib_get_components    library_path <the same library>

`COPY_PROBE` must appear, and the count must be one higher. Then:

    lib_rename_component  component_name COPY_PROBE  new_name RENAME_PROBE
    lib_get_components    library_path <the same library>

`RENAME_PROBE` must appear, `COPY_PROBE` must be gone, and the count
must be unchanged. Both replies now carry `verified:true`; a reply with
`success:false` and a `reason` is the handler reporting that the
read-back missed, which is the state that used to be reported as
success.

Note that `part_count` from `lib_get_components` is not evidence of
anything here. It comes from the CompInfoReader, which has been measured
reporting 2 for a symbol created single-part whose every pin carries
`OwnerPartId 1`, while `lib_get_component_details` reported 1 for an
identically created symbol.

## 12. Deleting a project variant (highest risk in this release)

`DM_RemoveProjectVariant` is the only write in Altium's entire variant
API, and nothing in this codebase has ever called it, so it carries the
undeclared-identifier risk in full: if the name is wrong it faults where
`Try/Except` cannot catch it and takes the polling loop with it.

It is also the only step here that destroys work which cannot be
rebuilt from this bridge. A variant's entries record which components
are not fitted and which carry an alternate part, and there is no
documented way to add a variation entry back. Deleting a populated
variant means re-making every one of those decisions in the Variant
Management dialog.

**Work on a copy of a project, or take an `app_checkpoint` first.**

Create a throwaway variant in the Variant Management dialog, then:

    proj_list_variants
    proj_delete_variant   variant_name SCRATCH_VARIANT
    proj_list_variants

The reply must carry `verified: true`, `variant_count_after` one lower
than before, and `entries_removed`. The second listing must not contain
the name. If the loop stops answering instead, the identifier is not
declared in DelphiScript and the reference is wrong about it; say so
and the tool comes back out.

Then check the refusals, which cost nothing: a name that does not exist
must return `VARIANT_NOT_FOUND` and change no count, and an empty
`variant_name` must return `MISSING_PARAMS`.

`proj_set_active_variant` changed in the same release and is cheap to
check alongside. It used to report success on the strength of the name
existing, without asking which variant was actually current afterwards.
Switch to a variant and confirm the reply carries `verified: true`;
the failure reply now names `current_variant` so a switch that did not
take is distinguishable from one that did.

## 13. obj_modify stops reporting writes it did not make

Measured on a live project: `obj_modify` was asked three times to set a
sheet symbol's `FileName`, answered `matched:1, saved:true` each time,
and wrote nothing. The property is readable and had no case in the
writer, so each attempt was recorded as an unknown name in a diagnostic
buffer that only `batch_modify` ever rendered. Nothing in the reply
distinguished it from a real one, and an operator spent a session
working around a rename that had never happened.

`matched` counts what the FILTER selected. It never said anything about
whether a write landed. Every modify reply now carries `properties`
and an explicit `success`.

The cheapest check needs no sheet symbol at all. On any open schematic:

    obj_modify  object_type eNetLabel  filter <anything that matches one>
                set NotAPropertyName=1

That must come back `success:false` with `NotAPropertyName` under
`properties.unknown`, and `matched` may still be 1. Before this release
it returned `matched:1, saved:true` and nothing else.

Then the real one, on a sheet symbol whose child sheet you do NOT mind
re-pointing, or on a scratch copy of a project:

    obj_query   object_type eSheetSymbol  properties Filename,UniqueId
    obj_modify  object_type eSheetSymbol  filter UniqueId=<the id>
                set Filename=SOMETHING_ELSE.SchDoc
    obj_query   object_type eSheetSymbol  properties Filename,UniqueId

The reply must be `success:true` with an empty `properties.unknown`,
and the second query must show the new text. A write that does not
stick now reports under `properties.failed`, because the setter reads
the label back rather than trusting the assignment.

**This re-points a symbol, it does not rename a sheet.** The filename
lives in three places: this label, the file on disk, and the project's
document list. Only Altium's **Sheet Symbol Actions > Rename Child
Sheet** does all three, and it keeps the symbol's `UniqueId`, which is
the project's handle for that sheet instance. Deleting and re-placing a
symbol issues a new id, and the next Update PCB then proposes
delete-and-re-add for every component on the sheet instead of matching
them.

## 14. Polygon pour options, and the PCB writer that never reported

Three properties this codebase has never written, so this carries the
undeclared-identifier risk in full. They are declared on `IPCB_Polygon`
with both accessors:

    Property RemoveDead : Boolean Read GetState_RemoveDead
                                  Write SetState_RemoveDead;

and the same shape for `RemoveNarrowNecks` and `RemoveIslandsByArea`.
Reported from a live board as "not an exposed property on this API",
which was a fair reading of a modify that answered `matched:2` and wrote
nothing.

**Nothing here repours.** These decide what the NEXT pour produces, so
the copper does not change until `pcb_repour_polygons` runs. Check the
flag first and the pour second, or a working change looks like a failed
one.

    pcb_get_polygons
    pcb_modify_polygon  index 0  remove_dead true
    pcb_get_polygons

The reply must carry `changed: ["remove_dead"]`, `modified: true` and
`repour_needed: true`. Then repour and confirm the dead copper goes.

Set it back to `false` afterwards and confirm `changed` names it again:
a handler that ignored the value and always wrote true would pass the
first half on its own.

Two refusals, which cost nothing and are the point of the release:

    pcb_modify_polygon  index 0  hatch_style Horizontal
    pcb_modify_polygon  index 0  net NO_SUCH_NET

Both must come back with `success: false` and the field named under
`not_applied`. `Horizontal` was documented by the tool and handled by no
branch, so it changed nothing and reported success; a net name that
matches nothing did the same.

The PCB property writer also reports now, so the generic route says so
too. On any board:

    obj_modify  object_type ePolyObject  set NotAProperty=1

must come back `success:false` with the name under
`properties.unknown`. Before this it returned a bare `matched` count.

## 15. A STEP model straight onto the board, and a read that stopped moving focus

Two things from one session, and the second is why the first took so
long to diagnose.

**THIS TOOL HAS ALREADY CRASHED ALTIUM ONCE.** The first build set
Layer, x, y and StandoffHeight on the body before adding it to the
board, and the PCB engine went down with "Access violation ... Read of
address 0x20" inside ADVPCB.DLL. A null dereference at a small field
offset is a property setter reaching for state that an owning board
provides. It also assigned x and y directly, where the reference and
`Lib_Link3DModel` both use `MoveByXY` after the add.

Both are corrected: add, register, then Layer, then MoveByXY, then
StandoffHeight. Treat this step as unproven all the same, and take an
`app_checkpoint` before running it.

**`pcb_place_3d_body`** puts a STEP model on the open PcbDoc as a free
3D body, which is Altium's Place > 3D Body > Generic STEP Model. Until
now `lib_link_3d_model` was the only STEP importer in the toolset and it
writes into a `.PcbLib` footprint, so putting a fixture or a
device-under-test on a board meant inventing a library, authoring a
footprint, placing it and deleting all of it again. The call sequence
here is the one `Lib_Link3DModel` already uses, minus the footprint
binding, so every identifier in it is exercised by shipped code.

On a scratch board, with any `.step` to hand:

    pcb_place_3d_body  model_path <path>  x 1000  y 1000  standoff_height 100
    obj_switch_view    3d

The reply's `x` and `y` are READ BACK from the placed body rather than
echoed. Confirm `standoff_applied` is true, then look: the body must be
visible and sitting 100 mils proud. `rotation_applied` is always false
and says why in `note`.

Then the refusals, which cost nothing:

    pcb_place_3d_body  model_path C:\nope.step
    pcb_place_3d_body  model_path <a .txt file>

The first must be `FILE_NOT_FOUND` and the second `MODEL_LOAD_FAILED`.
They are separated on purpose: the path is checked before anything is
created, because `ModelFactory_FromFilename` on a missing file returns
Nil and leaves an orphan body behind.

**The focus fix has no visible output, so check it by its absence.**
Nine read-only library actions used to leave the active document on the
library they read, because focusing it is the only way the PCBServer and
SchServer accessors will answer about it. Measured: `lib_probe_footprint`
silently focused a PcbLib, and the `obj_switch_view 3d` that followed
switched the LIBRARY into 3D. The board looked untouched and the model
looked absent.

With a PcbDoc focused:

    lib_probe_footprint  library_path <some .PcbLib>  footprint_name <one>
    app_get_active_document

The active document must still be the PcbDoc. Try it with
`lib_search`, `lib_get_component_details` and `lib_audit_styles` too;
all nine are listed in `LibActionIsReadOnly`.

Then confirm the opposite, which is the half that could regress
silently: library WRITES must still leave the library focused, because
authoring is a sequence of calls against a current component.

    lib_set_current_component  <a symbol>
    lib_add_pins               <a pin or two>

The pins must land on that symbol. If they land nowhere, the restore has
been applied to writes and the authoring flow is broken.

## 16. A region's Kind, and board cutouts

Reported from a live board as "the Board Cutout flag on a Region isn't
reachable through this API". It is:

    Property Kind : TRegionKind Read GetState_Kind Write SetState_Kind;

and it had simply never been exposed, which is the third instance this
release of a property being called absent because a reply said nothing.

The five identifiers are attested rather than assumed: four independent
scripts in `reference/` COMPARE against `eRegionKind_BoardCutout`,
`_Cutout`, `_Copper`, `_NamedRegion` and `_Cavity`, so they exist in
DelphiScript. None of them ASSIGNS one, so the write is unproven in the
way `StandoffHeight` is. Read first, and take an `app_checkpoint` before
writing.

Words, not numbers, in both directions. The ordinals are undocumented,
and publishing one invites a caller to write it back.

On a board with an existing cutout:

    obj_query   object_type eRegionObject  properties Kind,Layer

At least one region must come back `board_cutout` or `cutout`. If every
region reads `unknown`, the comparison is not matching and the write
below must not be attempted.

Then, on a scratch region you do not mind losing:

    obj_modify  object_type eRegionObject  filter <one region>
                set Kind=board_cutout
    obj_query   object_type eRegionObject  properties Kind

It must read back `board_cutout`, and the board outline must show the
hole. An unknown word is refused rather than ignored:

    obj_modify  object_type eRegionObject  filter <one>  set Kind=nonsense

must come back `success:false` with `Kind` under `properties.unknown`.

---

## 17. Writing a component's UniqueId (highest risk in this release)

`sch_set_component_unique_id` and `sch_replicate_component` both assign
`ISch_Component.UniqueId`. Nothing in this repository wrote it before,
and **no independent script in `reference/` writes it at all**, so the
identifier is unattested. It is guarded with `Try/Except`, which is no
guard: an undeclared identifier is not catchable in DelphiScript, so if
the name is wrong the modal takes the polling loop down.

It also carries a second risk that has nothing to do with whether the
call works. **A UniqueId is the project's handle for a component.** Give
one the wrong value and the next Update PCB stops matching that part and
proposes delete-and-re-add for it instead, taking its placement and
routing with it. That is a worse outcome than the tool failing.

**Work on a copy of a project.** An `app_checkpoint` does not cover the
PCB side of this.

Read one first, so there is something to put back:

    obj_query   object_type eSchComponent  filter Designator=<one>
                properties UniqueId

Then write the same value back to itself, which is the only edit here
that cannot lose anything:

    sch_set_component_unique_id  designator <the same>  unique_id <what it read>

The reply must carry `success: true` and `unique_id_after` equal to what
was asked. If the loop stops answering instead, the identifier is not
declared and the tool comes back out.

`success: false` with `unique_id_after` different is the OTHER outcome
worth knowing: the call worked, Altium declined the assignment and kept
its own id. That is a real answer, not a failure of the bridge, and it
is what the comparison was added to surface.

Only then try a component you do not mind re-annotating, and check with
`proj_compare_sch_pcb` that the PCB still matches before and after.

## 18. Moving a pin to another sub-part

`lib_set_pin_owner_part` writes `OwnerPartId`, which two independent
scripts in `reference/` also write, so unlike step 17 the identifier is
attested and the risk is behavioural rather than declarative.

The contributor stated plainly that this tool has never executed against
a live Altium. Two things are worth checking because they fail quietly:

    lib_get_pin_list        component_name <a multi-part symbol>
    lib_set_pin_owner_part  component_name <the same>  pin_designators "3, 12"
                            owner_part_id 2
    lib_get_pin_list        component_name <the same>

Both pins must come back with `owner_part_id` 2, and the count in the
reply must be 2 rather than 1. The spaced form is deliberate: `"3, 12"`
used to match nothing and report `count: 0` as a successful no-op.

Then the bound, which is the one that corrupts rather than refuses:

    lib_set_pin_owner_part  ...  owner_part_id 99

must be refused. An id above the symbol's `PartCount` is accepted by the
assignment and maps to no displayable part, so the pin vanishes from
every sub-part view while the library still contains it.

`owner_part_id 0` is Part Zero and is always legal: the pin is shared
across all parts. Confirm it reads back as 0 rather than being treated
as an error.

Save with `app_save_all` and reopen the library before trusting any of
it. A library edit is real in memory and absent from disk until then.

---

## Still open, and not blocking

* **#23** font size: the importer places symbol text but not its height.
  Altium's font size is not in mils and the conversion is undocumented,
  so the source range is reported rather than guessed. Calibrating it
  needs a live measurement.
