from __future__ import annotations

import html as htmlmod
import json
import re

from datetime import datetime

from jobhunt.core.vacancy_age import extract_published_at


def _lux_json(html_text: str) -> dict | None:
    if not html_text or "HH-Lux-InitialState" not in html_text:
        return None
    m = re.search(r'id="HH-Lux-InitialState"[^>]*>([^<]+)', html_text)
    if not m:
        return None
    try:
        return json.loads(htmlmod.unescape(m.group(1)))
    except json.JSONDecodeError:
        return None


def is_robot_check_title(title: str) -> bool:
    t = (title or "").lower().strip()
    if not t:
        return False
    if "не робот" in t or "not a robot" in t:
        return True
    return "подтвердите" in t and "робот" in t


def is_captcha_url(url: str) -> bool:
    return "/account/captcha" in (url or "").lower()


def is_captcha_html(html_text: str) -> bool:
    if not html_text:
        return False
    low = html_text.lower()
    # Только реальная страница капчи, не строки "error.signup.captcha" в JSON
    if "/account/captcha" in low:
        return True
    if "account-captcha" in low or 'data-qa="account-captcha"' in low:
        return True
    m = re.search(r"<title[^>]*>([^<]+)", html_text, re.I)
    if m and ("captcha" in m.group(1).lower() or is_robot_check_title(m.group(1))):
        return True
    m = re.search(r'property="og:title"[^>]+content="([^"]+)"', html_text, re.I)
    if m and is_robot_check_title(m.group(1)):
        return True
    return False


def is_captcha_state(html_text: str, url: str = "") -> bool:
    if is_captcha_url(url):
        return True
    return is_captcha_html(html_text)


def html_has_search_results(html_text: str) -> bool:
    return len(extract_vacancy_ids_from_html(html_text)) > 0


def html_has_vacancy(html_text: str) -> bool:
    data = _lux_json(html_text)
    if not data:
        return False
    v = data.get("vacancyView") or data.get("vacancy")
    return isinstance(v, dict) and bool(v.get("name"))


def _find_vacancy_dict(data: dict | None, depth: int = 0) -> dict | None:
    if not data or depth > 8:
        return None
    for key in ("vacancyView", "vacancy", "vacancyInfo", "initialVacancy"):
        v = data.get(key)
        if isinstance(v, dict) and (v.get("name") or v.get("title")):
            return v
    for val in data.values():
        if isinstance(val, dict):
            found = _find_vacancy_dict(val, depth + 1)
            if found:
                return found
        elif isinstance(val, list):
            for item in val[:30]:
                if isinstance(item, dict):
                    found = _find_vacancy_dict(item, depth + 1)
                    if found:
                        return found
    return None


def _parse_vacancy_dom(html_text: str) -> dict | None:
    if not html_text:
        return None
    title = ""
    for pat in (
        r'data-qa="vacancy-title"[^>]*>([^<]+)',
        r'<h1[^>]*data-qa="vacancy-title"[^>]*>([^<]+)',
        r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
    ):
        m = re.search(pat, html_text, re.I | re.S)
        if m:
            title = htmlmod.unescape(re.sub(r"\s+", " ", m.group(1)).strip())
            title = re.sub(r"\s*[-|–].*hh\.ru.*$", "", title, flags=re.I).strip()
            if title and len(title) > 2:
                break
    if not title:
        return None
    if is_robot_check_title(title):
        return None
    company = ""
    m = re.search(r'data-qa="vacancy-company-name"[^>]*>([^<]+)', html_text, re.I)
    if m:
        company = htmlmod.unescape(m.group(1).strip())
    desc = ""
    m = re.search(r'data-qa="vacancy-description"[^>]*>([\s\S]*?)</div>', html_text, re.I)
    if m:
        desc = htmlmod.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
        desc = re.sub(r"\s+", " ", desc).strip()
    return {
        "name": title,
        "company": company,
        "description": desc,
        "compensation": None,
    }


