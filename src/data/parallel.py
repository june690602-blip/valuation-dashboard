"""서로 모르는 네트워크 호출을 한 번에 띄우는 얇은 도구 (ADR-0045).

첫 조회의 시간은 거의 전부 네트워크 대기다 — 실측에서 순수 계산은 0.04~0.06초였다
(`scripts/check_load_timing.py`). 그런데 그 호출들이 서로를 기다릴 이유가 없는데도 한 줄로
서 있었다. 새 개념이 필요한 일이 아니라서, 이미 있던 패턴(`build_peer_table`의 스레드풀,
`file_cache`의 키 단위 락)을 로드 경로 나머지에 적용하기 위한 최소한의 도구만 둔다.

**이 모듈이 하지 않는 것 — 예외를 해석하지 않는다.** 퓨처로 바꾸면 모든 실패가
`.result()`에서 똑같은 모양으로 올라오는데, 이 저장소의 실패는 의미가 제각각이다.
US의 시세 실패는 치명(분석 중단)이고 KR의 미조정 시세 실패는 경고다(수정주가로 폴백).
그 구분은 호출부만 알 수 있으므로 값과 예외를 함께 들고 돌려주고, 치명/경고 분류는
`unwrap()`과 `or_else()`로 **호출부가 명시적으로** 한다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterable

# 로드 경로 병렬 그룹의 워커 수. yfinance는 평상시에도 성공률 62%(레이트리밋 — `cache.py`
# 주석)라 동시성을 올리면 실패·재시도가 쌓여 오히려 느려진다. 피어 풀(8워커)과는 시간대가
# 겹치지 않는다 — 피어는 업종분류 뒤, 즉 이 그룹의 조인 뒤에 오기 때문이다.
LOAD_FETCH_WORKERS = 4


@dataclass(frozen=True)
class Outcome:
    """태스크 하나의 결과 — 값이거나 예외다(둘 중 하나)."""

    value: Any = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def unwrap(self):
        """**치명적인** 태스크에 쓴다 — 원래 예외를 타입·메시지 그대로 다시 올린다.

        메시지를 갈아 끼우지 않는 이유: 호출부가 사용자에게 보여줄 안내문을 이미
        정성껏 쓴 자리가 있다(예: US의 "상장폐지·거래정지 상태이거나…").
        """
        if self.error is not None:
            raise self.error
        return self.value

    def or_else(self, default):
        """실패해도 분석이 계속되는 태스크에 쓴다 — 실패면 default."""
        return default if self.error is not None else self.value


def gather(tasks: Iterable[tuple[str, Callable[[], Any]]],
           workers: int = LOAD_FETCH_WORKERS,
           stage: str | None = None) -> dict[str, Outcome]:
    """(이름, 콜러블) 목록을 동시에 돌리고 `이름 → Outcome`을 돌려준다.

    **제출 순서가 중요하다.** 태스크가 워커보다 많으면 먼저 제출한 것이 먼저 출발하므로
    가장 느린 것(Gemini 업종분류·DART)을 앞에 둔다. 실측에서 Gemini 단독이 4초였고,
    그것이 뒤로 밀리면 전체 시간이 그만큼 늘어난다.

    `stage`를 주면 완료 개수를 진행 표시로 보고한다. 리포터는 스레드 로컬이라 워커에
    따로 심어 준다(`bind_reporter`) — 안 그러면 병렬 구간이 조용해진다.

    태스크 자체는 예외를 밖으로 내도 된다 — 여기서 잡아 `Outcome.error`에 담는다.
    한 태스크의 실패가 나머지를 취소시키지 않는다(무료 데이터라 결측이 흔하고, 이
    저장소의 원칙은 "값이 없으면 None, 절대 크래시 내지 말 것"이다).
    """
    from .progress import bind_reporter, report

    items = list(tasks)
    if not items:
        return {}
    out: dict[str, Outcome] = {}
    total = len(items)
    if stage:
        report(stage, 0, total)
    with ThreadPoolExecutor(max_workers=min(workers, total)) as ex:
        futures = {ex.submit(bind_reporter(fn)): name for name, fn in items}
        done = 0
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                out[name] = Outcome(value=fut.result())
            except Exception as e:      # BaseException은 통과시킨다(Ctrl-C 등)
                out[name] = Outcome(error=e)
            done += 1
            if stage:
                report(stage, done, total)
    return out
