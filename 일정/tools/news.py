"""뉴스 도구 — 구글 뉴스 RSS 를 읽는다. 가입도 API 키도 필요 없다.

요약과 판단은 비서(모델)가 한다. 여기서는 제목·언론사·시각·링크만 준다.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from core import context
from core.html_text import html_to_text, tidy
from core.net import NetError, fetch
from core.registry import tool

TOP_URL = "https://news.google.com/rss"
SEARCH_URL = "https://news.google.com/rss/search"

# 구글 뉴스 제목은 '제목 - 언론사' 모양이라 뒤쪽을 떼어 언론사로 쓴다
_TAIL = re.compile(r"\s+-\s+([^-]{2,20})$")

TOPICS = {
    "경제": "BUSINESS", "비즈니스": "BUSINESS", "산업": "BUSINESS",
    "정치": "NATION", "사회": "NATION", "국내": "NATION",
    "세계": "WORLD", "국제": "WORLD", "해외": "WORLD",
    "IT": "TECHNOLOGY", "기술": "TECHNOLOGY", "과학": "SCIENCE",
    "스포츠": "SPORTS", "연예": "ENTERTAINMENT", "건강": "HEALTH",
}


def _locale() -> dict:
    return {"hl": "ko", "gl": "KR", "ceid": "KR:ko"}


def _when(value: str) -> str:
    try:
        moment = parsedate_to_datetime(value).astimezone(context.cfg.tz)
    except (TypeError, ValueError):
        return ""
    gap = datetime.now(context.cfg.tz) - moment
    if gap < timedelta(hours=1):
        return "{}분 전".format(max(1, int(gap.total_seconds() // 60)))
    if gap < timedelta(days=1):
        return "{}시간 전".format(int(gap.total_seconds() // 3600))
    return moment.strftime("%m/%d %H:%M")


def _items(xml_text: str, count: int) -> list:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise NetError("뉴스 목록을 해석하지 못했습니다: {}".format(e))

    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        source = (item.findtext("source") or "").strip()
        if not source:
            found = _TAIL.search(title)
            if found:
                source = found.group(1).strip()
                title = title[:found.start()].strip()
        out.append({
            "title": title,
            "source": source,
            "when": _when(item.findtext("pubDate") or ""),
            "link": (item.findtext("link") or "").strip(),
            "summary": tidy(html_to_text(item.findtext("description") or ""), 200),
        })
        if len(out) >= count:
            break
    return out


def _render(items: list, header: str) -> str:
    if not items:
        return header + "\n(가져온 기사가 없습니다)"
    rows = [header, ""]
    for i, it in enumerate(items, 1):
        mark = " · ".join(x for x in (it["source"], it["when"]) if x)
        rows.append("{}. {}".format(i, it["title"]))
        if mark:
            rows.append("   {}".format(mark))
        if it["link"]:
            rows.append("   {}".format(it["link"]))
    return "\n".join(rows)


@tool(
    name="news",
    description=(
        "뉴스 헤드라인을 가져온다. '오늘 뉴스 뭐 있어?', '반도체 관련 뉴스 찾아줘', "
        "'경제 뉴스 정리해줘' 같은 요청에 쓴다. 제목과 언론사만 오므로 "
        "본문이 필요하면 read_webpage 로 링크를 열어라. 없는 기사를 지어내지 마라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string",
                        "description": "찾을 주제어. 예: 반도체, 금리, 우리 회사 이름. 없으면 빈 문자열"},
            "category": {"type": "string",
                         "description": "분야. 경제/정치/세계/IT/과학/스포츠/연예/건강 중 하나. 없으면 빈 문자열"},
            "count": {"type": "integer", "description": "몇 건까지. 기본 8, 최대 20"},
        },
        "required": [],
    },
)
def news(keyword: str = "", category: str = "", count: int = 8) -> str:
    keyword = (keyword or "").strip()
    category = (category or "").strip()
    count = max(1, min(int(count or 8), 20))

    params = _locale()
    if keyword:
        url = SEARCH_URL
        params["q"] = keyword
        header = "'{}' 뉴스".format(keyword)
    elif category:
        topic = TOPICS.get(category) or TOPICS.get(category.upper())
        if not topic:
            return ("'{}' 분야는 없습니다. 이 중에서 고르세요: {}"
                    .format(category, ", ".join(sorted(set(TOPICS)))))
        url = TOP_URL + "/headlines/section/topic/" + topic
        header = "{} 뉴스".format(category)
    else:
        url = TOP_URL
        header = "주요 뉴스"

    try:
        items = _items(fetch(url, params), count)
    except NetError as e:
        return "뉴스를 가져오지 못했습니다: {}".format(e)

    return _render(items, "{} {}건".format(header, len(items)))
