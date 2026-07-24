from __future__ import annotations

import re

from jobhunt.models import Vacancy

STOP_ROLE_PATTERNS = [
    r"\binternship\b",
    r"\btrainee\b",
    r"\bapprentice\b",
    r"\bjunior\b",
    r"\bджун\b",
    r"\bстаж[её]р",
    r"\bстажировк",
    r"без опыта",
    r"первая работа",
    r"graduate program",
    r"для студент",
    r"выпускник",
    r"devrel",
    r"developer relations",
    r"product marketing",
    r"продуктов\w*\s+маркетинг",
    r"продакт[\s-]?маркет",
    r"маркетолог",
    r"младший pm",
    r"помощник pm",
    r"assistant product",
]

MFO_PATTERNS = [
    r"\bмфо\b",
    r"микрофинанс",
    r"микрозайм",
    r"займ до зарплаты",
    r"payday",
]

IP_PATTERNS = [
    r"\bип\b",
    r"индивидуальный предприниматель",
    r"частный предприниматель",
    r"физлицо",
]

# Юрлица / бренды — не путать с ФИО рекрутера
_ORG_MARKERS_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:ооо|оао|зао|пао|нко|ано|лтт|ltd|llc|inc|gmbh|corp|plc)\b|"
    r"\b(?:group|holding|company|companies|studio|games|bank|lab|labs)\b|"
    r"\b(?:компани|групп|холдинг|платформ|банк|школ|академи|лаборатор|"
    r"сервис|софт|технолог|студи|агентств|фабрик|завод|институт|"
    r"университет|клиник|поликлиник|больниц|магазин|маркетплейс)\w*"
    r")"
)
_CYR_NAME_WORD_RE = re.compile(r"^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?$")
_PATRONYMIC_RE = re.compile(r"(?i)(ович|евич|ична|овна|евна|оглы|кызы)$")


def looks_like_person_name(company: str) -> bool:
    """True только для ИП / явного ФИО. Не режет «Группа Компаний …», English brands."""
    c = (company or "").strip()
    if not c:
        return False
    if re.search(r"(?i)(?:^|[^\w])ип(?:[^\w]|$)|индивидуальный\s+предприниматель", c):
        return True
    if _ORG_MARKERS_RE.search(c):
        return False
    letters = [ch for ch in c if ch.isalpha()]
    if letters:
        latin = sum(1 for ch in letters if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))
        if latin / len(letters) >= 0.45:
            return False
    words = [w for w in re.sub(r"[^\wа-яА-ЯёЁ\s-]", " ", c).split() if w]
    if not (2 <= len(words) <= 3):
        return False
    if not all(_CYR_NAME_WORD_RE.match(w) for w in words):
        return False
    if len(words) == 2:
        return True
    return bool(_PATRONYMIC_RE.search(words[-1]))


def normalize_company(name: str) -> str:
    n = name.lower().strip()
    for token in ("ооо", "пао", "ао", "зао", "оао", '"', "«", "»", "'"):
        n = n.replace(token, " ")
    return re.sub(r"\s+", " ", n).strip()


def parse_salary_upper(salary: str) -> int | None:
    if not salary or "не указан" in salary.lower():
        return None
    nums = [int(x.replace(" ", "")) for x in re.findall(r"(\d[\d\s]{2,})", salary)]
    if not nums:
        return None
    return max(nums)


def _salary_amount(value: object) -> int | None:
    if value is None:
        return None
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    # hh.ru иногда отдаёт 0 или мусор; реальная зарплата на hh — от ~10k ₽
    if amount < 10_000:
        return None
    return amount


def compensation_is_specified(comp: dict | str | None) -> bool:
    if not comp or isinstance(comp, str):
        return False
    if comp.get("noCompensation") is not None:
        return False
    lo, hi = compensation_bounds(comp)
    return lo is not None or hi is not None


def compensation_bounds(comp: dict | str | None) -> tuple[int | None, int | None]:
    """Вилка зарплаты из JSON hh.ru: (from, to) в рублях."""
    if not comp or isinstance(comp, str):
        return None, None
    if comp.get("noCompensation") is not None:
        return None, None
    return _salary_amount(comp.get("from")), _salary_amount(comp.get("to"))


