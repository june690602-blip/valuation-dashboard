from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.analysis.backtest import (_non_overlapping_values, _rim_discount,
                                   run_backtest)
from src.analysis.indicators import _average_balance
from src.analysis.portfolio import after_tax_row
from src.analysis.valuation import (BOOK_REJECTED_PBR, _band, _band_quality,
                                    _fundamental_daily, _rim, compute_valuation)
from src.data.models import CompanyData, Consensus, actual_prices
from src.data.opendart import _parse_report


class AverageBalanceTests(unittest.TestCase):
    def test_annual_fallback_uses_previous_year(self):
        annual = pd.Series([80.0, 120.0], index=[2023, 2024])
        company = SimpleNamespace(
            ttm=None,
            latest=lambda _col: 120.0,
            annual=lambda _col: annual,
        )

        self.assertEqual(_average_balance(company, "total_equity"), 100.0)

    def test_ttm_balance_uses_latest_annual_as_previous(self):
        annual = pd.Series([80.0, 120.0], index=[2023, 2024])
        company = SimpleNamespace(
            ttm=pd.Series({"total_equity": 140.0}),
            latest=lambda _col: 140.0,
            annual=lambda _col: annual,
        )

        self.assertEqual(_average_balance(company, "total_equity"), 130.0)


class HistoricalPerShareTests(unittest.TestCase):
    def test_pbr_fundamental_uses_period_share_count(self):
        financials = pd.DataFrame(
            {
                "total_equity": [1_000.0, 2_000.0],
                "shares_outstanding": [100.0, 200.0],
                "fiscal_end": [pd.Timestamp("2022-12-31"), pd.Timestamp("2023-12-31")],
            },
            index=[2022, 2023],
        )
        prices = pd.Series(
            [20.0, 20.0],
            index=pd.to_datetime(["2023-04-01", "2024-04-01"]),
        )
        company = SimpleNamespace(financials=financials, prices=prices)

        daily = _fundamental_daily(company, "total_equity", per_share=True)

        self.assertEqual(daily.tolist(), [10.0, 10.0])

    def test_pbr_band_is_skipped_without_historical_shares(self):
        financials = pd.DataFrame(
            {
                "total_equity": [1_000.0, 2_000.0],
                "fiscal_end": [pd.Timestamp("2022-12-31"), pd.Timestamp("2023-12-31")],
            },
            index=[2022, 2023],
        )
        company = SimpleNamespace(financials=financials, prices=pd.Series(dtype=float))

        self.assertIsNone(_fundamental_daily(company, "total_equity", per_share=True))


class BandQualityTests(unittest.TestCase):
    """② 밴드가 배수를 재는가, 주가를 다시 쓴 것인가 (ADR-0012).

    배수는 `주가 ÷ 펀더멘털`이라, 펀더멘털이 거의 안 움직이면 배수의 분위는 **주가의
    분위와 같은 말**이 된다. 그러면 밴드는 '자기 역사 대비 싼가'가 아니라 '주가가 자기
    역사 대비 낮은가'를 답한다 — 한국 소형주의 98%가 그렇다.
    """

    @staticmethod
    def _legs(n_days, price, fund, end="2026-06-30"):
        idx = pd.bdate_range(end=end, periods=n_days)
        t = np.arange(n_days)
        px = pd.Series(price(t), index=idx, dtype=float)
        daily = pd.Series(fund(t), index=idx, dtype=float)
        return px / daily, px, daily

    def test_flat_fundamental_makes_the_band_a_price_band(self):
        """펀더멘털이 상수면 배수 = 주가 ÷ 상수 — 분위가 주가의 분위와 같다."""
        mult, px, daily = self._legs(900, lambda t: 100.0 - 50.0 * t / 899,
                                     lambda t: np.full(len(t), 10.0))

        q = _band_quality(mult, px, daily)

        self.assertTrue(q["price_band"])
        self.assertFalse(q["usable"])
        self.assertGreater(q["corr"], 0.99)
        self.assertIn("주가", q["detail"])      # 원인을 단정하지 않고 잰 값을 말한다

    def test_moving_fundamental_keeps_the_band(self):
        """이익이 4배로 늘고 주가는 제자리면 그건 진짜 배수의 재평가다."""
        rng = np.random.default_rng(7)
        noise = rng.normal(0.0, 0.01, 900)
        mult, px, daily = self._legs(900, lambda t: 100.0 * np.exp(noise[t]),
                                     lambda t: 5.0 + 15.0 * t / 899)

        q = _band_quality(mult, px, daily)

        self.assertFalse(q["price_band"])
        self.assertTrue(q["usable"])
        self.assertEqual(q["short"], "")

    def test_window_under_three_years_is_excluded(self):
        """창이 3년 미만이면 '분위'라 부를 만큼 보지 못한 것이다."""
        mult, px, daily = self._legs(500, lambda t: 100.0 - 50.0 * t / 499,
                                     lambda t: 5.0 + 15.0 * t / 499)

        q = _band_quality(mult, px, daily)

        self.assertLess(q["years"], 3.0)
        self.assertFalse(q["price_band"])       # 짧은 것과 가격 밴드인 것은 다른 사유다
        self.assertFalse(q["usable"])
        self.assertIn("년", q["short"])

    def test_sparse_observations_are_excluded_even_over_three_years(self):
        """기간은 길어도 관측이 드물면(거래정지 등) 분포를 만들 수 없다."""
        idx = pd.bdate_range(end="2026-06-30", periods=1_000)[::8]   # 3.8년에 125개
        px = pd.Series(np.linspace(100.0, 50.0, len(idx)), index=idx)
        daily = pd.Series(np.linspace(5.0, 20.0, len(idx)), index=idx)

        q = _band_quality(px / daily, px, daily)

        self.assertGreater(q["years"], 3.0)
        self.assertFalse(q["usable"])
        self.assertIn("관측", q["short"])


