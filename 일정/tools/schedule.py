"""일정·할일 도구 — data/schedule.json 에 쌓아두고 꺼내 본다.

캘린더 연동 없이 파일 한 개로 돌아갑니다. '오늘 일정 뭐야?', '내일 2시 A사 미팅
넣어줘', '그거 처리했어' 같은 대화를 처리합니다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from core import context
from core.registry import tool

FILE = "schedule.json"
_WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]

_ISO = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$")
_SHORT = re.compile(r"^(\d{1,2})[-./](\d{1,2})$")
_KOREAN = re.compile(r"^(\d{1,2})월\s*(\d{1,2})일?$")
_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?$")

_RELATIVE = {"오늘": 0, "금일": 0, "내일": 1, "명일": 1, "모레": 2, "글피": 3, "어제": -1}


# ────────────────────────────────────────────────────────────
# 저장소
# ────────────────────────────────────────────────────────────

def _path():
    context.cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return context.cfg.data_dir / FILE


def _load() -> list:
    path = _path()
    if not path.exists():
        return []
    try:
        return (json.loads(path.read_text(encoding="utf-8")) or {}).get("items", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list) -> None:
    _path().write_text(
        json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_id(items: list) -> int:
    return max([int(i.get("id", 0)) for i in items] or [0]) + 1


# ────────────────────────────────────────────────────────────
# 날짜·시각 해석
# ────────────────────────────────────────────────────────────

def _today():
    return datetime.now(context.cfg.tz).date()


def _parse_date(text: str):
    """'오늘', '내일', '9/10', '2026-09-10', '9월 10일' → date. 못 읽으면 None."""
    text = (text or "").strip()
    if not text:
        return None
    if text in _RELATIVE:
        return _today() + timedelta(days=_RELATIVE[text])

    found = _ISO.match(text)
    if found:
        y, m, d = (int(x) for x in found.groups())
        return _make(y, m, d)

    for pattern in (_SHORT, _KOREAN):
        found = pattern.match(text)
        if found:
            m, d = (int(x) for x in found.groups())
            today = _today()
            made = _make(today.year, m, d)
            # 이미 지난 날짜면 내년으로 본다 (12월에 '1/5' 라고 하면 내년 1월)
            if made and (today - made).days > 180:
                made = _make(today.year + 1, m, d)
            return made
    return None


def _make(year: int, month: int, day: int):
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def _parse_time(text: str) -> str:
    """'14:00', '14', '오후 2시' → 'HH:MM'. 못 읽으면 빈 문자열."""
    text = (text or "").strip()
    if not text:
        return ""
    afternoon = any(w in text for w in ("오후", "저녁", "pm", "PM"))
    morning = any(w in text for w in ("오전", "아침", "am", "AM"))
    cleaned = re.sub(r"[^\d:]", "", text.replace("시", ":").replace("분", ""))
    cleaned = cleaned.rstrip(":")
    found = _TIME.match(cleaned)
    if not found:
        return ""
    hour = int(found.group(1))
    minute = int(found.group(2) or 0)
    if afternoon and hour < 12:
        hour += 12
    if morning and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return ""
    return "{:02d}:{:02d}".format(hour, minute)


def _label(value: str) -> str:
    """'2026-09-10' → '09/10(목)'. 날짜가 없으면 '날짜 미정'."""
    if not value:
        return "날짜 미정"
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return value
    return "{}({})".format(day.strftime("%m/%d"), _WEEKDAY[day.weekday()])


def _line(item: dict) -> str:
    mark = "✔" if item.get("done") else "·"
    when = _label(item.get("date", ""))
    if item.get("time"):
        when += " " + item["time"]
    return "{} [{}] {} — {}".format(mark, item.get("id"), when, item.get("text", ""))


def _sorted(items: list) -> list:
    return sorted(items, key=lambda i: (i.get("date") or "9999-99-99",
                                        i.get("time") or "99:99", i.get("id", 0)))


# ────────────────────────────────────────────────────────────
# 도구
# ────────────────────────────────────────────────────────────

@tool(
    name="add_schedule",
    description=(
        "일정이나 할 일을 저장한다. '내일 2시 A사 미팅 넣어줘', "
        "'오늘 할 일: 견적서 발송' 같은 요청에 쓴다. 여러 건이면 하나씩 나눠서 불러라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "무슨 일인지. 예: A사 견적서 발송"},
            "date": {"type": "string",
                     "description": "언제. 오늘/내일/모레 또는 2026-09-10, 9/10 형식. 없으면 빈 문자열"},
            "time": {"type": "string", "description": "몇 시. 14:00 또는 '오후 2시'. 없으면 빈 문자열"},
        },
        "required": ["text"],
    },
)
def add_schedule(text: str, date: str = "", time: str = "") -> str:
    text = (text or "").strip()
    if not text:
        return "무슨 일인지 내용이 없습니다."

    when = _parse_date(date)
    if date and when is None:
        return ("'{}' 는 날짜로 읽지 못했습니다. 오늘/내일/모레 또는 2026-09-10, 9/10 "
                "형식으로 다시 넣으세요.").format(date)

    items = _load()
    item = {
        "id": _next_id(items),
        "date": when.isoformat() if when else "",
        "time": _parse_time(time),
        "text": text,
        "done": False,
        "created": datetime.now(context.cfg.tz).strftime("%Y-%m-%d %H:%M"),
    }
    items.append(item)
    _save(items)
    return "저장했습니다.\n" + _line(item)


@tool(
    name="list_schedule",
    description=(
        "저장해둔 일정·할 일을 본다. '오늘 일정 뭐야?', '이번 주 할 일 정리해줘', "
        "'남은 일 뭐 있어?' 같은 요청에 쓴다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "when": {"type": "string",
                     "description": "오늘/내일/이번주/전체 또는 2026-09-10 같은 날짜. 기본 오늘"},
            "include_done": {"type": "boolean", "description": "끝낸 것도 볼지. 기본 false"},
        },
        "required": [],
    },
)
def list_schedule(when: str = "오늘", include_done: bool = False) -> str:
    when = (when or "오늘").strip()
    items = [i for i in _load() if include_done or not i.get("done")]

    today = _today()
    if when in ("전체", "모두", "all"):
        picked, header = items, "전체 일정"
    elif when in ("이번주", "이번 주", "주간", "week"):
        end = today + timedelta(days=6)
        picked = [i for i in items
                  if i.get("date") and today.isoformat() <= i["date"] <= end.isoformat()]
        header = "이번 주 일정 ({} ~ {})".format(_label(today.isoformat()),
                                              _label(end.isoformat()))
    else:
        day = _parse_date(when)
        if day is None:
            return ("'{}' 는 날짜로 읽지 못했습니다. 오늘/내일/이번주/전체 또는 "
                    "2026-09-10 형식으로 물어보세요.").format(when)
        picked = [i for i in items if i.get("date") == day.isoformat()]
        header = "{} 일정".format(_label(day.isoformat()))

    # 날짜를 안 정한 할 일은 항상 같이 보여준다 (잊히지 않게)
    undated = [i for i in items if not i.get("date")]
    if when not in ("전체", "모두", "all"):
        picked = picked + undated

    if not picked:
        return header + "\n(등록된 것이 없습니다)"

    rows = ["{} {}건".format(header, len(picked)), ""]
    rows += [_line(i) for i in _sorted(picked)]

    overdue = [i for i in items
               if i.get("date") and i["date"] < today.isoformat() and not i.get("done")]
    if overdue and when not in ("전체", "모두", "all"):
        rows.append("")
        rows.append("지난 것 중 아직 안 끝난 일 {}건:".format(len(overdue)))
        rows += [_line(i) for i in _sorted(overdue)]
    return "\n".join(rows)


@tool(
    name="finish_schedule",
    description="일정·할 일을 끝난 것으로 표시한다. '그거 했어', '견적서 보냈어' 같은 말에 쓴다.",
    parameters={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "목록의 대괄호 안 번호"}},
        "required": ["id"],
    },
)
def finish_schedule(id: int) -> str:
    items = _load()
    for item in items:
        if int(item.get("id", 0)) == int(id):
            if item.get("done"):
                return "이미 끝난 것으로 되어 있습니다.\n" + _line(item)
            item["done"] = True
            _save(items)
            return "끝난 것으로 표시했습니다.\n" + _line(item)
    return "그 번호의 일정이 없습니다: {}. list_schedule 로 번호를 먼저 확인하세요.".format(id)


@tool(
    name="remove_schedule",
    description="일정·할 일을 목록에서 지운다. 잘못 넣었거나 취소된 일에 쓴다.",
    parameters={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "목록의 대괄호 안 번호"}},
        "required": ["id"],
    },
)
def remove_schedule(id: int) -> str:
    items = _load()
    for i, item in enumerate(items):
        if int(item.get("id", 0)) == int(id):
            items.pop(i)
            _save(items)
            return "지웠습니다: " + item.get("text", "")
    return "그 번호의 일정이 없습니다: {}".format(id)
