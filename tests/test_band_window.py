"""② 역사적 밴드의 창(ADR-0026) — 창은 상수가 정하지, 주가를 몇 년 받았는지가 정하지 않는다.

이 파일이 막으려는 사고는 **조용한 변경**이다. ADR-0026 전에는 밴드의 창이
`base.py`의 `fetch_price_frame(..., period="5y")` 기본 인자였다. 즉 데이터 수집 쪽을
건드리는 사람이 판정을 바꿀 수 있었고, 예외도 경고도 안 났다. 여기서 상수를 고정한다.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.analysis.valuation import (BAND_WINDOW_YEARS, GATE_PBR_WINDOW_YEARS,
                                    _band)
from src.data.models import CompanyData


END = pd.Timestamp("2026-06-30")
BASE_YEAR = 2012
SHARES = 100.0


def _company(price_years: float, drift: float = 0.10) -> CompanyData:
    """**주가 이력의 길이만** 다른 최소 CompanyData.

    값을 날짜의 함수로 고정한다 — 그래야 8년짜리와 12년짜리의 **겹치는 구간이 완전히
    같아지고**, 밴드가 달라진다면 그것은 오직 창 때문이다. (처음에는 길이에 맞춰
    `linspace`로 만들었는데, 그러면 최근 7년의 주가 경로 자체가 서로 달라 시험이
    창이 아니라 픽스처를 재고 있었다.)

    EPS는 연도마다 `drift`만큼 늘려 배수가 시간에 따라 **움직이게** 둔다 — 상수로 두면
    창을 잘라도 분위가 같아서 이 시험이 아무것도 못 잡는다.
    """
    idx = pd.bdate_range(end=END, periods=int(252 * price_years))
    years = sorted({d.year for d in idx})
    fin = pd.DataFrame({
        "eps": [10.0 * (1 + drift) ** (y - BASE_YEAR) for y in years],
        "net_income": [1000.0] * len(years),
        "total_equity": [10_000.0] * len(years),
        "shares_outstanding": [SHARES] * len(years),
        "fiscal_end": [pd.Timestamp(f"{y}-12-31") for y in years],
    }, index=years)
    # 주가도 날짜의 함수 — 추세 위에 주기를 얹어 배수 분포가 한 점에 몰리지 않게 한다.
    t = np.array([(d - pd.Timestamp(f"{BASE_YEAR}-01-01")).days / 365.25 for d in idx])
    px = pd.Series(100.0 * (1.08 ** t) * (1.0 + 0.15 * np.sin(t * 2.0)), index=idx)
    return CompanyData(
        ticker="T", yahoo_ticker="T", name="T", market="KR", currency="KRW",
        sector="", industry="", price=float(px.iloc[-1]),
        market_cap=float(px.iloc[-1]) * SHARES, shares_outstanding=SHARES,
        financials=fin, ttm=None, prices=px, index_prices=px,
        benchmark_name="KOSPI", peers=pd.DataFrame(),
    )


class BandWindowTests(unittest.TestCase):
    def test_window_is_the_constant_not_the_price_history(self):
        """주가를 12년 받아도 밴드는 `BAND_WINDOW_YEARS`만 본다."""
        _, _, _, _, qual = _band(_company(12.0, drift=0.10), 10.0, "per")
        self.assertIsNotNone(qual)
        self.assertLessEqual(qual["years"], BAND_WINDOW_YEARS + 0.1)
        self.assertGreater(qual["years"], BAND_WINDOW_YEARS - 1.0)

    def test_short_history_is_left_alone(self):
        """주가가 창보다 짧으면 자를 것이 없다 — 예전 동작 그대로."""
        _, _, _, _, qual = _band(_company(4.0, drift=0.10), 10.0, "per")
        self.assertIsNotNone(qual)
        self.assertLess(qual["years"], 4.2)

    def test_deeper_prices_do_not_move_the_band(self):
        """**이 파일의 요점.** 주가를 더 받아도 밴드 분위가 안 바뀐다.

        ADR-0026 전에는 여기서 값이 달라졌다 — 그것이 '조용한 변경'이었다.
        """
        _, _, fair8, q8, _ = _band(_company(8.0, drift=0.10), 10.0, "per")
        _, _, fair12, q12, _ = _band(_company(12.0, drift=0.10), 10.0, "per")
        self.assertIsNotNone(fair8)
        self.assertIsNotNone(fair12)
        for i in range(3):
            self.assertAlmostEqual(fair8[i], fair12[i], places=6)
        self.assertAlmostEqual(q8[50], q12[50], places=6)

    def test_fetched_price_history_covers_the_band_window(self):
        """주가를 창보다 짧게 받으면 밴드가 **조용히** 짧아진다 — 그 어긋남을 막는다.

        `_band`는 있는 만큼만 자르므로 예외도 경고도 안 난다. ADR-0025가
        `HISTORY_YEARS >= NORMALIZE_WINDOW`에 건 것과 같은 종류의 관문이다.
        """
        from src.data.base import PRICE_PERIOD
        self.assertRegex(PRICE_PERIOD, r"^\d+y$")
        self.assertGreaterEqual(
            int(PRICE_PERIOD[:-1]), BAND_WINDOW_YEARS,
            "base.PRICE_PERIOD가 BAND_WINDOW_YEARS보다 짧다 — 밴드 창이 조용히 짧아진다")

    def test_gate_median_uses_its_own_shorter_window(self):
        """ADR-0010 게이트의 PBR 중앙값은 밴드 창을 따라가지 않는다(ADR-0026 결정 3)."""
        self.assertLess(GATE_PBR_WINDOW_YEARS, BAND_WINDOW_YEARS)
        _, _, _, q, _ = _band(_company(12.0, drift=0.10), 10.0, "per")
        self.assertIsNotNone(q.get("gate_median"))
        self.assertLessEqual(q["gate_years"], GATE_PBR_WINDOW_YEARS + 0.1)
        # 배수가 시간에 따라 움직이므로 두 창의 중앙값은 **달라야** 한다.
        # 같다면 게이트가 밴드 창을 그대로 따라간 것이다.
        self.assertNotAlmostEqual(q["gate_median"], q[50], places=6)


if __name__ == "__main__":
    unittest.main()
