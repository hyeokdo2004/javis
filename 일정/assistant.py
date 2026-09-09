#!/usr/bin/env python3
"""AI 업무 비서 — 대화하면서 도구를 시키는 방식.

  python assistant.py              대화 시작
  python assistant.py "하루 메일 정리해줘"   한 번만 물어보고 끝내기
  python assistant.py 진단          연결 상태 점검

설치할 게 없습니다. 파이썬 3.10 이상만 있으면 이 폴더째로 복사해서 바로 씁니다.
"""
from __future__ import annotations

import sys

from core import ui

ui.setup()

from core import context, registry
from core.agent import Agent
from core.config import env, load_config
from core.llm import Gemini, LLMError
from core.memory import Memory

BANNER = """
╭──────────────────────────────────────────────╮
│   AI 업무 비서                                │
╰──────────────────────────────────────────────╯"""

INCHAT_HELP = """[대화 중 쓸 수 있는 명령]
  /도구      쓸 수 있는 도구 목록
  /기억      저장된 기억 보기
  /문서      보관 중인 문서 목록
  /새대화    대화 맥락 지우기 (기억과 문서는 남음)
  /도움말    이 목록
  /종료      끝내기

그 외에는 그냥 하고 싶은 말을 쓰세요.
  예) 오늘 온 메일 정리해줘
      CCX 현행화 요청 있는지 봐줘
      그럼 그거 자동화 스크립트로 처리해줘
      A사 담당은 김과장이야, 기억해둬
"""


def build(cfg):
    """설정 → 기억 → 도구 → 비서 순으로 조립."""
    memory = Memory(cfg.data_dir, cfg.tz)
    context.init(cfg, memory)

    problems = registry.load_all(cfg.root)
    for p in problems:
        ui.warn(p)

    llm = Gemini(
        env("GEMINI_API_KEY", required=True),
        cfg.model,
        api_style=cfg.api_style,
        data_dir=cfg.data_dir,
    )

    def on_tool(name, args, result):
        ui.tool_done(result)

    def on_confirm(tool, args):
        ui.tool_start(tool.name, args)
        return ui.confirm(tool, args)

    agent = Agent(cfg, llm, memory, on_confirm=on_confirm, on_tool=on_tool)

    # 확인이 필요 없는 도구는 실행 시작을 먼저 보여준다
    original = agent._run_tool

    def traced(call):
        tool = registry.get(call.name)
        if tool is not None and not tool.confirm:
            ui.tool_start(call.name, call.args)
        return original(call)

    agent._run_tool = traced
    return agent, memory


# ────────────────────────────────────────────────────────────
# 대화 중 명령
# ────────────────────────────────────────────────────────────

def handle_command(text: str, agent, memory) -> bool:
    """슬래시 명령을 처리했으면 True."""
    cmd = text.strip().lower()

    if cmd in ("/종료", "/exit", "/quit", "/q"):
        raise SystemExit(0)

    if cmd in ("/도움말", "/help", "/?"):
        ui.info(INCHAT_HELP)
        return True

    if cmd in ("/도구", "/tools"):
        rows = []
        for t in registry.registry().values():
            mark = " (실행 전 확인)" if t.confirm else ""
            rows.append("· {}{}\n    {}".format(
                t.name, mark, t.description.strip().split("\n")[0]))
        ui.info("쓸 수 있는 도구 {}개\n\n".format(len(rows)) + "\n".join(rows))
        return True

    if cmd in ("/기억", "/memory"):
        facts = memory.facts()
        if not facts:
            ui.info("아직 저장된 기억이 없습니다.")
        else:
            ui.info("\n".join("· {}  [{}]".format(f["text"], f["id"]) for f in facts))
        return True

    if cmd in ("/문서", "/notes"):
        notes = memory.note_list()
        if not notes:
            ui.info("data/notes/ 폴더에 .md 파일을 넣으면 비서가 읽습니다.")
        else:
            ui.info("\n".join("· {} — {}".format(n["name"], n["summary"]) for n in notes))
        return True

    if cmd in ("/새대화", "/reset", "/clear"):
        agent.reset()
        ui.info("대화 맥락을 지웠습니다. (기억과 문서는 그대로입니다)")
        return True

    return False


# ────────────────────────────────────────────────────────────

def chat_loop(agent, memory) -> None:
    print(BANNER)
    ui.dim("모델: {}".format(agent.cfg.model))
    ui.dim("도구 {}개 · 기억 {}건 · 문서 {}건".format(
        len(registry.registry()), len(memory.facts()), len(memory.note_list())))
    ui.dim("/도움말 을 치면 사용법이 나옵니다. /종료 로 끝냅니다.")
    print()

    while True:
        try:
            text = input("나 ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n안녕히 가세요.")
            return

        if not text:
            continue
        if text.startswith("/") and handle_command(text, agent, memory):
            print()
            continue

        try:
            answer = agent.ask(text)
        except LLMError as e:
            ui.error(str(e))
            print()
            continue
        except KeyboardInterrupt:
            ui.warn("중단했습니다.")
            print()
            continue

        ui.say(answer)
        print()


def one_shot(agent, text: str) -> int:
    try:
        ui.say(agent.ask(text))
        return 0
    except LLMError as e:
        ui.error(str(e))
        return 1


def main() -> int:
    args = sys.argv[1:]

    if args and args[0] in ("진단", "diagnose", "--diagnose"):
        from core.check import run as diagnose_run

        return diagnose_run()

    cfg = load_config()
    try:
        agent, memory = build(cfg)
    except RuntimeError as e:
        ui.error(str(e))
        ui.info("먼저 python assistant.py 진단 을 실행해 무엇이 빠졌는지 확인하세요.")
        return 1

    if args:
        return one_shot(agent, " ".join(args))

    chat_loop(agent, memory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
