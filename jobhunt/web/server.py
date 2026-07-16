from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from jobhunt.config import (
    load_config,
    read_letter_template_text,
    save_letter_template,
    save_profile_text,
    update_filters,
    update_match_settings,
    update_min_match,
    update_profile_field,
    update_resume_search_url,
    update_role_filter,
    update_search_preferences,
    update_vacancy_age,
)
from jobhunt.core.match_config import (
    CRITERIA_CATALOG,
    STRICTNESS_LEVELS,
    TIER_LEVELS,
    match_settings_from_cfg,
)
from jobhunt.core.role_filter import ROLE_FILTER_MODES, build_allowed_roles, parse_resume_roles
from jobhunt.core.vacancy_age import VACANCY_AGE_MODES, vacancy_age_mode_from_search
from jobhunt.core.resume_io import extract_text, guess_resume_title
from jobhunt.platform import open_file, apply_windows_utf8_env
from jobhunt.web.browser_ui import get_bridge

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).parent / "static"
STATE_FILE = Path.home() / ".jobhunt" / "browser-state.json"

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_stop_flag = threading.Event()


def _config_path() -> Path:
    p = ROOT / "config.yaml"
    return p if p.exists() else ROOT / "config.example.yaml"


def _load_cfg() -> dict[str, Any]:
    with _config_path().open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _status() -> dict[str, Any]:
    cfg = _load_cfg()
    profile_path = cfg.get("profile", {}).get("profile_path", "")
    browser_profile = os.path.expanduser(cfg.get("browser", {}).get("profile_dir", "~/.jobhunt/browser-profile"))
    reports_dir = cfg.get("paths", {}).get("reports_dir", str(ROOT / "reports"))
    if not Path(reports_dir).is_absolute():
        reports_dir = str(ROOT / reports_dir)

    reports: list[dict[str, str]] = []
    rd = Path(reports_dir)
    if rd.exists():
        for f in sorted(rd.glob("*.xlsx"), reverse=True)[:10]:
            reports.append({"name": f.name, "path": str(f), "mtime": f.stat().st_mtime})

    browser_profile_ok = Path(browser_profile).exists()
    state_ok = STATE_FILE.exists() and STATE_FILE.stat().st_size > 20
    resume_text = ""
    letter_text = ""
    letter_path = cfg.get("profile", {}).get("letter_template_path", "")
    if profile_path:
        p = Path(profile_path)
        if not p.is_absolute():
            p = ROOT / profile_path
        if p.exists():
            resume_text = p.read_text(encoding="utf-8")
            profile_path = str(p)
    if letter_path:
        lp = Path(letter_path)
        if not lp.is_absolute():
            lp = ROOT / letter_path
        if lp.exists():
            letter_text = read_letter_template_text(lp)

    flt = cfg.get("filters", {})
    blacklist = list(flt.get("blacklist_employers", []))
    for p in flt.get("pause_employers", []):
        if p not in blacklist:
            blacklist.append(p)

    match_settings = match_settings_from_cfg(cfg)
    search = cfg.get("search", {})
    llm_min_percent = int(search.get("llm_min_percent") or 30)

    return {
        "profile_ok": bool(resume_text.strip()),
        "profile_chars": len(resume_text.strip()),
        "profile_path": profile_path,
        "letter_ok": bool(letter_text.strip()),
        "letter_chars": len(letter_text.strip()),
        "letter_text": letter_text,
        "browser_session": state_ok or browser_profile_ok,
        "resume_title": cfg.get("profile", {}).get("resume_title", ""),
        "min_match_percent": llm_min_percent,
        "llm_min_percent": llm_min_percent,
        "match_settings": match_settings,
        "match_tiers": TIER_LEVELS,
        "match_strictness_levels": STRICTNESS_LEVELS,
        "match_criteria_catalog": CRITERIA_CATALOG,
        "min_salary_rub": cfg.get("search", {}).get("min_salary_rub") or 0,
        "vacancy_preferences": cfg.get("search", {}).get("vacancy_preferences", ""),
        "resume_search_url": cfg.get("search", {}).get("resume_search_url", ""),
        "role_filter_mode": cfg.get("search", {}).get("role_filter_mode", "off"),
        "role_duties_min_percent": int(cfg.get("search", {}).get("role_duties_min_percent") or 40),
        "resume_roles": parse_resume_roles(cfg.get("profile", {}).get("resume_title", "")),
        "extra_roles": build_allowed_roles(
            cfg.get("profile", {}).get("resume_title", ""),
            cfg.get("search", {}).get("vacancy_preferences", ""),
        )[1],
        "role_filter_modes": ROLE_FILTER_MODES,
        "vacancy_age_mode": vacancy_age_mode_from_search(search),
        "vacancy_age_modes": VACANCY_AGE_MODES,
        "blacklist": blacklist,
        "whitelist": flt.get("whitelist_employers", []),
        "reports": reports,
        "config_path": str(_config_path()),
    }


