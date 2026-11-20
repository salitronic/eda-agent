# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""DRC-feedback repair planning for routed copper (offline, no Altium IPC).

:mod:`~eda_agent.route.repair` turns a DRC violation payload into an
ordered list of repair actions. The board router itself is
:mod:`eda_agent.layout.route`.
"""
