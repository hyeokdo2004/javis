"""날씨 도구 — Open-Meteo 공개 API 를 쓴다.

가입도 API 키도 필요 없고, 표준 라이브러리만 씁니다.
  · 지명 → 좌표 :  geocoding-api.open-meteo.com
  · 날씨      :  api.open-meteo.com
기본 도시는 config.json 의 weather.default_city 로 바꿉니다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from core import context
from core.registry import tool

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO 날씨 코드 → 사람 말
CODES = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "서리 안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비",
    56: "얼어붙는 약한 이슬비", 57: "얼어붙는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비",
    66: "얼어붙는 약한 비", 67: "얼어붙는 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기",
    85: "약한 소낙눈", 86: "소낙눈",
    95: "천둥번개", 96: "우박 동반 천둥번개", 99: "강한 우박 동반 천둥번개",
}


def _sky(code) -> str:
    try:
        return CODES.get(int(code), "알 수 없음({})".format(code))
    except (TypeError, ValueError):
        return "알 수 없음"


def _get(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "ai-bisu/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _default_city() -> str:
    raw = (context.cfg.raw if context.cfg else {}) or {}
    return ((raw.get("weather") or {}).get("default_city") or "서울").strip()


def _timezone() -> str:
    raw = (context.cfg.raw if context.cfg else {}) or {}
    return (raw.get("weather") or {}).get("timezone") or "Asia/Seoul"


def _find_place(city: str) -> dict:
    data = _get(GEO_URL, {"name": city, "count": 1, "language": "ko", "format": "json"})
    results = data.get("results") or []
    if not results:
        raise LookupError(city)
    return results[0]


def _place_name(place: dict) -> str:
    bits = [place.get("name") or "", place.get("admin1") or "", place.get("country") or ""]
    seen, parts = set(), []
    for b in bits:
        if b and b not in seen:
            seen.add(b)
            parts.append(b)
    return " ".join(parts)


def _rain_hours(hourly: dict, day: str, threshold: int = 40) -> str:
    """그 날짜 중 비 올 확률이 높은 시간대를 '14~17시' 처럼 묶어서 돌려준다."""
    times = hourly.get("time") or []
    probs = hourly.get("precipitation_probability") or []
    hours = [int(t[11:13]) for t, p in zip(times, probs)
             if t.startswith(day) and p is not None and p >= threshold]
    if not hours:
        return ""
    spans, start, prev = [], hours[0], hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        spans.append((start, prev))
        start = prev = h
    spans.append((start, prev))
    return ", ".join("{}시".format(a) if a == b else "{}~{}시".format(a, b) for a, b in spans)


@tool(
    name="weather",
    description=(
        "날씨를 알려준다. '오늘 날씨 어때?', '내일 비 와?', '부산 날씨' 같은 요청에 쓴다. "
        "도시를 말하지 않으면 기본 도시를 쓴다. 여기서 받은 숫자만 근거로 말하고 지어내지 마라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string",
                     "description": "도시나 지역 이름. 예: 서울, 부산, 수원. 안 쓰면 기본 도시"},
            "days": {"type": "integer",
                     "description": "며칠치를 볼지. 오늘만이면 1, 내일까지면 2. 기본 2, 최대 7"},
        },
        "required": [],
    },
)
def weather(city: str = "", days: int = 2) -> str:
    city = (city or "").strip() or _default_city()
    days = max(1, min(int(days or 2), 7))

    try:
        place = _find_place(city)
        data = _get(FORECAST_URL, {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": ("temperature_2m,apparent_temperature,relative_humidity_2m,"
                        "precipitation,weather_code,wind_speed_10m"),
            "hourly": "precipitation_probability",
            "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max"),
            "timezone": _timezone(),
            "forecast_days": days,
        })
    except LookupError:
        return ("'{}' 라는 지명을 찾지 못했습니다. 다른 이름으로 다시 불러보세요 "
                "(예: 서울, 수원, 부산).").format(city)
    except urllib.error.HTTPError as e:
        return "날씨 서버가 오류를 돌려줬습니다 ({}). 잠시 뒤 다시 시도하세요.".format(e.code)
    except urllib.error.URLError as e:
        return ("날씨 서버에 연결하지 못했습니다: {}. 인터넷 연결이나 회사 방화벽을 확인하세요."
                ).format(e.reason)
    except (ValueError, KeyError) as e:
        return "날씨 응답을 해석하지 못했습니다: {}".format(e)

    now = data.get("current") or {}
    daily = data.get("daily") or {}
    hourly = data.get("hourly") or {}
    units = (data.get("current_units") or {}).get("temperature_2m", "°C")

    rows = ["{} 날씨".format(_place_name(place)), ""]
    rows.append("[지금] {} · {}{} (체감 {}{}) · 습도 {}% · 바람 {}m/s".format(
        _sky(now.get("weather_code")),
        now.get("temperature_2m", "?"), units,
        now.get("apparent_temperature", "?"), units,
        now.get("relative_humidity_2m", "?"),
        now.get("wind_speed_10m", "?")))
    if now.get("precipitation"):
        rows.append("       지금 내리는 비/눈: {}mm".format(now["precipitation"]))
    rows.append("")

    labels = ["오늘", "내일", "모레"]
    for i, day in enumerate(daily.get("time") or []):
        label = labels[i] if i < len(labels) else day[5:].replace("-", "/")
        rain = (daily.get("precipitation_probability_max") or [None] * (i + 1))[i]
        line = "[{}] {} · {}~{}{}".format(
            label, _sky((daily.get("weather_code") or [None])[i]),
            (daily.get("temperature_2m_min") or ["?"])[i],
            (daily.get("temperature_2m_max") or ["?"])[i], units)
        if rain is not None:
            line += " · 강수확률 {}%".format(rain)
        rows.append(line)
        when = _rain_hours(hourly, day)
        if when:
            rows.append("       비 올 만한 시간: " + when)

    return "\n".join(rows)