def _export_browser_state() -> None:
    bridge = get_bridge()
    if not bridge or not bridge._context or not bridge._loop:
        return
    import asyncio

    try:
        future = asyncio.run_coroutine_threadsafe(
            bridge._context.storage_state(path=str(STATE_FILE)),
            bridge._loop,
        )
        future.result(timeout=15)
    except Exception:
        pass


def _run_job_inprocess(job_id: str, dry_run: bool, limit: int) -> None:
    log_lines: list[str] = []

    def append(line: str) -> None:
        log_lines.append(line)
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["log"] = "\n".join(log_lines[-500:])
                _jobs[job_id]["updated_at"] = datetime.now().isoformat()

    bridge = get_bridge()

    def log(msg: str) -> None:
        append(msg)

    _stop_flag.clear()
    append(f"Запуск: {'тест' if dry_run else 'отклики'}, лимит {limit}")
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "running"

    try:
        if bridge and bridge._context and bridge._loop:
            _export_browser_state()
            append("Использую окно приложения (общая сессия hh.ru)")

            async def _job() -> None:
                from jobhunt.runner import run_jobhunt

                report = await run_jobhunt(
                    _config_path(),
                    dry_run=dry_run,
                    max_applications=limit,
                    log_fn=log,
                    browser_context=bridge._context,
                    should_stop=_stop_flag.is_set,
                )
                if report.errors:
                    for err in report.errors:
                        log(f"ОШИБКА: {err}")
                status = "error" if report.errors else "done"
                with _jobs_lock:
                    if job_id in _jobs:
                        _jobs[job_id]["status"] = status
                        _jobs[job_id]["updated_at"] = datetime.now().isoformat()

            import asyncio

            future = asyncio.run_coroutine_threadsafe(_job(), bridge._loop)
            future.result(timeout=3600 * 4)
            return

        append("Окно приложения недоступно — запуск в фоновом браузере")
        _export_browser_state()
        args = ["run", "--limit", str(limit)] + (["--dry-run"] if dry_run else [])
        _run_jobhunt(args, job_id)
    except Exception as e:
        import traceback

        append(f"ОШИБКА: {e}")
        append(traceback.format_exc()[-800:])
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["updated_at"] = datetime.now().isoformat()


def _run_jobhunt(args: list[str], job_id: str) -> None:
    log_lines: list[str] = []

    def append(line: str) -> None:
        log_lines.append(line)
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["log"] = "\n".join(log_lines[-500:])
                _jobs[job_id]["updated_at"] = datetime.now().isoformat()

    append(f"$ jobhunt {' '.join(args)}")
    if args and args[0] in ("run", "doctor"):
        _export_browser_state()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    apply_windows_utf8_env(env)
    auto_profile = str(Path.home() / ".jobhunt" / "browser-profile-auto")
    env["JOBHUNT_BROWSER_PROFILE"] = auto_profile
    if args and args[0] in ("run", "doctor"):
        env["JOBHUNT_HEADLESS"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "jobhunt.cli", *args],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    with _jobs_lock:
        _jobs[job_id]["pid"] = proc.pid
        _jobs[job_id]["status"] = "running"

    assert proc.stdout is not None
    for line in proc.stdout:
        append(line.rstrip())

    code = proc.wait()
    with _jobs_lock:
        _jobs[job_id]["status"] = "done" if code == 0 else "error"
        _jobs[job_id]["exit_code"] = code
        _jobs[job_id]["updated_at"] = datetime.now().isoformat()


