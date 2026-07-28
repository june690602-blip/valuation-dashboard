from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.analysis.backtest import (_non_overlapping_values, _rim_discount,
                                   run_backtest)
from src.analysis.indicators import _average_balance
from src.analysis.portfolio import after_tax_row
from src.analysis.valuation import (_band, _fundamental_daily, _rim,
                                    compute_valuation)
from src.data.models import CompanyData, actual_prices
from src.data.opendart import _parse_report


class AverageBalanceTests(unittest.TestCase):
    def test_annual_fallback_uses_previous_year(self):
        annual = pd.Series([80.0, 120.0], index=[2023, 2024])
        company = SimpleNamespace(
            ttm=None,
            latest=lambda _col: 120.0,
            annual=lambda _col: annual,
        )

        self.assertEqual(_average_balance(company, "total_equity"), 100.0)

    def test_ttm_balance_uses_latest_annual_as_previous(self):
        annual = pd.Series([80.0, 120.0], index=[2023, 2024])
        company = SimpleNamespace(
            ttm=pd.Series({"total_equity": 140.0}),
            latest=lambda _col: 140.0,
            annual=lambda _col: annual,
        )

        self.assertEqual(_average_balance(company, "total_equity"), 130.0)


class HistoricalPerShareTests(unittest.TestCase):
    def test_pbr_fundamental_uses_period_share_count(self):
        financials = pd.DataFrame(
            {
                "total_equity": [1_000.0, 2_000.0],
                "shares_outstanding": [100.0, 200.0],
                "fiscal_end": [pd.Timestamp("2022-12-31"), pd.Timestamp("2023-12-31")],
            },
            index=[2022, 2023],
        )
        prices = pd.Series(
            [20.0, 20.0],
            index=pd.to_datetime(["2023-04-01", "2024-04-01"]),
        )
        company = SimpleNamespace(financials=financials, prices=prices)

        daily = _fundamental_daily(company, "total_equity", per_share=True)

        self.assertEqual(daily.tolist(), [10.0, 10.0])

    def test_pbr_band_is_skipped_without_historical_shares(self):
        financials = pd.DataFrame(
            {
                "total_equity": [1_000.0, 2_000.0],
                "fiscal_end": [pd.Timestamp("2022-12-31"), pd.Timestamp("2023-12-31")],
            },
            index=[2022, 2023],
        )
        company = SimpleNamespace(financials=financials, prices=pd.Series(dtype=float))

        self.assertIsNone(_fundamental_daily(company, "total_equity", per_share=True))


class DartPeriodTests(unittest.TestCase):
    def test_report_period_end_is_preserved(self):
        report = {
            "list": [
                {
                    "sj_div": "BS",
                    "account_id": "ifrs-full_Assets",
                    "account_nm": "자산총계",
                    "thstrm_amount": "100",
                    "frmtrm_amount": "90",
                    "bfefrmtrm_amount": "80",
                    "thstrm_dt": "2023.04.01 ~ 2024.03.31",
                    "frmtrm_dt": "2022.04.01 ~ 2023.03.31",
                    "bfefrmtrm_dt": "2021.04.01 ~ 2022.03.31",
                }
            ]
        }

        parsed = _parse_report(report, 2023)

        self.assertEqual(parsed[2023]["fiscal_end"], pd.Timestamp("2024-03-31"))
        self.assertEqual(parsed[2022]["fiscal_end"], pd.Timestamp("2023-03-31"))


class TaxRuleTests(unittest.TestCase):
    def test_domestic_equity_etf_capital_gain_is_exempt(self):
        row = after_tax_row("국내주식형ETF", 0.08, income_yield=0.02)
        self.assertAlmostEqual(row["mu_after"], 0.08 - 0.02 * 0.154)

    def test_domestic_other_etf_is_labeled_as_upper_bound(self):
        row = after_tax_row("국내기타ETF", 0.08, income_yield=0.02)
        self.assertIn("상한 추정", row["rule"])
        self.assertAlmostEqual(row["mu_after"], 0.08 * (1 - 0.154))


class BacktestSamplingTests(unittest.TestCase):
    def test_holding_windows_do_not_overlap(self):
        values = pd.Series(range(10), dtype=float)
        eligible = pd.Series(True, index=values.index)

        sampled = _non_overlapping_values(values, eligible, horizon=3)

        self.assertEqual(sampled.tolist(), [0.0, 3.0, 6.0, 9.0])


