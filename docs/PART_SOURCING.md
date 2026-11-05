# Part sourcing

Every part provider the server can query, what each one returns, and
which credentials it needs. No provider is enabled by default and there
is no fallback order.

Back to the [README](../README.md).

`part_search` queries **every** enabled part provider and merges the results,
each hit attributed to the source that found it. `part_fetch` then pulls one
part's detail from a provider you name.

Sources come in **two kinds**, merged into one result but never conflated. A
**library** provider yields geometry you can place. A **catalogue** provider
yields part identity, a datasheet and stock, and no geometry at all. Every hit
carries its `kind`, because discovering that a distributor hit has no symbol
after picking the part is the expensive way to learn it. They are not tiers and
neither is a fallback for the other: a catalogue tells you *which* part to use
and hands you the datasheet every check here measures against, and a library
tells you whether you can draw it.

| Provider | Kind | What it is | Network | Credential |
|---|---|---|---|---|
| `altium_local` | library | The `.SchLib` libraries already on this machine. The only source that answers whether you **already own** a part, which is what stops an import creating a second symbol with a slightly different name. Reads the OLE files directly, so it works with Altium closed. Nothing to download: a hit is already an Altium symbol. Point it with `EDA_AGENT_ALTIUM_LIBRARIES` | no | no |
| `digikey` | catalogue | Digi-Key's catalogue: MPN, datasheet, lifecycle and live stock. Needs an OAuth client from their developer portal via `DIGIKEY_CLIENT_ID` and `DIGIKEY_CLIENT_SECRET` | yes | yes |
| `easyeda` | library | EasyEDA / LCSC component data. Fetch by LCSC part number works; **search is unavailable** because the upstream endpoint was withdrawn | yes | no |
| `element14` | catalogue | element14 (Farnell, Newark): MPN, datasheet and stock. Needs `ELEMENT14_API_KEY`; `ELEMENT14_STORE` picks the regional storefront, which changes the catalogue you see | yes | yes |
| `kicad_local` | library | The libraries KiCad installed on this machine. A fetch resolves the symbol's footprint reference against the installed `.pretty` libraries and the footprint's model reference against the `3dmodels` tree, so a hit can be a whole part with a 3D body (18003 of the 22728 symbols shipped with KiCad 10.0.1 name a footprint). No MPN or datasheet for most entries | no | no |
| `mouser` | catalogue | Mouser's catalogue: MPN, datasheet and stock. Needs `MOUSER_API_KEY` | yes | yes |
| `nexar` | catalogue | Nexar, the API behind Octopart: aggregates offers across many distributors at once. Needs `NEXAR_CLIENT_ID` and `NEXAR_CLIENT_SECRET` | yes | yes |
| `partreel` | library | An open registry of verified KiCad parts, no login and no key: `/api/v1/parts.json` serves 21,657 parts. Yields KiCad files, usable in Altium through `lib_kicad_import`. Points at PartReel the way the Digi-Key client points at Digi-Key; `PARTS_REGISTRY_URL` redirects it at any API-compatible registry, since the API shape is the contract rather than the host. Run by a third party, proposed in issue #12 by its operator | yes | no |
| `public_libraries` | library | Openly published KiCad libraries, no login and no key: KiCad's own 12,011 footprints, Digi-Key's 936, and JLCPCB's 20 symbol libraries plus footprints. Mostly **land patterns rather than symbols**, which is what you want once the part is chosen but its geometry is not. Indexed through GitHub's documented API, one request per repository, cached on disk for a week. `EDA_AGENT_CACHE_DIR` relocates the cache | yes | no |
| `tme` | catalogue | TME: MPN, datasheet and stock, strongest on European availability. Requests are HMAC signed; needs `TME_TOKEN` and `TME_SECRET`, and `TME_COUNTRY` selects the market | yes | yes |

**No provider is a default and none is preferred.** All are searched equally,
and the merged order is alphabetical by provider then part, which is not a
relevance ranking: do not read the first hit as the best one. `part_fetch`
requires the provider name rather than supplying one, so a fetch always states
which source it trusts. Tests enforce this rather than leaving it to
convention, because a default parameter or a preference sort would quietly make
one source the answer to every query.

