"""도구 레지스트리.

비서가 쓸 수 있는 '도구'는 두 가지 방법으로 늘립니다.

1) tools/ 폴더에 파이썬 파일을 넣고 @tool 데코레이터를 붙인다
2) commands.json 에 외부 스크립트/명령을 등록한다  ← 파이썬 몰라도 됨

둘 다 프로그램을 켤 때 자동으로 읽어들입니다.
"""
from __future__ import annotations

import importlib.util
import json
import locale
import os
import shlex
import subprocess
import sys
import traceback
from pathlib import Path


def _decode_output(data: bytes) -> str:
    """외부 프로그램 출력 디코딩.

    윈도우에서는 자식 프로세스가 UTF-8 이 아니라 시스템 코드페이지(한국어면 cp949)로
    출력하는 경우가 많아서, 되는 인코딩을 순서대로 시도한다.
    """
    if not data:
        return ""
    tried = []
    for enc in ("utf-8", locale.getpreferredencoding(False), "cp949", "euc-kr"):
        if not enc or enc.lower() in tried:
            continue
        tried.append(enc.lower())
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")

_REGISTRY: dict = {}

EMPTY_SCHEMA = {"type": "object", "properties": {}, "required": []}


class Tool:
    def __init__(self, name, description, parameters, fn, *,
                 confirm=False, source="내장", danger=""):
        self.name = name
        self.description = description
        self.parameters = parameters or dict(EMPTY_SCHEMA)
        self.fn = fn
        self.confirm = confirm          # 실행 전에 사람에게 물어볼지
        self.source = source            # 어디서 왔는지 (표시용)
        self.danger = danger            # 확인창에 띄울 경고 문구

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}

    def __call__(self, **kwargs):
        return self.fn(**kwargs)


def tool(name, description, parameters=None, *, confirm=False, danger=""):
    """tools/ 안의 함수에 붙이면 비서가 쓸 수 있는 도구가 된다."""
    def deco(fn):
        _REGISTRY[name] = Tool(name, description, parameters, fn,
                               confirm=confirm, source="파이썬", danger=danger)
        return fn
    return deco


def registry() -> dict:
    return _REGISTRY


def get(name: str):
    return _REGISTRY.get(name)


def specs() -> list:
    return [t.spec() for t in _REGISTRY.values()]


# ────────────────────────────────────────────────────────────
# 1) tools/*.py 자동 로딩
# ────────────────────────────────────────────────────────────

def load_python_tools(tools_dir: Path) -> list:
    """tools/ 안의 .py 를 전부 불러온다. 밑줄로 시작하는 파일은 건너뛴다."""
    problems = []
    if not tools_dir.exists():
        return problems

    for path in sorted(tools_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = "tools_" + path.stem
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            problems.append("{} 불러오기 실패: {}".format(path.name, e))
            traceback.print_exc()
    return problems


# ────────────────────────────────────────────────────────────
# 2) commands.json — 외부 스크립트 등록
# ────────────────────────────────────────────────────────────

def load_command_tools(path: Path) -> list:
    problems = []
    if not path.exists():
        return problems

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return ["commands.json 형식 오류: {}".format(e)]

    for name, spec in data.items():
        if name.startswith("_"):
            continue  # 주석용 키
        try:
            _REGISTRY[name] = _make_command_tool(name, spec)
        except (KeyError, ValueError) as e:
            problems.append("commands.json 의 '{}' 등록 실패: {}".format(name, e))
    return problems


def _make_command_tool(name: str, spec: dict) -> Tool:
    argv_template = spec.get("argv")
    if not argv_template:
        command = spec.get("command")
        if not command:
            raise ValueError("argv 또는 command 중 하나는 있어야 합니다")
        # 윈도우 경로의 역슬래시가 이스케이프로 먹히지 않게 posix=False
        argv_template = shlex.split(command, posix=False)

    timeout = int(spec.get("timeout_sec", 600))
    cwd = spec.get("cwd") or None
    parameters = spec.get("parameters") or dict(EMPTY_SCHEMA)

    def run(**kwargs):
        argv = []
        for token in argv_template:
            filled = token
            for key, value in kwargs.items():
                filled = filled.replace("{" + key + "}", str(value if value is not None else ""))
            # 채워 넣었더니 빈 토큰이 되면 그 인자는 생략
            if "{" in token and "}" in token and not filled.strip():
                continue
            argv.append(filled.strip('"'))

        if not argv:
            return "실행할 명령이 비어 있습니다."

        # 파이썬 스크립트라면 UTF-8 로 내보내게 유도한다 (다른 프로그램엔 영향 없음)
        child_env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

        try:
            done = subprocess.run(
                argv, cwd=cwd, timeout=timeout, capture_output=True,
                env=child_env,
                shell=False,  # 셸을 거치지 않으므로 인자에 특수문자가 있어도 안전
            )
        except FileNotFoundError:
            return ("실행 파일을 찾을 수 없습니다: {}\n"
                    "commands.json 의 '{}' 경로를 확인하세요.").format(argv[0], name)
        except subprocess.TimeoutExpired:
            return "{}초 안에 끝나지 않아 중단했습니다.".format(timeout)
        except OSError as e:
            return "실행 실패: {}".format(e)

        out = _decode_output(done.stdout).strip()
        err = _decode_output(done.stderr).strip()
        parts = ["종료 코드: {}".format(done.returncode)]
        if out:
            parts.append("[출력]\n" + out[-4000:])
        if err:
            parts.append("[오류 출력]\n" + err[-2000:])
        return "\n\n".join(parts)

    return Tool(
        name,
        spec.get("description", name),
        parameters,
        run,
        # 외부 스크립트는 기본적으로 실행 전 확인을 받는다 (되돌리기 어려우므로)
        confirm=bool(spec.get("confirm", True)),
        source="commands.json",
        danger=spec.get("danger", "실행: " + " ".join(str(t) for t in argv_template)),
    )


def load_all(root: Path) -> list:
    problems = []
    problems += load_python_tools(root / "tools")
    problems += load_command_tools(root / "commands.json")
    return problems