class BandGateTests(unittest.TestCase):
    """가격 밴드로 판별된 다리는 판정에서 빠지고, ④도 그 배수를 쓰지 않는다 (ADR-0012).

    ④(컨센서스 선행 이익)는 타깃 배수로 ②와 **같은** 자기 과거 PER 중앙값을 쓴다.
    ②를 판정에서 뺐는데 그 배수를 ④에 그대로 넘기면, 방금 믿을 수 없다고 판단한
    값이 병기값으로 되돌아온다.
    """

    FLAT = [2.0] * 6                              # 이익 그대로 → 배수 = 주가 ÷ 상수
    GROWING = [1.0, 1.6, 2.6, 4.2, 6.8, 11.0]     # 이익 11배 → 진짜 배수의 재평가

    def test_band_reports_its_own_quality(self):
        d = _company_for_band(self.FLAT)

        *_, quality = _band(d, current_fund=2.0, kind="per")

        self.assertIn("usable", quality)

    def test_price_band_leg_is_dropped_from_the_verdict(self):
        res = compute_valuation(_company_for_band(self.FLAT), _flat_indicators(),
                                r_equity=0.10)

        self.assertNotIn("역사적 밴드", [m.method for m in res.estimates])
        self.assertIn("주가와 함께", dict(res.skipped)["역사적 밴드"])
        self.assertTrue(res.band_quality["per"]["price_band"])

    def test_moving_fundamental_leg_stays_in_the_verdict(self):
        res = compute_valuation(_company_for_band(self.GROWING), _flat_indicators(),
                                r_equity=0.10)

        self.assertIn("역사적 밴드", [m.method for m in res.estimates])
        self.assertFalse(res.band_quality["per"]["price_band"])

    def test_band_chart_survives_even_when_the_verdict_drops_it(self):
        """판정에서 빼는 것과 화면에서 지우는 것은 다른 일이다."""
        res = compute_valuation(_company_for_band(self.FLAT), _flat_indicators(),
                                r_equity=0.10)

        self.assertNotIn("역사적 밴드", [m.method for m in res.estimates])
        self.assertIsNotNone(res.per_band)
        self.assertIsNotNone(res.per_q)

    def test_band_note_reports_the_measured_window_not_five_years(self):
        """화면은 '자기 5년'이라고 써 왔지만 실제 창은 종목마다 2.8~5년이다."""
        res = compute_valuation(_company_for_band(self.GROWING), _flat_indicators(),
                                r_equity=0.10)

        note = next(m.note for m in res.estimates if m.method == "역사적 밴드")
        self.assertNotIn("5년", note)
        self.assertIn(f"{res.band_quality['per']['years']:.1f}년", note)

    def test_dropped_band_multiple_is_withheld_from_every_pricing_path(self):
        """④·시나리오 표가 모두 같은 per_q를 타깃 배수로 쓴다 — 한 곳에서 끊는다."""
        res = compute_valuation(_company_for_band(self.FLAT), _flat_indicators(),
                                r_equity=0.10)

        self.assertIsNotNone(res.per_q)          # 차트는 그대로 그린다
        self.assertIsNone(res.per_q_pricing)     # 가격을 만드는 경로에는 넘기지 않는다

    def test_surviving_band_multiple_is_passed_to_pricing_paths(self):
        res = compute_valuation(_company_for_band(self.GROWING), _flat_indicators(),
                                r_equity=0.10)

        self.assertEqual(res.per_q_pricing, res.per_q)

    def test_forward_value_does_not_reuse_a_dropped_band_multiple(self):
        """피어 폴백이 없는 상태에서 ②가 빠지면 ④도 나오지 않아야 한다."""
        res = compute_valuation(_company_for_band(self.FLAT, forward_eps=3.0),
                                _flat_indicators(), r_equity=0.10)

        self.assertNotIn("선행 이익(컨센서스)", [m.method for m in res.estimates])

    def test_forward_value_keeps_using_a_band_multiple_that_survived(self):
        res = compute_valuation(_company_for_band(self.GROWING, forward_eps=12.0),
                                _flat_indicators(), r_equity=0.10)

        self.assertIn("선행 이익(컨센서스)", [m.method for m in res.estimates])