A provider that cannot answer reports **why** instead of returning nothing.
"The endpoint is gone" and "no such part exists" are different answers, and
only one of them is a reason to stop looking.

Select a subset with `EDA_AGENT_PART_PROVIDERS=kicad_local,altium_local` (the
variable selects, it never ranks). `PARTS_REGISTRY_URL` names the registry the
`partreel` client queries; there is no default, so that source stays off until
you choose one. Call `part_search` with an empty query to list the providers
and who operates them.

**Four of the ten answer on a fresh install**, none of them needing a
credential: `altium_local` and `kicad_local` read libraries already on disk,
`partreel` queries an open registry, and `public_libraries` indexes openly
published KiCad libraries from GitHub. That is deliberately more than one, so
no single free source is load-bearing. The five catalogues each need their own
credential and none ships with one, and EasyEDA's search endpoint was withdrawn
upstream (fetch by LCSC part number still works). Every source that cannot answer reports itself unavailable, names the
environment variable it wants, and says so per provider in the search result
rather than folding into an empty list.

A client pointing at its own service is not a preference: the Digi-Key client
points at Digi-Key, and `partreel` points at PartReel. What neutrality means
here is the absence of RANKING, and that is enforced by tests rather than left
to intent. No source is consulted as a fallback when another returns thin
results, none can reach the front of a merged list, and there is deliberately
no "default provider" setting to point anywhere.

**What was verified, and what was not.** Every catalogue endpoint above was
probed live before it was written into the code: a 401 or 403 proves the host
and path exist and refused only for lack of a credential. Three further
candidates were probed and **dropped** for answering 404 on the recalled URL
rather than being shipped as plausible guesses. What that probing could *not*
establish, without a paid credential, is the request and response shapes. So
every catalogue publishes `verified_live: false`, and the parsers are written
to degrade a single hit on a renamed field rather than assume a shape and lose
the whole search. The flag flips only when a client has actually run against
the live API.

**Access policy of the hosts, checked rather than assumed.** Every host these
providers reach was checked for a `robots.txt` before anything was built
against it. That check changed the design once: `gitlab.com/robots.txt` carries
`Disallow: /api/v*`, and the GitLab API is where KiCad's canonical *symbol*
repository lives, so `public_libraries` does not touch GitLab and serves
footprints from GitHub instead. KiCad's symbols are already covered by
`kicad_local`, which reads them off disk. GitHub's API is used as documented,
with a User-Agent naming this project rather than impersonating a browser, one
recursive request per repository instead of directory walking, a week-long disk
cache so repeat searches cost nothing, and rate limiting treated as "back off"
rather than "no results". PartReel's `robots.txt` allows all agents and names
`ClaudeBot` and `GPTBot` explicitly. TrustedParts was evaluated and **dropped**:
it returns 403 to non-browser clients, so access is by arrangement rather than
open, and nothing here works around that.

One measured behaviour is worth naming, because it is the failure this layer
exists to prevent: **Mouser answers an invalid API key with HTTP 200** and an
`Errors` array. A client that judged success by status code would report a
rejected credential as a search that ran and found nothing. Payload-level error
detection is in the shared base class, not in five copies, and a mutation test
confirms removing it breaks the guard.

Each hit carries `formats`, `usable_in` and `import_with`: the tool that turns
that hit into a real part on the active backend. Those live on the provider,
not on the part, so without them a result gives no way to tell a lead from a
dead end. `import_with` is derived from the same map that gates a provider's
Altium claim, so it can only ever name an importer that exists, and it comes
back empty (never a guess) for a format nothing reads.

A hit is a lead, not a verified part. The `provenance` and `license` fields
report what the provider claims about where its geometry came from, and a blank
means unknown rather than permissive. Audit any imported footprint against the
manufacturer land pattern with `lib_audit_footprint_vs_datasheet` before
trusting it.

Pass `download_dir` to `part_fetch` to also write a provider's library files
there (off by default, since it writes to disk). Only known artefact kinds are
taken, and each is saved with the extension this code expects rather than one
read out of the payload.

Downloaded files are checked against the KiCad installed on this machine,
because a registry can publish a **newer** s-expression format than your KiCad
can open. Measured against the live service: PartReel ships format `20260206`
while KiCad 10.0.1 writes `20251024`, and KiCad's symbol parser refuses the
newer file outright ("Unable to load library") though its footprint parser
accepts it. Any such file comes back with a `*_warning` naming both versions,
rather than looking like a clean download.

