# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Rows: read the objects of one kind narrowly, write them back by id.

``rows_read`` lets Altium narrow the walk (one object kind, a scope such as
a footprint pattern or a net, a layer, a ``where``) and returns only the
fields asked for, as compact arrays. The caller decides what to change and
``rows_write`` sends one row per object, keyed by the id the read gave and
guarded by any of its fields: every row is checked first, and one row that
no longer matches refuses the whole call. One call each way, however many
objects. The contract is docs/ROWS_CONTRACT.md.

NUMBERS TRAVEL AS SCALED INTEGERS. The bridge side never parses or formats
a decimal: a length (field type ``l``) is Altium's own coordinate, 1/10000
mil; an angle (``a``) is 1/10000 degree; an integer (``i``) is as it is.
Every unit conversion happens here, from the field types the bridge lists
(rows.list_kinds): the caller works in mm or mil (``units``), or in the raw
integers with ``units="coord"``.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from ..bridge import get_bridge
from ..units import MM_PER_MIL
from .rows_rules import check_rows

#: The docs a rows call can name.
DOCS = ("pcblib", "pcb", "schlib", "sch")

#: Between the fields of a row and between the conditions of a where. Not
#: ';' or '|': both occur in real values (pin functions, Altium's own rule
#: strings), and rows take no escapes. A backtick appears in neither.
SEP = "`"

#: The units a caller may work in. "coord" = the wire's own integers.
UNITS = ("mm", "mil", "coord")

#: Coordinates per mil and per degree on the wire.
PER_MIL = 10000
PER_DEGREE = 10000

#: The 32-bit range the bridge keeps every integer in.
INT_MAX = 2147483647

#: A where condition: field, operator, value. Longest operators first.
_COND = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(!=|<=|>=|!~|=|<|>|~)(.*)$", re.S)


def unit_for(doc: str, units: str) -> str | None:
    """The unit a call works in: the one named, else mil for the schematic
    docs (their objects sit on a 100 mil grid) and mm for the board ones.
    None for a unit that is not mm, mil or coord."""
    u = str(units or "").strip().lower()
    if u == "mils":
        u = "mil"
    if not u:
        return "mil" if doc in ("schlib", "sch") else "mm"
    return u if u in UNITS else None


