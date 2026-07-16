from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import BrowserContext
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from jobhunt.browser.hh_urls import parse_resume_hash_from_url
from jobhunt.browser.session import BrowserSession
from jobhunt.browser.parse import is_captcha_state, is_robot_check_title
from jobhunt.config import load_config, read_letter_template_text, read_text_file
from jobhunt.core.vacancy_age import (
    VACANCY_AGE_MODES,
    period_days_for_mode,
    pick_newer_published_at,
    vacancy_age_mode_from_search,
)
from jobhunt.core.match_config import match_settings_from_cfg
from jobhunt.core.matching import detect_requires_test
from jobhunt.core.filters import normalize_company, pre_filter_vacancy, rule_match_score, vacancy_similarity_key
from jobhunt.core.role_filter import build_allowed_roles, role_filter_enabled
from jobhunt.core.letter import render_letter_template
from jobhunt.core.report import write_excel_report
from jobhunt.llm.client import build_llm, generate_letter_llm, score_match_llm
from jobhunt.models import ApplicationResult, RunReport, Vacancy
from jobhunt.platform import configure_stdio_utf8, console_safe_text

configure_stdio_utf8()
console = Console()

DELAY_MIN_SEC = 8
DELAY_MAX_SEC = 35
READ_DELAY_MIN_SEC = 0.8
READ_DELAY_MAX_SEC = 1.5
RULE_SKIP_MARGIN = 18


def _compensation_str(comp: dict | None) -> str:
    if not comp:
        return "не указана"
    if isinstance(comp, str):
        return comp
    parts = []
    for key in ("from", "to", "currencyCode"):
        if comp.get(key):
            parts.append(str(comp[key]))
    return " ".join(parts) if parts else "не указана"


