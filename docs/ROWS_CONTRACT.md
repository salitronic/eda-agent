# Rows: filtered bulk read / write -- the contract

A command family BESIDE the existing handlers (none of them changes). The
narrowing happens in Altium, the deciding in the caller, the writing in Altium:
one call to read the objects a tool needs, one call to write a different value
to each of them.

Every kind built on this contract MUST follow it. A change to the contract is
a change to `RowsCore.pas` and this file together, never inside one kind.

## Commands

Action names are unique across the bridge (`read` / `write` are taken by
`pcb.embedded_data`).

| Command | Does |
|---|---|
| `rows.list_kinds` | Lists every (doc, kind) built, with its field table. |
| `rows.read_rows`  | Reads the objects of ONE kind that match a scope and a `where`. |
| `rows.write_rows` | Writes rows keyed by object id, each guarded; all or nothing. |

Common parameters:

| Param | Meaning |
|---|---|
| `doc` | `pcblib`, `pcb`, `schlib`, `sch` (required). |
| `kind` | One object kind of that doc (required): `track`, `arc`, `via`, `pad`, `text`, `pin`, `netlabel`, ... |
| `units` | IGNORED by the bridge: every number on the wire is a scaled integer (see Numbers). The Python tool takes `mm`, `mil` (default `mil` for `schlib` / `sch`, `mm` for `pcblib` / `pcb`) or `coord` and converts. Board coordinates are ABSOLUTE (Altium's own origin, as the layout model and generic X / Y give them); library coordinates are from the footprint's reference. |
| `library_path` | `pcblib` / `schlib`: the library; default the focused document. |
| `document_path` | `sch`: an open sheet; default the sheet in front (a library in front is refused `NO_SCHDOC`). |

## Fields

Each kind has ONE field table: `name:type:access` entries joined by `|`.
- type `l` length, `a` angle, `i` integer (see Numbers), `s` text, `b`
  boolean (`true` / `false`), `v` point list (`x,y;x,y`, two or more
  points, every number a length; the Python tool takes and gives
  `[[x, y], ...]` in the caller's unit);
- access `w` writable, `r` read-only.

## Who checks what

`src/eda_agent/tools/rows.py` is the ONLY way into the rows commands
(2026-11-11). Before it sends a write it makes every check that
needs nothing from Altium (`rows_rules.py`): fields known and writable, an
id on every row and none twice, the container guard, the types (canonical
32-bit integers, `true` / `false`), the kinds' value rules (sizes above 0,
angles in range, shape / hole type / stack / electrical words, colours,
orientation, non-empty names) and the rules across one row's fields (a
slot width with a round hole, a simple stack with bottom sizes, a manual
flag set false with a value, a via hole not smaller than its size). A row
it refuses never reaches Altium; the error codes are the bridge's.

The bridge keeps a BACKSTOP and the checks that need the document: no
number reaches a converter before it is checked character by character
(RowsIntStr where it is used: Altium's StrToIntDef RAISES past 32 bits,
measured); fields known and writable (one lookup per name per call); an id
once; the table file's counts and row ranges (see rows.write_rows); then
guards, ids, layer names, a via's ring against its present size and
special strings. A caller that skips rows.py gets the backstop only: a value
within type and range but against a kind's rule (a zero width) is then
written as asked. The old rows text (`rows` / `rows_file`) still goes
through the bridge's own parse (RowsParseFlat), which also checks types.

## Numbers

NUMBERS TRAVEL AS SCALED INTEGERS (2026-11-10/11): the bridge never
parses or formats a decimal, so there is no decimal separator and no locale.
- `l` = Altium's own coordinate, 1/10000 mil (0.15 mm = 59055);
- `a` = 1/10000 degree (90 degrees = 900000);
- `i` = a plain integer (font id, colour, corner percentage ...).
Every integer is CANONICAL (digits, an optional `-`, no `+`, no leading zero,
not `-0`) and fits 32 bits, checked once at the parse, so equal numbers are
equal text: `=` / `!=` / guards / read-backs compare text, `< > <= >=`
compare the integers. A decimal anywhere a number is expected: `BAD_VALUE`
(rows) or `BAD_WHERE`. The client (src/eda_agent/tools/rows.py) does every
conversion from the field types `rows.list_kinds` gives; reading a length
back in mm gives the SHORTEST decimal that converts back to the same
coordinate (59055 reads 0.15), so a value read and written back never moves.
Every writer sets Altium's coordinates directly from the integers; no
decimal is made anywhere on the bridge side.

