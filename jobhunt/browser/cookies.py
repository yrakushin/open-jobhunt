from __future__ import annotations

import json
from pathlib import Path

from playwright.async_api import BrowserContext

STATE_FILE = Path.home() / ".jobhunt" / "browser-state.json"


async def apply_saved_cookies(context: BrowserContext) -> None:
    if not STATE_FILE.exists():
        return
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        cookies = state.get("cookies", [])
    except (json.JSONDecodeError, OSError):
        return
    if cookies:
        await context.add_cookies(cookies)
