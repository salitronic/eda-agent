# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The minimal toolset: two advertised tools, full reach behind them.

Issue #10: some MCP clients cap tool count, others serialize every schema
into the model context at startup and slow badly. This server registers
several hundred tools, so those clients cannot use it.

Merging tools into generic dispatchers was rejected deliberately: the
specialised names, descriptions and schemas are what let a model find the
right operation and follow each tool's documented discipline. "minimal"
therefore hides tools from the ADVERTISED list without collapsing any of
them, leaving tool_catalog to discover and tool_invoke to run.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from eda_agent.tools import DEFAULT_TOOLSET, register_backend
from eda_agent.tools.registry import MINIMAL_TOOLS, ToolRegistry


def _unwrap(result):
    """FastMCP returns content items; get back the tool's own value."""
    content = result[0] if isinstance(result, tuple) else result
    if isinstance(content, list) and content and hasattr(content[0], "text"):
        try:
            return json.loads(content[0].text)
        except (ValueError, TypeError):
            return content[0].text
    return content


@pytest.fixture(scope="module")
def full_server():
    mcp = FastMCP("full")
    register_backend(mcp, "altium", "full")
    return mcp


@pytest.fixture(scope="module")
def minimal_server():
    mcp = FastMCP("minimal")
    register_backend(mcp, "altium", "minimal")
    return mcp


@pytest.mark.anyio
async def test_minimal_advertises_only_the_two_meta_tools(minimal_server):
    names = sorted(t.name for t in await minimal_server.list_tools())
    assert names == sorted(MINIMAL_TOOLS)


@pytest.mark.anyio
async def test_full_still_advertises_the_whole_surface(full_server):
    names = [t.name for t in await full_server.list_tools()]
    # The exact count moves as tools are added; what matters is that
    # "full" is unchanged by the minimal work and stays large.
    assert len(names) > 100
    assert "lib_create_symbol" in names
    for meta in MINIMAL_TOOLS:
        assert meta in names


@pytest.mark.anyio
async def test_catalog_still_sees_the_hidden_tools(minimal_server):
    """Discovery must span everything, not just what is advertised."""
    got = _unwrap(await minimal_server.call_tool(
        "tool_catalog", {"category": "library"}))
    assert got["count"] > 20
    names = {t["name"] for t in got["tools"]}
    assert "lib_create_symbol" in names, "a hidden tool vanished from discovery"


@pytest.mark.anyio
async def test_invoke_runs_a_hidden_tool(minimal_server):
    """The point of the mode: reach a tool the client cannot see.

    Uses a pure calculator so the test needs no running Altium.
    """
    got = _unwrap(await minimal_server.call_tool("tool_invoke", {
        "name": "pcb_calc_trace_width_for_current",
        "arguments": {"current_amps": 2.0, "delta_t_c": 10.0,
                      "layer": "external"},
    }))
    assert got["tool"] == "pcb_calc_trace_width_for_current"
    assert got["result"]["ok"] is True
    assert got["result"]["min_width_mils"] > 0


@pytest.mark.anyio
async def test_invoke_rejects_unknown_and_itself(minimal_server):
    unknown = _unwrap(await minimal_server.call_tool(
        "tool_invoke", {"name": "definitely_not_a_tool"}))
    assert "unknown tool" in unknown["error"]

    recursive = _unwrap(await minimal_server.call_tool(
        "tool_invoke", {"name": "tool_invoke"}))
    assert "itself" in recursive["error"]


@pytest.mark.anyio
async def test_minimal_hides_nothing_it_cannot_reach(full_server,
                                                     minimal_server):
    """Every tool the full server advertises must still be invokable.

    A tool that is hidden AND unreachable would be silently lost, which
    is worse than not offering the mode at all.
    """
    registry = ToolRegistry()
    register_backend(registry, "altium", "full")

    advertised = {t.name for t in await full_server.list_tools()}
    reachable = set(registry.names)
    assert not advertised - reachable, (
        f"unreachable in minimal mode: {sorted(advertised - reachable)}")


def test_unknown_toolset_falls_back_to_full_not_empty():
    """A typo in the env must never leave the server with no tools."""
    registry = ToolRegistry()
    register_backend(registry, "altium", "nonsense-value")
    assert len(registry) > 100


