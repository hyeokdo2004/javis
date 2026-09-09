"""설정 로딩. 표준 라이브러리만 사용."""
from __future__ import annotations

import json
import os
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"


def load_dotenv() -> None:
    """.env 를 읽어 환경변수로 올린다. 이미 있는 값은 덮어쓰지 않는다."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


class Config:
    def __init__(self, raw: dict):
        self.raw = raw
        self.tz = timezone(timedelta(hours=raw.get("timezone_offset_hours", 9)))
        model_cfg = raw.get("model") or {}
        self.model = os.environ.get("GEMINI_MODEL") or model_cfg.get("id", "gemini-3.8-flash")
        self.api_style = model_cfg.get("api_style", "auto")
        self.mail = raw.get("mail") or {}
        self.root = ROOT
        self.data_dir = DATA_DIR


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        return Config({})
    return Config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))


def env(name: str, default: str = "", required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            "환경변수 {} 가 없습니다. .env 파일에 넣어주세요. "
            "(.env.example 을 복사해서 .env 로 만드세요)".format(name))
    return value or ""
