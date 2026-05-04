# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Real-Altium integration tests.

These tests require:
  - Altium Designer running
  - The Altium_API.PrjScr script project loaded and StartMCPServer running
  - The fixture project at tests/integration/fixtures/EDAAgentTest.PrjPcb
    open and focused

Tests are skipped automatically when these preconditions aren't met.
Set EDA_AGENT_INTEGRATION=1 in the environment to require them (CI).
"""