def test_default_toolset_is_full():
    """Existing installs must be unaffected by the new option."""
    assert DEFAULT_TOOLSET == "full"


@pytest.mark.anyio
async def test_catalog_exposes_parameters_so_invoke_need_not_guess(
        minimal_server):
    """Without schemas, tool_invoke is reduced to guessing argument names.

    This tool used to be the sharpest example: it took ``current_amps``
    while the same-named tool on the other backends took ``current_a``,
    so the plausible guess was wrong half the time. Both spellings are
    accepted now, which removes the hazard rather than documenting it.

    That fix has a cost this test is the right place to record. Making
    each spelling optional empties the schema's ``required`` list, so an
    absent current is no longer caught by schema validation and is
    refused at run time instead. The refusal names BOTH spellings, and
    a separate guard holds it to that, so a caller is not left guessing
    either way. What must not happen is the catalog hiding a parameter,
    which would put the guessing back.
    """
    got = _unwrap(await minimal_server.call_tool("tool_catalog", {
        "query": "trace_width_for_current", "with_schema": True}))
    entry = next(t for t in got["tools"]
                 if t["name"] == "pcb_calc_trace_width_for_current")

    for spelling in ("current_amps", "current_a"):
        assert spelling in entry["parameters"], (
            f"{spelling} is accepted but the catalog does not list it, so "
            f"a client reading the schema cannot discover it")
        assert entry["parameters"][spelling]["type"] == "number"
    assert "copper_oz" in entry["parameters"]

    # Either discovered spelling must actually work when invoked, and
    # both must give the same answer; that is what makes them aliases.
    runs = []
    for spelling in ("current_amps", "current_a"):
        run = _unwrap(await minimal_server.call_tool(
            "tool_invoke", {"name": entry["name"],
                            "arguments": {spelling: 2.0}}))
        assert run["result"]["ok"] is True, run
        runs.append(run["result"]["recommended_width_mils"])
    assert runs[0] == runs[1]

    # And omitting the current entirely must still be refused, since
    # the schema no longer does it.
    empty = _unwrap(await minimal_server.call_tool(
        "tool_invoke", {"name": entry["name"], "arguments": {}}))
    assert empty["result"]["ok"] is False


@pytest.mark.anyio
async def test_schema_dump_is_capped(minimal_server):
    """An unfiltered with_schema call must not flood the context.

    Returning every schema would undo the only thing this mode buys.
    """
    got = _unwrap(await minimal_server.call_tool(
        "tool_catalog", {"with_schema": True}))
    assert got["count"] > 100
    assert "schema_omitted" in got
    assert all("parameters" not in t for t in got["tools"])


@pytest.mark.anyio
async def test_schema_shape_is_identical_in_both_toolsets(full_server,
                                                          minimal_server):
    """A captured ToolSpec must describe a tool the same way FastMCP does.

    If these drift, guidance written against one mode misleads in the
    other.
    """
    q = {"query": "trace_width_for_current", "with_schema": True}
    a = _unwrap(await full_server.call_tool("tool_catalog", q))
    b = _unwrap(await minimal_server.call_tool("tool_catalog", q))
    pick = lambda r: next(  # noqa: E731
        t for t in r["tools"]
        if t["name"] == "pcb_calc_trace_width_for_current")
    assert pick(a)["required"] == pick(b)["required"]
    assert set(pick(a)["parameters"]) == set(pick(b)["parameters"])


# --------------------------------------------------------------------
# Discovery quality. Under the minimal toolset, tool_catalog IS the
# interface, so a tool that no category filter reaches is effectively
# invisible to a client that browses by category.
# --------------------------------------------------------------------