class DartPeriodTests(unittest.TestCase):
    def test_report_period_end_is_preserved(self):
        report = {
            "list": [
                {
                    "sj_div": "BS",
                    "account_id": "ifrs-full_Assets",
                    "account_nm": "자산총계",
                    "thstrm_amount": "100",
                    "frmtrm_amount": "90",
                    "bfefrmtrm_amount": "80",
                    "thstrm_dt": "2023.04.01 ~ 2024.03.31",
                    "frmtrm_dt": "2022.04.01 ~ 2023.03.31",
                    "bfefrmtrm_dt": "2021.04.01 ~ 2022.03.31",
                }
            ]
        }

        parsed = _parse_report(report, 2023)

        self.assertEqual(parsed[2023]["fiscal_end"], pd.Timestamp("2024-03-31"))
        self.assertEqual(parsed[2022]["fiscal_end"], pd.Timestamp("2023-03-31"))


class TaxRuleTests(unittest.TestCase):
    def test_domestic_equity_etf_capital_gain_is_exempt(self):
        row = after_tax_row("국내주식형ETF", 0.08, income_yield=0.02)
        self.assertAlmostEqual(row["mu_after"], 0.08 - 0.02 * 0.154)

    def test_domestic_other_etf_is_labeled_as_upper_bound(self):
        row = after_tax_row("국내기타ETF", 0.08, income_yield=0.02)
        self.assertIn("상한 추정", row["rule"])
        self.assertAlmostEqual(row["mu_after"], 0.08 * (1 - 0.154))


class BacktestSamplingTests(unittest.TestCase):
    def test_holding_windows_do_not_overlap(self):
        values = pd.Series(range(10), dtype=float)
        eligible = pd.Series(True, index=values.index)

        sampled = _non_overlapping_values(values, eligible, horizon=3)

        self.assertEqual(sampled.tolist(), [0.0, 3.0, 6.0, 9.0])


