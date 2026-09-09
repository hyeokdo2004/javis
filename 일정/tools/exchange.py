"""환율 도구 — open.er-api.com 의 무료 공개 환율을 쓴다 (키 없음).

기준 통화 1단위가 얼마인지 하루 단위로 갱신되는 값입니다.
은행 고시 환율과는 조금 다를 수 있어서, 그 점을 함께 알려줍니다.
"""
from __future__ import annotations

from core.net import NetError, fetch_json
from core.registry import tool

URL = "https://open.er-api.com/v6/latest/"

NAMES = {
    "KRW": "원", "USD": "미국 달러", "JPY": "일본 엔", "EUR": "유로",
    "CNY": "중국 위안", "GBP": "영국 파운드", "AUD": "호주 달러",
    "CAD": "캐나다 달러", "HKD": "홍콩 달러", "TWD": "대만 달러",
    "SGD": "싱가포르 달러", "VND": "베트남 동", "THB": "태국 바트",
}

# 사람들이 부르는 말 → 통화 코드
ALIASES = {
    "원": "KRW", "원화": "KRW", "한국": "KRW", "한국돈": "KRW",
    "달러": "USD", "미국": "USD", "미국달러": "USD", "불": "USD",
    "엔": "JPY", "엔화": "JPY", "일본": "JPY", "일본돈": "JPY",
    "유로": "EUR", "유럽": "EUR",
    "위안": "CNY", "중국": "CNY", "인민폐": "CNY",
    "파운드": "GBP", "영국": "GBP",
    "호주달러": "AUD", "호주": "AUD", "캐나다": "CAD",
    "홍콩": "HKD", "대만": "TWD", "싱가포르": "SGD",
    "베트남": "VND", "동": "VND", "태국": "THB", "바트": "THB",
}


def _code(word: str, default: str) -> str:
    word = (word or "").strip()
    if not word:
        return default
    if len(word) == 3 and word.isascii():
        return word.upper()
    return ALIASES.get(word, ALIASES.get(word.replace(" ", ""), word.upper()))


def _label(code: str) -> str:
    name = NAMES.get(code)
    return "{}({})".format(code, name) if name else code


def _num(value: float) -> str:
    """1300.5 → 1,300.5 / 0.00089 → 0.00089"""
    if value >= 100:
        return "{:,.1f}".format(value)
    if value >= 1:
        return "{:,.2f}".format(value)
    return "{:.5f}".format(value)


@tool(
    name="exchange_rate",
    description=(
        "환율을 알려준다. '오늘 환율 얼마야?', '100달러면 몇 원이야?', "
        "'엔화 환율' 같은 요청에 쓴다. 여기서 받은 숫자만 말하고 지어내지 마라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "base": {"type": "string",
                     "description": "바꿀 돈. 달러/엔/유로 또는 USD, JPY 같은 코드. 기본 USD"},
            "target": {"type": "string", "description": "받을 돈. 기본 KRW(원)"},
            "amount": {"type": "number", "description": "금액. 기본 1"},
        },
        "required": [],
    },
)
def exchange_rate(base: str = "USD", target: str = "KRW", amount: float = 1) -> str:
    base_code = _code(base, "USD")
    target_code = _code(target, "KRW")
    try:
        amount = float(amount or 1)
    except (TypeError, ValueError):
        amount = 1.0

    try:
        data = fetch_json(URL + base_code)
    except NetError as e:
        return "환율을 가져오지 못했습니다: {}".format(e)

    if data.get("result") != "success":
        return ("'{}' 는 알 수 없는 통화입니다. 달러·엔·유로·위안 처럼 부르거나 "
                "USD, JPY 같은 세 글자 코드를 쓰세요.").format(base)

    rate = (data.get("rates") or {}).get(target_code)
    if rate is None:
        return ("'{}' 는 알 수 없는 통화입니다. 달러·엔·유로·위안 처럼 부르거나 "
                "USD, JPY 같은 세 글자 코드를 쓰세요.").format(target)

    rows = ["1 {} = {} {}".format(_label(base_code), _num(rate), _label(target_code))]
    if amount != 1:
        rows.append("{} {} = {} {}".format(
            _num(amount), base_code, _num(amount * rate), target_code))
    rows.append("기준 시각: {}".format(data.get("time_last_update_utc", "?")))
    rows.append("(시장 참고용 환율입니다. 은행 고시·송금 환율과는 차이가 있습니다)")
    return "\n".join(rows)
