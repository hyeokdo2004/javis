"""기억 도구 — 비서가 스스로 기억하고 꺼내 쓰게 한다."""
from __future__ import annotations

from core import context
from core.registry import tool


@tool(
    name="remember",
    description=(
        "앞으로 계속 알고 있어야 할 짧은 사실을 저장한다. 사람 이름과 역할, 거래처 정보, "
        "반복되는 규칙 같은 것. 사용자가 '기억해'라고 하거나, 다시 물어보지 않는 게 나은 "
        "정보를 알게 됐을 때 쓴다. 긴 문서는 write_note 를 써라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "기억할 내용. 한 문장으로"},
            "tags": {"type": "string", "description": "분류 태그. 예: 인물, 거래처, 규칙. 없으면 빈 문자열"},
        },
        "required": ["text"],
    },
)
def remember(text: str, tags: str = "") -> str:
    entry = context.memory.remember(text, tags)
    return "기억했습니다: {} [{}]".format(entry["text"], entry["id"])


@tool(
    name="forget",
    description="저장된 기억을 지운다. 사용자가 틀렸다고 하거나 더 이상 유효하지 않을 때.",
    parameters={
        "type": "object",
        "properties": {"fact_id": {"type": "string", "description": "기억의 id (대괄호 안 값)"}},
        "required": ["fact_id"],
    },
)
def forget(fact_id: str) -> str:
    return "지웠습니다." if context.memory.forget(fact_id) else "그런 id 의 기억이 없습니다."


@tool(
    name="search_memory",
    description=(
        "저장된 기억을 검색한다. 시스템 프롬프트에 이미 최근 기억이 들어 있으니, "
        "거기에 없는 오래된 것을 찾을 때만 쓴다."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "검색어. 비우면 전체"}},
        "required": [],
    },
)
def search_memory(query: str = "") -> str:
    hits = context.memory.search_facts(query)
    if not hits:
        return "해당하는 기억이 없습니다."
    return "\n".join("- {} [{}]".format(h["text"], h["id"]) for h in hits[:50])


@tool(
    name="read_note",
    description=(
        "보관 중인 긴 문서를 읽는다. 절차서, 조사 결과, 스크립트 사용법 등이 여기 들어 있다. "
        "시스템 프롬프트의 '보관 중인 문서' 목록에 있는 이름을 쓴다. "
        "작업 방법을 모르겠으면 먼저 여기를 뒤져봐라."
    ),
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "문서 이름"}},
        "required": ["name"],
    },
)
def read_note(name: str) -> str:
    try:
        return context.memory.note_read(name)
    except FileNotFoundError as e:
        return str(e)


@tool(
    name="write_note",
    description=(
        "긴 내용을 문서로 저장한다. 사용자가 절차나 조사 결과를 알려줬을 때, "
        "또는 정리한 내용을 다음에도 쓸 수 있게 남길 때. 짧은 사실은 remember 를 써라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "문서 이름 (파일명이 된다)"},
            "content": {"type": "string", "description": "문서 내용. 마크다운"},
            "append": {"type": "boolean", "description": "기존 문서 뒤에 이어붙일지. 기본 false(덮어쓰기)"},
        },
        "required": ["name", "content"],
    },
)
def write_note(name: str, content: str, append: bool = False) -> str:
    filename = context.memory.note_write(name, content, append)
    return "저장했습니다: data/notes/{}".format(filename)
