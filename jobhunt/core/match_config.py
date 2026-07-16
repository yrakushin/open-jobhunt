from __future__ import annotations

from typing import Any

# 3-балльная шкала важности критерия (UI + scoring)
TIER_LEVELS: dict[str, dict[str, Any]] = {
    "low": {
        "label": "Низкий",
        "weight": 0.5,
        "min_overlap": 0.15,
        "type": "bonus",
        "hint": "Смежная роль или ≥15% совпадения опыта — достаточно",
    },
    "medium": {
        "label": "Средний",
        "weight": 1.5,
        "min_overlap": 0.50,
        "type": "preferred",
        "hint": "Заметное совпадение, ~50%+ релевантного опыта",
    },
    "high": {
        "label": "Высокий",
        "weight": 3.0,
        "min_overlap": 0.85,
        "type": "must",
        "hint": "Обязательно: ~85%+ или прямое подтверждение в профиле",
    },
}

STRICTNESS_LEVELS: dict[str, dict[str, Any]] = {
    "low": {
        "label": "Низкий",
        "min_percent": 10,
        "hint": "Максимум откликов: порог от 10%, смежные роли проходят",
    },
    "medium": {
        "label": "Средний",
        "min_percent": 55,
        "hint": "Больше откликов: смежные роли, мягче по «будет плюсом»",
    },
    "high": {
        "label": "Высокий",
        "min_percent": 65,
        "hint": "Баланс: must закрыты, preferred частично",
    },
}

CRITERIA_CATALOG: list[dict[str, str]] = [
    {"id": "role", "label": "Роль PM / PO", "hint": "Product ownership, не project-only"},
    {"id": "ai_llm", "label": "AI / LLM", "hint": "GenAI, agents, RAG, conversational"},
    {"id": "experience", "label": "Опыт и уровень", "hint": "Годы, seniority, не junior"},
    {"id": "b2b", "label": "Enterprise B2B", "hint": "Корпоративные клиенты, сложные продукты"},
    {"id": "delivery", "label": "Discovery / delivery", "hint": "Бэклог, внедрение, координация"},
]

DEFAULT_CRITERIA: dict[str, str] = {
    "role": "high",
    "ai_llm": "medium",
    "experience": "high",
    "b2b": "low",
    "delivery": "medium",
}


MATCH_PRESETS: dict[str, dict[str, Any]] = {
    "low": {
        "strictness": "low",
        "criteria": {c["id"]: "low" for c in CRITERIA_CATALOG},
    },
    "medium": {
        "strictness": "medium",
        "criteria": {c["id"]: "low" for c in CRITERIA_CATALOG},
    },
    "high": {
        "strictness": "high",
        "criteria": dict(DEFAULT_CRITERIA),
    },
}


def preset_for_strictness(strictness: str) -> dict[str, Any]:
    key = strictness if strictness in MATCH_PRESETS else "medium"
    p = MATCH_PRESETS[key]
    return normalize_match_settings(p)


def normalize_match_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    strictness = str(raw.get("strictness") or "medium").lower()
    if strictness not in STRICTNESS_LEVELS:
        strictness = "medium"

    criteria_in = raw.get("criteria") or {}
    criteria: dict[str, str] = {}
    for item in CRITERIA_CATALOG:
        cid = item["id"]
        tier = str(criteria_in.get(cid) or DEFAULT_CRITERIA.get(cid, "medium")).lower()
        if tier not in TIER_LEVELS:
            tier = DEFAULT_CRITERIA.get(cid, "medium")
        criteria[cid] = tier

    min_percent = raw.get("min_percent")
    if min_percent is None:
        min_percent = STRICTNESS_LEVELS[strictness]["min_percent"]
    else:
        min_percent = int(min_percent)

    return {
        "strictness": strictness,
        "min_percent": min_percent,
        "criteria": criteria,
    }


def match_settings_from_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    search = cfg.get("search", {})
    block = cfg.get("match")
    if not block and search.get("min_match_percent") is not None:
        pct = int(search.get("min_match_percent", 65))
        strictness = "high" if pct >= 65 else "medium" if pct >= 55 else "low"
        block = {"strictness": strictness, "min_percent": pct}
    return normalize_match_settings(block)


