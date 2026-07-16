from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

VACANCY_AGE_MODES: dict[str, dict[str, Any]] = {
    "all": {
        "label": "Всё время",
        "max_hours": None,
        "period_days": 0,
    },
    "24h": {
        "label": "24 часа",
        "max_hours": 24,
        "period_days": 1,
    },
    "3d": {
        "label": "3 дня",
        "max_hours": 72,
        "period_days": 3,
    },
    "7d": {
        "label": "7 дней",
        "max_hours": 168,
        "period_days": 7,
    },
}

DEFAULT_VACANCY_AGE_MODE = "all"


def normalize_vacancy_age_mode(mode: str | None) -> str:
    key = str(mode or DEFAULT_VACANCY_AGE_MODE).lower()
    if key in VACANCY_AGE_MODES:
        return key
    return DEFAULT_VACANCY_AGE_MODE


def vacancy_age_mode_from_search(search: dict[str, Any]) -> str:
    if search.get("vacancy_age_mode"):
        return normalize_vacancy_age_mode(search.get("vacancy_age_mode"))
    period = int(search.get("period_days") or 0)
    if period <= 0:
        return "all"
    if period <= 1:
        return "24h"
    if period <= 3:
        return "3d"
    return "7d"


def max_hours_for_mode(mode: str) -> int | None:
    return VACANCY_AGE_MODES[normalize_vacancy_age_mode(mode)]["max_hours"]


def period_days_for_mode(mode: str) -> int:
    return int(VACANCY_AGE_MODES[normalize_vacancy_age_mode(mode)]["period_days"])


def parse_hh_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.+)([+-]\d{2})(\d{2})$", s)
    if m and ":" not in m.group(2):
        s = f"{m.group(1)}{m.group(2)}:{m.group(3)}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


_PUBLICATION_DATE_KEYS: tuple[str, ...] = (
    "published_at",
    "publishedAt",
    "publicationDate",
    "publicationTime",
    "lastPublicationTime",
    "publishedTime",
)


def extract_published_at(vacancy_dict: dict[str, Any]) -> datetime | None:
    """Дата последней публикации/поднятия. Не путать с creationTime / initialCreatedAt."""
    if not vacancy_dict:
        return None
    for key in _PUBLICATION_DATE_KEYS:
        val = vacancy_dict.get(key)
        if val:
            dt = parse_hh_datetime(str(val))
            if dt:
                return dt
    nested = vacancy_dict.get("vacancy")
    if isinstance(nested, dict) and nested is not vacancy_dict:
        return extract_published_at(nested)
    return None


def pick_newer_published_at(*dates: datetime | None) -> datetime | None:
    valid = [d for d in dates if d is not None]
    if not valid:
        return None
    return max(valid, key=lambda d: d.astimezone(timezone.utc))


def check_vacancy_age(
    published_at: datetime | None,
    search: dict[str, Any],
) -> tuple[bool, str]:
    mode = vacancy_age_mode_from_search(search)
    max_hours = max_hours_for_mode(mode)
    if max_hours is None:
        return True, ""
    if published_at is None:
        return True, ""
    now = datetime.now(timezone.utc)
    pub = published_at.astimezone(timezone.utc)
    age_sec = (now - pub).total_seconds()
    if age_sec <= max_hours * 3600:
        return True, ""
    label = VACANCY_AGE_MODES[mode]["label"]
    hours = age_sec / 3600
    if hours < 48:
        detail = f"{int(hours)} ч. назад"
    else:
        detail = f"{hours / 24:.1f} дн. назад"
    return False, f"старше {label} ({detail})"
