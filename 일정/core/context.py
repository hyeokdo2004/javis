"""도구들이 공유하는 전역 참조. assistant.py 가 시작할 때 채운다."""
from __future__ import annotations

cfg = None      # core.config.Config
memory = None   # core.memory.Memory


def init(config, mem) -> None:
    global cfg, memory
    cfg = config
    memory = mem
