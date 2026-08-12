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


def current_reporter():
    """이 스레드에 걸린 콜백(없으면 None). 워커에 넘겨 심을 때 쓴다."""
    return getattr(_local, "cb", None)


def bind_reporter(fn, cb=None):
    """부모 스레드의 리포터를 **워커 안에서도 살려 주는** 래퍼.

    리포터가 `threading.local()`이라 워커 스레드에는 따라가지 않는다. 지금까지
    `build_peer_table`이 멀쩡히 동작한 것은 `report()`를 **메인 스레드에서만** 부르기
    때문이고(워커는 `fetch_info_metrics`만 돈다), 수집 자체를 워커로 옮기면 그 전제가
    깨져 **병렬 구간에서 진행 표시가 조용해진다.**

    **부모 스레드에서 불러야 한다** — 감쌀 때의 스레드에서 콜백을 읽어 가기 때문이다.
    풀의 워커는 재사용되므로 끝나면 반드시 지운다(안 지우면 다음 태스크가 남의 리포터를
    물려받아, 이미 끝난 분석의 진행률을 덮어쓴다).
    """
    cb = current_reporter() if cb is None else cb

    def inner(*a, **k):
        set_reporter(cb)
        try:
            return fn(*a, **k)
        finally:
            set_reporter(None)

    return inner


def report(stage: str, done: int, total: int) -> None:
    """진행 보고 — 리포터가 없으면 아무 일도 하지 않는다(데이터 계층 부담 0)."""
    cb = getattr(_local, "cb", None)
    if cb is None:
        return
    try:
        cb(stage, done, total)
    except Exception:
        pass  # 진행 표시는 편의 기능 — 본 분석을 깨뜨리지 않는다
