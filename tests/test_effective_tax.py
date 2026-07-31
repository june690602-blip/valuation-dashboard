"""유효세율(_effective_tax_rate) — 적자 해 허수·기간 중복·법정세율 초과 방어."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import pandas as pd

from src.analysis.capital_cost import _effective_tax_rate, _tax_pair


def _company(years: dict, ttm: dict | None = None):
    """years = {연도: (법인세, 세전이익)} · ttm = {'tax_expense':…, 'pretax_income':…}"""
    fin = pd.DataFrame(
        {"tax_expense": [v[0] for v in years.values()],
         "pretax_income": [v[1] for v in years.values()]},
        index=list(years),
    )
    return SimpleNamespace(
        financials=fin,
        latest=lambda col: (ttm or {}).get(col),
    )


class TaxPairTests(unittest.TestCase):
    def test_loss_year_is_dropped(self):
        # 롯데케미칼 2025: 세전 -2.71조 · 법인세 -0.23조 → 음수÷음수 = 8.4% (허수)
        self.assertIsNone(_tax_pair(-0.23e12, -2.71e12))

    def test_zero_pretax_is_dropped(self):
        self.assertIsNone(_tax_pair(1.0, 0.0))

    def test_profit_year_is_kept(self):
        self.assertAlmostEqual(_tax_pair(24.0, 100.0), 0.24)


class EffectiveTaxRateTests(unittest.TestCase):
    def test_all_loss_years_fall_back_to_statutory(self):
        # 적자만 이어지면 측정값이 없어야 하고, 법정세율로 폴백한다.
        d = _company({2024: (-0.44e12, -2.28e12), 2025: (-0.23e12, -2.71e12)},
                     ttm={"tax_expense": -0.22e12, "pretax_income": -2.42e12})
        rate, ok, raw = _effective_tax_rate(d, 0.24)
        self.assertFalse(ok)
        self.assertIsNone(raw)
        self.assertAlmostEqual(rate, 0.24)

    def test_capped_at_statutory_rate(self):
        # 실효세율이 법정세율보다 높아도 세금방패는 법정세율을 넘지 못한다.
        d = _company({2023: (-0.10e12, -0.52e12), 2024: (0.18e12, 0.44e12)})
        rate, ok, raw = _effective_tax_rate(d, 0.24)
        self.assertTrue(ok)
        self.assertGreater(raw, 0.24)
        self.assertAlmostEqual(rate, 0.24)

    def test_ttm_does_not_double_count_overlapping_year(self):
        # TTM을 쓰면 기간이 겹치는 최근 연도는 평균에서 빠진다.
        d = _company({2023: (2.0, 10.0), 2024: (1.0, 10.0), 2025: (4.0, 10.0)},
                     ttm={"tax_expense": 3.0, "pretax_income": 10.0})
        rate, ok, raw = _effective_tax_rate(d, 0.40)
        self.assertTrue(ok)
        # TTM 30% + FY2024 10% + FY2023 20% = 20%  (FY2025의 40%는 제외)
        self.assertAlmostEqual(raw, 0.20)
        self.assertAlmostEqual(rate, 0.20)

    def test_uses_latest_two_years_when_no_ttm(self):
        d = _company({2023: (2.0, 10.0), 2024: (1.0, 10.0), 2025: (3.0, 10.0)})
        rate, ok, raw = _effective_tax_rate(d, 0.40)
        self.assertTrue(ok)
        self.assertAlmostEqual(raw, 0.20)   # FY2024 10% + FY2025 30%


if __name__ == "__main__":
    unittest.main()
