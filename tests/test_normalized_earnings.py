"""정규화 이익 축(ADR-0015) 순수 함수 테스트."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.analysis.valuation import (NORMALIZE_MIN_YEARS, NORMALIZE_WINDOW,
                                    _normalized_earnings)


def _fin(values):
    """net_income 열만 가진 연간 재무 프레임 (과거→최신)."""
    return pd.DataFrame({"net_income": values},
                        index=range(2019, 2019 + len(values)))


class NormalizedEarningsTests(unittest.TestCase):
    def test_averages_the_last_five_years(self):
        # 6년이 있으면 **마지막 5년만** 쓴다 — 창은 최신 쪽으로 고정이다
        ni, years = _normalized_earnings(_fin([1000., 100., 200., 300., 400., 500.]))
        self.assertEqual(years, 5)
        self.assertAlmostEqual(ni, 300.0)     # (100+200+300+400+500)/5

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

    def test_window_and_minimum_are_five_and_three(self):
        self.assertEqual(NORMALIZE_WINDOW, 5)
        self.assertEqual(NORMALIZE_MIN_YEARS, 3)


if __name__ == "__main__":
    unittest.main()
