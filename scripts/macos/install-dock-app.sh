#!/usr/bin/env bash
# Собирает Open JobHunt.app и ставит в ~/Applications (+ опционально в Dock).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_NAME="Open JobHunt"
APP_DIR="${HOME}/Applications/${APP_NAME}.app"
ICON_SRC="${ROOT}/assets/open-jobhunt-icon.png"
JOBHUNT_BIN="${ROOT}/.venv/bin/jobhunt"

if [[ ! -x "${JOBHUNT_BIN}" ]]; then
  echo "Нет ${JOBHUNT_BIN}. Сначала: cd \"${ROOT}\" && python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 1
fi

if [[ ! -f "${ICON_SRC}" ]]; then
  echo "Нет иконки: ${ICON_SRC}"
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

# --- iconset → icns ---
ICONSET="${TMP}/AppIcon.iconset"
mkdir -p "${ICONSET}"
BASE="${TMP}/icon.png"
sips -z 1024 1024 "${ICON_SRC}" --out "${BASE}" >/dev/null
for size in 16 32 64 128 256 512; do
  sips -z "${size}" "${size}" "${BASE}" --out "${ICONSET}/icon_${size}x${size}.png" >/dev/null
  sips -z "$((size * 2))" "$((size * 2))" "${BASE}" --out "${ICONSET}/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "${ICONSET}" -o "${TMP}/AppIcon.icns"

# --- .app layout ---
rm -rf "${APP_DIR}"
mkdir -p "${APP_DIR}/Contents/MacOS" "${APP_DIR}/Contents/Resources"
cp "${TMP}/AppIcon.icns" "${APP_DIR}/Contents/Resources/AppIcon.icns"

cat > "${APP_DIR}/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>ru</string>
  <key>CFBundleDisplayName</key>
  <string>${APP_NAME}</string>
  <key>CFBundleExecutable</key>
  <string>open-jobhunt</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleIdentifier</key>
  <string>local.openjobhunt.app</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSUIElement</key>
  <false/>
</dict>
</plist>
EOF

cat > "${APP_DIR}/Contents/MacOS/open-jobhunt" <<EOF
#!/bin/bash
# Launcher for Open JobHunt (local install).
set -euo pipefail
ROOT="${ROOT}"
BIN="\${ROOT}/.venv/bin/jobhunt"
LOG_DIR="\${HOME}/.jobhunt"
mkdir -p "\${LOG_DIR}"
LOG="\${LOG_DIR}/ui-launch.log"

if [[ ! -x "\${BIN}" ]]; then
  osascript -e 'display dialog "Не найден .venv/bin/jobhunt.\\nОткройте проект и выполните:\\n  pip install -e .\\n  jobhunt setup" buttons {"OK"} default button 1 with title "Open JobHunt"'
  exit 1
fi

# Уже запущено — просто вывести окно вперёд (порт отвечает).
if curl -sf -o /dev/null --max-time 1 "http://127.0.0.1:8787/" 2>/dev/null; then
  osascript -e 'tell application "System Events" to set frontmost of first process whose name contains "Chrome for Testing" or name contains "Chromium" to true' 2>/dev/null || true
  exit 0
fi

cd "\${ROOT}"
echo "===== \$(date) =====" >>"\${LOG}"
exec "\${BIN}" ui >>"\${LOG}" 2>&1
EOF
chmod +x "${APP_DIR}/Contents/MacOS/open-jobhunt"

# Сбросить кэш Launch Services / иконки
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "${APP_DIR}" 2>/dev/null || true

# Добавить в Dock (file:// URL, type 15)
python3 - <<'PY'
import os, pathlib, plistlib, subprocess, urllib.parse

app = pathlib.Path(os.path.expanduser("~/Applications/Open JobHunt.app")).resolve()
url = app.as_uri() + "/"
dock_plist = pathlib.Path.home() / "Library/Preferences/com.apple.dock.plist"
data = plistlib.loads(subprocess.check_output(["defaults", "export", "com.apple.dock", "-"]))
apps = data.get("persistent-apps") or []
already = any(
    (item.get("tile-data") or {}).get("file-data", {}).get("_CFURLString", "").rstrip("/").endswith("Open Job Hunt.app")
    or "Open%20Job%20Hunt.app" in (item.get("tile-data") or {}).get("file-data", {}).get("_CFURLString", "")
    for item in apps
    if isinstance(item, dict)
)
if not already:
    apps.append({
        "tile-type": "file-tile",
        "tile-data": {
            "file-label": "Open JobHunt",
            "file-data": {
                "_CFURLString": url,
                "_CFURLStringType": 15,
            },
        },
    })
    data["persistent-apps"] = apps
    tmp = dock_plist.with_suffix(".plist.tmp")
    with tmp.open("wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)
    subprocess.run(["defaults", "import", "com.apple.dock", str(tmp)], check=True)
    tmp.unlink(missing_ok=True)
    subprocess.run(["killall", "Dock"], check=False)
    print("Добавлено в Dock.")
else:
    print("Уже есть в Dock.")
PY

echo "Готово: ${APP_DIR}"
echo "Запуск: open \"${APP_DIR}\""
