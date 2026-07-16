from __future__ import annotations

import io
import re
from pathlib import Path


ALLOWED_SUFFIXES = {".md", ".txt", ".pdf"}


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"Формат не поддерживается. Загрузите: {', '.join(sorted(ALLOWED_SUFFIXES))}")

    if suffix in (".md", ".txt"):
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
        if len(text) < 80:
            raise ValueError("Не удалось извлечь текст из PDF — попробуйте .md или .txt")
        return text

    raise ValueError("Неподдерживаемый формат")


def guess_resume_title(text: str, filename: str = "", fallback: str = "") -> str:
    for line in text.splitlines()[:25]:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            if title:
                return title
        m = re.match(r"(?:должность|position|title|роль|резюме)\s*[:：]\s*(.+)", line, re.I)
        if m:
            return m.group(1).strip()

    if filename:
        stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
        if stem:
            return stem

    return fallback