def _start_job(args: list[str], dry_run: bool = False, limit: int = 5) -> str:
    with _jobs_lock:
        stale = []
        for jid, j in _jobs.items():
            if j.get("status") not in ("starting", "running"):
                continue
            updated = j.get("updated_at", "")
            try:
                age = (datetime.now() - datetime.fromisoformat(updated)).total_seconds()
            except ValueError:
                age = 0
            if age > 7200:
                stale.append(jid)
        for jid in stale:
            _jobs[jid]["status"] = "error"
            _jobs[jid]["log"] = (_jobs[jid].get("log", "") + "\nТаймаут задачи").strip()
        if any(j.get("status") in ("starting", "running") for j in _jobs.values()):
            raise HTTPException(409, "Уже выполняется запуск — смотрите лог ниже")
    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "args": args,
            "status": "starting",
            "log": "Старт…",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
    if args and args[0] == "run":
        t = threading.Thread(target=_run_job_inprocess, args=(job_id, dry_run, limit), daemon=True)
    else:
        t = threading.Thread(target=_run_jobhunt, args=(args, job_id), daemon=True)
    t.start()
    return job_id


app = FastAPI(title="Open JobHunt", version="0.1.0")


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _report_content_disposition(filename: str) -> str:
    """Content-Disposition для xlsx: ASCII fallback + UTF-8 для кириллицы."""
    ascii_name = re.sub(r"[^\x20-\x7E]", "_", filename).strip(" ._") or "report.xlsx"
    if not ascii_name.lower().endswith(".xlsx"):
        ascii_name += ".xlsx"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _resolve_report_path(filename: str) -> Path:
    cfg = _load_cfg()
    reports_dir = Path(cfg.get("paths", {}).get("reports_dir", ROOT / "reports"))
    if not reports_dir.is_absolute():
        reports_dir = ROOT / reports_dir
    path = (reports_dir / filename).resolve()
    if not str(path).startswith(str(reports_dir.resolve())):
        raise HTTPException(403)
    if not path.exists():
        raise HTTPException(404, "Отчёт не найден")
    return path


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    with _jobs_lock:
        active = [j for j in _jobs.values() if j.get("status") == "running"]
    data = _status()
    data["active_jobs"] = len(active)
    return data


@app.get("/api/jobs")
def api_jobs() -> list[dict[str, Any]]:
    with _jobs_lock:
        return sorted(_jobs.values(), key=lambda x: x.get("created_at", ""), reverse=True)[:20]


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(404, "job not found")
        return _jobs[job_id]


@app.post("/api/match-settings")
def api_match_settings(body: dict[str, Any]) -> dict[str, Any]:
    from jobhunt.core.match_config import MATCH_PRESETS, preset_for_strictness

    strictness = str(body.get("strictness") or "medium").lower()
    if strictness not in MATCH_PRESETS:
        raise HTTPException(400, "strictness: low | medium | high")
    settings = preset_for_strictness(strictness)
    update_match_settings(_config_path(), settings)
    normalized = match_settings_from_cfg(load_config(_config_path()))
    label = STRICTNESS_LEVELS[strictness]["label"]
    return {
        "message": f"«{label}» — порог ≥{normalized['min_percent']}%",
        "match_settings": normalized,
        "min_match_percent": normalized["min_percent"],
    }


