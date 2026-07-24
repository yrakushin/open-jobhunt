from __future__ import annotations


def strip_template_docs(text: str) -> str:
    """Убрать пояснения после разделителя --- (в *.example.md)."""
    if "\n---\n" in text:
        return text.split("\n---\n", 1)[0].strip()
    return text.strip()


def render_letter_template(template: str, title: str, company: str, signature: str = "") -> str:
    text = strip_template_docs(template)
    for key, val in (
        ("{title}", title),
        ("{company}", company),
        ("{signature}", signature),
        ("{должность}", title),
        ("{компания}", company),
    ):
        text = text.replace(key, val)
    if signature and signature not in text:
        text = text.rstrip() + f"\n\n{signature}"
    return text.strip()
