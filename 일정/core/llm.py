"""Gemini 호출 계층 — 표준 라이브러리만 사용 (pip 설치 불필요).

Gemini 는 지금 두 가지 방식이 공존합니다.
  1) interactions   (신형) POST /v1beta/interactions
  2) generateContent(구형) POST /v1beta/models/{model}:generateContent

어느 쪽이 살아 있는지는 계정/시점에 따라 다를 수 있어서, 처음 한 번 둘 다 찔러보고
되는 쪽을 data/api_style.txt 에 기억해 둡니다. config.json 에서 강제할 수도 있습니다.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"
STYLE_FILE = "api_style.txt"


class LLMError(RuntimeError):
    """사람이 읽을 수 있는 형태로 정리된 API 오류."""


class ToolCall:
    def __init__(self, name: str, args: dict, call_id: str = ""):
        self.name = name
        self.args = args or {}
        self.call_id = call_id

    def __repr__(self):
        return "ToolCall({}, {})".format(self.name, self.args)


class Reply:
    def __init__(self, text: str = "", calls=None, raw=None):
        self.text = text or ""
        self.calls = calls or []
        self.raw = raw


# ────────────────────────────────────────────────────────────
# HTTP
# ────────────────────────────────────────────────────────────

def _post(url: str, api_key: str, payload: dict, timeout: int = 120) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, api_key: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"x-goog-api-key": api_key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _explain(err: urllib.error.HTTPError) -> str:
    detail = err.read().decode("utf-8", "replace")
    try:
        msg = json.loads(detail).get("error", {}).get("message", "")
    except json.JSONDecodeError:
        msg = detail[:400]

    if err.code == 400:
        return "요청 형식 오류 (400): {}".format(msg)
    if err.code in (401, 403):
        return ("API 키가 잘못됐거나 권한이 없습니다 ({}). "
                "https://aistudio.google.com/apikey 에서 키를 다시 확인하세요.\n{}"
                ).format(err.code, msg)
    if err.code == 429:
        return ("무료 사용량 한도에 걸렸습니다 (429). 잠시 뒤 다시 시도하세요. "
                "무료 티어는 분당 요청 수와 하루 요청 수에 제한이 있습니다.\n{}").format(msg)
    if err.code == 404:
        return "모델 이름이나 주소를 찾을 수 없습니다 (404): {}".format(msg)
    if 500 <= err.code < 600:
        return "구글 쪽 일시 오류 ({}): {}".format(err.code, msg)
    return "오류 {}: {}".format(err.code, msg)


# ────────────────────────────────────────────────────────────

class Gemini:
    def __init__(self, api_key: str, model: str, *, api_style: str = "auto",
                 data_dir=None, retries: int = 2):
        self.api_key = api_key
        self.model = model
        self.retries = retries
        self.data_dir = data_dir
        self.style = api_style if api_style in ("interactions", "generate") else None
        if self.style is None and data_dir is not None:
            remembered = (data_dir / STYLE_FILE)
            if remembered.exists():
                value = remembered.read_text(encoding="utf-8").strip()
                if value in ("interactions", "generate"):
                    self.style = value

    # ── 공개 API ────────────────────────────────────────────
    def chat(self, system: str, history: list, tools: list) -> Reply:
        """history 는 중립 형식. llm 백엔드에 맞게 변환해서 보낸다.

        history 항목:
          {"role": "user",      "text": "..."}
          {"role": "assistant", "text": "...", "calls": [ToolCall...]}
          {"role": "tool",      "name": "...", "call_id": "...", "result": "..."}
        """
        styles = [self.style] if self.style else ["interactions", "generate"]
        last_error = None

        for style in styles:
            for attempt in range(self.retries + 1):
                try:
                    reply = (self._chat_interactions if style == "interactions"
                             else self._chat_generate)(system, history, tools)
                    self._remember_style(style)
                    return reply
                except urllib.error.HTTPError as e:
                    message = _explain(e)
                    # 형식이 안 맞는 경우엔 다른 방식으로 넘어간다
                    if e.code in (400, 404) and not self.style:
                        last_error = LLMError(message)
                        break
                    if e.code == 429 or 500 <= e.code < 600:
                        if attempt < self.retries:
                            time.sleep(2 * (attempt + 1))
                            continue
                    raise LLMError(message) from e
                except urllib.error.URLError as e:
                    if attempt < self.retries:
                        time.sleep(2 * (attempt + 1))
                        continue
                    raise LLMError("네트워크에 연결하지 못했습니다: {}".format(e.reason)) from e

        raise last_error or LLMError("어떤 방식으로도 응답을 받지 못했습니다.")

    def list_models(self) -> list:
        data = _get(BASE + "/models", self.api_key)
        out = []
        for m in data.get("models", []):
            name = (m.get("name") or "").replace("models/", "")
            methods = m.get("supportedGenerationMethods") or m.get("supportedActions") or []
            out.append({"name": name, "methods": methods,
                        "display": m.get("displayName", "")})
        return out

    # ── 내부 ────────────────────────────────────────────────
    def _remember_style(self, style: str) -> None:
        if self.style == style or self.data_dir is None:
            return
        self.style = style
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            (self.data_dir / STYLE_FILE).write_text(style, encoding="utf-8")
        except OSError:
            pass

    # 신형: /v1beta/interactions
    def _chat_interactions(self, system: str, history: list, tools: list) -> Reply:
        payload = {"model": self.model, "input": _to_contents(history)}
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {"type": "function", "name": t["name"],
                 "description": t["description"], "parameters": t["parameters"]}
                for t in tools
            ]
        data = _post(BASE + "/interactions", self.api_key, payload)
        return _parse_interactions(data)

    # 구형: /v1beta/models/{model}:generateContent
    def _chat_generate(self, system: str, history: list, tools: list) -> Reply:
        payload = {"contents": _to_contents(history)}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"],
                 "parameters": t["parameters"]}
                for t in tools
            ]}]
        url = "{}/models/{}:generateContent".format(BASE, self.model)
        data = _post(url, self.api_key, payload)
        return _parse_generate(data)


# ────────────────────────────────────────────────────────────
# 변환
# ────────────────────────────────────────────────────────────

def _to_contents(history: list) -> list:
    """중립 형식 → Gemini contents 배열 (두 방식이 같은 모양을 받아들인다)."""
    out = []
    for turn in history:
        role = turn.get("role")

        if role == "user":
            out.append({"role": "user", "parts": [{"text": turn.get("text", "")}]})

        elif role == "assistant":
            parts = []
            if turn.get("text"):
                parts.append({"text": turn["text"]})
            for call in turn.get("calls") or []:
                parts.append({"functionCall": {"name": call.name, "args": call.args}})
            if parts:
                out.append({"role": "model", "parts": parts})

        elif role == "tool":
            out.append({
                "role": "user",
                "parts": [{"functionResponse": {
                    "name": turn.get("name", ""),
                    "response": {"result": turn.get("result", "")},
                }}],
            })
    return out


def _parse_generate(data: dict) -> Reply:
    candidates = data.get("candidates") or []
    if not candidates:
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise LLMError("Gemini 가 응답을 거부했습니다 (사유: {}).".format(blocked))
        raise LLMError("응답이 비어 있습니다.")

    texts, calls = [], []
    for part in (candidates[0].get("content") or {}).get("parts", []):
        if "text" in part and part["text"]:
            texts.append(part["text"])
        fc = part.get("functionCall")
        if fc:
            calls.append(ToolCall(fc.get("name", ""), fc.get("args") or {}))
    return Reply("\n".join(texts).strip(), calls, data)


def _parse_interactions(data: dict) -> Reply:
    texts, calls = [], []

    for step in data.get("steps") or []:
        kind = step.get("type")
        if kind == "function_call":
            calls.append(ToolCall(step.get("name", ""), step.get("arguments") or {},
                                  step.get("id", "")))
        elif kind in ("model_output", "message", "text"):
            texts.append(_step_text(step))

    # 형태가 다를 수 있으니 대비책을 둔다
    if not texts and not calls:
        if isinstance(data.get("output"), str):
            texts.append(data["output"])
        elif data.get("candidates"):
            return _parse_generate(data)
        else:
            raise LLMError("응답을 해석하지 못했습니다: {}".format(
                json.dumps(data, ensure_ascii=False)[:300]))

    return Reply("\n".join(t for t in texts if t).strip(), calls, data)


def _step_text(step: dict) -> str:
    if isinstance(step.get("text"), str):
        return step["text"]
    content = step.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""
