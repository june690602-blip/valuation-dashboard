"""분석 진행 상태 훅 — 오래 걸리는 단계(피어 수집)를 UI에 알리기 위한 얇은 통로.

데이터 계층은 reporter의 존재를 모른 채 report()만 호출하고(리포터 없으면 no-op),
웹 계층(serialize)이 요청 스레드에 리포터를 걸었다 풀었다 한다. 순수 분석 로직에는
어떤 상태도 스며들지 않는다.
"""
from __future__ import annotations

import threading

_local = threading.local()


def set_reporter(cb) -> None:
    """현재 스레드에 진행 콜백(cb(stage, done, total))을 건다. None이면 해제."""
    _local.cb = cb


def report(stage: str, done: int, total: int) -> None:
    """진행 보고 — 리포터가 없으면 아무 일도 하지 않는다(데이터 계층 부담 0)."""
    cb = getattr(_local, "cb", None)
    if cb is None:
        return
    try:
        cb(stage, done, total)
    except Exception:
        pass  # 진행 표시는 편의 기능 — 본 분석을 깨뜨리지 않는다
