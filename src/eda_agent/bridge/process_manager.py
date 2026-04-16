# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Altium process detection and management."""

import logging
import psutil
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("eda_agent.bridge.process")


@dataclass
class AltiumProcessInfo:
    """Information about a running Altium process."""

    pid: int
    name: str
    exe_path: str
    version: Optional[str] = None
    cmdline: Optional[list[str]] = None


class AltiumProcessManager:
    """Manages detection and interaction with Altium Designer process."""

    PROCESS_NAMES = ["X2.exe", "DXP.exe"]  # Altium Designer executable names

    def __init__(self):
        pass

    def find_altium_process(self) -> Optional[AltiumProcessInfo]:
        """Find a running Altium Designer process.

        Returns:
            AltiumProcessInfo if found, None otherwise.
        """
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                proc_name = proc.info["name"] or ""
                if proc_name.upper() in [n.upper() for n in self.PROCESS_NAMES]:
                    info = AltiumProcessInfo(
                        pid=proc.info["pid"],
                        name=proc.info["name"],
                        exe_path=proc.info["exe"] or "",
                        cmdline=proc.info["cmdline"],
                    )
                    logger.debug("Found Altium process: PID=%d", proc.info["pid"])
                    return info
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return None

    def is_altium_running(self) -> bool:
        """Check if Altium Designer is running.

        Returns:
            True if Altium is running, False otherwise.
        """
        return self.find_altium_process() is not None

    def get_altium_info(self) -> Optional[AltiumProcessInfo]:
        """Get information about the running Altium process.

        Returns:
            AltiumProcessInfo if Altium is running, None otherwise.
        """
        return self.find_altium_process()

    def get_altium_pid(self) -> Optional[int]:
        """Get the PID of the running Altium process.

        Returns:
            PID if Altium is running, None otherwise.
        """
        process = self.find_altium_process()
        return process.pid if process else None

    def refresh(self) -> None:
        """Re-scan for the Altium process."""
        self.find_altium_process()