@app.post("/api/role-filter")
def api_role_filter(body: dict[str, Any]) -> dict[str, Any]:
    mode = str(body.get("mode") or "off").lower()
    if mode not in ROLE_FILTER_MODES:
        raise HTTPException(400, "mode: off | role_lock")
    min_duties = body.get("role_duties_min_percent")
    if min_duties is not None:
        try:
            min_duties = int(min_duties)
        except (TypeError, ValueError) as e:
            raise HTTPException(400, "role_duties_min_percent — целое число") from e
        if min_duties < 10 or min_duties > 90:
            raise HTTPException(400, "role_duties_min_percent: 10–90")
    update_role_filter(_config_path(), mode, min_duties)
    cfg = load_config(_config_path())
    resume_title = cfg.get("profile", {}).get("resume_title", "")
    roles = parse_resume_roles(resume_title)
    label = ROLE_FILTER_MODES[mode]["label"]
    search = cfg.get("search", {})
    pct = int(search.get("role_duties_min_percent") or 40)
    roles_txt = ", ".join(roles) if roles else "—"
    msg = f"«{label}»"
    if mode == "role_lock":
        msg += f" · роли: {roles_txt} · обязанности ≥{pct}%"
    return {
        "message": msg,
        "role_filter_mode": mode,
        "role_duties_min_percent": pct,
        "resume_roles": roles,
    }


@app.post("/api/vacancy-age")
def api_vacancy_age(body: dict[str, Any]) -> dict[str, Any]:
    mode = str(body.get("mode") or "all").lower()
    if mode not in VACANCY_AGE_MODES:
        raise HTTPException(400, "mode: all | 24h | 3d | 7d")
    update_vacancy_age(_config_path(), mode)
    label = VACANCY_AGE_MODES[mode]["label"]
    return {
        "message": f"Свежесть: {label}",
        "vacancy_age_mode": mode,
    }


@app.post("/api/min-match")
def api_min_match(body: dict[str, Any]) -> dict[str, Any]:
    try:
        percent = int(body.get("percent", 75))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "percent — целое число") from e
    if percent < 50 or percent > 99:
        raise HTTPException(400, "Порог: от 50% до 99%")
    update_min_match(_config_path(), percent)
    return {"message": f"Порог подбора: ≥{percent}%", "min_match_percent": percent}


@app.post("/api/search-url")
def api_search_url(body: dict[str, Any]) -> dict[str, Any]:
    url = str(body.get("url") or "").strip()
    if url and not url.startswith("https://hh.ru/search/vacancy"):
        raise HTTPException(400, "Нужна ссылка вида https://hh.ru/search/vacancy?resume=…")
    update_resume_search_url(_config_path(), url)
    return {
        "message": "Ссылка сохранена" if url else "Ссылка очищена — будет авто-поиск по резюме",
        "resume_search_url": url,
    }


@app.post("/api/search-preferences")
def api_search_preferences(body: dict[str, Any]) -> dict[str, Any]:
    try:
        min_salary = int(body.get("min_salary_rub", 0) or 0)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "min_salary_rub — целое число") from e
    if min_salary < 0 or min_salary > 10_000_000:
        raise HTTPException(400, "Зарплата: от 0 до 10 000 000 ₽")
    prefs = str(body.get("vacancy_preferences") or "")
    if len(prefs) > 8000:
        raise HTTPException(400, "Пожелания — не больше 8000 символов")
    update_search_preferences(
        _config_path(),
        min_salary_rub=min_salary,
        vacancy_preferences=prefs,
    )
    msg = f"Мин. зарплата: {min_salary // 1000}k ₽" if min_salary else "Фильтр по зарплате выключен"
    if prefs.strip():
        msg += f"; пожелания сохранены ({len(prefs.strip())} симв.)"
    cfg = load_config(_config_path())
    _, extra_roles = build_allowed_roles(cfg.get("profile", {}).get("resume_title", ""), prefs.strip())
    return {
        "message": msg,
        "min_salary_rub": min_salary,
        "vacancy_preferences": prefs.strip(),
        "extra_roles": extra_roles,
        "role_duties_min_percent": int(cfg.get("search", {}).get("role_duties_min_percent") or 40),
    }


@app.post("/api/filters")
def api_save_filters(body: dict[str, Any]) -> dict[str, Any]:
    blacklist = body.get("blacklist") or []
    whitelist = body.get("whitelist") or []
    if not isinstance(blacklist, list) or not isinstance(whitelist, list):
        raise HTTPException(400, "blacklist и whitelist — списки строк")
    update_filters(_config_path(), [str(x) for x in blacklist], [str(x) for x in whitelist])
    return {
        "message": f"Сохранено: {len(blacklist)} в чёрном списке, {len(whitelist)} в белом",
        "blacklist": blacklist,
        "whitelist": whitelist,
    }


