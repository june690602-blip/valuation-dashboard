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


class KrPeerMarketCapTests(unittest.TestCase):
    """한국 피어 표의 시총은 KRX가 이긴다.

    yfinance의 한국 시총은 틀릴 때가 있다. 무작위 200종목 실측에서 45%가 2% 넘게,
    10%가 10% 넘게 어긋났고 **최소형 구간의 6%는 2배 넘게** 틀렸다(양방향 —
    참엔지니어링은 yfinance가 5.0배 작고, 남성은 10.8배 크다). 판정은 이미 KRX
    Marcap을 쓰므로(`d.market_cap`), 피어 표만 yfinance면 규모 창 비교가 원천을
    가로지른다.
    """

    def _listing(self):
        return pd.DataFrame(
            {"Name": ["참엔지니어링", "피어A"], "Market": ["KOSPI", "KOSPI"],
             "Marcap": [23_329_484_620.0, 5_000_000_000.0]},
            index=["009310", "111111"])

    def test_krx_marcap_overrides_a_wrong_yfinance_value(self):
        from src.data.kr_provider import KRProvider

        peers = pd.DataFrame(
            {"market_cap": [4_640_954_880.0, 5_000_000_000.0]},   # 자사 값이 5배 작다
            index=["009310.KS", "111111.KS"])
        with patch("src.data.kr_provider.fetch_naver_fundamental",
                   side_effect=RuntimeError("naver down")):
            out = KRProvider._patch_kr_peers(peers, self._listing())
        self.assertEqual(out.at["009310.KS", "market_cap"], 23_329_484_620.0)

    def test_missing_krx_row_keeps_the_yfinance_value(self):
        # 상장목록에 없으면(신규 상장·데이터 지연) yfinance 값이라도 남긴다 —
        # 지우면 규모 창에서 그 피어가 통째로 사라진다.
        from src.data.kr_provider import KRProvider

        peers = pd.DataFrame({"market_cap": [7.0]}, index=["999999.KS"])
        with patch("src.data.kr_provider.fetch_naver_fundamental",
                   side_effect=RuntimeError("naver down")):
            out = KRProvider._patch_kr_peers(peers, self._listing())
        self.assertEqual(out.at["999999.KS", "market_cap"], 7.0)


class AiClassifyFallbackTests(unittest.TestCase):
    """AI 업종분류가 실패하면 조용히 넘어가지 않고 사유를 돌려준다.

    실측 25종목이 전부 KRX 폴백을 탔는데(Gemini 무료 할당량 초과) 화면에는 아무
    경고도 뜨지 않았다. 피어 구성이 통째로 달라지는 일이라 사용자가 알아야 한다.

    **예외 원문은 절대 싣지 않는다** — `gemini.py`가 URL 질의 파라미터로 API 키를
    넘기므로 requests 예외 문자열에 키가 섞여 나올 수 있다.
    """

    def _listing(self):
        return pd.DataFrame({"Name": ["피어A"]}, index=["111111"])

    def _run_kr(self, available, boom):
        from src.data import kr_provider

        with patch("src.data.gemini.is_available", lambda: available), \
             patch("src.analysis.ai_analysis.classify_peers", side_effect=boom):
            return kr_provider._ai_classify_kr("참엔지니어링", "반도체", self._listing())

    def test_quota_exceeded_is_reported(self):
        boom = RuntimeError("429 RESOURCE_EXHAUSTED quota key=AIzaSyTOPSECRET")
        *_, err = self._run_kr(True, boom)
        self.assertIsNotNone(err)
        self.assertIn("할당량", err)
        self.assertNotIn("AIzaSyTOPSECRET", err)   # 키가 새면 안 된다
        self.assertNotIn("key=", err)

    def test_network_error_is_reported_without_raw_text(self):
        boom = TimeoutError("connection timed out to https://x/models?key=AIzaSySECRET")
        *_, err = self._run_kr(True, boom)
        self.assertIn("네트워크", err)
        self.assertNotIn("AIzaSySECRET", err)

    def test_no_api_key_is_not_an_error(self):
        # 키가 없는 것은 정상 동작이다(CLAUDE.md: 키 없어도 앱이 돌아야 한다).
        # 경고를 띄우면 키를 안 넣은 사람에게 매번 소음이 된다.
        *_, err = self._run_kr(False, RuntimeError("안 불려야 함"))
        self.assertIsNone(err)

    def test_us_path_reports_the_same_way(self):
        from src.data import us_provider

        with patch("src.data.gemini.is_available", lambda: True), \
             patch("src.analysis.ai_analysis.classify_peers",
                   side_effect=RuntimeError("429 quota")):
            *_, err = us_provider._ai_classify_us("Apple", "Tech")
        self.assertIn("할당량", err)


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
