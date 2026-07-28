"""미국 ETF 판별 — 큐레이션 목록 밖의 ETF도 인식해야 한다.

목록(US_ETFS)은 71개뿐이라 그것만 믿으면 QLD·SSO 같은 ETF가 '기업'으로 취급돼
"연간 손익계산서를 가져오지 못했습니다" 오류로 빠진다(사이트에서 볼 방법이 없어짐).
목록에 없으면 yfinance가 알려주는 quoteType으로 확인한다. 네트워크는 모킹한다.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.data.us_provider import _us_etf_name


def _unlisted_symbol() -> str:
    """큐레이션 목록에 없는 ETF 심볼 — '목록 밖' 경로를 검증하려면 반드시 목록 밖이어야 한다.

    나중에 이 심볼이 목록에 추가되면 여기서 걸려 테스트가 조용히 무의미해지는 걸 막는다."""
    from src.data.universe import US_ETFS
    listed = {s for s, _name in US_ETFS}
    for candidate in ("TECL", "FNGU", "XYLD", "SPXL", "TMF"):
        if candidate not in listed:
            return candidate
    raise AssertionError("후보가 전부 목록에 들어갔다 — 목록 밖 심볼을 새로 고를 것")


class UsEtfDetectionTests(unittest.TestCase):
    def test_curated_symbol_resolves_without_network(self):
        """목록에 있는 심볼(QQQ)은 네트워크 조회 없이 즉시 판별한다."""
        with patch("src.data.us_provider._yf_fund_info") as fake:
            self.assertIsNotNone(_us_etf_name("QQQ"))
            fake.assert_not_called()

    def test_unlisted_etf_detected_by_quote_type(self):
        """목록 밖이어도 quoteType이 ETF면 ETF로 본다.

        미국 상장 ETF는 수천 개인데 목록은 70여 개뿐이라, 대부분이 이 경로로 판별된다."""
        with patch("src.data.us_provider._yf_fund_info",
                   return_value=("ETF", "Direxion Daily Technology Bull 3X")):
            self.assertEqual(_us_etf_name(_unlisted_symbol()),
                             "Direxion Daily Technology Bull 3X")

    def test_mutual_fund_is_also_not_a_company(self):
        """뮤추얼펀드도 기업 재무가 없어 같은 경로로 보내야 한다."""
        with patch("src.data.us_provider._yf_fund_info",
                   return_value=("MUTUALFUND", "Vanguard 500 Index Fund")):
            self.assertIsNotNone(_us_etf_name("VFIAX"))

    def test_equity_is_not_treated_as_etf(self):
        """개별 기업(AAPL)은 ETF가 아니다 — 기업 분석 경로로 가야 한다."""
        with patch("src.data.us_provider._yf_fund_info",
                   return_value=("EQUITY", "Apple Inc.")):
            self.assertIsNone(_us_etf_name("AAPL"))

    def test_name_falls_back_to_symbol(self):
        """이름을 못 받아도 ETF라는 사실이 확인되면 심볼로라도 알려준다."""
        sym = _unlisted_symbol()
        with patch("src.data.us_provider._yf_fund_info", return_value=("ETF", None)):
            self.assertEqual(_us_etf_name(sym.lower()), sym)

    def test_lookup_failure_is_not_an_etf(self):
        """조회 실패(네트워크 오류 등)는 ETF로 단정하지 않는다 — 크래시도 없어야 한다."""
        with patch("src.data.us_provider._yf_fund_info", return_value=(None, None)):
            self.assertIsNone(_us_etf_name("ZZZZ"))


class CuratedListTests(unittest.TestCase):
    """자동완성 목록 — 레버리지 시리즈가 짝이 맞아야 한다(3배만 있고 2배가 없으면 혼란)."""

    def test_leveraged_pairs_are_listed(self):
        from src.data.universe import US_ETFS
        symbols = {s for s, _name in US_ETFS}
        for s in ("QQQ", "TQQQ", "QLD", "SPY", "UPRO", "SSO"):
            self.assertIn(s, symbols, f"{s}가 자동완성 목록에 없음")


if __name__ == "__main__":
    unittest.main()
