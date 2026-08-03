"""회귀 기반 적정 배수(ADR-0014) 순수 함수 테스트."""
from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from src.analysis.warranted import (EXTRAPOLATION_LIMIT, MIN_FIT_SAMPLE,
                                    OTHER_SECTOR, ROE_EDGES, fit_leg,
                                    roe_bucket, sector_labels,
                                    warranted_multiple)


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

        # 모든 경계가 '아래 구간'에 속하는지 직접 못박는다 (edges[i] < v <= edges[i+1])
        self.assertEqual(roe_bucket(-0.05), "-20~-5%")
        self.assertEqual(roe_bucket(0.0), "-5~0%")
        self.assertEqual(roe_bucket(0.05), "0~5%")
        self.assertEqual(roe_bucket(0.10), "5~10%")
        self.assertEqual(roe_bucket(0.15), "10~15%")

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


def _synthetic(n=600, beta=0.30, seed=0):
    """log(배수) = -6 + 0.30·log(시총) + 업종효과 인 합성 데이터."""
    rng = np.random.default_rng(seed)
    mcap = np.exp(rng.uniform(np.log(1e10), np.log(1e13), n))
    sector = rng.choice(["A", "B", "C"], n)
    eff = {"A": 0.0, "B": 0.5, "C": -0.4}
    roe = rng.uniform(0.0, 0.20, n)
    y = -6.0 + beta * np.log(mcap) + np.array([eff[s] for s in sector])
    return pd.DataFrame({"multiple": np.exp(y), "mcap": mcap,
                         "sector": sector, "roe": roe})


class FitTests(unittest.TestCase):
    def test_recovers_known_size_coefficient(self):
        coef = fit_leg(_synthetic(), leg="pbr")
        self.assertIsNotNone(coef)
        self.assertAlmostEqual(coef["beta_size"], 0.30, places=2)

    def test_records_training_range_and_sample(self):
        df = _synthetic()
        coef = fit_leg(df, leg="pbr")
        self.assertEqual(coef["n"], len(df))
        self.assertAlmostEqual(coef["mcap_min"], df["mcap"].min(), places=0)
        self.assertAlmostEqual(coef["mcap_max"], df["mcap"].max(), places=0)
        self.assertEqual(coef["leg"], "pbr")

    def test_returns_none_when_sample_too_small(self):
        # 표본이 얇으면 계수를 만들지 않는다 — 만들면 과적합이다.
        # (실측: 상위 65종목만으로 적합하면 β가 0.066으로 4배 작게 나왔다)
        self.assertIsNone(fit_leg(_synthetic(n=MIN_FIT_SAMPLE - 1), leg="pbr"))

    def test_drops_nonpositive_and_nan_multiples(self):
        df = _synthetic(n=MIN_FIT_SAMPLE + 50)
        df.loc[df.index[:10], "multiple"] = 0.0
        df.loc[df.index[10:20], "multiple"] = np.nan
        coef = fit_leg(df, leg="pbr")
        self.assertEqual(coef["n"], len(df) - 20)

    def test_drops_infinite_values(self):
        # 자유 데이터에는 0으로 나눈 값이 섞인다. inf가 남으면 mcap 쪽은 lstsq를
        # 터뜨리고 multiple 쪽은 계수를 통째로 NaN으로 만든다(더 나쁘다 — 성공처럼 보인다).
        df = _synthetic(n=MIN_FIT_SAMPLE + 50)
        df.loc[df.index[:3], "mcap"] = np.inf
        df.loc[df.index[3:6], "multiple"] = np.inf
        coef = fit_leg(df, leg="pbr")
        self.assertEqual(coef["n"], len(df) - 6)
        self.assertTrue(np.isfinite(coef["beta_size"]))
        self.assertTrue(np.isfinite(coef["intercept"]))
        self.assertTrue(all(np.isfinite(v) for v in coef["sector_coef"].values()))

    def test_missing_sector_goes_to_other(self):
        # pandas 버전에 따라 astype(str)이 None을 "None" 문자열로 만드는 것을 막는다
        df = _synthetic(n=MIN_FIT_SAMPLE + 50)
        df["sector"] = df["sector"].astype(object)
        df.loc[df.index[:15], "sector"] = None
        coef = fit_leg(df, leg="pbr")
        self.assertNotIn("None", coef["sector_coef"])
        self.assertIn(OTHER_SECTOR, coef["sector_coef"])

    def test_returns_none_when_design_is_rank_deficient(self):
        # 시총이 전부 같으면 절편과 log(시총) 열이 공선이라 계수가 의미를 잃는다.
        # lstsq는 예외 대신 최소노름 해를 내므로 우리가 직접 걸러야 한다.
        df = _synthetic(n=MIN_FIT_SAMPLE + 50)
        df["mcap"] = 1e11
        self.assertIsNone(fit_leg(df, leg="pbr"))

    def test_recovers_known_sector_effects(self):
        # _synthetic의 업종효과: A=0.0, B=+0.5, C=-0.4. 더미는 기준 업종이 임의로
        # 정해지므로 절대값이 아니라 **차이**를 본다.
        coef = fit_leg(_synthetic(), leg="pbr")
        sc = coef["sector_coef"]
        self.assertAlmostEqual(sc["B"] - sc["A"], 0.5, places=2)
        self.assertAlmostEqual(sc["C"] - sc["A"], -0.4, places=2)

    def test_display_baselines_cover_every_sector(self):
        # Task 9가 이 둘로 '업종 기준 배수'를 만든다 — 업종이 빠지면 화면이 비어 버린다
        coef = fit_leg(_synthetic(), leg="pbr")
        for sec in coef["sector_coef"]:
            self.assertIn(sec, coef["sector_median_mcap"])
            self.assertIn(sec, coef["sector_median_roe_coef"])


