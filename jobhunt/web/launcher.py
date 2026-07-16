from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time

from jobhunt.platform import kill_listeners_on_port


def _port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) == 0


def start_server_thread(host: str, port: int) -> None:
    from jobhunt.web.server import app
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(0.6)


def run_ui(host: str = "127.0.0.1", port: int = 8787, native: bool = True) -> None:
    url = f"http://{host}:{port}"

    if _port_busy(host, port):
        try:
            import httpx

            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                if native:
                    print(
                        f"Open JobHunt уже запущен — переключитесь на окно Chromium "
                        f"(вкладка «Панель»). Адрес {url} работает только там, не в Chrome."
                    )
                else:
                    print(f"Open JobHunt уже запущен: {url}")
                    import webbrowser

                    webbrowser.open(url)
                sys.exit(0)
        except Exception:
            pass
        print(f"Порт {port} занят, но сервер не отвечает — перезапуск…")
        kill_listeners_on_port(host, port)
        time.sleep(1)

    start_server_thread(host, port)

    if not native:
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    from jobhunt.config import load_config

    cfg = load_config()
    profile_dir = cfg["browser"]["profile_dir"]

    from jobhunt.web.browser_ui import run_playwright_ui

    asyncio.run(run_playwright_ui(url, profile_dir))
