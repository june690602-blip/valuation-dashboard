"""서버 인메모리 캐시가 **줄어드는가** (2026-09-15 SIGKILL 조사에서 나온 결함).

`_CACHE`·`_AI_CACHE`는 평범한 dict였고 넣기만 했다. TTL은 '다시 쓸지'를 정할 뿐이라
다시 찾지 않는 키는 프로세스가 죽을 때까지 남았다. 키는 검색어 원문·피어 수(5~15)·
뉴스 여부·피어 편집으로 갈라지므로 **화면 조작만으로도** 늘어난다 — 같은 종목에 피어
수만 바꿔 13개를 만들자 +39MB였다(실측). 파이썬은 한 번 올라간 메모리를 OS에 돌려주지
않으므로 그 고점이 512MB 인스턴스에서 그대로 남는다.

검사하는 것은 넷이다 — 만료분이 지워지는가 · 상한이 지켜지는가 · 오래 안 쓴 것부터
버리는가 · 항목마다 제 TTL을 지키는가.
"""
from __future__ import annotations

import time
import unittest

import server


class ServerCacheBoundTests(unittest.TestCase):
    def setUp(self):
        server._CACHE.clear()
        server._AI_CACHE.clear()

    tearDown = setUp

    def test_expired_entries_are_dropped_on_the_next_write(self):
        """이것이 실질적인 몫이다 — 30분 창을 지난 것은 다음 쓰기에서 사라진다."""
        now = time.time()
        server._remember(server._CACHE, "old", {"v": 1}, now - server._TTL - 1,
                         server._TTL, server._CACHE_MAX)
        self.assertEqual(len(server._CACHE), 1)
        server._remember(server._CACHE, "new", {"v": 2}, now, server._TTL, server._CACHE_MAX)
        self.assertEqual(list(server._CACHE), ["new"])

    def test_cap_is_enforced_and_evicts_the_least_recently_used(self):
        now = time.time()
        for i in range(server._CACHE_MAX + 8):
            server._remember(server._CACHE, i, {"v": i}, now, server._TTL, server._CACHE_MAX)
        self.assertEqual(len(server._CACHE), server._CACHE_MAX)
        self.assertNotIn(0, server._CACHE)          # 가장 오래된 것이 먼저 나간다
        self.assertIn(server._CACHE_MAX + 7, server._CACHE)

    def test_a_hit_moves_the_key_out_of_the_eviction_line(self):
        """자주 보는 종목이 한 번 몰려온 새 키들에 밀려나면 캐시가 제 일을 못 한다."""
        now = time.time()
        for i in range(server._CACHE_MAX):
            server._remember(server._CACHE, i, {"v": i}, now, server._TTL, server._CACHE_MAX)
        self.assertIsNotNone(server._peek(server._CACHE, 0, now))   # 0번을 다시 본다
        server._remember(server._CACHE, "새것", {"v": -1}, now, server._TTL, server._CACHE_MAX)
        self.assertIn(0, server._CACHE)             # 살아남고
        self.assertNotIn(1, server._CACHE)          # 그다음으로 오래된 것이 나간다

    def test_each_entry_keeps_its_own_ttl(self):
        """`cached_generic`은 항목마다 다른 TTL을 쓴다(시장 파라미터 1시간 · 자동완성 5분).

        저장소가 한 벌이라고 TTL까지 한 벌로 보면 1시간짜리가 30분에 버려진다.
        """
        now = time.time()
        server._remember(server._CACHE, ("g", "market"), {"v": 1}, now - 2000, 3600,
                         server._CACHE_MAX)
        self.assertIsNotNone(server._peek(server._CACHE, ("g", "market"), now))
        server._remember(server._CACHE, ("g", "sug"), {"v": 2}, now - 400, 300,
                         server._CACHE_MAX)
        self.assertIsNone(server._peek(server._CACHE, ("g", "sug"), now))

    def test_peek_returns_none_once_the_ttl_passes(self):
        now = time.time()
        server._remember(server._CACHE, "k", {"v": 1}, now - server._TTL - 1,
                         server._TTL, server._CACHE_MAX)
        self.assertIsNone(server._peek(server._CACHE, "k", now))

    def test_ai_cache_is_bounded_too(self):
        now = time.time()
        for i in range(server._AI_CACHE_MAX + 5):
            server._remember(server._AI_CACHE, i, {"v": i}, now, server._AI_TTL,
                             server._AI_CACHE_MAX)
        self.assertEqual(len(server._AI_CACHE), server._AI_CACHE_MAX)


if __name__ == "__main__":
    unittest.main()