The SAME table drives `fields`, `where`, the reply and the writer. A field
whose value cannot be read on an object comes back `null`.

Every table starts with `id:s:r`, then the CONTEXT fields of its doc, then the
object's own fields:

| doc | context fields (read-only) |
|---|---|
| `pcblib` | `footprint`, `description` |
| `pcb` | `owner` (`free` / `component`), `designator`, `pattern` |
| `schlib` | `symbol`, `part`, `mode` |
| `sch` | `doc` (sheet path) |

## The id

`id` = the object's `I_ObjectAddress` as text on `pcblib` / `pcb` (schematic
components: their `UniqueId`). It names the object between ONE read and the
NEXT write. It is NOT kept across sessions, saves or reloads, and an object
deleted and created again (an undo may do this) has a new one. A stale id is
refused, never guessed.

`schlib` pins: `id` = `S:P`, the symbol's position in the library walk
(every symbol counted, from 1) and the pin's position among that symbol's
pins (every part and display mode, from 1). A schematic object's
`I_ObjectAddress` cannot be turned into text on AD 25.8 (it stopped the
script three times), so it is only compared. The position holds while no
symbol or pin is added or removed; every write carries `?symbol` (and may
guard any field, e.g. `?designator`), so a pin that moved in the walk is
refused, not written.