## Getting a KiCad-format part into Altium

Two of the three providers publish KiCad format, so on the Altium side a hit
would otherwise be a dead end. `lib_kicad_import` reads `.kicad_sym` and
`.kicad_mod` and produces the same thing `lib_easyeda_import` does: with
`target=altium`, an ordered plan of this server's own library tools, run
tool by tool. Altium's binary library formats are never synthesized.

The two importers share one neutral geometry model and one Altium emitter, so
they cannot drift apart: a fix to pad shapes or arc handling lands in both. The
s-expression reader is written here rather than taken as a dependency, which
keeps the escaping rules (a footprint named `2.5"`, a description containing
parentheses) verifiable instead of trusted.

Eight things about the format are silent when handled wrongly, and all eight
produce something that still looks like a converted part:

- **Derived symbols.** Over half of KiCad's standard entries (12209 of 22728 in
  10.0.1) carry no geometry at all: they are `(extends "PARENT")` and inherit
  the parent's pins and body, restating only the properties that differ. That
  link is followed, with the child's own values winning and everything it does
  not restate inherited. Not following it yields a part with no pins.
- **Multi-part components.** A quad gate keeps each gate in its own unit, all
  drawn at the same coordinates. They convert in one call to a real Altium
  multi-part symbol (`part_count` plus per-pin `owner_part_id`), not to N
  symbols to merge by hand; pass `unit=N` to take a single sub-part instead.
  Merging units into a flat symbol would stack every unit's pins on the same
  points and still look converted.

  Supply rails come across whichever way the source expresses them. A symbol
  that puts them in **unit 0** (shared by every unit; 678 unit-0 sub-symbols
  in KiCad 10.0.1 carry pins) maps straight onto Altium's `owner_part_id=0`,
  so a CD4001 becomes four gates sharing one Vdd/Vss. A symbol that gives them
  their **own unit** instead keeps that structure, and a warning names the
  shared alternative with the exact edit rather than silently reinterpreting
  what the file says. Both forms are legitimate; only one of them is what the
  file actually contains.
- **Pin electrical type.** Carried across rather than flattened, because it is
  what ERC reasons about: an open-collector output recorded as passive stops
  ERC asking for its pull-up, and two of them driving one net stops being a
  reported conflict. KiCad's `open_collector`, `open_emitter` and `tri_state`
  map to Altium's `open_collector`, `open_emitter` and `hiz` (1827, 119 and
  1858 pins respectively in KiCad 10.0.1). `no_connect` becomes passive, and
  that one is a genuine gap rather than a choice: Altium's pin vocabulary has
  no "not connected" (an unused pin carries a No-ERC directive instead).
- **Hidden pins.** Kept, and kept hidden (5378 of the 106032 pin definitions
  in KiCad 10.0.1 are hidden). Dropping them would lose real supply and
  no-connect pins; showing them would clutter every symbol that hides them.
  Both the modern `(hide yes)` and the older bare `hide` spelling are read,
  since a library can predate the KiCad that opens it.
