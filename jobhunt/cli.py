from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.table import Table

from jobhunt.config import load_config
from jobhunt.platform import configure_stdio_utf8
from jobhunt.runner import run_sync

configure_stdio_utf8()
console = Console()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


# Файлы пользователя ← из обезличенных примеров (см. .gitignore).
_INIT_BOOTSTRAP: list[tuple[str, str]] = [
    ("config.example.yaml", "config.yaml"),
    ("context/resume-profile.example.md", "context/resume-profile.md"),
    ("context/cover-letter.example.md", "context/cover-letter.md"),
    ("skills/stop-slop-writing.example.md", "skills/stop-slop-writing.md"),
    ("prompts/hh-autopilot.example.md", "prompts/hh-autopilot.md"),
]


@click.group()
@click.version_option(package_name="open-jobhunt")
def main() -> None:
    """Open JobHunt — локальный ассистент откликов на hh.ru."""


@main.command()
def init() -> None:
    """Создать config.yaml и персональные файлы из *.example (если ещё нет)."""
    root = _project_root()
    created: list[str] = []
    skipped: list[str] = []
    for src_rel, dst_rel in _INIT_BOOTSTRAP:
        src = root / src_rel
        dst = root / dst_rel
        if not src.exists():
            console.print(f"[yellow]Пропуск (нет примера):[/] {src_rel}")
            continue
        if dst.exists():
            skipped.append(dst_rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst_rel == "context/cover-letter.md":
            dst.write_text("", encoding="utf-8")
        else:
            shutil.copy(src, dst)
        created.append(dst_rel)

    if created:
        console.print("[green]Создано:[/]")
        for p in created:
            console.print(f"  • {p}")
    if skipped:
        console.print("[dim]Уже есть (не перезаписывал):[/]")
        for p in skipped:
            console.print(f"  • {p}")
    if not created and not skipped:
        console.print("[yellow]Нечего создавать — проверьте наличие *.example в репозитории[/]")
    console.print(
        "\n[bold]Дальше:[/] отредактируйте [cyan]context/resume-profile.md[/], "
        "затем [bold]jobhunt setup[/] и [bold]jobhunt ui[/]"
    )


@main.command()
def setup() -> None:
    """Установить Playwright и скачать модель Ollama."""
    console.print("[cyan]Установка Playwright Chromium...[/]")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)

    cfg = load_config()
    model = cfg.get("llm", {}).get("ollama", {}).get("model", "qwen2.5:7b")
    if shutil.which("ollama"):
        console.print(f"[cyan]Скачивание модели {model}...[/]")
        subprocess.run(["ollama", "pull", model], check=False)
    else:
        console.print("[yellow]Ollama не найден. Установи: https://ollama.com[/]")
    console.print("[green]Готово.[/] Дальше: [bold]jobhunt login[/]")


@main.command()
def login() -> None:
    """Открыть браузер для входа на hh.ru (один раз)."""
    from jobhunt.browser.session import BrowserSession

    cfg = load_config()
    browser_cfg = cfg["browser"]

    async def _login() -> None:
        session = BrowserSession(
            browser_cfg["profile_dir"],
            headless=False,
            slow_mo_ms=browser_cfg.get("slow_mo_ms", 50),
        )
        await session.start()
        assert session.page
        await session.page.goto("https://hh.ru/account/login", wait_until="domcontentloaded")
        console.print(
            "[bold]Войди на hh.ru в открывшемся окне.[/] "
            "Когда увидишь резюме — закрой окно браузера или нажми Ctrl+C в терминале."
        )
        try:
            while True:
                await asyncio.sleep(2)
                if not session._context or not session._context.pages:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            await session.close()

    asyncio.run(_login())
    console.print("[green]Сессия сохранена.[/] Запуск: [bold]jobhunt run[/]")


@main.command()
@click.option("--dry-run", is_flag=True, help="Без отправки откликов — только отбор и письма")
@click.option("--limit", type=int, default=None, help="Сколько откликов за запуск")
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
def run(config_path: Path | None, dry_run: bool, limit: int | None) -> None:
    """Поиск вакансий, фильтр, отклики, Excel-отчёт."""
    report = run_sync(config_path, dry_run=dry_run, max_applications=limit)
    if report.errors:
        for err in report.errors:
            console.print(f"[red]{err}[/]")
        raise SystemExit(1)

    s = report.summary()
    table = Table(title="Итог запуска")
    table.add_column("Метрика")
    table.add_column("Значение")
    table.add_row("Откликов отправлено", str(s["sent"]))
    table.add_row("Требует действия", str(s["manual"]))
    table.add_row("Пропущено", str(s["skipped"]))
    if report.output_path:
        table.add_row("Отчёт", str(report.output_path))
    console.print(table)
    if s["skip_top"]:
        console.print(f"Топ причин пропуска: {s['skip_top']}")


@main.command()
def doctor() -> None:
    """Проверка окружения."""
    cfg = load_config()
    table = Table(title="Open JobHunt — диагностика")
    table.add_column("Проверка")
    table.add_column("Статус")

    root = _project_root()
    table.add_row("config.yaml", "OK" if (root / "config.yaml").exists() else "используется config.example.yaml")
    table.add_row("profile", "OK" if Path(cfg["profile"].get("profile_path", "")).exists() else "MISSING — jobhunt init")

    try:
        import playwright  # noqa: F401

        table.add_row("playwright", "OK")
    except ImportError:
        table.add_row("playwright", "pip install -e .")

    model = cfg.get("llm", {}).get("ollama", {}).get("model", "qwen2.5:7b")
    base = cfg.get("llm", {}).get("ollama", {}).get("base_url", "http://127.0.0.1:11434")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=3.0)
        names = [m["name"] for m in r.json().get("models", [])]
        ok = any(model.split(":")[0] in n for n in names)
        table.add_row("ollama", "OK" if r.status_code == 200 else "down")
        table.add_row(f"model {model}", "OK" if ok else f"run: ollama pull {model}")
    except httpx.HTTPError:
        table.add_row("ollama", "недоступен — ollama serve")

    profile_dir = Path(cfg["browser"]["profile_dir"])
    table.add_row("browser profile", "OK" if profile_dir.exists() else "нет — jobhunt login")
    table.add_row("платформа", sys.platform)

    console.print(table)


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8787, show_default=True, type=int)
@click.option("--browser", is_flag=True, help="Открыть панель в системном браузере (без окна Chromium)")
def ui(host: str, port: int, browser: bool) -> None:
    """Графический интерфейс — одно окно Chromium с панелью и вкладкой hh.ru."""
    from jobhunt.web.launcher import run_ui

    url = f"http://{host}:{port}"
    console.print(f"[green]Open JobHunt:[/] {url}")
    run_ui(host=host, port=port, native=not browser)


if __name__ == "__main__":
    main()
