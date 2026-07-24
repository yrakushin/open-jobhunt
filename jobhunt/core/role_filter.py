from __future__ import annotations

import re

from jobhunt.models import Vacancy

ROLE_FILTER_MODES: dict[str, dict[str, str]] = {
    "off": {
        "label": "Свободно",
        "hint": "Фильтр по названию роли выключен",
    },
    "role_lock": {
        "label": "По роли",
        "hint": "PM/PO из резюме; в «Пожеланиях» можно добавить роли («откликайся на IT Project Manager»)",
    },
}

DEFAULT_ROLE_DUTIES_MIN_PERCENT = 40

# Жёсткий отсев: не PM/PO даже при совпадении обязанностей
HARD_REJECT_TITLE_PATTERNS: list[tuple[str, str]] = [
    (r"\bархитект", "архитектор"),
    (r"\barchitect\b", "architect"),
    (r"\bengineer\b", "engineer"),
    (r"\bинженер\b", "инженер"),
    (r"r\s*&\s*d\s*engineer", "R&D engineer"),
    (r"solutions?\s+engineer", "solutions engineer"),
    (r"fullstack", "fullstack"),
    (r"developer\b", "developer"),
    (r"разработчик", "разработчик"),
    (r"project[\s-]?manager", "project manager"),
    (r"менеджер\s+проект", "менеджер проектов"),
    (r"руководитель\s+проект", "руководитель проектов"),
    (r"it[\s-]?project", "IT project"),
    (r"delivery[\s/]?(?:manager|men)", "delivery"),
    (r"program\s+manager", "program manager"),
    (r"business\s+analyst", "business analyst"),
    (r"бизнес[\s-]?аналит", "бизнес-аналитик"),
    (r"системный\s+аналит", "системный аналитик"),
    (r"product\s+analyst", "product analyst"),
    (r"продуктовый\s+аналит", "продуктовый аналитик"),
    (r"data\s+partner", "data partner"),
    (r"дата[\s-]?партнер", "дата-партнер"),
    (r"\bmarketing\b", "marketing"),
    (r"маркетинг", "маркетинг"),
    (r"product\s+marketing", "product marketing"),
    (r"brand[\s-]?manager", "brand manager"),
    (r"бренд[\s-]?мен", "бренд-менеджер"),
    (r"\bsales\b", "sales"),
    (r"продаж", "продажи"),
    (r"presale", "presale"),
    (r"пресейл", "пресейл"),
    (r"bizdev", "bizdev"),
    (r"развитию\s+бизнеса", "bizdev"),
    (r"\bcrm[\s-]?мен", "CRM"),
    (r"crm\s+master", "CRM"),
    (r"concept[\s-]?research", "researcher"),
    (r"методолог", "методолог"),
    (r"ассистент", "ассистент"),
    (r"\bassistant\b", "assistant"),
    (r"партнер", "партнёры"),
    (r"partner\s+manager", "partner manager"),
    (r"solution\s+owner", "solution owner"),
    (r"солюшн", "solution manager"),
    (r"e-commerce\s+manager", "e-commerce manager"),
    (r"руководитель\s+веб", "веб-проекты"),
    (r"technical\s+project", "technical project"),
    (r"team\s+lead", "team lead"),
    (r"lead\s+qualification", "lead qualification"),
    (r"researcher", "researcher"),
    (r"customer\s+success", "customer success"),
    (r"клиентскому\s+опыту", "CX"),
    (r"операционн", "operations"),
    (r"operations\s+lead", "operations lead"),
    (r"account\s+manager", "account manager"),
]

PRODUCT_OWNER_PATTERNS = [
    r"product\s*owner",
    r"product-owner",
    r"product[\s\-/]*owner[\s\-/]*ai",
    r"ai[\s\-/]*product[\s\-/]*owner",
    r"владелец\s*продукт",
    r"\bpo\b",
    r"stream\s+product\s+owner",
    r"technical\s+product\s+owner",
    r"lead\s+product\s+owner",
    r"senior\s+product\s+owner",
]

