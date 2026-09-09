"""비서가 자기 능력의 한계를 다루는 도구.

핵심은 need_help 입니다. 비서가 못 하는 일을 만나면 억지로 시도하는 대신,
'다른 AI 에게 그대로 붙여넣을 수 있는 요청서'를 만들어 냅니다.
사용자는 그걸 Claude Code 같은 데 붙여넣어 해결하고, 결과를 도구나 문서로 넣어주면
비서는 그때부터 그 일을 할 수 있게 됩니다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from core import context
from core.registry import registry, tool

TEMPLATE = """# 비서가 처리하지 못한 요청

**요청일**: {date}

## 사용자가 시킨 일
{goal}

## 왜 지금은 안 되는지
{blocker}

## 해결되려면 필요한 것
{needs}

---

## 이 아래를 다른 AI(Claude Code 등)에 그대로 붙여넣으세요

내 개인 AI 비서에 새 기능을 추가하려고 합니다. 비서는 파이썬으로 되어 있고,
도구를 두 가지 방법으로 추가할 수 있습니다.

**방법 1 — 외부 스크립트/명령 등록 (파이썬 안 짜도 됨)**
`commands.json` 에 이렇게 추가하면 됩니다:

```json
{{
  "도구이름": {{
    "description": "언제 쓰는 도구인지. 비서가 이 설명을 읽고 고릅니다",
    "argv": ["python", "C:/경로/스크립트.py", "--옵션", "{{인자이름}}"],
    "parameters": {{
      "type": "object",
      "properties": {{
        "인자이름": {{"type": "string", "description": "이 인자가 무엇인지"}}
      }},
      "required": []
    }},
    "confirm": true,
    "timeout_sec": 600
  }}
}}
```

**방법 2 — 파이썬 도구 파일 추가**
`tools/` 폴더에 .py 파일을 만들고:

```python
from core.registry import tool

@tool(
    name="도구이름",
    description="언제 쓰는 도구인지",
    parameters={{
        "type": "object",
        "properties": {{"인자": {{"type": "string", "description": "설명"}}}},
        "required": [],
    }},
    confirm=False,   # 되돌리기 어려운 작업이면 True
)
def 함수이름(인자: str = "") -> str:
    ...
    return "결과 문자열"
```

**내가 필요한 것**은 위 '해결되려면 필요한 것' 항목입니다.
{needs}

이걸 할 수 있는 것을 만들어 주세요. 방법 1로 되면 방법 1이 좋습니다.
만들고 나면 어디에 무엇을 넣어야 하는지도 알려주세요.

## 참고: 비서가 지금 가진 도구
{tools}
"""


def _slug(text: str) -> str:
    text = re.sub(r"[^\w가-힣 -]", "", text).strip()
    return (re.sub(r"\s+", "-", text)[:40] or "요청")


@tool(
    name="need_help",
    description=(
        "지금 가진 도구로는 처리할 수 없는 일을 요청받았을 때 쓴다. 억지로 다른 도구를 "
        "끼워 맞추거나 결과를 지어내지 말고 이 도구를 써라. 사용자가 다른 AI 로 해결한 뒤 "
        "그 결과를 도구나 문서로 넣어줄 것이다. 무엇이 있으면 할 수 있는지 구체적으로 적어라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "사용자가 원한 것. 사용자 말 그대로에 가깝게"},
            "blocker": {"type": "string", "description": "왜 지금 못 하는지. 어떤 도구가 없어서인지"},
            "needs": {"type": "string",
                      "description": "무엇이 있으면 할 수 있는지. 스크립트 이름, 접근 권한, "
                                     "정보 등을 구체적으로. 여러 개면 줄바꿈으로 나열"},
        },
        "required": ["goal", "blocker", "needs"],
    },
)
def need_help(goal: str, blocker: str, needs: str) -> str:
    now = datetime.now(context.cfg.tz)
    tools_desc = "\n".join(
        "- {} : {}".format(t.name, t.description.split(".")[0])
        for t in registry().values()
    ) or "(없음)"

    body = TEMPLATE.format(
        date=now.strftime("%Y-%m-%d %H:%M"),
        goal=goal.strip(),
        blocker=blocker.strip(),
        needs=needs.strip(),
        tools=tools_desc,
    )

    out_dir = context.cfg.data_dir / "requests"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "{}-{}.md".format(now.strftime("%Y%m%d-%H%M"), _slug(goal))
    path.write_text(body, encoding="utf-8")

    return (
        "이건 지금 제가 가진 도구로는 못 합니다.\n\n"
        "필요한 것:\n{}\n\n"
        "다른 AI 에 붙여넣을 요청서를 만들어 뒀습니다:\n  data/requests/{}\n"
        "그걸 Claude Code 같은 데 붙여넣어 해결하신 뒤, 결과를 commands.json 이나 "
        "tools/ 폴더에 넣어주시면 다음부터는 제가 처리할 수 있습니다."
    ).format(needs.strip(), path.name)


@tool(
    name="list_tools",
    description=(
        "지금 내가 쓸 수 있는 도구 목록을 본다. 사용자가 '뭘 할 수 있어?' 라고 물으면 쓴다."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
def list_tools() -> str:
    rows = []
    for t in registry().values():
        params = list((t.parameters or {}).get("properties", {}).keys())
        rows.append("- {} ({}){}\n    {}\n    인자: {}".format(
            t.name,
            t.source,
            " · 실행 전 확인함" if t.confirm else "",
            t.description.strip().split("\n")[0],
            ", ".join(params) or "없음",
        ))
    return "\n".join(rows) if rows else "등록된 도구가 없습니다."


@tool(
    name="list_help_requests",
    description="지금까지 '못 하겠다'고 남긴 요청서 목록을 본다. 아직 해결 안 된 게 뭔지 확인할 때.",
    parameters={"type": "object", "properties": {}, "required": []},
)
def list_help_requests() -> str:
    out_dir = context.cfg.data_dir / "requests"
    if not out_dir.exists():
        return "아직 없습니다."
    files = sorted(out_dir.glob("*.md"), reverse=True)[:20]
    if not files:
        return "아직 없습니다."
    return "\n".join("- {}".format(f.name) for f in files)
