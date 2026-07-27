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


class PriceBasisTests(unittest.TestCase):
    """가격 기준 분리 — 밴드는 미조정(실제 거래가), 성과는 수정종가(총수익).

    수정종가는 과거를 그 뒤 지급된 배당만큼 낮춰 잡는다. 그 값으로 배당수익률 밴드를
    만들면 과거 수익률이 부풀려져 현재가 늘 '고평가' 쪽으로 밀린다(배당 많은 ETF일수록 심함).
    """

    @staticmethod
    def _adjusted(raw: pd.Series, annual_yield: float) -> pd.Series:
        """raw를 배당조정한 시리즈 — 과거일수록 더 많이 낮춘다(yfinance auto_adjust와 같은 방향)."""
        n = len(raw)
        years_back = (n - 1 - np.arange(n)) / 252.0
        return raw * np.exp(-annual_yield * years_back)

    def test_dividend_band_uses_raw_prices_when_available(self):
        """미조정 시세가 있으면 배당밴드가 그걸 쓴다 — 수정종가로 계산한 값과 달라야 한다."""
        raw = _price_series()
        divs = _dividends_on(raw.index, amount=0.9)
        adj = self._adjusted(raw, 0.036)
        kw = dict(name="Test Bond-ish ETF", category="Long Government",
                  dividends=divs, index_prices=adj)
        with_raw = compute_etf(_etf_data(prices=adj, prices_raw=raw, **kw))
        without = compute_etf(_etf_data(prices=adj, **kw))
        # 조정가는 과거 수익률을 부풀려 중앙값을 높이므로, 미조정 기준 gap이 더 커야(=덜 비싸 보여야) 한다.
        self.assertGreater(with_raw.div_gap, without.div_gap)
        self.assertLess(with_raw.div_median, without.div_median)

    def test_52w_band_uses_raw_prices(self):
        """52주 밴드도 실제 거래가 기준 — 조정가는 저점을 실제보다 낮게 찍는다."""
        raw = _price_series().copy()
        # 최근 1년에 V자(하락 후 부분 회복)를 준다 — 저점이 '과거'에 있어야 조정 배율 차이가
        # 드러나고, 현재가가 고점에 붙어 있지 않아야 밴드 내 위치 비교가 성립한다.
        shape = np.interp(np.arange(252), [0, 90, 251], [1.0, 0.80, 0.88])
        raw.iloc[-252:] = raw.iloc[-252:] * shape
        adj = self._adjusted(raw, 0.05)
        kw = dict(name="Test ETF", category="Large Blend", index_prices=adj)
        with_raw = compute_etf(_etf_data(prices=adj, prices_raw=raw, **kw))
        without = compute_etf(_etf_data(prices=adj, **kw))
        self.assertGreater(with_raw.w52_low, without.w52_low)   # 저점이 덜 눌림
        self.assertGreater(without.w52_pos, with_raw.w52_pos)   # 조정가는 위치를 위로 밀어 올림

    def test_excess_return_stays_on_total_return_basis(self):
        """초과성과는 총수익 비교라 미조정 시세를 섞으면 안 된다(수정종가끼리 계산)."""
        raw = _price_series()
        adj = self._adjusted(raw, 0.04)
        d = _etf_data(name="Test ETF", prices=adj, prices_raw=raw,
                      index_prices=adj, benchmark_name="VTI")
        r = compute_etf(d)
        # ETF와 벤치마크가 같은 시리즈 → 총수익 기준으로는 초과성과가 정확히 0.
        # 미조정 가격이 섞여 들어가면 배당수익률만큼 0에서 벗어난다.
        self.assertIsNotNone(r.rel_1y)
        self.assertAlmostEqual(r.rel_1y, 0.0, places=9)

    def test_missing_raw_prices_falls_back_without_crashing(self):
        """미조정 시세를 못 받아도(=None) 수정종가로 계산해 동작은 유지한다."""
        px = _price_series()
        divs = _dividends_on(px.index, amount=0.9)
        r = compute_etf(_etf_data(name="Test ETF", category="Large Value",
                                  prices=px, prices_raw=None, dividends=divs, index_prices=px))
        self.assertIsNotNone(r.div_gap)
        self.assertIsNotNone(r.w52_pos)

    def test_empty_raw_prices_falls_back(self):
        """빈 시리즈가 들어와도 폴백해야 한다(길이 0을 그대로 쓰면 밴드가 통째로 사라진다)."""
        px = _price_series()
        divs = _dividends_on(px.index, amount=0.9)
        r = compute_etf(_etf_data(name="Test ETF", category="Large Value", prices=px,
                                  prices_raw=pd.Series(dtype=float), dividends=divs,
                                  index_prices=px))
        self.assertIsNotNone(r.div_gap)
        self.assertIsNotNone(r.w52_pos)


