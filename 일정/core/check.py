"""연결 점검 —  python assistant.py 진단

Gemini 키가 살아 있는지, 메일이 붙는지, 도구가 제대로 등록됐는지 순서대로 봅니다.
"""
from __future__ import annotations

import json
import os

from . import context, registry, ui
from .config import load_config
from .llm import Gemini, LLMError
from .memory import Memory

OK = "  ✅"
NG = "  ❌"
DOT = "  ·"


def _mask(value: str) -> str:
    if not value:
        return "(비어 있음)"
    return value[:4] + "…" + value[-4:] if len(value) > 10 else value[0] + "***"


def check_gemini(cfg) -> bool:
    print("\n[1/4] Gemini API")
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print(NG, "GEMINI_API_KEY 가 없습니다.")
        print(DOT, ".env 파일을 만들고 넣으세요. 키 발급: https://aistudio.google.com/apikey")
        return False
    print(DOT, "키:", _mask(key))
    print(DOT, "모델:", cfg.model)

    llm = Gemini(key, cfg.model, api_style=cfg.api_style, data_dir=cfg.data_dir, retries=0)

    # 어떤 모델을 쓸 수 있는지 먼저 보여준다 (모델명이 틀렸을 때 바로 알 수 있게)
    try:
        models = llm.list_models()
        free_flash = [m["name"] for m in models if "flash" in m["name"].lower()][:8]
        if free_flash:
            print(DOT, "쓸 수 있는 flash 계열:", ", ".join(free_flash))
        if not any(m["name"] == cfg.model for m in models):
            print(NG, "config.json 의 모델 '{}' 이 목록에 없습니다.".format(cfg.model))
            print(DOT, "위 목록 중 하나로 config.json 의 model.id 를 바꾸세요.")
    except Exception as e:
        print(DOT, "모델 목록 조회는 건너뜁니다 ({})".format(type(e).__name__))

    try:
        reply = llm.chat("너는 점검용이다. 아주 짧게 답한다.",
                         [{"role": "user", "text": "연결 확인. '정상'이라고만 답해."}], [])
        print(OK, "응답 정상: {}".format((reply.text or "").strip()[:40]))
        print(DOT, "사용된 방식: {}".format(llm.style or "?"))
    except LLMError as e:
        print(NG, str(e))
        return False

    # 도구 호출(function calling)이 되는지가 핵심이라 따로 확인한다
    probe = [{
        "name": "확인용_더하기",
        "description": "두 수를 더한다",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    }]
    try:
        reply = llm.chat("도구가 있으면 반드시 도구를 써라.",
                         [{"role": "user", "text": "3 더하기 4 를 도구로 계산해줘."}], probe)
        if reply.calls:
            print(OK, "도구 호출 정상: {}({})".format(
                reply.calls[0].name, reply.calls[0].args))
        else:
            print(NG, "도구를 호출하지 않았습니다. 이 모델은 function calling 이 약할 수 있습니다.")
            print(DOT, "응답: {}".format((reply.text or "")[:80]))
            return False
    except LLMError as e:
        print(NG, "도구 호출 실패: {}".format(e))
        return False

    return True


def check_mail(cfg) -> bool:
    print("\n[2/4] 메일 (IMAP)")
    user = os.environ.get("IMAP_USER", "")
    password = os.environ.get("IMAP_PASS", "")
    if not user or not password:
        print(DOT, "IMAP_USER / IMAP_PASS 가 없습니다 — 메일 기능만 못 씁니다 (나머지는 정상 동작)")
        return True  # 메일은 선택 기능이라 실패로 치지 않는다

    import imaplib
    import socket
    import ssl

    mail_cfg = cfg.mail or {}
    hosts = []
    for h in (os.environ.get("IMAP_HOST"), mail_cfg.get("imap_host"),
              "imap.hiworks.com", "outlook.office365.com", "imap.gmail.com"):
        if h and h not in hosts:
            hosts.append(h)
    port = int(os.environ.get("IMAP_PORT") or mail_cfg.get("imap_port", 993))

    print(DOT, "계정:", user)
    for host in hosts:
        print(DOT, "시도: {}:{}".format(host, port))
        try:
            conn = imaplib.IMAP4_SSL(host, port, timeout=10)
        except (socket.gaierror, socket.timeout, TimeoutError, OSError, ssl.SSLError) as e:
            print("       └ 접속 불가 ({})".format(type(e).__name__))
            continue
        try:
            conn.login(user, password)
        except imaplib.IMAP4.error as e:
            print(NG, "서버는 열렸는데 로그인 실패: {}".format(e))
            print(DOT, "IMAP 사용이 켜져 있는지, 앱 비밀번호가 필요한지 확인하세요.")
            _close(conn)
            return False
        try:
            conn.select("INBOX", readonly=True)
            status, data = conn.uid("SEARCH", None, "ALL")
            count = len((data[0] or b"").split()) if status == "OK" else 0
            print(OK, "로그인 성공. INBOX {}통".format(count))
            if host != mail_cfg.get("imap_host"):
                print(DOT, "→ config.json 의 mail.imap_host 를 '{}' 로 바꾸세요.".format(host))
            return True
        finally:
            _close(conn)

    print(NG, "모든 후보 서버에 붙지 못했습니다. IMAP_HOST 를 .env 에 직접 넣어보세요.")
    return False


def _close(conn) -> None:
    import imaplib

    for fn in ("close", "logout"):
        try:
            getattr(conn, fn)()
        except (imaplib.IMAP4.error, OSError):
            pass


def check_tools(cfg) -> bool:
    print("\n[3/4] 도구")
    problems = registry.load_all(cfg.root)
    for p in problems:
        print(NG, p)

    tools = registry.registry()
    if not tools:
        print(NG, "등록된 도구가 없습니다.")
        return False

    by_source = {}
    for t in tools.values():
        by_source.setdefault(t.source, []).append(t.name)
    for source, names in by_source.items():
        print(OK, "{}: {}개 — {}".format(source, len(names), ", ".join(names)))

    external = [t for t in tools.values() if t.source == "commands.json"]
    if not external:
        print(DOT, "commands.json 에 등록된 외부 스크립트가 아직 없습니다.")
        print(DOT, "CCX 자동화 같은 걸 여기 등록하면 비서가 실행할 수 있습니다.")
    return not problems


def check_files(cfg) -> bool:
    print("\n[4/4] 폴더")
    for name in ("data", "data/notes", "tools"):
        path = cfg.root / name
        mark = OK if path.exists() else NG
        print(mark, name + ("" if path.exists() else "  ← 없습니다"))

    cmd_path = cfg.root / "commands.json"
    if cmd_path.exists():
        try:
            json.loads(cmd_path.read_text(encoding="utf-8"))
            print(OK, "commands.json 형식 정상")
        except json.JSONDecodeError as e:
            print(NG, "commands.json 형식 오류: {}".format(e))
            return False
    return True


def run() -> int:
    ui.setup()
    cfg = load_config()
    context.init(cfg, Memory(cfg.data_dir, cfg.tz))

    print("=" * 52)
    print(" AI 업무 비서 — 연결 점검")
    print("=" * 52)

    results = [check_gemini(cfg), check_mail(cfg), check_tools(cfg), check_files(cfg)]

    print("\n" + "=" * 52)
    if all(results):
        print(" 전부 정상입니다. python assistant.py 로 시작하세요.")
        print("=" * 52)
        return 0
    print(" 위에 ❌ 표시된 것을 먼저 해결하세요.")
    print("=" * 52)
    return 1
