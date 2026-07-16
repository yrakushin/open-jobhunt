#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/jobhunt init
.venv/bin/jobhunt setup
if command -v uipro >/dev/null; then
  uipro init --ai cursor --global 2>/dev/null || true
fi
echo ""
echo "Готово. Дальше:"
echo "  source .venv/bin/activate"
echo "  jobhunt ui"
echo ""
echo "В панели: войти на hh.ru → загрузить резюме → вставить ссылку поиска → Тест → Запуск"
echo "Подробно: GUIDE.md и AGENTS.md"
