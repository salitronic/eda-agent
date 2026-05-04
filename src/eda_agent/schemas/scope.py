# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba
"""Re-export the structured scope schema from eda_agent.scope.

The scope helper lives at the top-level so callers can import it without
pulling in the full schemas package, but the schema generator needs it
in the same registry as command schemas.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ScopeSchema(BaseModel):
    """Structured scope object as it appears on the wire inside params."""

    type: Literal["active_doc", "project", "doc"] = "active_doc"
    file_path: Optional[str] = Field(default=None)
