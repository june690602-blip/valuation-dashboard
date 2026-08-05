"""정규화 이익 축(ADR-0015) 순수 함수 테스트."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.analysis.valuation import (NORMALIZE_MIN_YEARS, NORMALIZE_WINDOW,
                                    _normalized_earnings, _normalized_value)
from src.analysis.warranted import fit_leg, warranted_multiple


def _fin(values):
    """net_income 열만 가진 연간 재무 프레임 (과거→최신)."""
    return pd.DataFrame({"net_income": values},
                        index=range(2019, 2019 + len(values)))


class NormalizedEarningsTests(unittest.TestCase):
    def test_averages_the_last_eight_years(self):
        # 9년이 있으면 **마지막 8년만** 쓴다 — 창은 최신 쪽으로 고정이다
        ni, years = _normalized_earnings(
            _fin([1000., 100., 200., 300., 400., 500., 600., 700., 800.]))
        self.assertEqual(years, 8)
        self.assertAlmostEqual(ni, 450.0)     # (100+…+800)/8 — 맨 앞 1000은 창 밖

    def test_uses_what_exists_when_history_is_short(self):
        ni, years = _normalized_earnings(_fin([100., 200., 300.]))
        self.assertEqual(years, 3)
        self.assertAlmostEqual(ni, 200.0)

    def test_refuses_when_fewer_than_min_years(self):
        ni, years = _normalized_earnings(_fin([100., 200.]))
        self.assertIsNone(ni)
        self.assertEqual(years, 2)

    def test_drops_missing_inside_the_window(self):
        # 창 안 결측은 빼고 남은 것으로 평균한다. 0으로 채우면 이익을 지어내는 것이다.
        ni, years = _normalized_earnings(_fin([100., np.nan, 300., 400., 500.]))
        self.assertEqual(years, 4)
        self.assertAlmostEqual(ni, 325.0)     # (100+300+400+500)/4

    def test_refuses_when_too_many_missing(self):
        ni, years = _normalized_earnings(_fin([100., np.nan, np.nan, np.nan, 500.]))
        self.assertIsNone(ni)
        self.assertEqual(years, 2)

    def test_keeps_negative_years(self):
        # 적자 연도를 빼면 정규화가 아니라 체리피킹이다. 사이클 저점이 창에 들어와야 한다.
        ni, years = _normalized_earnings(_fin([-100., -50., 100., 200., 350.]))
        self.assertEqual(years, 5)
        self.assertAlmostEqual(ni, 100.0)

    def test_handles_missing_column_and_empty_frame(self):
        self.assertEqual(_normalized_earnings(None), (None, 0))
        self.assertEqual(_normalized_earnings(pd.DataFrame()), (None, 0))
        self.assertEqual(_normalized_earnings(pd.DataFrame({"revenue": [1.0]})), (None, 0))

    def test_drops_infinities(self):
        ni, years = _normalized_earnings(_fin([np.inf, 100., 200., 300., 400.]))
        self.assertEqual(years, 4)
        self.assertAlmostEqual(ni, 250.0)

    def test_window_and_minimum_are_eight_and_three(self):
        self.assertEqual(NORMALIZE_WINDOW, 8)
        self.assertEqual(NORMALIZE_MIN_YEARS, 3)

    def test_data_layer_reaches_back_at_least_as_far_as_the_window(self):
        """창을 올려도 데이터가 안 따라오면 **조용히** 짧아진다 — 그 어긋남을 여기서 막는다.

        `_normalized_earnings`는 프레임의 마지막 `NORMALIZE_WINDOW`행을 쓰는데,
        `opendart.py`가 그보다 얕게 잘라 두면 평균에 쓰는 연수가 말없이 줄어든다.
        예외도 경고도 안 나므로 시험이 아니면 알아챌 방법이 없다.
        """
        from src.data.opendart import HISTORY_YEARS
        self.assertGreaterEqual(
            HISTORY_YEARS, NORMALIZE_WINDOW,
            "opendart.HISTORY_YEARS가 NORMALIZE_WINDOW보다 얕다 — 창이 조용히 짧아진다")

    def test_epv_shares_the_same_window(self):
        """EPV도 같은 창을 쓴다(ADR-0025 · 인계문 경고 4). 한쪽만 바뀌면 안 된다."""
        from src.analysis import epv
        self.assertEqual(epv.NORMALIZE_WINDOW, NORMALIZE_WINDOW)


def _synthetic_per(n=600, beta=0.30, seed=0):
    """log(PER) = -6 + 0.30·log(시총) + 업종효과 인 합성 데이터."""
    rng = np.random.default_rng(seed)
    mcap = np.exp(rng.uniform(np.log(1e10), np.log(1e13), n))
    sector = rng.choice(["A", "B", "C"], n)
    eff = {"A": 0.0, "B": 0.5, "C": -0.4}
    roe = rng.uniform(0.0, 0.20, n)
    y = -6.0 + beta * np.log(mcap) + np.array([eff[s] for s in sector])
    return pd.DataFrame({"multiple": np.exp(y), "mcap": mcap,
                         "sector": sector, "roe": roe})


class NormalizedValueTests(unittest.TestCase):
    def setUp(self):
        self.coef = {"per": fit_leg(_synthetic_per(), leg="per")}
        self.mcap = 1e11
        self.shares = 1_000_000.0
        self.equity = 5e10

    def _call(self, values, **kw):
        kw.setdefault("shares", self.shares)
        kw.setdefault("equity", self.equity)
        kw.setdefault("mcap", self.mcap)
        kw.setdefault("sector", "B")
        kw.setdefault("coef", self.coef)
        return _normalized_value(_fin(values), **kw)

    def test_fair_value_is_regression_per_times_normalized_eps(self):
        fv, meta = self._call([100e8, 200e8, 300e8, 400e8, 500e8])
        self.assertIsNotNone(fv)
        self.assertEqual(fv.method, "정규화 이익")
        self.assertAlmostEqual(meta["eps"], 300e8 / self.shares, places=6)
        self.assertAlmostEqual(fv.mid, meta["per"] * meta["eps"], delta=fv.mid * 1e-9)
        self.assertEqual(meta["years"], 5)

    def test_stands_when_current_year_is_a_loss_but_average_is_positive(self):
        # 이 축의 존재 이유다 — 현재 적자라 ①은 부정확한 PSR로 가고 ②는 아예 빠진다.
        fv, meta = self._call([500e8, 400e8, 300e8, 200e8, -100e8])
        self.assertIsNotNone(fv)
        self.assertAlmostEqual(meta["eps"], 260e8 / self.shares, places=6)
        self.assertIsNone(meta["ratio"])      # 현재이익 ≤ 0이면 비율이 뜻을 잃는다

    def test_refused_when_average_is_a_loss(self):
        fv, meta = self._call([-100e8, -200e8, -300e8, -400e8, -500e8])
        self.assertIsNone(fv)
        self.assertIn("적자", meta["reason"])

    def test_refused_when_history_is_short(self):
        fv, meta = self._call([100e8, 200e8])
        self.assertIsNone(fv)
        self.assertIn("이력", meta["reason"])

    def test_refused_without_per_coefficient(self):
        # 계수가 없으면 피어 중앙값으로 폴백하지 않는다 — 배수를 바꾸면 다른 방법이다.
        fv, meta = self._call([100e8, 200e8, 300e8, 400e8, 500e8], coef={})
        self.assertIsNone(fv)
        self.assertIn("계수", meta["reason"])

    def test_refused_without_shares_or_equity(self):
        for kw in ({"shares": 0}, {"equity": 0}, {"shares": None}, {"equity": None}):
            with self.subTest(**kw):
                fv, _ = self._call([100e8, 200e8, 300e8, 400e8, 500e8], **kw)
                self.assertIsNone(fv)

    def test_uses_normalized_roe_not_current_roe(self):
        # 현재 적자여도 정상 이익이 흑자면 ROE도 흑자 구간으로 넣는다. 현재 ROE를 쓰면
        # ADR-0014의 U자 더미가 '대규모 적자' 칸을 골라 배수를 크게 올린다(실측 +110%).
        losses = [500e8, 400e8, 300e8, 200e8, -100e8]
        _fv, meta = self._call(losses)
        want = warranted_multiple(self.coef["per"], self.mcap, "B",
                                  (sum(losses) / 5) / self.equity)["multiple"]
        self.assertAlmostEqual(meta["per"], want, delta=want * 1e-9)

    def test_ratio_reports_how_far_normalization_moved_it(self):
        _fv, meta = self._call([100e8, 200e8, 300e8, 400e8, 500e8])
        self.assertAlmostEqual(meta["ratio"], 300e8 / 500e8, places=6)   # 0.6

    def test_range_comes_from_the_windows_own_spread(self):
        fv, _ = self._call([100e8, 200e8, 300e8, 400e8, 500e8])
        self.assertLessEqual(fv.low, fv.mid)
        self.assertLessEqual(fv.mid, fv.high)
        self.assertLess(fv.low, fv.high)

    def test_range_degenerates_rather_than_going_negative(self):
        # 창 하위 분위가 적자면 low를 음수로 두지 않는다 — 음수 적정가는 뜻이 없다.
        fv, _ = self._call([-400e8, -200e8, 300e8, 600e8, 900e8])
        self.assertGreater(fv.low, 0)
        self.assertLessEqual(fv.low, fv.mid)


class WeightRegistrationTests(unittest.TestCase):
    def test_registered_at_the_same_tier_as_relative_and_band(self):
        # 가중치는 우리 측정이 아니라 문헌 순위의 인코딩이다(ADR-0003의 원칙).
        # LNT(2002)의 '과거 이익 멀티플' 칸이고, 그 칸 안에서 Anderson&Brooks(2006)가
        # 장기평균이 단년보다 낫다고 보고했으므로 ①②보다 낮출 근거가 없다.
        from src.analysis.valuation import FUNDAMENTAL_METHODS, METHOD_WEIGHTS

        self.assertEqual(METHOD_WEIGHTS["정규화 이익"], 0.25)
        self.assertEqual(METHOD_WEIGHTS["정규화 이익"], METHOD_WEIGHTS["업종 상대가치"])
        self.assertIn("정규화 이익", FUNDAMENTAL_METHODS)
        self.assertNotIn("선행 이익(컨센서스)", FUNDAMENTAL_METHODS)

    def test_renormalized_weights_match_the_design(self):
        from src.analysis.valuation import FairValue, _weighted

        est = [FairValue(m, 1.0, 1.0, 1.0) for m in
               ("업종 상대가치", "역사적 밴드", "수익가치(RIM)", "정규화 이익")]
        _lo, _mid, _hi, w = _weighted(est)
        self.assertAlmostEqual(w["업종 상대가치"], 0.2778, places=3)
        self.assertAlmostEqual(w["역사적 밴드"], 0.2778, places=3)
        self.assertAlmostEqual(w["수익가치(RIM)"], 0.1667, places=3)
        self.assertAlmostEqual(w["정규화 이익"], 0.2778, places=3)

    def test_result_carries_the_normalization_evidence(self):
        from src.analysis.valuation import ValuationResult

        r = ValuationResult()
        self.assertIsNone(r.normalized_eps)
        self.assertIsNone(r.normalized_years)
        self.assertIsNone(r.normalized_ratio)
        self.assertIsNone(r.normalized_per)


if __name__ == "__main__":
    unittest.main()
