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
        self.assertAlmostEqual(e["up"], math.exp(0.639) - 1, places=6)
        self.assertAlmostEqual(e["margin"], math.exp(-0.639) - 1, places=6)
        # 로그 MAE는 원 스케일에서 비대칭이다 — 위로 더 크고 아래로 더 작다.
        self.assertGreater(e["up"], abs(e["margin"]))
        self.assertLess(e["margin"], 0)

    def test_psr_is_the_worst_leg_when_present(self):
        from src.analysis.warranted import leg_error

        e = leg_error("KR", ["pbr", "psr"])
        self.assertEqual(e["worst_leg"], "PSR")
        # ADR-0014가 "±150% 수준"이라 적은 그 값이어야 한다.
        self.assertAlmostEqual(e["up"], 1.512, delta=0.01)

    def test_unmeasured_legs_are_named_not_invented(self):
        from src.analysis.warranted import leg_error

        e = leg_error("US", ["per", "pbr", "ev_ebitda"])
        # 미국 EV/EBITDA는 ADR-0014가 재지 않았다 — 한국 값을 빌려오면 안 된다.
        self.assertEqual(e["unmeasured"], ["EV/EBITDA"])
        self.assertEqual([m["label"] for m in e["measured"]], ["PER", "PBR"])
        self.assertEqual(e["worst_leg"], "PBR")

    def test_no_measured_leg_yields_no_margin(self):
        from src.analysis.warranted import leg_error

        e = leg_error("US", ["psr"])
        self.assertEqual(e["measured"], [])
        self.assertIsNone(e["margin"])
        self.assertIsNone(e["worst_leg"])
        self.assertEqual(e["unmeasured"], ["PSR"])

    def test_unknown_market_is_not_a_crash(self):
        from src.analysis.warranted import leg_error

        e = leg_error("JP", ["per"])
        self.assertIsNone(e["margin"])
        self.assertEqual(e["unmeasured"], ["PER"])
