"""Кроссплатформенные утилиты (macOS, Linux, Windows)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Символы в логах, которые ломают cp1251 (консоль Windows по умолчанию).
_CONSOLE_ASCII_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("\u2192", "->"),
    ("\u2190", "<-"),
    ("\u2265", ">="),
    ("\u2264", "<="),
    ("\u2026", "..."),
    ("\u2014", "-"),
    ("\u2013", "-"),
    ("\u00ab", '"'),
    ("\u00bb", '"'),
    ("\u26a0", "(!)"),
)


def configure_stdio_utf8() -> None:
    """По возможности переключить stdout/stderr на UTF-8 (Windows)."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def console_safe_text(text: str) -> str:
    """Запасной вариант для legacy-консоли Windows."""
    out = text
    for src, dst in _CONSOLE_ASCII_REPLACEMENTS:
        out = out.replace(src, dst)
    try:
        out.encode(sys.stdout.encoding or "utf-8")
        return out
    except (UnicodeEncodeError, LookupError):
        return out.encode("ascii", errors="replace").decode("ascii")


def apply_windows_utf8_env(env: dict[str, str]) -> dict[str, str]:
    if sys.platform == "win32":
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def downloads_dir() -> Path:
    """Папка «Загрузки» пользователя."""
    home = Path.home()
    if sys.platform == "win32":
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            return Path(userprofile) / "Downloads"
    xdg = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg:
        return Path(xdg).expanduser()
    return home / "Downloads"


def open_file(path: Path | str) -> None:
    """Открыть файл системным приложением по умолчанию."""
    p = Path(path)
    if not p.exists():
        return
    target = str(p.resolve())
    if sys.platform == "darwin":
        subprocess.run(["open", target], check=False)
    elif sys.platform.startswith("linux"):
        subprocess.run(["xdg-open", target], check=False)
    elif sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]


def kill_listeners_on_port(host: str, port: int) -> None:
    """Завершить процесс, слушающий порт (для перезапуска jobhunt ui)."""
    if sys.platform == "win32":
        _kill_windows_port(port)
    else:
        _kill_unix_port(host, port)


def _kill_unix_port(host: str, port: int) -> None:
    # lsof есть на macOS; на Linux часто тоже (пакет lsof).
    for cmd in (
        ["lsof", "-ti", f"tcp:{port}"],
        ["lsof", "-ti", f":{port}"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        for pid in r.stdout.split():
            if pid.isdigit():
                subprocess.run(["kill", "-9", pid], check=False)
        if r.stdout.strip():
            return
    # fallback: fuser (Linux)
    if sys.platform.startswith("linux"):
        subprocess.run(["fuser", "-k", f"{port}/tcp"], check=False, capture_output=True)


def _kill_windows_port(port: int) -> None:
    try:
        r = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return
    needle = f":{port}"
    for line in r.stdout.splitlines():
        if "LISTENING" not in line.upper() or needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        pid = parts[-1]
        if pid.isdigit() and int(pid) > 0:
            subprocess.run(["taskkill", "/F", "/PID", pid], check=False, capture_output=True)
