"""대화 루프 — 사용자 말 → 모델 → 도구 실행 → 모델 → … → 최종 답변."""
from __future__ import annotations

import json
from datetime import datetime

from . import registry
from .llm import LLMError

MAX_TOOL_ROUNDS = 8       # 한 번의 질문에 도구를 최대 몇 번까지 부를지
_WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]

SYSTEM_TEMPLATE = """너는 {user_name} 의 개인 업무 비서다. 한국어로 존댓말을 쓴다.

지금 시각: {now}

[네가 지켜야 할 것]
1. 모르는 것을 지어내지 마라. 특히 메일 내용, 파일 내용, 실행 결과는 반드시 도구로
   확인한 것만 말해라. 확인하지 않은 것을 사실처럼 말하는 게 가장 큰 잘못이다.
2. 등록된 도구로 처리할 수 없는 일을 요청받으면, 억지로 시도하지 말고 need_help 도구를
   써라. 사용자가 다른 AI 로 그 문제를 해결한 뒤 결과를 너에게 넣어줄 것이다.
   "못 합니다"로 끝내지 말고 '무엇이 있으면 할 수 있는지'를 구체적으로 적어라.
3. 사용자가 알아둘 만한 사실을 말해주면 remember 로 저장해라. 다음에 또 묻지 않도록.
4. 답은 짧고 실무적으로. 인사말·사족·과장된 칭찬을 붙이지 마라.
   목록이 길면 중요한 것부터 추려서 보여주고 나머지는 건수만 말해라.
5. 여러 도구를 연달아 써야 하면 알아서 순서대로 써라. 중간 과정을 일일이 설명하지 마라.
6. 사용자가 무언가를 '처리해줘'라고 했는데 그에 맞는 도구가 있으면 바로 그 도구를 써라.
   확인이 필요한 도구는 시스템이 알아서 사용자에게 물어보니 네가 미리 물을 필요 없다.

{profile}

{memory}"""


class Agent:
    def __init__(self, cfg, llm, memory, *, on_confirm=None, on_tool=None):
        self.cfg = cfg
        self.llm = llm
        self.memory = memory
        self.on_confirm = on_confirm or (lambda tool, args: True)
        self.on_tool = on_tool or (lambda name, args, result: None)
        self.turns = memory.history()

    # ── 시스템 프롬프트 ─────────────────────────────────────
    def system_prompt(self) -> str:
        now = datetime.now(self.cfg.tz)
        stamp = "{} ({}요일) {}".format(
            now.strftime("%Y-%m-%d"), _WEEKDAY[now.weekday()], now.strftime("%H:%M"))

        profile = (self.cfg.raw.get("profile") or {}).get("notes", "").strip()
        profile = "[사용자에 대해]\n" + profile if profile else ""

        return SYSTEM_TEMPLATE.format(
            user_name=(self.cfg.raw.get("profile") or {}).get("name", "사용자"),
            now=stamp,
            profile=profile,
            memory=self.memory.prompt_block(),
        ).strip()

    # ── 한 번의 질문 처리 ───────────────────────────────────
    def ask(self, user_text: str) -> str:
        self.turns.append({"role": "user", "text": user_text})
        specs = registry.specs()
        final = ""
        repaired = False

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                reply = self.llm.chat(self.system_prompt(), self.turns, specs)
            except LLMError as e:
                # 지난 대화 기록이 어긋나 있으면(도구 호출/결과 짝이 깨짐)
                # 이번 질문만 남기고 한 번 다시 해본다
                if repaired or e.code != 400 or "function call" not in str(e).lower():
                    raise
                repaired = True
                self.turns = [{"role": "user", "text": user_text}]
                self.memory.clear_history()
                continue

            self.turns.append({
                "role": "assistant", "text": reply.text, "calls": reply.calls,
                "parts": reply.parts,   # 그대로 돌려줘야 하는 원본 (thoughtSignature 포함)
            })

            if not reply.calls:
                final = reply.text
                break

            for call in reply.calls:
                result = self._run_tool(call)
                self.turns.append({
                    "role": "tool", "name": call.name,
                    "call_id": call.call_id, "result": result,
                })
        else:
            final = (reply.text or "").strip() or (
                "도구를 너무 여러 번 호출해서 중단했습니다. 요청을 조금 나눠서 다시 말씀해 주세요.")

        self.memory.save_history(self.turns)
        return final or "(빈 응답)"

    # ── 도구 실행 ───────────────────────────────────────────
    def _run_tool(self, call) -> str:
        tool = registry.get(call.name)
        if tool is None:
            available = ", ".join(registry.registry().keys())
            return "그런 도구는 없습니다: {}. 쓸 수 있는 도구: {}".format(call.name, available)

        args = call.args if isinstance(call.args, dict) else {}
        args = _coerce(args, tool.parameters)

        if tool.confirm and not self.on_confirm(tool, args):
            return ("사용자가 실행을 거부했습니다. 실행하지 마라. "
                    "다른 방법이 필요하면 사용자에게 물어봐라.")

        try:
            result = tool(**args)
        except TypeError as e:
            return "인자가 맞지 않습니다: {}. 이 도구가 받는 인자: {}".format(
                e, json.dumps(tool.parameters.get("properties", {}), ensure_ascii=False))
        except LLMError as e:
            return "오류: {}".format(e)
        except Exception as e:
            return "도구 실행 중 오류: {}: {}".format(type(e).__name__, e)

        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        self.on_tool(call.name, args, text)
        return text

    # ── 관리 ────────────────────────────────────────────────
    def reset(self) -> None:
        self.turns = []
        self.memory.clear_history()


def _coerce(args: dict, schema: dict) -> dict:
    """모델이 숫자를 문자열로 주는 등 사소하게 어긋나는 걸 맞춰준다."""
    props = (schema or {}).get("properties") or {}
    out = {}
    for key, value in args.items():
        if key not in props:
            continue  # 스키마에 없는 인자는 버린다 (TypeError 방지)
        want = props[key].get("type")
        try:
            if want == "integer" and not isinstance(value, bool):
                out[key] = int(float(value))
            elif want == "number" and not isinstance(value, bool):
                out[key] = float(value)
            elif want == "boolean" and isinstance(value, str):
                out[key] = value.strip().lower() in ("true", "1", "yes", "y", "네", "예")
            elif want == "string" and value is not None and not isinstance(value, str):
                out[key] = str(value)
            else:
                out[key] = value
        except (TypeError, ValueError):
            out[key] = value
    return out
