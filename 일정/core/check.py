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
    if cfg.fallbacks:
        print(DOT, "대체 모델:", ", ".join(cfg.fallbacks))

    def on_switch(old, new, why):
        print(DOT, "'{}' 이 응답하지 않아 '{}' 로 바꿔서 시도합니다.".format(old, new))

    llm = Gemini(key, cfg.model, api_style=cfg.api_style, data_dir=cfg.data_dir,
                 retries=0, fallbacks=cfg.fallbacks, on_switch=on_switch)

    # 어떤 모델을 쓸 수 있는지 먼저 보여준다 (모델명이 틀렸을 때 바로 알 수 있게)
    try:
        models = llm.list_models()
        free_flash = [m["name"] for m in models if "flash" in m["name"].lower()][:8]
        if free_flash:
            print(DOT, "쓸 수 있는 flash 계열:", ", ".join(free_flash))
        names = {m["name"] for m in models}
        if cfg.model not in names:
            print(NG, "config.json 의 모델 '{}' 이 목록에 없습니다.".format(cfg.model))
            print(DOT, "위 목록 중 하나로 config.json 의 model.id 를 바꾸세요.")
        missing = [m for m in cfg.fallbacks if m not in names]
        if missing:
            print(DOT, "대체 모델 중 목록에 없는 것: {} (지워도 됩니다)".format(", ".join(missing)))
    except Exception as e:
        print(DOT, "모델 목록 조회는 건너뜁니다 ({})".format(type(e).__name__))

    try:
        reply = llm.chat("너는 점검용이다. 아주 짧게 답한다.",
                         [{"role": "user", "text": "연결 확인. '정상'이라고만 답해."}], [])
        print(OK, "응답 정상: {}".format((reply.text or "").strip()[:40]))
        print(DOT, "사용된 모델: {} / 방식: {}".format(llm.model, llm.style or "?"))
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
    candidates = [os.environ.get("IMAP_HOST"), mail_cfg.get("imap_host")]
    candidates += list(mail_cfg.get("imap_fallbacks") or [])
    candidates += ["mail.hiworks.co.kr", "mailapp.hiworks.co.kr",
                   "outlook.office365.com", "imap.gmail.com"]
    for h in candidates:
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
            reason = str(e)
            print("       └ 서버는 열렸는데 로그인 실패: {}".format(reason))
            if "basic authentication is disabled" in reason.lower():
                print("         (이 서버는 ID/비밀번호 로그인을 막아둔 곳입니다 — 당신 메일 서버가 아닙니다)")
            _close(conn)
            continue  # 다음 후보 서버로
        try:
            conn.select("INBOX", readonly=True)
            status, data = conn.uid("SEARCH", None, "ALL")
            count = len((data[0] or b"").split()) if status == "OK" else 0
            print(OK, "로그인 성공. INBOX {}통".format(count))
            if host != (os.environ.get("IMAP_HOST") or mail_cfg.get("imap_host")):
                print(DOT, "→ config.json 의 mail.imap_host 를 '{}' 로 바꾸세요.".format(host))
            return True
        finally:
            _close(conn)

    print(NG, "모든 후보 서버에서 실패했습니다.")
    print(DOT, "도메인({}) 의 메일 서버 주소를 관리자에게 확인해 .env 의 IMAP_HOST 에 넣으세요."
          .format(user.split("@")[-1] if "@" in user else "회사"))
    print(DOT, "하이웍스라면 보통 mail.hiworks.co.kr:993 (SSL) 이고, "
               "관리자 화면에서 IMAP 사용이 켜져 있어야 합니다.")
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