PRODUCT_MANAGER_PATTERNS = [
    r"product\s*manager",
    r"product-manager",
    r"product[\s\-/]*manager[\s\-/]*ai",
    r"product[\s\-/]*менедж",
    r"ai[\s\-/]*продакт",
    r"продакт[\s\-/]*менедж",
    r"product\s*lead",
    r"head\s+of\s+product",
    r"chief\s+product",
    r"\bcpo\b",
    r"директор\s+по\s+продукт",
    r"руководитель\s+группы\s+продукт",
    r"руководитель\s+продукт",
    r"менеджер\s+продукт",
    r"менеджер\s+по\s+продукт",
    r"продакт[\s-]?менедж",
    r"product\s+manager",
    r"it\s+product\s+manager",
    r"growth\s+product",
    r"lead\s+product\s+manager",
    r"lead\s+ai\s+product",
    r"ai\s+product\s+manager",
    r"technical\s+product\s+manager",
    r"technical\s+product\s+lead",
    r"data\s+product\s+manager",
    r"(?:senior|lead|principal|staff|главный|ведущий)\s+product\s+(?:manager|owner|менеджер|владелец)",
]

PM_DUTY_TERMS: list[str] = ["discovery", "delivery", "backlog", "roadmap", "stakeholder", "hypothesis", "priorit", "a/b", "метрик", "бэклог", "product vision", "user research", "custdev", "cust dev", "scrum", "agile", "kanban", "внедрен", "product ownership", "управление продукт", "feature", "mvp", "go-to-market", "gtm", "retention", "unit economics", "юнит-эконом", "воронк", "conversion", "конверс", "prd", "user story", "b2b", "enterprise", "presale", "ai", "llm", "genai", "rag", "agent", "automation", "workflow", "координац"]


def role_filter_enabled(search_cfg: dict | None) -> bool:
    mode = str((search_cfg or {}).get("role_filter_mode") or "off").lower()
    return mode == "role_lock"


def parse_resume_roles(resume_title: str) -> list[str]:
    """«Product Manager / Product Owner» → ['Product Manager', 'Product Owner']."""
    raw = (resume_title or "").strip()
    if not raw:
        return []

    title = re.sub(r"\([^)]*\)", "", raw).strip()
    title = re.sub(r"\[[^\]]*\]", "", title).strip()
    if not title:
        return [raw]

    chunks = re.split(r"[/|,]|(?:\s+&\s+)|(?:\s+и\s+)", title, flags=re.I)
    chunks = [c.strip() for c in chunks if c.strip()]
    if len(chunks) <= 1:
        return [raw] if raw else chunks

    prefix = ""
    roles: list[str] = []
    for i, part in enumerate(chunks):
        words = part.split()
        if i == 0:
            if len(words) > 1:
                prefix = " ".join(words[:-1])
            roles.append(part)
            continue
        if len(words) == 1 and prefix:
            roles.append(f"{prefix} {part}".strip())
        else:
            roles.append(part)

    out: list[str] = []
    seen: set[str] = set()
    for r in roles:
        key = r.lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out or [raw]


_PREFERENCE_ROLE_TRIGGERS = [
    r"(?:роли|должности)\s*[:：]\s*(.+)",
    r"(?:отклик(?:айся|аться|ы)?|смотри|ищи|добавь|включи|разреши)\s+(?:ещ[её]\s+)?(?:на\s+)?(?:вакансии\s+)?(.+)",
    r"(?:также|тоже)\s+(?:на\s+)?(?:вакансии\s+)?(.+)",
    r"ещ[её]\s+на\s+(?:вакансии\s+)?(.+)",
]
_PREFERENCE_ROLE_TRIGGERS_COMPILED = [re.compile(p, re.I) for p in _PREFERENCE_ROLE_TRIGGERS]


def _split_role_phrases(chunk: str) -> list[str]:
    chunk = re.sub(r"[.!?]+$", "", chunk.strip())
    parts = re.split(r"[,;]|(?:\s+и\s+)", chunk, flags=re.I)
    out: list[str] = []
    for part in parts:
        p = part.strip()
        p = re.sub(r"^(?:вакансии|роли|должности)\s+", "", p, flags=re.I)
        if len(p) >= 3:
            out.append(p)
    return out


def parse_extra_roles_from_preferences(vacancy_preferences: str) -> list[str]:
    """«откликайся ещё на вакансии IT Project Manager» → ['IT Project Manager']."""
    text = (vacancy_preferences or "").strip()
    if not text:
        return []

    found: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for pat in _PREFERENCE_ROLE_TRIGGERS_COMPILED:
            m = pat.search(line)
            if m:
                found.extend(_split_role_phrases(m.group(1)))
                break

    out: list[str] = []
    seen: set[str] = set()
    for role in found:
        key = role.lower()
        if key not in seen:
            seen.add(key)
            out.append(role)
    return out


def build_allowed_roles(resume_title: str, vacancy_preferences: str = "") -> tuple[list[str], list[str]]:
    """(роли из резюме, доп. роли из пожеланий)."""
    return parse_resume_roles(resume_title), parse_extra_roles_from_preferences(vacancy_preferences)


