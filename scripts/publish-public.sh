#!/usr/bin/env bash
# Публикация одного чистого коммита в github.com/yrakushin/open-jobhunt (без dev-истории и PII).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WT="${ROOT}/../open-jobhunt-publish"
REMOTE="${1:-public}"
VERSION="$(python3 -c "import tomllib; print(tomllib.load(open('${ROOT}/pyproject.toml','rb'))['project']['version'])")"

PUBLIC_FILES=(
  .gitignore
  AGENTS.md
  GUIDE.md
  LICENSE
  README.md
  SETUP.md
  assets/open-jobhunt-icon.png
  config.example.yaml
  context/cover-letter.example.md
  context/match-methodology.md
  context/resume-profile.example.md
  docs/MCP-MAGIC.md
  jobhunt/__init__.py
  jobhunt/browser/__init__.py
  jobhunt/browser/cookies.py
  jobhunt/browser/hh_urls.py
  jobhunt/browser/parse.py
  jobhunt/browser/session.py
  jobhunt/cli.py
  jobhunt/config.py
  jobhunt/core/employer_qa.py
  jobhunt/core/filters.py
  jobhunt/core/letter.py
  jobhunt/core/match_config.py
  jobhunt/core/matching.py
  jobhunt/core/report.py
  jobhunt/core/resume_io.py
  jobhunt/core/role_filter.py
  jobhunt/core/vacancy_age.py
  jobhunt/llm/__init__.py
  jobhunt/llm/client.py
  jobhunt/models.py
  jobhunt/platform.py
  jobhunt/runner.py
  jobhunt/web/__init__.py
  jobhunt/web/browser_ui.py
  jobhunt/web/launcher.py
  jobhunt/web/server.py
  jobhunt/web/static/app.js
  jobhunt/web/static/index.html
  jobhunt/web/static/styles.css
  prompts/hh-autopilot.example.md
  prompts/match-scoring.md
  pyproject.toml
  reports/.gitkeep
  scripts/generate_report.py
  scripts/install.ps1
  scripts/install.sh
  scripts/macos/install-dock-app.sh
  scripts/publish-public.sh
  skills/stop-slop-writing.example.md
)

rm -rf "$WT"
mkdir -p "$WT"
cd "$WT"
git init -q
git checkout -q -b main

for f in "${PUBLIC_FILES[@]}"; do
  src="$ROOT/$f"
  if [[ ! -f "$src" ]]; then
    echo "missing: $f" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$f")"
  cp "$src" "$f"
done

# Быстрый sanity: не тащим телефон / resume hash / личный telegram handle
if rg -n --ignore-case \
  -g '!scripts/publish-public.sh' -g '!LICENSE' -g '!README.md' \
  '8415168aff|\+7\s*\([0-9]{3}\)|telegram\s*@rakushin' \
  . >/dev/null 2>&1; then
  echo "PII-like string found in public tree — abort" >&2
  rg -n --ignore-case \
    -g '!scripts/publish-public.sh' -g '!LICENSE' -g '!README.md' \
    '8415168aff|\+7\s*\([0-9]{3}\)|telegram\s*@rakushin' \
    . >&2 || true
  exit 1
fi
# Личные файлы не должны попасть в дерево
for banned in config.yaml context/resume-profile.md context/cover-letter.md skills/stop-slop-writing.md; do
  if [[ -f "$banned" ]]; then
    echo "banned file present: $banned" >&2
    exit 1
  fi
done

git add -A
git commit -q -m "$(cat <<EOF
Open JobHunt v${VERSION} — salary-only, employer Q&A, role filter fixes.

- Mode «Все по зарплате» (CLI --salary-only / UI) — apply without match/role lock
- Employer questions at apply + Q&A column in Excel report
- Vacancy age filter (24h / 3d / 7d / all) wired end-to-end
- Stop-role checks title only; broader PM/PO title patterns (Product-менеджер, AI-продакт)
- macOS Dock app installer (scripts/macos/install-dock-app.sh)
EOF
)"

if git remote get-url "$REMOTE" >/dev/null 2>&1; then
  :
elif [[ "$REMOTE" == "public" ]] && git -C "$ROOT" remote get-url public >/dev/null 2>&1; then
  git remote add origin "$(git -C "$ROOT" remote get-url public)"
else
  git remote add origin "https://github.com/yrakushin/open-jobhunt.git"
fi

# Prefer named remote from parent if caller passed it
if [[ "$REMOTE" != "origin" ]] && git -C "$ROOT" remote get-url "$REMOTE" >/dev/null 2>&1; then
  git remote remove origin 2>/dev/null || true
  git remote add origin "$(git -C "$ROOT" remote get-url "$REMOTE")"
fi

git push -f origin main

TAG="v${VERSION}"
git tag -f "$TAG"
git push -f origin "$TAG"

echo "Published to https://github.com/yrakushin/open-jobhunt @ ${TAG}"
