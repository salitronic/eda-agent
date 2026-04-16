# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Allow running the package as a module: python -m eda_agent"""

import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main())
