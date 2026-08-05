"""회귀 기반 적정 배수(ADR-0014) 순수 함수 테스트."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.analysis.warranted import (EXTRAPOLATION_LIMIT, MIN_FIT_SAMPLE,
                                    MIN_KNOT_TAIL, OTHER_SECTOR, ROE_EDGES,
                                    SIZE_KNOT_QS, fit_leg, roe_bucket,
                                    loo_leg_error, sector_labels, size_knots,
                                    size_slope, size_term,
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


def _kinked(n=1200, lo=0.30, hi=0.02, kink_q=SIZE_KNOT_QS[0], seed=3):
    """꼬리에서 기울기가 꺾이는 합성 데이터 (ADR-0021).

    log(배수) = -6 + lo·x                     (x ≤ 꺾이는 점)
              = -6 + lo·k + hi·(x − k)        (x > 꺾이는 점)

    실측이 말하는 모양이다 — 중간 구간은 규모에 따라 배수가 오르는데 초대형 구간에서
    그 관계가 평평해진다. 직선 하나로 적합하면 꼬리를 과대추정한다.

    `kink_q`를 마디 분위에 맞추면 참 함수가 설계행렬의 생성공간 안에 있어 **정확히**
    복원된다. 어긋내면 근사만 되는데, 그건 결함이 아니라 마디를 분위로 고정한 대가다 —
    실제 데이터의 꺾이는 점을 우리는 모른다.
    """
    rng = np.random.default_rng(seed)
    x = np.sort(rng.uniform(np.log(1e10), np.log(1e14), n))
    k = float(np.quantile(x, kink_q))
    y = -6.0 + lo * x + (hi - lo) * np.maximum(0.0, x - k)
    return pd.DataFrame({"multiple": np.exp(y), "mcap": np.exp(x),
                         "sector": rng.choice(["A", "B"], n),
                         "roe": rng.uniform(0.0, 0.20, n)}), k


class SizeSplineTests(unittest.TestCase):
    """규모 항의 마디 (ADR-0021)."""

    def test_thin_tail_drops_the_knot(self):
        # 마디 위 표본이 얇으면 몇 종목이 기울기를 정하게 된다 — 그 마디는 두지 않는다
        lm = np.log(np.linspace(1e10, 1e13, MIN_KNOT_TAIL * 2))
        self.assertEqual(size_knots(lm), [])   # 0.80 위 20곳 · 0.95 위 2곳, 둘 다 미달

    def test_knots_appear_when_the_tail_is_thick_enough(self):
        lm = np.log(np.linspace(1e10, 1e13, 2000))
        self.assertEqual(len(size_knots(lm)), 2)

    def test_no_knots_is_exactly_the_old_linear_form(self):
        # 마디가 없으면 s(x) = β·x여야 한다. 폴백 경로가 현행과 같아지는 근거다.
        coef = {"beta_size": 0.3, "size_knots": [], "size_slopes": []}
        self.assertAlmostEqual(size_term(coef, 5.0), 1.5)
        self.assertAlmostEqual(size_slope(coef, 5.0), 0.3)

    def test_slope_is_local_not_global(self):
        coef = {"beta_size": 0.30, "size_knots": [10.0], "size_slopes": [-0.25]}
        self.assertAlmostEqual(size_slope(coef, 9.0), 0.30)    # 마디 아래
        self.assertAlmostEqual(size_slope(coef, 11.0), 0.05)   # 마디 위 — 꺾인 뒤
        # 연속이어야 한다 — 마디에서 값이 튀면 시총이 1원 달라질 때 적정가가 점프한다
        self.assertAlmostEqual(size_term(coef, 10.0), 3.0)
        self.assertAlmostEqual(size_term(coef, 10.001), 3.0 + 0.05 * 0.001, places=9)

    def test_recovers_a_kinked_generating_process(self):
        # 이 테스트가 이 ADR의 전부다 — 꺾인 관계를 직선 하나로 적합하면 꼬리를
        # 과대추정하고, 그것이 초대형주를 '저평가'로 미는 실측 현상이었다.
        df, _ = _kinked()          # 꺾이는 점 = 첫 마디. 참 함수가 생성공간 안에 있다
        coef = fit_leg(df, leg="pbr")
        self.assertIsNotNone(coef)
        big = math.log(float(df["mcap"].max()))
        self.assertAlmostEqual(size_slope(coef, big), 0.02, delta=0.02)
        self.assertAlmostEqual(size_slope(coef, math.log(2e10)), 0.30, delta=0.02)

    def test_misaligned_kink_still_flattens_the_tail(self):
        # 실제 데이터의 꺾이는 점은 마디와 어긋난다. 정확 복원은 안 되지만 **방향**은
        # 맞아야 한다 — 꼬리 기울기가 몸통보다 뚜렷하게 낮아야 한다.
        df, _ = _kinked(kink_q=0.85)
        coef = fit_leg(df, leg="pbr")
        big = size_slope(coef, math.log(float(df["mcap"].max())))
        small = size_slope(coef, math.log(2e10))
        self.assertLess(big, small - 0.10)

    def test_linear_data_leaves_the_slope_alone(self):
        # 꺾이지 않은 데이터에 마디를 얹어도 β를 흔들면 안 된다(과적합 방어)
        coef = fit_leg(_synthetic(n=1200), leg="pbr")
        for x in (math.log(2e10), math.log(5e12)):
            self.assertAlmostEqual(size_slope(coef, x), 0.30, delta=0.02)

    def test_kinked_fit_beats_a_straight_line_at_the_tail(self):
        # 꼬리에서 실제로 덜 틀리는가 — 직선 적합을 손으로 만들어 대조한다
        df, _ = _kinked()
        coef = fit_leg(df, leg="pbr")
        x = np.log(df["mcap"].to_numpy(float))
        y = np.log(df["multiple"].to_numpy(float))
        b1, b0 = np.polyfit(x, y, 1)
        tail = x >= np.quantile(x, 0.99)
        spline_err = np.abs([size_term(coef, v) + coef["intercept"]
                             - yv for v, yv in zip(x[tail], y[tail])])
        line_err = np.abs(b0 + b1 * x[tail] - y[tail])
        self.assertLess(spline_err.mean(), line_err.mean())


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

    def test_decomposition_holds_across_a_knot(self):
        # 마디가 있어도 화면의 곱셈 복원이 깨지면 안 된다 — 마디 아래·위 양쪽에서 확인한다
        df, _ = _kinked()
        coef = fit_leg(df, leg="pbr")
        for mcap in (2e10, 5e13):
            with self.subTest(mcap=mcap):
                out = warranted_multiple(coef, mcap=mcap, sector="A", roe=0.08)
                recomposed = (out["sector_base"] * (1 + out["size_adj"])
                              * (1 + out["roe_adj"]))
                self.assertAlmostEqual(recomposed, out["multiple"],
                                       delta=out["multiple"] * 1e-6)

    def test_reported_beta_is_the_local_slope(self):
        # 화면이 이 값으로 "시총이 10배면 배수를 N배로 봅니다"를 쓴다. 전역 기울기를
        # 주면 초대형주 화면에서 그 문장이 거짓이 된다.
        df, _ = _kinked()
        coef = fit_leg(df, leg="pbr")
        small = warranted_multiple(coef, mcap=2e10, sector="A", roe=0.08)
        big = warranted_multiple(coef, mcap=5e13, sector="A", roe=0.08)
        self.assertGreater(small["beta_size"], big["beta_size"])
        self.assertAlmostEqual(big["beta_size"], 0.02, delta=0.02)

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

    def test_too_small_is_also_below_range(self):
        out = warranted_multiple(self.coef,
                                 mcap=self.coef["mcap_min"] / (EXTRAPOLATION_LIMIT + 1),
                                 sector="A", roe=0.08)
        self.assertTrue(out["too_small"])
        self.assertTrue(out["below_range"])   # 같은 축의 더 극단이다

    def test_malformed_coefficients_degrade_to_blank(self):
        # 계수는 24시간 JSON 캐시를 거쳐 온다. 스키마가 바뀐 뒤 남은 옛 캐시가
        # 얕은 검증을 통과해 들어와도 예외 대신 '계산 불가'가 나와야 한다.
        for missing in ("sector_coef", "mcap_min", "intercept", "beta_size",
                        "size_knots", "size_slopes",
                        "roe_coef", "n", "sector_median_mcap",
                        "sector_median_roe_coef"):
            broken = {k: v for k, v in self.coef.items() if k != missing}
            with self.subTest(missing=missing):
                out = warranted_multiple(broken, mcap=5e10, sector="A", roe=0.08)
                self.assertIsNone(out["multiple"])

    def test_corrupt_numbers_degrade_to_blank(self):
        bad = dict(self.coef)
        bad["sector_median_mcap"] = dict(bad["sector_median_mcap"], A=-5e9)
        self.assertIsNone(warranted_multiple(bad, mcap=5e10, sector="A", roe=0.08)["multiple"])

        blown = dict(self.coef, beta_size=50.0)
        self.assertIsNone(warranted_multiple(blown, mcap=5e10, sector="A", roe=0.08)["multiple"])

    def test_non_float_mcap_is_coerced_or_refused(self):
        # 업스트림이 float로 강제하지만 이 함수 혼자서도 무너지지 않아야 한다
        self.assertIsNotNone(
            warranted_multiple(self.coef, mcap="5e10", sector="A", roe=0.08)["multiple"])
        self.assertIsNone(
            warranted_multiple(self.coef, mcap="열", sector="A", roe=0.08)["multiple"])


class Sp1500Tests(unittest.TestCase):
    def test_sp1500_concatenates_three_indices_without_duplicates(self):
        from src.data import universe

        def fake(url_key):
            n = {"500": 2, "400": 1, "600": 2}[url_key]
            return pd.DataFrame({
                "Symbol": {"500": ["AAPL", "MSFT"], "400": ["AAON"],
                           "600": ["AAON", "XPEL"]}[url_key],
                "Sector": ["Tech"] * n,
                "SubIndustry": ["X"] * n,
            })

        with patch.object(universe, "get_sp500", lambda: fake("500")), \
             patch.object(universe, "_wiki_index_table",
                          lambda url: fake("400" if "400" in url else "600")):
            out = universe.get_sp1500.__wrapped__()
        self.assertEqual(sorted(out["Symbol"]), ["AAON", "AAPL", "MSFT", "XPEL"])
        self.assertEqual(len(out), 4)   # AAON 중복 제거
        # 어느 지수에서 왔는지가 남아야 한다 — 이 표에는 시총 열이 없어서
        # `check_confidence.py`가 **지수를 규모 층으로** 쓴다(500 대형·400 중형·600 소형).
        tier = dict(zip(out["Symbol"], out["Index"]))
        self.assertEqual(tier["AAPL"], "S&P 500")
        self.assertEqual(tier["XPEL"], "S&P 600")
        # 겹친 종목은 먼저 온 쪽(400)의 층을 갖는다 — 층이 실행마다 흔들리면 안 된다.
        self.assertEqual(tier["AAON"], "S&P 400")

    def test_sp1500_survives_all_auxiliary_indices_failing(self):
        # 400·600을 둘 다 못 받아도 나머지(S&P 500)로 진행한다 — 무료 원천은 자주 흔들린다
        from src.data import universe

        def boom(url):
            raise RuntimeError("network down")

        base = pd.DataFrame({"Symbol": ["AAPL"], "Sector": ["Tech"],
                             "SubIndustry": ["X"]})
        with patch.object(universe, "get_sp500", lambda: base), \
             patch.object(universe, "_wiki_index_table", boom):
            out = universe.get_sp1500.__wrapped__()
        self.assertEqual(list(out["Symbol"]), ["AAPL"])

    def test_sp1500_keeps_the_index_that_succeeded(self):
        # 흔한 경우는 둘 다 죽는 게 아니라 하나만 죽는 것이다. 살아남은 쪽은 들어와야 한다.
        from src.data import universe

        base = pd.DataFrame({"Symbol": ["AAPL"], "Sector": ["Tech"],
                             "SubIndustry": ["X"]})

        def half(url):
            if "400" in url:
                raise RuntimeError("400 down")
            return pd.DataFrame({"Symbol": ["XPEL"], "Sector": ["Tech"],
                                 "SubIndustry": ["X"]})

        with patch.object(universe, "get_sp500", lambda: base), \
             patch.object(universe, "_wiki_index_table", half):
            out = universe.get_sp1500.__wrapped__()
        self.assertEqual(sorted(out["Symbol"]), ["AAPL", "XPEL"])

    def test_degraded_universe_is_not_cached(self):
        # 400·600이 모두 실패하면 503행짜리 축소 유니버스가 나오는데, 그것이 7일 동안
        # 캐시되면 하한이 $7.0B로 되돌아간 상태로 굳는다 — 이 유니버스를 넓힌 이유가 사라진다.
        from src.data import universe

        small = pd.DataFrame({"Symbol": [f"S{i}" for i in range(503)],
                              "Sector": ["Tech"] * 503, "SubIndustry": ["X"] * 503})
        with tempfile.TemporaryDirectory() as tmp, \
             patch("src.data.cache.CACHE_DIR", Path(tmp)), \
             patch.object(universe, "get_sp500", lambda: small), \
             patch.object(universe, "_wiki_index_table",
                          lambda url: (_ for _ in ()).throw(RuntimeError("down"))):
            out = universe.get_sp1500()
            self.assertEqual(len(out), 503)          # 값 자체는 돌려준다
            cached = list(Path(tmp).glob("sp1500_*"))
            self.assertEqual(cached, [])             # 그러나 저장하지는 않는다

    def test_cache_without_the_index_column_is_not_reused(self):
        # `Index` 열은 나중에 붙었다. 캐시 이름(`sp1500`)은 그대로라 **열이 없던 시절의
        # 7일짜리 캐시가 그대로 통과할 수 있다** — 그러면 층화표집이 KeyError로 죽는다.
        # 이름에 버전을 붙이는 대신 실제 모양을 검사한다(`_coefficients_usable`과 같은 선택).
        from src.data import universe

        from src.data.cache import _key

        old = pd.DataFrame({"Symbol": [f"S{i}" for i in range(1200)],
                            "Sector": ["Tech"] * 1200, "SubIndustry": ["X"] * 1200})
        fresh = old.assign(Index="S&P 500")
        with tempfile.TemporaryDirectory() as tmp, \
             patch("src.data.cache.CACHE_DIR", Path(tmp)), \
             patch.object(universe, "get_sp500", lambda: fresh), \
             patch.object(universe, "_wiki_index_table",
                          lambda url: (_ for _ in ()).throw(RuntimeError("down"))):
            # 옛 모양의 '신선한' 캐시를 깔아 둔다 — ttl로는 걸러지지 않는 상태다.
            old.to_parquet(Path(tmp) / f"{_key('sp1500', (), {})}.parquet")
            out = universe.get_sp1500()
        self.assertIn("Index", out.columns)           # 캐시를 버리고 다시 받았다


class UniverseSnapshotTests(unittest.TestCase):
    def _listing(self):
        return pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"],
                             "Market": ["KOSPI"], "Marcap": [1.5e15],
                             "Sector": ["반도체"], "is_common": [True]})

    def test_num_rejects_non_finite_and_unparseable(self):
        from src.data.universe_multiples import _num

        self.assertIsNone(_num(None))
        self.assertIsNone(_num(float("inf")))
        self.assertIsNone(_num(float("nan")))
        self.assertIsNone(_num("열두"))
        self.assertEqual(_num("3.5"), 3.5)
        self.assertEqual(_num(2), 2)

    def test_kr_keeps_naver_values_when_yfinance_dies(self):
        # 한국은 두 원천이 상보적이다(네이버 per·pbr·roe / yfinance psr·ev_ebitda).
        # yfinance가 레이트리밋으로 죽는 것은 흔한 일이고 — 실측에서 2,688개 중
        # 1,655개만 성공했다 — 그때 네이버 값까지 잃으면 안 된다.
        from src.data import universe_multiples as um

        def boom(t):
            raise RuntimeError("401 Invalid Crumb")

        with patch.object(um, "_kr_listing", self._listing), \
             patch.object(um, "_naver_fundamental",
                          lambda code: {"per": 12.0, "pbr": 1.4, "roe_approx": 0.11}), \
             patch.object(um, "_info_metrics", boom):
            df = um.collect_kr()
        row = df.iloc[0]
        self.assertEqual(row["per"], 12.0)          # 네이버는 살아남았다
        self.assertEqual(row["pbr"], 1.4)
        self.assertEqual(row["roe"], 0.11)
        self.assertIsNone(row["psr"])               # yfinance만 비었다
        self.assertIsNone(row["ev_ebitda"])

    def test_kr_keeps_yfinance_values_when_naver_dies(self):
        from src.data import universe_multiples as um

        def boom(code):
            raise RuntimeError("naver down")

        with patch.object(um, "_kr_listing", self._listing), \
             patch.object(um, "_naver_fundamental", boom), \
             patch.object(um, "_info_metrics",
                          lambda t: {"psr": 1.2, "ev_ebitda": 8.0}):
            df = um.collect_kr()
        row = df.iloc[0]
        self.assertIsNone(row["per"])
        self.assertEqual(row["psr"], 1.2)
        self.assertEqual(row["ev_ebitda"], 8.0)
        self.assertEqual(row["mcap"], 1.5e15)       # 시총은 상장목록에서 온다

    def test_us_row_survives_a_failing_symbol(self):
        from src.data import universe_multiples as um

        uni = pd.DataFrame({"Symbol": ["AAPL", "MSFT"], "Sector": ["Tech", "Tech"],
                            "SubIndustry": ["X", "X"]})

        def half(sym):
            if sym == "AAPL":
                raise RuntimeError("429")
            return {"market_cap": 3e12, "per": 30.0, "pbr": 12.0,
                    "psr": 11.0, "ev_ebitda": 22.0, "roe": 0.35}

        with patch.object(um, "_us_universe", lambda: uni), \
             patch.object(um, "_info_metrics", half):
            df = um.collect_us().set_index("code")
        # 2행 이상에서 None과 float가 섞이면 pandas가 컬럼을 float64로 승격하며
        # None을 NaN으로 바꾼다(1행짜리 KR 테스트는 object dtype이라 None이 유지됨,
        # 실측 확인됨) — 그래서 여기서는 isna로 "값이 비었다"를 확인한다.
        self.assertTrue(pd.isna(df.loc["AAPL", "per"]))  # 실패한 종목은 비지만 행은 남는다
        self.assertEqual(df.loc["MSFT", "per"], 30.0)
        self.assertEqual(len(df), 2)


class CoefficientTableTests(unittest.TestCase):
    def test_builds_only_legs_with_enough_sample(self):
        from src.data.universe_multiples import build_coefficients

        base = _synthetic(n=MIN_FIT_SAMPLE + 100)
        df = pd.DataFrame({
            "mcap": base["mcap"], "sector": base["sector"], "roe": base["roe"],
            "pbr": base["multiple"],
            "per": base["multiple"] * 10,
            "psr": [np.nan] * len(base),                       # 전부 결측 → 계수 없음
            "ev_ebitda": ([np.nan] * (len(base) - 50)
                          + list(base["multiple"][:50])),      # 50개뿐 → 계수 없음
        })
        out = build_coefficients(df)
        self.assertIn("pbr", out)
        self.assertIn("per", out)
        self.assertNotIn("psr", out)
        self.assertNotIn("ev_ebitda", out)
        self.assertAlmostEqual(out["pbr"]["beta_size"], 0.30, places=2)

    def test_coefficients_or_none_swallows_any_failure(self):
        # 계수를 못 읽는 것은 판정을 멈출 이유가 아니다 — 피어 중앙값 폴백이 있다.
        # 이게 예외를 흘리면 캐시·네트워크 장애가 그날 전 종목의 분석을 통째로 죽인다.
        from src.data import universe_multiples as um

        with patch.object(um, "get_coefficients", side_effect=RuntimeError("cache down")):
            self.assertIsNone(um.coefficients_or_none("KR"))
        # 빈 dict도 None으로 — 호출부는 `if coef:`로 갈림길을 판단한다
        with patch.object(um, "get_coefficients", lambda market: {}):
            self.assertIsNone(um.coefficients_or_none("KR"))


class RelativeValueTests(unittest.TestCase):
    def test_warranted_fairs_uses_regression_not_peers(self):
        from src.analysis.valuation import _warranted_fairs

        coef = {"pbr": fit_leg(_synthetic(), leg="pbr")}
        fairs, used, parts = _warranted_fairs(
            coef, mcap=1e11, sector="B", roe=0.10,
            eps=None, bps=1000.0, ebitda_ps=None, debt_ps=0.0, cash_ps=0.0,
            revenue_ps=None, is_loss=True, is_financial=False)
        self.assertEqual(len(fairs), 1)
        m = math.exp(-6.0 + 0.30 * math.log(1e11) + 0.5)
        self.assertAlmostEqual(fairs[0], m * 1000.0, delta=m * 1000.0 * 0.02)
        self.assertTrue(used[0].startswith("PBR"))
        self.assertEqual(parts[0]["leg"], "pbr")

    def test_no_coefficients_yields_no_fairs(self):
        from src.analysis.valuation import _warranted_fairs

        fairs, used, parts = _warranted_fairs(
            {}, mcap=1e11, sector="B", roe=0.10, eps=100.0, bps=1000.0,
            ebitda_ps=None, debt_ps=0.0, cash_ps=0.0, revenue_ps=None,
            is_loss=False, is_financial=False)
        self.assertEqual(fairs, [])


class LegErrorTests(unittest.TestCase):
    """①에 쓴 다리의 실측 오차와 안전마진 문턱 (ADR-0017)."""

    def test_worst_leg_drives_margin(self):
        from src.analysis.warranted import leg_error

        e = leg_error("KR", ["per", "pbr", "ev_ebitda"])
        # 가장 나쁜 다리를 골라야 안전마진이 보수적인 쪽으로 선다.
        self.assertEqual(e["worst_leg"], "EV/EBITDA")
        self.assertAlmostEqual(e["up"], math.exp(0.667) - 1, places=6)
        self.assertAlmostEqual(e["margin"], math.exp(-0.667) - 1, places=6)
        # 로그 MAE는 원 스케일에서 비대칭이다 — 위로 더 크고 아래로 더 작다.
        self.assertGreater(e["up"], abs(e["margin"]))
        self.assertLess(e["margin"], 0)

    def test_psr_is_the_worst_leg_when_present(self):
        from src.analysis.warranted import leg_error

        e = leg_error("KR", ["pbr", "psr"])
        self.assertEqual(e["worst_leg"], "PSR")
        # 한국 PSR은 네 다리 중 가장 부정확하다. ADR-0014 당시 ±151%였고 ADR-0022의
        # 재측정에서 ±145%다 — 크기 자체가 결론이라 자릿수만 지키면 된다.
        self.assertAlmostEqual(e["up"], 1.452, delta=0.01)

    def test_us_psr_is_not_borrowed_from_kr(self):
        from src.analysis.warranted import leg_error

        # ADR-0022에서 미국 PSR·EV/EBITDA를 처음 쟀다. **시장마다 값이 다르다**는 것이
        # "다른 시장 값을 빌려오지 않는다"는 규칙의 근거다 — 한국 0.897 대 미국 0.656.
        kr = leg_error("KR", ["psr"])["up"]
        us = leg_error("US", ["psr"])["up"]
        self.assertLess(us, kr * 0.8, "미국 PSR이 한국 값과 사실상 같으면 규칙의 근거가 없다")

    def test_unmeasured_legs_are_named_not_invented(self):
        from src.analysis.warranted import leg_error

        # 네 다리는 두 시장 모두 쟀으므로(ADR-0022), 미측정은 **아직 재지 않은 새 축**에서
        # 생긴다. EPV가 곧 그 자리다(ADR-0016) — 재기 전에 넣으면 여기 걸린다.
        e = leg_error("KR", ["pbr", "epv"])
        self.assertEqual(e["unmeasured"], ["epv"])
        self.assertEqual([m["label"] for m in e["measured"]], ["PBR"])
        self.assertEqual(e["worst_leg"], "PBR")

    def test_no_measured_leg_yields_no_margin(self):
        from src.analysis.warranted import leg_error

        # 다리가 전부 미측정이면 안전마진을 내지 않는다. 지어낸 값보다 '없음'이 정직하다.
        # 화면은 침묵하지 않고 "측정된 적이 없습니다"라고 말한다(PR #107에서 고친 자리).
        e = leg_error("US", ["epv"])
        self.assertEqual(e["measured"], [])
        self.assertIsNone(e["margin"])
        self.assertIsNone(e["worst_leg"])
        self.assertEqual(e["unmeasured"], ["epv"])

    def test_unknown_market_is_not_a_crash(self):
        from src.analysis.warranted import leg_error

        e = leg_error("JP", ["per"])
        self.assertIsNone(e["margin"])
        self.assertEqual(e["unmeasured"], ["PER"])

    def test_psr_is_worst_in_every_measured_market(self):
        from src.analysis.warranted import LEG_MAE

        # 화면이 "PSR이 실측 오차가 가장 크다"고 말한다(valuation.psr_error_phrase).
        # 그 문장은 이 표에 기대고 있으므로, 재측정으로 순위가 바뀌면 **문장부터**
        # 고쳐야 한다. 여기서 걸리게 두는 것이 그 알림이다.
        for market, table in LEG_MAE.items():
            if "psr" not in table:
                continue
            with self.subTest(market=market):
                self.assertEqual(max(table, key=table.get), "psr",
                                 f"{market}에서 PSR이 더는 최악이 아니다 — 화면 문구를 고쳐라")


class PsrErrorPhraseTests(unittest.TestCase):
    """PSR 오차 폭 문구가 **잰 값에서만** 나오는가 (ADR-0017·0022).

    한국에서 잰 "±150%"가 문장에 박혀 있어 미국 종목 화면에도 그대로 떴던 자리다.
    """

    def phrase(self, market, legs):
        from src.analysis.valuation import psr_error_phrase
        from src.analysis.warranted import leg_error
        return psr_error_phrase(leg_error(market, legs))

    def test_each_market_quotes_its_own_number(self):
        kr = self.phrase("KR", ["psr"])
        us = self.phrase("US", ["psr"])
        # 한국 0.897 → +145%, 미국 0.656 → +93%. **같은 문장이면 하나는 빌려온 것이다.**
        self.assertIn("145%", kr)
        self.assertIn("93%", us)
        self.assertNotEqual(kr, us)

    def test_unmeasured_path_says_so_instead_of_a_number(self):
        # 피어 중앙값 폴백에는 `leg_error`가 없다(오차는 회귀를 재서 나온 값이다).
        # 그때 다른 데서 숫자를 끌어오면 그것이 바로 지어내기다(ADR-0011).
        for empty in (None, {}, {"measured": []}):
            with self.subTest(leg_error=empty):
                from src.analysis.valuation import psr_error_phrase
                text = psr_error_phrase(empty)
                self.assertIn("측정된 적이 없", text)
                self.assertNotIn("%", text)

    def test_unknown_market_does_not_borrow_a_number(self):
        # 아직 재지 않은 시장이 한국 숫자를 물려받으면 안 된다.
        self.assertNotIn("%", self.phrase("JP", ["psr"]))


def _noisy(n=600, beta=0.30, sigma=0.40, seed=11):
    """_synthetic과 같되 잡음을 얹는다 — 잔차가 0이면 LOO를 검증할 수 없다."""
    rng = np.random.default_rng(seed)
    mcap = np.exp(rng.uniform(np.log(1e10), np.log(1e13), n))
    sector = rng.choice(["A", "B", "C"], n)
    eff = {"A": 0.0, "B": 0.5, "C": -0.4}
    roe = rng.uniform(0.0, 0.20, n)
    y = (-6.0 + beta * np.log(mcap) + np.array([eff[s] for s in sector])
         + rng.normal(0.0, sigma, n))
    return pd.DataFrame({"multiple": np.exp(y), "mcap": mcap,
                         "sector": sector, "roe": roe})


class LooLegErrorTests(unittest.TestCase):
    """LEG_MAE를 만드는 측정 자체를 검증한다 (ADR-0022).

    이 표의 원래 값을 만든 스크립트가 저장소에 없어서 재현이 안 된 적이 있다.
    측정을 코드로 남기는 것만으로는 부족하고, 그 측정이 맞는지도 못박아야 한다.
    """

    def test_press_equals_brute_force_leave_one_out(self):
        # PRESS 잔차 e_i/(1-h_ii)가 '실제로 그 점을 빼고 다시 적합한' 잔차와 같은지.
        # 근사가 아니라 항등식이므로 엄격하게 본다.
        from src.analysis.warranted import _design_matrix, _prep, loo_leg_error

        df = _noisy()
        d = _prep(df)
        X, *_ = _design_matrix(d)
        y = np.log(d["multiple"].to_numpy(float))
        beta, _r, _rk, _s = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        hat = np.einsum("ij,jk,ik->i", X, np.linalg.pinv(X.T @ X), X)
        press = resid / (1 - hat)

        rng = np.random.default_rng(0)
        for i in rng.choice(len(y), 12, replace=False):
            keep = np.arange(len(y)) != i
            b_i, _r, _rk, _s = np.linalg.lstsq(X[keep], y[keep], rcond=None)
            brute = y[i] - X[i] @ b_i
            self.assertAlmostEqual(press[i], brute, places=6,
                                   msg=f"PRESS와 실제 LOO가 어긋난다 (i={i})")

        out = loo_leg_error(df)
        self.assertAlmostEqual(out["mae"], float(np.mean(np.abs(press))), places=9)

    def test_leave_one_out_is_never_better_than_in_sample(self):
        # 같은 데이터로 적합하고 같은 데이터를 예측하면 항상 더 좋아 보인다.
        # 이 부등호가 뒤집히면 측정이 잘못된 것이다.
        out = loo_leg_error(_noisy())
        self.assertGreater(out["mae"], out["mae_in_sample"])

    def test_returns_none_when_sample_too_small(self):
        # 오염된 값보다 '없음'이 정직하다(ADR-0011).
        self.assertIsNone(loo_leg_error(_noisy(n=MIN_FIT_SAMPLE - 1)))

    def test_reports_saturated_points(self):
        out = loo_leg_error(_noisy())
        self.assertIn("saturated", out)
        self.assertIsInstance(out["saturated"], int)


class EffectiveAxesTests(unittest.TestCase):
    """겹치는 방법을 몇 개로 쳐야 하나 (ADR-0022)."""

    def test_independent_methods_count_fully(self):
        from src.analysis.warranted import effective_axes

        table = {("a", "b"): 0.0, ("a", "c"): 0.0, ("b", "c"): 0.0}
        n_eff, ok = effective_axes(["a", "b", "c"], "KR", table)
        self.assertTrue(ok)
        self.assertAlmostEqual(n_eff, 3.0, places=9)

    def test_identical_methods_count_as_one(self):
        from src.analysis.warranted import effective_axes

        # ①과 ⑤가 같은 적정 배수를 쓰는 경우가 이 극단이다(ADR-0015 · AAPL).
        table = {("a", "b"): 1.0, ("a", "c"): 1.0, ("b", "c"): 1.0}
        n_eff, ok = effective_axes(["a", "b", "c"], "KR", table)
        self.assertTrue(ok)
        self.assertAlmostEqual(n_eff, 1.0, places=9)

    def test_partial_overlap_falls_between(self):
        from src.analysis.warranted import effective_axes

        table = {("a", "b"): 0.5, ("a", "c"): 0.5, ("b", "c"): 0.5}
        n_eff, _ = effective_axes(["a", "b", "c"], "KR", table)
        self.assertAlmostEqual(n_eff, 3 / 2.0, places=9)
        self.assertLess(n_eff, 3.0)
        self.assertGreater(n_eff, 1.0)

    def test_unknown_pair_disables_the_cap(self):
        from src.analysis.warranted import effective_axes

        # EPV처럼 아직 안 잰 축이 들어오면 상한을 걸지 않는다 — 지어낸 상관으로 등급을
        # 깎느니 안 깎는 쪽이 정직하다(ADR-0011). 이것이 뒤집히면 조용히 틀린 등급이 나간다.
        table = {("a", "b"): 0.9}
        n_eff, ok = effective_axes(["a", "b", "epv"], "KR", table)
        self.assertFalse(ok)
        self.assertAlmostEqual(n_eff, 3.0, places=9)

    def test_negative_correlation_is_clipped(self):
        from src.analysis.warranted import effective_axes

        # n_eff > n은 '독립보다 더 독립'이라 뜻이 없다.
        table = {("a", "b"): -0.8}
        n_eff, ok = effective_axes(["a", "b"], "KR", table)
        self.assertTrue(ok)
        self.assertAlmostEqual(n_eff, 2.0, places=9)

    def test_single_method_matches_current_behaviour(self):
        from src.analysis.warranted import effective_axes

        n_eff, ok = effective_axes(["a"], "KR", {})
        self.assertTrue(ok)
        self.assertAlmostEqual(n_eff, 1.0, places=9)
        self.assertEqual(effective_axes([], "KR", {})[0], 0.0)

    def test_order_does_not_matter(self):
        from src.analysis.warranted import effective_axes

        table = {("a", "b"): 0.3, ("a", "c"): 0.7, ("b", "c"): 0.1}
        self.assertEqual(effective_axes(["c", "a", "b"], "KR", table),
                         effective_axes(["a", "b", "c"], "KR", table))
