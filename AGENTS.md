# AGENTS.md — Open JobHunt

Инструкции для AI-агентов (Cursor, Claude Code, Copilot), помогающих пользователю с этим репозиторием.

> **Главный документ — [`GUIDE.md`](GUIDE.md).** Перед ответом пользователю сверяйтесь с ним.

## Установка с нуля (пользователь дал ссылку на GitHub)

**Цель:** после установки у пользователя та же программа, что и у автора — `jobhunt ui` на `127.0.0.1:8787`.

Выполни **сам** (пользователь не обязан вводить команды вручную):

```bash
git clone https://github.com/yrakushin/open-jobhunt.git
cd open-jobhunt
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e .
jobhunt init    # config.yaml + context/*.md + skills/* из *.example
jobhunt setup   # Playwright Chromium + ollama pull
jobhunt doctor  # всё зелёное кроме browser profile (появится после login)
jobhunt ui      # или jobhunt ui --browser на Windows при проблемах с Chromium
```

**Один скрипт:** `scripts/install.sh` (macOS/Linux) или `scripts/install.ps1` (Windows).

**После установки попроси пользователя:**
1. Войти на hh.ru во вкладке браузера (кнопка «Войти на hh.ru»).
2. Заполнить `context/resume-profile.md` своим опытом (или загрузить .md/.pdf в UI).
3. Указать в `config.yaml` → `profile.resume_title` **точное** название резюме на hh.ru.
4. Вставить ссылку «Подходящие вакансии» с hh.ru в панель.
5. Запустить **Тест** (`dry-run`), затем **Запустить отклики**.

**Не коммить** `config.yaml`, `context/resume-profile.md`, отчёты — они в `.gitignore`.

## Что это

Open JobHunt — локальный CLI/UI для автопоиска и откликов на hh.ru:
- Playwright (браузер + сессия в отдельном профиле);
- Ollama (Qwen) для оценки match и писем (по умолчанию локально; опционально OpenAI-совместимый API);
- веб-панель `jobhunt ui` на `127.0.0.1:8787`;
- Excel-отчёты.

**Не облако, не SaaS.** Данные пользователя локально; в облако уходят только при явном
`provider: openai` / `fallback_to_api: true` (см. `GUIDE.md` §10).

## Команды CLI

| Команда | Назначение |
|---------|------------|
| `jobhunt init` | Создать `config.yaml` и персональные файлы из `*.example.md` |
| `jobhunt setup` | `playwright install chromium` + `ollama pull <model>` |
| `jobhunt login` | Один раз войти на hh.ru в открывшемся окне |
| `jobhunt run` | Полный цикл (поиск → оценка → письмо → отклик → отчёт) |
| `jobhunt run --dry-run` | Без отправки откликов (отчёт пишется) |
| `jobhunt run --limit N` | Лимит откликов за запуск |
| `jobhunt doctor` | Диагностика окружения |
| `jobhunt ui` | Графическая панель (окно Chromium: Панель + hh.ru) |
| `jobhunt ui --browser` | Панель в системном браузере (без окна Chromium) |

## Файлы пользователя (в .gitignore, не коммитить)

- `config.yaml` — лимиты, модель, фильтры, зарплата, резюме.
- `context/resume-profile.md` — профиль кандидата (копируется из `context/resume-profile.example.md`).
- `context/cover-letter.md` — шаблон письма (из `.example.md`).
- `skills/stop-slop-writing.md` — стиль писем (из `.example.md`).
- `reports/*.xlsx` — отчёты прогона.

В репозитории лежат только `*.example.md` (обезличенные) и `config.example.yaml`.

## Типовые задачи пользователя

1. **«Установи и настрой»** → раздел «Установка с нуля» выше или `GUIDE.md` §3–4.
   `jobhunt init` создаёт все файлы из `*.example.md` автоматически.
2. **«Не авторизован / капча»** → `GUIDE.md` §12. Вход только в окне `jobhunt ui` (профиль
   `~/.jobhunt/browser-profile`), не в Chrome. Капчу — вручную, бот не обходит.
3. **«0 откликов / 0 match»** → `GUIDE.md` §12. Проверить `resume_title`, `resume_search_url`,
   `llm_min_percent`, режим «По роли»/«Свободно», заполненность профиля. Dry-run + лист «Пропущено».