class PredictTests(unittest.TestCase):
    def setUp(self):
        self.coef = fit_leg(_synthetic(), leg="pbr")

    def test_prediction_matches_generating_process(self):
        # 합성 데이터의 참값: log(배수) = -6 + 0.30·log(시총) + 업종효과(B=+0.5)
        mcap = 1e11
        out = warranted_multiple(self.coef, mcap=mcap, sector="B", roe=0.10)
        expected = math.exp(-6.0 + 0.30 * math.log(mcap) + 0.5)
        self.assertAlmostEqual(out["multiple"], expected, delta=expected * 0.02)

    def test_decomposition_multiplies_back_to_multiple(self):
        # 화면에 '업종기준 × 시총조정 × ROE조정'으로 풀어 쓰므로 정확히 복원돼야 한다
        out = warranted_multiple(self.coef, mcap=5e10, sector="A", roe=0.08)
        recomposed = (out["sector_base"]
                      * (1 + out["size_adj"])
                      * (1 + out["roe_adj"]))
        self.assertAlmostEqual(recomposed, out["multiple"], delta=out["multiple"] * 1e-6)

    def test_below_training_range_is_flagged(self):
        out = warranted_multiple(self.coef, mcap=self.coef["mcap_min"] / 2,
                                 sector="A", roe=0.08)
        self.assertTrue(out["below_range"])
        self.assertFalse(out["too_small"])

    def test_far_below_training_range_is_unusable(self):
        # 학습 하한의 1/EXTRAPOLATION_LIMIT 미만이면 쓰지 않는다
        out = warranted_multiple(self.coef,
                                 mcap=self.coef["mcap_min"] / (EXTRAPOLATION_LIMIT + 1),
                                 sector="A", roe=0.08)
        self.assertTrue(out["too_small"])
        self.assertIsNone(out["multiple"])

    def test_unknown_sector_falls_back_to_other(self):
        out = warranted_multiple(self.coef, mcap=5e10, sector="없는업종", roe=0.08)
        self.assertIsNotNone(out["multiple"])
        self.assertEqual(out["sector_used"], OTHER_SECTOR)

    def test_missing_roe_applies_no_roe_adjustment(self):
        # ROE를 모르면 조정하지 않는다 — 0.0을 넣으면 '기준 구간과 같다'고 판단한
        # 셈이 되는데 우리는 그걸 모른다. 규모는 항상 아니까 시총 조정은 그대로 간다.
        out = warranted_multiple(self.coef, mcap=5e10, sector="A", roe=None)
        self.assertIsNotNone(out["multiple"])
        self.assertAlmostEqual(out["roe_adj"], 0.0, places=9)
        self.assertNotAlmostEqual(out["size_adj"], 0.0, places=3)

    def test_no_coefficients_returns_blank(self):
        out = warranted_multiple(None, mcap=5e10, sector="A", roe=0.08)
        self.assertIsNone(out["multiple"])
        self.assertFalse(out["too_small"])
