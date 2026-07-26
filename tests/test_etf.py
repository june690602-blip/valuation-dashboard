"""ETF 적정가 분석(compute_etf) 단위 테스트 — 네트워크 없이 합성 ETFData로 검증."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.analysis.etf import compute_etf
from src.data.models import ETFData


def _price_series(n=1500, start="2020-01-01", start_price=100.0, growth=0.00025):
    """완만하게 우상향하는 합성 일별 종가(달력일 기준, 결측 없음)."""
    idx = pd.date_range(start=start, periods=n, freq="D")
    values = start_price * (1.0 + growth) ** np.arange(n)
    return pd.Series(values, index=idx, dtype=float)


def _dividends_on(idx: pd.DatetimeIndex, amount=0.9, start_offset=20, step_days=91) -> pd.Series:
    """idx 위의 정기 배당 이력(정수 오프셋으로 생성해 항상 idx의 원소가 되게 한다)."""
    dates, i = [], start_offset
    while i < len(idx):
        dates.append(idx[i])
        i += step_days
    return pd.Series([amount] * len(dates), index=pd.DatetimeIndex(dates), dtype=float)


def _etf_data(**overrides) -> ETFData:
    """공통 필드를 채운 ETFData. 개별 테스트는 필요한 필드만 덮어쓴다."""
    prices = overrides.pop("prices", None)
    if prices is None:
        prices = _price_series()
    base = dict(
        ticker="TEST", yahoo_ticker="TEST", name="Test ETF", market="US", currency="USD",
        price=float(prices.iloc[-1]) if len(prices) else 100.0,
        prices=prices, dividends=pd.Series(dtype=float), index_prices=prices,
    )
    base.update(overrides)
    return ETFData(**base)


class DividendEquityTests(unittest.TestCase):
    """배당형 주식 ETF(SCHD류) — 배당수익률 밴드가 주 신호가 돼야 한다."""

    def test_dividend_driven_verdict(self):
        px = _price_series()
        divs = _dividends_on(px.index, amount=0.9)
        d = _etf_data(
            name="Test Dividend Equity ETF", category="Large Value",
            prices=px, dividends=divs, index_prices=px,
            basket_pe=15.0, basket_pb=3.0, bench_pe=22.0, bench_label="미국 전체시장(VTI)",
        )
        r = compute_etf(d)
        self.assertEqual(r.fund_type, "equity")
        self.assertEqual(r.primary, "dividend")
        self.assertIsNotNone(r.verdict)
        self.assertIsNotNone(r.div_gap)


class BondTypeTests(unittest.TestCase):
    """채권형 ETF(TLT류) — 이익 기반 배수(PER)는 마스킹, 분배수익률 안내가 붙어야 한다."""

    def test_bond_masks_per_and_notes_distribution_yield(self):
        px = _price_series()
        divs = _dividends_on(px.index, amount=0.2, step_days=30)  # 월 분배 근사
        d = _etf_data(
            name="Test Long Government Bond ETF", category="Long Government",
            prices=px, dividends=divs, index_prices=px,
            basket_pe=None, basket_pb=None,
        )
        r = compute_etf(d)
        self.assertEqual(r.fund_type, "bond")
        masked_labels = [label for label, _reason in r.masked]
        self.assertIn("바스켓 PER", masked_labels)
        self.assertTrue(any("분배수익률" in note for note in r.notes),
                        f"분배수익률 안내 노트가 없음: {r.notes}")


class GrowthTypeTests(unittest.TestCase):
    """성장형 ETF(QQQ류) — 배당이 미미해 적정가 판정을 보류(verdict=None)해야 한다."""

    def test_growth_verdict_withheld(self):
        px = _price_series()
        divs = _dividends_on(px.index, amount=0.02)  # 미미한 배당
        d = _etf_data(
            name="Test Large Growth ETF", category="Large Growth",
            prices=px, dividends=divs, index_prices=px,
            basket_pe=30.0, basket_pb=8.0, bench_pe=22.0, bench_label="미국 전체시장(VTI)",
        )
        r = compute_etf(d)
        self.assertEqual(r.fund_type, "growth_equity")
        self.assertIsNone(r.verdict)
        self.assertEqual(r.primary, "relative")


class PremiumAxisTests(unittest.TestCase):
    """NAV 결측 시 괴리(① 실시간) 축이 available=False로 표시돼야 한다."""

    def test_missing_nav_marks_premium_unavailable(self):
        px = _price_series()
        d = _etf_data(
            name="Test No-NAV ETF", category="Large Blend",
            prices=px, index_prices=px, nav=None, basket_pe=18.0,
        )
        r = compute_etf(d)
        self.assertIsNone(r.premium)
        premium_axis = next(a for a in r.axes if a.key == "premium")
        self.assertFalse(premium_axis.available)


class TrackingErrorTests(unittest.TestCase):
    """추적오차(tracking_error) — ETF와 벤치마크 일간수익률 차이의 연율화 표준편차."""

    def test_identical_series_near_zero_tracking_error(self):
        """ETF와 벤치마크 가격이 완전히 같으면(액티브 리턴=0) 추적오차는 0에 가까워야 한다."""
        px = _price_series()
        d = _etf_data(prices=px, index_prices=px, benchmark_name="VTI")
        r = compute_etf(d)
        self.assertIsNotNone(r.tracking_error)
        self.assertLess(r.tracking_error, 1e-6)

    def test_divergent_series_positive_tracking_error(self):
        """벤치마크 대비 일간 변동이 다르면(잡음 추가) 추적오차가 유의미하게 커야 한다."""
        bench = _price_series()
        rng = np.random.default_rng(7)
        noise = np.cumprod(1.0 + rng.normal(0, 0.01, size=len(bench)))
        etf_px = bench * pd.Series(noise, index=bench.index)
        d = _etf_data(prices=etf_px, index_prices=bench, benchmark_name="VTI")
        r = compute_etf(d)
        self.assertIsNotNone(r.tracking_error)
        self.assertGreater(r.tracking_error, 0.001)

    def test_empty_benchmark_name_no_tracking_error(self):
        """벤치마크가 자기 자신으로 폴백된 경우(benchmark_name 없음)는 추적오차가 의미 없어 None."""
        px = _price_series()
        d = _etf_data(prices=px, index_prices=px)  # benchmark_name 기본값 ""
        r = compute_etf(d)
        self.assertIsNone(r.tracking_error)


class EmptyDataRobustnessTests(unittest.TestCase):
    """무료 데이터 결측(빈·짧은 시세·배당)에서도 예외 없이 N/A로 처리돼야 한다."""

    def test_fully_empty_series_no_exception(self):
        empty = pd.Series(dtype=float)
        d = _etf_data(name="Test Empty ETF", prices=empty, dividends=empty, index_prices=empty)
        r = compute_etf(d)  # 예외가 나면 unittest가 이 테스트를 실패시킨다
        self.assertIsNone(r.verdict)
        self.assertIsNone(r.premium)

    def test_short_series_no_exception(self):
        px = _price_series(n=10, start_price=50.0)
        divs = pd.Series([0.1], index=[px.index[3]])
        d = _etf_data(name="Test Short History ETF", prices=px, dividends=divs, index_prices=px)
        r = compute_etf(d)
        # 배당·추세 밴드 모두 표본 부족(250일/60일 미만)으로 계산되지 않고 N/A여야 한다.
        self.assertIsNone(r.div_pct)
        self.assertIsNone(r.w52_pos)


if __name__ == "__main__":
    unittest.main()