4. **«Хочу откликаться на другие роли»** → поменять `profile.resume_title`, `search.queries`,
   блок `blocker` в `context/resume-profile.md`. Режим «По роли» завязан на `resume_title`.
5. **«Где отчёт / что в нём»** → `reports/Otclicki_hh_User_*.xlsx`, листы Отклики / Требует действия /
   Пропущено. `GUIDE.md` §11.
6. **«Настрой LLM / Ollama / внешний API»** → `GUIDE.md` §10. По умолчанию Ollama локально;
   внешний API — через env `JOBHUNT_API_KEY`, предупредить про приватность.
7. **«Какие поля в config.yaml»** → `GUIDE.md` §7 (все поля с типами/умолчаниями).

## Правила для агента

1. **Не меняй логику фильтров / пороги / роль-фильтр без явного запроса пользователя.** Это влияет
   на то, куда уйдут реальные отклики. Правки в `jobhunt/core/filters.py`, `role_filter.py`,
   `match_config.py`, `runner.py` — только по запросу.
2. **Не коммить персональные данные.** `config.yaml`, `context/resume-profile.md`,
   `context/cover-letter.md`, `skills/stop-slop-writing.md`, `reports/*.xlsx`,
   `scripts/batch_apply_data.json` — в `.gitignore`. Реальные данные пользователя не в репо.
3. **Секреты только через env** (`JOBHUNT_API_KEY`), не в файлах и не в git.
4. **Не выставляй сервер в сеть.** `jobhunt ui` — только `127.0.0.1`. Не меняй `host` на `0.0.0.0`
   и не включай CORS без явного запроса и предупреждения о рисках.
5. **Не обходи капчу/антибот hh.ru** и не нарушай лимиты площадки. Паузы и лимиты — в `runner.py`.
6. **Новые job-сайты — через `jobhunt/adapters/`** (реализовать search/parse/apply), зарегистрировать
   в `search.site`. v0.2 = только hh.ru.
7. **После изменений — `jobhunt doctor`** и (если правка в коде прогона) `jobhunt run --dry-run`
   для проверки без отправки.
8. **Ссылайся на `GUIDE.md`**, а не пересказывай своими словами — там актуальные детали.

## Карта кода (для навигации)

| Файл | Назначение |
|------|------------|
| `jobhunt/cli.py` | команды CLI (init/setup/login/run/doctor/ui) |
| `jobhunt/runner.py` | цикл прогона (поиск → оценка → отклик → отчёт) |
| `jobhunt/config.py` | загрузка config + updaters (UI пишет через них) |
| `jobhunt/core/filters.py` | pre-фильтры: стоп-роли, blacklist, зарплата, МФО, ИП, дубли |
| `jobhunt/core/role_filter.py` | режимы «По роли»/«Свободно», hard-reject, обязанности |
| `jobhunt/core/match_config.py` | 3-уровневая шкала must/preferred/bonus |
| `jobhunt/core/matching.py` | weighted score, парсинг JSON LLM, детект тестов |
| `jobhunt/llm/client.py` | Ollama / OpenAI-совместимый клиент |
| `jobhunt/browser/session.py` | Playwright-сессия hh.ru, поиск, отклик |
| `jobhunt/browser/parse.py` | парсинг HTML вакансий |
| `jobhunt/core/report.py` | Excel-отчёт (листы Отклики/Требует действия/Пропущено) |
| `jobhunt/core/letter.py` | подстановка плейсхолдеров в шаблон письма |
| `jobhunt/core/resume_io.py` | извлечение текста из .md/.txt/.pdf, угадывание title |
| `jobhunt/web/server.py` | FastAPI-панель, все эндпоинты |
| `jobhunt/web/launcher.py` | запуск сервера + окна Chromium |
| `jobhunt/platform.py` | кроссплатформенность: Downloads, open_file, kill port |
| `jobhunt/web/browser_ui.py` | мост Playwright (вкладки Панель + hh.ru) |
| `jobhunt/web/static/` | index.html, app.js, styles.css — клиент панели |
| `prompts/match-scoring.md` | системный промпт LLM-оценки (используется кодом) |
| `context/match-methodology.md` | методология match (первые 1200 символов → в промпт) |
