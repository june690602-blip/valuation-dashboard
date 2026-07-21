"""선행 이익(컨센서스) 적정가·시나리오 분석 순수 함수 테스트.

CI(quality.yml)는 `python -m unittest discover`로 실행하므로 unittest 규약을 따른다.
"""
from __future__ import annotations

import unittest

import pandas as pd

from src.analysis.scenario import build_scenarios
from src.analysis.valuation import _forward_value
from src.data.models import Consensus, recomm_label

PER_Q = {10: 5.0, 25: 8.0, 50: 10.0, 75: 12.0, 90: 15.0, "current": 11.0}


# ── ④ 선행 이익 적정가 ──────────────────────────────────────────────
class ForwardValueTests(unittest.TestCase):
    def test_own_median_is_mid_and_band_is_range(self):
        fv = _forward_value(fwd_eps=100.0, peer_fwd_per=10.0, per_q=PER_Q)
        self.assertIsNotNone(fv)
        self.assertAlmostEqual(fv.mid, 1000.0)   # 자기 5년 중앙값 10배 × 100
        # 범위 = 자기 5년 밴드 q25~q75 × 선행 EPS
        self.assertAlmostEqual(fv.low, 800.0)
        self.assertAlmostEqual(fv.high, 1200.0)

    def test_own_median_primary_over_peer(self):
        # 실증 결과(가격 설명력·목표가 내재 멀티플 일치)에 따라 자기 5년 중앙값 우선.
        # 피어 선행PER가 크게 달라도(소형주 오염 등) 자기 중앙값을 쓴다.
        fv_hi = _forward_value(fwd_eps=100.0, peer_fwd_per=20.0, per_q=PER_Q)
        fv_lo = _forward_value(fwd_eps=100.0, peer_fwd_per=6.0, per_q=PER_Q)
        self.assertAlmostEqual(fv_hi.mid, 1000.0)
        self.assertAlmostEqual(fv_lo.mid, 1000.0)

    def test_fallback_to_historical_median_without_peer(self):
        fv = _forward_value(fwd_eps=100.0, peer_fwd_per=None, per_q=PER_Q)
        self.assertAlmostEqual(fv.mid, 1000.0)
        self.assertAlmostEqual(fv.low, 800.0)
        self.assertAlmostEqual(fv.high, 1200.0)

    def test_peer_only_without_history(self):
        fv = _forward_value(fwd_eps=100.0, peer_fwd_per=9.0, per_q=None)
        self.assertAlmostEqual(fv.low, 900.0)
        self.assertAlmostEqual(fv.mid, 900.0)
        self.assertAlmostEqual(fv.high, 900.0)

    def test_none_when_no_inputs(self):
        self.assertIsNone(_forward_value(fwd_eps=100.0, peer_fwd_per=None, per_q=None))

    def test_none_when_loss_making(self):
        self.assertIsNone(_forward_value(fwd_eps=-50.0, peer_fwd_per=10.0, per_q=PER_Q))
        self.assertIsNone(_forward_value(fwd_eps=None, peer_fwd_per=10.0, per_q=PER_Q))


