"""첫 방문자가 콜드를 겪지 않게 한다 (ADR-0048).

Render는 디스크를 붙이지 않으면 파일시스템이 휘발성이라 **배포할 때마다
`data/cache/`가 통째로 빈다.** 실측 완전 콜드는 12.33초이고, 예열이 없으면 그 12초는
언제나 첫 방문자가 낸다.

여기서 지키는 것은 속도가 아니라 **예열이 조용히 사라지지 않는 것**이다 —
한 종목의 실패가 나머지를 데려가지 않는가, 계수가 먼저인가, 목록이 한 벌인가.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.web import prewarm

ROOT = Path(__file__).resolve().parents[1]


class WarmAllTests(unittest.TestCase):
    def test_every_showcase_entry_is_warmed(self):
        seen = []
        with patch.object(prewarm, "warm_stock", lambda m, q, log=None: seen.append((m, q))), \
             patch.object(prewarm, "warm_bonds", lambda log=None: None):
            prewarm.warm_all(log=lambda _: None)
        self.assertEqual(seen, prewarm.SHOWCASE)

    def test_one_failing_stock_does_not_stop_the_rest(self):
        """무료 데이터라 결측이 흔하다 — 두 번째가 죽어도 네 번째까지 가야 한다."""
        seen = []

        def flaky(market, query, **kwargs):
            seen.append(query)
            if query == prewarm.SHOWCASE[1][1]:
                raise RuntimeError("레이트리밋")

        with patch("src.web.serialize.analyze", flaky), \
             patch.object(prewarm, "warm_bonds", lambda log=None: None):
            prewarm.warm_all(log=lambda _: None)

        self.assertEqual(len(seen), len(prewarm.SHOWCASE))

    def test_a_failure_is_reported_not_swallowed(self):
        lines = []
        with patch("src.web.serialize.analyze",
                   side_effect=RuntimeError("레이트리밋")):
            ok = prewarm.warm_stock("KR", "005930", log=lines.append)
        self.assertFalse(ok)
        self.assertTrue(any("005930" in ln for ln in lines), lines)


    def test_the_log_survives_a_cp949_console(self):
        """윈도우 콘솔은 cp949다 — `대시보드실행.bat`이 그 경로다.

        예전 `✓`/`✗`가 여기서 `UnicodeEncodeError`를 냈고, 그 예외는 `log()` 호출
        자리라 `warm_stock`의 try 밖이었다. **예열 스레드에서 터지면 첫 종목 뒤로
        전부 조용히 죽는다.**
        """
        lines = []
        with patch("src.web.serialize.analyze", lambda *a, **k: None):
            prewarm.warm_stock("KR", "005930", log=lines.append)
        with patch("src.web.serialize.analyze",
                   side_effect=RuntimeError("레이트리밋")):
            prewarm.warm_stock("KR", "005930", log=lines.append)
        with patch("src.web.serialize.bond_data", lambda: None), \
             patch("src.web.serialize.bond_history", lambda m, t: None):
            prewarm.warm_bonds(log=lines.append)

        self.assertTrue(lines)
        for line in lines:
            line.encode("cp949")    # 인코딩 불가 문자가 있으면 여기서 터진다


class ServerWarmsOnBootTests(unittest.TestCase):
    def _run_warm(self, order):
        import server

        class _FakeThread:
            def __init__(self, target, daemon=None, name=None):
                self._target = target

            def start(self):
                self._target()

        with patch.object(server, "threading", SimpleNamespace(Thread=_FakeThread)), \
             patch.object(server, "_warm_coefficients", lambda: order.append("계수")), \
             patch.object(prewarm, "warm_all", lambda log=None: order.append("쇼케이스")):
            server._warm_cache()

    def test_the_showcase_is_warmed_on_boot(self):
        order = []
        self._run_warm(order)
        self.assertIn("쇼케이스", order, "기동 예열이 종목을 채우지 않는다")

    def test_coefficients_come_first(self):
        """①⑤의 적정 배수가 계수에 걸려 있다(판정 가중의 77%). 뒤집으면 종목
        예열이 각자 계수를 만들려 들고, 기다리는 자리만 늘어난다."""
        order = []
        self._run_warm(order)
        self.assertEqual(order, ["계수", "쇼케이스"])


class OneListOnlyTests(unittest.TestCase):
    """목록이 두 벌이면 반드시 갈라진다 — 스크립트는 모듈을 **가져다 쓴다.**"""

    def test_the_cli_does_not_keep_its_own_copy(self):
        src = (ROOT / "scripts" / "prewarm_cache.py").read_text(encoding="utf-8")
        self.assertIn("from src.web.prewarm import", src)
        self.assertNotIn("SHOWCASE = [", src,
                         "스크립트가 쇼케이스 목록을 따로 들고 있다 — 한 벌이어야 한다")


if __name__ == "__main__":
    unittest.main()
