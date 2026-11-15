# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium's own translations of its menus and dialogs, read from its install.

Altium translates its UI into the Windows language when "Use localized
resources" is on: simplified and traditional Chinese, Japanese, German,
French, Korean and Russian. Every tool here that finds a menu or a button
by caption used to know English (and a little Spanish), so on any of those
installs a menu walk found nothing and the dialog driver recognised no
button.

THE TRANSLATIONS ARE ALTIUM'S, NOT GUESSED: they ship with the install,
one folder per language. A hand-written table would cover one language at a time and guess at the
wording: Altium's Chinese "Execute Changes" is 执行变更, not the 执行更改 a
translator would likely write. Reading the install covers every language
Altium has, in the words it actually shows. Windows' own message boxes are localised by Windows, not Altium,
and come from MB_GetString in whatever language Windows runs.

These are Altium's program files, read only; no design file is touched.
"""
from __future__ import annotations

import ctypes
import glob
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

#: Altium's resource folders (Windows LCIDs, as Altium names them) and
#: the language each one is.
LANGUAGES = {
    "7": "de", "12": "fr", "17": "ja", "1028": "zh-TW",
    "1042": "ko", "1049": "ru", "2052": "zh-CN",
}

#: Overrides the install folder, e.g. for a second Altium version.
INSTALL_ENV = "EDA_AGENT_ALTIUM_DIR"

_CAPTION_KEY = "Caption.'".encode("utf-16-le")
#: " (&F)" after a translated caption, where the English had "&File".
_ACCEL_SUFFIX = re.compile(r"\s*\(&?\w\)")

#: MB_GetString's indices, in the order Windows documents them.
_MB_STRINGS = ("ok", "cancel", "abort", "retry", "ignore", "yes", "no",
               "close", "help", "try again", "continue")


def plain_caption(text: str) -> str:
    """A caption as compared: no accelerator, no ellipsis, lower case.

    "文件 (&F)" and "&File..." both come out as their words alone.
    """
    text = _ACCEL_SUFFIX.sub("", str(text or ""))
    return text.replace("&", "").strip().rstrip(".").rstrip(">").strip().lower()


def install_dir() -> Optional[Path]:
    """Altium's install folder: the override, the running X2.EXE, or the
    newest one under Program Files.

    Found once per process: the process scan behind it takes seconds,
    and translation lookups ask for it per caption.
    """
    forced = os.environ.get(INSTALL_ENV, "").strip()
    if forced:
        return Path(forced)
    return _found_install()


@lru_cache(maxsize=1)
def _found_install() -> Optional[Path]:
    exe = _running_altium_exe()
    if exe:
        return Path(exe).parent
    found = sorted(glob.glob(r"C:\Program Files\Altium\*\X2.EXE"),
                   key=os.path.getmtime)
    return Path(found[-1]).parent if found else None


def _running_altium_exe() -> Optional[str]:
    """X2.EXE's path, from the process that owns Altium's main window.

    Through the window rather than a process scan: a scan took over three
    seconds on a busy machine, a window lookup takes milliseconds. The
    bridge's status window, when it is up, names the scripted instance.
    """
    try:
        from . import windows
        if not windows.available():
            return None
        everything = windows.enumerate_windows(visible_only=False)
    except Exception:                            # noqa: BLE001
        return None
    owners = [w.pid for w in everything if w.title == "EDA Agent MCP"]
    owners += [w.pid for w in everything if w.class_name == "TDocumentForm"]
    kernel32 = ctypes.windll.kernel32
    for pid in owners:
        handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED
        if not handle:
            continue
        try:
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)):
                if buffer.value.upper().endswith("X2.EXE"):
                    return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    return None


def _resources(install: Path) -> Path:
    return install / "System" / "Resources"


def installed_languages(install: Optional[Path] = None) -> dict:
    """{LCID folder: language} for the translations this install has."""
    install = install or install_dir()
    if install is None or not _resources(install).is_dir():
        return {}
    return {p.name: LANGUAGES.get(p.name, p.name)
            for p in _resources(install).iterdir()
            if p.is_dir() and p.name.isdigit()}


def _parse_forms(data: bytes) -> dict:
    """English caption to translation, from one forms.fdb."""
    out: dict = {}
    i = 0
    while True:
        i = data.find(_CAPTION_KEY, i)
        if i < 0:
            return out
        n = data[i - 1] if i else 0
        end = i + 2 * n
        source = data[i:end].decode("utf-16-le", "replace")
        if (n > len("Caption.''") and end < len(data)
                and source.startswith("Caption.'") and source.endswith("'")):
            m = data[end]
            text = data[end + 1:end + 1 + 2 * m].decode("utf-16-le", "replace")
            english = source[len("Caption.'"):-1].replace("''", "'")
            if text and text != english:
                out.setdefault(plain_caption(english), set()).add(text)
        i = max(end, i + len(_CAPTION_KEY))


def _rcs_captions(text: str) -> dict:
    """{"PL <id>" / "Tree <id>": caption}. Parsed by token, not by line:
    an entry's Caption can sit on a later line than its id."""
    from .altium_menus import parse_rcs

    entries, trees = parse_rcs(text)
    out = {f"PL {k}": v["attrs"]["Caption"] for k, v in entries.items()
           if v["attrs"].get("Caption")}
    out.update({f"Tree {k}": v["attrs"]["Caption"] for k, v in trees.items()
                if v["attrs"].get("Caption")})
    return out


