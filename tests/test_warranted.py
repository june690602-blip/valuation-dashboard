"""회귀 기반 적정 배수(ADR-0014) 순수 함수 테스트."""
from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from src.analysis.warranted import ROE_EDGES, roe_bucket, sector_labels


class BucketTests(unittest.TestCase):
    def test_roe_bucket_edges(self):
        # ADR-0014: ROE와 배수는 U자라 반드시 구간 더미로 넣는다
        self.assertEqual(roe_bucket(-0.50), "≤-20%")
        self.assertEqual(roe_bucket(-0.20), "≤-20%")     # 경계는 아래 구간에 넣는다
        self.assertEqual(roe_bucket(-0.10), "-20~-5%")
        self.assertEqual(roe_bucket(-0.01), "-5~0%")
        self.assertEqual(roe_bucket(0.03), "0~5%")
        self.assertEqual(roe_bucket(0.12), "10~15%")
        self.assertEqual(roe_bucket(0.40), ">15%")

    def test_roe_bucket_missing_is_none(self):
        self.assertIsNone(roe_bucket(None))
        self.assertIsNone(roe_bucket(float("nan")))

    def test_roe_edges_are_seven_buckets(self):
        self.assertEqual(len(ROE_EDGES) - 1, 7)

    def test_sector_labels_pools_thin_sectors(self):
        # 표본 10곳 미만 업종은 '기타'로 묶는다 — 셀당 표본이 얇으면 더미가 불안정하다
        s = pd.Series(["반도체"] * 12 + ["조선"] * 3 + ["제약"] * 10)
        out = sector_labels(s, min_n=10)
        self.assertEqual(out.tolist().count("반도체"), 12)
        self.assertEqual(out.tolist().count("제약"), 10)
        self.assertEqual(out.tolist().count("기타"), 3)
