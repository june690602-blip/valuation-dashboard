from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from src.data import universe
from src.data.kr_provider import merge_financials
from src.data.models import FIN_COLUMNS


class FinancialMergeTests(unittest.TestCase):
    def test_dart_values_win_and_derived_fields_are_recomputed(self):
        dart = pd.DataFrame(
            {
                "revenue": [100.0],
                "operating_income": [20.0],
                "da": [5.0],
                "ocf": [30.0],
                "capex": [10.0],
            },
            index=[2024],
        )
        yahoo = pd.DataFrame(
            {"revenue": [90.0], "total_equity": [50.0]},
            index=[2024],
        )

        merged = merge_financials(dart, yahoo)

        self.assertEqual(merged.at[2024, "revenue"], 100.0)
        self.assertEqual(merged.at[2024, "total_equity"], 50.0)
        self.assertEqual(merged.at[2024, "ebitda"], 25.0)
        self.assertEqual(merged.at[2024, "fcf"], 20.0)
        self.assertTrue(set(FIN_COLUMNS).issubset(merged.columns))


class DataSourceErrorTests(unittest.TestCase):
    def test_krx_failure_has_an_actionable_message(self):
        fake_fdr = SimpleNamespace(
            StockListing=lambda _market: (_ for _ in ()).throw(
                UnboundLocalError("cannot access local variable 'r'")
            )
        )
        with patch.dict(sys.modules, {"FinanceDataReader": fake_fdr}):
            with self.assertRaisesRegex(RuntimeError, "KRX 종목 목록") as caught:
                universe.get_kr_listing.__wrapped__()

        self.assertIsInstance(caught.exception.__cause__, UnboundLocalError)

    def test_sp500_failure_has_an_actionable_message(self):
        with patch("requests.get", side_effect=ConnectionError("offline")):
            with self.assertRaisesRegex(RuntimeError, "S&P 500 종목 목록") as caught:
                universe.get_sp500.__wrapped__()

        self.assertIsInstance(caught.exception.__cause__, ConnectionError)


if __name__ == "__main__":
    unittest.main()
