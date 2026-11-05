# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every Altium menu item and the command it runs, read from Altium's install.

A menu click needs the foreground, a moving pointer and one popup read per
level, and it fails whenever a popup lays out slowly. Yet every menu item
is only a caption over a process call, and Altium declares them all in
its resource files: <install>\\System\\*.rcs holds the menu trees
("Tree ... Caption=", nested), "Link ... PLID=" leaves, and the "PL" entry
each leaf points at, with its Command, Params and shortcut.

So a menu path can be resolved to "PCB:EditRules" and run through the
bridge with no menu walk at all, a menu can be searched by words in any
language Altium ships, and a wrong path can be refused before anything is
clicked. This is what the default install declares. A user's customised
menus are not read, so a click is still the check that a path exists on
screen.

Altium's program files, read only. No design file is touched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import altium_locale

#: The menu bar each editor shows, by the document kind the bridge
#: reports. The trees are named in AdvPcb.rcs and AdvSch.rcs.
EDITOR_MENUS = {
    "PCB": "MNPCBMenu",
    "PCBLIB": "MNPCBLibMenu",
    "SCH": "MNSchematicMenu",
    "SCHLIB": "MNSchLibMenu",
}

_TOKEN = re.compile(r"//[^\n]*|'(?:[^']|'')*'|=|[^\s=']+")
_LEAVES = {"PL", "PLRepeat", "Link", "LinkRepeat", "Separator", "TreeCopy",
           "Deletion", "TreeLink"}

#: Part of the disk cache's name. Raised when the walk changes, so items
#: read by an older walk are not served again.
_CACHE_FORMAT = "menus2"


@dataclass
class MenuItem:
    """One clickable menu entry and what it runs."""

    path: list
    command: str
    params: str = ""
    shortcut: str = ""
    plid: str = ""
    description: str = ""
    #: Ids of the trees above it and of its PL entry, for translation.
    ids: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"path": "|".join(self.path), "command": self.command,
                "params": self.params, "shortcut": self.shortcut,
                "description": self.description}


def _value(token: str) -> str:
    if token.startswith("'"):
        return token[1:-1].replace("''", "'")
    return token


def parse_rcs(text: str):
    """(PL entries by id, trees by id) from one .rcs file."""
    entries, trees, _edits = _parse(text)
    return entries, trees


def _parse(text: str):
    """(PL entries, trees, edits) from one .rcs file.

    An edit is an Insertion block (items added to the tree TargetID, next
    to RefID0) or a Deletion (DeleteID removed from TargetID). Altium adds
    some menu items only this way: Place > Comment on a schematic is an
    Insertion, and reading Insertion as a one-line leaf dropped it.
    """
    tokens = [t for t in _TOKEN.findall(text) if not t.startswith("//")]
    entries, trees, edits, stack = {}, {}, [], []

    def attrs(i):
        found = {}
        while i + 2 < len(tokens) and tokens[i + 1] == "=":
            found[tokens[i]] = _value(tokens[i + 2])
            i += 3
        return found, i

    i = 0
    while i < len(tokens):
        word = tokens[i]
        if word == "End":
            if stack:
                stack.pop()
            i += 1
        elif word == "Tree" and i + 1 < len(tokens):
            ident = tokens[i + 1]
            found, i = attrs(i + 2)
            node = {"kind": "Tree", "id": ident, "attrs": found,
                    "children": []}
            trees[ident] = node
            if stack:
                stack[-1]["children"].append(node)
            stack.append(node)
        elif word == "Insertion" and i + 1 < len(tokens):
            # A block: its items are children, and its own End closes it.
            ident = tokens[i + 1]
            found, i = attrs(i + 2)
            node = {"kind": word, "id": ident, "attrs": found,
                    "children": []}
            edits.append(node)
            stack.append(node)
        elif word in _LEAVES and i + 1 < len(tokens):
            ident = tokens[i + 1]
            found, i = attrs(i + 2)
            if i < len(tokens) and tokens[i] == "End":
                i += 1
            node = {"kind": word, "id": ident, "attrs": found}
            if word in ("PL", "PLRepeat"):
                entries[ident] = node
            elif word == "Deletion":
                edits.append(node)
            elif stack:
                stack[-1]["children"].append(node)
        else:
            i += 1
    return entries, trees, edits


def _caption(text: str) -> str:
    return re.sub(r"\s*\(&?\w\)", "", text or "").replace("&", "").strip() \
        .rstrip(".").strip()


@lru_cache(maxsize=4)
def _load(install: str):
    entries, trees, edits = {}, {}, {}
    system = Path(install) / "System"
    for path in sorted(system.iterdir()) if system.is_dir() else []:
        if path.suffix.lower() != ".rcs":
            continue
        try:
            found, nested, changes = _parse(path.read_text(encoding="latin-1"))
        except OSError:
            continue
        entries.update(found)
        trees.update(nested)
        for change in changes:
            target = change["attrs"].get("TargetID", "")
            if target:
                edits.setdefault(target, []).append(change)
    return entries, trees, edits


def _children(node, suffix: str, edits: dict) -> list:
    """(child, suffix) pairs of a tree as Altium builds it.

    A TreeCopy gives every id under it the copy's IDSuffix, so the Place
    menu on the schematic menu bar is MNSchematic_Place + "_Menu". Edits
    name their target and reference by those built ids. Inserted items
    keep their own ids.
    """
    out = [(child, suffix) for child in node.get("children", [])]
    for change in edits.get(node["id"] + suffix, []):
        if change["kind"] == "Deletion":
            gone = change["attrs"].get("DeleteID", "")
            out = [(c, s) for c, s in out if c["id"] + s != gone]
            continue
        ref = change["attrs"].get("RefID0", "")
        at = next((n for n, (c, s) in enumerate(out) if c["id"] + s == ref), None)
        added = [(child, "") for child in change.get("children", [])]
        if at is None:
            out += added
        elif change["attrs"].get("InsertType", "").lower() == "before":
            out[at:at] = added
        else:
            out[at + 1:at + 1] = added
    return out


