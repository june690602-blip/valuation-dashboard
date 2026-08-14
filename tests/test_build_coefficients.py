"""계수 빌드는 **못 만든 것을 파괴하지 않는다** (ADR-0049).

2026-08-12 밤에 KRX가 GitHub Actions IP를 막아 KR 계수 빌드가 실패했다. 그런데
워크플로는 **초록불로 끝나면서** publish 단계의 orphan force-push로 **멀쩡하던 어제치
`KR.json`까지 지웠다.** 그 뒤 서버는 404를 받아 전 종목 수집으로 물러났고
(로컬 실측 263.85초), 판정 가중의 77%가 그 계수에 걸려 있다.

여기서 지키는 것은 셋이다:
  ㉠ 이번에 못 만든 시장은 **이전 파일이 그대로 남는다**
  ㉡ 이어받았다는 사실과 **원래 만든 시각**이 meta에 남는다(안 적으면 얼마나 낡았는지 모른다)
  ㉢ 끝내는 코드가 그 상태를 말한다 — 0 전부새로 · 2 일부이어받음 · 1 낼것없음
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "build_coefficients_under_test", ROOT / "scripts" / "build_coefficients.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


COEF = {"per": {"sector": {"기타": 1.0}, "n": 100},
        "pbr": {"sector": {"기타": 1.0}, "n": 100}}


class _ScriptCase(unittest.TestCase):
    """스크립트를 임시 폴더에서 돌리는 공통 준비."""

    def setUp(self):
        self.mod = _load()
        self.dir = Path(tempfile.mkdtemp(prefix="coef_"))
        self.out = self.dir / "coefficients"
        self.out.mkdir()

    def _seed(self, market="KR", built_at="2026-08-11 18:44 UTC"):
        """어제 워크플로가 브랜치에 남긴 상태 = 워크플로가 --out에 깔아 주는 것."""
        (self.out / f"{market}.json").write_text(json.dumps({"어제": True}), encoding="utf-8")
        (self.out / "meta.json").write_text(json.dumps(
            {"built_at": built_at,
             "markets": {market: {"ok": True, "rows": 2687, "legs": ["per"],
                                  "built_at": built_at}}}), encoding="utf-8")

    def _run(self, results, markets=("KR", "US")):
        """results: {market: (coef, rows)} 또는 {market: Exception}"""
        def fake_build(market):
            r = results[market]
            if isinstance(r, Exception):
                raise r
            return r

        argv = ["build", "--out", str(self.out.relative_to(ROOT)) if self.out.is_relative_to(ROOT)
                else str(self.out), "--markets", *markets]
        with patch.object(self.mod, "build", fake_build), \
             patch.object(self.mod, "ROOT", Path("/")), \
             patch.object(sys, "argv", argv):
            return self.mod.main()


class CarryOverTests(_ScriptCase):
    def test_a_market_it_could_not_build_keeps_yesterdays_file(self):
        self._seed("KR")
        code = self._run({"KR": RuntimeError("KRX 종목 목록을 가져오지 못했습니다."),
                          "US": (COEF, 1506)})

        self.assertEqual(code, 2, "일부를 이어받았으면 2로 끝나야 한다")
        kept = json.loads((self.out / "KR.json").read_text(encoding="utf-8"))
        self.assertEqual(kept, {"어제": True}, "어제치 KR.json이 지워졌다")
        self.assertTrue((self.out / "US.json").exists())

    def test_the_carried_over_entry_says_when_it_was_actually_built(self):
        self._seed("KR", built_at="2026-08-11 18:44 UTC")
        self._run({"KR": RuntimeError("boom"), "US": (COEF, 1506)})

        meta = json.loads((self.out / "meta.json").read_text(encoding="utf-8"))
        kr = meta["markets"]["KR"]
        self.assertTrue(kr["carried_over"])
        self.assertEqual(kr["built_at"], "2026-08-11 18:44 UTC",
                         "이어받은 파일이 언제 만들어진 것인지 없으면 낡음을 알 수 없다")
        self.assertFalse(kr["ok"])
        self.assertTrue(meta["markets"]["US"]["ok"])

    def test_all_fresh_returns_zero(self):
        code = self._run({"KR": (COEF, 2687), "US": (COEF, 1506)})
        self.assertEqual(code, 0)
        meta = json.loads((self.out / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(all(m["ok"] for m in meta["markets"].values()))
        self.assertNotIn("carried_over", meta["markets"]["KR"])

    def test_nothing_usable_returns_one_so_publish_is_skipped(self):
        """이어받을 것도 없고 만들지도 못했다 — publish가 돌면 브랜치가 비워진다."""
        code = self._run({"KR": RuntimeError("boom"), "US": RuntimeError("boom")})
        self.assertEqual(code, 1)

    def test_a_market_with_no_file_at_all_is_not_green(self):
        """**이 사고가 시작된 자리다.** 이어받을 파일조차 없는 시장(지금의 KR)이 초록으로
        끝나면 브랜치가 비어 있는 채로 아무도 모른다 — 실제로 그래서 몰랐다."""
        code = self._run({"KR": RuntimeError("Access Denied"), "US": (COEF, 1506)})

        self.assertEqual(code, 2, "한 시장이 비어 있는데 초록으로 끝났다")
        self.assertFalse((self.out / "KR.json").exists())
        self.assertTrue((self.out / "US.json").exists())
        meta = json.loads((self.out / "meta.json").read_text(encoding="utf-8"))
        self.assertFalse(meta["markets"]["KR"]["ok"])

    def test_a_total_failure_with_yesterdays_files_still_publishes(self):
        """둘 다 실패해도 이어받을 것이 있으면 2다 — 브랜치를 그대로 두는 편이 낫다."""
        self._seed("KR")
        self._seed("US", built_at="2026-08-11 18:44 UTC")
        code = self._run({"KR": RuntimeError("boom"), "US": RuntimeError("boom")})
        self.assertEqual(code, 2)
        self.assertTrue((self.out / "KR.json").exists())
        self.assertTrue((self.out / "US.json").exists())


class ThinCoefficientTests(unittest.TestCase):
    """얇아진 계수가 두꺼운 계수를 조용히 덮어쓰지 못하게 한다 (ADR-0050).

    `_coefficients_usable`은 **모양만 본다.** 그래서 레이트리밋에 걸린 빌드가 만든
    얇은 계수도 통과한다. 어느 다리를 지킬지는 재서 정했다 — 같은 스냅숏에서 40%로
    깎았을 때 **per·pbr는 적정가를 중앙값 11.3% 움직였고 ev_ebitda·psr는 0.0%였다**
    (`scripts/check_coefficient_thinning.py --isolate`).
    """

    def test_a_thinner_pillar_leg_is_rejected(self):
        from src.data.universe_multiples import thinned_legs

        prev = {"per": {"n": 1527}, "pbr": {"n": 2529}}
        new = {"per": {"n": 624}, "pbr": {"n": 2529}}
        self.assertEqual(thinned_legs(new, prev), [("per", 624, 1527)])

    def test_the_flaky_legs_are_allowed_to_shrink(self):
        """ev_ebitda·psr는 yfinance 원천이라 정상적인 날에도 36~41%까지 요동친다.
        여기에 문턱을 걸면 멀쩡한 빌드를 계속 거부한다."""
        from src.data.universe_multiples import thinned_legs

        prev = {"per": {"n": 1527}, "pbr": {"n": 2529},
                "ev_ebitda": {"n": 1376}, "psr": {"n": 1999}}
        new = {"per": {"n": 1527}, "pbr": {"n": 2529},
               "ev_ebitda": {"n": 565}, "psr": {"n": 728}}
        self.assertEqual(thinned_legs(new, prev), [])

    def test_a_small_dip_is_normal(self):
        """상장·폐지로 유니버스가 조금 변하는 것까지 막으면 매일 거부된다."""
        from src.data.universe_multiples import thinned_legs

        prev = {"per": {"n": 1527}, "pbr": {"n": 2529}}
        new = {"per": {"n": 1500}, "pbr": {"n": 2500}}
        self.assertEqual(thinned_legs(new, prev), [])

    def test_no_previous_means_no_comparison(self):
        """처음 만드는 경우까지 막으면 브랜치가 영영 안 생긴다."""
        from src.data.universe_multiples import thinned_legs

        self.assertEqual(thinned_legs({"per": {"n": 10}}, {}), [])
        self.assertEqual(thinned_legs({"per": {"n": 10}}, {"per": {}}), [])


class ThinBuildCarriesOverTests(_ScriptCase):
    """스크립트가 실제로 얇은 빌드를 거부하고 이전 것을 이어받는가."""

    def test_a_thin_build_does_not_overwrite_the_thick_one(self):
        thick = {"per": {"n": 1527, "sector": {}}, "pbr": {"n": 2529, "sector": {}}}
        (self.out / "KR.json").write_text(json.dumps(thick), encoding="utf-8")
        (self.out / "meta.json").write_text(json.dumps(
            {"built_at": "2026-08-11 18:44 UTC",
             "markets": {"KR": {"ok": True, "rows": 2687, "built_at": "2026-08-11 18:44 UTC"}}}),
            encoding="utf-8")

        thin = {"per": {"n": 624, "sector": {}}, "pbr": {"n": 2529, "sector": {}}}
        code = self._run({"KR": (thin, 2687), "US": (COEF, 1506)})

        self.assertEqual(code, 2, "얇은 빌드를 받아들이고 초록으로 끝났다")
        kept = json.loads((self.out / "KR.json").read_text(encoding="utf-8"))
        self.assertEqual(kept["per"]["n"], 1527, "두꺼운 계수가 얇은 것으로 덮였다")
        meta = json.loads((self.out / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["markets"]["KR"]["error"], "thin")
        self.assertTrue(meta["markets"]["KR"]["carried_over"])

    def test_an_equally_thick_build_is_accepted(self):
        thick = {"per": {"n": 1527, "sector": {}}, "pbr": {"n": 2529, "sector": {}}}
        (self.out / "KR.json").write_text(json.dumps(thick), encoding="utf-8")

        fresh = {"per": {"n": 1530, "sector": {}}, "pbr": {"n": 2531, "sector": {}}}
        code = self._run({"KR": (fresh, 2690), "US": (COEF, 1506)})

        self.assertEqual(code, 0)
        got = json.loads((self.out / "KR.json").read_text(encoding="utf-8"))
        self.assertEqual(got["per"]["n"], 1530, "멀쩡한 새 계수가 반영되지 않았다")


class KrxListingRetryTests(unittest.TestCase):
    """KRX 차단은 상시가 아니라 가끔이다 — 한 번 실패로 KR 경로 전체를 버리지 않는다."""

    def test_the_listing_is_retried_before_giving_up(self):
        import pandas as pd

        from src.data import universe

        calls = []
        good = pd.DataFrame({"Symbol": ["005930"], "Name": ["삼성전자"],
                             "Market": ["KOSPI"], "Marcap": [1.0], "Stocks": [1.0],
                             "Close": [1.0]})

        def flaky(kind):
            calls.append(kind)
            if kind == "KRX" and len(calls) < 3:
                raise RuntimeError("Access Denied")
            return good

        with patch.object(universe, "KRX_RETRY_WAITS", (0.0, 0.0)), \
             patch.dict(sys.modules, {"FinanceDataReader": type(
                 "fdr", (), {"StockListing": staticmethod(flaky)})}):
            out = universe.get_kr_listing.__wrapped__()      # 캐시 우회

        self.assertEqual(calls.count("KRX"), 3, f"재시도가 없다: {calls}")
        self.assertIn("005930", list(out.index) + list(out.get("Code", [])))

    def test_it_still_gives_the_recoverable_message_when_every_try_fails(self):
        from src.data import universe

        def always_dead(kind):
            raise RuntimeError("Access Denied")

        with patch.object(universe, "KRX_RETRY_WAITS", (0.0, 0.0)), \
             patch.dict(sys.modules, {"FinanceDataReader": type(
                 "fdr", (), {"StockListing": staticmethod(always_dead)})}):
            with self.assertRaises(RuntimeError) as ctx:
                universe.get_kr_listing.__wrapped__()

        self.assertIn("KRX 종목 목록을 가져오지 못했습니다", str(ctx.exception))
        self.assertIsNotNone(ctx.exception.__cause__, "원 예외를 보존해야 한다")


class MarketOrderTests(_ScriptCase):
    """**US가 먼저 돌아야 한다** — 알파벳순으로 되돌리면 미국이 다시 전멸한다.

    야후는 한 러너 IP에서 약 800번을 넘기면 그 뒤로 전부 끊는다(2026-08-14 러너 실측:
    1,506종목 중 803에서 절벽 · 끊긴 뒤엔 토큰을 다시 받아도, 주가·재무 같은 다른
    통로로도 안 된다). 그래서 **먼저 도는 시장이 예산을 다 태운다.**

    KR이 먼저면 US는 차례가 왔을 때 이미 끊겨 다리 0개가 된다. KR은 기둥(per·pbr)이
    네이버라 야후가 끊겨도 얇아질 뿐이므로, 예산은 **잃을 것이 많은 US에 먼저** 준다.

    이 테스트가 지키는 것은 숫자가 아니라 **순서**다 — 기본값이 조용히
    알파벳순으로 돌아가는 것을 막는다.
    """

    def test_the_default_order_puts_us_first(self):
        seen = []

        def fake_build(market):
            seen.append(market)
            return {"per": {"a": 1.0}}, 100

        argv = ["build", "--out", str(self.out)]        # --markets를 주지 않는다 = 기본값
        with patch.object(self.mod, "build", fake_build), \
             patch.object(self.mod, "ROOT", Path("/")), \
             patch.object(sys, "argv", argv):
            self.mod.main()

        self.assertEqual(seen, ["US", "KR"],
                         "US가 먼저여야 한다 — KR이 먼저 돌면 야후 예산을 태워 US가 전멸한다")


if __name__ == "__main__":
    unittest.main()
