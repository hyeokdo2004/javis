"""바깥 인터넷을 쓰는 도구들이 같이 쓰는 얇은 HTTP 계층.

키가 필요 없는 공개 API·RSS 만 씁니다. 오류는 사람이 읽을 수 있게 바꿔서
도구가 그대로 돌려줄 수 있게 합니다.
"""
from __future__ import annotations

import json
import ssl
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
        raise NetError(_why(e.reason)) from e
    except ssl.SSLError as e:
        raise NetError(_why(e)) from e
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


def _why(reason) -> str:
    text = str(reason)
    if "WRONG_VERSION_NUMBER" in text or "UNEXPECTED_EOF" in text:
        return ("연결이 중간에서 가로채인 것 같습니다 ({}). 회사 방화벽·보안 프로그램이 "
                "이 주소를 막고 있을 때 나는 증상입니다. 프록시를 쓰는 환경이면 .env 에 "
                "HTTPS_PROXY=http://프록시주소:포트 를 넣어보세요.").format(text)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return ("보안 인증서를 확인하지 못했습니다 ({}). 회사 보안 프로그램이 통신을 "
                "들여다보는 환경일 수 있습니다. 전산 담당자에게 사내 인증서 설정을 문의하세요."
                ).format(text)
    if "getaddrinfo" in text or "Name or service" in text or "11001" in text:
        return "주소를 찾지 못했습니다 ({}). 인터넷 연결이나 DNS 를 확인하세요.".format(text)
    if "timed out" in text.lower():
        return "응답이 없어 시간이 초과됐습니다 ({}). 방화벽에 막혔을 수 있습니다.".format(text)
    return "연결하지 못했습니다: {}. 인터넷 연결이나 회사 방화벽을 확인하세요.".format(text)


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