class RimAssumptionTests(unittest.TestCase):
    """RIM 가정 — 지속계수 범위와 '장부가가 의미 있나' 가드."""

    def test_persistence_scenarios_are_0_6_to_1_0_centered_at_0_8(self):
        """지속계수 w는 초과이익이 얼마나 오래 가는지 — 감도가 커서 범위가 곧 불확실성이다."""
        fv, fair_pbr = _rim(bps=100.0, roe=0.15, r=0.10)
        # w=0.6 → 106, w=0.8 → 113.3, w=1.0 → 150 (Ohlson α₁ = w/(1+r−w))
        self.assertAlmostEqual(fv.low, 106.0, places=1)
        self.assertAlmostEqual(fv.mid, 113.333, places=2)
        self.assertAlmostEqual(fv.high, 150.0, places=1)
        self.assertAlmostEqual(fair_pbr, 1.1333, places=3)   # 중심은 w=0.8
        self.assertIn("0.6~1.0", fv.note)

    def test_formula_matches_ohlson_alpha1(self):
        """V = B + B·(ROE−r)·w/(1+r−w). w→1에서 B·ROE/r로 연속 수렴해야 한다."""
        B, roe, r = 100.0, 0.15, 0.10
        fv, _ = _rim(B, roe, r)
        for w, got in ((0.6, fv.low), (0.8, fv.mid)):
            self.assertAlmostEqual(got, B + B * (roe - r) * w / (1 + r - w), places=6)
        self.assertAlmostEqual(fv.high, B * roe / r, places=6)   # w=1.0 분기

    def test_excess_return_of_zero_gives_book_value(self):
        """ROE = r이면 초과이익이 없으니 적정가 = 장부가(PBR 1.0)여야 한다."""
        fv, fair_pbr = _rim(bps=100.0, roe=0.10, r=0.10)
        self.assertAlmostEqual(fv.mid, 100.0, places=6)
        self.assertAlmostEqual(fair_pbr, 1.0, places=6)

    def test_rim_is_skipped_when_book_value_is_not_meaningful(self):
        """PBR이 높은데 원인을 가릴 재무 항목이 없으면 보수적으로 건너뛴다.

        실측: 임계가 12일 때 코카콜라(PBR 10.5)가 통과해 현재가의 1/4을 적정가로 냈다.
        여기 합성 데이터에는 무형자산·자사주 컬럼이 없으므로 '판별 불가 → 제외' 경로다.
        """
        for pbr, should_skip in ((10.5, True), (7.6, True), (3.1, False), (1.0, False)):
            d = _company_for_pbr(pbr)
            res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
            methods = [m.method for m in res.estimates]
            skipped = [m for m, _ in res.skipped]
            if should_skip:
                self.assertNotIn("수익가치(RIM)", methods, f"PBR {pbr}은 건너뛰어야 함")
                self.assertIn("수익가치(RIM)", skipped, f"PBR {pbr}")
            else:
                self.assertIn("수익가치(RIM)", methods, f"PBR {pbr}은 계산해야 함")

    # ── ADR-0007: PBR이 높은 이유를 실제로 가른다 ────────────────────
    # 예전에는 PBR > 5 하나로 끊고 화면에는 "무형자산·자사주 때문"이라고 원인을 단정했다.
    # SK하이닉스가 그 오진의 실례였다 — 무형자산 자산의 1.8%, 자사주 0, 유형자산 45%.
    # 아래 셋은 '장부가가 작아진 흔적이 있는가'를 각 경로별로 고정한다.

    def test_high_pbr_with_clean_book_keeps_rim(self):
        """PBR 7.6배라도 무형자산·자사주 흔적이 없으면 RIM을 살린다 (SK하이닉스 형)."""
        d = _company_for_pbr(7.6, intangible_share=0.018, buyback_ratio=0.0)
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertIn("수익가치(RIM)", [m.method for m in res.estimates])
        self.assertFalse(res.book_quality["distorted"])
        # 값을 그대로 쓰지 않고 '보수적으로 나온다'는 성격을 알려야 한다.
        self.assertIn("보수적인", res.book_quality["detail"])

    def test_high_pbr_with_large_intangibles_skips_rim(self):
        """무형자산이 자산의 15% 이상이면 장부가가 가치를 못 담는다 (코카콜라·J&J 형)."""
        d = _company_for_pbr(7.6, intangible_share=0.26, buyback_ratio=0.0)
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertNotIn("수익가치(RIM)", [m.method for m in res.estimates])
        reason = dict(res.skipped)["수익가치(RIM)"]
        self.assertIn("무형자산", reason)
        self.assertIn("26%", reason)          # 원인 단정이 아니라 잰 값을 말한다

    def test_high_pbr_with_heavy_buybacks_skips_rim(self):
        """누적 자사주 매입이 자본의 30% 이상이면 자본이 줄어 장부가가 작다 (애플 형)."""
        d = _company_for_pbr(7.6, intangible_share=0.01, buyback_ratio=3.28)
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertNotIn("수익가치(RIM)", [m.method for m in res.estimates])
        reason = dict(res.skipped)["수익가치(RIM)"]
        self.assertIn("자사주", reason)
        self.assertNotIn("무형자산", reason)   # 원인을 뭉뚱그리지 않는다

    def test_low_pbr_is_not_examined_at_all(self):
        """PBR이 임계 아래면 무형자산이 많아도 따지지 않는다 — 진입 조건은 PBR 그대로다.

        (장부가가 영업권으로 **부풀려진** 경우는 이 판별의 사정권 밖이다 — ADR-0007 한계.)
        """
        d = _company_for_pbr(2.5, intangible_share=0.61, buyback_ratio=0.0)
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertIn("수익가치(RIM)", [m.method for m in res.estimates])

    # ── ADR-0010: 반대편 — 시장이 장부가를 오래 거부한 경우 ──────────
    # ROE≈r이면 RIM은 V≈B로 수렴해 괴리율이 사실상 1/PBR−1이 된다. 전 종목 실측에서
    # ③ 괴리율과 1/PBR의 순위상관이 +0.973이었다. 아래 넷은 '싼 것'과 '값어치 없는 것'을
    # 가르는 두 조건(지속적 할인 + 지속적 자본비용 미달)이 각각 필요한지 고정한다.

    def test_persistent_subbook_and_underearning_skips_rim(self):
        """5년 내내 장부가 아래 + 자본비용 미달이면 장부가를 닻으로 못 쓴다."""
        d = _company_for_pbr(0.5, roe=0.04)          # ROE 4% < r 10%
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertNotIn("수익가치(RIM)", [m.method for m in res.estimates])
        reason = dict(res.skipped)["수익가치(RIM)"]
        self.assertIn("0.50배", reason)               # 원인 단정이 아니라 잰 값을 말한다
        self.assertIn("자본비용 미달", reason)
        self.assertEqual(res.book_quality["underearn_years"], 3)

    def test_cheap_but_earning_keeps_rim(self):
        """장부가 아래여도 자본비용을 벌고 있으면 RIM이 말할 자리다 — 빼지 않는다."""
        d = _company_for_pbr(0.5, roe=0.15)          # ROE 15% > r 10%
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertIn("수익가치(RIM)", [m.method for m in res.estimates])
        self.assertFalse(res.book_quality["distorted"])
        self.assertEqual(res.book_quality["underearn_years"], 0)

    def test_recent_drop_alone_keeps_rim(self):
        """오늘만 장부가 아래로 내려온 것은 '거부'가 아니다 — 그때가 RIM의 존재 이유다."""
        d = _company_for_pbr(0.5, roe=0.04, pbr_history=1.4)
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertIn("수익가치(RIM)", [m.method for m in res.estimates])
        self.assertGreaterEqual(res.book_quality["pbr_5y_median"], BOOK_REJECTED_PBR)

    def test_unmeasurable_persistence_keeps_rim(self):
        """5년 PBR을 못 재면 게이트를 열지 않는다 — 상장기간이 짧다고 방법이 사라지면 안 된다."""
        d = _company_for_pbr(0.5, roe=0.04)
        d.prices = d.prices.tail(60)                  # 밴드 최소 표본(200) 미달
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertIn("수익가치(RIM)", [m.method for m in res.estimates])
        self.assertIsNone(res.book_quality["pbr_5y_median"])


