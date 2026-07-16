# Open JobHunt

Локальный open-source ассистент откликов на [hh.ru](https://hh.ru). Бесплатно, данные и сессия остаются на вашем компьютере.

**Зачем:** компании автоматизируют ATS и массовые отказы — кандидат тратит часы на рутину. Этот инструмент ускоряет поиск, фильтрацию и отклики с осмысленными письмами.

> **Полная инструкция — в [`GUIDE.md`](GUIDE.md).** Установка, CLI, каждый блок UI, все поля `config.yaml`,
> LLM/приватность, отчёты, troubleshooting, FAQ для AI-агента.

## Возможности v0.2

- **Веб-панель** `jobhunt ui` — одно окно Chromium с вкладками «Панель» и «hh.ru» на `127.0.0.1:8787`
- Поиск вакансий: подбор по резюме (ссылка «Подходящие вакансии») или keyword-запросы
- **Режимы соответствия**: «По роли» (`role_lock`) / «Свободно» (`off`)
- Rule-based + LLM (Ollama / Qwen) фильтр match с порогом `llm_min_percent`
- Сопроводительные письма: шаблон с плейсхолдерами или генерация LLM по вашему стилю
- Отклики через Playwright (сохранённая сессия, паузы между откликами)
- Excel-отчёт: «Отклики» / «Требует действия» / «Пропущено»
- Опциональный внешний LLM (OpenAI-совместимый API) через env-ключ
- **Свежесть вакансий**: 24 ч / 3 дня / 7 дней / всё время (в панели и `vacancy_age_mode`)

## Быстрый старт

**macOS / Linux:**

```bash
git clone https://github.com/yrakushin/open-jobhunt.git
cd open-jobhunt
pip install -e .

jobhunt init      # создаёт config.yaml
jobhunt setup     # ставит Chromium + тянет модель Ollama
jobhunt ui        # графическая панель (рекомендуется)
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/yrakushin/open-jobhunt.git
cd open-jobhunt
.\scripts\install.ps1
.\.venv\Scripts\Activate.ps1
jobhunt ui
```

> Замените `YOUR_USERNAME` на свой GitHub-форк. Если Chromium не стартует: `jobhunt ui --browser`.

### Подготовьте персональные файлы (один раз)

В репозитории — только примеры `*.example.md`. Реальные файлы в `.gitignore` (не коммитятся):

```bash
jobhunt init   # config.yaml, пустой context/cover-letter.md и остальное из *.example
# или вручную:
cp context/resume-profile.example.md context/resume-profile.md
cp skills/stop-slop-writing.example.md skills/stop-slop-writing.md
```

Откройте их и заполните своими данными. Подробно — `GUIDE.md` §4, §8, §9.

### Дальше — в панели `jobhunt ui`

1. **Войти на hh.ru** (вкладка hh.ru в окне панели — сессия сохранится)
2. **Загрузить резюме** (карточка «Резюме», `.md`/`.txt`/`.pdf`)
3. **Задать ссылку поиска** («Подходящие вакансии» с hh.ru)
4. **Выбрать режим** «По роли» / «Свободно»
5. **Тест** (dry-run) → **Запустить отклики** (двойной клик для подтверждения)

Или через CLI:

```bash
jobhunt login            # войти на hh.ru один раз
jobhunt run --dry-run    # прогон без отправки откликов
jobhunt run              # полный цикл
jobhunt doctor           # диагностика окружения
```

**Панель:** `jobhunt ui` → http://127.0.0.1:8787

## LLM

По умолчанию: **Ollama + qwen2.5:7b** (локально, бесплатно).

```yaml
llm:
  provider: ollama
  ollama:
    model: qwen2.5:7b
```

Внешний API (свой ключ; **данные уходят на внешний API** — см. `GUIDE.md` §10):

```yaml
llm:
  provider: ollama        # или openai
  fallback_to_api: true
  openai:
    api_key_env: JOBHUNT_API_KEY
    model: gpt-4o-mini
```

```bash
export JOBHUNT_API_KEY=sk-...
```

## Использование с Cursor / Claude / Copilot

Склонируйте репозиторий и укажите агенту:

> Прочитай `GUIDE.md` и `AGENTS.md` и помоги настроить Open JobHunt.

`AGENTS.md` содержит правила для агента и карту кода. `GUIDE.md` §14 — FAQ для типовых вопросов.

## Структура

```
jobhunt/           # Python-пакет (cli, runner, core, llm, browser, web)
config.example.yaml
context/           # *.example.md — примеры профиля/письма/методологии
skills/            # *.example.md — пример стиля писем
prompts/           # match-scoring.md (LLM-промпт) + *.example.md
reports/           # Excel-отчёты (в .gitignore)
GUIDE.md           # полная инструкция
AGENTS.md          # правила для AI-агентов
SETUP.md           # настройка с нуля / troubleshooting
```

## Ограничения

- Капча, тесты, нестандартные формы — в лист «Требует действия» (бот не обходит антибот)
- Соблюдайте лимиты hh.ru и правила площадки (паузы и лимиты уже в коде)
- v0.2: только hh.ru (другие площадки — через `jobhunt/adapters/`)

## Приватность

- Данные (профиль, письма, фильтры, отчёты, сессия) — локально, в `.gitignore`
- Сервер только на `127.0.0.1` (loopback), CORS отключён, телеметрии нет
- Секреты через env (`JOBHUNT_API_KEY`), не в git
- В облако данные уходят **только** при `provider: openai` / `fallback_to_api: true`

Подробно — `GUIDE.md` §10, §13.

## Лицензия

Личное использование для **физических лиц** (поиск работы на себя). Организациям — только с отдельного согласия автора. Коммерческая монетизация и платные услуги на базе проекта — **только правообладателю** (Ярослав Ракушин). Полный текст — [`LICENSE`](LICENSE).
