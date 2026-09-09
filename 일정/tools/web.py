"""웹페이지 읽기 — 주소를 받아 본문 글자만 뽑아준다.

뉴스 기사 본문 확인처럼 '링크를 열어 읽어야 하는' 일에 씁니다.
검색은 못 합니다. 주소를 알고 있을 때만 씁니다.
"""
from __future__ import annotations

import re

from core.html_text import html_to_text, tidy
from core.net import NetError, fetch
from core.registry import tool

_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")


@tool(
    name="read_webpage",
    description=(
        "웹페이지 주소를 열어 본문 글자를 읽어온다. 뉴스 기사 전문을 확인하거나 "
        "사용자가 준 링크 내용을 볼 때 쓴다. 검색은 못 하니 주소를 알 때만 써라. "
        "읽어온 내용만 근거로 말하고 지어내지 마라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http 또는 https 로 시작하는 주소"},
            "limit": {"type": "integer", "description": "가져올 글자 수. 기본 3000, 최대 12000"},
        },
        "required": ["url"],
    },
)
def read_webpage(url: str, limit: int = 3000) -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "주소는 http:// 또는 https:// 로 시작해야 합니다: {}".format(url)
    limit = max(500, min(int(limit or 3000), 12000))

    try:
        html = fetch(url, timeout=25)
    except NetError as e:
        return "페이지를 읽지 못했습니다: {}".format(e)

    found = _TITLE.search(html)
    title = tidy(html_to_text(found.group(1))) if found else ""
    body = tidy(html_to_text(html), limit)
    if not body:
        return ("글자를 찾지 못했습니다. 자바스크립트로 그리는 페이지이거나 "
                "로그인이 필요한 곳일 수 있습니다: {}".format(url))

    head = "[{}]\n{}".format(title, url) if title else url
    return head + "\n\n" + body