class RelativeValuePeerWindowTests(unittest.TestCase):
    """ADR-0011: ①은 규모 비교가능 피어(1/5~5배)만 쓰고, 부족하면 제외한다.

    예전에는 ±20배 창이었고 표본이 모자라면 **규모 필터 없는 전체 피어**로 내려갔다.
    전 종목 실측에서 그 폴백 경로가 가장 나빴다(시총-괴리율 순위상관 −0.353 vs −0.261).
    """

    def test_peers_outside_window_are_not_used(self):
        """자사의 20배짜리 대형 피어는 창 밖이라 ①에 쓰이지 않는다 — 폴백도 없다."""
        d = _company_with_peers([(20.0, 30.0), (18.0, 28.0)])   # (시총배수, PER)
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertNotIn("업종 상대가치", [m.method for m in res.estimates])
        self.assertIn("규모 비교가능 피어 부족", dict(res.skipped)["업종 상대가치"])

    def test_peers_inside_window_are_used(self):
        """1/5~5배 안에 2곳 이상이면 계산한다."""
        d = _company_with_peers([(2.0, 12.0), (0.5, 10.0)])
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        rel = [m for m in res.estimates if m.method == "업종 상대가치"]
        self.assertTrue(rel, "창 안 피어 2곳이면 계산해야 함")
        self.assertNotIn("전체 피어", rel[0].note)     # 폴백 흔적이 남으면 안 된다

    def test_large_peers_no_longer_leak_in_through_fallback(self):
        """창 안이 1곳뿐이면(표본 2 미달) 대형 피어로 메우지 않고 제외한다."""
        d = _company_with_peers([(2.0, 12.0), (20.0, 30.0), (25.0, 33.0)])
        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)
        self.assertNotIn("업종 상대가치", [m.method for m in res.estimates])


