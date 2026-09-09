"""도구 만드는 법 — 이 파일을 복사해서 이름을 바꾸고 고치세요.

파일 이름이 밑줄(_)로 시작하면 불러오지 않습니다. 그래서 이 파일은 예시로만 남습니다.
실제로 쓰려면 예를 들어 tools/내도구.py 로 저장하세요.

파이썬을 짜기 싫으면 commands.json 에 명령만 등록해도 됩니다. 그쪽이 더 쉽습니다.
"""
from core import context          # context.cfg, context.memory 를 쓸 수 있습니다
from core.registry import tool


@tool(
    # 비서가 부를 이름. 영문/숫자/밑줄
    name="예시_인사",

    # ★ 가장 중요합니다. 비서는 이 설명만 읽고 이 도구를 쓸지 말지 정합니다.
    #    "언제 쓰는 도구인지"를 사용자가 할 법한 말과 함께 적으세요.
    description=(
        "사람 이름을 받아 인사말을 만든다. 사용자가 '누구한테 인사말 만들어줘' "
        "라고 할 때 쓴다."
    ),

    # 비서가 채워 넣을 인자. JSON Schema 형식입니다.
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "인사할 사람 이름"},
            "formal": {"type": "boolean", "description": "격식체로 할지. 기본 true"},
        },
        "required": ["name"],
    },

    # 되돌리기 어려운 작업(파일 삭제, 메일 발송, 배포 등)이면 True 로 두세요.
    # True 면 실행 전에 화면에서 y/n 을 물어봅니다.
    confirm=False,
)
def 예시_인사(name: str, formal: bool = True) -> str:
    # 반환값은 반드시 문자열입니다. 이 문자열을 비서가 읽고 사용자에게 설명합니다.
    # 그러니 비서가 이해할 수 있게, 결과와 함께 상황도 적어주세요.
    if formal:
        return "{} 님, 안녕하십니까.".format(name)
    return "{}님 안녕하세요!".format(name)
