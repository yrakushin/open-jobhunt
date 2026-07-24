from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from jobhunt.core.matching import MatchEvaluation, compute_weighted_score, load_match_prompt, parse_match_response, valid_match_payload


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen2.5:7b"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def available(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            if r.status_code != 200:
                return False
            names = {m["name"].split(":")[0] for m in r.json().get("models", [])}
            base = self.model.split(":")[0]
            return base in names or self.model in {m["name"] for m in r.json().get("models", [])}
        except httpx.HTTPError:
            return False

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        num_predict: int | None = None,
        timeout: float = 120.0,
        json_mode: bool = False,
    ) -> str:
        options: dict[str, Any] = {"temperature": temperature}
        if num_predict is not None:
            options["num_predict"] = num_predict
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"
        r = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        num_predict: int | None = None,
        timeout: float = 120.0,
        json_mode: bool = False,
    ) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = httpx.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=180.0)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


def build_llm(cfg: dict[str, Any]):
    llm_cfg = cfg.get("llm", {})
    provider = llm_cfg.get("provider", "ollama")
    if provider == "none":
        return None
    if provider == "openai":
        oc = llm_cfg.get("openai", {})
        key = os.getenv(oc.get("api_key_env", "JOBHUNT_API_KEY"), "")
        if not key:
            return None
        return OpenAICompatibleClient(oc.get("base_url", "https://api.openai.com/v1"), key, oc.get("model", "gpt-4o-mini"))
    oc = llm_cfg.get("ollama", {})
    client = OllamaClient(oc.get("base_url", "http://127.0.0.1:11434"), oc.get("model", "qwen2.5:7b"))
    if client.available():
        return client
    if llm_cfg.get("fallback_to_api"):
        oac = llm_cfg.get("openai", {})
        key = os.getenv(oac.get("api_key_env", "JOBHUNT_API_KEY"), "")
        if key:
            return OpenAICompatibleClient(
                oac.get("base_url", "https://api.openai.com/v1"), key, oac.get("model", "gpt-4o-mini")
            )
    return None


def score_match_llm(
    llm,
    profile_md: str,
    vacancy_title: str,
    company: str,
    description: str,
    methodology_path: str | None = None,
    match_settings: dict | None = None,
    vacancy_preferences: str = "",
    apply_threshold: int | None = None,
) -> MatchEvaluation:
    from pathlib import Path

    from jobhunt.core.filters import rule_match_score
    from jobhunt.core.match_config import apply_user_tiers_to_score, build_match_instructions

    rule_score, rule_reason = rule_match_score(vacancy_title, description, profile_md)

    def rule_fallback(note: str = "") -> MatchEvaluation:
        reason = f"rule {rule_score}% ({rule_reason})"
        if note:
            reason = f"{reason}; {note}"
        return MatchEvaluation(score=rule_score, reason=reason)

    root = Path(__file__).resolve().parents[2]
    system = load_match_prompt(root)
    system += (
        "\n\nКРИТИЧНО: ответ — только один JSON-объект, без markdown, без нумерованных списков, "
        'без текста до/после. Поле reason — короткая строка, не анализ.'
    )
    if methodology_path:
        meth = Path(methodology_path)
        if meth.is_file():
            system += (
                "\n\n## Методология (кратко, только для логики оценки)\n"
                + meth.read_text(encoding="utf-8")[:1200]
                + "\n\nНе меняй формат ответа: только JSON с массивом criteria, как в инструкции выше."
            )
    if match_settings:
        system += "\n\n## Настройки пользователя\n" + build_match_instructions(
            match_settings, apply_threshold=apply_threshold
        )
        system += (
            "\n\nВ criteria[].name используй id из списка: role, ai_llm, experience, b2b, delivery."
        )

    user = (
        f"ПРОФИЛЬ КАНДИДАТА:\n{profile_md[:3500]}\n\n"
        f"ВАКАНСИЯ: {vacancy_title} — {company}\n"
        f"ОПИСАНИЕ/ТРЕБОВАНИЯ:\n{description[:4000]}"
    )
    prefs = (vacancy_preferences or "").strip()
    if prefs:
        user += f"\n\nПОЖЕЛАНИЯ КАНДИДАТА (учитывай при match, blocker если явно не совпадает):\n{prefs[:1500]}"

    json_mode = True
    raw = llm.chat(system, user, num_predict=600, timeout=120.0, temperature=0.1, json_mode=json_mode)
    data = parse_match_response(raw)
    if (not data or not valid_match_payload(data)) and json_mode:
        raw = llm.chat(
            system + "\n\nВерни ТОЛЬКО JSON с полем criteria (массив объектов). Никакого markdown.",
            user,
            num_predict=600,
            timeout=120.0,
            temperature=0.0,
            json_mode=True,
        )
        data = parse_match_response(raw)

    if not data or not valid_match_payload(data):
        return rule_fallback("LLM не вернул criteria[]")

    result = compute_weighted_score(data)
    if result.score <= 10 and rule_score >= 40:
        return rule_fallback("LLM score подозрительно низкий")

    if match_settings:
        adjusted, tier_gaps = apply_user_tiers_to_score(
            result.score,
            data.get("criteria") or [],
            match_settings,
            vacancy_title=vacancy_title,
            vacancy_description=description,
            profile_text=profile_md,
            rule_score=rule_score,
        )
        result.score = adjusted
        for g in tier_gaps:
            if g not in result.must_gaps:
                result.must_gaps.append(g)
        if tier_gaps:
            result.reason = (result.reason + f"; tier: {', '.join(tier_gaps[:2])}").strip()
    return result


