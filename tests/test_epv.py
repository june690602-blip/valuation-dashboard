"""EPV 축 후보(ADR-0023 예정) 순수 함수 테스트.

실제 종목 전수 측정은 `scripts/check_epv_viability.py`가 한다(네트워크 필요).
여기서는 산식과 **안 서는 조건**만 본다 — 값을 못 내는 자리에서 억지로 숫자를
만들지 않는 것이 이 축의 핵심이다(ADR-0011).
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.analysis.epv import epv_per_share, normalized_operating_income


def _fin(values):
    """operating_income 열만 가진 연간 재무 프레임 (과거→최신)."""
    return pd.DataFrame({"operating_income": values},
                        index=range(2019, 2019 + len(values)))


class NormalizedOperatingIncomeTests(unittest.TestCase):
    def test_averages_the_last_five_years(self):
        # 6년이 있으면 마지막 5년만 쓴다 — `_normalized_earnings`와 같은 창 규칙이다
        oi, years = normalized_operating_income(_fin([9999., 100., 200., 300., 400., 500.]))
        self.assertEqual(years, 5)
        self.assertAlmostEqual(oi, 300.0)

    def test_uses_what_exists_when_history_is_short(self):
        oi, years = normalized_operating_income(_fin([100., 200., 300.]))
        self.assertEqual(years, 3)
        self.assertAlmostEqual(oi, 200.0)

    def test_refuses_when_fewer_than_min_years(self):
        oi, years = normalized_operating_income(_fin([100., 200.]))
        self.assertIsNone(oi)
        self.assertEqual(years, 2)

    def test_keeps_loss_years_in_the_window(self):
        # 적자 해를 빼면 정규화가 아니라 체리피킹이다. 사이클이 이 축이 감당할 대상이다.
        oi, years = normalized_operating_income(_fin([-200., 100., 200., 300., 400.]))
        self.assertEqual(years, 5)
        self.assertAlmostEqual(oi, 160.0)

    def test_drops_missing_and_infinite_inside_the_window(self):
        oi, years = normalized_operating_income(_fin([100., np.nan, np.inf, 300., 500.]))
        self.assertEqual(years, 3)
        self.assertAlmostEqual(oi, 300.0)

    def test_returns_none_without_the_column(self):
        oi, years = normalized_operating_income(pd.DataFrame({"net_income": [1., 2., 3.]}))
        self.assertIsNone(oi)
        self.assertEqual(years, 0)


class EpvPerShareTests(unittest.TestCase):
    def test_known_inputs(self):
        # 기업가치 100×0.8/0.08 = 1,000 · 주주가치 1,000 − 150 = 850 · 주당 85.0
        v = epv_per_share(op_income=100.0, tax_rate=0.20, wacc=0.08,
                          net_debt=150.0, shares=10.0)
        self.assertAlmostEqual(v, 85.0)

    def test_net_cash_raises_the_value(self):
        # 순부채가 음수(현금이 차입금보다 많음)면 주주가치가 기업가치보다 크다
        v = epv_per_share(op_income=100.0, tax_rate=0.20, wacc=0.08,
                          net_debt=-200.0, shares=10.0)
        self.assertAlmostEqual(v, 120.0)

    def test_refuses_when_operating_income_is_not_positive(self):
        for oi in (0.0, -50.0, None):
            with self.subTest(oi=oi):
                self.assertIsNone(epv_per_share(oi, 0.2, 0.08, 0.0, 10.0))

    def test_refuses_without_wacc(self):
        self.assertIsNone(epv_per_share(100.0, 0.2, None, 0.0, 10.0))

    def test_refuses_when_wacc_is_not_positive(self):
        # 할인율이 0 이하면 영구가치가 발산하거나 부호가 뒤집힌다
        for w in (0.0, -0.01):
            with self.subTest(wacc=w):
                self.assertIsNone(epv_per_share(100.0, 0.2, w, 0.0, 10.0))

    def test_refuses_without_usable_tax_rate(self):
        for t in (None, -0.1, 1.0, 1.5):
            with self.subTest(tax=t):
                self.assertIsNone(epv_per_share(100.0, t, 0.08, 0.0, 10.0))

    def test_refuses_when_debt_exceeds_enterprise_value(self):
        # 기업가치 1,000인데 순부채 1,200 → 주주가치 음수. 음수 적정주가는 판정에 못 쓴다.
        self.assertIsNone(epv_per_share(100.0, 0.20, 0.08, 1200.0, 10.0))

    def test_refuses_without_shares(self):
        for s in (None, 0.0, -10.0):
            with self.subTest(shares=s):
                self.assertIsNone(epv_per_share(100.0, 0.2, 0.08, 0.0, s))


if __name__ == "__main__":
    unittest.main()
