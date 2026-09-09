"""주가 조회 — 야후 파이낸스의 공개 시세를 읽는다 (키 없음).

실시간이 아니라 지연 시세입니다. 매매 판단에 쓰지 말라고 답에 함께 적습니다.
국내 종목은 종목코드에 .KS(코스피) / .KQ(코스닥) 를 붙여 조회합니다.
"""
from __future__ import annotations

import re
from datetime import datetime

from core import context
from core.net import NetError, fetch_json
from core.registry import tool

URL = "https://query1.finance.yahoo.com/v8/finance/chart/"

# 자주 찾는 국내 종목 (그 외에는 종목코드를 직접 받는다)
KNOWN = {
    "삼성전자": "005930.KS", "삼성전자우": "005935.KS",
    "SK하이닉스": "000660.KS", "하이닉스": "000660.KS",
    "LG에너지솔루션": "373220.KS", "삼성바이오로직스": "207940.KS",
    "현대차": "005380.KS", "기아": "000270.KS", "네이버": "035420.KS",
    "NAVER": "035420.KS", "카카오": "035720.KS", "셀트리온": "068270.KS",
    "포스코홀딩스": "005490.KS", "LG화학": "051910.KS", "삼성SDI": "006400.KS",
    "KB금융": "105560.KS", "신한지주": "055550.KS", "한화에어로스페이스": "012450.KS",
    "코스피": "^KS11", "코스닥": "^KQ11", "나스닥": "^IXIC", "S&P500": "^GSPC",
    "다우": "^DJI", "니케이": "^N225",
    "애플": "AAPL", "테슬라": "TSLA", "엔비디아": "NVDA", "마이크로소프트": "MSFT",
    "구글": "GOOGL", "아마존": "AMZN", "메타": "META",
}


def _symbol(name: str) -> str:
    name = (name or "").strip()
    if name in KNOWN:
        return KNOWN[name]
    squashed = name.replace(" ", "")
    if squashed in KNOWN:
        return KNOWN[squashed]
    if re.fullmatch(r"\d{6}", squashed):     # 005930 → 코스피로 먼저 시도
        return squashed + ".KS"
    return squashed.upper()


def _money(value, currency: str) -> str:
    if value is None:
        return "?"
    if currency == "KRW":
        return "{:,.0f}원".format(value)
    return "{:,.2f} {}".format(value, currency or "")


@tool(
    name="stock_price",
    description=(
        "주가나 지수를 알려준다. '삼성전자 주가 얼마야?', '코스피 지금 몇이야?' 같은 "
        "요청에 쓴다. 지연 시세이므로 받은 숫자와 시각만 말하고 전망을 지어내지 마라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string",
                       "description": "종목 이름이나 코드. 예: 삼성전자, 005930, 코스피, AAPL"},
        },
        "required": ["symbol"],
    },
)
def stock_price(symbol: str) -> str:
    asked = (symbol or "").strip()
    if not asked:
        return "종목 이름이나 코드가 없습니다."
    code = _symbol(asked)

    def _look(ticker: str):
        data = fetch_json(URL + ticker, {"interval": "1d", "range": "5d"})
        results = ((data.get("chart") or {}).get("result") or [])
        return (results[0].get("meta") or {}) if results else {}

    try:
        meta = _look(code)
        if not meta and code.endswith(".KS"):   # 코스피에 없으면 코스닥
            code = code[:-3] + ".KQ"
            meta = _look(code)
    except NetError as e:
        return "주가를 가져오지 못했습니다: {}".format(e)

    price = meta.get("regularMarketPrice")
    if price is None:
        return ("'{}' 종목을 찾지 못했습니다. 이 도구를 다시 부르지 말고 종목코드"
                "(예: 005930)나 정확한 이름을 사용자에게 물어보세요.").format(asked)

    currency = meta.get("currency", "")
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    rows = ["{} ({})".format(meta.get("longName") or asked, meta.get("symbol") or code),
            "현재가: " + _money(price, currency)]

    if previous:
        diff = price - previous
        rate = diff / previous * 100 if previous else 0
        arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "―")
        rows.append("전일대비: {} {} ({:+.2f}%)".format(arrow, _money(abs(diff), currency), rate))

    stamp = meta.get("regularMarketTime")
    if stamp:
        try:
            when = datetime.fromtimestamp(int(stamp), context.cfg.tz)
            rows.append("기준: {} (한국시간)".format(when.strftime("%Y-%m-%d %H:%M")))
        except (ValueError, OSError, OverflowError):
            pass
    rows.append("(지연 시세입니다. 매매 판단에 쓰지 마세요)")
    return "\n".join(rows)