def build_match_instructions(settings: dict[str, Any], *, apply_threshold: int | None = None) -> str:
    s = normalize_match_settings(settings)
    threshold = apply_threshold if apply_threshold is not None else s["min_percent"]
    lines = [
        f"Порог отклика: ≥{threshold}%. Строгость взвешивания: "
        f"{STRICTNESS_LEVELS[s['strictness']]['label']}.",
        "Важность критериев для этого кандидата (шкала 3 уровня):",
    ]
    for item in CRITERIA_CATALOG:
        cid = item["id"]
        tier = s["criteria"][cid]
        meta = TIER_LEVELS[tier]
        lines.append(
            f"- {item['label']} ({cid}): {meta['label']} — {meta['hint']}; "
            f"тип {meta['type']}, min overlap {int(meta['min_overlap']*100)}%"
        )
    lines.append(
        "При разборе вакансии сопоставляй требования с этими id. "
        "Если критерий с «Высокий» не закрыт (<85%) — must_gaps. "
        "«Низкий» не блокирует отклик при слабом совпадении."
    )
    return "\n".join(lines)


def apply_user_tiers_to_score(
    eval_score: int,
    llm_criteria: list[dict],
    settings: dict[str, Any],
    *,
    vacancy_title: str = "",
    vacancy_description: str = "",
    profile_text: str = "",
    rule_score: int = 0,
) -> tuple[int, list[str]]:
    """Корректирует score по tier. Мягкие штрафы, сопоставление по id + эвристики."""
    s = normalize_match_settings(settings)
    gaps: list[str] = []
    penalties = 0

    id_aliases = {
        "role": [
            "role", "роль", "pm", "po", "product owner", "product manager", "продакт",
            "product ownership", "владелец продукта", "менеджер продукта", "cpo",
            "директор по продукту", "chief product",
        ],
        "ai_llm": [
            "ai", "llm", "genai", "rag", "agent", "нейросет", "искусствен", "ai_llm",
            "machine learning", "ml product", "gen ai",
        ],
        "experience": [
            "experience", "опыт", "years", "лет", "level", "уровень", "senior", "middle",
            "стаж", "год",
        ],
        "b2b": ["b2b", "enterprise", "корпорат", "saas", "b2b saas"],
        "delivery": [
            "delivery", "discovery", "backlog", "внедрен", "scrum", "agile", "delivery",
            "roadmap", "бэклог",
        ],
    }

    blob = f"{vacancy_title}\n{vacancy_description}".lower()
    prof_low = profile_text.lower()

    def infer_match(cid: str) -> float:
        if cid == "role":
            if any(x in blob for x in ("product manager", "product owner", "менеджер продукта", "владелец продукта", "cpo", "продакт")):
                return 0.9
        if cid == "ai_llm":
            if any(x in blob for x in ("ai", "llm", "genai", "rag", "gpt", "нейросет", "агент")):
                return 0.75
            if any(x in prof_low for x in ("llm", "genai", "rag", "gpt", "ai product")):
                return 0.7
        if cid == "experience":
            if any(x in blob for x in ("senior", "lead", "5 лет", "3 года", "3+")):
                return 0.85
            if "middle" in blob or "3" in prof_low:
                return 0.7
        if cid == "b2b":
            if "b2b" in blob or "enterprise" in blob or "saas" in blob:
                return 0.75
        if cid == "delivery":
            if any(x in blob for x in ("discovery", "backlog", "scrum", "agile", "roadmap", "бэклог", "delivery")):
                return 0.7
        if rule_score >= 70:
            return 0.65
        if rule_score >= 50:
            return 0.5
        return 0.0

    for item in CRITERIA_CATALOG:
        cid = item["id"]
        tier_key = s["criteria"][cid]
        tier = TIER_LEVELS[tier_key]
        min_ov = float(tier["min_overlap"])

        best_match = 0.0
        for c in llm_criteria:
            name = str(c.get("name", "")).lower()
            cid_name = name.replace(" ", "_").replace("/", "_")
            if cid_name == cid or any(alias in name for alias in id_aliases.get(cid, [cid])):
                best_match = max(best_match, float(c.get("match", 0)))

        if best_match <= 0:
            best_match = infer_match(cid)

        if tier_key == "high" and best_match < min_ov:
            gaps.append(item["label"])
            penalties += 8
        elif tier_key == "medium" and best_match < min_ov:
            penalties += 3

    penalties = min(penalties, 20)
    score = max(0, min(100, eval_score - penalties))
    return score, gaps
