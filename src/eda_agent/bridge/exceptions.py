# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Custom exceptions for Altium Bridge."""


class AltiumError(Exception):
    """Base exception for Altium-related errors."""

    def __init__(self, message: str, code: str = "ALTIUM_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class AltiumNotRunningError(AltiumError):
    """Raised when Altium Designer is not running."""

    def __init__(self, message: str = "Altium Designer is not running"):
        super().__init__(message, code="ALTIUM_NOT_RUNNING")


class AltiumTimeoutError(AltiumError):
    """Raised when waiting for Altium response times out."""

    def __init__(self, message: str = "Timeout waiting for Altium response"):
        super().__init__(message, code="ALTIUM_TIMEOUT")


class AltiumCommandError(AltiumError):
    """Raised when an Altium command fails."""

    def __init__(self, message: str, code: str = "COMMAND_FAILED"):
        super().__init__(message, code=code)


class ScriptNotLoadedError(AltiumError):
    """Raised when the Altium script is not loaded."""

    def __init__(
        self, message: str = "Altium API script is not loaded. Please load Altium_API.PrjScr in Altium Designer."
    ):
        super().__init__(message, code="SCRIPT_NOT_LOADED")


class ComponentNotFoundError(AltiumError):
    """Raised when a component is not found."""

    def __init__(self, component: str, library: str = None):
        msg = f"Component '{component}' not found"
        if library:
            msg += f" in library '{library}'"
        super().__init__(msg, code="COMPONENT_NOT_FOUND")


class DocumentNotFoundError(AltiumError):
    """Raised when a document is not found."""

    def __init__(self, document: str):
        super().__init__(f"Document '{document}' not found", code="DOCUMENT_NOT_FOUND")


class ProjectNotFoundError(AltiumError):
    """Raised when a project is not found."""

    def __init__(self, project: str):
        super().__init__(f"Project '{project}' not found", code="PROJECT_NOT_FOUND")


class InvalidParameterError(AltiumError):
    """Raised when an invalid parameter is provided."""

    def __init__(self, param: str, message: str = None):
        msg = f"Invalid parameter '{param}'"
        if message:
            msg += f": {message}"
        super().__init__(msg, code="INVALID_PARAMETER")
