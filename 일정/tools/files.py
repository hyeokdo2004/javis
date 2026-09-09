"""내 컴퓨터 파일 읽기 — 지정한 폴더 안에서만.

config.json 의 files.root 에 적은 폴더(와 그 아래)만 읽습니다. 그 밖은 막습니다.
텍스트·CSV·JSON 은 물론 엑셀(.xlsx)도 별도 설치 없이 읽습니다.
쓰기·삭제는 하지 않습니다 — 읽기 전용입니다.
"""
from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from core import context
from core.registry import tool

TEXT_SUFFIX = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".ini", ".yml", ".yaml"}
ALL_SUFFIX = TEXT_SUFFIX | {".xlsx"}

_CELL = re.compile(r"^([A-Z]+)(\d+)$")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _root() -> Path:
    raw = (context.cfg.raw.get("files") or {}).get("root", "")
    return Path(raw).expanduser() if raw else context.cfg.root


def _resolve(name: str) -> Path:
    """폴더 밖으로 나가는 경로(.. 등)는 막는다."""
    root = _root().resolve()
    target = (root / (name or "")).resolve()
    if target != root and root not in target.parents:
        raise PermissionError(str(root))
    return target


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "{:.0f}{}".format(size, unit)
        size /= 1024.0
    return str(size)


# ── 엑셀 (.xlsx) ────────────────────────────────────────────

def _column(ref: str) -> int:
    found = _CELL.match(ref or "")
    if not found:
        return 0
    number = 0
    for ch in found.group(1):
        number = number * 26 + (ord(ch) - 64)
    return number - 1


def _shared_strings(book: zipfile.ZipFile) -> list:
    try:
        root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out = []
    for si in root:
        out.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    return out


def _first_sheet(book: zipfile.ZipFile) -> str:
    names = [n for n in book.namelist() if n.startswith("xl/worksheets/sheet")]
    return sorted(names)[0] if names else ""


def _read_xlsx(path: Path, max_rows: int) -> list:
    with zipfile.ZipFile(path) as book:
        sheet = _first_sheet(book)
        if not sheet:
            return []
        strings = _shared_strings(book)
        root = ET.fromstring(book.read(sheet))

    rows = []
    for row in root.iter(_NS + "row"):
        cells = {}
        for cell in row.iter(_NS + "c"):
            value = cell.findtext(_NS + "v")
            if cell.get("t") == "s" and value is not None:
                try:
                    value = strings[int(value)]
                except (ValueError, IndexError):
                    value = ""
            elif cell.get("t") == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(_NS + "t"))
            cells[_column(cell.get("r", ""))] = (value or "").strip()
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
        if len(rows) >= max_rows:
            break
    return rows


def _read_csv(path: Path, max_rows: int) -> list:
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            text = path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return []
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        rows.append([c.strip() for c in row])
        if len(rows) >= max_rows:
            break
    return rows


def _table(rows: list) -> str:
    if not rows:
        return "(내용이 없습니다)"
    width = max(len(r) for r in rows)
    lines = []
    for i, row in enumerate(rows):
        padded = row + [""] * (width - len(row))
        lines.append(" | ".join(padded))
        if i == 0:
            lines.append("-" * min(60, max(10, len(lines[0]))))
    return "\n".join(lines)


# ── 도구 ────────────────────────────────────────────────────

@tool(
    name="list_files",
    description=(
        "지정된 작업 폴더 안의 파일 목록을 본다. '무슨 파일 있어?', "
        "'견적서 파일 찾아줘' 같은 요청에 쓴다. 파일을 열려면 read_file 을 써라."
    ),
    parameters={
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "작업 폴더 안의 하위 폴더. 없으면 빈 문자열"},
            "keyword": {"type": "string", "description": "이름에 이 말이 든 것만. 없으면 빈 문자열"},
        },
        "required": [],
    },
)
def list_files(folder: str = "", keyword: str = "") -> str:
    try:
        base = _resolve(folder)
    except PermissionError as e:
        return "작업 폴더({}) 밖은 볼 수 없습니다.".format(e)
    if not base.exists():
        return "그런 폴더가 없습니다: {}".format(folder or ".")

    keyword = (keyword or "").strip().lower()
    folders, files = [], []
    for item in sorted(base.iterdir()):
        if item.name.startswith("."):
            continue
        if keyword and keyword not in item.name.lower():
            continue
        if item.is_dir():
            folders.append("[폴더] " + item.name)
        else:
            mark = "" if item.suffix.lower() in ALL_SUFFIX else "  (읽기 미지원)"
            files.append("{}  {}{}".format(item.name, _human(item.stat().st_size), mark))

    if not folders and not files:
        return "{} 안에 해당하는 파일이 없습니다.".format(base)
    rows = ["{} ({}개)".format(base, len(folders) + len(files)), ""]
    return "\n".join(rows + folders + files)


@tool(
    name="read_file",
    description=(
        "작업 폴더 안의 파일을 읽는다. 텍스트·CSV·JSON 과 엑셀(.xlsx) 을 읽을 수 있다. "
        "읽은 내용만 근거로 말하고 지어내지 마라. 파일을 고치거나 지우지는 못한다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "파일 이름. 하위 폴더면 '폴더명/파일명'"},
            "max_rows": {"type": "integer", "description": "표에서 몇 줄까지. 기본 50"},
            "limit": {"type": "integer", "description": "글 파일에서 몇 글자까지. 기본 4000"},
        },
        "required": ["name"],
    },
)
def read_file(name: str, max_rows: int = 50, limit: int = 4000) -> str:
    try:
        path = _resolve(name)
    except PermissionError as e:
        return "작업 폴더({}) 밖은 읽을 수 없습니다.".format(e)
    if not path.exists() or not path.is_file():
        return "그런 파일이 없습니다: {}. list_files 로 이름을 먼저 확인하세요.".format(name)

    max_rows = max(1, min(int(max_rows or 50), 300))
    limit = max(200, min(int(limit or 4000), 20000))
    suffix = path.suffix.lower()

    try:
        if suffix == ".xlsx":
            rows = _read_xlsx(path, max_rows)
            return "[{}] 엑셀 {}줄\n\n{}".format(path.name, len(rows), _table(rows))
        if suffix in (".csv", ".tsv"):
            rows = _read_csv(path, max_rows)
            return "[{}] 표 {}줄\n\n{}".format(path.name, len(rows), _table(rows))
        if suffix in TEXT_SUFFIX:
            for enc in ("utf-8", "cp949", "utf-8-sig"):
                try:
                    text = path.read_text(encoding=enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            else:
                return "글자 인코딩을 알아내지 못했습니다: {}".format(name)
            if len(text) > limit:
                text = text[:limit] + "\n…(이하 생략)"
            return "[{}]\n\n{}".format(path.name, text)
    except (zipfile.BadZipFile, ET.ParseError, OSError) as e:
        return "파일을 읽지 못했습니다: {}: {}".format(type(e).__name__, e)

    return ("'{}' 형식은 읽지 못합니다. 읽을 수 있는 것: {}"
            .format(suffix or "확장자 없음", ", ".join(sorted(ALL_SUFFIX))))