@pytest.mark.parametrize("backend", ["altium", "kicad"])
def test_no_tool_falls_into_the_other_bucket(backend):
    """"other" is not a documented category, so anything landing there
    cannot be found by browsing.

    This caught the EDA-agnostic main flow (review_design,
    get_board_info, list_components, list_nets, run_drc, run_erc): those
    carry no name prefix, so prefix-derived categorisation dropped the
    product's own headline tools into "other".
    """
    from eda_agent.tools.metadata import tool_metadata

    registry = ToolRegistry()
    register_backend(registry, backend, "full")
    orphans = [n for n in registry.names
               if tool_metadata(n)["category"] == "other"]
    assert not orphans, (
        f"{backend}: uncategorised tools are undiscoverable by category: "
        f"{orphans}. Add an explicit entry to metadata._CATEGORY_BY_NAME "
        f"or give the tool a recognised prefix.")


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["altium", "kicad"])
async def test_documented_categories_match_reality(backend):
    """Every live category must appear in tool_catalog's own docstring.

    A hardcoded list in prose drifts silently as tools are added, and a
    model filtering by a category the docs omit gets an empty result and
    concludes the tool does not exist.
    """
    mcp = FastMCP(f"cat-{backend}")
    register_backend(mcp, backend, "minimal")
    listed = {t.name: t for t in await mcp.list_tools()}
    doc = (listed["tool_catalog"].description or "")

    got = _unwrap(await mcp.call_tool("tool_catalog", {}))
    undocumented = [c for c in got["categories"] if c not in doc]
    assert not undocumented, (
        f"{backend}: categories present but not documented in "
        f"tool_catalog's docstring: {undocumented}")


@pytest.mark.parametrize("backend", ["altium", "kicad", "both"])
def test_meta_tools_exist_on_every_backend(backend):
    """tool_catalog/tool_invoke describe the tool surface, not an EDA tool.

    They lived inside register_application_tools, which only runs for
    Altium, so the KiCad backend shipped without any way to discover its
    own tools -- and the minimal toolset was impossible there. They are
    backend-agnostic like register_eda_tools and must register everywhere.
    """
    registry = ToolRegistry()
    register_backend(registry, backend, "full")
    missing = [t for t in MINIMAL_TOOLS if t not in registry]
    assert not missing, f"{backend} is missing {missing}"


@pytest.mark.parametrize("backend,minimum", [
    ("altium", 350), ("kicad", 90), ("both", 450),
])
def test_tool_surface_has_not_silently_shrunk(backend, minimum):
    """A registration error drops tools without failing anything.

    A decorator typo or an import that raises inside a registrar leaves
    the server running with a smaller surface and no error anywhere.
    Counts today: altium 391, kicad 101, both 476.
    """
    registry = ToolRegistry()
    register_backend(registry, backend, "full")
    assert len(registry) >= minimum, (
        f"{backend} registered only {len(registry)} tools (expected at "
        f"least {minimum}); a registrar probably failed silently")


def test_easyeda_tools_are_discoverable_under_the_minimal_toolset():
    """Under `minimal`, the catalog is the ONLY way to find a tool.

    So a family the catalog cannot classify is unreachable in practice,
    even though it is registered and `tool_invoke` would run it. That is
    exactly what happened: every easyeda_ tool fell into `other` because
    no prefix was registered, and browsing returned nothing.

    Nothing raises in that state. The backend simply appears empty.

    The fix has since moved: the tools no longer share one "easyeda"
    heading, they file under the same SUBJECT headings Altium uses, so
    a client browsing "pcb" finds them on either backend. This checks
    the property that matters either way, which is that every tool is
    reachable through some category, rather than checking whichever
    heading happens to be current.
    """
    import asyncio

    from eda_agent.tools import register_backend

    class Capture:
        def __init__(self):
            self.tools = {}

        def tool(self, *a, **k):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

    capture = Capture()
    register_backend(capture, backend="easyeda", toolset="minimal")
    assert sorted(capture.tools) == ["tool_catalog", "tool_invoke"]

    everything = asyncio.run(capture.tools["tool_catalog"]())
    categories = everything["categories"]
    assert categories, "the catalog reports no categories at all"

    # Catch-alls do not count as found. Browsing reaches a tool filed
    # under "other" too, so counting every listed category would pass in
    # exactly the broken state this test describes: the tools were in
    # "other" the whole time, and the complaint was that a caller
    # looking for them by subject got nothing.
    catch_all = {"other", "easyeda"}

    reachable: set[str] = set()
    for category in categories:
        if category in catch_all:
            continue
        listing = asyncio.run(
            capture.tools["tool_catalog"](category=category))
        reachable |= {t["name"] for t in listing["tools"]}

    easyeda_tools = {n for n in reachable if n.startswith("easyeda_")}
    assert len(easyeda_tools) > 100, (
        f"only {len(easyeda_tools)} easyeda tools sit under a subject "
        f"category; the rest are in a catch-all, where a caller "
        f"browsing by subject will not find them")

    # And browsing must not have dropped any of them, which is what a
    # tool filed under a category the catalog does not list would do.
    from eda_agent.tools.registry import ToolRegistry

    full = ToolRegistry()
    register_backend(full, backend="easyeda", toolset="full")
    registered = {n for n in full.names if n.startswith("easyeda_")}
    missing = sorted(registered - easyeda_tools)
    assert not missing, (
        f"these easyeda tools are registered but reachable through no "
        f"category, so a minimal client cannot find them: {missing}")


