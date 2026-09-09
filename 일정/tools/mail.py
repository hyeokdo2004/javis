"""메일 도구 — IMAP 으로 메일을 읽어온다. 요약은 비서(모델)가 한다.

Hiworks, Outlook/Office365, Gmail, 네이버 등 IMAP 을 지원하는 곳이면 다 됩니다.
서버 주소는 config.json 의 mail.imap_host 또는 .env 의 IMAP_HOST 로 지정합니다.
"""
from __future__ import annotations

import email
import email.utils
import imaplib
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header

from core import context
from core.config import env
from core.registry import tool

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")


# ────────────────────────────────────────────────────────────
# IMAP 기본
# ────────────────────────────────────────────────────────────

def _settings() -> dict:
    mail_cfg = (context.cfg.mail if context.cfg else {}) or {}
    primary = env("IMAP_HOST") or mail_cfg.get("imap_host", "mail.hiworks.co.kr")
    hosts = [primary]
    for h in (mail_cfg.get("imap_fallbacks") or []):
        if h and h not in hosts:
            hosts.append(h)
    return {
        "host": primary,
        "hosts": hosts,
        "port": int(env("IMAP_PORT") or mail_cfg.get("imap_port", 993)),
        "user": env("IMAP_USER", required=True),
        "password": env("IMAP_PASS", required=True),
        "folder": mail_cfg.get("folder", "INBOX"),
        "body_limit": int(mail_cfg.get("body_limit", 1500)),
    }


def _connect(s: dict) -> imaplib.IMAP4_SSL:
    """주소가 여러 개면 붙는 곳까지 순서대로 시도한다."""
    failures = []
    for host in s.get("hosts") or [s["host"]]:
        try:
            conn = imaplib.IMAP4_SSL(host, s["port"], timeout=20)
        except OSError as e:  # 이름 못 찾음 / 접속 불가 → 다음 후보
            failures.append("{}: 접속 불가 ({})".format(host, type(e).__name__))
            continue
        try:
            conn.login(s["user"], s["password"])
        except imaplib.IMAP4.error as e:
            _close(conn)
            failures.append("{}: 로그인 실패 ({})".format(host, e))
            continue
        return conn

    raise RuntimeError(
        "메일 서버에 붙지 못했습니다.\n  " + "\n  ".join(failures)
        + "\n  .env 의 IMAP_HOST / IMAP_USER / IMAP_PASS 를 확인하세요. "
          "(하이웍스는 보통 mail.hiworks.co.kr:993)")


def _close(conn) -> None:
    for fn in ("close", "logout"):
        try:
            getattr(conn, fn)()
        except (imaplib.IMAP4.error, OSError):
            pass


def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return str(value)


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = _TAG.sub(" ", text)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    return text


def _body(msg, limit: int) -> str:
    plain, html = "", ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            chunk = payload.decode(charset, "replace")
        except (LookupError, UnicodeDecodeError):
            chunk = payload.decode("utf-8", "replace")
        if ctype == "text/plain":
            plain += chunk + "\n"
        else:
            html += chunk + "\n"

    text = plain.strip() or _html_to_text(html)
    text = _NL.sub("\n\n", _WS.sub(" ", text)).strip()
    if limit and len(text) > limit:
        text = text[:limit] + "\n…(본문 생략)"
    return text


def _attachments(msg) -> list:
    names = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                names.append(_decode(part.get_filename()) or "이름없음")
    return names


def _fetch(uids, conn, s: dict, body_limit: int) -> list:
    mails = []
    for uid in uids:
        status, data = conn.uid("FETCH", str(uid), "(RFC822)")
        if status != "OK" or not data or not data[0]:
            continue
        raw = data[0][1]
        if not isinstance(raw, (bytes, bytearray)):
            continue
        msg = email.message_from_bytes(raw)

        try:
            received = email.utils.parsedate_to_datetime(msg.get("Date"))
            when = received.astimezone(context.cfg.tz).strftime("%m/%d %H:%M")
        except (TypeError, ValueError):
            when = "?"

        mails.append({
            "uid": uid,
            "when": when,
            "subject": _decode(msg.get("Subject")) or "(제목 없음)",
            "from": _decode(msg.get("From")),
            "to": _decode(msg.get("To")),
            "cc": _decode(msg.get("Cc")),
            "attachments": _attachments(msg),
            "body": _body(msg, body_limit),
        })
    return mails


def _render(mails: list, header: str) -> str:
    if not mails:
        return header + "\n(해당하는 메일이 없습니다)"
    rows = [header, ""]
    for m in mails:
        rows.append("── [{}] {} ──".format(m["uid"], m["when"]))
        rows.append("제목: " + m["subject"])
        rows.append("보낸사람: " + m["from"])
        if m["cc"]:
            rows.append("참조: " + m["cc"])
        if m["attachments"]:
            rows.append("첨부: " + ", ".join(m["attachments"]))
        rows.append("본문:")
        rows.append(m["body"] or "(본문 없음)")
        rows.append("")
    return "\n".join(rows)


# ────────────────────────────────────────────────────────────
# 도구
# ────────────────────────────────────────────────────────────

