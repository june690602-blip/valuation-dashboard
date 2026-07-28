"""관리 페이지 무차별 대입 방어 — 실패가 쌓인 IP를 잠시 잠근다.

관리 토큰은 시도 횟수 제한이 없으면 길이만이 유일한 방어선이다. 실패를 기억해 잠그면
자동화된 대입이 사실상 불가능해진다(잠금 없이 초당 수천 번 → 잠금 후 분당 몇 번).
시간을 인자로 주입해 실제 대기 없이 검증한다.
"""
from __future__ import annotations

import unittest

import server


class AdminRateLimitTests(unittest.TestCase):
    def setUp(self):
        server._admin_reset_all()

    def test_allows_attempts_below_threshold(self):
        """한도 미만에서는 잠기지 않는다 — 오타 몇 번으로 막히면 안 된다."""
        ip, now = "1.2.3.4", 1000.0
        for _ in range(server._ADMIN_MAX_FAILS - 1):
            server._admin_note_fail(ip, now=now)
        self.assertEqual(server._admin_lock_left(ip, now=now), 0)

    def test_locks_after_threshold(self):
        """한도를 넘기면 잠기고, 남은 시간이 초 단위로 나온다."""
        ip, now = "1.2.3.4", 1000.0
        for _ in range(server._ADMIN_MAX_FAILS):
            server._admin_note_fail(ip, now=now)
        left = server._admin_lock_left(ip, now=now)
        self.assertGreater(left, 0)
        self.assertLessEqual(left, server._ADMIN_LOCK_SEC)

    def test_unlocks_after_wait(self):
        """잠금 시간이 지나면 다시 시도할 수 있어야 한다(영구 차단 아님)."""
        ip, now = "1.2.3.4", 1000.0
        for _ in range(server._ADMIN_MAX_FAILS):
            server._admin_note_fail(ip, now=now)
        self.assertEqual(server._admin_lock_left(ip, now=now + server._ADMIN_LOCK_SEC + 1), 0)

    def test_success_clears_history(self):
        """토큰이 맞으면 실패 기록을 지운다 — 다음 오타가 즉시 잠금으로 이어지지 않게."""
        ip, now = "1.2.3.4", 1000.0
        for _ in range(server._ADMIN_MAX_FAILS - 1):
            server._admin_note_fail(ip, now=now)
        server._admin_clear(ip)
        for _ in range(server._ADMIN_MAX_FAILS - 1):
            server._admin_note_fail(ip, now=now)
        self.assertEqual(server._admin_lock_left(ip, now=now), 0)

    def test_lock_is_per_ip(self):
        """한 IP가 잠겨도 다른 접속자는 영향받지 않아야 한다."""
        now = 1000.0
        for _ in range(server._ADMIN_MAX_FAILS):
            server._admin_note_fail("1.2.3.4", now=now)
        self.assertGreater(server._admin_lock_left("1.2.3.4", now=now), 0)
        self.assertEqual(server._admin_lock_left("5.6.7.8", now=now), 0)

    def test_old_entries_are_pruned(self):
        """기록이 임계를 넘으면 만료된 것부터 정리한다 — 무한정 쌓이면 메모리 누수가 된다.

        매 실패마다 전체를 훑으면 낭비라, 기록이 _ADMIN_PRUNE_MAX를 넘었을 때만 청소한다."""
        old = 1000.0
        for i in range(server._ADMIN_PRUNE_MAX + 10):
            server._admin_note_fail(f"10.{i // 65536}.{i // 256 % 256}.{i % 256}", now=old)
        # 잠금 시간이 한참 지난 뒤 새 실패가 들어오면 만료된 기록이 청소된다.
        server._admin_note_fail("10.9.9.9", now=old + server._ADMIN_LOCK_SEC * 10)
        self.assertLessEqual(len(server._ADMIN_FAILS), 2)


class ClientIpTests(unittest.TestCase):
    """Render 같은 프록시 뒤에서는 실제 방문자 IP가 X-Forwarded-For에 담겨 온다."""

    def test_prefers_forwarded_first_hop(self):
        self.assertEqual(server._first_forwarded_ip("203.0.113.7, 70.41.3.18"), "203.0.113.7")

    def test_blank_forwarded_falls_back(self):
        self.assertIsNone(server._first_forwarded_ip(""))
        self.assertIsNone(server._first_forwarded_ip(None))


if __name__ == "__main__":
    unittest.main()
