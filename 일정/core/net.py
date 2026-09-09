"""바깥 인터넷을 쓰는 도구들이 같이 쓰는 얇은 HTTP 계층.

키가 필요 없는 공개 API·RSS 만 씁니다. 오류는 사람이 읽을 수 있게 바꿔서
도구가 그대로 돌려줄 수 있게 합니다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

# HTTP 헤더는 latin-1 로만 보낼 수 있다. 한글을 넣으면 요청 자체가 터진다.
UA = "ai-bisu/1.0 (personal work assistant)"


class NetError(RuntimeError):
    """사람이 읽을 수 있게 정리한 네트워크 오류."""


def fetch(url: str, params: dict = None, *, timeout: int = 20,
          max_bytes: int = 2_000_000) -> str:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"}
    # 주소에 한글이 남아 있으면 여기서 퍼센트 인코딩해 둔다
    url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")
    req = urllib.request.Request(url, headers=_ascii(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            charset = resp.headers.get_content_charset()
    except urllib.error.HTTPError as e:
        raise NetError("서버가 오류를 돌려줬습니다 ({} {}). 주소가 맞는지 확인하세요."
                       .format(e.code, e.reason)) from e
    except urllib.error.URLError as e:
        raise NetError("연결하지 못했습니다: {}. 인터넷 연결이나 회사 방화벽을 확인하세요."
                       .format(e.reason)) from e
    except OSError as e:
        raise NetError("연결 중 오류: {}".format(e)) from e

    for enc in (charset, "utf-8", "cp949", "euc-kr"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _ascii(headers: dict) -> dict:
    """헤더 값에 latin-1 로 못 보내는 글자가 있으면 떼어낸다."""
    out = {}
    for key, value in headers.items():
        try:
            value.encode("latin-1")
        except UnicodeEncodeError:
            value = value.encode("ascii", "ignore").decode("ascii")
        out[key] = value
    return out


def fetch_json(url: str, params: dict = None, *, timeout: int = 20) -> dict:
    text = fetch(url, params, timeout=timeout)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise NetError("응답을 해석하지 못했습니다: {}".format(e)) from e
