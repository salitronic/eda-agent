# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""server.json, the MCP Registry entry, agrees with the package it lists.

The registry refuses a server.json whose package version is not on PyPI,
and one whose name is not in the PyPI page's README (mcp-name). The
release workflow publishes both from the same tag, so they must agree in
the repository before the tag is made.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from eda_agent.tools import BACKENDS, TOOLSETS

ROOT = Path(__file__).resolve().parents[1]
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
PACKAGE = SERVER["packages"][0]


def test_the_entry_lists_this_package_at_this_version():
    assert PACKAGE["registryType"] == "pypi"
    assert PACKAGE["identifier"] == PROJECT["name"]
    assert SERVER["version"] == PACKAGE["version"] == PROJECT["version"]


def test_the_readme_names_the_entry_for_the_registry_to_find():
    readme = (ROOT / PROJECT["readme"]).read_text(encoding="utf-8")
    assert re.search(rf"mcp-name: {re.escape(SERVER['name'])}(\s|-->)", readme)


def test_the_settings_offered_are_the_ones_the_server_takes():
    env = {v["name"]: v for v in PACKAGE["environmentVariables"]}
    assert set(env["EDA_AGENT_BACKEND"]["choices"]) == set(BACKENDS)
    assert set(env["EDA_AGENT_TOOLSET"]["choices"]) == set(TOOLSETS)


def test_the_description_fits_the_registry():
    assert 0 < len(SERVER["description"]) <= 100


def test_the_readme_on_pypi_has_no_links_into_the_repository():
    """PyPI shows the README without the repository around it, so a
    relative link or picture there leads nowhere."""
    readme = (ROOT / PROJECT["readme"]).read_text(encoding="utf-8")
    targets = re.findall(r"\]\(([^)\s]+)\)|src=\"([^\"]+)\"", readme)
    relative = [a or b for a, b in targets
                if not re.match(r"(https?:|mailto:|#)", a or b)]
    assert relative == []
