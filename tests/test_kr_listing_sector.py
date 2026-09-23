"""업종분류 원천이 끊겼을 때 KR 상장목록이 버티는가 (2026-09-18 장애).

fdr의 `KRX-DESC`는 **그날 날짜의 CSV 한 장**을 본다. 캐시 저장소가 2026-09-17을 끝으로
desc 발행을 멈추자 404가 났고, 옛 코드는 그 실패를 `except Exception: pass`로 삼킨 뒤
`Sector`를 NaN으로 채웠다. 업종이 빈 목록은 정상처럼 흘러가 24시간 캐시에 앉았고,
**KR 회귀 계수가 6일간 0행**이었다(워크플로 빨간불로만 드러났다).

검사하는 것은 셋이다 — 물러나 받는가 · 못 받으면 말하는가 · 나쁜 목록을 캐시에 남기지
않는가. **네트워크는 타지 않는다**(CI가 원천 장애로 빨간불이 되면 아무도 안 믿는다).
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

from src.data import universe as U


def _desc_frame():
    return pd.DataFrame({"Code": ["005930", "000660"],
                         "Name": ["삼성전자", "SK하이닉스"],
                         "Industry": ["전자부품 제조업", "반도체 제조업"],
                         "Sector": ["전기전자", "전기전자"]})


def _base_frame():
    return pd.DataFrame({"Code": ["005930", "000660"],
                         "Name": ["삼성전자", "SK하이닉스"],
                         "Market": ["KOSPI", "KOSPI"],
                         "Marcap": [1_000, 900], "Stocks": [10, 9], "Close": [100, 90]})


class KrxDescFallbackTests(unittest.TestCase):
    def test_walks_back_to_the_latest_available_day(self):
        """최신 파일이 없으면 있는 날짜까지 물러난다 — 업종은 하루 단위로 바뀌지 않는다."""
        asked = []

        def fake_read_csv(url, **kw):
            asked.append(url)
            if len(asked) <= 3:
                raise OSError("HTTP Error 404: Not Found")
            return _desc_frame()

        with patch.object(U.pd, "read_csv", side_effect=fake_read_csv):
            got = U._krx_desc_recent()

        self.assertIsNotNone(got)
        self.assertEqual(len(asked), 4)
        used = (date.today() - timedelta(days=3)).isoformat()
        self.assertIn(used, asked[-1])

    def test_gives_up_after_the_window_instead_of_looping_forever(self):
        with patch.object(U.pd, "read_csv", side_effect=OSError("404")) as m:
            self.assertIsNone(U._krx_desc_recent())
        self.assertEqual(m.call_count, U.KRX_DESC_LOOKBACK_DAYS)

    def test_fdr_failure_falls_back(self):
        with patch("FinanceDataReader.StockListing", side_effect=OSError("404")), \
             patch.object(U, "_krx_desc_recent", return_value=_desc_frame()) as fb:
            got = U._krx_desc()
        self.assertTrue(fb.called)
        self.assertIn("Industry", got.columns)


class KrListingSectorTests(unittest.TestCase):
    def test_sector_survives_when_fdr_desc_is_down(self):
        """fdr이 404를 내도 목록에 업종이 붙는다 — 이것이 계수 빌드의 입력이다."""
        def fake_listing(market):
            if market == "KRX":
                return _base_frame()
            raise OSError("HTTP Error 404: Not Found")

        with patch("FinanceDataReader.StockListing", side_effect=fake_listing), \
             patch.object(U, "_krx_desc_recent", return_value=_desc_frame()):
            out = U.get_kr_listing.__wrapped__()

        self.assertEqual(out["Sector"].notna().sum(), 2)
        self.assertEqual(out.loc[out["Code"] == "005930", "Sector"].iloc[0], "전자부품 제조업")
        self.assertEqual(out.loc[out["Code"] == "005930", "SubSector"].iloc[0], "전기전자")

    def test_says_so_when_industry_is_unavailable(self):
        """둘 다 실패하면 **말한다**. 조용한 실패가 이 장애를 6일간 숨겼다."""
        def fake_listing(market):
            if market == "KRX":
                return _base_frame()
            raise OSError("404")

        with patch("FinanceDataReader.StockListing", side_effect=fake_listing), \
             patch.object(U, "_krx_desc_recent", return_value=None), \
             patch("builtins.print") as say:
            out = U.get_kr_listing.__wrapped__()

        self.assertTrue(out["Sector"].isna().all())
        said = " ".join(str(c.args[0]) for c in say.call_args_list if c.args)
        self.assertIn("업종분류", said)


class KrListingCacheGuardTests(unittest.TestCase):
    """업종 없는 목록이 24시간 캐시에 눌러앉으면 그동안 KR이 통째로 죽는다."""

    def test_rejects_listing_without_sector(self):
        df = pd.DataFrame({"Code": [f"{i:06d}" for i in range(1500)],
                           "Sector": [None] * 1500})
        self.assertFalse(U._kr_listing_usable(df))

    def test_accepts_listing_with_mostly_filled_sector(self):
        sectors = ["제조업"] * 1440 + [None] * 60      # 96% — 실측 커버리지
        df = pd.DataFrame({"Code": [f"{i:06d}" for i in range(1500)], "Sector": sectors})
        self.assertTrue(U._kr_listing_usable(df))

    def test_rejects_suspiciously_short_listing(self):
        df = pd.DataFrame({"Code": ["005930"], "Sector": ["제조업"]})
        self.assertFalse(U._kr_listing_usable(df))


if __name__ == "__main__":
    unittest.main()
