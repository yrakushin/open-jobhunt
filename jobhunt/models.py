from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Vacancy:
    id: str
    title: str
    company: str
    url: str
    salary: str = ""
    compensation: dict | None = None
    region: str = ""
    description: str = ""
    match_percent: int = 0
    match_reason: str = ""
    published_at: datetime | None = None


@dataclass
class ApplicationResult:
    vacancy: Vacancy
    status: str  # sent | skipped | manual | error
    reason: str = ""
    letter: str = ""
    notes: str = ""


@dataclass
class RunReport:
    sent: list[ApplicationResult] = field(default_factory=list)
    manual: list[ApplicationResult] = field(default_factory=list)
    skipped: list[ApplicationResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    resume_hash: str = ""
    output_path: Path | None = None

    def summary(self) -> dict[str, Any]:
        skip_reasons: dict[str, int] = {}
        for item in self.skipped:
            skip_reasons[item.reason] = skip_reasons.get(item.reason, 0) + 1
        manual_reasons: dict[str, int] = {}
        for item in self.manual:
            manual_reasons[item.reason] = manual_reasons.get(item.reason, 0) + 1
        return {
            "sent": len(self.sent),
            "manual": len(self.manual),
            "skipped": len(self.skipped),
            "skip_top": sorted(skip_reasons.items(), key=lambda x: -x[1])[:3],
            "manual_top": sorted(manual_reasons.items(), key=lambda x: -x[1])[:5],
        }