# ── 시나리오(비관/기준/낙관) + 민감도 ───────────────────────────────
class ScenarioTests(unittest.TestCase):
    def test_three_cases_with_band_multiples(self):
        res = build_scenarios(price=10_000.0, eps_fwd=1_000.0, eps_ttm=900.0,
                              per_q=PER_Q, peer_per=None,
                              bear_delta=-0.15, bull_delta=0.15)
        self.assertIsNotNone(res)
        self.assertEqual([c.name for c in res.cases], ["비관", "기준", "낙관"])
        bear, base, bull = res.cases
        # 기준: 조정 0% × q50 → 1000 × 10 = 10,000 (현재가와 같음 → 괴리 0)
        self.assertAlmostEqual(base.price, 10_000.0)
        self.assertAlmostEqual(base.upside, 0.0)
        # 비관: 1000×0.85 × q25(8) = 6,800 | 낙관: 1000×1.15 × q75(12) = 13,800
        self.assertAlmostEqual(bear.price, 6_800.0)
        self.assertAlmostEqual(bull.price, 13_800.0)
        self.assertIn("컨센서스", res.eps_basis)

    def test_ttm_fallback_when_no_consensus(self):
        res = build_scenarios(price=10_000.0, eps_fwd=None, eps_ttm=900.0,
                              per_q=PER_Q, peer_per=None)
        self.assertAlmostEqual(res.eps_base, 900.0)
        self.assertIn("TTM", res.eps_basis)

    def test_peer_fallback_when_no_band(self):
        res = build_scenarios(price=10_000.0, eps_fwd=1_000.0, eps_ttm=None,
                              per_q=None, peer_per=10.0,
                              bear_delta=-0.15, bull_delta=0.15)
        # 밴드 없으면 피어 PER × (0.8 / 1.0 / 1.2)
        self.assertAlmostEqual(res.cases[1].price, 10_000.0)
        self.assertAlmostEqual(res.cases[0].price, 8.0 * 850.0)
        self.assertAlmostEqual(res.cases[2].price, 12.0 * 1150.0)

    def test_none_when_loss_making(self):
        self.assertIsNone(build_scenarios(price=100.0, eps_fwd=None, eps_ttm=-10.0,
                                          per_q=PER_Q, peer_per=10.0))
        self.assertIsNone(build_scenarios(price=100.0, eps_fwd=None, eps_ttm=1000.0,
                                          per_q=None, peer_per=None))

    def test_multiple_adjust(self):
        # 멀티플 조정 +10%는 세 케이스 가격을 모두 10% 올린다 (그리드는 불변)
        base = build_scenarios(price=10_000.0, eps_fwd=1_000.0, eps_ttm=None,
                               per_q=PER_Q, peer_per=None)
        adj = build_scenarios(price=10_000.0, eps_fwd=1_000.0, eps_ttm=None,
                              per_q=PER_Q, peer_per=None, mult_adjust=0.10)
        for b, a in zip(base.cases, adj.cases):
            with self.subTest(case=b.name):
                self.assertAlmostEqual(a.price / b.price, 1.10)
        self.assertAlmostEqual(adj.grid.iloc[2, 2], base.grid.iloc[2, 2])

    def test_sensitivity_grid(self):
        res = build_scenarios(price=10_000.0, eps_fwd=1_000.0, eps_ttm=None,
                              per_q=PER_Q, peer_per=None)
        g = res.grid
        self.assertIsInstance(g, pd.DataFrame)
        self.assertEqual(g.shape, (5, 5))              # EPS 5단계 × 멀티플 5단계
        # 중앙 셀 = 조정 0% × q50 = 기준 가격
        self.assertAlmostEqual(g.iloc[2, 2], 10_000.0)
        # 행(EPS) 증가·열(멀티플) 증가 방향으로 단조 증가
        self.assertLess(g.iloc[0, 2], g.iloc[2, 2])
        self.assertLess(g.iloc[2, 2], g.iloc[4, 2])
        self.assertLess(g.iloc[2, 0], g.iloc[2, 2])
        self.assertLess(g.iloc[2, 2], g.iloc[2, 4])


# ── 컨센서스 모델 ───────────────────────────────────────────────────
class ConsensusModelTests(unittest.TestCase):
    def test_recomm_label_bands(self):
        for score, expected in ((4.6, "적극매수"), (4.04, "매수"), (3.0, "중립"),
                                (2.0, "매도"), (1.2, "적극매도")):
            with self.subTest(score=score):
                self.assertEqual(recomm_label(score), expected)
        self.assertIsNone(recomm_label(None))

    def test_has_any(self):
        self.assertFalse(Consensus().has_any())
        self.assertTrue(Consensus(forward_eps=100.0).has_any())
        self.assertTrue(Consensus(target_mean=50_000.0).has_any())


if __name__ == "__main__":
    unittest.main()