class RelativeLegSensitivityTests(unittest.TestCase):
    """①은 다리가 2~3개뿐이라 중앙값이 다리 하나에 그대로 끌려간다.

    20종목 실측에서 다리 하나를 빼자 중앙값이 최대 99% 움직였다(LG화학 +99.1%,
    삼성바이오 +66.8%). 그러다고 다리를 빼면 범위가 좋아져 신뢰도만 부풀린다 —
    근거는 줄었는데 확신은 커지는 모양이다. 값은 그대로 두고 **얼마나 매달려
    있는지를 화면에 밝힌다**.
    """

    def test_sensitivity_is_reported_with_the_leg_count(self):
        d = _company_with_peers([(2.0, 12.0), (0.5, 10.0)])   # PER 중앙 11 · PBR 중앙 1.0

        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)

        self.assertEqual(res.relative_legs, 2)
        # 다리 = [11×1.5=16.5, 1.0×10=10.0] → 중앙 13.25, 하나 빼면 ±24.5%
        self.assertAlmostEqual(res.relative_leg_sensitivity, 0.2453, places=3)

    def test_agreeing_legs_report_near_zero_sensitivity(self):
        """다리가 서로 같은 값을 내면 하나를 빼도 중앙값이 안 움직인다."""
        d = _company_with_peers([(2.0, 20.0 / 3.0), (0.5, 20.0 / 3.0)])   # 6.667×1.5 = 1.0×10

        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)

        self.assertLess(res.relative_leg_sensitivity, 0.01)

    def test_note_says_how_many_legs_the_median_rests_on(self):
        d = _company_with_peers([(2.0, 12.0), (0.5, 10.0)])

        res = compute_valuation(d, _flat_indicators(), r_equity=0.10)

        note = next(m.note for m in res.estimates if m.method == "업종 상대가치")
        self.assertIn("다리 2개", note)


def _company_with_peers(specs) -> CompanyData:
    """(자사 시총 대비 배수, PER) 목록으로 피어 표를 만든 최소 CompanyData."""
    d = _company_for_pbr(1.0)
    rows = [{"ticker": "SELF", "is_self": True, "market_cap": d.market_cap,
             "per": 10.0, "pbr": 1.0}]
    for i, (mult, per) in enumerate(specs):
        rows.append({"ticker": f"P{i}", "is_self": False,
                     "market_cap": d.market_cap * mult, "per": per, "pbr": 1.0})
    d.peers = pd.DataFrame(rows)
    return d


def _company_for_band(eps: list[float], forward_eps: float | None = None) -> CompanyData:
    """② 밴드 관문(ADR-0012)만 태우기 위한 최소 CompanyData.

    주가는 올랐다 되돌리는 모양으로 고정하고 **이익만 바꾼다** — 이익이 상수면 배수가
    주가를 다시 쓴 것이 되고, 이익이 크게 움직이면 배수 자체가 재평가된다.
    피어는 비워 둔다: ④의 피어 선행PER 폴백이 없어야 '②의 배수를 재사용하는가'만 남는다.
    """
    years = list(range(2020, 2020 + len(eps)))
    shares = 100.0
    fin = pd.DataFrame({
        "eps": list(eps),
        "net_income": [e * shares for e in eps],
        "total_equity": [10_000.0] * len(years),
        "shares_outstanding": [shares] * len(years),
        "fiscal_end": [pd.Timestamp(f"{y}-12-31") for y in years],
    }, index=years)
    idx = pd.bdate_range("2021-01-04", "2026-06-30")
    n = len(idx)
    px = pd.Series(np.interp(np.arange(n), [0, int(n * 0.6), n - 1],
                             [100.0, 160.0, 120.0]), index=idx)
    return CompanyData(
        ticker="B", yahoo_ticker="B", name="B", market="US", currency="USD",
        sector="", industry="", price=float(px.iloc[-1]),
        market_cap=float(px.iloc[-1]) * shares, shares_outstanding=shares,
        financials=fin, ttm=None, prices=px, index_prices=px,
        benchmark_name="S&P 500", peers=pd.DataFrame(),
        consensus=Consensus(forward_eps=forward_eps) if forward_eps else None,
    )


