# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium 365 over GraphQL, through the workspace itself or through Nexar.

Workspace: a token created in the workspace under Admin > Developer, sent
to the workspace's own GraphQL endpoint (EDA_AGENT_ALTIUM365_DOMAIN and
EDA_AGENT_ALTIUM365_TOKEN). It reaches more than Nexar does. These tokens
are not refreshed; an expired one is reported for the user to replace.

Nexar: api.nexar.com with an app the user registered with Nexar
(EDA_AGENT_NEXAR_APP_ID). ``eda-agent altium365-login`` signs in once in
the browser (authorization code with PKCE); the token is kept encrypted
for the current Windows user and refreshed when it expires.

A domain, when set, selects the workspace route. Nothing Altium 365 sends
back is written to disk: replies stay in memory for ten minutes, well
inside Nexar's 24-hour limit on keeping data. EDA_AGENT_ALTIUM365_WORKSPACES
(comma-separated URLs) limits which workspaces the tools may touch.

Any reading query may be run; one containing ``mutation`` or
``subscription`` is refused. Changes go only through the fixed operations
built below, each sent only after the live schema shows it exists.

Not yet run against a live workspace; that needs an account's token.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

NEXAR_GRAPHQL = "https://api.nexar.com/graphql"
NEXAR_UPLOAD = "https://files.nexar.com/File/Upload"
NEXAR_AUTH = "https://identity.nexar.com/connect"
NEXAR_SCOPES = " ".join(["openid", "profile", "email", "offline_access",
                         "user.access", "design.domain"])
MEMORY_SECONDS = 600
_DEFAULT_REDIRECT = "http://localhost:3000/login"


class Altium365Error(Exception):
    """A failure or refusal, worded for the user to act on."""


def _workspace_key(url: str) -> str:
    return url.strip().rstrip("/") + "/"


@dataclass(frozen=True)
class Altium365Config:
    domain: str = ""
    workspace_token: str = ""
    nexar_app_id: str = ""
    redirect_uri: str = _DEFAULT_REDIRECT
    allowed_workspaces: tuple[str, ...] = ()
    saved_login: Path = field(
        default_factory=lambda: Path.home() / ".eda-agent" / "nexar_login.bin")
    timeout_s: float = 60.0

    @property
    def mode(self) -> str:
        """"workspace", "nexar", or "" when neither is set up."""
        if self.domain:
            return "workspace"
        return "nexar" if self.nexar_app_id else ""

    @property
    def graphql_url(self) -> str:
        return f"https://{self.domain}/api/graphql/" if self.domain else NEXAR_GRAPHQL

    @property
    def upload_url(self) -> str:
        return f"https://{self.domain}/api/files" if self.domain else NEXAR_UPLOAD


def read_environment(env: Optional[dict] = None) -> Altium365Config:
    env = os.environ if env is None else env
    domain = re.sub(r"^https?://", "", env.get("EDA_AGENT_ALTIUM365_DOMAIN", "").strip())
    allowed = [_workspace_key(u) for u in
               env.get("EDA_AGENT_ALTIUM365_WORKSPACES", "").split(",") if u.strip()]
    return Altium365Config(
        domain=domain.strip("/"),
        workspace_token=env.get("EDA_AGENT_ALTIUM365_TOKEN", "").strip(),
        nexar_app_id=env.get("EDA_AGENT_NEXAR_APP_ID", "").strip(),
        redirect_uri=env.get("EDA_AGENT_NEXAR_REDIRECT", _DEFAULT_REDIRECT),
        allowed_workspaces=tuple(allowed))


# --- what is sent -----------------------------------------------------------

_READS = {
    "workspaces": "query { desWorkspaceInfos { url workspaceId name description } }",
    "projects": ("query($ws: String!, $after: String) { desProjects(workspaceUrl: $ws, "
                 "after: $after) { totalCount pageInfo { hasNextPage endCursor } "
                 "nodes { id name description } } }"),
    "project": "query($id: ID!) { desProjectById(id: $id) { id name description } }",
    "folders": ("query($ws: String!) { desLibrary(workspaceUrl: $ws) "
                "{ folders { id name path } } }"),
    "components": ("query($ws: String!) { desLibrary(workspaceUrl: $ws) "
                   "{ components { nodes { id name%s } } } }"),
    "component": ("query($id: ID!) { desComponentById(id: $id) { id name details "
                  "{ parameters { type name value } } } }"),
    "type": ("query($n: String!) { __type(name: $n) { name kind description fields "
             "{ name description args { name type { name kind ofType { name kind } } } "
             "type { name kind ofType { name kind ofType { name kind } } } } } }"),
}
_PARAMETERS = " details { parameters { type name value } }"

