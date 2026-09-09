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

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code  # HTTP 상태코드 (모르면 0)

    @property
    def retry_other_model(self) -> bool:
        """다른 모델로 바꿔서 다시 해볼 만한 오류인가."""
        return self.code in (404, 429) or 500 <= self.code < 600


class ToolCall:
    def __init__(self, name: str, args: dict, call_id: str = "", signature: str = ""):
        self.name = name
        self.args = args or {}
        self.call_id = call_id
        self.signature = signature  # Gemini 3 의 thoughtSignature

    def __repr__(self):
        return "ToolCall({}, {})".format(self.name, self.args)


class Reply:
    def __init__(self, text: str = "", calls=None, raw=None, parts=None):
        self.text = text or ""
        self.calls = calls or []
        self.raw = raw
        # 모델이 준 parts 원본. Gemini 3 부터는 functionCall 에 thoughtSignature 가
        # 붙어 오고, 다음 요청에 그대로 돌려보내지 않으면 400 이 난다.
        self.parts = parts or []


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
        if "no longer available" in msg.lower():
            return ("이 모델은 이제 신규 사용자에게 제공되지 않습니다 (404): {}\n"
                    "config.json 의 model.id 를 안내된 모델로 바꾸세요 "
                    "(gemini-flash-latest 로 두면 항상 최신 flash 를 씁니다).").format(msg)
        return "모델 이름이나 주소를 찾을 수 없습니다 (404): {}".format(msg)
    if 500 <= err.code < 600:
        return ("구글 쪽 일시 오류 ({}) — 그 모델이 지금 몰려서 밀리는 중입니다: {}\n"
                "잠시 뒤 다시 하거나, config.json 의 model.fallbacks 에 다른 모델을 넣어두면 "
                "자동으로 바꿔서 시도합니다.").format(err.code, msg)
    return "오류 {}: {}".format(err.code, msg)


# ────────────────────────────────────────────────────────────

class Gemini:
    def __init__(self, api_key: str, model: str, *, api_style: str = "auto",
                 data_dir=None, retries: int = 2, fallbacks=None, on_switch=None):
        self.api_key = api_key
        self.model = model
        self.fallbacks = [m for m in (fallbacks or []) if m and m != model]
        self.on_switch = on_switch  # 모델을 바꿨을 때 알려줄 콜백(선택)
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
        models = [self.model] + list(self.fallbacks)
        last_error = None

        for index, model in enumerate(models):
            try:
                reply = self._chat_with_model(model, system, history, tools)
            except LLMError as e:
                last_error = e
                # 과부하(503)·한도(429)·없는 모델(404) 이면 다음 후보 모델로
                if e.retry_other_model and index + 1 < len(models):
                    if self.on_switch:
                        self.on_switch(model, models[index + 1], str(e))
                    continue
                raise
            if model != self.model:
                # 이번 세션 동안은 살아 있는 모델을 계속 쓴다
                self.model = model
            return reply

        raise last_error or LLMError("어떤 모델로도 응답을 받지 못했습니다.")

    def _chat_with_model(self, model: str, system: str, history: list, tools: list) -> Reply:
        styles = [self.style] if self.style else ["interactions", "generate"]
        last_error = None

        for style in styles:
            for attempt in range(self.retries + 1):
                try:
                    reply = (self._chat_interactions if style == "interactions"
                             else self._chat_generate)(model, system, history, tools)
                    self._remember_style(style)
                    return reply
                except urllib.error.HTTPError as e:
                    message = _explain(e)
                    # 형식이 안 맞는 경우엔 다른 방식으로 넘어간다
                    if e.code in (400, 404) and not self.style:
                        last_error = LLMError(message, e.code)
                        break
                    if e.code == 429 or 500 <= e.code < 600:
                        if attempt < self.retries:
                            time.sleep(2 * (attempt + 1))
                            continue
                    raise LLMError(message, e.code) from e
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
    def _chat_interactions(self, model: str, system: str, history: list, tools: list) -> Reply:
        payload = {"model": model, "input": _to_contents(history)}
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
    def _chat_generate(self, model: str, system: str, history: list, tools: list) -> Reply:
        payload = {"contents": _to_contents(history)}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"],
                 "parameters": t["parameters"]}
                for t in tools
            ]}]
        url = "{}/models/{}:generateContent".format(BASE, model)
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
            # 모델이 준 parts 를 그대로 돌려주는 게 가장 안전하다 (thoughtSignature 보존)
            parts = list(turn.get("parts") or [])
            if not parts:
                if turn.get("text"):
                    parts.append({"text": turn["text"]})
                for call in turn.get("calls") or []:
                    part = {"functionCall": {"name": call.name, "args": call.args}}
                    if call.signature:
                        part["thoughtSignature"] = call.signature
                    parts.append(part)
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
    parts = (candidates[0].get("content") or {}).get("parts", []) or []
    for part in parts:
        if "text" in part and part["text"]:
            texts.append(part["text"])
        fc = part.get("functionCall")
        if fc:
            calls.append(ToolCall(fc.get("name", ""), fc.get("args") or {},
                                  signature=part.get("thoughtSignature", "")))
    return Reply("\n".join(texts).strip(), calls, data, parts)


def _parse_interactions(data: dict) -> Reply:
    texts, calls = [], []

    for step in data.get("steps") or []:
        kind = step.get("type")
        if kind == "function_call":
            calls.append(ToolCall(step.get("name", ""), step.get("arguments") or {},
                                  step.get("id", ""),
                                  step.get("thought_signature", "")))
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