def _parse_menus(install: Path, lcid: str) -> dict:
    """English menu caption to translation, pairing lines by their id."""
    out: dict = {}
    folder = _resources(install) / lcid
    for path in folder.glob("*.rcs"):
        english = install / "System" / path.name
        if not english.exists():
            continue
        try:
            source = _rcs_captions(english.read_text(encoding="latin-1"))
            local = _rcs_captions(path.read_text(encoding="utf-16"))
        except (OSError, UnicodeError):
            continue
        for key, caption in source.items():
            text = local.get(key)
            if text and text != caption:
                out.setdefault(plain_caption(caption), set()).add(text)
    return out


@lru_cache(maxsize=8)
def _table(install: str, lcid: str) -> dict:
    """Every translation one language has, dialogs and menus together.

    Kept on disk after the first parse, so a later session starts with it.
    """
    root = Path(install)
    stored = disk_cache_read(f"locale-{lcid}", root)
    if stored is not None:
        return {k: set(v) for k, v in stored.items()}
    table: dict = {}
    try:
        forms = _parse_forms((_resources(root) / lcid / "forms.fdb").read_bytes())
    except OSError:
        forms = {}
    for source in (forms, _parse_menus(root, lcid)):
        for english, texts in source.items():
            table.setdefault(english, set()).update(texts)
    disk_cache_write(f"locale-{lcid}", root,
                     {k: sorted(v) for k, v in table.items()})
    return table


def _stamp(install: Path) -> str:
    """Changes whenever Altium's menu or translation files do, as after an
    update: their names, sizes and modification times."""
    parts = [str(install).lower()]
    system = install / "System"
    try:
        files = [p for p in system.iterdir() if p.suffix.lower() == ".rcs"]
        files += list((system / "Resources").glob("*/*"))
    except OSError:
        return ""
    for path in sorted(files):
        try:
            st = path.stat()
        except OSError:
            continue
        parts.append(f"{path.name}:{st.st_size}:{int(st.st_mtime)}")
    import hashlib
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _cache_dir() -> Optional[Path]:
    try:
        from ..config import get_config
        return Path(get_config().workspace_dir) / "cache" / "altium_ui"
    except Exception:                            # noqa: BLE001
        return None


def disk_cache_read(name: str, install: Path):
    """What was stored under name for this exact install, or None."""
    folder = _cache_dir()
    stamp = _stamp(install)
    if folder is None or not stamp:
        return None
    try:
        import json
        return json.loads((folder / f"{name}-{stamp}.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def disk_cache_write(name: str, install: Path, value) -> None:
    """Store value for this install. Best-effort: a cache is never a
    reason to fail."""
    folder = _cache_dir()
    stamp = _stamp(install)
    if folder is None or not stamp:
        return
    try:
        import json

        from ..atomicfile import replace_with_retry
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob(f"{name}-*.json"):
            if old.name != f"{name}-{stamp}.json":
                old.unlink(missing_ok=True)
        tmp = folder / f"{name}-{stamp}.json.tmp"
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        replace_with_retry(tmp, folder / f"{name}-{stamp}.json")
    except Exception:                            # noqa: BLE001
        pass


def translations(english: str, install: Optional[Path] = None) -> dict:
    """{language: {captions}} Altium shows for an English caption."""
    install = install or install_dir()
    if install is None:
        return {}
    key = plain_caption(english)
    out = {}
    for lcid, lang in installed_languages(install).items():
        found = _table(str(install), lcid).get(key)
        if found:
            out[lang] = set(found)
    return out


def all_captions_for(english: str, install: Optional[Path] = None) -> set:
    """The English caption and every translation of it, plain."""
    found = {plain_caption(english)}
    for texts in translations(english, install).values():
        found.update(plain_caption(t) for t in texts)
    return found


def windows_captions() -> dict:
    """Windows' message box buttons in the language Windows runs, by role
    name ("ok", "cancel", "yes", ...). {} off Windows."""
    try:
        get = ctypes.windll.user32.MB_GetString
        get.restype = ctypes.c_wchar_p
        get.argtypes = [ctypes.c_uint]
    except Exception:                            # noqa: BLE001
        return {}
    out = {}
    for index, name in enumerate(_MB_STRINGS):
        try:
            text = get(index)
        except Exception:                        # noqa: BLE001
            text = None
        if text:
            out[name] = plain_caption(text)
    return out


def language_of(captions, install: Optional[Path] = None) -> Optional[str]:
    """Which language a set of captions on screen is in: "en", one of
    LANGUAGES, or None when nothing matches. Judged from the menu bar's
    top-level names, which every language translates."""
    shown = {plain_caption(c) for c in captions if c}
    if not shown:
        return None
    probes = ("&File", "&Edit", "&View", "&Tools", "&Help", "&Window")
    if shown & {plain_caption(p) for p in probes}:
        return "en"
    best, best_hits = None, 0
    for probe_lang in installed_languages(install).values():
        hits = 0
        for probe in probes:
            local = translations(probe, install).get(probe_lang, set())
            hits += bool(shown & {plain_caption(t) for t in local})
        if hits > best_hits:
            best, best_hits = probe_lang, hits
    return best
