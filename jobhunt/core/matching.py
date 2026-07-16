from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TYPE_WEIGHTS = {
    "must": 3.0,
    "preferred": 1.5,
    "bonus": 0.5,
    "blocker": 3.0,
}

TEST_PATTERNS = [
    r"пройти тест",
    r"пройти опрос",
    r"тестовое задание",
    r"assessment",
    r"опросник",
    r"пройдите тест",
    r"online[- ]test",
    r"кейс[- ]интервью",
]


@dataclass
class MatchEvaluation:
    score: int
    reason: str
    must_gaps: list[str] = field(default_factory=list)
    requires_test: bool = False
    criteria_count: int = 0
    blocked: bool = False


def detect_requires_test(title: str, description: str) -> bool:
    blob = f"{title}\n{description}".lower()
    return any(re.search(p, blob, re.I) for p in TEST_PATTERNS)


def load_match_prompt(root: Path | None = None) -> str:
    base = root or Path(__file__).resolve().parents[2]
    path = base / "prompts" / "match-scoring.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "Оцени match 0-100. JSON: {\"criteria\":[], \"requires_test\": false, \"must_gaps\":[], \"reason\":\"\"}"


def parse_match_response(raw: str) -> dict:
    if not raw:
        return {}
    for pattern in (
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
        r"(\{.*\})",
    ):
        m = re.search(pattern, raw, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return {}


def valid_match_payload(data: dict) -> bool:
    criteria = data.get("criteria")
    return isinstance(criteria, list) and len(criteria) > 0


def compute_weighted_score(data: dict) -> MatchEvaluation:
    criteria = data.get("criteria") or []
    requires_test = bool(data.get("requires_test"))
    must_gaps = [str(x) for x in (data.get("must_gaps") or []) if x]
    reason = str(data.get("reason") or "weighted match")

    if not criteria:
        if isinstance(data.get("score"), (int, float)):
            score = min(100, max(0, int(data["score"])))
            return MatchEvaluation(score=score, reason=reason, requires_test=requires_test)
        return MatchEvaluation(
            score=0,
            reason="LLM: нет criteria[]",
            requires_test=requires_test,
        )

    blocked = False
    must_failures = 0
    weighted_sum = 0.0
    total_weight = 0.0

    for c in criteria:
        ctype = str(c.get("type", "preferred")).lower()
        if ctype not in TYPE_WEIGHTS:
            ctype = "preferred"
        weight = float(c.get("weight") or TYPE_WEIGHTS[ctype])
        match = float(c.get("match", 0))
        match = max(0.0, min(1.0, match))

        if ctype == "blocker" and match < 0.5:
            blocked = True
        if ctype == "must" and match < 0.4:
            must_failures += 1
            name = str(c.get("name", ""))
            if name and name not in must_gaps:
                must_gaps.append(name)

        weighted_sum += weight * match
        total_weight += weight

    if blocked:
        return MatchEvaluation(
            score=0,
            reason="blocker: " + reason,
            must_gaps=must_gaps,
            requires_test=requires_test,
            criteria_count=len(criteria),
            blocked=True,
        )

    raw_score = int(round(100 * weighted_sum / total_weight)) if total_weight > 0 else 0
    raw_score = min(100, max(0, raw_score))

    if must_failures >= 2:
        raw_score = min(raw_score, 45)
    elif must_failures == 1:
        raw_score = min(raw_score, 49)

    return MatchEvaluation(
        score=raw_score,
        reason=reason,
        must_gaps=must_gaps,
        requires_test=requires_test,
        criteria_count=len(criteria),
    )
