from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    cfg_path = path or root / "config.yaml"
    if not cfg_path.exists():
        example = root / "config.example.yaml"
        if example.exists():
            cfg_path = example
        else:
            raise FileNotFoundError(
                "Нет config.yaml — скопируй config.example.yaml в config.yaml "
                "(macOS/Linux: cp config.example.yaml config.yaml; "
                "Windows: copy config.example.yaml config.yaml)"
            )
    with cfg_path.open(encoding="utf-8") as f:
        cfg: dict[str, Any] = yaml.safe_load(f) or {}

    cfg["_root"] = str(root)
    cfg["_config_path"] = str(cfg_path.resolve())

    browser = cfg.setdefault("browser", {})
    browser["profile_dir"] = str(_expand(browser.get("profile_dir", "~/.jobhunt/browser-profile")))

    paths = cfg.setdefault("paths", {})
    reports = paths.get("reports_dir", "reports")
    if not Path(reports).is_absolute():
        paths["reports_dir"] = str((root / reports).resolve())
    else:
        paths["reports_dir"] = str(_expand(reports))
    paths["data_dir"] = str(_expand(paths.get("data_dir", "~/.jobhunt")))

    profile = cfg.setdefault("profile", {})
    for key in ("profile_path", "letter_style_path", "letter_template_path"):
        val = profile.get(key)
        if val and not Path(val).is_absolute():
            profile[key] = str((root / val).resolve())

    return cfg


def read_text_file(path: str | Path) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


def read_letter_template_text(path: str | Path) -> str:
    """Шаблон письма без блока документации после --- (legacy из старого example)."""
    raw = read_text_file(path).strip()
    if not raw:
        return ""
    sep = "\n---\n"
    idx = raw.find(sep)
    if idx >= 0:
        raw = raw[:idx].strip()
    return raw


def save_profile_text(cfg: dict[str, Any], text: str) -> Path:
    path = Path(cfg["profile"]["profile_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def save_letter_template(cfg: dict[str, Any], text: str) -> Path:
    root = Path(cfg.get("_root", "."))
    raw = str(cfg["profile"].get("letter_template_path", "") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
    else:
        path = root / "context" / "cover-letter.md"
        cfg["profile"]["letter_template_path"] = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def update_profile_field(cfg_path: Path, field: str, value: str) -> None:
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("profile", {})[field] = value
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def update_resume_search_url(cfg_path: Path, url: str) -> None:
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("search", {})["resume_search_url"] = url.strip()
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def update_filters(cfg_path: Path, blacklist: list[str], whitelist: list[str]) -> None:
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    flt = data.setdefault("filters", {})
    flt["blacklist_employers"] = [x.strip() for x in blacklist if x.strip()]
    flt["whitelist_employers"] = [x.strip() for x in whitelist if x.strip()]
    # pause_employers — устаревший дубль чёрного списка; UI пишет только в blacklist
    flt["pause_employers"] = []
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def update_min_match(cfg_path: Path, percent: int) -> None:
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("search", {})["min_match_percent"] = percent
    match = data.setdefault("match", {})
    if percent >= 65:
        match["strictness"] = "high"
    elif percent >= 55:
        match["strictness"] = "medium"
    else:
        match["strictness"] = "low"
    match["min_percent"] = percent
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def update_role_filter(cfg_path: Path, mode: str, min_duties_percent: int | None = None) -> None:
    from jobhunt.core.role_filter import ROLE_FILTER_MODES, DEFAULT_ROLE_DUTIES_MIN_PERCENT

    mode = str(mode or "off").lower()
    if mode not in ROLE_FILTER_MODES:
        mode = "off"
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    search = data.setdefault("search", {})
    search["role_filter_mode"] = mode
    if min_duties_percent is not None:
        search["role_duties_min_percent"] = max(10, min(90, int(min_duties_percent)))
    elif "role_duties_min_percent" not in search:
        search["role_duties_min_percent"] = DEFAULT_ROLE_DUTIES_MIN_PERCENT
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def update_vacancy_age(cfg_path: Path, mode: str) -> None:
    from jobhunt.core.vacancy_age import (
        DEFAULT_VACANCY_AGE_MODE,
        VACANCY_AGE_MODES,
        normalize_vacancy_age_mode,
        period_days_for_mode,
    )

    mode = normalize_vacancy_age_mode(mode or DEFAULT_VACANCY_AGE_MODE)
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    search = data.setdefault("search", {})
    search["vacancy_age_mode"] = mode
    search["period_days"] = period_days_for_mode(mode)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def format_salary_expectation(min_salary_rub: int) -> str:
    """Текст ответа на вопрос о зарплате из порога UI."""
    n = max(0, int(min_salary_rub or 0))
    if n <= 0:
        return ""
    pretty = f"{n:,}".replace(",", " ")
    return f"от {pretty} ₽ на руки"


def update_search_preferences(
    cfg_path: Path,
    *,
    min_salary_rub: int | None = None,
    vacancy_preferences: str | None = None,
    free_experience_min_percent: int | None = None,
) -> None:
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    search = data.setdefault("search", {})
    if min_salary_rub is not None:
        search["min_salary_rub"] = max(0, int(min_salary_rub))
        # Тот же порог из UI → что отвечать на вопрос работодателя о деньгах
        profile = data.setdefault("profile", {})
        expect = format_salary_expectation(int(min_salary_rub))
        if expect:
            profile["salary_expectation"] = expect
        elif not str(profile.get("salary_expectation") or "").strip():
            profile["salary_expectation"] = ""
    if vacancy_preferences is not None:
        search["vacancy_preferences"] = vacancy_preferences.strip()
    if free_experience_min_percent is not None:
        search["free_experience_min_percent"] = max(10, min(100, int(free_experience_min_percent)))
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def update_match_settings(cfg_path: Path, settings: dict[str, Any]) -> None:
    from jobhunt.core.match_config import normalize_match_settings

    normalized = normalize_match_settings(settings)
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data["match"] = normalized
    data.setdefault("search", {})["min_match_percent"] = normalized["min_percent"]
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
