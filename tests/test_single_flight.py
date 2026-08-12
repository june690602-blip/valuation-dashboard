"""같은 것을 동시에 두 번 만들지 않는다 (ADR-0047).

`file_cache`는 PR #151에서 키 단위 락을 얻어 **원천 다운로드**를 한 벌로 묶었다.
그런데 그 위층 `serialize._pipeline`은 `@lru_cache`였다 — lru_cache는 **호출 중에
락을 잡지 않으므로**, 같은 종목을 두 사람이 동시에 조회하면 둘 다 miss를 보고 둘 다
파이프라인을 돌린다.

**아래층에 락이 있으니 네트워크가 두 배가 되지는 않는다** — 두 번째 스레드는 각
`file_cache` 키 락에서 기다렸다 첫 스레드가 채운 파일을 쓴다. 두 벌로 도는 것은
그 위다: 스레드풀 두 벌(피어 8워커 × N명), 파이프라인 계산, 직렬화.

**그래서 이 변경이 사는 것은 시간이 아니라 부하다.** yfinance는 평상시에도 성공률
62%(레이트리밋 — `cache.py` 주석)이고, `file_cache`는 `validate` 실패한 빈 응답을
**저장하지 않으므로** 레이트리밋에 걸린 원천은 캐시로 막히지 않고 전원이 각자 다시
때린다. 사람이 몰릴수록 정확히 그 상황이 된다.
"""
from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from src.data.cache import single_flight_memo

# 가짜 계산 하나가 자는 시간. 겹치면 총 시간이 합이 아니라 최댓값 쪽으로 간다.
NAP = 0.15


class _Counter:
    """호출 횟수를 세는 가짜 계산. 자는 동안 다른 호출자가 끼어들 틈을 준다."""

    def __init__(self, nap: float = NAP):
        self.calls = 0
        self.nap = nap
        self._lock = threading.Lock()

    def __call__(self, key: str) -> str:
        with self._lock:
            self.calls += 1
        time.sleep(self.nap)
        return f"값:{key}"


class SingleFlightTests(unittest.TestCase):
    def test_the_same_key_is_computed_once_even_when_callers_race(self):
        """여섯 명이 동시에 같은 종목을 조회해도 계산은 **한 번**이다."""
        counter = _Counter()
        memo = single_flight_memo(maxsize=8)(counter)

        with ThreadPoolExecutor(max_workers=6) as ex:
            got = list(ex.map(memo, ["005930"] * 6))

        self.assertEqual(counter.calls, 1, f"{counter.calls}번 계산됐다 — 한 번이어야 한다")
        self.assertEqual(got, ["값:005930"] * 6)

    def test_different_keys_do_not_wait_for_each_other(self):
        """락은 **키 단위**다. 전역 락 하나로 묶으면 무관한 종목까지 줄을 선다.

        `cache.py`가 파일 캐시에서 같은 판단을 한 이유와 같다 — 고치려던 것보다
        나쁜 병목을 만든다.
        """
        counter = _Counter()
        memo = single_flight_memo(maxsize=8)(counter)

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(memo, ["005930", "000660", "035420", "105560"]))
        elapsed = time.perf_counter() - t0

        self.assertEqual(counter.calls, 4)
        self.assertLess(elapsed, 4 * NAP * 0.6,
                        f"{elapsed:.2f}s — 키가 서로를 기다리고 있다")

    def test_a_hit_skips_the_computation(self):
        counter = _Counter(nap=0)
        memo = single_flight_memo(maxsize=8)(counter)

        self.assertEqual(memo("005930"), "값:005930")
        self.assertEqual(memo("005930"), "값:005930")
        self.assertEqual(counter.calls, 1)

    def test_a_failure_is_not_remembered(self):
        """실패를 캐시하면 **한 번의 레이트리밋이 그 종목을 계속 죽인다.**

        `file_cache`가 빈 응답을 저장하지 않는 것과 같은 이유다(오염 방지).
        """
        calls = []

        def flaky(key):
            calls.append(key)
            if len(calls) == 1:
                raise RuntimeError("레이트리밋")
            return "값"

        memo = single_flight_memo(maxsize=8)(flaky)

        with self.assertRaises(RuntimeError):
            memo("005930")
        self.assertEqual(memo("005930"), "값")     # 다시 시도된다
        self.assertEqual(len(calls), 2)

    def test_the_oldest_entry_leaves_first(self):
        counter = _Counter(nap=0)
        memo = single_flight_memo(maxsize=2)(counter)

        memo("a"), memo("b"), memo("c")            # a가 밀려난다
        self.assertEqual(counter.calls, 3)
        memo("c"), memo("b")                       # 둘 다 적중
        self.assertEqual(counter.calls, 3)
        memo("a")                                  # 밀려났으므로 다시 계산
        self.assertEqual(counter.calls, 4)


    def test_the_same_call_spelled_differently_is_one_entry(self):
        """생략한 기본값과 적어 준 기본값은 **같은 호출**이다.

        `lru_cache`는 이 둘을 다르게 본다. 그래서 `_pipeline`의 도크스트링이 말하는
        *"AI 버튼은 캐시 적중으로 빠르다"* 가 실제로는 빗나가고 있었다 — `analyze()`는
        `exclude`·`extra`를 적어 7인자로 부르고 AI 헬퍼는 생략해 5인자로 부른다.
        """
        counter = _Counter(nap=0)

        @single_flight_memo(maxsize=8)
        def fn(market, query, exclude=(), extra=()):
            return counter(f"{market}:{query}")

        fn("KR", "005930", (), ())      # analyze()가 부르는 철자
        fn("KR", "005930")              # AI 헬퍼가 부르는 철자
        fn("KR", query="005930")        # 키워드로 부르는 철자
        self.assertEqual(counter.calls, 1)

        fn("KR", "005930", ("000660",), ())     # 피어를 편집하면 진짜 다른 호출이다
        self.assertEqual(counter.calls, 2)


class PipelineIsSingleFlightedTests(unittest.TestCase):
    """진짜 `_pipeline`이 그 계약을 갖는가 — 데코레이터만 맞아도 소용없다."""

    def test_two_callers_on_the_same_stock_load_it_once(self):
        from src.web import serialize as ser

        loads = []
        lock = threading.Lock()

        def slow_load(market, query, peer_count, exclude, extra):
            with lock:
                loads.append(query)
            time.sleep(NAP)
            return SimpleNamespace(peers=None, yahoo_ticker="005930.KS",
                                   is_financial=False, market="KR")

        ser._pipeline.cache_clear()
        with patch.object(ser, "_load", slow_load), \
             patch.object(ser, "compute_indicators", lambda d: "ind"), \
             patch("src.analysis.scoring.compute_scores", lambda *a: "scores"), \
             patch.object(ser, "compute_capital_cost",
                          lambda d, rf, mrp: SimpleNamespace(k_e=0.09)), \
             patch("src.data.universe_multiples.coefficients_or_none", lambda m: None), \
             patch.object(ser, "compute_valuation", lambda *a, **k: "val"):
            with ThreadPoolExecutor(max_workers=4) as ex:
                futures = [ex.submit(ser._pipeline, "KR", "005930", 9, 0.035, 0.06)
                           for _ in range(4)]
                results = [f.result() for f in futures]
        ser._pipeline.cache_clear()

        self.assertEqual(len(loads), 1, f"{len(loads)}번 수집됐다 — 한 번이어야 한다")
        self.assertEqual(len({id(r) for r in results}), 1, "호출자마다 다른 결과를 받았다")


if __name__ == "__main__":
    unittest.main()