def parse_vacancy_html(html_text: str) -> dict | None:
    data = _lux_json(html_text)
    v = None
    if data:
        v = data.get("vacancyView") or data.get("vacancy")
        if not isinstance(v, dict) or not (v.get("name") or v.get("title")):
            v = _find_vacancy_dict(data)
    if not isinstance(v, dict) or not (v.get("name") or v.get("title")):
        dom = _parse_vacancy_dom(html_text)
        if dom and dom.get("name"):
            return dom
        return None
    comp = v.get("company") or v.get("employer") or {}
    if isinstance(comp, str):
        company_name = comp
    else:
        company_name = comp.get("name") or comp.get("visibleName") or ""
    desc = v.get("description") or ""
    branded = v.get("brandedDescription")
    if not desc and isinstance(branded, dict):
        desc = branded.get("text") or ""
    published_at = extract_published_at(v)
    name = v.get("name") or v.get("title") or ""
    if is_robot_check_title(name):
        return None
    return {
        "name": name,
        "company": company_name,
        "description": desc,
        "compensation": v.get("compensation"),
        "published_at": published_at,
    }


def _negotiations_list(html_text: str) -> list[dict]:
    data = _lux_json(html_text)
    if not data:
        return []
    lst = (data.get("applicantNegotiations") or {}).get("topicList") or []
    return lst if isinstance(lst, list) else []


def html_usable_for_url(url: str, html_text: str) -> bool:
    if not html_text or is_captcha_state(html_text, url):
        return False
    if "/vacancy/" in url and re.search(r"/vacancy/\d+", url):
        return parse_vacancy_html(html_text) is not None
    if "search/vacancy" in url:
        return len(extract_vacancy_ids_from_html(html_text)) > 0
    if "negotiations" in url:
        return bool(_negotiations_list(html_text)) or "applicantNegotiations" in html_text
    return len(html_text) > 5000


def extract_negotiation_vacancy_ids(html_text: str) -> list[str]:
    return [str(x["vacancyId"]) for x in _negotiations_list(html_text) if x.get("vacancyId")]


def extract_negotiation_employers(html_text: str) -> list[str]:
    names: list[str] = []
    for item in _negotiations_list(html_text):
        name = item.get("employerName") or item.get("companyName") or ""
        if name:
            names.append(str(name))
    return names


def _vacancy_id_from_search_item(item: dict) -> str | None:
    vid = item.get("vacancyId") or item.get("id")
    if not vid and isinstance(item.get("vacancy"), dict):
        vid = item["vacancy"].get("id")
    return str(vid) if vid else None


def _search_vacancy_lists(data: dict) -> list[list]:
    lists = [
        (data.get("vacancySearchResult") or {}).get("vacancies"),
        (data.get("vacancySearchResult") or {}).get("items"),
        (data.get("results") or {}).get("vacancies"),
        (data.get("applicantVacancies") or {}).get("vacancies"),
    ]
    return [lst for lst in lists if isinstance(lst, list)]


def extract_search_publication_map(html_text: str) -> dict[str, datetime]:
    """Даты публикации из выдачи поиска (до открытия карточки вакансии)."""
    data = _lux_json(html_text)
    out: dict[str, datetime] = {}
    if not data:
        return out
    for lst in _search_vacancy_lists(data):
        for item in lst:
            if not isinstance(item, dict):
                continue
            vid = _vacancy_id_from_search_item(item)
            if not vid:
                continue
            published = extract_published_at(item)
            if published:
                out[vid] = published
    return out


def extract_vacancy_ids_from_html(html_text: str) -> list[str]:
    data = _lux_json(html_text)
    ids: set[str] = set()
    if data:
        for lst in _search_vacancy_lists(data):
            for item in lst:
                if not isinstance(item, dict):
                    continue
                vid = _vacancy_id_from_search_item(item)
                if vid:
                    ids.add(vid)
    for m in re.finditer(r"/vacancy/(\d+)", html_text or ""):
        ids.add(m.group(1))
    return list(ids)
