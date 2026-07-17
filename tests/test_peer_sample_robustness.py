"""피어 표본 부족 견고화 회귀 테스트.

- file_cache의 validate: 빈 응답은 캐시하지 않고(오염 방지), 이전 정상 캐시를 우선한다
- fetch_info_metrics의 빈 응답 판별(_info_has_substance)
- compute_scores details에 지표별 피어 보유 수(n)가 실려 UI가 사유를 표시할 수 있다
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.analysis.scoring import compute_scores
from src.data.base import _info_has_substance, fill_self_from_financials
from src.data.cache import file_cache


class CacheValidateTests(unittest.TestCase):
    def test_invalid_result_is_returned_but_not_cached(self):
        results = [{"ok": False}, {"ok": True}]

        @file_cache("val-test-a", ttl_hours=1, validate=lambda d: d.get("ok"))
        def load():
            return results.pop(0)

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.data.cache.CACHE_DIR", Path(tmp)
        ):
            self.assertEqual(load(), {"ok": False})   # 빈 응답 — 그대로 반환하되 저장 안 함
            self.assertEqual(load(), {"ok": True})    # 재호출 시 원천 재요청으로 정상값 획득
            self.assertEqual(load(), {"ok": True})    # 정상값은 캐시에서 재사용

    def test_good_cache_survives_later_empty_response(self):
        results = [{"ok": True, "v": 1}, {"ok": False}]

        @file_cache("val-test-b", ttl_hours=0, validate=lambda d: d.get("ok"))
        def load():
            return results.pop(0)

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.data.cache.CACHE_DIR", Path(tmp)
        ):
            self.assertEqual(load()["v"], 1)          # 정상값 저장 (ttl=0 → 즉시 만료)
            self.assertEqual(load()["v"], 1)          # 재요청이 빈 응답 → 이전 정상 캐시 반환

    def test_poisoned_fresh_cache_is_refetched(self):
        @file_cache("val-test-c", ttl_hours=1)
        def save_bad():
            return {"ok": False}

        @file_cache("val-test-c", ttl_hours=1, validate=lambda d: d.get("ok"))
        def load_good():
            return {"ok": True}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.data.cache.CACHE_DIR", Path(tmp)
        ):
            save_bad()                                 # 과거에 저장된 오염 캐시 재현 (같은 키)
            self.assertEqual(load_good(), {"ok": True})  # 검사 실패 캐시는 무시하고 재요청


class InfoSubstanceTests(unittest.TestCase):
    def test_throttled_empty_info_detected(self):
        empty = {k: None for k in ("market_cap", "price", "per", "pbr", "roe", "name")}
        self.assertFalse(_info_has_substance(empty))

    def test_partial_but_real_info_passes(self):
        self.assertTrue(_info_has_substance({"market_cap": 1e12, "per": None, "pbr": None, "price": None}))
        self.assertTrue(_info_has_substance({"market_cap": None, "price": 1000, "per": None, "pbr": None}))


class ScoreDetailCountTests(unittest.TestCase):
    def _frame(self):
        # 자기 + 피어 4: per/pbr은 4개 모두, rev_growth는 2개만 보유
        rows = {
            "SELF": {"is_self": True, "per": 10, "pbr": 1.0, "roe": 0.12, "rev_growth": 0.05},
            "P1": {"is_self": False, "per": 12, "pbr": 1.2, "roe": 0.10, "rev_growth": 0.04},
            "P2": {"is_self": False, "per": 15, "pbr": 1.5, "roe": 0.08, "rev_growth": 0.02},
            "P3": {"is_self": False, "per": 9, "pbr": 0.9, "roe": 0.15, "rev_growth": None},
            "P4": {"is_self": False, "per": 20, "pbr": 2.0, "roe": 0.05, "rev_growth": None},
        }
        return pd.DataFrame.from_dict(rows, orient="index")

    def test_axis_null_with_insufficient_peers_and_n_exposed(self):
        out = compute_scores(self._frame(), "SELF")
        self.assertIsNone(out.scores["성장성"])          # rev_growth 보유 피어 2 < 3
        self.assertIsNotNone(out.scores["밸류에이션"])    # per·pbr는 4개 → 계산됨
        by_key = {r[0]: r for r in out.details["성장성"]}
        self.assertEqual(by_key["rev_growth"][4], 2)     # n(보유 피어 수)이 상세에 노출
        by_key_val = {r[0]: r for r in out.details["밸류에이션"]}
        self.assertEqual(by_key_val["per"][4], 4)


class SelfFillTests(unittest.TestCase):
    def _fin(self):
        return pd.DataFrame({
            "revenue": [100.0, 120.0], "net_income": [10.0, 12.0],
            "total_debt": [20.0, 20.0], "total_equity": [100.0, 110.0],
            "current_assets": [50.0, 60.0], "current_liabilities": [25.0, 30.0],
            "fcf": [8.0, 9.0], "ocf": [11.0, 12.0],
        }, index=[2023, 2024])

    def _peers(self, self_rev_growth=None):
        return pd.DataFrame.from_dict({
            "SELF": {"is_self": True, "per": 10.0, "rev_growth": self_rev_growth,
                     "debt_to_equity": None, "fcf_yield": None},
            "P1": {"is_self": False, "per": 12.0, "rev_growth": 0.05,
                   "debt_to_equity": 40.0, "fcf_yield": 0.03},
        }, orient="index")

    def test_missing_self_metrics_filled_from_statements(self):
        out = fill_self_from_financials(self._peers(), "SELF", self._fin(), market_cap=300.0)
        self.assertAlmostEqual(out.at["SELF", "rev_growth"], 0.2)          # 120/100 - 1
        self.assertAlmostEqual(out.at["SELF", "debt_to_equity"], 20 / 110 * 100)
        self.assertAlmostEqual(out.at["SELF", "current_ratio"], 2.0)
        self.assertAlmostEqual(out.at["SELF", "fcf_yield"], 9.0 / 300.0)
        # 피어 행은 건드리지 않는다
        self.assertAlmostEqual(out.at["P1", "rev_growth"], 0.05)

    def test_existing_self_value_not_overwritten(self):
        out = fill_self_from_financials(self._peers(self_rev_growth=0.33), "SELF",
                                        self._fin(), market_cap=300.0)
        self.assertAlmostEqual(out.at["SELF", "rev_growth"], 0.33)

    def test_negative_base_year_growth_skipped(self):
        # 적자→흑자 전환은 성장률 정의가 무의미하므로 채우지 않는다
        fin = self._fin()
        fin.loc[2023, "net_income"] = -5.0
        out = fill_self_from_financials(self._peers(), "SELF", fin, market_cap=300.0)
        val = out.at["SELF", "earnings_growth"] if "earnings_growth" in out.columns else None
        self.assertTrue(val is None or pd.isna(val))


if __name__ == "__main__":
    unittest.main()
