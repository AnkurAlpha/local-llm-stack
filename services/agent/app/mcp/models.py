from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MCPService:
    name: str
    category: str
    transport: str
    url: str
    metadata: dict[str, Any]