def generate_letter_llm(
    llm,
    profile_md: str,
    style_md: str,
    vacancy_title: str,
    company: str,
    description: str,
    salary_expectation: str,
    signature: str,
) -> str:
    system = (
        "Напиши сопроводительное письмо на русском. Следуй стилю из инструкции. "
        "70-90 слов. Только текст письма, без пояснений."
    )
    user = (
        f"Стиль:\n{style_md}\n\n"
        f"Профиль:\n{profile_md[:5000]}\n\n"
        f"Вакансия: {vacancy_title}\nКомпания: {company}\n"
        f"Описание:\n{description[:4000]}\n\n"
        f"Если спрашивают зарплату: {salary_expectation}\n"
        f"Подпись: {signature}"
    )
    return llm.chat(system, user, temperature=0.5)


def answer_employer_questions_llm(
    llm,
    profile_md: str,
    style_md: str,
    questions: list[dict[str, Any]],
    *,
    vacancy_title: str = "",
    company: str = "",
    salary_expectation: str = "",
) -> dict[str, str]:
    """Ответы на вопросы формы отклика. Возвращает id → текст ответа (для choice — точная метка)."""
    from jobhunt.core.employer_qa import fallback_answers, parse_answers_json, sanitize_human_answer

    if not questions:
        return {}

    compact = []
    for q in questions:
        row: dict[str, Any] = {
            "id": str(q.get("id")),
            "type": q.get("type") or "text",
            "question": (q.get("question") or "")[:400],
        }
        if q.get("type") == "choice":
            row["options"] = [
                str(o.get("label") or "").strip()
                for o in (q.get("options") or [])
                if str(o.get("label") or "").strip()
            ]
        compact.append(row)

    system = (
        "Ты кандидат на вакансию. Отвечаешь на вопросы работодателя от первого лица. "
        "Пиши по-русски, коротко и по-человечески, как в переписке. "
        "Только факты из профиля кандидата. Не выдумывай опыт, компании, цифры, сертификаты. "
        "Если в профиле нет факта — честно и коротко: готов обсудить на собеседовании. "
        "Для type=choice ответ — РОВНО одна строка из options, без кавычек и пояснений. "
        "Для type=text — 1–3 коротких предложения, без списков и markdown. "
        "Запрещено: упоминать ИИ/бота/нейросеть/модель; китайские/японские иероглифы; "
        "английский канцелярит; «как языковая модель». "
        "Пиши только русскими буквами и цифрами (латиница только в названиях вроде SQL, CRM). "
        "Зарплату бери только из поля salary_expectation, если вопрос про деньги. "
        "Ответ строго JSON: {\"answers\":[{\"id\":\"0\",\"answer\":\"...\"}]}."
    )
    user = (
        f"Стиль (тон, без канцелярита):\n{(style_md or '')[:2500]}\n\n"
        f"Профиль кандидата:\n{(profile_md or '')[:6000]}\n\n"
        f"Вакансия: {vacancy_title}\nКомпания: {company}\n"
        f"salary_expectation: {salary_expectation or 'не указано'}\n\n"
        f"Вопросы:\n{json.dumps(compact, ensure_ascii=False)}"
    )
    try:
        raw = llm.chat(
            system,
            user,
            temperature=0.25,
            num_predict=900,
            timeout=90.0,
            json_mode=True,
        )
        parsed = parse_answers_json(raw, questions)
        if len(parsed) >= max(1, len(questions) // 2):
            return {k: sanitize_human_answer(v) for k, v in parsed.items()}
    except Exception:
        pass
    return fallback_answers(questions, salary=salary_expectation, letter="")
