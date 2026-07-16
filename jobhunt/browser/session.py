from __future__ import annotations

import asyncio
import os
import random
import re
import time
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

from jobhunt.browser.cookies import apply_saved_cookies
from jobhunt.browser.hh_urls import build_paged_search_url
from jobhunt.browser.parse import (
    extract_negotiation_employers,
    extract_negotiation_vacancy_ids,
    extract_search_publication_map,
    extract_vacancy_ids_from_html,
    html_usable_for_url,
    is_captcha_html,
    is_captcha_state,
    is_captcha_url,
    parse_vacancy_html,
)

HH_BASE = "https://hh.ru"
CAPTCHA_WAIT_SEC = 600
CAPTCHA_POLL_SEC = 2.5
def _silent_fetch_url(url: str) -> bool:
    """Читаем через HTTP/fetch — без page.goto (не мешаем вкладке пользователя)."""
    u = url or ""
    return any(
        x in u
        for x in ("/vacancy/", "search/vacancy", "negotiations", "/applicant/")
    )

TEST_BUTTON_SELECTORS = [
    'a:has-text("Пройти тест")',
    'button:has-text("Пройти тест")',
    'a:has-text("пройти тест")',
    'button:has-text("пройти тест")',
    'a:has-text("Пройти опрос")',
    'button:has-text("Пройти опрос")',
    'a:has-text("пройти опрос")',
    'a[href*="assessment"]',
    'a[href*="opros"]',
    'a:has-text("Перейти на сайт")',
    'a:has-text("перейти на сайт")',
]

MANUAL_PAGE_MARKERS = [
    "пройдите тест для отклика",
    "пройти тест для отклика",
    "для отклика необходимо пройти",
    "обязательно пройдите тест",
    "заполните анкету для отклика",
    "перейдите на сайт работодателя",
]

APPLIED_MARKERS = (
    "вы откликнулись",
    "отклик отправлен",
    "ваш отклик отправлен",
    "откликнулись на эту",
    "отклик успешно",
    "ваше резюме отправлено",
)

APPLIED_SELECTORS = [
    '[data-qa="vacancy-response-success"]',
    '[data-qa="negotiations-open-chat"]',
    'button:has-text("Отозвать отклик")',
    'button:has-text("Отозвать")',
    'a:has-text("Перейти в отклики")',
]

CUSTOM_FIELD_SELECTORS = [
    '[data-qa*="additional-question"] input',
    '[data-qa*="employer-question"] input',
    '[data-qa*="vacancy-response"] select',
    'form[data-qa*="vacancy-response"] input[type="text"]',
    'form[data-qa*="vacancy-response"] input[type="number"]',
]

LETTER_TEXTAREA_SELECTORS = [
    # После мгновенного отклика (vacancy_response → страница вакансии)
    '[data-qa="vacancy-response-letter-informer"] textarea',
    'textarea[data-qa="vacancy-response-popup-form-letter-input"]',
    'textarea[data-qa="vacancy-response-letter-input"]',
    '[data-qa="vacancy-response-popup"] textarea',
    '[role="dialog"] textarea',
]

LETTER_TOGGLE_SELECTORS = [
    '[data-qa="vacancy-response-letter-toggle"]',
    '[data-qa="vacancy-response-letter-informer"] button:has-text("Добавить")',
    'button:has-text("Сопроводительное письмо")',
    'button:has-text("сопроводительное письмо")',
    'button:has-text("Добавить сопроводительное")',
    'button:has-text("Добавить письмо")',
    'button:has-text("добавить письмо")',
    'span:has-text("Сопроводительное письмо")',
]

SUBMIT_SELECTORS = [
    'button[data-qa="vacancy-response-submit-popup"]',
    '[data-qa="vacancy-response-popup"] button[type="submit"]',
]

LETTER_SAVE_SELECTORS = [
    'button[data-qa="vacancy-response-letter-submit"]',
    'button[data-qa="vacancy-response-save-letter"]',
    'button:has-text("Сохранить")',
    'button:has-text("Отправить")',
]

READ_TEXTAREA_JS = """(selectors) => {
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && el.value) return el.value.length;
    }
    return 0;
}"""

