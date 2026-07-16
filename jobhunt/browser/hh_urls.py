from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def parse_resume_hash_from_url(url: str) -> str | None:
    m = re.search(r"[?&]resume=([a-f0-9]{20,})", url, re.I)
    return m.group(1) if m else None


def build_paged_search_url(
    base_url: str,
    page: int,
    per_page: int,
    area: int | None = None,
    search_period: int | None = None,
) -> str:
    parsed = urlparse(base_url.strip())
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["page"] = [str(page)]
    qs["items_on_page"] = [str(per_page)]
    if area is not None and "area" not in qs:
        qs["area"] = [str(area)]
    if search_period is not None:
        if search_period > 0:
            qs["search_period"] = [str(search_period)]
        else:
            qs.pop("search_period", None)
    pairs: list[tuple[str, str]] = []
    for key, vals in qs.items():
        for val in vals:
            pairs.append((key, val))
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(pairs), parsed.fragment)
    )