def _number(key: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{key}: takes a number, not {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: takes a number, not {value!r}") from None


def _checked(key: str, n: int) -> str:
    if abs(n) > INT_MAX:
        raise ValueError(f"{key}: {n} is outside what Altium can hold")
    return str(n)


def _points(key: str, value: Any) -> list[tuple[Any, Any]]:
    """A point list as given: [[x, y], ...] or the text "x,y;x,y"."""
    if isinstance(value, str):
        pts = []
        for part in value.split(";"):
            if not part.strip():
                continue
            xy = part.split(",")
            if len(xy) != 2:
                raise ValueError(f"{key}: a point is x,y, not {part.strip()!r}")
            pts.append((xy[0].strip(), xy[1].strip()))
        return pts
    try:
        return [(p[0], p[1]) for p in value if len(p) == 2]
    except TypeError:
        raise ValueError(f"{key}: takes points [[x, y], ...], not {value!r}") from None


def to_wire(key: str, ftype: str, value: Any, unit: str) -> str:
    """A value of a field of type ftype, given in unit, as the wire carries
    it. Raises ValueError for a value the field cannot take. A point list
    (type v) is every x and y a length, joined as x,y;x,y."""
    if ftype == "v":
        pts = _points(key, value)
        if len(pts) < 2:
            raise ValueError(f"{key}: takes two or more points, not {value!r}")
        return ";".join(f"{to_wire(key, 'l', x, unit)},{to_wire(key, 'l', y, unit)}"
                        for x, y in pts)
    if ftype in ("l", "a", "i"):
        v = _number(key, value)
        if ftype == "i" or unit == "coord":
            if not v.is_integer():
                raise ValueError(f"{key}: takes a whole number, not {value!r}")
            return _checked(key, int(v))
        if ftype == "a":
            return _checked(key, round(v * PER_DEGREE))
        if unit == "mil":
            return _checked(key, round(v * PER_MIL))
        return _checked(key, round(v / MM_PER_MIL * PER_MIL))
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    if SEP in text or "~~" in text:
        raise ValueError(f"{key}: a value may not hold a backtick or '~~'")
    return text


def _mm(n: int) -> float:
    """A coordinate in mm, the SHORTEST decimal that comes back to n: 59055
    reads 0.15, yet any coordinate read and written back is unchanged."""
    exact = n * MM_PER_MIL / PER_MIL
    for places in range(0, 8):
        x = round(exact, places)
        if round(x / MM_PER_MIL * PER_MIL) == n:
            return x
    return exact


def from_wire(ftype: str, value: Any, unit: str) -> Any:
    """A value the bridge replied with, in the caller's unit. A point list
    (type v) comes back as [[x, y], ...]."""
    if ftype == "v" and isinstance(value, str) and value:
        try:
            return [[from_wire("l", int(x), unit), from_wire("l", int(y), unit)]
                    for x, y in (p.split(",") for p in value.split(";"))]
        except ValueError:
            return value
    if ftype not in ("l", "a") or unit == "coord" or value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    n = int(value)
    if ftype == "a":
        return round(n / PER_DEGREE, 4)
    if unit == "mil":
        return round(n / PER_MIL, 4)
    return _mm(n)


def _type_of(types: dict[str, str], key: str) -> str:
    return types.get(str(key).strip().lstrip("?").lower(), "")


def wire_rows(rows: list[dict[str, Any]], types: dict[str, str] | None = None,
              unit: str = "coord") -> list[dict[str, str]]:
    """Rows with every value as the wire carries it (numbers of a length or
    angle field scaled from unit, see the module head); a None value drops
    its field. A key starting with ``?`` is a guard (compared, not written).
    """
    types = types or {}
    out = []
    for i, row in enumerate(rows):
        if not str(row.get("id") or "").strip():
            raise ValueError(f"row {i}: id is required")
        w: dict[str, str] = {}
        for key, value in row.items():
            if value is None:
                continue
            name = str(key).strip()
            if not name or "=" in name or SEP in name:
                raise ValueError(f"row {i}: bad field name {key!r}")
            ftype = "s" if name == "id" else _type_of(types, name)
            try:
                w[name] = to_wire(name, ftype, value, unit)
            except ValueError as exc:
                raise ValueError(f"row {i}: {exc}") from None
        out.append(w)
    return out


def join_rows(wired: list[dict[str, str]]) -> str:
    """Wire rows as the text the bridge reads: ``id=..`?guard=..`field=..``
    joined by ``~~``."""
    return "~~".join(SEP.join(f"{k}={v}" for k, v in w.items()) for w in wired)


def encode_rows(rows: list[dict[str, Any]], types: dict[str, str] | None = None,
                unit: str = "coord") -> str:
    """Rows as the wire format (wire_rows, then join_rows)."""
    return join_rows(wire_rows(rows, types, unit))


def encode_where(where: str | list[str], types: dict[str, str] | None = None,
                 unit: str = "coord") -> str:
    """A where as the wire carries it: conditions joined by the backtick, a
    number of a length / angle / integer field scaled from unit. A string
    is taken as conditions already joined by the backtick."""
    conds = where.split(SEP) if isinstance(where, str) else list(where)
    out = []
    for cond in conds:
        text = str(cond).strip()
        if not text:
            continue
        m = _COND.match(text)
        ftype = _type_of(types or {}, m[1]) if m else ""
        if m and ftype in ("l", "a", "i") and m[2] not in ("~", "!~"):
            text = f"{m[1]}{m[2]}{to_wire(m[1], ftype, m[3].strip(), unit)}"
        out.append(text)
    return SEP.join(out)


def _scope_wire(scope: dict[str, str] | None, unit: str) -> dict[str, str]:
    """Scope keys, one parameter each; a board's bounds are lengths."""
    out = {}
    for key, value in (scope or {}).items():
        if str(key) == "bounds":
            parts = value if isinstance(value, (list, tuple)) else str(value).split(",")
            value = ",".join(to_wire("bounds", "l", p, unit) for p in parts)
        out[str(key)] = str(value)
    return out


#: Characters the table file uses itself (section line, type marker, the
#: swapped line breaks); a value may not hold them.
_TABLE_RESERVED = ("\x01", "\x02", "\x10", "\x11")


def table_text(wired: list[dict[str, str]]) -> str:
    """Wire rows as the FLAT TABLE the bridge loads without parsing
    (table_file; RowsTableTake in RowsCore.pas): lines joined by CRLF,
    sections divided by a line holding chr(1) -- a header (ROWSTABLE 1,
    rows, cells, whether values had line breaks), the distinct keys, the
    ids, each row's first cell and its end, then every key and every value,
    row after row, each row's id first. Keys in lower case, values trimmed,
    as the bridge's own parse leaves them. Raises ValueError for a row the
    table cannot carry (a key twice, a reserved character)."""
    keys: list[str] = []
    vals: list[str] = []
    ids: list[str] = []
    starts: list[str] = []
    ends: list[str] = []
    distinct: dict[str, None] = {}
    breaks = False
    for i, w in enumerate(wired):
        row: dict[str, str] = {}
        for k, v in w.items():
            key = str(k).strip().lower()
            if key in row:
                raise ValueError(f"row {i}: field {key!r} given twice")
            row[key] = str(v).strip()
        rid = row.pop("id", "")
        if not rid:
            raise ValueError(f"row {i}: id is required")
        starts.append(str(len(keys)))
        for key, value in [("id", rid), *row.items()]:
            if not key or any(c in key for c in ("\r", "\n", *_TABLE_RESERVED)):
                raise ValueError(f"row {i}: bad field name {key!r}")
            if any(c in value for c in _TABLE_RESERVED):
                raise ValueError(f"row {i}: {key} holds a control character the rows "
                                 "table reserves")
            if "\r" in value or "\n" in value:
                if key == "id":
                    raise ValueError(f"row {i}: an id may not hold a line break")
                breaks = True
                value = value.replace("\r", "\x10").replace("\n", "\x11")
            keys.append(key)
            vals.append(value)
            distinct[key] = None
        ends.append(str(len(keys)))
        ids.append(rid)
    head = ["ROWSTABLE 1", str(len(ids)), str(len(keys)), "1" if breaks else "0"]
    sections = [head, list(distinct), ids, starts, ends, keys, vals]
    return "\r\n\x01\r\n".join("\r\n".join(sec) for sec in sections) + "\r\n"


def _table_file(bridge, wired: list[dict[str, str]]):
    """Write the rows as a table file in the bridge workspace (table_text)
    and return its path, or None when the bridge has no workspace (then the
    rows go inline and the bridge parses them)."""
    folder = getattr(getattr(bridge, "config", None), "workspace_dir", None)
    if folder is None:
        return None
    path = folder / f"rows_{uuid.uuid4().hex}.tbl"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(table_text(wired))
    return path


def _profiled(reply: Any, t0: float, t1: float) -> Any:
    """A reply whose data carries the bridge's ``profile`` gets the client's
    side too: ``client`` = ms before sending, ``round_trip`` = ms from
    sending to the reply, ``outside`` = the round trip less Altium's total
    (the request and reply files, main's polling and JSON handling)."""
    data = reply.get("data", reply) if isinstance(reply, dict) else None
    prof = data.get("profile") if isinstance(data, dict) else None
    if isinstance(prof, dict):
        prof["client"] = round((t1 - t0) * 1000)
        prof["round_trip"] = round((time.perf_counter() - t1) * 1000)
        if isinstance(prof.get("total"), (int, float)):
            prof["outside"] = prof["round_trip"] - prof["total"]
    return reply


def rows_as_dicts(reply: dict[str, Any]) -> list[dict[str, Any]]:
    """A rows_read reply's arrays as one dict per object."""
    data = reply.get("data", reply)
    fields = data.get("fields") or []
    return [dict(zip(fields, values)) for values in data.get("rows") or []]


#: The rows.list_kinds entry per bridge, per (doc, kind).
_KINDS: dict[int, dict[tuple[str, str], dict[str, Any]]] = {}


async def kind_entry(bridge, doc: str, kind: str) -> dict[str, Any]:
    """The rows.list_kinds entry of (doc, kind) -- fields with type and
    writable, the container guard -- asked of the bridge once."""
    known = _KINDS.get(id(bridge))
    if known is None:
        reply = await bridge.send_command_async("rows.list_kinds", {})
        data = reply.get("data", reply) if isinstance(reply, dict) else {}
        known = {(e.get("doc"), e.get("kind")): e for e in data.get("kinds") or []}
        if known:
            _KINDS[id(bridge)] = known
    return known.get((doc, kind), {})


def types_of(entry: dict[str, Any]) -> dict[str, str]:
    """A kind entry's field types by name."""
    return {f.get("name"): f.get("type") for f in entry.get("fields") or []}


async def field_types(bridge, doc: str, kind: str) -> dict[str, str]:
    """The field types of (doc, kind), asked of the bridge once."""
    return types_of(await kind_entry(bridge, doc, kind))


def decode_reply(reply: dict[str, Any], types: dict[str, str], unit: str) -> dict[str, Any]:
    """A read or write reply with its lengths and angles in unit. The bridge
    client hands back the reply's data itself; a ``{"data": ...}`` wrapper
    is read too."""
    if not isinstance(reply, dict) or unit == "coord":
        return reply
    data = reply.get("data", reply)
    if not isinstance(data, dict):
        return reply
    fields = data.get("fields")
    if isinstance(fields, list) and isinstance(data.get("rows"), list):
        kinds = [_type_of(types, f) for f in fields]
        data["rows"] = [[from_wire(t, v, unit) for t, v in zip(kinds, row)]
                        for row in data["rows"]]
    if isinstance(data.get("results"), list):
        data["results"] = [
            {k: from_wire(_type_of(types, k), v, unit) for k, v in r.items()}
            if isinstance(r, dict) else r for r in data["results"]]
    if "units" in data:
        data["units"] = unit
    return reply


def register_rows_tools(mcp) -> None:
    """Register the rows tools."""

    @mcp.tool()
    async def rows_list_kinds() -> dict[str, Any]:
        """The object kinds rows_read / rows_write handle, with their fields.

        Returns:
            ``kinds``: one entry per (doc, kind) with ``container_guard``
            (the ``?`` guard every written row must carry) and ``fields``
            (name; type s text / b boolean / l length / a angle / i
            integer; writable).
        """
        return await get_bridge().send_command_async("rows.list_kinds", {})

    @mcp.tool()
    async def rows_read(
        doc: str,
        kind: str,
        scope: dict[str, str] | None = None,
        layer: str = "",
        where: str | list[str] = "",
        fields: str = "",
        offset: int = 0,
        limit: int = 5000,
        units: str = "",
        library_path: str = "",
        profile: bool = False,
        document_path: str = "",
    ) -> dict[str, Any]:
        """Read the objects of ONE kind, narrowed inside Altium.

        For many objects at once: one call, only what is asked for. Ids in
        the reply name the objects for rows_write until they change.

        Args:
            doc: "pcblib", "pcb" (the focused board), "schlib" or "sch"
                (one schematic sheet, its own drawing only).
            kind: One object kind, e.g. "track"; rows_list_kinds lists them.
            scope: Where to look, per doc. pcblib: {"footprint": "BGA*"};
                pcb: net, layer, designator, pattern, owner (free /
                component / all), bounds, poured (tracks / arcs); schlib:
                {"symbol": "LM*"} (default: the symbol on screen).
            layer: One layer by its user or Altium name.
            where: Conditions, all must hold: a list such as
                ["width=0.15", "length>1"], or one string joined by a
                backtick. Each is field op value, op = != < > <= >= ~
                (wildcard) !~.
            fields: Comma list of fields to return; default all. id is
                always first.
            offset: Skip this many matches (paging).
            limit: At most this many rows (0 = no limit); "more" says
                when there are more.
            units: "mm", "mil" (default mil for schlib, mm otherwise) or
                "coord" (the raw integers: 1/10000 mil, 1/10000 degree).
            library_path: For a library doc; default the focused one.
            profile: Add ``profile``: ms per stage inside Altium, the
                total, and the round trip seen from here.
            document_path: sch only: an open sheet; default the one in
                front.

        Returns:
            ``fields``, ``rows`` (arrays in field order), ``count``,
            ``offset``, ``more``, ``scanned``, ``note``.
        """
        unit = unit_for(doc, units)
        if unit is None:
            return {"success": False, "error": f"units must be one of {UNITS}, not {units!r}"}
        if doc not in DOCS:
            return {"success": False, "error": f"doc must be one of {DOCS}"}
        bridge = get_bridge()
        types = await field_types(bridge, doc, kind)
        try:
            params: dict[str, Any] = {"doc": doc, "kind": kind}
            params.update(_scope_wire(scope, unit))
            params.update({"layer": layer, "where": encode_where(where, types, unit),
                           "fields": fields, "offset": str(int(offset)),
                           "limit": str(int(limit)), "library_path": library_path})
            if document_path:
                params["document_path"] = document_path
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if profile:
            params["profile"] = "1"
        t0 = time.perf_counter()
        reply = await bridge.send_command_async("rows.read_rows", params)
        return decode_reply(_profiled(reply, t0, t0), types, unit)

    @mcp.tool()
    async def rows_write(
        doc: str,
        kind: str,
        rows: list[dict[str, Any]],
        scope: dict[str, str] | None = None,
        units: str = "",
        library_path: str = "",
        profile: bool = False,
        undo: bool = True,
        document_path: str = "",
    ) -> dict[str, Any]:
        """Write a different value to each object, by the ids rows_read gave.

        All or nothing: every row is checked first, then every object is
        found by id and every guard compared; one miss writes nothing.
        Never adds or removes an object.

        Args:
            doc: As in rows_read.
            kind: As in rows_read.
            rows: One dict per object: "id" (required), guards as
                "?field": value (compared, not written; pcblib rows need
                "?footprint", schlib "?symbol"), then the fields to write.
                E.g. {"id": "123", "?footprint": "SOT23", "?width": 0.15,
                "width": 0.12}.
            scope: pcb only: the read's scope (net, layer ...), so the
                write finds its objects without walking the whole board.
            units: As in rows_read.
            library_path: For a library doc; default the focused one.
            profile: As in rows_read; ``client`` = ms spent here before
                sending (checks, the table file).
            undo: pcblib pad / via only. False skips Altium's undo record
                for each object: about half the write time on many
                objects, but the write gets no undo step and one undo
                afterwards also takes back the edit before it. Keep a
                copy of the library first.
            document_path: sch only: an open sheet; default the one in
                front.

        Returns:
            ``rows``, ``written``, ``failed`` (a write that did not read
            back), ``repeats``, ``results`` (each written field read back,
            with ``ok``), ``note``.
        """
        unit = unit_for(doc, units)
        if unit is None:
            return {"success": False, "error": f"units must be one of {UNITS}, not {units!r}"}
        if doc not in DOCS:
            return {"success": False, "error": f"doc must be one of {DOCS}"}
        if not rows:
            return {"success": False, "error": "rows is empty"}
        if any(not str(r.get("id") or "").strip() for r in rows):
            return {"success": False, "error": "every row needs an id"}
        if not undo and not (doc == "pcblib" and kind in ("pad", "via")):
            return {"success": False, "error": "BAD_PARAM: undo=false is for "
                    f"pcblib pad and via writes only, not {doc}/{kind}"}
        bridge = get_bridge()
        entry = await kind_entry(bridge, doc, kind)
        t0 = time.perf_counter()
        if not entry:
            return {"success": False, "error": f"BAD_KIND: no rows kind {doc}/{kind}"}
        types = types_of(entry)
        try:
            wired = wire_rows(rows, types, unit)
            params: dict[str, Any] = {"doc": doc, "kind": kind}
            params.update(_scope_wire(scope, unit))
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        # Every check that needs nothing from Altium, here: the bridge keeps
        # only a backstop and the checks against the document.
        code, problem = check_rows(doc, kind, wired, entry)
        if code:
            return {"success": False, "error": f"{code}: {problem}"}
        if not undo:
            params["undo"] = "false"
        params["library_path"] = library_path
        if document_path:
            params["document_path"] = document_path
        if profile:
            params["profile"] = "1"
        try:
            path = _table_file(bridge, wired)
        except ValueError as exc:
            return {"success": False, "error": f"BAD_ROWS: {exc}; nothing was changed"}
        if path is None:
            params["rows"] = join_rows(wired)
            t1 = time.perf_counter()
            reply = await bridge.send_command_async("rows.write_rows", params)
            return decode_reply(_profiled(reply, t0, t1), types, unit)
        params["table_file"] = path.name
        t1 = time.perf_counter()
        try:
            reply = await bridge.send_command_async("rows.write_rows", params)
        finally:
            path.unlink(missing_ok=True)
        return decode_reply(_profiled(reply, t0, t1), types, unit)
