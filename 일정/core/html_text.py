"""HTML → 읽을 수 있는 글자. 메일 본문과 웹페이지가 같이 쓴다."""
from __future__ import annotations

import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")

_ENTITIES = (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
             ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"))


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|section|article)>", "\n", text)
    text = _TAG.sub(" ", text)
    for a, b in _ENTITIES:
        text = text.replace(a, b)
    text = re.sub(r"&#(\d{1,6});", lambda m: _chr(m.group(1)), text)
    return text


def tidy(text: str, limit: int = 0) -> str:
    """줄바꿈·공백을 정리하고 필요하면 자른다."""
    text = _NL.sub("\n\n", _WS.sub(" ", text or "")).strip()
    if limit and len(text) > limit:
        text = text[:limit] + "\n…(이하 생략)"
    return text


def _chr(number: str) -> str:
    try:
        return chr(int(number))
    except (ValueError, OverflowError):
        return ""