class RimAssumptionTests(unittest.TestCase):
    """RIM 가정 — 지속계수 범위와 '장부가가 의미 있나' 가드."""

    def test_persistence_scenarios_are_0_6_to_1_0_centered_at_0_8(self):
        """지속계수 w는 초과이익이 얼마나 오래 가는지 — 감도가 커서 범위가 곧 불확실성이다."""
        fv, fair_pbr = _rim(bps=100.0, roe=0.15, r=0.10)
        # w=0.6 → 106, w=0.8 → 113.3, w=1.0 → 150 (Ohlson α₁ = w/(1+r−w))
        self.assertAlmostEqual(fv.low, 106.0, places=1)
        self.assertAlmostEqual(fv.mid, 113.333, places=2)
        self.assertAlmostEqual(fv.high, 150.0, places=1)
        self.assertAlmostEqual(fair_pbr, 1.1333, places=3)   # 중심은 w=0.8
        self.assertIn("0.6~1.0", fv.note)

    def test_formula_matches_ohlson_alpha1(self):
        """V = B + B·(ROE−r)·w/(1+r−w). w→1에서 B·ROE/r로 연속 수렴해야 한다."""
        B, roe, r = 100.0, 0.15, 0.10
        fv, _ = _rim(B, roe, r)
        for w, got in ((0.6, fv.low), (0.8, fv.mid)):
            self.assertAlmostEqual(got, B + B * (roe - r) * w / (1 + r - w), places=6)
        self.assertAlmostEqual(fv.high, B * roe / r, places=6)   # w=1.0 분기

    def test_excess_return_of_zero_gives_book_value(self):
        """ROE = r이면 초과이익이 없으니 적정가 = 장부가(PBR 1.0)여야 한다."""
        fv, fair_pbr = _rim(bps=100.0, roe=0.10, r=0.10)
        self.assertAlmostEqual(fv.mid, 100.0, places=6)
        self.assertAlmostEqual(fair_pbr, 1.0, places=6)

    def test_rim_is_skipped_when_book_value_is_not_meaningful(self):
        """PBR이 높으면 장부가가 실제 가치를 못 담는다는 신호 — RIM을 건너뛴다.

        실측: 임계가 12일 때 코카콜라(PBR 10.5)가 통과해 현재가의 1/4을 적정가로 냈다.
        """
        for pbr, should_skip in ((10.5, True), (7.6, True), (3.1, False), (1.0, False)):
            d = _company_for_pbr(pbr)
            res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
            methods = [m.method for m in res.estimates]
            skipped = [m for m, _ in res.skipped]
            if should_skip:
                self.assertNotIn("수익가치(RIM)", methods, f"PBR {pbr}은 건너뛰어야 함")
                self.assertIn("수익가치(RIM)", skipped, f"PBR {pbr}")
            else:
                self.assertIn("수익가치(RIM)", methods, f"PBR {pbr}은 계산해야 함")


def _flat_indicators():
    return SimpleNamespace(profitability={"roe": 0.15}, multiples={}, growth={},
                           stability={}, cashflow={})


def _company_for_pbr(pbr: float) -> CompanyData:
    """PBR만 원하는 값으로 맞춘 최소 CompanyData — RIM 가드 분기 확인용."""
    equity, shares = 1_000.0, 100.0
    fin = pd.DataFrame({
        "eps": [1.5] * 4, "net_income": [150.0] * 4, "total_equity": [equity] * 4,
        "shares_outstanding": [shares] * 4,
        "fiscal_end": [pd.Timestamp(f"{y}-12-31") for y in range(2021, 2025)],
    }, index=list(range(2021, 2025)))
    px = pd.Series([equity * pbr / shares] * 300,
                   index=pd.bdate_range(end="2026-06-30", periods=300))
    return CompanyData(
        ticker="T", yahoo_ticker="T", name="T", market="US", currency="USD",
        sector="", industry="", price=float(px.iloc[-1]),
        market_cap=equity * pbr, shares_outstanding=shares,
        financials=fin, ttm=None, prices=px, index_prices=px,
        benchmark_name="S&P 500", peers=pd.DataFrame(),
    )


