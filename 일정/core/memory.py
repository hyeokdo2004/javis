"""기억.

두 층으로 나눕니다.

  짧은 사실   data/memory.json   — 항상 프롬프트에 들어감 ("A사 담당은 김과장")
  긴 문서     data/notes/*.md    — 제목만 프롬프트에 넣고, 필요할 때 도구로 읽음

긴 문서 쪽이 핵심입니다. 다른 AI로 정리한 절차서·조사 결과·스크립트 사용법을
notes 폴더에 .md 파일로 넣어두기만 하면 비서가 그때부터 그걸 알고 씁니다.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

MAX_HISTORY_TURNS = 40
FACT_LIMIT = 200


def _slug(text: str) -> str:
    """파일명으로 쓸 수 있게만 다듬는다. 공백은 그대로 둬서 제목이 안 바뀌게."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:60] or "메모"


class Memory:
    def __init__(self, data_dir: Path, tz):
        self.dir = data_dir
        self.tz = tz
        self.notes_dir = data_dir / "notes"
        self.facts_path = data_dir / "memory.json"
        self.history_path = data_dir / "conversation.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.notes_dir.mkdir(parents=True, exist_ok=True)

    def _stamp(self) -> str:
        return datetime.now(self.tz).isoformat(timespec="seconds")

    # ── 짧은 사실 ───────────────────────────────────────────
    def facts(self) -> list:
        if not self.facts_path.exists():
            return []
        try:
            return json.loads(self.facts_path.read_text(encoding="utf-8")).get("facts", [])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []

    def _save_facts(self, facts: list) -> None:
        self.facts_path.write_text(
            json.dumps({"facts": facts[-FACT_LIMIT:]}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def remember(self, text: str, tags: str = "") -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("기억할 내용이 비어 있습니다")
        facts = self.facts()
        # 같은 내용이 이미 있으면 새로 만들지 않는다
        for f in facts:
            if f["text"].strip() == text:
                return f
        entry = {"id": uuid.uuid4().hex[:6], "text": text,
                 "tags": tags, "created_at": self._stamp()}
        facts.append(entry)
        self._save_facts(facts)
        return entry

    def forget(self, fact_id: str) -> bool:
        facts = self.facts()
        kept = [f for f in facts if f["id"] != fact_id and not f["id"].endswith(fact_id)]
        if len(kept) == len(facts):
            return False
        self._save_facts(kept)
        return True

    def search_facts(self, query: str) -> list:
        q = (query or "").lower()
        if not q:
            return self.facts()
        return [f for f in self.facts()
                if q in f["text"].lower() or q in (f.get("tags") or "").lower()]

    # ── 긴 문서 (notes) ─────────────────────────────────────
    def note_list(self) -> list:
        out = []
        for p in sorted(self.notes_dir.glob("*.md")):
            try:
                first = ""
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        first = line.strip().lstrip("# ").strip()
                        break
                out.append({"name": p.stem, "summary": first[:80],
                            "chars": p.stat().st_size})
            except OSError:
                continue
        return out

    def note_read(self, name: str, limit: int = 12000) -> str:
        name = (name or "").strip()
        candidates = [p for p in self.notes_dir.glob("*.md")
                      if p.stem == name or name.lower() in p.stem.lower()]
        if not candidates:
            raise FileNotFoundError(
                "그런 문서가 없습니다: {}. 있는 것: {}".format(
                    name, ", ".join(n["name"] for n in self.note_list()) or "없음"))
        text = candidates[0].read_text(encoding="utf-8")
        return text[:limit] + ("\n…(이하 생략)" if len(text) > limit else "")

    def note_write(self, name: str, content: str, append: bool = False) -> str:
        path = self.notes_dir / (_slug(name) + ".md")
        if append and path.exists():
            existing = path.read_text(encoding="utf-8").rstrip()
            content = existing + "\n\n---\n\n" + content
        path.write_text(content, encoding="utf-8")
        return path.name

    # ── 대화 기록 ───────────────────────────────────────────
    def history(self) -> list:
        if not self.history_path.exists():
            return []
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []
        return _decode_history(raw.get("turns", []))

    def save_history(self, turns: list) -> None:
        trimmed = _trim(turns, MAX_HISTORY_TURNS)
        self.history_path.write_text(
            json.dumps({"updated_at": self._stamp(), "turns": _encode_history(trimmed)},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")

    def clear_history(self) -> None:
        self.save_history([])

    # ── 프롬프트에 넣을 요약 ────────────────────────────────
    def prompt_block(self) -> str:
        blocks = []

        facts = self.facts()
        if facts:
            rows = ["[기억하고 있는 사실]"]
            for f in facts[-60:]:
                tag = " ({})".format(f["tags"]) if f.get("tags") else ""
                rows.append("- {}{}  [{}]".format(f["text"], tag, f["id"]))
            blocks.append("\n".join(rows))

        notes = self.note_list()
        if notes:
            rows = ["[보관 중인 문서 — 필요하면 read_note 로 열어봐라]"]
            for n in notes:
                rows.append("- {} : {}".format(n["name"], n["summary"] or "(요약 없음)"))
            blocks.append("\n".join(rows))

        return "\n\n".join(blocks)


# ────────────────────────────────────────────────────────────

def _trim(turns: list, max_turns: int) -> list:
    """오래된 대화를 자르되 도구 호출/결과 쌍이 끊기지 않게 한다."""
    if len(turns) <= max_turns:
        return turns
    cut = turns[-max_turns:]
    # 잘린 첫 턴이 도구 결과면 짝이 되는 assistant 턴이 없으므로 더 잘라낸다
    while cut and cut[0].get("role") == "tool":
        cut = cut[1:]
    return cut


def _encode_history(turns: list) -> list:
    out = []
    for t in turns:
        item = {"role": t.get("role"), "text": t.get("text", "")}
        if t.get("role") == "assistant" and t.get("calls"):
            item["calls"] = [{"name": c.name, "args": c.args, "call_id": c.call_id,
                              "signature": c.signature} for c in t["calls"]]
        if t.get("role") == "assistant" and t.get("parts"):
            item["parts"] = t["parts"]
        if t.get("role") == "tool":
            item["name"] = t.get("name", "")
            item["call_id"] = t.get("call_id", "")
            item["result"] = t.get("result", "")
        out.append(item)
    return out


def _decode_history(raw: list) -> list:
    from .llm import ToolCall

    out = []
    for item in raw:
        turn = {"role": item.get("role"), "text": item.get("text", "")}
        if item.get("calls"):
            turn["calls"] = [ToolCall(c.get("name", ""), c.get("args") or {},
                                      c.get("call_id", ""), c.get("signature", ""))
                             for c in item["calls"]]
        if item.get("parts"):
            turn["parts"] = item["parts"]
        if turn["role"] == "tool":
            turn["name"] = item.get("name", "")
            turn["call_id"] = item.get("call_id", "")
            turn["result"] = item.get("result", "")
        out.append(turn)
    return out
