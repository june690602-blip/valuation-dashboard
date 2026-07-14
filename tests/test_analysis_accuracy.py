from __future__ import annotations

import unittest
from types import SimpleNamespace

import pandas as pd

from src.analysis.backtest import _non_overlapping_values
from src.analysis.indicators import _average_balance
from src.analysis.portfolio import after_tax_row
from src.analysis.valuation import _fundamental_daily
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


if __name__ == "__main__":
    unittest.main()
