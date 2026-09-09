"""계산기와 날짜 계산 — 모델이 암산으로 틀리기 쉬운 것들을 정확히 처리한다."""
from __future__ import annotations

import ast
import math
import operator
import re
from datetime import datetime, timedelta

from core import context
from core.registry import tool

_WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "round": round, "abs": abs, "min": min, "max": max, "sum": sum,
    "int": int, "float": float,
    "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil,
}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("숫자만 계산할 수 있습니다")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if type(node.op) is ast.Pow and (abs(right) > 100 or abs(left) > 10 ** 8):
            raise ValueError("너무 큰 거듭제곱입니다")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCS.get(node.func.id)
        if fn is None:
            raise ValueError("쓸 수 없는 함수입니다: {}".format(node.func.id))
        return fn(*[_eval(a) for a in node.args])
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval(e) for e in node.elts]
    raise ValueError("계산할 수 없는 식입니다")


@tool(
    name="calc",
    description=(
        "숫자 계산을 정확히 한다. 부가세·할인·단가 합계처럼 틀리면 안 되는 계산은 "
        "암산하지 말고 반드시 이 도구를 써라. 예: 1250000*1.1, (38000-5000)*12"
    ),
    parameters={
        "type": "object",
        "properties": {
            "expression": {"type": "string",
                           "description": "계산식. + - * / % ** 와 round, sqrt, min, max 를 쓸 수 있다"},
        },
        "required": ["expression"],
    },
)
def calc(expression: str) -> str:
    raw = (expression or "").strip()
    if not raw:
        return "계산할 식이 없습니다."
    # 사람이 쓰는 쉼표와 곱셈 기호를 정리
    cleaned = raw.replace(",", "").replace("×", "*").replace("÷", "/").replace("^", "**")
    if not re.fullmatch(r"[0-9a-zA-Z_+\-*/%.()\s,]+", cleaned):
        return "계산식에 쓸 수 없는 문자가 있습니다: {}".format(raw)

    try:
        value = _eval(ast.parse(cleaned, mode="eval"))
    except SyntaxError:
        return "식을 읽지 못했습니다: {}".format(raw)
    except ZeroDivisionError:
        return "0 으로 나눌 수 없습니다: {}".format(raw)
    except (ValueError, TypeError, OverflowError) as e:
        return "계산하지 못했습니다: {}".format(e)

    if isinstance(value, float) and value == int(value) and abs(value) < 10 ** 15:
        value = int(value)
    shown = "{:,}".format(value) if isinstance(value, int) else "{:,.6g}".format(value)
    return "{} = {}".format(raw, shown)


def _parse(text: str):
    text = (text or "").strip()
    today = datetime.now(context.cfg.tz).date()
    if not text or text in ("오늘", "금일"):
        return today
    if text in ("내일", "명일"):
        return today + timedelta(days=1)
    if text in ("어제", "작일"):
        return today - timedelta(days=1)
    if text == "모레":
        return today + timedelta(days=2)
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    found = re.match(r"^(\d{1,2})[-./월]\s*(\d{1,2})일?$", text)
    if found:
        try:
            return today.replace(month=int(found.group(1)), day=int(found.group(2)))
        except ValueError:
            return None
    return None


def _show(day) -> str:
    return "{}({})".format(day.strftime("%Y-%m-%d"), _WEEKDAY[day.weekday()])


@tool(
    name="date_calc",
    description=(
        "날짜를 계산한다. '3주 뒤가 며칠이야?', '납기까지 며칠 남았어?', "
        "'10월 15일은 무슨 요일이야?' 같은 요청에 쓴다. 날짜 계산은 암산하지 말고 이걸 써라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "base": {"type": "string",
                     "description": "기준 날짜. 오늘/내일 또는 2026-10-15. 없으면 오늘"},
            "days": {"type": "integer", "description": "더할 날 수. 빼려면 음수. 기본 0"},
            "until": {"type": "string",
                      "description": "여기까지 며칠 남았는지 셀 날짜. 쓰면 days 는 무시"},
        },
        "required": [],
    },
)
def date_calc(base: str = "", days: int = 0, until: str = "") -> str:
    start = _parse(base)
    if start is None:
        return "'{}' 는 날짜로 읽지 못했습니다. 2026-10-15 형식으로 쓰세요.".format(base)

    if until:
        end = _parse(until)
        if end is None:
            return "'{}' 는 날짜로 읽지 못했습니다. 2026-10-15 형식으로 쓰세요.".format(until)
        gap = (end - start).days
        if gap == 0:
            return "{} 은(는) 기준일과 같은 날입니다.".format(_show(end))
        word = "남았습니다" if gap > 0 else "지났습니다"
        return "{} 부터 {} 까지 {}일 {} (D{}{})".format(
            _show(start), _show(end), abs(gap), word,
            "-" if gap > 0 else "+", abs(gap))

    try:
        target = start + timedelta(days=int(days or 0))
    except (OverflowError, ValueError):
        return "날 수가 너무 큽니다."
    if not days:
        return _show(target)
    return "{} 에서 {}일 {} → {}".format(
        _show(start), abs(int(days)), "뒤" if days > 0 else "전", _show(target))
