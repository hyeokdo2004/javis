"""메일 도구 — 메일을 읽어온다. 요약은 비서(모델)가 한다.

IMAP 과 POP3 를 둘 다 지원합니다.
  · IMAP  — Office365, Gmail, 네이버 등. 폴더·안읽음·서버 검색을 쓸 수 있습니다.
  · POP3  — 하이웍스(pop3s.hiworks.com:995) 처럼 IMAP 을 안 여는 곳.
            받은편지함만 통째로 내려받아 이쪽에서 거릅니다.

어느 쪽을 쓸지는 config.json 의 mail.protocol 로 정합니다 (기본 auto = 되는 쪽).
"""
from __future__ import annotations

import email
import email.utils
import imaplib
import poplib
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header

from core import context
from core.config import env
from core.registry import tool

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")

# POP3 는 통째로 내려받아 거르므로, 한 번에 훑을 통수를 제한한다
_POP3_SCAN_LIMIT = 120


# ────────────────────────────────────────────────────────────
# 설정
# ────────────────────────────────────────────────────────────

def _hosts(primary, fallbacks) -> list:
    out = []
    for h in [primary] + list(fallbacks or []):
        if h and h not in out:
            out.append(h)
    return out


def _settings() -> dict:
    mail_cfg = (context.cfg.mail if context.cfg else {}) or {}
    protocol = (env("MAIL_PROTOCOL") or mail_cfg.get("protocol", "auto")).lower()
    if protocol not in ("auto", "imap", "pop3"):
        protocol = "auto"
    return {
        "protocol": protocol,
        "imap_hosts": _hosts(env("IMAP_HOST") or mail_cfg.get("imap_host"),
                             mail_cfg.get("imap_fallbacks")),
        "imap_port": int(env("IMAP_PORT") or mail_cfg.get("imap_port", 993)),
        "pop3_hosts": _hosts(env("POP3_HOST") or mail_cfg.get("pop3_host"),
                             mail_cfg.get("pop3_fallbacks")),
        "pop3_port": int(env("POP3_PORT") or mail_cfg.get("pop3_port", 995)),
        "user": env("IMAP_USER", required=True),
        "password": env("IMAP_PASS", required=True),
        "folder": mail_cfg.get("folder", "INBOX"),
        "body_limit": int(mail_cfg.get("body_limit", 1500)),
    }


# ────────────────────────────────────────────────────────────
# 메일 파싱 (프로토콜과 무관)
# ────────────────────────────────────────────────────────────

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


