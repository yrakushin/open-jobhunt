from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

from jobhunt.platform import downloads_dir, open_file

STATE_FILE = Path.home() / ".jobhunt" / "browser-state.json"

_bridge: BrowserBridge | None = None


class BrowserBridge:
    """Одно окно Chromium: вкладка панели + вкладка hh.ru (настоящий сайт, без прокси)."""

    def __init__(self, app_url: str, profile_dir: str) -> None:
        self.app_url = app_url
        self.profile_dir = profile_dir
        self._pw = None
        self._context: BrowserContext | None = None
        self._panel_page: Page | None = None
        self._hh_page: Page | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        global _bridge
        self._loop = asyncio.get_running_loop()
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            headless=False,
            viewport={"width": 1320, "height": 880},
            locale="ru-RU",
            slow_mo=30,
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        _bridge = self

        # Playwright по умолчанию гасит нативные диалоги (confirm/alert),
        # из-за чего кнопки подтверждения «исчезали». Автопринимаем их.
        def _accept_dialog(dialog: object) -> None:
            try:
                asyncio.ensure_future(dialog.accept())  # type: ignore[attr-defined]
            except Exception:
                pass

        self._context.on("page", lambda p: p.on("dialog", _accept_dialog))
        self._context.on("download", lambda d: asyncio.ensure_future(self._handle_download(d)))

        await self._ensure_panel(_accept_dialog)

        # Не выходим, когда пользователь закрыл вкладку — иначе падает весь jobhunt ui
        # посреди прогона откликов. Держим окно и пересоздаём панель при необходимости.
        try:
            while True:
                if self._context.is_closed():
                    break
                if self._panel_page is None or self._panel_page.is_closed():
                    await self._ensure_panel(_accept_dialog)
                await asyncio.sleep(0.5)
        finally:
            _bridge = None
            if self._context and not self._context.is_closed():
                await self._context.close()
            if self._pw:
                await self._pw.stop()

    async def _handle_download(self, download: object) -> None:
        suggested = getattr(download, "suggested_filename", None) or "report.xlsx"
        name = str(suggested)
        if not name.lower().endswith(".xlsx"):
            name = f"{name}.xlsx" if name else "report.xlsx"
        dest = downloads_dir() / name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            await download.save_as(str(dest))  # type: ignore[attr-defined]
            open_file(dest)
        except Exception:
            pass

    async def _ensure_panel(self, dialog_handler: object) -> None:
        assert self._context and not self._context.is_closed()
        if self._panel_page and not self._panel_page.is_closed():
            return
        if self._context.pages:
            self._panel_page = self._context.pages[0]
        else:
            self._panel_page = await self._context.new_page()
        self._panel_page.on("dialog", dialog_handler)  # type: ignore[arg-type]
        await self._panel_page.goto(self.app_url, wait_until="domcontentloaded", timeout=60000)

    async def _save_state(self) -> None:
        if self._context:
            await self._context.storage_state(path=str(STATE_FILE))

    async def open_hh(self, path: str = "/applicant/resumes", full_url: str | None = None) -> None:
        if not self._context:
            return
        if self._hh_page is None or self._hh_page.is_closed():
            self._hh_page = await self._context.new_page()
        target = full_url if full_url else f"https://hh.ru{path}"
        await self._hh_page.goto(
            target,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await self._hh_page.bring_to_front()
        await self._save_state()

    async def open_panel(self) -> None:
        if self._panel_page and not self._panel_page.is_closed():
            await self._panel_page.bring_to_front()
            if self.app_url.split("//", 1)[-1] not in self._panel_page.url:
                await self._panel_page.goto(self.app_url, wait_until="domcontentloaded", timeout=60000)


def get_bridge() -> BrowserBridge | None:
    return _bridge


async def run_playwright_ui(app_url: str, profile_dir: str) -> None:
    bridge = BrowserBridge(app_url, profile_dir)
    await bridge.start()