def _norm(text: str) -> str:
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[^\w\s/-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _role_family(resume_role: str) -> str:
    r = _norm(resume_role)
    if re.search(r"\bowner\b|владелец|\bpo\b", r) and "manager" not in r and "менеджер" not in r:
        return "product_owner"
    if any(
        x in r
        for x in (
            "manager",
            "менеджер",
            "продакт",
            "cpo",
            "director",
            "директор",
            "head",
            "lead",
            "руководитель",
        )
    ):
        return "product_manager"
    if "product" in r or "продукт" in r:
        return "product_manager"
    return "unknown"


def _patterns_for_resume_role(resume_role: str) -> list[str]:
    fam = _role_family(resume_role)
    if fam == "product_owner":
        return list(PRODUCT_OWNER_PATTERNS)
    if fam == "product_manager":
        return list(PRODUCT_MANAGER_PATTERNS)
    escaped = re.escape(_norm(resume_role))
    return [escaped.replace(r"\ ", r"\s+")]


def _title_matches_role(title: str, resume_role: str) -> bool:
    norm = _norm(title)
    for pat in _patterns_for_resume_role(resume_role):
        if re.search(pat, norm, re.I):
            return True
    role_norm = _norm(resume_role)
    if len(role_norm) >= 4 and role_norm in norm:
        return True
    return False


def _title_matches_any_resume_role(title: str, resume_roles: list[str]) -> tuple[bool, str]:
    for role in resume_roles:
        if _title_matches_role(title, role):
            return True, f"роль: {role}"
    return False, ""


def _title_matches_extra_role(title: str, extra_role: str) -> bool:
    norm = _norm(title)
    role_norm = _norm(extra_role)
    if not role_norm:
        return False
    if role_norm in norm:
        return True
    escaped = re.escape(role_norm).replace(r"\ ", r"\s+")
    if re.search(escaped, norm, re.I):
        return True
    words = [w for w in role_norm.split() if len(w) > 1]
    return bool(words) and all(w in norm for w in words)


def _title_matches_any_extra_role(title: str, extra_roles: list[str]) -> tuple[bool, str]:
    for role in extra_roles:
        if _title_matches_extra_role(title, role):
            return True, f"пожелание: {role}"
    return False, ""


def _hard_reject_reason(title: str, *, extra_roles: list[str] | None = None) -> str | None:
    ok, _ = _title_matches_any_extra_role(title, extra_roles or [])
    if ok:
        return None
    norm = _norm(title)
    for pat, label in HARD_REJECT_TITLE_PATTERNS:
        if re.search(pat, norm, re.I):
            return label
    return None


def duties_overlap_percent(vacancy_description: str, profile_text: str) -> int:
    """Доля терминов из профиля, которые есть в описании вакансии (0–100)."""
    vac = (vacancy_description or "").lower()
    prof = (profile_text or "").lower()
    if not vac or not prof:
        return 0

    profile_terms = [t for t in PM_DUTY_TERMS if t in prof]
    if not profile_terms:
        profile_terms = PM_DUTY_TERMS

    matched = sum(1 for t in profile_terms if t in vac)
    if not profile_terms:
        return 0
    return int(round(100 * matched / len(profile_terms)))


def check_role_lock(
    resume_roles: list[str],
    vacancy: Vacancy,
    profile_text: str,
    min_duties_percent: int = DEFAULT_ROLE_DUTIES_MIN_PERCENT,
    extra_roles: list[str] | None = None,
) -> tuple[bool, str]:
    extra_roles = extra_roles or []
    if not resume_roles and not extra_roles:
        return True, "роли не заданы"

    title = vacancy.title or ""

    ok, reason = _title_matches_any_extra_role(title, extra_roles)
    if ok:
        return True, reason

    ok, reason = _title_matches_any_resume_role(title, resume_roles)
    if ok:
        return True, reason

    reject = _hard_reject_reason(title, extra_roles=extra_roles)
    if reject:
        return False, f"не PM/PO ({reject})"

    pct = duties_overlap_percent(vacancy.description or "", profile_text)
    if pct >= min_duties_percent:
        return True, f"обязанности {pct}% (другое название)"

    labels = list(resume_roles)
    if extra_roles:
        labels.extend([f"+{r}" for r in extra_roles])
    roles_label = ", ".join(labels) if labels else "—"
    return False, f"роль не «{roles_label}», обязанности {pct}%"