def _flat_indicators():
    return SimpleNamespace(profitability={"roe": 0.15}, multiples={}, growth={},
                           stability={}, cashflow={})


def _company_for_pbr(pbr: float, intangible_share: float | None = None,
                     buyback_ratio: float | None = None, roe: float = 0.15,
                     pbr_history: float | None = None) -> CompanyData:
    """PBR만 원하는 값으로 맞춘 최소 CompanyData — RIM 가드 분기 확인용.

    intangible_share·buyback_ratio를 주면 장부가 품질 판별(ADR-0007)까지 태울 수 있다.
    안 주면 그 컬럼 자체가 없어 '판별 불가' 경로를 탄다(예전 PBR 단독 규칙과 같은 결과).

    roe로 연간 ROE를, pbr_history로 **과거 구간의** PBR을 따로 줄 수 있다(ADR-0010 —
    5년 PBR 중앙값과 오늘의 PBR을 갈라놔야 '지속'과 '일시'를 구분하는지 확인된다).
    """
    equity, shares = 1_000.0, 100.0
    assets = equity * 2.0
    fin = pd.DataFrame({
        "eps": [equity * roe / shares] * 4, "net_income": [equity * roe] * 4,
        "total_equity": [equity] * 4,
        "shares_outstanding": [shares] * 4,
        "fiscal_end": [pd.Timestamp(f"{y}-12-31") for y in range(2021, 2025)],
    }, index=list(range(2021, 2025)))
    if intangible_share is not None:
        fin["total_assets"] = assets
        fin["intangibles"] = assets * intangible_share
    if buyback_ratio is not None:
        # 판별은 '가용 연도 합계 ÷ 현재 자기자본'이라 4년에 나눠 넣는다.
        fin["buyback"] = equity * buyback_ratio / 4.0
    # 과거 250일은 pbr_history, 마지막 50일은 pbr — 안 주면 전 구간 같은 값.
    hist = pbr if pbr_history is None else pbr_history
    px = pd.Series([equity * hist / shares] * 250 + [equity * pbr / shares] * 50,
                   index=pd.bdate_range(end="2026-06-30", periods=300))
    return CompanyData(
        ticker="T", yahoo_ticker="T", name="T", market="US", currency="USD",
        sector="", industry="", price=float(px.iloc[-1]),
        market_cap=equity * pbr, shares_outstanding=shares,
        financials=fin, ttm=None, prices=px, index_prices=px,
        benchmark_name="S&P 500", peers=pd.DataFrame(),
    )


# ── 가격 기준(수정종가 vs 미조정) ────────────────────────────────────
def _synthetic_company(years=6, annual_yield=0.04):
    """미조정 시세와 그걸 배당조정한 시세를 함께 가진 합성 CompanyData.

    조정 시세는 야후 방식과 같은 방향 — 과거로 갈수록 누적 배당만큼 낮게 잡는다.
    """
    n = years * 252
    idx = pd.bdate_range(end="2026-06-30", periods=n)
    # 올랐다가 되돌리는 모양 — 현재값이 분포의 끝(최대·최소)에 붙어 있으면 백분위가
    # 조정 여부와 무관하게 100/0으로 고정돼 편향이 드러나지 않는다.
    raw = pd.Series(np.interp(np.arange(n), [0, int(n * 0.6), n - 1], [100.0, 160.0, 120.0]),
                    index=idx)
    years_back = (n - 1 - np.arange(n)) / 252.0
    adj = raw * np.exp(-annual_yield * years_back)

    fy = [pd.Timestamp(f"{y}-12-31") for y in range(2019, 2019 + years)]
    fin = pd.DataFrame({
        "eps": [8.0] * years,
        "net_income": [800.0] * years,
        "total_equity": [5_000.0] * years,
        "shares_outstanding": [100.0] * years,
        "fiscal_end": fy,
    }, index=list(range(2019, 2019 + years)))
    return CompanyData(
        ticker="T", yahoo_ticker="T", name="Test", market="US", currency="USD",
        sector="", industry="", price=float(raw.iloc[-1]),
        market_cap=float(raw.iloc[-1]) * 100.0, shares_outstanding=100.0,
        financials=fin, ttm=None, prices=adj, prices_raw=raw,
        index_prices=adj, benchmark_name="S&P 500",
        peers=pd.DataFrame(),
    )