#: Schema fields the reads depend on; check_setup() looks for each one.
READ_FIELDS = (("Query", "desWorkspaceInfos"), ("Query", "desProjects"),
               ("Query", "desProjectById"), ("Query", "desLibrary"),
               ("Query", "desComponentById"))
#: Each change and the schema field it needs before it is sent.
CHANGES = {"publish_symbol": ("Mutation", "desCreateSymbol"),
           "publish_footprint": ("Mutation", "desCreateFootprint"),
           "release_component": ("Mutation", "desReleaseComponent"),
           "set_parameters": ("Mutation", "desUpdateComponentRevisionParameters")}


def refuse_changes(query: str) -> None:
    """Raise when a query could change anything, or is too long to check."""
    if len(query) > 20000:
        raise Altium365Error("the query is longer than 20000 characters")
    code = re.sub(r"#[^\n]*", "", re.sub(r'"(?:\\.|[^"\\])*"', '""', query))
    if re.search(r"\b(?:mutation|subscription)\b", code, re.IGNORECASE):
        raise Altium365Error("only reading queries run here; the altium365 change "
                             "tools make changes")


def gql_value(value: Any) -> str:
    """A Python value written as a GraphQL input value."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (str, int, float)):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(map(gql_value, value)) + "]"
    if isinstance(value, dict):
        bad = [k for k in value if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)]
        if bad:
            raise Altium365Error(f"{bad[0]!r} is not a GraphQL field name")
        return "{ " + ", ".join(f"{k}: {gql_value(v)}" for k, v in value.items()) + " }"
    raise Altium365Error(f"a {type(value).__name__} cannot be sent")


def publish_operation(kind: str) -> tuple[str, str]:
    """(schema field, operation) creating a symbol or footprint from a file."""
    name = {"symbol": "desCreateSymbol", "footprint": "desCreateFootprint"}[kind]
    return name, (
        "mutation($fileId: String!, $relativePath: String!, $folderId: ID!, "
        "$ws: String!, $name: String!) { " + name + "(input: { files: { fileId: "
        "$fileId, relativePath: $relativePath }, folderId: $folderId, name: $name, "
        "workspaceUrl: $ws }) { errors { message } id } }")


def release_operation(spec: dict) -> str:
    return ("mutation { desReleaseComponent(input: " + gql_value(spec)
            + ") { errors { message } } }")


def parameters_operation(component_id: str, parameters: list[dict], note: str,
                         replace: bool) -> str:
    spec = {"componentId": component_id, "releaseNote": note, "replaceExisting": replace,
            "parameters": [{"name": str(p["name"]), "value": str(p["value"])}
                           for p in parameters]}
    return ("mutation { desUpdateComponentRevisionParameters(input: " + gql_value(spec)
            + ") { componentId } }")


def reported_errors(data: dict, name: str) -> list[str]:
    return [e.get("message", "") for e in ((data or {}).get(name) or {}).get("errors") or []]


# --- the Nexar login ----------------------------------------------------------

def _windows_crypt(blob: bytes, encrypt: bool) -> bytes:  # pragma: no cover - Windows only
    """DPAPI, scoped to the current Windows user."""
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    source = ctypes.create_string_buffer(blob, len(blob))
    given = DataBlob(len(blob), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
    result = DataBlob()
    api = ctypes.windll.crypt32
    no_ui = 0x01
    if encrypt:
        done = api.CryptProtectData(ctypes.byref(given), "eda-agent", None, None, None,
                                    no_ui, ctypes.byref(result))
    else:
        done = api.CryptUnprotectData(ctypes.byref(given), None, None, None, None,
                                      no_ui, ctypes.byref(result))
    if not done:
        raise Altium365Error("Windows could not protect or read the saved Nexar login")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def store_login(path: Path, token: dict) -> None:
    if sys.platform != "win32":
        raise Altium365Error("the Nexar login is only kept on Windows, encrypted per user")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_windows_crypt(json.dumps(token).encode(), True))


def fetch_login(path: Path) -> Optional[dict]:
    if sys.platform != "win32" or not path.is_file():
        return None
    return json.loads(_windows_crypt(path.read_bytes(), False))


def forget_login(path: Path) -> bool:
    """Remove the saved Nexar login; False when there was none."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _ask_for_token(form: dict, timeout_s: float) -> dict:
    request = urllib.request.Request(
        f"{NEXAR_AUTH}/token", data=urllib.parse.urlencode(form).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as reply:
            return json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        raise Altium365Error(f"Nexar turned the sign-in down (HTTP {exc.code})") from exc


def browser_login(config: Altium365Config, open_url: Callable[[str], Any],
                  wait_s: float = 300) -> dict:  # pragma: no cover - needs an account
    """Sign in to Nexar in the browser and keep the login, encrypted."""
    import http.server

    if not config.nexar_app_id:
        raise Altium365Error("set EDA_AGENT_NEXAR_APP_ID to the client id of your Nexar app")
    back = urllib.parse.urlparse(config.redirect_uri)
    if back.hostname not in ("localhost", "127.0.0.1"):
        raise Altium365Error("the redirect URI has to point at this machine (localhost)")
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_hex(16)
    arrived = threading.Event()
    codes: list[str] = []

    class Catcher(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            asked = urllib.parse.urlparse(self.path)
            fields = dict(urllib.parse.parse_qsl(asked.query))
            if asked.path == back.path and fields.get("state") == nonce and fields.get("code"):
                codes.append(fields["code"])
                arrived.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in for eda-agent. You can close this tab.")

    listener = http.server.HTTPServer(("127.0.0.1", back.port or 80), Catcher)
    threading.Thread(target=listener.serve_forever, daemon=True).start()
    try:
        open_url(f"{NEXAR_AUTH}/authorize?" + urllib.parse.urlencode({
            "client_id": config.nexar_app_id, "response_type": "code",
            "redirect_uri": config.redirect_uri, "scope": NEXAR_SCOPES, "state": nonce,
            "code_challenge": code_challenge(verifier), "code_challenge_method": "S256"}))
        arrived.wait(wait_s)
    finally:
        listener.shutdown()
    if not codes:
        raise Altium365Error("the browser never came back with a sign-in")
    token = _ask_for_token({"grant_type": "authorization_code", "code": codes[0],
                            "redirect_uri": config.redirect_uri,
                            "client_id": config.nexar_app_id,
                            "code_verifier": verifier}, config.timeout_s)
    token["saved_at"] = time.time()
    store_login(config.saved_login, token)
    return {"signed_in": True, "expires_in_s": token.get("expires_in")}


# --- the session --------------------------------------------------------------

class Altium365Session:
    """Reads and changes against one configured route, replies kept briefly."""

    _BACKOFF_S = (1.0, 2.0, 4.0)

    def __init__(self, config: Optional[Altium365Config] = None,
                 urlopen: Callable = urllib.request.urlopen,
                 now: Callable[[], float] = time.time, pause: Callable = time.sleep):
        self.config = config or read_environment()
        self._urlopen, self._now, self._pause = urlopen, now, pause
        self._recent: dict[tuple[str, str], tuple[float, dict]] = {}
        self._guard = threading.Lock()

    # credentials and transport

    def _access_token(self) -> str:
        cfg = self.config
        if cfg.mode == "workspace":
            if cfg.workspace_token:
                return cfg.workspace_token
            raise Altium365Error(f"no token for {cfg.domain}: create one in the workspace "
                                 "(Admin > Developer) and set EDA_AGENT_ALTIUM365_TOKEN")
        if cfg.mode != "nexar":
            raise Altium365Error("Altium 365 is not set up: set EDA_AGENT_ALTIUM365_DOMAIN "
                                 "and EDA_AGENT_ALTIUM365_TOKEN, or EDA_AGENT_NEXAR_APP_ID "
                                 "and run eda-agent altium365-login")
        login = fetch_login(cfg.saved_login)
        if not login:
            raise Altium365Error("no Nexar login: run eda-agent altium365-login")
        expires = login.get("saved_at", 0) + int(login.get("expires_in", 0))
        if self._now() < expires - 120:
            return login["access_token"]
        if not login.get("refresh_token"):
            raise Altium365Error("the Nexar login expired: run eda-agent altium365-login")
        renewed = _ask_for_token({"grant_type": "refresh_token",
                                  "refresh_token": login["refresh_token"],
                                  "client_id": cfg.nexar_app_id}, cfg.timeout_s)
        renewed.setdefault("refresh_token", login["refresh_token"])
        renewed["saved_at"] = self._now()
        store_login(cfg.saved_login, renewed)
        return renewed["access_token"]

    def _send(self, operation: str, variables: Optional[dict]) -> dict:
        payload = json.dumps({"query": operation, "variables": variables or {}}).encode()
        waits = list(self._BACKOFF_S)
        while True:
            request = urllib.request.Request(
                self.config.graphql_url, data=payload, method="POST",
                headers={"Authorization": "Bearer " + self._access_token(),
                         "Content-Type": "application/json"})
            try:
                with self._urlopen(request, timeout=self.config.timeout_s) as reply:
                    answer = json.loads(reply.read())
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 502, 503, 504) and waits:
                    self._pause(waits.pop(0))
                    continue
                if exc.code in (401, 403):
                    raise Altium365Error(f"Altium 365 did not accept the token "
                                         f"(HTTP {exc.code})") from exc
                text = exc.read()[:300].decode(errors="replace")
                raise Altium365Error(f"Altium 365 answered HTTP {exc.code}: {text}") from exc
        problems = answer.get("errors") or []
        if problems:
            raise Altium365Error("GraphQL errors: " + "; ".join(
                str(p.get("message", p)) for p in problems[:5]))
        return answer.get("data") or {}

    def read(self, query: str, variables: Optional[dict] = None) -> dict:
        """Run a reading query; the same query within ten minutes is answered
        from memory."""
        refuse_changes(query)
        key = (query, json.dumps(variables or {}, sort_keys=True))
        moment = self._now()
        with self._guard:
            kept = self._recent.get(key)
        if kept and moment - kept[0] < MEMORY_SECONDS:
            return kept[1]
        data = self._send(query, variables)
        with self._guard:
            for old in [k for k, (t, _) in self._recent.items() if moment - t >= MEMORY_SECONDS]:
                del self._recent[old]
            self._recent[key] = (moment, data)
        return data

    def permitted(self, workspace_url: str) -> str:
        """The workspace URL, normalised, unless the allow-list leaves it out."""
        url = _workspace_key(workspace_url)
        if self.config.allowed_workspaces and url not in self.config.allowed_workspaces:
            raise Altium365Error(f"{url} is not listed in EDA_AGENT_ALTIUM365_WORKSPACES")
        return url

    # reads

    def list_workspaces(self) -> list[dict]:
        found = self.read(_READS["workspaces"]).get("desWorkspaceInfos") or []
        allowed = self.config.allowed_workspaces
        return [w for w in found if not allowed
                or _workspace_key(w.get("url") or "") in allowed]

    def list_projects(self, workspace_url: str, name_contains: str = "",
                      limit: int = 200) -> dict:
        ws = self.permitted(workspace_url)
        needle = name_contains.lower()
        kept: list[dict] = []
        cursor, total = None, 0
        for _page in range(100):
            page = self.read(_READS["projects"], {"ws": ws, "after": cursor})["desProjects"]
            total = page.get("totalCount", 0)
            kept.extend(p for p in page.get("nodes") or []
                        if needle in (p.get("name") or "").lower())
            more = page["pageInfo"]
            if len(kept) >= limit or not more["hasNextPage"]:
                break
            cursor = more["endCursor"]
        return {"total_in_workspace": total, "projects": kept[:limit],
                "cut_short": len(kept) > limit}

    def get_project(self, project_id: str) -> dict:
        project = self.read(_READS["project"], {"id": project_id}).get("desProjectById")
        if project is None:
            raise Altium365Error(f"project {project_id} was not found or is not shared "
                                 "with this token")
        return project

    def list_library_folders(self, workspace_url: str) -> list[dict]:
        library = self.read(_READS["folders"], {"ws": self.permitted(workspace_url)})
        return (library.get("desLibrary") or {}).get("folders") or []

    def list_components(self, workspace_url: str, name_contains: str = "",
                        with_parameters: bool = False) -> list[dict]:
        query = _READS["components"] % (_PARAMETERS if with_parameters else "")
        library = self.read(query, {"ws": self.permitted(workspace_url)}).get("desLibrary")
        nodes = ((library or {}).get("components") or {}).get("nodes") or []
        needle = name_contains.lower()
        return [n for n in nodes if needle in (n.get("name") or "").lower()]

    def get_component(self, component_id: str) -> dict:
        part = self.read(_READS["component"], {"id": component_id}).get("desComponentById")
        if part is None:
            raise Altium365Error(f"component {component_id} was not found")
        return part

    def describe_type(self, type_name: str) -> dict:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,80}", type_name or ""):
            raise Altium365Error("a type name holds letters, digits and _ only")
        described = self.read(_READS["type"], {"n": type_name}).get("__type")
        if described is None:
            raise Altium365Error(f"the schema has no type {type_name}")
        return described

    @staticmethod
    def _base_type(type_ref: dict) -> dict:
        while type_ref.get("ofType") and type_ref.get("kind") in ("NON_NULL", "LIST"):
            type_ref = type_ref["ofType"]
        return type_ref

    def read_object(self, query_field: str, object_id: str, most_fields: int = 60) -> dict:
        """Any ``des...ById`` object, asking for the plain fields its schema
        type lists."""
        if not re.fullmatch(r"des[A-Za-z]+ById", query_field or ""):
            raise Altium365Error("query_field is a des...ById field, e.g. desProjectById")
        roots = {f["name"]: f for f in self.describe_type("Query").get("fields") or []}
        if query_field not in roots:
            raise Altium365Error(f"the schema has no Query.{query_field}")
        type_ref = roots[query_field]["type"]
        while type_ref.get("ofType"):
            type_ref = type_ref["ofType"]
        members = self.describe_type(type_ref["name"]).get("fields") or []
        plain = [f["name"] for f in members if not f.get("args")
                 and self._base_type(f["type"]).get("kind") in ("SCALAR", "ENUM")]
        nested = [f["name"] for f in members
                  if self._base_type(f["type"]).get("kind") not in ("SCALAR", "ENUM")]
        found = self.read(
            "query($id: ID!) { %s(id: $id) { %s } }" % (query_field,
                                                        " ".join(plain[:most_fields])),
            {"id": object_id}).get(query_field)
        if found is None:
            raise Altium365Error(f"{query_field}({object_id}) returned nothing")
        return {"type": type_ref["name"], "fields": found, "nested_fields": nested[:80]}

    def check_setup(self) -> dict:
        self._access_token()       # raises with what is missing
        listed: dict[str, set[str]] = {}
        for type_name in {t for t, _ in READ_FIELDS} | {t for t, _ in CHANGES.values()}:
            try:
                listed[type_name] = {f["name"] for f in
                                     self.describe_type(type_name).get("fields") or []}
            except Altium365Error:
                listed[type_name] = set()
        absent = [f"{t}.{f}" for t, f in READ_FIELDS if f not in listed[t]]
        return {"ok": not absent, "route": self.config.mode,
                "endpoint": self.config.graphql_url, "missing_fields": absent,
                "changes_offered": {name: f in listed[t] for name, (t, f) in CHANGES.items()}}

    # changes

    def require_change(self, name: str) -> None:
        type_name, field_name = CHANGES[name]
        offered = {f["name"] for f in self.describe_type(type_name).get("fields") or []}
        if field_name not in offered:
            raise Altium365Error(f"the schema has no {type_name}.{field_name}, so "
                                 f"{name} is not sent")

    def run_change(self, operation: str, variables: Optional[dict] = None) -> dict:
        """One of the fixed operations above, never answered from memory."""
        return self._send(operation, variables)

    def upload_file(self, path: Path) -> str:
        """Upload a library file for a publish; the id it returns lasts a day."""
        path = Path(path)
        mark = "eda-agent-" + secrets.token_hex(12)
        head = (f"--{mark}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n")
        body = head.encode() + path.read_bytes() + f"\r\n--{mark}--\r\n".encode()
        token = self._access_token()
        # The workspace's file service takes a bearer token like its GraphQL
        # endpoint; Nexar's takes the token in a header named "token".
        auth = ({"Authorization": f"Bearer {token}"} if self.config.mode == "workspace"
                else {"token": token})
        request = urllib.request.Request(
            self.config.upload_url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={mark}", **auth})
        with self._urlopen(request, timeout=self.config.timeout_s) as reply:
            file_id = reply.read().decode(errors="replace").strip().strip('"')
        if not file_id:
            raise Altium365Error("the upload returned no file id")
        return file_id