def test_a_narrow_query_returns_the_schema_needed_to_invoke(stub=None):
    """Filter first, then read the schema. That is the documented flow.

    Schemas are dropped past a cap so a broad query cannot flood the
    caller, which means a filter that is too wide silently returns tools
    with no parameters, and an agent then guesses argument names.
    """
    import asyncio

    from eda_agent.tools import register_backend

    class Capture:
        def __init__(self):
            self.tools = {}

        def tool(self, *a, **k):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

    capture = Capture()
    register_backend(capture, backend="easyeda", toolset="minimal")

    catalog = asyncio.run(capture.tools["tool_catalog"](
        query="net_length", with_schema=True))

    # Narrow enough to stay under the schema cap, which is the property
    # being tested. NOT "exactly one result": that held only while one
    # tool happened to match, and adding easyeda_get_net_lengths broke
    # it without changing anything the test cares about.
    assert 0 < catalog["count"] <= 5, (
        f"{catalog['count']} results for a narrow query; either the "
        f"query stopped matching or it is no longer narrow")

    by_name = {t["name"]: t for t in catalog["tools"]}
    entry = by_name.get("easyeda_get_net_length")
    assert entry is not None, (
        f"the query no longer finds the tool it was written for: "
        f"{sorted(by_name)}")
    assert "net" in entry["parameters"], (
        "the schema is missing, so a caller must guess the argument name")


def test_a_tool_reached_through_the_registry_takes_items():
    import asyncio
    registry = ToolRegistry()

    @registry.tool()
    async def demo_close(file_path: str, discard_changes: bool = False) -> dict:
        return {"closed": file_path, "discarded": discard_changes}

    spec = {t.name: t for t in asyncio.run(registry.list_tools())}["demo_close"]
    assert "items" in spec.inputSchema["properties"]
    single = asyncio.run(registry.call_tool("demo_close", {"file_path": "a"}))
    assert single == {"closed": "a", "discarded": False}
    many = asyncio.run(registry.call_tool("demo_close", {
        "items": [{"file_path": "a"}, {"file_path": "b"}], "discard_changes": True}))
    assert many["succeeded"] == 2 and [r["closed"] for r in many["items"]] == ["a", "b"]
    assert all(r["discarded"] for r in many["items"])


def test_a_list_argument_is_advertised_as_an_array_not_a_string():
    import asyncio
    from typing import Optional
    registry = ToolRegistry()

    @registry.tool()
    async def demo_nets(nets: list[str], label: Optional[str] = None) -> dict:
        return {}

    props = {t.name: t for t in asyncio.run(registry.list_tools())}["demo_nets"].inputSchema["properties"]
    assert props["nets"]["type"] == "array" and props["items"]["type"] == "array"
    assert props["label"]["type"] == "string"


def test_tool_invoke_checks_arguments_as_the_advertised_tool_does():
    import asyncio
    import pydantic
    registry = ToolRegistry()

    @registry.tool()
    async def demo_nets(nets: list[str]) -> dict:
        return {"nets": nets}

    assert asyncio.run(registry.call_tool("demo_nets", {"nets": ["A", "B"]})) == {"nets": ["A", "B"]}
    with pytest.raises(pydantic.ValidationError):
        asyncio.run(registry.call_tool("demo_nets", {"nets": "TIDY"}))