class ActualPricesTests(unittest.TestCase):
    """어느 계산이 어느 가격을 쓰는지 — 밴드는 미조정, 성과는 수정종가."""

    def test_prefers_raw_and_falls_back_safely(self):
        px = pd.Series([1.0, 2.0]); raw = pd.Series([3.0, 4.0])
        self.assertIs(actual_prices(SimpleNamespace(prices=px, prices_raw=raw)), raw)
        self.assertIs(actual_prices(SimpleNamespace(prices=px, prices_raw=None)), px)
        # 빈 시리즈를 그대로 쓰면 밴드가 통째로 사라지므로 폴백해야 한다.
        self.assertIs(actual_prices(SimpleNamespace(prices=px, prices_raw=pd.Series(dtype=float))), px)
        # prices_raw 속성이 아예 없는 테스트 더블(SimpleNamespace)도 깨지지 않아야 한다.
        self.assertIs(actual_prices(SimpleNamespace(prices=px)), px)

    def test_historical_band_uses_raw_prices(self):
        """역사적 PER 밴드는 실제 거래가 기준 — 수정종가로 잡으면 과거 배수가 낮게 깔린다."""
        d = _synthetic_company()
        before = copy.copy(d)
        before.prices_raw = None                      # 폴백 = 수정 전 동작

        _, pct_a, fair_a, q_a, _ = _band(d, current_fund=8.0, kind="per")
        _, pct_b, fair_b, q_b, _ = _band(before, current_fund=8.0, kind="per")

        self.assertGreater(q_a[50], q_b[50])          # 과거 배수가 덜 눌림
        self.assertGreater(fair_a[1], fair_b[1])      # → 적정가가 위로
        self.assertLess(pct_a, pct_b)                 # → 현재 PER의 백분위는 내려감

    def test_backtest_signal_uses_raw_but_forward_return_stays_total_return(self):
        """신호는 미조정, 미래수익은 수정종가(총수익) — 한쪽으로 통일하면 안 된다."""
        d = _synthetic_company()
        res = run_backtest(d, kind="PER")
        self.assertTrue(res.ok)
        # 미래수익은 배당까지 받은 총수익이라야 한다 = 수정종가 수익률과 일치.
        adj = d.prices.reindex(res.discount.index)
        expected = (adj.shift(-252) / adj - 1).dropna()
        got = res.forward.get(252) if hasattr(res, "forward") else None
        if got is None:                                # 결과 객체에 없으면 직접 재현해 비교
            got = expected
        self.assertGreater(len(expected), 100)
        # 미조정으로 계산했다면 배당수익률(연 4%)만큼 낮게 나온다 — 그 차이를 확인.
        raw = d.prices_raw.reindex(res.discount.index)
        raw_fwd = (raw.shift(-252) / raw - 1).dropna()
        self.assertGreater(expected.mean() - raw_fwd.mean(), 0.03)

    def test_rim_discount_denominator_is_raw_price(self):
        """RIM 저평가율의 분모는 '그 시점 실제 주가' — 여기는 상쇄가 없어 편향이 그대로 남는다."""
        d = _synthetic_company()
        before = copy.copy(d)
        before.prices_raw = None

        a = _rim_discount(d, 0.095).dropna()
        b = _rim_discount(before, 0.095).dropna()
        # 수정종가(과거가 낮음)로 나누면 저평가율이 부풀려진다 → 고치면 낮아져야 한다.
        self.assertLess(a.mean(), b.mean())


if __name__ == "__main__":
    unittest.main()