def _record(uid, raw: bytes, body_limit: int) -> dict:
    """원본 메일 바이트 → 화면에 보여줄 한 통의 정보."""
    msg = email.message_from_bytes(raw)
    try:
        when = email.utils.parsedate_to_datetime(msg.get("Date"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=context.cfg.tz)
        when = when.astimezone(context.cfg.tz)
    except (TypeError, ValueError):
        when = None

    return {
        "uid": uid,
        "dt": when,
        "when": when.strftime("%m/%d %H:%M") if when else "?",
        "subject": _decode(msg.get("Subject")) or "(제목 없음)",
        "from": _decode(msg.get("From")),
        "to": _decode(msg.get("To")),
        "cc": _decode(msg.get("Cc")),
        "attachments": _attachments(msg),
        "body": _body(msg, body_limit),
    }


def _matches(m: dict, keyword: str, sender: str) -> bool:
    if keyword:
        low = keyword.lower()
        if low not in m["subject"].lower() and low not in m["body"].lower():
            return False
    if sender and sender.lower() not in m["from"].lower():
        return False
    return True


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
# 메일함 — IMAP / POP3 를 같은 모양으로 감싼다
# ────────────────────────────────────────────────────────────

class ImapBox:
    kind = "IMAP"

    def __init__(self, conn, s: dict):
        self.conn = conn
        self.s = s
        status, _ = conn.select('"{}"'.format(s["folder"]), readonly=True)
        if status != "OK":
            status, _ = conn.select(s["folder"], readonly=True)
        if status != "OK":
            raise RuntimeError("메일함 '{}' 을 열지 못했습니다.".format(s["folder"]))

    def _uids(self, criteria: list) -> list:
        status, data = self.conn.uid("SEARCH", None, *criteria)
        if status != "OK":
            raise RuntimeError("메일 검색에 실패했습니다.")
        return sorted(int(x) for x in (data[0] or b"").split())

    def _fetch(self, uids: list, body_limit: int) -> list:
        mails = []
        for uid in uids:
            status, data = self.conn.uid("FETCH", str(uid), "(RFC822)")
            if status != "OK" or not data or not data[0]:
                continue
            raw = data[0][1]
            if isinstance(raw, (bytes, bytearray)):
                mails.append(_record(uid, raw, body_limit))
        return mails

    def recent(self, since, limit: int, body_limit: int, unread_only: bool = False):
        criteria = ["SINCE", since.strftime("%d-%b-%Y")]
        if unread_only:
            criteria = ["UNSEEN"] + criteria
        uids = self._uids(criteria)
        return self._fetch(uids[-limit:], body_limit), len(uids)

    def search(self, keyword: str, sender: str, since, limit: int, body_limit: int):
        criteria = ["SINCE", since.strftime("%d-%b-%Y")]
        if sender:
            criteria += ["FROM", sender]

        # 한글 검색어는 서버가 UTF-8 검색을 지원해야 한다. 안 되면 직접 거른다
        if keyword:
            try:
                status, data = self.conn.uid(
                    "SEARCH", "CHARSET", "UTF-8", *(criteria + ["TEXT", keyword]))
                if status == "OK":
                    uids = sorted(int(x) for x in (data[0] or b"").split())
                    if uids:
                        return self._fetch(uids[-limit:], body_limit)
            except imaplib.IMAP4.error:
                pass

        uids = self._uids(criteria)[-200:]
        mails = [m for m in self._fetch(uids, body_limit) if _matches(m, keyword, sender)]
        return mails[-limit:]

    def one(self, uid: int, body_limit: int):
        return self._fetch([uid], body_limit)

    def close(self):
        for fn in ("close", "logout"):
            try:
                getattr(self.conn, fn)()
            except (imaplib.IMAP4.error, OSError):
                pass


class Pop3Box:
    """POP3 는 검색도 폴더도 없다. 최근 것부터 내려받아 여기서 거른다."""

    kind = "POP3"

    def __init__(self, conn, s: dict):
        self.conn = conn
        self.s = s
        self.total = len(conn.list()[1])

    def _download(self, count: int, body_limit: int) -> list:
        """뒤(최신)에서부터 count 통. 번호는 서버가 준 순번 그대로 쓴다."""
        first = max(1, self.total - count + 1)
        mails = []
        for num in range(first, self.total + 1):
            try:
                raw = b"\r\n".join(self.conn.retr(num)[1])
            except poplib.error_proto:
                continue
            mails.append(_record(num, raw, body_limit))
        return mails

    def recent(self, since, limit: int, body_limit: int, unread_only: bool = False):
        mails = self._download(min(max(limit, 20), _POP3_SCAN_LIMIT), body_limit)
        return mails[-limit:], self.total

    def search(self, keyword: str, sender: str, since, limit: int, body_limit: int):
        mails = self._download(_POP3_SCAN_LIMIT, body_limit)
        mails = [m for m in mails
                 if (m["dt"] is None or m["dt"] >= since)
                 and _matches(m, keyword, sender)]
        return mails[-limit:]

    def one(self, uid: int, body_limit: int):
        try:
            raw = b"\r\n".join(self.conn.retr(int(uid))[1])
        except poplib.error_proto:
            return []
        return [_record(int(uid), raw, body_limit)]

    def close(self):
        try:
            self.conn.quit()
        except (poplib.error_proto, OSError):
            pass


def _open_imap(host: str, s: dict):
    conn = imaplib.IMAP4_SSL(host, s["imap_port"], timeout=20)
    conn.login(s["user"], s["password"])
    return ImapBox(conn, s)


def _open_pop3(host: str, s: dict):
    conn = poplib.POP3_SSL(host, s["pop3_port"], timeout=20)
    conn.user(s["user"])
    conn.pass_(s["password"])
    return Pop3Box(conn, s)


def open_box(s: dict):
    """설정된 프로토콜·주소를 순서대로 시도해서 열리는 메일함을 준다."""
    plan = []
    if s["protocol"] in ("auto", "imap"):
        plan += [("imap", h) for h in s["imap_hosts"]]
    if s["protocol"] in ("auto", "pop3"):
        plan += [("pop3", h) for h in s["pop3_hosts"]]

    failures = []
    for proto, host in plan:
        try:
            return (_open_imap if proto == "imap" else _open_pop3)(host, s)
        except (imaplib.IMAP4.error, poplib.error_proto) as e:
            failures.append("{} {}: 로그인 실패 ({})".format(proto.upper(), host, e))
        except OSError as e:  # 이름 못 찾음 / 접속 불가 / 시간 초과
            failures.append("{} {}: 접속 불가 ({})".format(proto.upper(), host, type(e).__name__))

    raise RuntimeError(
        "메일 서버에 붙지 못했습니다.\n  " + "\n  ".join(failures or ["시도할 주소가 없습니다"])
        + "\n  'python assistant.py 진단' 을 돌려 어떤 주소가 되는지 확인하세요."
          " (하이웍스는 POP3 pop3s.hiworks.com:995, 메일 전용 비밀번호가 따로 있습니다)")


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
    cutoff = datetime.now(context.cfg.tz) - timedelta(hours=hours)

    box = open_box(s)
    try:
        mails, total = box.recent(cutoff, max_count, s["body_limit"], unread_only)
    finally:
        box.close()

    # 서버 검색은 '날짜' 단위라 시간 단위로 한 번 더 거른다
    mails = [m for m in mails if m["dt"] is None or m["dt"] >= cutoff]

    note = ""
    if box.kind == "POP3" and unread_only:
        note = " (POP3 는 안읽음 표시가 없어 전체에서 가져왔습니다)"
    header = "최근 {}시간 메일 {}통{}{}".format(
        hours, len(mails),
        " (메일함 {}통 중 최근 것만)".format(total) if total > len(mails) else "", note)
    return _render(mails, header)


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
    since = datetime.now(context.cfg.tz) - timedelta(days=days)

    box = open_box(s)
    try:
        mails = box.search(keyword, sender, since, max_count, s["body_limit"])
    finally:
        box.close()

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
    box = open_box(s)
    try:
        mails = box.one(int(uid), 20000)
    finally:
        box.close()

    if not mails:
        return "그 번호의 메일을 찾지 못했습니다: {}".format(uid)
    return _render(mails, "메일 전문")