def _walk(node, trees, entries, path, ids, out, depth=0, suffix="", edits=None):
    if depth > 12:
        return
    for child, child_suffix in _children(node, suffix, edits or {}):
        kind = child["kind"]
        if kind == "Tree":
            _walk(child, trees, entries,
                  path + [_caption(child["attrs"].get("Caption", ""))],
                  ids + [f"Tree {child['id']}"], out, depth + 1,
                  child_suffix, edits)
        elif kind == "TreeCopy":
            original = trees.get(child["attrs"].get("OriginalID", ""))
            if original is not None:
                name = (child["attrs"].get("Caption")
                        or original["attrs"].get("Caption", ""))
                _walk(original, trees, entries, path + [_caption(name)],
                      ids + [f"Tree {original['id']}"], out, depth + 1,
                      child["attrs"].get("IDSuffix", "") + child_suffix, edits)
        elif kind == "Link":
            entry = entries.get(child["attrs"].get("PLID", ""))
            if entry is None:
                continue
            a = entry["attrs"]
            name = child["attrs"].get("Caption") or a.get("Caption", "")
            if not _caption(name) or not a.get("Command"):
                continue
            out.append(MenuItem(
                path=path + [_caption(name)], command=a.get("Command", ""),
                params=a.get("Params", ""), shortcut=a.get("Shortcut1", ""),
                plid=entry["id"], description=a.get("Description", ""),
                ids=ids + [f"PL {entry['id']}"]))


@lru_cache(maxsize=16)
def _items(install: str, editor: str) -> tuple:
    """One editor's items, from the disk cache when this install has
    been read before."""
    stored = altium_locale.disk_cache_read(f"{_CACHE_FORMAT}-{editor}",
                                           Path(install))
    if stored is not None:
        return tuple(MenuItem(**row) for row in stored)
    entries, trees, edits = _load(install)
    root = trees.get(EDITOR_MENUS.get(editor, ""))
    if root is None:
        return ()
    out: list = []
    _walk(root, trees, entries, [], [], out, edits=edits)
    altium_locale.disk_cache_write(
        f"{_CACHE_FORMAT}-{editor}", Path(install),
        [{"path": i.path, "command": i.command, "params": i.params,
          "shortcut": i.shortcut, "plid": i.plid,
          "description": i.description, "ids": i.ids} for i in out])
    return tuple(out)


def menu_items(editor: str, install: Optional[Path] = None) -> list:
    """Every item in one editor's menu bar, as the install declares it."""
    install = install or altium_locale.install_dir()
    if install is None:
        return []
    return list(_items(str(install), editor.upper()))


def _flat(text: str) -> str:
    return altium_locale.plain_caption(text)


def resolve(menu_path, editor: str, install: Optional[Path] = None):
    """The item a pipe path names, in English or in any language Altium
    ships, or None. Accelerators and "..." are ignored."""
    levels = ([str(x) for x in menu_path] if isinstance(menu_path, (list, tuple))
              else [p for p in str(menu_path or "").split("|")])
    want = [_flat(level) for level in levels if level.strip()]
    if not want:
        return None
    items = menu_items(editor, install)
    for item in items:
        if [_flat(p) for p in item.path] == want:
            return item
    # A translated path: compare level by level against Altium's own
    # translations of each English caption.
    for item in items:
        if len(item.path) != len(want):
            continue
        if all(w == _flat(p) or w in {
                _flat(t) for t in altium_locale.all_captions_for(p, install)}
               for w, p in zip(want, item.path)):
            return item
    return None


def nearest(menu_path, editor: str, limit: int = 8,
            install: Optional[Path] = None) -> list:
    """The paths closest to one that does not exist, for a misspelling or
    a submenu named wrongly: word search first, then by similarity."""
    import difflib

    text = " ".join(str(menu_path).replace("|", " ").split())
    hits = search(text, editor, limit, install)
    if hits:
        return hits
    items = menu_items(editor, install) if editor else [
        i for name in EDITOR_MENUS for i in menu_items(name, install)]
    paths = {"|".join(i.path).lower(): i for i in items}
    want = "|".join(_flat(p) for p in str(menu_path).split("|"))
    close = difflib.get_close_matches(want, list(paths), n=limit, cutoff=0.6)
    return [{"editor": editor or None, **paths[p].as_dict()} for p in close]


def search(query: str, editor: str = "", limit: int = 20,
           install: Optional[Path] = None) -> list:
    """Menu items whose path, command or description contains every word
    of the query. Words may be in any language Altium ships."""
    words = [w for w in _flat(query).split() if w]
    if not words:
        return []
    editors = [editor.upper()] if editor else list(EDITOR_MENUS)
    hits, seen = [], set()
    for name in editors:
        for item in menu_items(name, install):
            text = " ".join([*item.path, item.command, item.params,
                             item.description]).lower()
            if not all(w in text for w in words):
                local = " ".join(
                    t for p in item.path
                    for t in altium_locale.all_captions_for(p, install))
                if not all(w in local for w in words):
                    continue
            key = (name, tuple(item.path))
            if key in seen:
                continue
            seen.add(key)
            hits.append({"editor": name, **item.as_dict()})
            if len(hits) >= limit:
                return hits
    return hits
