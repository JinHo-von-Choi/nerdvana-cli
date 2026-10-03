"""The result of one nerdvana doctor check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class CheckResult:
    """Single diagnostic check result."""

    name:   str
    status: Literal["ok", "warn", "fail", "skip"]
    detail: str = ""
