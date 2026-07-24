"""Ответы на вопросы работодателя: санитайз, фолбэк, формат для отчёта."""

from __future__ import annotations

import json
import re
from typing import Any

# CJK + полный диапазон иероглифов (Qwen иногда сыпет)
_CJK_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]+"
)
_FULLWIDTH_PUNCT_RE = re.compile(r"[\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65]+")
_AI_LEAK_RE = re.compile(
    r"(?i)\b("
    r"языков(ая|ой)\s+модель|нейросет|chatgpt|gpt-?\d|как\s+ии|как\s+ai|"
    r"я\s+ии|я\s+бот|искусственн\w+\s+интеллект|ollama|qwen|llm|"
    r"as an ai|language model|i'?m an ai"
    r")\b"
)
_MD_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```", re.I)


def sanitize_human_answer(text: str, max_len: int = 480) -> str:
    """Убирает китайский, AI-утечки, markdown. Оставляет человеческий русский."""
    s = (text or "").strip()
    s = _MD_FENCE_RE.sub("", s)
    s = _CJK_RE.sub(" ", s)
    s = _FULLWIDTH_PUNCT_RE.sub(" ", s)
    s = _AI_LEAK_RE.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    s = re.sub(r"[，。、；：！？·…]{1,}", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,.;…")
    # убрать кавычки-обёртки от модели
    if len(s) >= 2 and s[0] in "«\"'" and s[-1] in "»\"'":
        s = s[1:-1].strip()
    # после вырезания CJK ответ иногда превращается в мусор
    letters = sum(1 for c in s if c.isalpha())
    if letters < 8:
        return ""
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def format_qa_for_report(qa: list[dict[str, str]]) -> str:
    if not qa:
        return ""
    lines = []
    for i, row in enumerate(qa, 1):
        q = (row.get("question") or "").strip()
        a = (row.get("answer") or "").strip()
        lines.append(f"{i}. В: {q}\n   О: {a}")
    return "\n".join(lines)


def parse_answers_json(raw: str, questions: list[dict[str, Any]]) -> dict[str, str]:
    """id → answer text. Пустой dict если не распарсили."""
    text = _MD_FENCE_RE.sub("", (raw or "").strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    items = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    by_id = {str(q.get("id")): q for q in questions}
    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id", "")).strip()
        ans = sanitize_human_answer(str(item.get("answer") or ""))
        if not qid or not ans or qid not in by_id:
            continue
        q = by_id[qid]
        if q.get("type") == "choice":
            labels = [str(o.get("label") or "").strip() for o in (q.get("options") or [])]
            match = _match_option(ans, labels)
            if match:
                out[qid] = match
            elif labels:
                out[qid] = pick_choice_label(labels, q.get("question") or "")
        else:
            out[qid] = ans
    return out


def _match_option(answer: str, labels: list[str]) -> str | None:
    a = answer.strip().lower()
    for lab in labels:
        if lab.lower() == a:
            return lab
    for lab in labels:
        if a and (a in lab.lower() or lab.lower() in a):
            return lab
    return None


def pick_choice_label(labels: list[str], question: str = "") -> str:
    if not labels:
        return ""
    q = (question or "").lower()
    # не галлюцинируем «Да» на всё подряд
    neg = any(x in q for x in ("не готов", "отказ", "против", "запрещ"))
    for lab in labels:
        low = lab.lower()
        if not neg and low in ("да", "yes", "готов", "согласен"):
            return lab
        if neg and low in ("нет", "no"):
            return lab
    for lab in labels:
        if "свой" not in lab.lower():
            return lab
    return labels[0]


def fallback_answers(
    questions: list[dict[str, Any]],
    *,
    salary: str = "",
    letter: str = "",
) -> dict[str, str]:
    """Без LLM: зарплата из профиля, choice — безопасный вариант, текст — коротко из письма."""
    out: dict[str, str] = {}
    letter_bit = sanitize_human_answer(
        re.sub(r"\s+", " ", (letter or "").strip())[:280],
        max_len=280,
    )
    for q in questions:
        qid = str(q.get("id"))
        text = (q.get("question") or "").lower()
        if q.get("type") == "choice":
            labels = [str(o.get("label") or "").strip() for o in (q.get("options") or []) if o.get("label")]
            if labels:
                out[qid] = pick_choice_label(labels, q.get("question") or "")
            continue
        if any(x in text for x in ("зарплат", "вознагражд", "компенсац", "ожидан", "доход")):
            out[qid] = sanitize_human_answer(salary or "обсудим на собеседовании")
        elif letter_bit:
            out[qid] = letter_bit
        else:
            out[qid] = "есть релевантный опыт, готов подробнее на собеседовании"
    return out
