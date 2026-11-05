# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium 365 tools: workspaces, projects and the managed library, read
through the workspace's own GraphQL API or through Nexar (eda_agent/altium365.py
says how to set up either), and three changes to the library that are only
sent with confirm=True.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Optional


def _session():
    from ..altium365 import Altium365Session
    return Altium365Session()


async def _answer(work: Callable[[], dict]) -> dict[str, Any]:
    """Run blocking work off the event loop; failures come back as reasons."""
    from ..altium365 import Altium365Error
    try:
        result = await asyncio.to_thread(work)
    except Altium365Error as exc:
        return {"ok": False, "reason": str(exc)}
    except OSError as exc:
        return {"ok": False, "reason": f"Altium 365 could not be reached: {exc}"}
    return {"ok": True, **result}


def _preview(operation: str, what: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "sent": False, "would_send": operation, **extra,
            "note": f"nothing was {what}; call again with confirm=True to send it"}


def register_altium365_tools(mcp) -> None:

    @mcp.tool()
    async def altium365_check_setup() -> dict[str, Any]:
        """Which Altium 365 route is set up, whether the live schema has the
        fields the read tools use, and which changes it offers.

        Routes: the workspace's own API (EDA_AGENT_ALTIUM365_DOMAIN, and a
        token made under Admin > Developer in EDA_AGENT_ALTIUM365_TOKEN), or
        Nexar (EDA_AGENT_NEXAR_APP_ID, then ``eda-agent altium365-login``).
        """
        return await _answer(lambda: _session().check_setup())

    @mcp.tool()
    async def altium365_list_workspaces() -> dict[str, Any]:
        """The Altium 365 workspaces the token sees (url, id, name,
        description); with EDA_AGENT_ALTIUM365_WORKSPACES set, only those."""
        return await _answer(lambda: {"workspaces": _session().list_workspaces()})

    @mcp.tool()
    async def altium365_list_projects(workspace_url: str, name_contains: str = "",
                                      limit: int = 200) -> dict[str, Any]:
        """Projects of one workspace (id, name, description), optionally those
        whose name contains a text."""
        return await _answer(
            lambda: _session().list_projects(workspace_url, name_contains, limit))

    @mcp.tool()
    async def altium365_get_project(project_id: str) -> dict[str, Any]:
        """One Altium 365 project by id."""
        return await _answer(lambda: {"project": _session().get_project(project_id)})

    @mcp.tool()
    async def altium365_list_library_folders(workspace_url: str) -> dict[str, Any]:
        """Folders of a workspace's managed library (id, name, path); publishing
        names its folder by id."""
        return await _answer(
            lambda: {"folders": _session().list_library_folders(workspace_url)})

    @mcp.tool()
    async def altium365_list_components(workspace_url: str, name_contains: str = "",
                                        with_parameters: bool = False) -> dict[str, Any]:
        """Managed components of a workspace (id, name), and their parameters
        when asked, the whole library in one call."""
        return await _answer(lambda: {"components": _session().list_components(
            workspace_url, name_contains, with_parameters)})

    @mcp.tool()
    async def altium365_get_component(component_id: str) -> dict[str, Any]:
        """One managed component and its parameters."""
        return await _answer(lambda: {"component": _session().get_component(component_id)})

    @mcp.tool()
    async def altium365_read_object(query_field: str, object_id: str) -> dict[str, Any]:
        """Any object a ``des...ById`` query returns (a release, a part choice,
        a comment thread), asking for the fields the live schema lists, never
        guessed ones. altium365_describe_type("Query") lists the queries.

        Returns:
            ``type``, ``fields``, and ``nested_fields`` to read with
            altium365_run_query.
        """
        return await _answer(lambda: _session().read_object(query_field, object_id))

    @mcp.tool()
    async def altium365_describe_type(type_name: str = "Query") -> dict[str, Any]:
        """One type of the live Altium 365 schema with its fields, their
        arguments and types: start at "Query" and follow the types named."""
        return await _answer(lambda: {"type": _session().describe_type(type_name)})

    @mcp.tool()
    async def altium365_run_query(query: str,
                                  variables: Optional[dict] = None) -> dict[str, Any]:
        """Run a reading GraphQL query against Altium 365. Queries that change
        anything (mutation, subscription) are refused; library changes are
        altium365_publish_library_file, altium365_release_component and
        altium365_set_component_parameters."""
        return await _answer(lambda: {"data": _session().read(query, variables)})

    @mcp.tool()
    async def altium365_publish_library_file(kind: str, workspace_url: str, folder_id: str,
                                             name: str, file_path: str,
                                             confirm: bool = False) -> dict[str, Any]:
        """Publish a symbol (.SchLib) or footprint (.PcbLib) into a folder of a
        workspace's managed library.

        Without ``confirm`` this shows what would be sent. With it the file
        is uploaded and the item created, and its ``id`` comes back.
        """
        from ..altium365 import Altium365Error, publish_operation, reported_errors
        if kind not in ("symbol", "footprint"):
            return {"ok": False, "reason": "kind is symbol or footprint"}
        source = Path(file_path)
        if not source.is_file() or source.suffix.lower() not in (".schlib", ".pcblib"):
            return {"ok": False, "reason": "file_path names an existing .SchLib or .PcbLib"}
        field_name, operation = publish_operation(kind)
        values = {"fileId": "<the uploaded file>", "relativePath": f"Released/{source.name}",
                  "folderId": folder_id, "ws": workspace_url, "name": name}
        if not confirm:
            return _preview(operation, "published", variables=values)

        def publish() -> dict:
            session = _session()
            session.require_change(f"publish_{kind}")
            values["ws"] = session.permitted(workspace_url)
            values["fileId"] = session.upload_file(source)
            data = session.run_change(operation, values)
            made = (data.get(field_name) or {}).get("id")
            problems = reported_errors(data, field_name)
            if problems or not made:
                raise Altium365Error(f"{field_name} created nothing: "
                                     + "; ".join(problems or ["no id came back"]))
            return {"sent": True, "id": made, "file_id": values["fileId"]}
        return await _answer(publish)

    @mcp.tool()
    async def altium365_release_component(release: dict,
                                          confirm: bool = False) -> dict[str, Any]:
        """Release a managed component (desReleaseComponent). ``release`` is
        that operation's input as Altium documents it, with at least
        ``workspaceUrl`` and ``componentItemName``. Without ``confirm`` this
        shows what would be sent."""
        from ..altium365 import Altium365Error, release_operation, reported_errors
        if not isinstance(release, dict) or not all(
                release.get(k) for k in ("workspaceUrl", "componentItemName")):
            return {"ok": False, "reason": "release needs workspaceUrl and componentItemName"}
        try:
            operation = release_operation(release)
        except Altium365Error as exc:
            return {"ok": False, "reason": str(exc)}
        if not confirm:
            return _preview(operation, "released")

        def send() -> dict:
            session = _session()
            session.require_change("release_component")
            session.permitted(release["workspaceUrl"])
            problems = reported_errors(session.run_change(operation), "desReleaseComponent")
            if problems:
                raise Altium365Error("desReleaseComponent: " + "; ".join(problems))
            return {"sent": True, "released": release["componentItemName"]}
        return await _answer(send)

    @mcp.tool()
    async def altium365_set_component_parameters(
            component_id: str, parameters: list[dict], release_note: str,
            replace_existing: bool = False, confirm: bool = False) -> dict[str, Any]:
        """Add or change a managed component's parameters as a new revision.

        ``parameters`` is [{"name", "value"}]; ``replace_existing`` drops
        every parameter the component has first. The new revision has a new
        component id, which comes back. Without ``confirm`` this shows what
        would be sent.
        """
        from ..altium365 import Altium365Error, parameters_operation
        if not parameters or any(not isinstance(p, dict) or not p.get("name")
                                 for p in parameters):
            return {"ok": False, "reason": "parameters is a list of {name, value}"}
        if not release_note.strip():
            return {"ok": False, "reason": "release_note says why the revision is made"}
        try:
            operation = parameters_operation(component_id, parameters, release_note,
                                             replace_existing)
        except Altium365Error as exc:
            return {"ok": False, "reason": str(exc)}
        if not confirm:
            return _preview(operation, "changed")

        def send() -> dict:
            session = _session()
            session.require_change("set_parameters")
            data = session.run_change(operation)
            revised = (data.get("desUpdateComponentRevisionParameters") or {}).get(
                "componentId")
            if not revised:
                raise Altium365Error("the change returned no new component id")
            return {"sent": True, "new_component_id": revised}
        return await _answer(send)
