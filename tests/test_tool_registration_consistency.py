# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Cross-touchpoint consistency for the MCP tool-registration surface.

src/eda_agent/tools/__init__.py has THREE parallel lists that must stay
in sync as tool modules are added:

  1. ``from .X import register_X_tools`` (top-of-file imports)
  2. ``register_X_tools(mcp)`` calls inside a registration entrypoint
     (``register_all_tools`` for the Altium suite, or ``register_backend``
     for the KiCad / neutral EDA-agnostic tools)
  3. ``__all__`` re-export list

If any of these drift, the failure mode varies:
  - Import but no call: the symbol is loadable but tools don't appear
    in the MCP catalog (silent feature drop).
  - Call but no import: NameError at server boot.
  - Symbol missing from __all__: external imports of
    ``from eda_agent.tools import register_X_tools`` break.

Plus the on-disk set: any file matching ``register_*_tools`` in
``src/eda_agent/tools/`` should also be wired through __init__.py.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "src" / "eda_agent" / "tools"
INIT_PY = TOOLS_DIR / "__init__.py"


def _imported_registrars() -> set[str]:
    """Registrars __init__.py can call: imported, or defined in it.

    A registrar DEFINED here is as available as an imported one, and it
    is the half this test cares about: what it catches is a call that
    would NameError at boot, and a local definition cannot.
    """
    tree = ast.parse(INIT_PY.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            for alias in node.names:
                if alias.name.startswith("register_") and \
                        alias.name.endswith("_tools"):
                    names.add(alias.name)
        elif isinstance(node, ast.FunctionDef):
            # Not the aggregators: they ARE the entrypoints, so nothing
            # calls them from inside one and counting them here would
            # report every entrypoint as imported-but-never-called.
            if (node.name.startswith("register_")
                    and node.name.endswith("_tools")
                    and node.name not in _AGGREGATORS):
                names.add(node.name)
    return names


# The registration entrypoints. ``register_all_tools`` bundles the Altium
# suite; ``register_backend`` orchestrates per-backend registration (Altium,
# KiCad, the neutral EDA-agnostic tools). A registrar wired through either
# counts as registered.
# Registration entrypoints in __init__.py. register_backend dispatches on
# the toolset and delegates the actual per-backend calls to _register_full,
# so that helper is an entrypoint too: leaving it out would make this guard
# report every backend-specific registrar as "imported but never called".
_AGGREGATORS = {
    "register_all_tools",
    "register_backend",
    "_register_full",
    "_register_surface",
    "_register_minimal",
}


def _called_registrars() -> set[str]:
    """Names called inside any registration entrypoint in __init__.py."""
    tree = ast.parse(INIT_PY.read_text(encoding="utf-8"))
    called = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _AGGREGATORS:
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Call):
                    fn = stmt.func
                    if isinstance(fn, ast.Name) and fn.id.startswith("register_") \
                            and fn.id.endswith("_tools") \
                            and fn.id not in _AGGREGATORS:
                        called.add(fn.id)
    return called


def _all_listed_registrars() -> set[str]:
    """Names listed in __all__."""
    tree = ast.parse(INIT_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, ast.List):
                        return {
                            elt.value for elt in node.value.elts
                            if isinstance(elt, ast.Constant)
                            and isinstance(elt.value, str)
                            and elt.value.startswith("register_")
                            and elt.value.endswith("_tools")
                        }
    return set()


def _on_disk_registrars() -> set[str]:
    """register_X_tools functions defined anywhere in src/eda_agent/tools/
    EXCEPT the __init__.py aggregator (which only re-exports / orchestrates).
    """
    found = set()
    pattern = re.compile(r"^def (register_[a-z_]+_tools)\(", re.MULTILINE)
    for py_file in TOOLS_DIR.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        if ".tmp." in py_file.name:
            continue
        text = py_file.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            found.add(m.group(1))
    return found


def test_imported_set_matches_called_set():
    """Every imported registrar is called inside a registration entrypoint
    (register_all_tools or register_backend), and vice versa. Catches "added
    the import but forgot the call" and "added the call but the symbol doesn't
    actually exist"."""
    imported = _imported_registrars()
    called = _called_registrars()
    imported_not_called = imported - called
    called_not_imported = called - imported
    assert not imported_not_called, (
        f"register_all_tools imports these but never calls them: "
        f"{sorted(imported_not_called)} -- tools registered but silent."
    )
    assert not called_not_imported, (
        f"register_all_tools calls these but doesn't import them: "
        f"{sorted(called_not_imported)} -- NameError at server boot."
    )


def test_all_export_matches_imported_set():
    """__all__ lists every imported registrar (and only those).

    ``register_all_tools`` itself is defined locally in __init__.py and
    is naturally in __all__; exclude it from comparison since the test
    is about external-importable registrars.
    """
    imported = _imported_registrars()
    exported = _all_listed_registrars() - {"register_all_tools"}
    not_in_all = imported - exported
    not_imported = exported - imported
    assert not not_in_all, (
        f"__all__ missing registrar names that __init__.py imports: "
        f"{sorted(not_in_all)} -- external `from eda_agent.tools import X` "
        f"will silently miss these in some packagers."
    )
    assert not not_imported, (
        f"__all__ lists registrar names that __init__.py never imports: "
        f"{sorted(not_imported)} -- dangling export."
    )


def test_on_disk_registrars_are_wired():
    """Every register_*_tools function on disk is plumbed through
    __init__.py. Catches "added a new tool module file but forgot to
    register it"."""
    on_disk = _on_disk_registrars()
    imported = _imported_registrars()
    orphan = on_disk - imported
    assert not orphan, (
        f"register_*_tools functions defined on disk but not wired into "
        f"__init__.py: {sorted(orphan)} -- their tools won't appear in "
        f"the MCP catalog."
    )
