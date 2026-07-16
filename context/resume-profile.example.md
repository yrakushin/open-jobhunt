# Профиль кандидата — Иван Примеров

> Это **пример** профиля для Open JobHunt. Скопируйте его в `context/resume-profile.md`
> (`cp context/resume-profile.example.md context/resume-profile.md`) и замените на свои данные.
> Реальный профиль в `.gitignore` и не попадает в git.

## Контакты

- Город: Москва
- Переезд: готов / не готов (укажите своё)
- Формат: полная занятость, удалённо / гибрид / офис

> Контакты (телефон, email, telegram) сюда писать **не нужно** — Open JobHunt не использует
> их при отклике. Письмо и подпись настраиваются отдельно (`context/cover-letter.md`,
> `profile.signature` в `config.yaml`).

## Целевая роль

Product Manager / Product Owner

Специализации: Менеджер продукта, Руководитель проектов
Уровень: Middle / Senior (укажите свой)

## Опыт — N лет

### Компания «Пример» (Москва) — месяц год — месяц год
**Product Manager / Product Owner**

Опишите 2–4 ключевых результата с цифрами:
- продукт / фича: метрика «было → стало»
- продукт / фича: метрика «было → стало»

### Компания «Пример 2» (город) — месяц год — месяц год
**Product Manager**

- продукт / фича: метрика «было → стало»

## Навыки

Product Management, Product Ownership, Product Discovery, Backlog, Agile/Scrum,
Stakeholder Management, Business Analysis, REST API, SQL, Jira, Confluence, Miro

(добавьте свои: AI/LLM, RAG, GenAI, enterprise B2B, delivery и т.д.)

## Языки

- Русский — родной
- Английский — B1 / B2 / C1 (укажите свой)

## Образование

Вуз, специальность, год выпуска

## Критерий соответствия (weighted match)

Подробная методология: `context/match-methodology.md`.

Порог отклика в config: `search.llm_min_percent` (по умолчанию 30) — взвешенная
оценка must/preferred/bonus, не плоский процент.

**must** (обязательно):
- product management / product ownership (не чистый project management без продукта)
- уровень не junior / не стажёр
- опыт в диапазоне ваших лет

**preferred** (желательно, не блокирует):
- ваши ключевые домены и технологии (укажите свои: AI/LLM, enterprise B2B, delivery …)

**blocker** — пропуск:
- чистый ML Engineer / Data Scientist / Backend без PM-фокуса
- Junior / стажёр / «без опыта»
- Sales, Marketing Manager, HR, рекрутер, аккаунт-менеджер
- Project Manager без product scope
- Business Analyst без product ownership

## Название резюме на hh.ru

`Product Manager` — точное название резюме на hh.ru. Его бот ищет при авто-поиске
(когда `search.resume_search_url` пуст), и из него же выводит разрешённые роли
в режиме «По роли». Замените на своё.
