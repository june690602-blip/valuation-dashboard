"""ETF 배당수익률의 단위 정규화 — 소수/퍼센트 혼입 방어(이슈 #58).

야후는 배당수익률을 버전에 따라 소수(0.0101)로도 퍼센트(1.01)로도 준다. 주식 경로에는
`base._norm_div_yield`가 이미 걸려 있었지만 **미국 ETF 경로에만 없었다**(한국 ETF는
`kr_etf_provider._pct`로 이미 정상). 단위가 100배 틀리면 배당수익률 역사밴드가 통째로
튀고, 그 밴드는 주식·가치·배당형 ETF 판정의 **주도 축**이라 판정이 뒤집힌다.

이 테스트가 못 박는 것 둘:
  ㉠ **지금 값(소수)은 안 바뀐다** — 방어를 넣어도 출력이 달라지면 잘못 끼운 것이다.
  ㉡ 퍼센트로 오면 소수로 되돌린다 — 본체와 벤치마크 **양쪽 다**.

네트워크는 쓰지 않는다(yfinance와 시세 수집 셋을 모킹).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.data import etf_provider

# 실측값(이슈 #58) — 지금 yfinance가 주는 단위. 이 셋은 정규화를 지나도 그대로여야 한다.
FRACTION_SAMPLES = {"SPY": 0.0101, "TLT": 0.0453, "SCHD": 0.033}


def _prices(n: int = 400) -> pd.Series:
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.Series(100.0 * (1.0005 ** np.arange(n)), index=idx, dtype=float)


class _FakeTicker:
    """yf.Ticker 대역 — 심볼별로 다른 info를 돌려준다."""

    def __init__(self, infos: dict):
        self._infos = infos
        self.symbol = None

    def __call__(self, symbol):
        self.symbol = symbol
        return self

    @property
    def info(self):
        return dict(self._infos.get(self.symbol, {}))

    @property
    def dividends(self):
        return pd.Series(dtype=float)

    @property
    def funds_data(self):
        raise RuntimeError("펀드 구성 데이터 없음 — 이 테스트의 관심사가 아니다")


def _fetch(etf_yield, bench_yield):
    """주어진 원시 yield 둘로 fetch_etf를 돌려 (본체, 벤치마크) 정규화 결과를 낸다."""
    infos = {
        "TESTETF": {"quoteType": "ETF", "longName": "Test Large Blend ETF",
                    "category": "Large Blend", "regularMarketPrice": 100.0,
                    "yield": etf_yield},
        # category 'Large Blend' → `_benchmark_for`가 VTI를 고른다.
        "VTI": {"trailingPE": 21.0, "yield": bench_yield},
    }
    px = _prices()
    with patch.object(etf_provider, "yf") as fake_yf, \
         patch.object(etf_provider, "fetch_prices", return_value=px), \
         patch.object(etf_provider, "fetch_prices_raw", return_value=px), \
         patch.object(etf_provider, "fetch_index_prices", return_value=px):
        fake_yf.Ticker = _FakeTicker(infos)
        data = etf_provider.fetch_etf("TESTETF")
    return data.div_yield, data.bench_yield


class EtfDivYieldNormalizationTests(unittest.TestCase):
    def test_benchmark_is_the_one_we_expect(self):
        """앞선 테스트들이 벤치마크 경로를 실제로 지나는지 먼저 확인한다.

        `_benchmark_for`가 나중에 바뀌어 VTI가 아닌 것을 고르면, 아래 벤치마크
        단언들이 **조용히 아무것도 검사하지 않게** 된다."""
        self.assertEqual(etf_provider._benchmark_for("Large Blend", "Test")[0], "VTI")

    def test_fraction_input_is_unchanged(self):
        """지금 야후가 주는 소수값은 그대로여야 한다 — 고쳐도 출력이 바뀌면 안 된다."""
        for sym, frac in FRACTION_SAMPLES.items():
            with self.subTest(etf=sym):
                div, bench = _fetch(frac, frac)
                self.assertAlmostEqual(div, frac, places=12)
                self.assertAlmostEqual(bench, frac, places=12)

    def test_percent_input_is_converted(self):
        """퍼센트로 오면 /100 해서 소수로 되돌린다 — 본체와 벤치마크 양쪽."""
        for sym, frac in FRACTION_SAMPLES.items():
            with self.subTest(etf=sym):
                div, bench = _fetch(frac * 100.0, frac * 100.0)
                self.assertAlmostEqual(div, frac, places=12)
                self.assertAlmostEqual(bench, frac, places=12)

    def test_missing_yield_stays_none(self):
        """결측은 0이 아니라 None으로 남는다 — 없는 것과 0%는 다르다."""
        div, bench = _fetch(None, None)
        self.assertIsNone(div)
        self.assertIsNone(bench)

    def test_boundary_is_not_crossed_by_plausible_fractions(self):
        """문턱 0.25는 '소수로 있을 법한 값'을 퍼센트로 오인하지 않아야 한다.

        고배당 ETF도 소수 0.25(=25%)를 넘기 어렵다는 것이 `_norm_div_yield`의 전제다.
        경계 자체(0.25)는 소수로 남고, 그 위만 퍼센트로 본다."""
        self.assertAlmostEqual(_fetch(0.25, 0.25)[0], 0.25, places=12)
        self.assertAlmostEqual(_fetch(0.2501, 0.2501)[0], 0.002501, places=12)


if __name__ == "__main__":
    unittest.main()