async def run_jobhunt(
    cfg_path: Path | None = None,
    dry_run: bool = False,
    max_applications: int | None = None,
    log_fn: Callable[[str], None] | None = None,
    browser_context: BrowserContext | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RunReport:
    def log(msg: str) -> None:
        if log_fn:
            log_fn(msg)
            return
        try:
            console.print(msg)
        except UnicodeEncodeError:
            console.print(console_safe_text(msg))

    cfg = load_config(cfg_path)
    from jobhunt import __version__

    log(f"Open JobHunt v{__version__}")
    report = RunReport()
    search = cfg["search"]
    profile_cfg = cfg["profile"]
    profile_md = read_text_file(profile_cfg.get("profile_path", ""))
    resume_title = profile_cfg.get("resume_title", "")
    style_md = read_text_file(profile_cfg.get("letter_style_path", ""))
    letter_template = read_letter_template_text(profile_cfg.get("letter_template_path", ""))
    llm = build_llm(cfg)

    browser_cfg = cfg["browser"]
    headless = browser_cfg.get("headless", False)
    if browser_context is None:
        if os.environ.get("JOBHUNT_HEADLESS", "").lower() in ("1", "true", "yes"):
            headless = True
        # отдельный профиль, чтобы не конфликтовать с окном UI
        os.environ.setdefault(
            "JOBHUNT_BROWSER_PROFILE",
            str(Path.home() / ".jobhunt" / "browser-profile-auto"),
        )

    session = BrowserSession(
        browser_cfg["profile_dir"],
        headless=headless,
        slow_mo_ms=browser_cfg.get("slow_mo_ms", 50),
        context=browser_context,
    )

    try:
        log("Старт: открываю браузер…")
        await session.start()
        session.bind_run(log_fn=log, should_stop=should_stop)
        resume_search_url = (search.get("resume_search_url") or "").strip()
        session.set_search_home(resume_search_url or None)
        login_check = resume_search_url if resume_search_url else None
        log("Проверяю вход на hh.ru…")
        if not await session.ensure_logged_in(login_check):
            report.errors.append("Не авторизован на hh.ru. Войдите через «Войти на hh.ru».")
            log("ОШИБКА: не авторизован на hh.ru")
            return report

        if resume_search_url:
            resume_hash = parse_resume_hash_from_url(resume_search_url)
            if not resume_hash:
                report.errors.append("В ссылке поиска нет параметра resume=…")
                log("ОШИБКА: некорректная ссылка подбора hh.ru")
                return report
            log("Резюме: hash из сохранённой ссылки поиска.")
        else:
            log(f"Ищу резюме «{resume_title}»…")
            resume_hash = await session.get_resume_hash(resume_title)
            if not resume_hash:
                report.errors.append(f"Резюме «{resume_title}» не найдено на hh.ru")
                log(f"ОШИБКА: резюме «{resume_title}» не найдено")
                log("Подсказка: вставьте ссылку «Подходящие вакансии» в панели Open JobHunt")
                return report
            log("Резюме найдено.")

        report.resume_hash = resume_hash

        log("Собираю уже отправленные отклики…")
        applied_ids = await session.collect_applied_vacancy_ids()
        company_counts = await session.company_application_counts()
        log(f"Уже есть отклики на {len(applied_ids)} вакансий (их пропустим при отправке)")

        per_page = search.get("items_per_page", 100)
        max_scan = search.get("max_vacancies_to_scan", 0)
        age_mode = vacancy_age_mode_from_search(search)
        period = period_days_for_mode(age_mode)
        area = search.get("area", 1)
        resume_pages = min(int(search.get("max_pages_resume", 30) or 30), 30)
        query_pages = search.get("max_pages_per_query", 10)
        use_resume = search.get("use_resume_search", True)
        search_period = period if age_mode != "all" else 0

        vacancy_ids: list[str] = []
        search_publication_map: dict[str, object] = {}

        if use_resume:
            if resume_search_url:
                log(f"Открываю вашу ссылку подбора (до {resume_pages} стр.)…")
                resume_ids = await session.search_vacancy_ids_from_url(
                    resume_search_url,
                    resume_pages,
                    per_page,
                    None,
                    log_fn=log,
                    should_stop=should_stop,
                    search_period=search_period,
                    publication_map=search_publication_map,
                )
            else:
                log(f"Поиск по резюме: до {resume_pages} стр.")
                resume_ids = await session.search_vacancy_ids_by_resume(
                    resume_hash,
                    area,
                    resume_pages,
                    per_page,
                    log_fn=log,
                    should_stop=should_stop,
                    search_period=search_period,
                    publication_map=search_publication_map,
                )
            vacancy_ids.extend(resume_ids)
            log(f"По резюме: {len(resume_ids)} вакансий (до {resume_pages} стр.)")

        if not resume_search_url:
            for query in search.get("queries", []):
                if max_scan and len(vacancy_ids) >= max_scan:
                    break
                log(f"Поиск: {query}")
                ids = await session.search_vacancy_ids(
                    query,
                    area,
                    period,
                    query_pages,
                    per_page,
                    log_fn=log,
                    should_stop=should_stop,
                    publication_map=search_publication_map,
                )
                before = len(vacancy_ids)
                vacancy_ids.extend(ids)
                log(f"  +{len(vacancy_ids) - before} по запросу")

        vacancy_ids = list(dict.fromkeys(vacancy_ids))
        total_found = len(vacancy_ids)
        if max_scan and max_scan > 0 and total_found > max_scan:
            log(f"⚠ Лимит скана {max_scan}: обрезано с {total_found} вакансий (см. max_vacancies_to_scan в config)")
            vacancy_ids = vacancy_ids[:max_scan]
        log(f"Найдено уникальных вакансий: {len(vacancy_ids)}" + (f" из {total_found}" if total_found != len(vacancy_ids) else ""))

        match_settings = match_settings_from_cfg(cfg)
        min_match = int(cfg.get("search", {}).get("llm_min_percent") or 30)
        max_apps = max_applications if max_applications is not None else search.get("max_applications_per_run", 20)
        vacancy_preferences = (search.get("vacancy_preferences") or "").strip()
        min_salary = int(search.get("min_salary_rub") or 0)
        sent_count = 0
        seen_similar: dict[str, str] = {}
        processed = 0
        consecutive_captcha = 0
        log(f"Цель: {max_apps} откликов (match ≥{min_match}%)")
        if age_mode != "all":
            log(f"Свежесть: только {VACANCY_AGE_MODES[age_mode]['label'].lower()}")
            if search_period > 0:
                log(f"В поиске hh.ru: search_period={search_period} (фильтр на стороне сайта)")
        if role_filter_enabled(search):
            resume_roles, extra_roles = build_allowed_roles(resume_title, vacancy_preferences)
            min_d = int(search.get("role_duties_min_percent") or 40)
            roles_txt = ", ".join(resume_roles) if resume_roles else "не задано"
            if extra_roles:
                roles_txt += f" + {', '.join(extra_roles)} (пожелания)"
            log(f"Режим «По роли»: {roles_txt}; иначе обязанности ≥{min_d}%")
        if min_salary > 0:
            log(f"Мин. зарплата: от {min_salary // 1000}k ₽")
        log(f"Буду проверять все {len(vacancy_ids)} вакансий по одной (цель: {max_apps} откликов).")
        log("Оценка — в фоне, окно браузера не трогаем.")

        meth = str(cfg.get("paths", {}).get("match_methodology", "context/match-methodology.md"))
        root = Path(cfg.get("_root", "."))
        meth_path = str(root / meth) if not Path(meth).is_absolute() else meth

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            disable=bool(log_fn),
        ) as progress:
            task = progress.add_task("Обработка вакансий...", total=len(vacancy_ids))

            for vid in vacancy_ids:
                progress.advance(task)
                processed += 1
                if should_stop and should_stop():
                    log("Остановлено пользователем.")
                    break
                if sent_count >= max_apps:
                    log(f"Достигнут лимит {max_apps} откликов.")
                    break

                url = f"https://hh.ru/vacancy/{vid}"
                vac = Vacancy(id=vid, title="", company="", url=url)
                await asyncio.sleep(random.uniform(READ_DELAY_MIN_SEC, READ_DELAY_MAX_SEC))
                html_text, err = await session.fetch_vacancy_html(vid, should_stop=should_stop)
                if err == "captcha":
                    consecutive_captcha += 1
                    log("  пропуск: капча hh.ru — пройди в Chromium; иду к следующей вакансии")
                    report.skipped.append(
                        ApplicationResult(
                            vacancy=Vacancy(id=vid, title="", company="", url=url),
                            status="skipped",
                            reason="капча hh.ru",
                        )
                    )
                    if consecutive_captcha >= 5:
                        log(
                            "Капча на hh.ru подряд — дальше смысла нет. "
                            "Пройди «не робот» в окне Chromium и запусти снова."
                        )
                        break
                    continue
                parsed = session.parse_vacancy_from_html(html_text or "")
                if parsed and is_robot_check_title(parsed.get("name", "")):
                    consecutive_captcha += 1
                    log("  пропуск: капча hh.ru (страница «не робот»)")
                    report.skipped.append(
                        ApplicationResult(
                            vacancy=Vacancy(id=vid, title="", company="", url=url),
                            status="skipped",
                            reason="капча hh.ru",
                        )
                    )
                    if consecutive_captcha >= 5:
                        log(
                            "Капча на hh.ru подряд — дальше смысла нет. "
                            "Пройди «не робот» в окне Chromium и запусти снова."
                        )
                        break
                    continue
                consecutive_captcha = 0
                if not parsed:
                    if err and err != "остановка":
                        log(f"→ {vid}: пропуск ({err})")
                        report.skipped.append(
                            ApplicationResult(
                                vacancy=Vacancy(id=vid, title="", company="", url=url),
                                status="skipped",
                                reason=err or "не удалось прочитать",
                            )
                        )
                    continue

                vac.title = parsed.get("name", "")
                vac.company = parsed.get("company", "")
                vac.description = parsed.get("description", "")
                vac.compensation = parsed.get("compensation")
                vac.salary = _compensation_str(parsed.get("compensation"))
                vac.published_at = pick_newer_published_at(
                    parsed.get("published_at"),
                    search_publication_map.get(vid),
                )

                log(f"→ {vid}: {(vac.title or '')[:55]}")
                if processed % 50 == 0:
                    log(f"  … проверено {processed}/{len(vacancy_ids)}, отправлено {sent_count}/{max_apps}")

                sim_key = vacancy_similarity_key(vac)
                if sim_key in seen_similar and seen_similar[sim_key] != vid:
                    log(
                        f"  похожая вакансия (как {seen_similar[sim_key]}), "
                        f"откликаемся — может быть другой HR"
                    )
                else:
                    seen_similar[sim_key] = vid

                ok, skip_reason = pre_filter_vacancy(
                    vac,
                    cfg,
                    applied_ids,
                    company_counts,
                    profile_text=profile_md,
                    resume_title=resume_title,
                )
                if not ok:
                    log(f"  пропуск: {skip_reason}")
                    report.skipped.append(ApplicationResult(vacancy=vac, status="skipped", reason=skip_reason))
                    continue

                score, reason = rule_match_score(vac.title, vac.description, profile_md)
                requires_test = detect_requires_test(vac.title, vac.description)
                eval_result = None

                if llm:
                    if score < min_match - RULE_SKIP_MARGIN:
                        reason = f"rule {score}% (быстрый пропуск)"
                        log(f"  match: {score}% — {reason}")
                    else:
                        log(f"  match: оценка «{(vac.title or '')[:50]}»…")
                        try:
                            eval_result = await asyncio.to_thread(
                                score_match_llm,
                                llm,
                                profile_md,
                                vac.title,
                                vac.company,
                                vac.description,
                                meth_path,
                                match_settings,
                                vacancy_preferences,
                                min_match,
                            )
                            score = eval_result.score
                            reason = eval_result.reason
                            log(f"  match: {score}% — {reason[:80]}")
                            if eval_result.must_gaps:
                                reason = f"{reason}; must: {', '.join(eval_result.must_gaps[:3])}"
                            requires_test = requires_test or eval_result.requires_test
                        except Exception as e:
                            log(f"  LLM score failed: {e}")
                elif llm is None:
                    reason = f"rule {score}% (LLM недоступен)"

                try:
                    if requires_test:
                        log(f"  ⚠ в описании упомянут тест — попробую откликнуться сам")

                    if eval_result and eval_result.blocked:
                        log(f"  пропуск: blocker ({reason})")
                        report.skipped.append(
                            ApplicationResult(vacancy=vac, status="skipped", reason=f"blocker ({reason})")
                        )
                        continue

                    if score < min_match:
                        log(f"  пропуск: match {score}% < {min_match}% ({reason})")
                        report.skipped.append(
                            ApplicationResult(
                                vacancy=vac, status="skipped", reason=f"match <{min_match}% ({score}%)"
                            )
                        )
                        continue

                    vac.match_percent = score
                    vac.match_reason = reason
                    if requires_test:
                        vac.match_reason = (vac.match_reason + "; тест/опрос").strip("; ")

                    if letter_template:
                        letter = render_letter_template(
                            letter_template,
                            vac.title,
                            vac.company,
                            profile_cfg.get("signature", ""),
                        )
                    elif llm:
                        try:
                            letter = generate_letter_llm(
                                llm,
                                profile_md,
                                style_md,
                                vac.title,
                                vac.company,
                                vac.description,
                                profile_cfg.get("salary_expectation", ""),
                                profile_cfg.get("signature", ""),
                            )
                        except Exception as e:
                            report.manual.append(
                                ApplicationResult(
                                    vacancy=vac,
                                    status="manual",
                                    reason="ошибка LLM",
                                    notes=str(e),
                                )
                            )
                            continue
                    else:
                        letter = (
                            f"здравствуйте, интересна роль {vac.title} в {vac.company}. "
                            f"опыт enterprise B2B product и AI/LLM — 3+ года. готов обсудить. "
                            f"{profile_cfg.get('signature', '')}"
                        )

                    if dry_run:
                        sent_count += 1
                        log(f"[dry-run] {vac.title} @ {vac.company} ({score}%)")
                        report.sent.append(
                            ApplicationResult(vacancy=vac, status="dry_run", reason="dry-run", letter=letter)
                        )
                        continue

                    log(f"Отклик: {vac.title} @ {vac.company} ({score}%)…")
                    status, apply_note = await session.apply_to_vacancy(vid, resume_hash, letter)

                    if status == "sent":
                        sent_count += 1
                        applied_ids.add(vid)
                        key = normalize_company(vac.company)
                        company_counts[key] = company_counts.get(key, 0) + 1
                        note = f" ({apply_note})" if apply_note else ""
                        report.sent.append(ApplicationResult(vacancy=vac, status="sent", letter=letter))
                        log(f"✓ Отправлено ({sent_count}/{max_apps}){note}")

                        if sent_count < max_apps:
                            delay = random.uniform(DELAY_MIN_SEC, DELAY_MAX_SEC)
                            if delay >= 60:
                                log(f"Пауза {int(delay // 60)}м {int(delay % 60)}с до следующего отклика…")
                            else:
                                log(f"Пауза {int(delay)}с до следующего отклика…")
                            await asyncio.sleep(delay)
                    elif status == "manual":
                        log(f"⚠ Вручную: {apply_note} — {vac.title}")
                        report.manual.append(
                            ApplicationResult(vacancy=vac, status="manual", reason=apply_note, letter=letter)
                        )
                    else:
                        report.skipped.append(
                            ApplicationResult(vacancy=vac, status="skipped", reason=apply_note)
                        )
                except Exception as e:
                    log(f"  ОШИБКА на {vid}: {e} — пропускаю, продолжаю прогон")
                    report.skipped.append(
                        ApplicationResult(vacancy=vac, status="skipped", reason=f"ошибка: {e}")
                    )
                    if session.page and session.page.is_closed():
                        log("  Вкладка hh.ru закрыта — восстанавливаю…")
                        await session._ensure_work_page()

        reports_dir = Path(cfg["paths"]["reports_dir"])
        out = write_excel_report(report, reports_dir)
        log(f"Отчёт: {out}")
        log(
            f"Итог: отправлено {len(report.sent)}, вручную {len(report.manual)}, "
            f"пропущено {len(report.skipped)} (просмотрено {processed})"
        )
        return report
    finally:
        await session.close()


def run_sync(
    cfg_path: Path | None = None,
    dry_run: bool = False,
    max_applications: int | None = None,
    log_fn: Callable[[str], None] | None = None,
    browser_context: BrowserContext | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RunReport:
    return asyncio.run(
        run_jobhunt(
            cfg_path,
            dry_run=dry_run,
            max_applications=max_applications,
            log_fn=log_fn,
            browser_context=browser_context,
            should_stop=should_stop,
        )
    )