class KoreanETFTests(unittest.TestCase):
    """한국 ETF 전용 — 운용사 공시값(괴리율·추적오차)이 우리 추정치보다 우선해야 한다."""

    def test_published_deviation_beats_stale_nav(self):
        """공시 괴리율이 있으면 NAV 계산값을 무시해야 한다.

        네이버 etfAnalysis의 NAV는 하루 뒤처져 현재가와 짝지으면 괴리가 -6%로 왜곡된다
        (실측: KODEX 200). 공시 괴리율 -0.13%가 그대로 쓰여야 한다.
        """
        px = _price_series()
        d = _etf_data(
            name="KODEX 200", market="KR", currency="KRW", category="Korea Equity",
            prices=px, index_prices=px, price=106365.0, nav=113375.0,   # 시점 어긋난 NAV
            deviation_rate=-0.0013,
        )
        r = compute_etf(d)
        self.assertAlmostEqual(r.premium, -0.0013, places=6)   # NAV로 계산했다면 -0.06
        self.assertEqual(r.type_label, "국내 주식형")

    def test_published_tracking_error_beats_estimate(self):
        """공시 추적오차율이 있으면 벤치마크 대용 추정을 건너뛴다."""
        bench = _price_series()
        rng = np.random.default_rng(11)
        noise = np.cumprod(1.0 + rng.normal(0, 0.02, size=len(bench)))
        etf_px = bench * pd.Series(noise, index=bench.index)     # 추정하면 수% 나올 시세
        d = _etf_data(name="KODEX 200", market="KR", currency="KRW",
                      prices=etf_px, index_prices=bench, benchmark_name="KOSPI",
                      tracking_error_pub=0.0039)
        r = compute_etf(d)
        self.assertAlmostEqual(r.tracking_error, 0.0039, places=6)

    def test_basket_note_travels_with_pe(self):
        """바스켓 PER는 상위 보유종목 기준 추정치라, 산출 근거가 축 설명에 함께 나가야 한다."""
        px = _price_series()
        note = "상위 10종목(비중 합 72.2%) 가중평균 — 펀드 전체가 아닌 상위 보유종목 기준 추정치"
        d = _etf_data(name="KODEX 200", market="KR", currency="KRW", category="Korea Equity",
                      prices=px, index_prices=px, basket_pe=21.1, basket_note=note)
        r = compute_etf(d)
        rel = next(a for a in r.axes if a.key == "relative")
        self.assertIn("상위 10종목", rel.note)

    def test_foreign_equity_without_basket_marks_axis_unavailable(self):
        """해외형은 구성종목이 해외 티커라 바스켓 PER를 못 만든다 — 축을 숨기지 말고 사유를 남긴다."""
        px = _price_series()
        divs = _dividends_on(px.index, amount=0.2)
        d = _etf_data(name="TIGER 미국S&P500", market="KR", currency="KRW",
                      category="Foreign Equity", prices=px, dividends=divs, index_prices=px,
                      basket_pe=None, bench_pe=None)
        r = compute_etf(d)
        self.assertEqual(r.fund_type, "foreign_equity")
        rel = next(a for a in r.axes if a.key == "relative")
        self.assertFalse(rel.available)
        self.assertEqual(rel.value, "N/A")


class KoreanCategoryTests(unittest.TestCase):
    """이름 기반 유형 추정(_infer_category) — 네트워크 없이 문자열만 검증."""

    def test_finance_and_bank_are_not_commodity(self):
        """'금융'·'은행'이 원자재로 새지 않아야 한다(한 글자 '금'·'은' 매칭 금지)."""
        from src.data.kr_etf_provider import _infer_category
        eq, kr = {"EQUITY": 0.99}, {"KR": 0.99}
        self.assertEqual(_infer_category("KODEX 은행", eq, kr), "Korea Equity")
        self.assertEqual(_infer_category("TIGER 금융", eq, kr), "Korea Equity")
        # 금광 '주식'을 담는 ETF도 원자재가 아니다.
        self.assertEqual(_infer_category("HANARO 글로벌금채굴기업",
                                         eq, {"KR": 0.02}), "Foreign Equity")

    def test_real_commodity_names_detected(self):
        from src.data.kr_etf_provider import _infer_category
        for nm in ("KODEX 골드선물(H)", "ACE KRX금현물", "KODEX WTI원유선물(H)"):
            self.assertEqual(_infer_category(nm, {}, {}), "Commodity", nm)

    def test_name_fallback_when_country_weights_missing(self):
        """상세 조회 실패로 국가 비중이 없을 때는 이름으로 국내/해외를 가른다."""
        from src.data.kr_etf_provider import _infer_category
        self.assertEqual(_infer_category("TIGER 미국S&P500", {}, {}), "Foreign Equity")
        self.assertEqual(_infer_category("KODEX 200", {}, {}), "Korea Equity")

    def test_bond_weight_wins_over_name(self):
        """채권 비중이 과반이면 이름과 무관하게 채권형."""
        from src.data.kr_etf_provider import _infer_category
        self.assertEqual(_infer_category("KODEX 미국채10년선물",
                                         {"BOND": 0.96}, {"US": 0.96}), "Bond")


if __name__ == "__main__":
    unittest.main()