@app.post("/api/letter/save")
def api_letter_save(body: dict[str, Any]) -> dict[str, Any]:
    text = str(body.get("text") or "")
    if len(text) > 500_000:
        raise HTTPException(400, "Письмо больше 500 КБ")
    cfg = load_config(_config_path())
    save_letter_template(cfg, text)
    update_profile_field(_config_path(), "letter_template_path", "context/cover-letter.md")
    return {
        "chars": len(text.strip()),
        "message": f"Письмо сохранено ({len(text.strip())} символов)",
    }


@app.post("/api/letter/upload")
async def api_letter_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(400, "Файл не выбран")
    data = await file.read()
    if len(data) > 500_000:
        raise HTTPException(400, "Файл больше 500 КБ")

    try:
        text = extract_text(file.filename, data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    cfg = load_config(_config_path())
    save_letter_template(cfg, text)
    update_profile_field(_config_path(), "letter_template_path", "context/cover-letter.md")

    return {
        "chars": len(text),
        "message": f"Письмо загружено ({len(text)} символов). Подстановки: {{company}}, {{title}}, {{signature}}",
    }


@app.post("/api/resume/upload")
async def api_resume_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(400, "Файл не выбран")
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "Файл больше 5 МБ")

    try:
        text = extract_text(file.filename, data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    cfg = load_config(_config_path())
    save_profile_text(cfg, text)
    title = guess_resume_title(text, file.filename or "", cfg.get("profile", {}).get("resume_title", ""))
    if title:
        update_profile_field(_config_path(), "resume_title", title)

    return {
        "ok": True,
        "chars": len(text),
        "resume_title": title,
        "message": f"Резюме загружено ({len(text)} символов). Подбор вакансий — от {int(cfg.get('search', {}).get('llm_min_percent') or 30)}% match.",
    }


@app.post("/api/hh/open")
async def api_hh_open() -> dict[str, Any]:
    bridge = get_bridge()
    if not bridge:
        raise HTTPException(503, "Окно приложения не запущено — перезапустите jobhunt ui")
    cfg = load_config(_config_path())
    search_url = str(cfg.get("search", {}).get("resume_search_url") or "").strip()
    if search_url:
        await bridge.open_hh(full_url=search_url)
        return {"message": "Открыта вкладка с подбором вакансий hh.ru."}
    await bridge.open_hh("/applicant/resumes")
    return {
        "message": "Открыта вкладка hh.ru. Войдите и скопируйте ссылку «Подходящие вакансии» в панель.",
    }


@app.post("/api/panel/open")
async def api_panel_open() -> dict[str, str]:
    bridge = get_bridge()
    if bridge:
        await bridge.open_panel()
    return {"ok": True}


@app.post("/api/login")
async def api_login() -> dict[str, str]:
    return await api_hh_open()


@app.post("/api/run")
def api_run(dry_run: bool = False, limit: int = 5) -> dict[str, str]:
    if limit < 1 or limit > 500:
        raise HTTPException(400, "Укажите число откликов от 1 до 500")
    args = ["run", "--limit", str(limit)]
    job_id = _start_job(args, dry_run=dry_run, limit=limit)
    return {"job_id": job_id}


@app.post("/api/stop")
def api_stop() -> dict[str, str]:
    _stop_flag.set()
    return {"message": "Останавливаю после текущей вакансии…"}


@app.get("/api/reports/{filename}")
def download_report(filename: str):
    path = _resolve_report_path(filename)
    return FileResponse(
        path,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": _report_content_disposition(filename)},
    )


@app.post("/api/reports/{filename}/open")
def open_report(filename: str) -> dict[str, str]:
    path = _resolve_report_path(filename)
    try:
        open_file(path)
    except OSError as e:
        raise HTTPException(500, f"Не удалось открыть файл: {e}") from e
    return {"ok": "true", "path": str(path)}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def run_server(host: str = "127.0.0.1", port: int = 8787) -> None:
    from jobhunt.web.launcher import run_ui

    run_ui(host=host, port=port, native=True)