def parse_salary_range_from_text(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    nums = [int(x.replace(" ", "")) for x in re.findall(r"(\d[\d\s]{2,})", text)]
    if not nums:
        return None, None
    if len(nums) >= 2:
        return min(nums), max(nums)
    return nums[0], nums[0]


def salary_below_minimum(
    compensation: dict | str | None,
    salary_text: str,
    min_sal: int,
    description: str = "",
) -> tuple[bool, str]:
    """True = вакансию отсекаем (зарплата ниже порога). Только если зарплата явно указана на hh.ru."""
    if min_sal <= 0:
        return False, ""
    comp_dict = compensation if isinstance(compensation, dict) else None
    if not compensation_is_specified(comp_dict):
        return False, ""
    lo, hi = compensation_bounds(comp_dict)
    # Отсекаем только когда верх вилки явно ниже порога (например «до 299k» или «200–280k»).
    # «от 250k» без потолка и «250–300k» при пороге 300k — не отсекаем.
    if hi is not None and hi < min_sal:
        if lo is not None and lo != hi:
            return True, f"зарплата {lo // 1000}–{hi // 1000}k < {min_sal // 1000}k"
        return True, f"зарплата до {hi // 1000}k < {min_sal // 1000}k"
    return False, ""


def rule_match_score(title: str, description: str, profile_text: str) -> tuple[int, str]:
    text = f"{title}\n{description}".lower()
    score = 50
    reasons: list[str] = []

    pm_signals = ["product manager", "product owner", "менеджер продукта", "продакт", "product management"]
    ai_signals = ["ai", "llm", "genai", "rag", "gpt", "ml product", "искусственн", "нейросет", "агент"]
    bad_ic = ["data scientist", "ml engineer", "backend developer", "devops", "qa engineer"]
    bad_other = ["sales manager", "account manager", "hr ", "рекрутер", "business analyst"]

    if any(s in text for s in pm_signals):
        score += 20
        reasons.append("PM scope")
    if any(s in text for s in ai_signals):
        score += 20
        reasons.append("AI/LLM")
    if "enterprise" in text or "b2b" in text:
        score += 5
    if any(s in text for s in bad_ic):
        score -= 25
        reasons.append("IC role")
    if any(s in text for s in bad_other):
        score -= 20
        reasons.append("non-PM")
    if "project manager" in text and "product" not in text:
        score -= 15
        reasons.append("project not product")

    score = min(100, max(0, score))
    return score, ", ".join(reasons) or "rule-based"


def company_in_list(company: str, entries: list[str]) -> bool:
    norm_co = normalize_company(company)
    if not norm_co:
        return False
    for entry in entries:
        ne = normalize_company(entry)
        if not ne:
            continue
        if ne in norm_co or norm_co in ne:
            return True
    return False


def vacancy_similarity_key(vacancy: Vacancy) -> str:
    """Ключ похожести: одна компания + то же название (разные ID — разные HR)."""
    title = re.sub(r"\s+", " ", vacancy.title.lower().strip())
    return f"{normalize_company(vacancy.company)}|{title}"


def pre_filter_vacancy(
    vacancy: Vacancy,
    cfg: dict,
    applied_ids: set[str],
    company_counts: dict[str, int],
    *,
    profile_text: str = "",
    resume_title: str = "",
    salary_only: bool = False,
) -> tuple[bool, str]:
    if not vacancy.title.strip() and not normalize_company(vacancy.company):
        return False, "не удалось прочитать вакансию"

    if vacancy.id in applied_ids:
        return False, "уже откликались на hh.ru"

    title_blob = (vacancy.title or "").lower()
    full_blob = f"{vacancy.title}\n{vacancy.description}\n{vacancy.company}".lower()
    norm_co = normalize_company(vacancy.company)
    filters = cfg.get("filters", {})

    whitelist = filters.get("whitelist_employers", [])
    if whitelist and not company_in_list(vacancy.company, whitelist):
        return False, "не в списке разрешённых компаний"

    for blk in filters.get("blacklist_employers", []):
        if company_in_list(vacancy.company, [blk]):
            return False, f"чёрный список: {blk}"

    for pause in filters.get("pause_employers", []):
        if company_in_list(vacancy.company, [pause]):
            return False, f"чёрный список: {pause}"

    min_sal = cfg.get("search", {}).get("min_salary_rub") or 0
    if min_sal > 0:
        comp = getattr(vacancy, "compensation", None)
        below, sal_reason = salary_below_minimum(
            comp, vacancy.salary, min_sal, vacancy.description or ""
        )
        if below:
            return False, sal_reason

    max_co = cfg.get("search", {}).get("max_per_company", 0)
    if max_co > 0 and company_counts.get(norm_co, 0) >= max_co:
        return False, f"лимит компании ({max_co} за прогон)"

    # Режим «только зарплата»: без match / роли / стоп-ролей / МФО / ИП
    if salary_only:
        return True, ""

    # Стоп-роль — только по названию вакансии (в описании часто «product marketing», «junior» в контексте команды).
    for pat in STOP_ROLE_PATTERNS:
        if re.search(pat, title_blob, re.I):
            return False, "стоп-роль"

    for pat in MFO_PATTERNS:
        if re.search(pat, full_blob, re.I):
            return False, "МФО/микрофинансы"

    if looks_like_person_name(vacancy.company):
        return False, "ИП/ФИО работодатель"

    search = cfg.get("search", {})
    from jobhunt.core.role_filter import (
        build_allowed_roles,
        check_role_lock,
        duties_overlap_percent,
        role_filter_enabled,
    )
    from jobhunt.core.vacancy_age import check_vacancy_age

    ok_age, age_reason = check_vacancy_age(getattr(vacancy, "published_at", None), search)
    if not ok_age:
        return False, age_reason

    if role_filter_enabled(search):
        title_src = resume_title or cfg.get("profile", {}).get("resume_title", "")
        prefs = str(search.get("vacancy_preferences") or "")
        resume_roles, extra_roles = build_allowed_roles(title_src, prefs)
        min_duties = int(search.get("role_duties_min_percent") or 40)
        ok, role_reason = check_role_lock(
            resume_roles,
            vacancy,
            profile_text,
            min_duties,
            extra_roles=extra_roles,
        )
        if not ok:
            return False, role_reason
    else:
        # Свободный режим («off»): без ограничения по названию роли и без hard-reject,
        # но отклик только если опыт/обязанности совпадают ≥ free_experience_min_percent.
        free_min = int(search.get("free_experience_min_percent") or 30)
        pct = duties_overlap_percent(vacancy.description or "", profile_text)
        if pct < free_min:
            return False, f"опыт {pct}% < {free_min}%"

    return True, ""