# hh.ru (React/Magritte): Playwright fill() не обновляет state — нужен нативный setter + input.
FILL_REACT_TEXTAREA_JS = """(args) => {
    const text = args.text;
    const selectors = args.selectors;
    let el = null;
    for (const sel of selectors) {
        el = document.querySelector(sel);
        if (el && el.offsetParent !== null) break;
        el = null;
    }
    if (!el) {
        for (const sel of selectors) {
            el = document.querySelector(sel);
            if (el) break;
        }
    }
    if (!el) return { ok: false, len: 0 };
    el.scrollIntoView({ block: 'center' });
    el.focus();
    const proto = el instanceof HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
    return { ok: el.value.length > 0, len: el.value.length };
}"""


class BrowserSession:
    def __init__(
        self,
        profile_dir: str,
        headless: bool = False,
        slow_mo_ms: int = 50,
        context: BrowserContext | None = None,
    ):
        self.profile_dir = Path(os.path.expanduser(profile_dir))
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self._external_context = context
        self._pw = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None
        self._work_page: Page | None = None
        self._work_page_parked = False
        self._search_home_url: str | None = None
        self._manual_tabs: set[Page] = set()
        self._browser_lock = asyncio.Lock()
        self._log_fn: Callable[[str], None] | None = None
        self._should_stop: Callable[[], bool] | None = None

    def bind_run(
        self,
        log_fn: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self._log_fn = log_fn
        self._should_stop = should_stop

    async def start(self) -> Page:
        if self._external_context:
            self._context = self._external_context
            return await self._ensure_work_page()

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        profile = os.environ.get("JOBHUNT_BROWSER_PROFILE")
        user_data = str(Path(profile).expanduser()) if profile else str(self.profile_dir)
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=user_data,
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
            viewport={"width": 1280, "height": 900},
            locale="ru-RU",
        )
        self.page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await apply_saved_cookies(self._context)
        return self.page

    async def _ensure_work_page(self) -> Page:
        """Отдельная вкладка для автomation — не трогаем вкладку пользователя (отклики/панель)."""
        assert self._context and not self._context.is_closed()
        if self._work_page and not self._work_page.is_closed():
            self.page = self._work_page
            return self._work_page
        self._work_page = await self._context.new_page()
        self.page = self._work_page
        return self._work_page

    async def close(self) -> None:
        if self._external_context:
            if self._work_page and not self._work_page.is_closed():
                await self._work_page.close()
            self._work_page = None
            self.page = None
            return
        if self._context:
            await self._context.close()
        if self._pw:
            await self._pw.stop()

    async def ensure_logged_in(self, check_url: str | None = None) -> bool:
        page = await self._ensure_work_page()
        url = check_url or f"{HH_BASE}/applicant/resumes"
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.5)
        cur = page.url
        if "account/login" in cur or "oauth" in cur.lower():
            return False
        if is_captcha_url(cur):
            await self.wait_for_captcha_solved(page, self._log_fn, self._should_stop)
            cur = page.url
            if "account/login" in cur:
                return False
        return True

    async def get_resume_hash(self, resume_title: str) -> str | None:
        assert self.page
        # Читаем страницу резюме через её же DOM (надёжнее, чем JS fetch).
        await self.page.goto(f"{HH_BASE}/applicant/resumes", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.0)
        links = await self.page.eval_on_selector_all(
            "a[href*='/resume/']",
            "els => els.map(e => ({href: e.href, text: e.innerText}))",
        )
        title_l = resume_title.lower().strip()
        # 1) точное совпадение по названию
        for link in links:
            if title_l and title_l in (link.get("text") or "").lower():
                m = re.search(r"/resume/([a-f0-9]{20,})", link.get("href", ""))
                if m:
                    return m.group(1)
        # 2) если не нашли по названию — берём первое резюме
        for link in links:
            m = re.search(r"/resume/([a-f0-9]{20,})", link.get("href", ""))
            if m:
                return m.group(1)
        return None

    async def _request_get(self, url: str) -> str:
        assert self._context
        try:
            resp = await self._context.request.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                },
                timeout=45000,
            )
            if resp.ok:
                return await resp.text()
        except Exception:
            pass
        return ""

    async def _restore_user_tab(self) -> None:
        """Не переключаем вкладку пользователя — работаем в фоне."""
        return

    async def _park_work_page(self, page: Page) -> None:
        """Служебная вкладка остаётся на hh.ru — fetch без перехода на каждую вакансию."""
        if self._work_page_parked:
            return
        try:
            if not (page.url or "").startswith(HH_BASE):
                await page.goto(f"{HH_BASE}/", wait_until="domcontentloaded", timeout=60000)
            self._work_page_parked = True
        except Exception:
            pass

    async def _fetch_html_in_page(self, page: Page, url: str) -> str:
        try:
            if not (page.url or "").startswith(HH_BASE):
                await page.goto(f"{HH_BASE}/", wait_until="domcontentloaded", timeout=60000)
                self._work_page_parked = True
            return await page.evaluate(
                """async (u) => {
                    const r = await fetch(u, {
                        credentials: 'include',
                        headers: { Accept: 'text/html,application/xhtml+xml' },
                    });
                    return await r.text();
                }""",
                url,
            )
        except Exception:
            return ""

    async def wait_for_captcha_solved(
        self,
        page: Page,
        log_fn: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> bool:
        def log(msg: str) -> None:
            if log_fn:
                log_fn(msg)

        log("⚠ Капча hh.ru — найди вкладку Chromium с капчей и пройди её. Окно не переключаю.")

        elapsed = 0.0
        last_ping = 0.0
        while elapsed < CAPTCHA_WAIT_SEC:
            if should_stop and should_stop():
                log("Остановка: капча не пройдена.")
                return False
            try:
                url = page.url or ""
                html = await page.content()
            except Exception:
                await asyncio.sleep(CAPTCHA_POLL_SEC)
                elapsed += CAPTCHA_POLL_SEC
                continue
            if not is_captcha_state(html, url):
                log("✓ Капча пройдена, продолжаю.")
                await asyncio.sleep(0.8)
                await self._restore_user_tab()
                return True
            if elapsed - last_ping >= 30:
                log(f"  … всё ещё капча ({int(elapsed)}с). Вкладка: {url[:70]}")
                last_ping = elapsed
            await asyncio.sleep(CAPTCHA_POLL_SEC)
            elapsed += CAPTCHA_POLL_SEC

        log(f"Таймаут {CAPTCHA_WAIT_SEC // 60} мин — капча не пройдена.")
        return False

    def set_search_home(self, url: str | None) -> None:
        self._search_home_url = (url or "").strip() or None

    async def _return_to_search_home(self) -> None:
        if not self._work_page or self._work_page.is_closed():
            return
        try:
            await self._work_page.goto(f"{HH_BASE}/", wait_until="domcontentloaded", timeout=60000)
            self._work_page_parked = True
        except Exception:
            pass
        await self._restore_user_tab()

    async def fetch_vacancy_html(
        self,
        vacancy_id: str,
        log_fn: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[str, str | None]:
        """HTML вакансии. Сначала HTTP, затем fetch/goto в браузере (под lock)."""
        log_fn = log_fn or self._log_fn
        should_stop = should_stop or self._should_stop
        url = f"{HH_BASE}/vacancy/{vacancy_id}"
        html_text = await self._request_get(url)
        if html_text and not is_captcha_state(html_text, url) and parse_vacancy_html(html_text):
            return html_text, None

        async with self._browser_lock:
            page = await self._ensure_work_page()
            for _ in range(3):
                if should_stop and should_stop():
                    break
                if not (page.url or "").startswith(HH_BASE):
                    await self._park_work_page(page)
                html_text = await self._fetch_html_in_page(page, url)
                if is_captcha_state(html_text, url):
                    solved = await self.wait_for_captcha_solved(page, log_fn, should_stop)
                    if not solved:
                        return html_text, "captcha"
                    continue
                if parse_vacancy_html(html_text):
                    return html_text, None
                await asyncio.sleep(random.uniform(0.4, 0.8))

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_selector(
                        '[data-qa="vacancy-title"], [data-qa="vacancy-description"], '
                        'button[data-qa="vacancy-response-link-top"]',
                        timeout=12000,
                    )
                except Exception:
                    pass
                await asyncio.sleep(0.6)
                html_text = await page.content()
                cur = page.url or ""
                if is_captcha_state(html_text, cur):
                    solved = await self.wait_for_captcha_solved(page, log_fn, should_stop)
                    if not solved:
                        return html_text, "captcha"
                    html_text = await page.content()
                if parse_vacancy_html(html_text):
                    try:
                        await page.goto(f"{HH_BASE}/", wait_until="domcontentloaded", timeout=30000)
                        self._work_page_parked = True
                    except Exception:
                        pass
                    return html_text, None
            except Exception:
                html_text = ""

            try:
                await page.goto(f"{HH_BASE}/", wait_until="domcontentloaded", timeout=30000)
                self._work_page_parked = True
            except Exception:
                pass
        return html_text, "не удалось прочитать"

    async def fetch_html(
        self,
        url: str,
        log_fn: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        foreground: bool = False,
    ) -> str:
        """HTML страницы. Оценка вакансий — тихо (HTTP/fetch). goto только при foreground или капче."""
        log_fn = log_fn or self._log_fn
        should_stop = should_stop or self._should_stop
        assert self._context
        html_text = await self._request_get(url)
        if html_usable_for_url(url, html_text):
            return html_text

        silent = not foreground and _silent_fetch_url(url)
        page = await self._ensure_work_page()

        async with self._browser_lock:
            if silent:
                await self._park_work_page(page)
                for _ in range(3):
                    if should_stop and should_stop():
                        return html_text
                    html_text = await self._fetch_html_in_page(page, url)
                    if is_captcha_state(html_text, url):
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        except Exception:
                            pass
                        solved = await self.wait_for_captcha_solved(page, log_fn, should_stop)
                        if not solved:
                            return html_text
                        self._work_page_parked = False
                        await self._park_work_page(page)
                        continue
                    if html_usable_for_url(url, html_text):
                        return html_text
                    await asyncio.sleep(random.uniform(0.4, 0.9))
                if "/vacancy/" in url and re.search(r"/vacancy/\d+", url):
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        await asyncio.sleep(0.7)
                        html_text = await page.content()
                        if html_usable_for_url(url, html_text):
                            await self._park_work_page(page)
                            return html_text
                    except Exception:
                        pass
                return html_text

            for _ in range(3):
                if should_stop and should_stop():
                    return html_text
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                    html_text = await page.content()
                except Exception:
                    html_text = ""

                cur_url = page.url or ""
                if is_captcha_state(html_text, cur_url):
                    solved = await self.wait_for_captcha_solved(page, log_fn, should_stop)
                    if not solved:
                        return html_text
                    continue
                if html_usable_for_url(url, html_text) or html_text:
                    return html_text
        return html_text

    def parse_vacancy_from_html(self, html_text: str) -> dict | None:
        return parse_vacancy_html(html_text)

    async def collect_applied_vacancy_ids(self) -> set[str]:
        await self._ensure_work_page()
        ids: set[str] = set()
        for page_num in range(0, 3):
            url = f"{HH_BASE}/applicant/negotiations?page={page_num}"
            html_text = await self.fetch_html(url)
            found = extract_negotiation_vacancy_ids(html_text)
            if not found:
                break
            ids.update(found)
        return ids

    async def company_application_counts(self) -> dict[str, int]:
        await self._ensure_work_page()
        counts: dict[str, int] = {}
        html_text = await self.fetch_html(f"{HH_BASE}/applicant/negotiations?page=0")
        pairs = extract_negotiation_employers(html_text)
        from jobhunt.core.filters import normalize_company

        for name in pairs:
            key = normalize_company(name)
            counts[key] = counts.get(key, 0) + 1
        return counts

    async def _extract_vacancy_ids(self, html_text: str) -> list[str]:
        return extract_vacancy_ids_from_html(html_text)

    async def search_vacancy_ids_from_url(
        self,
        base_url: str,
        pages: int,
        per_page: int,
        area: int | None = None,
        log_fn: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        search_period: int | None = None,
        publication_map: dict | None = None,
    ) -> list[str]:
        ids: list[str] = []
        for p in range(pages):
            if should_stop and should_stop():
                break
            url = build_paged_search_url(base_url, p, per_page, area, search_period)
            if p == 0 and log_fn:
                log_fn(f"  -> {url[:100]}...")
            html_text = await self.fetch_html(url, log_fn=log_fn, should_stop=should_stop)
            if publication_map is not None:
                publication_map.update(extract_search_publication_map(html_text))
            page_ids = await self._extract_vacancy_ids(html_text)
            if not page_ids:
                break
            ids.extend(page_ids)
            if p == 0 and log_fn:
                log_fn(f"  страница 1: {len(page_ids)} вакансий")
            await asyncio.sleep(random.uniform(1.0, 2.5))
        return list(dict.fromkeys(ids))

    async def search_vacancy_ids_by_resume(
        self,
        resume_hash: str,
        area: int,
        pages: int,
        per_page: int,
        log_fn: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        search_period: int | None = None,
        publication_map: dict | None = None,
    ) -> list[str]:
        """Вакансии, подобранные hh.ru под резюме (как «2000+ подходят» в личном кабинете)."""
        assert self.page
        ids: list[str] = []
        for p in range(pages):
            if should_stop and should_stop():
                break
            period_param = f"&search_period={search_period}" if search_period and search_period > 0 else ""
            url = (
                f"{HH_BASE}/search/vacancy?resume={resume_hash}"
                f"&area={area}&order_by=publication_time{period_param}"
                f"&items_on_page={per_page}&page={p}"
            )
            html_text = await self.fetch_html(url, log_fn=log_fn, should_stop=should_stop)
            if publication_map is not None:
                publication_map.update(extract_search_publication_map(html_text))
            page_ids = await self._extract_vacancy_ids(html_text)
            if not page_ids:
                break
            ids.extend(page_ids)
            await asyncio.sleep(random.uniform(1.0, 2.5))
        return list(dict.fromkeys(ids))

    async def search_vacancy_ids(
        self,
        query: str,
        area: int,
        period: int,
        pages: int,
        per_page: int,
        log_fn: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        publication_map: dict | None = None,
    ) -> list[str]:
        assert self.page
        ids: list[str] = []
        for p in range(pages):
            if should_stop and should_stop():
                break
            from urllib.parse import quote

            q = quote(query)
            period_param = f"&search_period={period}" if period > 0 else ""
            url = (
                f"{HH_BASE}/search/vacancy?text={q}&search_field=name"
                f"&area={area}{period_param}&order_by=publication_time"
                f"&items_on_page={per_page}&page={p}"
            )
            html_text = await self.fetch_html(url, log_fn=log_fn, should_stop=should_stop)
            if publication_map is not None:
                publication_map.update(extract_search_publication_map(html_text))
            page_ids = await self._extract_vacancy_ids(html_text)
            if not page_ids:
                break
            ids.extend(page_ids)
            await asyncio.sleep(random.uniform(1.0, 2.5))
        return list(dict.fromkeys(ids))

    async def _protected_tabs(self) -> set[Page]:
        protected: set[Page] = set()
        if self.page and not self.page.is_closed():
            protected.add(self.page)
        if self._work_page and not self._work_page.is_closed():
            protected.add(self._work_page)
        for tab in self._manual_tabs:
            if not tab.is_closed():
                protected.add(tab)
        try:
            from jobhunt.web.browser_ui import get_bridge

            bridge = get_bridge()
            if bridge:
                for attr in ("_hh_page", "_panel_page"):
                    tab = getattr(bridge, attr, None)
                    if tab and not tab.is_closed():
                        protected.add(tab)
        except Exception:
            pass
        if self._context:
            for tab in self._context.pages:
                if tab.is_closed():
                    continue
                url = tab.url or ""
                if "127.0.0.1" in url and ":8787" in url:
                    protected.add(tab)
        return protected

    async def _open_user_tab(self, url: str, log_hint: str = "") -> Page:
        """Отдельная вкладка для ручных действий — без переключения фокуса."""
        assert self._context
        tab = await self._context.new_page()
        await tab.goto(url, wait_until="domcontentloaded", timeout=60000)
        self._manual_tabs.add(tab)
        if self._log_fn:
            msg = f"  ⚠ {log_hint} — вкладка в фоне"
            if url:
                msg += f": {url[:90]}"
            self._log_fn(msg)
        return tab

    async def open_vacancy_for_manual(self, vacancy_id: str, title: str = "", vacancy_url: str = "") -> str:
        url = vacancy_url or f"{HH_BASE}/vacancy/{vacancy_id}"
        hint = f"Тест/форма: {(title or vacancy_id)[:50]}"
        tab = await self._open_user_tab(url, hint)
        return tab.url or url

    async def _extract_test_url(self) -> str | None:
        assert self.page
        for sel in TEST_BUTTON_SELECTORS:
            loc = self.page.locator(sel)
            if await loc.count() == 0:
                continue
            try:
                href = await loc.first.get_attribute("href")
                if href:
                    return href if href.startswith("http") else f"{HH_BASE}{href}"
            except Exception:
                continue
        return None

    async def _page_needs_manual_input(self) -> tuple[bool, str, str | None]:
        """Только реальные блокеры: внешний сайт или ссылка на тест не на hh.ru."""
        assert self.page
        cur = self.page.url or ""
        cur_low = cur.lower()
        if cur and "hh.ru" not in cur_low and "127.0.0.1" not in cur_low:
            return True, "внешний сайт — заполните вручную", cur

        test_url = await self._extract_test_url()
        if test_url:
            low = test_url.lower()
            if "assessment" in low or "opros" in low or "questionnaire" in low:
                return True, "тест на сайте работодателя", test_url
            if not low.startswith(f"{HH_BASE}/vacancy/") and "hh.ru/applicant" not in low:
                return True, "тест на сайте работодателя", test_url

        lower = await self._body_lower()
        if any(m in lower for m in MANUAL_PAGE_MARKERS):
            ext = await self._extract_test_url()
            if ext and not ext.lower().startswith(f"{HH_BASE}/vacancy/"):
                return True, "тест/анкета для отклика", ext

        for sel in CUSTOM_FIELD_SELECTORS:
            loc = self.page.locator(sel)
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    submit = self.page.locator(", ".join(SUBMIT_SELECTORS))
                    if await submit.count() > 0 and await submit.first.is_visible():
                        return True, "дополнительные поля в форме", None
            except Exception:
                continue
        return False, "", None

    async def _confirm_apply_sent(self) -> bool:
        return await self._is_already_applied()

    async def _body_lower(self) -> str:
        assert self.page
        return (await self.page.inner_text("body")).lower()

    async def _is_already_applied(self) -> bool:
        """Отклик уже отправлен на hh.ru."""
        assert self.page
        lower = await self._body_lower()
        if any(x in lower for x in APPLIED_MARKERS):
            return True
        for sel in APPLIED_SELECTORS:
            loc = self.page.locator(sel)
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    return True
            except Exception:
                continue
        chat = self.page.locator(
            '[data-qa="negotiations-open-chat"], '
            'a[data-qa="vacancy-response-link"], '
            'button:has-text("Чат")'
        )
        try:
            if await chat.count() > 0 and await chat.first.is_visible():
                submit = self.page.locator(", ".join(SUBMIT_SELECTORS))
                if await submit.count() == 0 or not await submit.first.is_visible():
                    return True
        except Exception:
            pass
        return False

    async def _letter_field_len(self) -> int:
        assert self.page
        try:
            n = await self.page.evaluate(READ_TEXTAREA_JS, LETTER_TEXTAREA_SELECTORS)
            return int(n or 0)
        except Exception:
            return 0

    async def _open_letter_field(self) -> bool:
        """Раскрывает поле сопроводительного письма, если оно свёрнуто."""
        assert self.page
        combined = ", ".join(LETTER_TEXTAREA_SELECTORS)
        textarea = self.page.locator(combined)
        try:
            if await textarea.count() > 0 and await textarea.first.is_visible():
                return True
        except Exception:
            pass

        informer = self.page.locator('[data-qa="vacancy-response-letter-informer"]')
        try:
            if await informer.count() > 0 and await informer.first.is_visible():
                inner = informer.locator("textarea")
                if await inner.count() > 0:
                    return True
        except Exception:
            pass

        for sel in LETTER_TOGGLE_SELECTORS:
            toggle = self.page.locator(sel)
            if await toggle.count() == 0:
                continue
            try:
                await toggle.first.scroll_into_view_if_needed(timeout=5000)
                await toggle.first.click(timeout=5000)
                await asyncio.sleep(0.6)
                try:
                    await textarea.first.wait_for(state="visible", timeout=5000)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
        return await textarea.count() > 0

    async def _fill_react_textarea(self, letter: str) -> bool:
        """Заполняет textarea через нативный setter — иначе React на hh.ru игнорирует текст."""
        assert self.page
        if not letter.strip():
            return True
        min_len = min(40, len(letter.strip()))

        for attempt in range(3):
            result = await self.page.evaluate(
                FILL_REACT_TEXTAREA_JS,
                {"text": letter, "selectors": LETTER_TEXTAREA_SELECTORS},
            )
            if isinstance(result, dict) and int(result.get("len", 0)) >= min_len:
                return True
            if await self._letter_field_len() >= min_len:
                return True

            combined = ", ".join(LETTER_TEXTAREA_SELECTORS)
            loc = self.page.locator(combined)
            if await loc.count() > 0:
                try:
                    target = loc.first
                    await target.scroll_into_view_if_needed(timeout=5000)
                    await target.click(timeout=5000)
                    await target.fill("")
                    # Посимвольный ввод — второй fallback для React-форм.
                    await target.press_sequentially(letter[:4000], delay=8)
                    await asyncio.sleep(0.3)
                    if await self._letter_field_len() >= min_len:
                        return True
                except Exception:
                    pass
            await asyncio.sleep(0.4)
        return await self._letter_field_len() >= min_len

    async def _click_first_visible(self, selectors: list[str], timeout: int = 10000) -> bool:
        assert self.page
        for sel in selectors:
            loc = self.page.locator(sel)
            if await loc.count() == 0:
                continue
            try:
                btn = loc.first
                await btn.scroll_into_view_if_needed(timeout=5000)
                await btn.click(timeout=timeout)
                return True
            except Exception:
                continue
        loc = self.page.get_by_role("button", name=re.compile("откликнуться", re.I))
        if await loc.count() > 0:
            try:
                await loc.first.click(timeout=timeout)
                return True
            except Exception:
                pass
        return False

    def _letter_min_len(self, letter: str) -> int:
        return min(40, len(letter.strip()))

    async def _letter_is_attached(self, letter: str) -> bool:
        if not letter.strip():
            return True
        return await self._letter_field_len() >= self._letter_min_len(letter)

    async def _submit_cover_letter(self, letter: str) -> bool:
        """Заполняет и отправляет сопроводительное письмо (попап или блок на странице вакансии)."""
        assert self.page
        if not letter.strip():
            return True
        if await self._letter_is_attached(letter):
            return True
        if self._log_fn:
            self._log_fn("  сопроводительное письмо…")

        for _ in range(3):
            await self._open_letter_field()
            if not await self._fill_react_textarea(letter):
                await asyncio.sleep(0.6)
                continue

            letter_submit = self.page.locator(
                '[data-qa="vacancy-response-letter-informer"] [data-qa="vacancy-response-letter-submit"]'
            )
            if await letter_submit.count() > 0:
                try:
                    btn = letter_submit.first
                    if await btn.is_visible():
                        await btn.scroll_into_view_if_needed(timeout=5000)
                        await btn.click(timeout=10000)
                        await asyncio.sleep(1.2)
                except Exception:
                    pass
            elif await self._click_first_visible(LETTER_SAVE_SELECTORS):
                await asyncio.sleep(1.2)

            if await self._letter_is_attached(letter):
                return True
            await asyncio.sleep(0.6)

        return await self._letter_is_attached(letter)

    async def _ensure_letter_filled(self, letter: str) -> bool:
        """Раскрывает поле и заполняет письмо. False = письмо обязательно, но не вставилось."""
        if not letter.strip():
            return True
        for _ in range(2):
            await self._open_letter_field()
            if await self._fill_react_textarea(letter):
                return True
            await asyncio.sleep(0.8)
        return False

    async def _attach_letter_after_apply(self, letter: str) -> bool:
        """Отклик уже ушёл: письмо через informer на странице вакансии или попап."""
        return await self._submit_cover_letter(letter)

    async def _wait_apply_confirmed(self, timeout_sec: float = 15.0) -> bool:
        """Ждём подтверждение отклика на hh.ru после клика «Откликнуться»."""
        assert self.page
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if await self._is_already_applied():
                return True
            try:
                ok = await self.page.locator('[data-qa="vacancy-response-success"]').first.is_visible()
                if ok:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
        return await self._is_already_applied()

    async def _apply_with_letter(self, letter: str) -> tuple[bool, str]:
        """Возвращает (успех, примечание). Успех = отклик ушёл (письмо опционально)."""
        assert self.page
        need = bool(letter.strip())
        already = await self._is_already_applied()

        if already:
            note = ""
            if need and not await self._attach_letter_after_apply(letter):
                note = "письмо не прикрепилось"
            return True, note

        if need and not await self._ensure_letter_filled(letter):
            if await self._is_already_applied():
                return True, "письмо не прикрепилось"
            return False, "не удалось заполнить письмо до отправки"

        if not await self._click_first_visible(SUBMIT_SELECTORS):
            needs, why, _ = await self._page_needs_manual_input()
            if needs:
                return False, why
            if await self._is_already_applied():
                return True, ""
            return False, "кнопка «Откликнуться» не найдена"

        if await self._wait_apply_confirmed():
            note = ""
            if need and not await self._submit_cover_letter(letter):
                note = "письмо не прикрепилось"
            return True, note

        needs, why, _ = await self._page_needs_manual_input()
        if needs:
            return False, why

        if need:
            await self._submit_cover_letter(letter)

        if await self._is_already_applied():
            note = ""
            if need and not await self._submit_cover_letter(letter):
                note = "письмо не прикрепилось"
            return True, note

        needs, why, _ = await self._page_needs_manual_input()
        if needs:
            return False, why
        return False, "отклик не подтверждён"

    async def _close_extra_tabs(self) -> None:
        """Не закрываем вкладки пользователя и ручные (тесты/анкеты)."""
        assert self._context
        protected = await self._protected_tabs()
        for tab in list(self._context.pages):
            if tab in protected or tab.is_closed():
                continue
            url = tab.url or ""
            if "127.0.0.1" in url or "hh.ru" in url:
                continue
            try:
                await tab.close()
            except Exception:
                pass

    async def _handoff_to_user(self, vacancy_id: str, reason: str, extra_url: str | None = None) -> tuple[str, str]:
        if await self._confirm_apply_sent():
            return "sent", reason
        target = extra_url or await self._extract_test_url()
        if target and not target.lower().startswith(f"{HH_BASE}/vacancy/"):
            await self._open_user_tab(target, reason)
            return "manual", f"{reason} — вкладка с тестом в фоне"
        return "manual", reason

    async def apply_to_vacancy(self, vacancy_id: str, resume_hash: str, letter: str) -> tuple[str, str]:
        """Returns (status, reason). status: sent | manual | skipped"""
        assert self._context
        async with self._browser_lock:
            return await self._apply_to_vacancy_locked(vacancy_id, resume_hash, letter)

    async def _apply_to_vacancy_locked(self, vacancy_id: str, resume_hash: str, letter: str) -> tuple[str, str]:
        page = await self._ensure_work_page()
        if self._log_fn:
            self._log_fn("  отклик (фоновая вкладка, окно не трогаю)…")
        url = f"{HH_BASE}/applicant/vacancy_response?vacancyId={vacancy_id}&resumeHash={resume_hash}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                await self.page.wait_for_selector(
                    '[data-qa="vacancy-response-letter-informer"], '
                    '[data-qa="vacancy-response-letter-toggle"], '
                    'textarea[data-qa="vacancy-response-popup-form-letter-input"], '
                    'button[data-qa="vacancy-response-submit-popup"], '
                    'button:has-text("Откликнулись"), '
                    'button:has-text("Чат")',
                    timeout=15000,
                )
            except Exception:
                pass
            await asyncio.sleep(0.5)

            if await self._is_already_applied():
                note = ""
                if letter.strip() and not await self._submit_cover_letter(letter):
                    note = "письмо не прикрепилось"
                return "sent", note or "уже откликались"

            lower = await self._body_lower()
            if "капч" in lower or ("робот" in lower and "не робот" in lower):
                return "manual", "капча — пройдите вручную"

            ok, note = await self._apply_with_letter(letter)
            if ok or await self._is_already_applied():
                return "sent", note

            needs, why, extra_url = await self._page_needs_manual_input()
            if needs:
                return await self._handoff_to_user(vacancy_id, why, extra_url)

            return "manual", note or "отклик не подтверждён"
        finally:
            await self._return_to_search_home()
            await self._close_extra_tabs()