`schlib` pin `functions` are in Altium's own name form, `f1/f2/.../fn`
(what Altium's split of a pin name expects). A write sets
the pin's name to the list plus `/`, lets Altium split it
(`SetState_FunctionsFromName`) and puts the name back; an empty list is
refused (nothing clears the list).

`schlib` / `sch` drawing (`line`, `polyline`, `arc`): `id` = the walk
position and, after an `@`, the object's first point in coordinates (line
start, first vertex, arc centre): `S:P@x,y` in a library (P = position among
that symbol's objects of the kind, every part and display mode), `P@x,y` on
a sheet (P = position among the sheet's own objects of the kind). A write
finds the object only when position AND point still agree, so an object
moved, added or removed since the read is refused, not written in the place
of another. A sheet is walked FIRST LEVEL only: a placed part's drawing and
the sheet template are never listed nor written. Fields: `width` 0 smallest
/ 1 small / 2 medium / 3 large, `style` 0 solid / 1 dashed / 2 dotted / 3
dash dot (lines and polylines), `color` packed BGR, polyline `vertices`
(type `v`), `points` (read-only count), `start_shape` / `end_shape` by name
(None, Arrow, Solid Arrow, Tail, Solid Tail, Circle, Square), `shape_size`
0..3; line `x1 y1 x2 y2`; arc centre `x y`, `radius`, `start_angle`,
`end_angle`.

## Separators

A row's fields, and a `where`'s conditions, are joined by a BACKTICK
(`` ` ``, character 96). Not `;` or `|`: both occur in real values (pin
names and functions, descriptions, Altium's own design-rule strings), and
rows take NO escape sequences (2026-11-10). A backtick occurs in
none of them; a value that holds one cannot be written. Rows are joined by
`~~`, as in main's batch format. Main's own batch format (`GetBatchField`)
keeps `;`; rows read their own fields.
Typed into a PowerShell command line a backtick needs quoting; through
Python or the MCP tools it is an ordinary character.

## rows.read_rows

| Param | Meaning |
|---|---|
| scope | per doc, Altium narrows the walk with it (below) |
| `layer` | one layer by its user or internal name; resolved ONCE |
| `where` | conditions joined by a backtick (see Separators): `field op value`, op `=` `!=` `<` `>` `<=` `>=` `~` `!~` |
| `fields` | comma list of fields to return; default every field. `id` is always first. |
| `offset`, `limit` | paging over the matches in walk order; `limit` default 5000, 0 = none |

Scope per doc:

| doc | scope params |
|---|---|
| `pcblib` | `footprint` = name or wildcard pattern (default `*` = every footprint) |
| `pcb` | the FOCUSED board only (`RowsFocusedBoard`). `net`, `designator` (comma list), `pattern` (footprint name, wildcards), `owner` = `free` / `component` / `all` (default `all`), `bounds` = x1,y1,x2,y2; tracks / arcs also `poured` = `exclude` (default) / `include` / `only` -- pour copper is noise to routing edits. Each object is checked against the whole scope again, so a route only saves time, never changes the answer. A bad scope value: `BAD_SCOPE` |
| `schlib` | `symbol` = name or pattern (default the symbol on screen) |
| `sch` | `document_path` = one open sheet (default the one in front); its own drawing only (first level) |

`where` rules:
- the field must be in the kind's table (else `BAD_FIELD`);
- numbers: canonical integers; `=` / `!=` exact; `< > <= >=` numeric;
- text: `=` / `!=` ignore case; `~` / `!~` wildcards `*` `?`, case ignored;
- booleans: `=true` / `=false`;
- the conditions are parsed ONCE, before the walk.

Reply data:

```json
{"doc":"pcblib","kind":"track","units":"coord",
 "fields":["id","footprint","layer","width"],
 "rows":[["2235412","SOT23","TopOverlay",59055], ...],
 "count":2,"offset":0,"more":false,"scanned":118,"note":""}
```

- `rows` = arrays in `fields` order: numbers bare, text quoted, booleans bare,
  unreadable `null`.
- `count` = rows in this reply; `more` = true when `limit` cut the walk off
  (ask again with `offset` + `count`). Never a silent cap.
- `scanned` = objects of the kind the walk looked at (the narrowing's cost).

## rows.write_rows

| Param | Meaning |
|---|---|
| `table_file` | what rows.py sends: the rows as the FLAT TABLE, a file name in the bridge workspace (below) |
| `rows_file` | the rows text in a file in the workspace (lab scripts) |
| `rows` | the rows text inline: rows joined by `~~`, fields by a backtick (see Separators) |
| `profile` | `1` = add `profile` to the reply data (below) |
| `undo` | `false` = no undo record per object (pcblib pad / via only; below) |

UNDO=FALSE (2026-11-23, opt-in, `pcblib/pad` and `pcblib/via` only; any other
kind, or a value other than `true` / `false`, is refused `BAD_PARAM` before
anything is written). The writer leaves out the BeginModify / EndModify
messages Altium sends round each object for its undo record; the edit
bracket, the redraw and every read-back stay. Measured on 1000 changed pads:
write stage 1.69 -> 0.89 s, the whole call 2.5 -> 1.6 s, nothing read back
wrong. THE COST: Altium keeps no undo step for the write, and ONE undo
afterwards restores the state from before the PREVIOUS undo step -- it takes
back this write AND the edit before it. For a caller that keeps a copy of
the library first. Board writes always keep their undo record.
`BeginIgnoreChanges` round the write gained nothing in a library (2.33 s).

THE TABLE FILE (2026-11-11). The bridge's own parse of the rows text cost
2.5 s of a 1000-pad write in the script interpreter; rows.py has the rows
split already, so it writes the table the bridge would have built and the
bridge loads it with native list calls, looping once per row. UTF-8 with a
BOM, lines joined by CRLF, sections divided by a line holding only
character 1:
1. `ROWSTABLE 1`, N rows, M cells, `1` when a value held a line break
   (CR / LF travel as characters 16 / 17), else `0`;
2. the distinct keys of the cells (`id`, `?name` guards, written names);
3. the N ids, in row order;
4. each row's first cell, then (section 5) its end, cell indexes;
6. the M keys, lower case, each row's id first and a key once per row;
7. the M values, trimmed.
A value may not hold characters 1, 2, 16 or 17. The bridge refuses with
`BAD_ROWS` (nothing written) a file whose counts, row ranges (one after
another from cell 0 to M, whole numbers of at most 9 digits) or key list do
not hold, and checks each distinct key against the kind's table (`BAD_FIELD`,
`READ_ONLY_FIELD`). `tests/test_rows_table.py` holds the table to what the
old parse builds from the same rows.

THE PROFILE (`profile=1`, both commands): milliseconds per stage inside
Altium, each named after the stage that ENDS at its mark -- `load` (the
rows read), `parse`, `open` (the document found), `find` (walk 1), `write`
(walk 2 and the read-backs) or `walk` (a read), `reply` -- and `total`.
rows.py adds `client` (its checks and the table file), `round_trip` (send to
reply) and `outside` (round trip less total: the request and reply files,
main's polling and JSON handling). GetTickCount counts in ~16 ms steps.

A row, fields joined by a backtick: ``id=<id>`?<field>=<value>`<field>=<value>``
- `id` is required, once per call.
- `?field=value` is a GUARD: compared, not written. Any field of the table.
- `field=value` is a WRITE: only writable fields.
- The container guard is REQUIRED per doc, and also tells the writer where to
  look: `pcblib` `?footprint`, `schlib` `?symbol`, `sch` `?doc`. `pcb` has
  none: a `pcb` write takes the READ'S SCOPE PARAMETERS instead (net, layer,
  designator, pattern, owner, bounds, poured), which narrow its walk; with
  none it walks every object of the kind, stopping once every id is found.
- Values: integers as in the reply (Numbers), booleans `true` / `false`. A value may
  not hold a backtick or `~~`; `;`, `|` and `=` are plain characters in it
  (only the first `=` of a field splits name from value).

What happens:
1. Every row is checked before Altium is touched (rows.py, then the
   backstop): id present and unique, fields known, writes writable, values
   of the right type, layers resolvable. Any problem: `BAD_ROWS` / `BAD_FIELD` / `READ_ONLY_FIELD` /
   `BAD_VALUE`, nothing written.
2. WALK 1 finds each row's object by id (sorted lookup) and checks every
   guard. A row whose id is not found, or a guard that does not hold:
   `ROWS_REFUSED` naming each row, nothing written.
3. WALK 2 writes each object once (robots BeginModify / EndModify round each
   object), all inside ONE edit. Objects a walk hands out twice are written
   once and counted as `repeats`. A `pcb` writer may instead write the
   objects WALK 1 collected in a `TInterfaceList` (never freed: it holds
   Altium objects), as main's generic writer does -- no second walk. A board
   VIA whose mask or tenting was written is checked in the spatial index and
   re-indexed when it went stale (#208); other writes re-index by themselves
   and are not checked (six spatial searches per object, measured as most of
   a write's time).
4. Every written field is read back. Numbers exactly (arc angles: the same
   point on the circle, 0 = 3600000);
   TEXT READ-BACKS ARE EXACT (a case-only rename that did not take is
   caught), while text guards and where ignore case.

The writer NEVER adds or removes an object, and never moves anything it was
not given (moving a track's end does not drag what is connected to it).

Reply data:

```json
{"doc":"pcblib","kind":"track","rows":2,"written":2,"failed":0,"repeats":0,
 "results":[{"id":"2235412","ok":true,"width":47244}, ...],"note":""}
```

`ok` false = a write did not hold (the read-back differs); `note` says which.

## Errors

`MISSING_PARAMS`, `BAD_DOC`, `BAD_KIND` (lists the built kinds), `BAD_UNITS`,
`BAD_FIELD`, `READ_ONLY_FIELD`, `BAD_VALUE`, `BAD_WHERE`, `BAD_LAYER`,
`BAD_ROWS`, `ROWS_REFUSED`, `BAD_SCOPE` (an owner / bounds / poured value it
cannot take), `NO_PCBLIB` / `NO_PCB` / `NO_SCHLIB` / `NO_SCH`, `NO_FOOTPRINT`,
`NO_SYMBOL` (schlib: no symbol on screen and no `symbol=`).

Shared core helpers every kind uses rather than its own copy:
`RowsFocusedBoard` (the focused PcbDoc, path-checked, never another board),
`RowsArcSweep`, `RowsIntOf`, `RowsAngle` / `RowsAngleDeg`, the flat row
table (`RowsTakeRows`: the table file, or each row split ONCE by
`RowsParseFlat`), the stage profile (`RowsProfMark`), plus the read / write
helpers above.

## Building a kind (for the parallel work)

1. A field table function `Rows<Doc><Kind>Spec(Dummy : Integer) : String`.
2. A getter `Rows<Doc><Kind>Get(Obj, Field, ...context) : String` -- the
   field's value as text, numbers as the wire's integers, `''` when unreadable.
3. A setter `Rows<Doc><Kind>Set(Obj, Field, Value, ...) : Boolean`; the
   kind's value rules go in `rows_rules.py` (Python), not in Pascal.
4. `Rows<Doc><Kind>Read` / `Rows<Doc><Kind>Write` on the core's helpers,
   following `RowsPcbLib.pas` (`pcblib` / `track`, the worked template).
5. One line in `HandleRowsCommand` (Rows.pas) and one in `RowsKindsJson` --
   THE MERGER adds those, not the kind.

Units: a kind's own unit file, after `Generic.pas`, before `Rows.pas`. Reuse
the existing per-object helpers (`SetPCBProperty`, `SetSchProperty`,
`LibViaDetailJson` ...) rather than copying them, and name what was reused.
A writer takes its rows with `RowsTakeRows` and marks `open` / `find` round
its passes.
