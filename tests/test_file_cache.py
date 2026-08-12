from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.data.cache import file_cache


class FileCacheTests(unittest.TestCase):
    def test_fresh_cache_avoids_duplicate_source_calls(self):
        calls = 0

        @file_cache("fresh-test", ttl_hours=1)
        def load_value():
            nonlocal calls
            calls += 1
            return {"value": 7}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.data.cache.CACHE_DIR", Path(tmp)
        ):
            self.assertEqual(load_value(), {"value": 7})
            self.assertEqual(load_value(), {"value": 7})

        self.assertEqual(calls, 1)

    def test_stale_cache_is_used_when_source_fails(self):
        should_fail = False

        @file_cache("stale-test", ttl_hours=1)
        def load_value():
            if should_fail:
                raise ConnectionError("source unavailable")
            return {"value": 11}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.data.cache.CACHE_DIR", Path(tmp)
        ):
            self.assertEqual(load_value(), {"value": 11})
            cache_file = next(Path(tmp).glob("stale-test_*.json"))
            old = time.time() - 2 * 3600
            os.utime(cache_file, (old, old))
            should_fail = True

            self.assertEqual(load_value(), {"value": 11})


if __name__ == "__main__":
    unittest.main()


class SingleFlightTests(unittest.TestCase):
    """캐시가 비었을 때 **같은 것을 동시에 두 번 만들지 않는가.**

    이 검사가 없어서 배포 직후 첫 방문자가 오래 기다렸다. 서버는 `ThreadingHTTPServer`라
    요청마다 스레드가 뜨고 기동 직후엔 예열 스레드가 같은 함수를 부르는데, 캐시 미스가
    나면 **호출자 전부가 각자 원천을 긁었다** — 한국 2,871종목 수집이 동시에 여러 벌.
    yfinance는 평상시 성공률 62%(레이트리밋)라 부하가 겹치면 훨씬 나빠진다.
    """

    def _run_concurrently(self, fn, n=8):
        import threading

        start = threading.Barrier(n)          # 전원이 동시에 출발해야 경합이 재현된다
        results, errors = [], []

        def worker():
            try:
                start.wait(timeout=5)
                results.append(fn())
            except Exception as e:             # noqa: BLE001 — 테스트가 원인을 보여줘야 한다
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [], f"스레드에서 예외: {errors}")
        return results

    def test_concurrent_misses_build_only_once(self):
        calls = 0
        lock = __import__("threading").Lock()

        @file_cache("single-flight-test", ttl_hours=1)
        def load_value():
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.2)                    # 수집이 느린 상황 — 경합 창을 벌린다
            return {"value": 42}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.data.cache.CACHE_DIR", Path(tmp)
        ):
            results = self._run_concurrently(load_value, n=8)

        self.assertEqual(results, [{"value": 42}] * 8, "전원이 같은 값을 받아야 한다")
        self.assertEqual(calls, 1, f"원천을 {calls}번 불렀다 — 동시 중복 수집이 살아 있다")

    def test_different_keys_do_not_block_each_other(self):
        """락은 **키 단위**여야 한다. 전역 락 하나면 무관한 캐시까지 멈춘다."""
        import threading

        @file_cache("per-key-test", ttl_hours=1)
        def load_value(which):
            time.sleep(0.3)
            return {"which": which}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.data.cache.CACHE_DIR", Path(tmp)
        ):
            out = {}
            threads = [threading.Thread(target=lambda k=k: out.__setitem__(k, load_value(k)))
                       for k in ("a", "b", "c")]
            t0 = time.time()
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            elapsed = time.time() - t0

        self.assertEqual(len(out), 3)
        # 직렬화됐다면 0.9초 이상 걸린다. 넉넉히 잡아도 0.75초를 넘으면 키 락이 아니다.
        self.assertLess(elapsed, 0.75, f"키가 다른데 서로 막았다 ({elapsed:.2f}초)")
