# Справка: `context/cover-letter.md`

При `jobhunt init` файл создаётся **пустым**. Заполните текст в UI и нажмите «Сохранить письмо».

## Плейсхолдеры в шаблоне

- `{title}` — название вакансии
- `{company}` — компания
- `{signature}` — подпись из `config.yaml` → `profile.signature`

## Пустой файл

Если `cover-letter.md` пустой, письмо генерирует LLM по профилю и стилю из `skills/stop-slop-writing.md`.
Подробнее — в `GUIDE.md` (разделы про письмо и LLM).
