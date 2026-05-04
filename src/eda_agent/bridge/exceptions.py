# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Custom exceptions for Altium Bridge.

Errors carry a code (programmatic dispatch), a message (user display), and
optional details (structured failure context — e.g. which item in a batch
failed). The hierarchy mirrors the canonical error code categories defined
in scripts/altium/Main.pas comments and surfaces them as exception
subclasses so callers can ``except PreconditionError`` instead of
inspecting strings.
"""

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Error code categories — keep aligned with the Pascal side. New codes get
# a category by adding them to the appropriate set below; uncategorised
# codes default to AltiumCommandError.
# ---------------------------------------------------------------------------

# Caller's system / connection: nothing the user can fix in their request,
# they need Altium running and the script loaded.
_CONNECTION_CODES = {
    "ALTIUM_NOT_RUNNING",
    "SCRIPT_NOT_LOADED",
    "ALTIUM_TIMEOUT",
}

# Wire-protocol mismatch — versions disagree.
_PROTOCOL_CODES = {
    "PROTOCOL_VERSION_MISMATCH",
    "MALFORMED_REQUEST",
}

# Caller's request is well-formed but Altium isn't in the right state to
# answer it. Caller fixes by opening a project, focusing a doc, etc.
_PRECONDITION_CODES = {
    "NO_SCHEMATIC",
    "NO_PCB",
    "NO_WORKSPACE",
    "NO_PROJECT",
    "NO_DOCUMENT",
    "NO_LIBRARY",
    "NO_SCHLIB",
    "NO_PCBLIB",
    "NO_COMPONENT",
    "NO_FOOTPRINT",
    "NOT_LOADED",
    "PRECONDITION_FAILED",
}

# Specific named entity is missing.
_NOT_FOUND_CODES = {
    "COMPONENT_NOT_FOUND",
    "PROJECT_NOT_FOUND",
    "DOCUMENT_NOT_FOUND",
    "NOT_FOUND",
    "NO_BATCH_FILE",
}

# Caller's parameters are malformed or invalid.
_INVALID_PARAMETER_CODES = {
    "INVALID_TYPE",
    "INVALID_PARAMETER",
    "MISSING_PARAMS",
    "UNKNOWN_COMMAND",
    "UNKNOWN_ACTION",
}

# Altium attempted the operation and failed (e.g. file save, link, create).
_OPERATION_FAILED_CODES = {
    "CREATE_FAILED",
    "LINK_FAILED",
    "READER_FAILED",
    "SAVE_FAILED",
    "MENU_FAILED",
}

# Pascal-side bug (unhandled exception, internal contract violation).
_INTERNAL_CODES = {
    "INTERNAL_ERROR",
}


class AltiumError(Exception):
    """Base exception for Altium-related errors."""

    def __init__(
        self,
        message: str,
        code: str = "ALTIUM_ERROR",
        details: Optional[dict] = None,
    ):
        self.message = message
        self.code = code
        self.details = details
        super().__init__(message)


class AltiumNotRunningError(AltiumError):
    def __init__(self, message: str = "Altium Designer is not running"):
        super().__init__(message, code="ALTIUM_NOT_RUNNING")


class AltiumTimeoutError(AltiumError):
    def __init__(self, message: str = "Timeout waiting for Altium response"):
        super().__init__(message, code="ALTIUM_TIMEOUT")


class ScriptNotLoadedError(AltiumError):
    def __init__(
        self,
        message: str = "Altium API script is not loaded. Please load Altium_API.PrjScr in Altium Designer.",
    ):
        super().__init__(message, code="SCRIPT_NOT_LOADED")


class AltiumCommandError(AltiumError):
    """Generic command failure — base for code-specific subclasses below.

    Use ``raise_for_code`` to construct the right subclass automatically
    from a Pascal-side error code.
    """

    def __init__(
        self,
        message: str,
        code: str = "COMMAND_FAILED",
        details: Optional[dict] = None,
    ):
        super().__init__(message, code=code, details=details)


class AltiumProtocolError(AltiumCommandError):
    """Wire protocol version mismatch between client and server."""

    def __init__(self, client_version: int = 0, server_version: int = 0,
                 message: Optional[str] = None,
                 code: str = "PROTOCOL_VERSION_MISMATCH",
                 details: Optional[dict] = None):
        if message is None:
            message = (
                f"Protocol version mismatch: client={client_version} "
                f"server={server_version}. Update eda-agent and reload "
                f"Altium_API.PrjScr in Altium so both sides match."
            )
        if details is None:
            details = {
                "client_version": client_version,
                "server_version": server_version,
            }
        super().__init__(message, code=code, details=details)


class PreconditionError(AltiumCommandError):
    """Altium isn't in the right state to answer this request.

    The caller fixes by opening the right project, loading the right doc,
    selecting the right object — not by changing the request itself.
    """


class NotFoundError(AltiumCommandError):
    """A specific named entity (component, project, etc.) was not found."""


class InvalidParameterError(AltiumCommandError):
    """Caller's parameters are malformed or invalid."""

    def __init__(
        self,
        message: str,
        code: str = "INVALID_PARAMETER",
        details: Optional[dict] = None,
        param: Optional[str] = None,
    ):
        if param and "Invalid parameter" not in message:
            message = f"Invalid parameter '{param}': {message}"
        super().__init__(message, code=code, details=details)


class OperationFailedError(AltiumCommandError):
    """Altium attempted the operation and it failed."""


class InternalError(AltiumCommandError):
    """Pascal-side bug — unhandled exception, contract violation, etc."""


# ---------------------------------------------------------------------------
# Legacy aliases (kept so older import paths still work)
# ---------------------------------------------------------------------------

ComponentNotFoundError = NotFoundError
DocumentNotFoundError = NotFoundError
ProjectNotFoundError = NotFoundError


# ---------------------------------------------------------------------------
# Code → exception class dispatch
# ---------------------------------------------------------------------------

def _exception_class_for_code(code: str) -> type:
    """Map a Pascal-side error code to the exception class we should raise."""
    if code in _CONNECTION_CODES:
        if code == "ALTIUM_NOT_RUNNING":
            return AltiumNotRunningError
        if code == "ALTIUM_TIMEOUT":
            return AltiumTimeoutError
        if code == "SCRIPT_NOT_LOADED":
            return ScriptNotLoadedError
    if code in _PROTOCOL_CODES:
        return AltiumProtocolError
    if code in _PRECONDITION_CODES:
        return PreconditionError
    if code in _NOT_FOUND_CODES:
        return NotFoundError
    if code in _INVALID_PARAMETER_CODES:
        return InvalidParameterError
    if code in _OPERATION_FAILED_CODES:
        return OperationFailedError
    if code in _INTERNAL_CODES:
        return InternalError
    return AltiumCommandError


def raise_for_code(code: str, message: str, details: Optional[dict] = None) -> None:
    """Raise the appropriate AltiumCommandError subclass for the given code."""
    cls = _exception_class_for_code(code)
    if cls is AltiumProtocolError:
        if details and "client_version" in details and "server_version" in details:
            raise cls(
                client_version=details.get("client_version", 0),
                server_version=details.get("server_version", 0),
                message=message,
                code=code,
                details=details,
            )
        raise cls(message=message, code=code, details=details)
    if cls in (AltiumNotRunningError, AltiumTimeoutError, ScriptNotLoadedError):
        raise cls(message)
    raise cls(message=message, code=code, details=details)