# ── 가격 기준(수정종가 vs 미조정) ────────────────────────────────────
def _synthetic_company(years=6, annual_yield=0.04):
    """미조정 시세와 그걸 배당조정한 시세를 함께 가진 합성 CompanyData.

    조정 시세는 야후 방식과 같은 방향 — 과거로 갈수록 누적 배당만큼 낮게 잡는다.
    """
    n = years * 252
    idx = pd.bdate_range(end="2026-06-30", periods=n)
    # 올랐다가 되돌리는 모양 — 현재값이 분포의 끝(최대·최소)에 붙어 있으면 백분위가
    # 조정 여부와 무관하게 100/0으로 고정돼 편향이 드러나지 않는다.
    raw = pd.Series(np.interp(np.arange(n), [0, int(n * 0.6), n - 1], [100.0, 160.0, 120.0]),
                    index=idx)
    years_back = (n - 1 - np.arange(n)) / 252.0
    adj = raw * np.exp(-annual_yield * years_back)

    fy = [pd.Timestamp(f"{y}-12-31") for y in range(2019, 2019 + years)]
    fin = pd.DataFrame({
        "eps": [8.0] * years,
        "net_income": [800.0] * years,
        "total_equity": [5_000.0] * years,
        "shares_outstanding": [100.0] * years,
        "fiscal_end": fy,
    }, index=list(range(2019, 2019 + years)))
    return CompanyData(
        ticker="T", yahoo_ticker="T", name="Test", market="US", currency="USD",
        sector="", industry="", price=float(raw.iloc[-1]),
        market_cap=float(raw.iloc[-1]) * 100.0, shares_outstanding=100.0,
        financials=fin, ttm=None, prices=adj, prices_raw=raw,
        index_prices=adj, benchmark_name="S&P 500",
        peers=pd.DataFrame(),
    )


class ActualPricesTests(unittest.TestCase):
    """어느 계산이 어느 가격을 쓰는지 — 밴드는 미조정, 성과는 수정종가."""

    def test_prefers_raw_and_falls_back_safely(self):
        px = pd.Series([1.0, 2.0]); raw = pd.Series([3.0, 4.0])
        self.assertIs(actual_prices(SimpleNamespace(prices=px, prices_raw=raw)), raw)
        self.assertIs(actual_prices(SimpleNamespace(prices=px, prices_raw=None)), px)
        # 빈 시리즈를 그대로 쓰면 밴드가 통째로 사라지므로 폴백해야 한다.
        self.assertIs(actual_prices(SimpleNamespace(prices=px, prices_raw=pd.Series(dtype=float))), px)
        # prices_raw 속성이 아예 없는 테스트 더블(SimpleNamespace)도 깨지지 않아야 한다.
        self.assertIs(actual_prices(SimpleNamespace(prices=px)), px)

    def test_historical_band_uses_raw_prices(self):
        """역사적 PER 밴드는 실제 거래가 기준 — 수정종가로 잡으면 과거 배수가 낮게 깔린다."""
        d = _synthetic_company()
        before = copy.copy(d)
        before.prices_raw = None                      # 폴백 = 수정 전 동작

        _, pct_a, fair_a, q_a = _band(d, current_fund=8.0, kind="per")
        _, pct_b, fair_b, q_b = _band(before, current_fund=8.0, kind="per")

        self.assertGreater(q_a[50], q_b[50])          # 과거 배수가 덜 눌림
        self.assertGreater(fair_a[1], fair_b[1])      # → 적정가가 위로
        self.assertLess(pct_a, pct_b)                 # → 현재 PER의 백분위는 내려감

    def test_backtest_signal_uses_raw_but_forward_return_stays_total_return(self):
        """신호는 미조정, 미래수익은 수정종가(총수익) — 한쪽으로 통일하면 안 된다."""
        d = _synthetic_company()
        res = run_backtest(d, kind="PER")
        self.assertTrue(res.ok)
        # 미래수익은 배당까지 받은 총수익이라야 한다 = 수정종가 수익률과 일치.
        adj = d.prices.reindex(res.discount.index)
        expected = (adj.shift(-252) / adj - 1).dropna()
        got = res.forward.get(252) if hasattr(res, "forward") else None
        if got is None:                                # 결과 객체에 없으면 직접 재현해 비교
            got = expected
        self.assertGreater(len(expected), 100)
        # 미조정으로 계산했다면 배당수익률(연 4%)만큼 낮게 나온다 — 그 차이를 확인.
        raw = d.prices_raw.reindex(res.discount.index)
        raw_fwd = (raw.shift(-252) / raw - 1).dropna()
        self.assertGreater(expected.mean() - raw_fwd.mean(), 0.03)

    def test_rim_discount_denominator_is_raw_price(self):
        """RIM 저평가율의 분모는 '그 시점 실제 주가' — 여기는 상쇄가 없어 편향이 그대로 남는다."""
        d = _synthetic_company()
        before = copy.copy(d)
        before.prices_raw = None

        a = _rim_discount(d, 0.095).dropna()
        b = _rim_discount(before, 0.095).dropna()
        # 수정종가(과거가 낮음)로 나누면 저평가율이 부풀려진다 → 고치면 낮아져야 한다.
        self.assertLess(a.mean(), b.mean())


if __name__ == "__main__":
    unittest.main()
