from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.web.serialize import _price


class PriceSerializationTests(unittest.TestCase):
    @staticmethod
    def company(frame_price: float = 999.0, benchmark=None,
                prices=None, prices_raw=None):
        ns = SimpleNamespace(
            yahoo_ticker="TEST",
            price=frame_price,
            index_prices=benchmark,
            market="KR",
            currency="KRW",
        )
        # 시세 계열이 없는 더블은 52주 계산이 차트 종가로 폴백해야 한다(기존 동작 유지).
        if prices is not None:
            ns.prices = prices
            ns.prices_raw = prices_raw
        return ns

    def test_serializes_full_five_year_contract_and_summary_metrics(self):
        dates = pd.bdate_range("2024-01-02", periods=300)
        close = np.arange(100.0, 400.0)
        frame = pd.DataFrame(
            {
                "Open": close - 1.0,
                "High": close + 2.0,
                "Low": close - 3.0,
                "Close": close,
                "Volume": np.arange(1_000.0, 1_300.0),
            },
            index=dates,
        )
        benchmark = pd.Series(
            [1_000.0, 1_002.0, 1_100.0],
            index=[dates[0] - pd.offsets.BDay(1), dates[1], dates[-1]],
        )

        with patch("src.data.base.fetch_ohlcv", return_value=frame) as fetch:
            payload = _price(self.company(benchmark=benchmark))

        fetch.assert_called_once_with("TEST", period="5y")
        self.assertEqual(len(payload["dates"]), 300)  # 기존 1년(252일) 절단 방지
        self.assertEqual(payload["dates"][0], dates[0].strftime("%Y-%m-%d"))
        self.assertEqual(payload["dates"][-1], dates[-1].strftime("%Y-%m-%d"))

        parallel_keys = (
            "open", "high", "low", "close", "vol", "ma20", "ma60",
            "ma120", "bench",
        )
        for key in parallel_keys:
            with self.subTest(key=key):
                self.assertEqual(len(payload[key]), len(payload["dates"]))

        self.assertEqual(payload["open"][0], 99.0)
        self.assertEqual(payload["high"][-1], 401.0)
        self.assertEqual(payload["low"][-1], 396.0)
        self.assertEqual(payload["close"][-1], 399.0)
        self.assertEqual(payload["vol"][0], 1_000.0)
        self.assertIsNone(payload["ma20"][18])
        self.assertAlmostEqual(payload["ma20"][19], 109.5)
        self.assertEqual(payload["bench"][0], 1_000.0)  # 이전 벤치 거래일 전방 채움
        self.assertEqual(payload["bench"][1], 1_002.0)

        self.assertEqual(payload["cur"], 399.0)
        self.assertEqual(payload["prev_close"], 398.0)
        self.assertEqual(payload["change"], 1.0)
        self.assertAlmostEqual(payload["change_pct"], 1.0 / 398.0)
        self.assertEqual(payload["hi52"], 399.0)
        self.assertEqual(payload["lo52"], 148.0)
        self.assertAlmostEqual(payload["ret1y"], 399.0 / 148.0 - 1.0)
        self.assertEqual(payload["pos52"], 100.0)
        self.assertEqual(payload["asof"], dates[-1].strftime("%Y-%m-%d"))
        self.assertIn("Yahoo Finance", payload["source"])
        self.assertIn("실시간이 아니", payload["delay_note"])

        # API json.dumps(..., allow_nan=False)에서도 안전해야 한다.
        json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_short_history_and_missing_ohlc_columns_are_null_safe(self):
        dates = pd.bdate_range("2026-07-10", periods=2)
        frame = pd.DataFrame(
            {"close": [100.0, 110.0], "VOLUME": [np.inf, 5.0]},
            index=dates,
        )

        with patch("src.data.base.fetch_ohlcv", return_value=frame):
            payload = _price(self.company(frame_price=777.0, benchmark=None))

        self.assertEqual(payload["dates"], [date.strftime("%Y-%m-%d") for date in dates])
        self.assertEqual(payload["open"], [None, None])
        self.assertEqual(payload["high"], [None, None])
        self.assertEqual(payload["low"], [None, None])
        self.assertEqual(payload["close"], [100.0, 110.0])
        self.assertEqual(payload["vol"], [None, 5.0])
        self.assertEqual(payload["bench"], [None, None])
        self.assertEqual(payload["cur"], 110.0)
        self.assertEqual(payload["prev_close"], 100.0)
        self.assertEqual(payload["change"], 10.0)
        self.assertEqual(payload["change_pct"], 0.1)
        self.assertEqual(payload["hi52"], 110.0)
        self.assertEqual(payload["lo52"], 100.0)
        self.assertIsNone(payload["ret1y"])
        self.assertEqual(payload["pos52"], 100.0)
        self.assertTrue(all(value is None for value in payload["ma20"]))
        json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_52week_band_uses_unadjusted_prices_while_return_uses_adjusted(self):
        """52주 밴드는 실거래가, 1년 수익률은 수정종가 — 기준이 서로 다르다.

        수정종가는 과거를 배당만큼 낮춰 잡아 저점이 실제보다 낮게 찍힌다. 그러면 현재가가
        밴드 위쪽에 있는 것처럼 보인다(실측 저점 괴리 KB금융 -3.13%). 반대로 1년 수익률은
        배당까지 받은 총수익이라야 실제로 번 돈에 맞는다.
        """
        dates = pd.bdate_range("2025-08-01", periods=260)
        adjusted = np.linspace(80.0, 200.0, 260)      # 배당만큼 과거가 눌린 계열
        raw = np.linspace(100.0, 200.0, 260)          # 그날 실제로 붙어 있던 가격
        frame = pd.DataFrame({"Close": adjusted}, index=dates)

        with patch("src.data.base.fetch_ohlcv", return_value=frame):
            payload = _price(self.company(
                benchmark=None,
                prices=pd.Series(adjusted, index=dates),
                prices_raw=pd.Series(raw, index=dates)))

        first = 260 - 252   # tail(252)의 첫 위치
        self.assertAlmostEqual(payload["lo52"], raw[first:].min())   # 수정종가 저점이 아니다
        self.assertAlmostEqual(payload["hi52"], raw[first:].max())
        self.assertNotAlmostEqual(payload["lo52"], adjusted[first:].min())
        self.assertAlmostEqual(payload["pos52"], 100.0)
        # 1년 수익률만은 수정종가 계열 그대로다
        self.assertAlmostEqual(payload["ret1y"], adjusted[-1] / adjusted[first] - 1.0)
        json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_52week_band_falls_back_to_chart_close_without_unadjusted(self):
        """미조정 시세를 못 받으면 차트 종가로 폴백한다 — 정확도만 떨어지고 동작은 유지."""
        dates = pd.bdate_range("2025-08-01", periods=260)
        adjusted = np.linspace(80.0, 200.0, 260)
        frame = pd.DataFrame({"Close": adjusted}, index=dates)

        with patch("src.data.base.fetch_ohlcv", return_value=frame):
            payload = _price(self.company(
                benchmark=None,
                prices=pd.Series(adjusted, index=dates),
                prices_raw=None))

        self.assertAlmostEqual(payload["lo52"], adjusted[260 - 252:].min())

    def test_missing_close_preserves_parallel_rows_and_uses_price_fallback(self):
        dates = pd.bdate_range("2026-07-10", periods=2)
        frame = pd.DataFrame({"Open": [70.0, 71.0]}, index=dates)

        with patch("src.data.base.fetch_ohlcv", return_value=frame):
            payload = _price(self.company(frame_price=77.5, benchmark=pd.Series(dtype=float)))

        self.assertEqual(payload["open"], [70.0, 71.0])
        self.assertEqual(payload["close"], [None, None])
        self.assertEqual(payload["cur"], 77.5)
        for key in ("prev_close", "change", "change_pct", "hi52", "lo52",
                    "ret1y", "pos52", "asof"):
            with self.subTest(key=key):
                self.assertIsNone(payload[key])
        for key in ("high", "low", "close", "vol", "ma20", "ma60", "ma120", "bench"):
            with self.subTest(key=key):
                self.assertEqual(payload[key], [None, None])
        json.dumps(payload, ensure_ascii=False, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