@tool(
    name="read_mail",
    description=(
        "메일함에서 최근 메일을 읽어온다. '오늘 온 메일 정리해줘', '어제 메일 뭐 왔어?' "
        "같은 요청에 쓴다. 요약은 네가 한다. 반환된 내용만 근거로 말하고 지어내지 마라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "hours": {"type": "integer",
                      "description": "최근 몇 시간 이내 메일인지. 하루면 24, 어제까지면 48. 기본 24"},
            "max_count": {"type": "integer", "description": "최대 몇 통까지 가져올지. 기본 30"},
            "unread_only": {"type": "boolean", "description": "안 읽은 메일만 볼지. 기본 false"},
        },
        "required": [],
    },
)
def read_mail(hours: int = 24, max_count: int = 30, unread_only: bool = False) -> str:
    s = _settings()
    hours = max(1, min(int(hours or 24), 24 * 30))
    max_count = max(1, min(int(max_count or 30), 60))

    conn = _connect(s)
    try:
        status, _ = conn.select('"{}"'.format(s["folder"]), readonly=True)
        if status != "OK":
            status, _ = conn.select(s["folder"], readonly=True)
        if status != "OK":
            return "메일함 '{}' 을 열지 못했습니다.".format(s["folder"])

        since = (datetime.now(context.cfg.tz) - timedelta(hours=hours))
        criteria = ["SINCE", since.strftime("%d-%b-%Y")]
        if unread_only:
            criteria = ["UNSEEN"] + criteria

        status, data = conn.uid("SEARCH", None, *criteria)
        if status != "OK":
            return "메일 검색에 실패했습니다."

        uids = [int(x) for x in (data[0] or b"").split()]
        uids.sort()
        total = len(uids)
        uids = uids[-max_count:]
        mails = _fetch(uids, conn, s, s["body_limit"])
    finally:
        _close(conn)

    # IMAP 의 SINCE 는 '날짜' 단위라 시간 단위로 한 번 더 거른다
    cutoff = datetime.now(context.cfg.tz) - timedelta(hours=hours)
    filtered = []
    for m in mails:
        try:
            when = datetime.strptime(m["when"], "%m/%d %H:%M").replace(
                year=cutoff.year, tzinfo=context.cfg.tz)
            if when >= cutoff - timedelta(days=1):
                filtered.append(m)
        except ValueError:
            filtered.append(m)

    header = "최근 {}시간 메일 {}통{}".format(
        hours, len(filtered),
        " (전체 {}통 중 최근 것만)".format(total) if total > len(filtered) else "")
    return _render(filtered, header)


@tool(
    name="search_mail",
    description=(
        "제목이나 보낸사람으로 메일을 찾는다. 'CCX 관련 메일 찾아줘', "
        "'김과장이 보낸 메일' 같은 요청에 쓴다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "찾을 단어 (제목/본문에서 검색)"},
            "sender": {"type": "string", "description": "보낸사람 주소나 이름 일부. 없으면 빈 문자열"},
            "days": {"type": "integer", "description": "며칠 이내에서 찾을지. 기본 14"},
            "max_count": {"type": "integer", "description": "최대 몇 통. 기본 15"},
        },
        "required": ["keyword"],
    },
)
def search_mail(keyword: str, sender: str = "", days: int = 14, max_count: int = 15) -> str:
    s = _settings()
    days = max(1, min(int(days or 14), 365))
    max_count = max(1, min(int(max_count or 15), 40))

    conn = _connect(s)
    try:
        status, _ = conn.select('"{}"'.format(s["folder"]), readonly=True)
        if status != "OK":
            conn.select(s["folder"], readonly=True)

        since = (datetime.now(context.cfg.tz) - timedelta(days=days)).strftime("%d-%b-%Y")
        criteria = ["SINCE", since]
        if sender:
            criteria += ["FROM", sender]

        # 한글 검색어는 서버가 UTF-8 검색을 지원해야 한다. 안 되면 제목으로 직접 거른다
        uids = []
        if keyword:
            try:
                status, data = conn.uid(
                    "SEARCH", "CHARSET", "UTF-8", *(criteria + ["TEXT", keyword]))
                if status == "OK":
                    uids = [int(x) for x in (data[0] or b"").split()]
            except imaplib.IMAP4.error:
                uids = []

        if not uids:
            status, data = conn.uid("SEARCH", None, *criteria)
            if status != "OK":
                return "메일 검색에 실패했습니다."
            uids = [int(x) for x in (data[0] or b"").split()]
            uids = uids[-200:]  # 직접 거를 땐 범위를 제한
            mails = _fetch(uids, conn, s, s["body_limit"])
            low = keyword.lower()
            mails = [m for m in mails
                     if low in m["subject"].lower() or low in m["body"].lower()]
            mails = mails[-max_count:]
        else:
            uids.sort()
            mails = _fetch(uids[-max_count:], conn, s, s["body_limit"])
    finally:
        _close(conn)

    return _render(mails, "'{}' 검색 결과 {}통 (최근 {}일)".format(keyword, len(mails), days))


@tool(
    name="read_one_mail",
    description="특정 메일 한 통의 전체 본문을 본다. read_mail/search_mail 결과의 대괄호 안 번호를 쓴다.",
    parameters={
        "type": "object",
        "properties": {"uid": {"type": "integer", "description": "메일 번호"}},
        "required": ["uid"],
    },
)
def read_one_mail(uid: int) -> str:
    s = _settings()
    conn = _connect(s)
    try:
        status, _ = conn.select('"{}"'.format(s["folder"]), readonly=True)
        if status != "OK":
            conn.select(s["folder"], readonly=True)
        mails = _fetch([int(uid)], conn, s, 20000)
    finally:
        _close(conn)

    if not mails:
        return "그 번호의 메일을 찾지 못했습니다: {}".format(uid)
    return _render(mails, "메일 전문")
