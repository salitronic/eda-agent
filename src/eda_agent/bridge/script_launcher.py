# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Script launcher helpers for EDA Agent.

Utility for locating the bundled Altium_API.PrjScr and, when Altium is
running, asking it to open the project via COM. The main IPC path
(request.json / response.json) does not use COM — this helper is only
for the convenience of opening the script project from Python.
"""

import logging
from pathlib import Path
from typing import Optional

from .process_manager import AltiumProcessManager

logger = logging.getLogger(__name__)


class ScriptLauncher:
    """Utility for finding Altium and the script project path."""

    def __init__(self):
        self.process_manager = AltiumProcessManager()

    def get_script_path(self) -> Path:
        """Get the path to the Altium_API.PrjScr script."""
        package_dir = Path(__file__).parent.parent.parent.parent
        return package_dir / "scripts" / "altium" / "Altium_API.PrjScr"

    def is_altium_running(self) -> bool:
        """Check if Altium is running."""
        return self.process_manager.is_altium_running()

    def open_script_project(self) -> bool:
        """Open the script project in a running Altium instance via COM.

        Returns True if successful.
        """
        if not self.is_altium_running():
            return False

        try:
            import win32com.client

            altium = win32com.client.GetActiveObject("Altium.Application")
            script_path = self.get_script_path()
            if not script_path.exists():
                return False

            altium.OpenDocument(str(script_path))
            return True

        except ImportError:
            logger.error("pywin32 not installed. Install with: pip install pywin32")
            return False
        except Exception as e:
            logger.error("Error opening script project via COM: %s", e)
            return False
