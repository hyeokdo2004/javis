"""터미널 출력. 윈도우 cmd 에서도 안 깨지게."""
from __future__ import annotations

import sys

LINE = "─" * 52


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def say(text: str) -> None:
    print("\n🤖 " + str(text).replace("\n", "\n   "))


def info(text: str) -> None:
    print("   " + str(text).replace("\n", "\n   "))


def dim(text: str) -> None:
    print("   · " + str(text))


def warn(text: str) -> None:
    print("\n⚠️  " + str(text).replace("\n", "\n    "))


def error(text: str) -> None:
    print("\n❌ " + str(text).replace("\n", "\n   "))


def rule() -> None:
    print(LINE)


def tool_start(name: str, args: dict) -> None:
    shown = ", ".join("{}={}".format(k, _short(v)) for k, v in (args or {}).items())
    print("   🔧 {}({})".format(name, shown))


def tool_done(result: str) -> None:
    first = (result or "").strip().splitlines()
    head = first[0] if first else ""
    more = "  …외 {}줄".format(len(first) - 1) if len(first) > 1 else ""
    print("      → {}{}".format(_short(head, 90), more))


def confirm(tool, args: dict) -> bool:
    """되돌리기 어려운 도구는 실행 전에 반드시 사람에게 묻는다."""
    print("\n" + LINE)
    print("⚠️  실행 확인이 필요합니다")
    print("   도구  : {}  ({})".format(tool.name, tool.source))
    print("   설명  : {}".format(tool.description))
    if tool.danger:
        print("   내용  : {}".format(tool.danger))
    for key, value in (args or {}).items():
        print("   {:<6}: {}".format(key, value))
    print(LINE)
    try:
        answer = input("실행할까요? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes", "ㅇ", "네", "예")


def _short(value, limit: int = 40) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"
