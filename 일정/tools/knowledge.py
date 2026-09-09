"""지식 조회 — 한국어 위키백과에서 찾아 읽는다 (키 없음).

검색 엔진 검색은 아닙니다. 인물·회사·용어·제도처럼 '사전에 있을 법한 것'을
확인할 때 씁니다. 최신 소식은 news 도구를 쓰세요.
"""
from __future__ import annotations

from core.net import NetError, fetch_json
from core.registry import tool

API = "https://ko.wikipedia.org/w/api.php"
API_EN = "https://en.wikipedia.org/w/api.php"


def _search(api: str, query: str, count: int) -> list:
    data = fetch_json(api, {
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": count, "format": "json", "utf8": 1,
    })
    return ((data.get("query") or {}).get("search") or [])


def _extract(api: str, title: str, limit: int) -> str:
    data = fetch_json(api, {
        "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
        "redirects": 1, "titles": title, "format": "json", "utf8": 1,
    })
    pages = ((data.get("query") or {}).get("pages") or {})
    for page in pages.values():
        text = (page.get("extract") or "").strip()
        if text:
            return text[:limit] + ("\n…(이하 생략)" if len(text) > limit else "")
    return ""


@tool(
    name="search_wikipedia",
    description=(
        "위키백과에서 찾아본다. 인물·회사·제도·용어처럼 사전에 있을 법한 것을 "
        "확인할 때 쓴다. 최신 뉴스는 news 도구를, 특정 주소를 읽을 땐 read_webpage 를 써라. "
        "여기서 읽은 내용만 근거로 말하고 지어내지 마라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "찾을 말. 예: 부가가치세, 하이웍스"},
            "limit": {"type": "integer", "description": "본문을 몇 글자까지. 기본 1200"},
        },
        "required": ["query"],
    },
)
def search_wikipedia(query: str, limit: int = 1200) -> str:
    query = (query or "").strip()
    if not query:
        return "찾을 말이 없습니다."
    limit = max(200, min(int(limit or 1200), 5000))

    try:
        hits = _search(API, query, 5)
        api = API
        if not hits:  # 한국어에 없으면 영문판도 본다
            hits = _search(API_EN, query, 5)
            api = API_EN
    except NetError as e:
        return "위키백과를 읽지 못했습니다: {}".format(e)

    if not hits:
        return ("'{}' 에 대한 문서를 찾지 못했습니다. 이 도구를 다시 부르지 말고 "
                "news 나 read_webpage 로 알아보거나 사용자에게 물어보세요.").format(query)

    title = hits[0].get("title", "")
    try:
        body = _extract(api, title, limit)
    except NetError as e:
        return "문서를 읽지 못했습니다: {}".format(e)

    others = ", ".join(h.get("title", "") for h in hits[1:4])
    rows = ["[{}] (위키백과)".format(title), "", body or "(본문이 비어 있습니다)"]
    if others:
        rows += ["", "비슷한 문서: " + others]
    return "\n".join(rows)