- **Body styles.** `NAME_1_1` and `NAME_1_2` are the same unit drawn two ways
  (KiCad's DeMorgan alternate), with the same pins. Taking both duplicates
  every pin, so one style is converted and the other is reported.
- **Rounded rectangle pads.** Used by 116 of the 206 SMD footprints sampled
  from KiCad 10.0.1, and Altium supports the shape natively, so it is not
  flattened to a plain rectangle. The corner value does not carry over
  directly: Altium's
  documentation defines its percentage against *half* the shortest pad side,
  while KiCad's `roundrect_rratio` is measured against the *whole* shorter
  side, so the conversion is a factor of two. Multiplying by 100 would halve
  every corner radius and look entirely plausible.
- **Y axis.** `.kicad_sym` is Y-up like the neutral model, `.kicad_mod` is
  Y-down. A sign error mirrors the land pattern.
- **Pin angles and arcs.** KiCad's pin angle already matches the neutral
  convention and passes through untouched (EasyEDA's is 180 degrees off, and
  the asymmetry is deliberate). Arcs are stored as start/mid/end, so the radius
  and sweep are recovered from the circle through those three points.

A local hit can also carry its **3D body**. KiCad ships STEP models and
Altium's linker takes STEP, so `part_fetch` resolves the footprint's model
reference against the installed `3dmodels` tree and `lib_kicad_import` adds a
`lib_link_3d_model` step for it (while the `.PcbLib` is still the active
document, which is where that tool has to run). The path has to be one that
resolved: the tool loads the file, so a guess would either fail on execution or
attach the wrong shape. An unresolved reference is reported instead.

Generated KiCad files are checked against **KiCad's own parser** (`kicad-cli`),
not just re-read by this code. That matters because this reader is lenient by
design and a round trip through it cannot see output KiCad refuses: the writer
once emitted two graphic-style tokens on an inverted pin, which read back as a
merely-missing bubble here and as `Unable to load library` in KiCad. Those
tests skip when KiCad is absent.

Anything with no faithful Altium equivalent comes back in `warnings` rather
than being quietly approximated. The library API takes one hole diameter and
no plating flag, so a **slotted** drill is emitted round and an **unplated**
hole is emitted plated: both are the right size and the wrong thing, and
neither would show up anywhere downstream. Custom and trapezoid pads are
emitted as their bounding rectangle.

**Solder-paste and mask apertures are never emitted as pads.** Fine-pitch
chip footprints subdivide paste with apertures that carry no copper (332 of
the 6902 pads in KiCad 10.0.1's sampled libraries), and an Altium pad always
carries copper, so emitting one would short the pads the aperture exists to
subdivide. They are skipped because of their layer, not because they usually
lack a designator, and reported as apertures with the advice to draw them as
paste-layer regions.

**Active-low and clock pin markers** are read but cannot be written. `ISch_Pin`
has the slots (`Symbol_OuterEdge` for the inversion bubble, `Symbol_InnerEdge`
for the clock wedge) and `lib_add_pins` has no field for either, so setting
them needs a bridge change. They are reported per part rather than dropped
silently, because an active-low pin drawn plain states the opposite of the
truth. Both of KiCad's spellings count: `inverted` (the bubble) and `*_low`
(IEEE's wedge) mean the same thing.

These checks live in the shared emitter, so they apply to `lib_easyeda_import`
too.

Check the result against the manufacturer land pattern with
`lib_audit_footprint_vs_datasheet` before using it.

## KiCad

KiCad support talks to a running KiCad over its API socket, so - unlike the Altium side - there are no scripts to install and no extra packages. Requirements: KiCad 10, its API enabled (Preferences → Plugins → Enable KiCad API), and the board open in the PCB editor. KiCad 10.0 answers board requests only, so schematic work goes through the `.kicad_sch` file and `kicad-cli`.

The KiCad backend covers, at parity with what KiCad's API and CLI expose:

- **Review** - an EDA-agnostic design review (annotation, connectivity, shorts, decoupling, net classes) that runs the same engine on the PCB and, via the netlist, on the schematic; plus a one-call `kicad_full_review` that adds DRC, ERC, and schematic↔PCB comparison.
- **Checks** - geometric DRC and schematic ERC via KiCad's own `kicad-cli`.
- **Reads** - footprints, pads, tracks, vias, zones, shapes, text, stackup, layers, net classes, board outline, project info, netlist, and a consolidated BOM.
- **Exports** - every `kicad-cli` format: Gerbers, drill, STEP/GLB/VRML/STL/3D-PDF, PDF/SVG/DXF, position files, IPC-2581, ODB++, IPC-D-356, plus schematic BOM/netlist/PDF/SVG.
- **Authoring** - place/move/rotate/lock components, edit values, and create tracks, vias, zones, text, and graphics.
- **Calculators** - the same trace-width, impedance, termination, length-match, and thermal-via sizing tools as the Altium backend (pure physics, EDA-independent).

The neutral tools (`review_design`, `run_drc`, `run_erc`, `get_board_info`, `list_components`, `list_nets`) work on whichever backend is active.

> If you'd rather not register the script globally, you can also open `Altium_API.PrjScr` via **File > Open...** and launch `StartMCPServer` from the **Run Script...** dialog the same way; the dialog picks up any loaded script project.

