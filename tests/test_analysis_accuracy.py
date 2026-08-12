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
from src.analysis.valuation import (BOOK_REJECTED_PBR, RIM_PERSISTENCE,
                                    RIM_PERSISTENCE_CENTER, _band, _band_quality,
                                    _fundamental_daily, _rim, compute_valuation,
                                    rim_value)
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

    def test_persistence_scenarios_come_from_the_literature(self):
        """지속계수 세 점은 전부 문헌값이다(ADR-0039) — 0.21 Wang·Myers / 0.62 FF / 1.00 F&L.

        값을 손으로 적지 않고 상수에서 만든다. 적어 두면 상수를 고칠 때 조용히 썩는다.
        """
        self.assertEqual(RIM_PERSISTENCE, (0.21, 0.62, 1.00))
        self.assertEqual(RIM_PERSISTENCE_CENTER, 0.62)
        self.assertIn(RIM_PERSISTENCE_CENTER, RIM_PERSISTENCE)

        B, roe, r = 100.0, 0.15, 0.10
        fv, fair_pbr = _rim(bps=B, roe=roe, r=r)
        want = {w: rim_value(B, roe, r, w) for w in RIM_PERSISTENCE}
        self.assertAlmostEqual(fv.low, min(want.values()), places=9)
        self.assertAlmostEqual(fv.high, max(want.values()), places=9)
        self.assertAlmostEqual(fv.mid, want[RIM_PERSISTENCE_CENTER], places=9)
        self.assertAlmostEqual(fair_pbr, fv.mid / B, places=9)
        # 실측 고정 — 상수가 바뀌면 여기서 걸려 ADR을 함께 고치게 된다.
        self.assertAlmostEqual(fv.mid, 106.458333, places=5)
        self.assertIn("0.21~1.00", fv.note)

    def test_formula_matches_ohlson_alpha1(self):
        """V = B + B·(ROE−r)·w/(1+r−w). w→1에서 B·ROE/r로 연속 수렴해야 한다."""
        B, roe, r = 100.0, 0.15, 0.10
        for w in RIM_PERSISTENCE:
            if w >= 1.0:
                continue
            self.assertAlmostEqual(rim_value(B, roe, r, w),
                                   B + B * (roe - r) * w / (1 + r - w), places=9)
        self.assertAlmostEqual(rim_value(B, roe, r, 1.0), B * roe / r, places=9)
        # w→1 연속성 — 극한 분기가 다른 식이 아니라는 확인.
        # α₁ = w/(1+r−w)는 w→1에서 1/r로 가는데 **수렴이 느리다**(w=0.9999에서 0.055 차이).
        # places=로 조이면 '연속성'이 아니라 수렴 속도를 재게 된다.
        self.assertAlmostEqual(rim_value(B, roe, r, 0.9999), B * roe / r, delta=0.1)

    def test_backtest_reconstruction_uses_the_same_persistence(self):
        """복원 백테스트와 판정이 **같은 w**를 쓴다.

        `backtest.py`가 0.9를 따로 박아 두어 한 저장소 안에 w가 둘이었다(판정 0.8).
        ADR-0009가 옛 백테스트를 접은 이유 중 하나가 그것이라, 다시 갈라지면 실패시킨다.
        """
        import inspect

        from src.analysis import backtest as bt
        # **본문만 본다.** docstring은 옛 0.9를 이력으로 설명하고 있어서 통째로 검사하면
        # 그 설명 때문에 실패한다 — 지우면 왜 통일했는지가 사라지므로 검사 쪽을 좁힌다.
        # `getdoc()`은 들여쓰기를 벗긴 문자열이라 원본과 안 맞는다 — 삼중따옴표로 자른다.
        body = inspect.getsource(bt._rim_discount).split('"""')[-1]
        self.assertIn("RIM_PERSISTENCE_CENTER", body)
        self.assertNotIn("0.9", body)
        # 두 경로가 같은 값을 내는가 — 적정 PBR은 B=1에서의 rim_value다.
        roe, r = 0.15, 0.10
        _fv, fair_pbr = _rim(bps=100.0, roe=roe, r=r)
        self.assertAlmostEqual(fair_pbr, rim_value(1.0, roe, r, RIM_PERSISTENCE_CENTER),
                               places=9)

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


class BasisShareTests(unittest.TestCase):
    """판정이 무엇에 기대는가 — 실효 절대/상대 비중 (ADR-0018)."""

    def test_classification_covers_every_fundamental_method(self):
        from src.analysis.valuation import (FUNDAMENTAL_METHODS, INTRINSIC_METHODS,
                                            RELATIVE_METHODS)

        # 어느 쪽에도 없는 방법이 생기면 화면의 두 비중을 더해도 1이 안 된다.
        # EPV가 들어올 때 여기서 걸린다(ADR-0016 결정 2).
        self.assertEqual(set(FUNDAMENTAL_METHODS),
                         set(INTRINSIC_METHODS) | set(RELATIVE_METHODS))
        self.assertEqual(set(INTRINSIC_METHODS) & set(RELATIVE_METHODS), set())

    def test_shares_sum_to_one_and_use_effective_weights(self):
        from src.analysis.valuation import INTRINSIC_METHODS, RELATIVE_METHODS

        # ③이 빠진 종목: 명목 가중이 아니라 재정규화된 실효 가중이라 절대가 정확히 0이다.
        # ②가 판정에서 빠지며(ADR-0035) 이 자리의 가중도 ①⑤ 둘로만 선다 — 이 표에
        # ②를 남겨 두면 상대가치 합이 1에 못 미쳐, **분류가 판정 방법을 남김없이
        # 가른다**는 계약이 깨진 것을 이 테스트가 잡는다.
        weights = {"업종 상대가치": 0.5, "정규화 이익": 0.5}
        intrinsic = sum(w for m, w in weights.items() if m in INTRINSIC_METHODS)
        relative = sum(w for m, w in weights.items() if m in RELATIVE_METHODS)
        self.assertEqual(intrinsic, 0.0)
        self.assertAlmostEqual(relative, 1.0, places=9)

    def test_no_consensus_row_when_there_is_no_consensus_estimate(self):
        """④가 없으면 병기 값을 내지 않는다 — 화면이 없는 것을 들었다고 말하게 된다.

        `if with_fwd:`만 보던 동안, ④가 없는 종목에서 `with_fwd == core`라 병기 값이
        펀더멘털과 **똑같이** 잡혔다. 화면은 `fair_mid_consensus != null`만 보고 행을
        세우므로, 같은 숫자가 두 줄 서고 아래 줄이 *"컨센서스 반영 (④ 포함)"*이라
        적혔다. 애널리스트 커버리지가 없는 소형주에서 흔한 상태다.

        병기 값은 펀더멘털과 **나란히 놓고 차이를 읽으라고** 있는 값이다(ADR-0006).
        같은 재료로 서면 낼 이유가 없다.
        """
        from src.analysis.valuation import CONSENSUS_METHOD, compute_valuation

        # 피어는 있고 forward_eps는 없다 → ①③은 서고 ④는 skipped
        res = compute_valuation(
            _company_with_peers([(1.0, 12.0), (1.2, 14.0), (0.8, 9.0), (1.1, 20.0)]),
            _flat_indicators(), r_equity=0.09)
        self.assertNotIn(CONSENSUS_METHOD, {e.method for e in res.estimates},
                         "픽스처가 ④를 세웠다 — 이 테스트가 무의미해진다")
        self.assertIsNotNone(res.fair_mid, "판정은 나와야 한다")
        self.assertIsNone(res.fair_mid_consensus,
                          "④가 없는데 병기 값이 잡혔다 — 화면이 '(④ 포함)'이라 적는다")
        self.assertIsNone(res.gap_consensus)
        self.assertIsNone(res.verdict_consensus)

    def test_screen_notes_derive_the_method_marks_instead_of_writing_them(self):
        """화면에 나가는 문장의 ①③⑤는 **상수에서 유도**돼야 한다.

        손으로 적으면 축이 바뀔 때마다 썩는다 — 실제로 "①②③"이 여덟 자리에 적힌 채
        실제 구성(①②③⑤)과 어긋나 있었고, ②를 빼면서 또 어긋날 뻔했다(PR #139).
        이 테스트가 지키는 것은 문구가 아니라 **유도한다는 사실**이다: 축 목록을 바꾸면
        문장도 따라 바뀌어야 한다.
        """
        from src.analysis import valuation as V

        self.assertEqual(V.marks_of(V.FUNDAMENTAL_METHODS), "①③⑤")
        # 번호 순으로 정렬한다 — 들어온 순서를 따라가면 화면마다 다르게 읽힌다
        self.assertEqual(V.marks_of(["정규화 이익", "업종 상대가치"]), "①⑤")
        # 축 목록을 바꾸면 문장이 따라온다. 이것이 깨지면 어딘가 손으로 적혀 있다는 뜻이다
        original = V.FUNDAMENTAL_METHODS
        try:
            V.FUNDAMENTAL_METHODS = ("업종 상대가치", "역사적 밴드")
            self.assertEqual(V.marks_of(V.FUNDAMENTAL_METHODS), "①②")
        finally:
            V.FUNDAMENTAL_METHODS = original

    def test_excluded_from_verdict_carries_the_reason_not_just_the_name(self):
        """값은 냈는데 판정에 안 들어간 방법은 **사유와 함께** 나가야 한다.

        화면은 가중 칸이 빈 방법에 '판정 제외 · 참고' 배지를 붙이는데, 사유가 없으면
        배지가 일반 문구밖에 못 쓴다. 그리고 사유를 화면에 손으로 적으면 판정 구성이
        바뀔 때 조용히 썩는다 — ①②③이 여덟 자리에 하드코딩돼 실제 구성과 어긋나 있던
        것이 정확히 그 사고다. 그래서 사유는 ADR과 같은 파일(파이썬)에서 나간다.

        `skipped`와 섞이면 안 된다: 그쪽은 **값이 없는** 것이고 이쪽은 **값은 있는** 것이다.
        """
        from src.analysis.valuation import FUNDAMENTAL_METHODS, compute_valuation

        # ②와 ③이 함께 서는 픽스처 — ②는 계산되지만 판정에는 안 들어간다(ADR-0035).
        res = compute_valuation(_company_for_band([1.0, 1.6, 2.6, 4.2, 6.8, 11.0]),
                                _flat_indicators(), r_equity=0.09)
        got = dict(res.excluded_from_verdict)
        self.assertIn("역사적 밴드", got, "값을 낸 ②가 제외 목록에 없다")
        self.assertNotIn("역사적 밴드", res.weights or {}, "②가 판정 가중에 남아 있다")
        self.assertIn("0035", got["역사적 밴드"], "사유가 근거 ADR을 가리키지 않는다")

        # 계산된 방법만 들어간다 — 값이 없어 건너뛴 것은 `skipped`의 몫이다
        computed = {e.method for e in res.estimates}
        self.assertTrue(set(got) <= computed,
                        "값을 내지 않은 방법이 제외 목록에 섞였다")
        # **`skipped`와 겹치지 않는다.** 화면은 값이 없는 행을 `skipped` 쪽 '제외' 행으로
        # 그리므로, 겹치면 그 사유가 어디에도 안 뜬다(PR #139가 기대는 계약이다).
        self.assertEqual(set(got) & {m for m, _ in res.skipped}, set(),
                         "같은 방법이 skipped와 제외 목록에 동시에 있다")
        # 판정에 든 방법은 여기 없어야 한다
        self.assertEqual(set(got) & set(res.weights or {}), set())
        self.assertEqual(set(got) & set(FUNDAMENTAL_METHODS), set())

    def test_the_band_cannot_carry_the_verdict_through_the_fallback(self):
        """①③⑤가 하나도 없을 때 **②가 판정을 끌면 안 된다** (ADR-0035).

        폴백이 `core or res.estimates`였다. ②가 판정 축이던 동안에는 둘이 같았다 —
        `res.estimates`에서 core를 빼면 ④뿐이었으니까. ②를 core에서 빼자 그 자리가
        벌어져서, **폴백을 타는 종목에서만 ②가 판정을 끌게 됐다.** 예측력이 없다고
        판단해 뺀 축이 뒷문으로 돌아온다.

        여기서 함께 못 박는 것: 판정에 실제로 쓰인 방법은 제외 목록에 오르지 않는다.
        기준이 상수 목록이면 폴백 종목에서 ④가 **가중도 있고 제외 목록에도 오른다.**
        """
        from src.analysis.valuation import compute_valuation

        # 이익이 11배로 커지는 픽스처는 ②③이 선다. ③을 장부가 게이트로 떨어뜨리면
        # 판정 축이 하나도 안 남고 ②만 값을 가진 상태가 된다 — 폴백이 타는 자리다.
        d = _company_for_band([1.0, 1.6, 2.6, 4.2, 6.8, 11.0])
        d.financials = d.financials.copy()
        d.financials["total_equity"] = 1.0        # PBR을 게이트 위로 밀어 ③을 뺀다
        res = compute_valuation(d, _flat_indicators(), r_equity=0.09)

        computed = {e.method for e in res.estimates}
        self.assertIn("역사적 밴드", computed, "픽스처가 ②를 못 세웠다 — 이 테스트가 무의미해진다")
        self.assertNotIn("역사적 밴드", res.weights or {},
                         "②가 폴백을 타고 판정으로 돌아왔다")
        weights = set(res.weights or {})
        excluded = {m for m, _ in res.excluded_from_verdict}
        self.assertEqual(weights & excluded, set(),
                         f"판정에 쓰인 방법이 제외 목록에도 있다: {weights & excluded}")

    def test_all_axes_standing_leave_rim_a_minority(self):
        from src.analysis.valuation import (FUNDAMENTAL_METHODS, INTRINSIC_METHODS,
                                            METHOD_WEIGHTS)

        # 축이 다 서도 절대가치는 소수다 — 이것이 실측 '평균 9.3%'의 상한이다.
        # **②가 판정에서 빠지며(ADR-0035) 이 상한이 16.7% → 23.1%로 올랐다.** 빠진
        # 25%가 남은 셋에 나뉘어 붙기 때문이고, 그중 ③만이 절대가치 축이다. 도구가
        # 시장 배수에 덜 기대게 되는 방향인데, **의도한 것이 아니라 따라온 것**이라
        # 여기 적어 둔다 — 다음에 이 수를 보는 사람이 근거로 삼지 않도록.
        total = sum(METHOD_WEIGHTS[m] for m in FUNDAMENTAL_METHODS)
        intrinsic = sum(METHOD_WEIGHTS[m] for m in INTRINSIC_METHODS)
        self.assertAlmostEqual(intrinsic / total, 0.2308, places=3)


class ConfidenceGradeTests(unittest.TestCase):
    """신뢰도 = min(흩어짐 등급, 실질 축 수 상한) (ADR-0022)."""

    def setUp(self):
        from src.analysis.valuation import confidence_grade
        self.grade = confidence_grade

    def test_independent_methods_keep_the_spread_grade(self):
        # 겹치지 않으면 등급이 바뀌면 안 된다. 이 작업은 부풀린 것을 되돌리는 것이지
        # 전부 깎는 것이 아니다.
        for disp, want in ((0.05, "높음"), (0.25, "중간"), (0.60, "낮음")):
            final, spread, cap = self.grade(disp, 4, n_eff=4.0, capped=True)
            self.assertEqual(spread, want)
            self.assertEqual(final, want, f"disp={disp}에서 독립인데 등급이 깎였다")
            self.assertEqual(cap, "높음")

    def test_overlapping_methods_are_capped_even_when_tightly_clustered(self):
        # 이 작업의 핵심. 값이 거의 같아도(disp 0.01) 실질 축이 하나면 '높음'을 줄 수 없다.
        # AAPL이 정확히 이 모양이다 — ①과 ⑤가 같은 적정 PER을 써서 값이 가깝다(ADR-0015).
        final, spread, cap = self.grade(0.01, 2, n_eff=1.05, capped=True)
        self.assertEqual(spread, "높음")
        self.assertEqual(cap, "낮음")
        self.assertEqual(final, "낮음")

    def test_partial_overlap_caps_at_middle(self):
        # **픽스처를 2.4에서 2.0으로 내렸다(ADR-0036).** 상한을 절대 개수가 아니라
        # 독립분 (n_eff−1)/(n−1)으로 재게 되면서, 축이 셋일 때 2.4는 독립분 0.70으로
        # '부분적으로 겹침'이 아니라 **꽤 독립**이다(문턱 0.600). 이 테스트가 재려는
        # 것은 '중간'이라는 숫자가 아니라 **부분 겹침이 중간으로 잡히는가**이므로,
        # 새 자에서 그 뜻에 해당하는 값으로 옮긴다 — 셋 중 2.0이면 독립분 0.50이다.
        final, _s, cap = self.grade(0.01, 3, n_eff=2.0, capped=True)
        self.assertEqual(cap, "중간")
        self.assertEqual(final, "중간")

    def test_two_nearly_identical_methods_are_not_half_independent(self):
        """축이 둘일 때 `n_eff/n`을 쓰면 안 되는 이유 — 최솟값이 0.5다.

        두 방법이 완전히 같은 자여도 `n_eff/n = 0.5`라, '절반은 독립'인 것처럼 나온다.
        AAPL이 그 모양이고(①⑤가 같은 적정 PER, ADR-0015) **ADR-0022가 만들어진 이유가
        정확히 그 종목**이다. 만들다 실제로 `n_eff/n`을 먼저 넣었고, 이 자리에서
        AAPL이 '중간'으로 올라가는 것을 보고 독립분으로 바꿨다.
        """
        final, spread, cap = self.grade(0.01, 2, n_eff=1.05, capped=True)
        self.assertEqual(spread, "높음", "흩어짐만 보면 값이 붙어 있다")
        self.assertEqual(cap, "낮음", "사실상 한 축인데 상한이 안 걸렸다")
        self.assertEqual(final, "낮음")

    def test_cap_never_raises_a_grade(self):
        # 상한은 내리기만 한다. 흩어짐이 크면 축이 아무리 독립이어도 '낮음'이다.
        final, _s, cap = self.grade(0.90, 4, n_eff=4.0, capped=True)
        self.assertEqual(cap, "높음")
        self.assertEqual(final, "낮음")

    def test_unknown_correlation_disables_the_cap(self):
        # 상관을 모르면 상한을 걸지 않는다 — 지어낸 값으로 깎지 않는다(ADR-0011).
        final, _s, cap = self.grade(0.01, 3, n_eff=1.0, capped=False)
        self.assertEqual(cap, "높음")
        self.assertEqual(final, "높음")

    def test_single_method_is_low_as_before(self):
        # 현행 동작 — 방법이 1개면 흩어짐을 보지 않고 '낮음'이다. 바뀌면 안 된다.
        self.assertEqual(self.grade(0.0, 1, n_eff=1.0, capped=True)[0], "낮음")
        self.assertEqual(self.grade(None, 3, n_eff=3.0, capped=True)[0], "낮음")

    def test_result_exposes_both_grades_so_the_screen_can_match_number_to_text(self):
        """`dispersion`이 뜻하는 등급과 최종 등급을 **둘 다** 내보내야 한다.

        화면은 ±%를 찍고 설명을 고른다. 설명을 **최종 등급**으로 고르면, 상한이 등급을
        내린 종목에서 *"±10% … 다소 흩어져 있습니다(±15~35%)"*처럼 숫자와 문장이
        모순된다(PR #130이 찾은 버그). 그래서 `confidence_spread`가 필요하다.

        등급 문자열을 파이썬이 내보내는 이유는 임계(0.15/0.35)를 JS에 옮겨 적지 않기
        위해서다 — 같은 수식이 두 언어에 사는 것이 #84·ADR-0019가 잡은 문제다.
        """
        # **픽스처를 ②에서 ①로 갈았다(ADR-0035).** 예전에는 이익이 11배로 커지는
        # `_company_for_band`가 ②③을 함께 세웠는데, ②가 판정에서 빠지자 ③ 하나만 남아
        # 흩어짐이 아예 계산되지 않았다 — 테스트가 조용히 무의미해지는 대신 **빨갛게**
        # 터진 자리다. 피어를 준 픽스처는 ①③이 서서 흩어짐이 실제로 나온다(실측 0.117).
        res = compute_valuation(
            _company_with_peers([(1.0, 12.0), (1.2, 14.0), (0.8, 9.0), (1.1, 20.0)]),
            _flat_indicators(), r_equity=0.09)
        self.assertIsNotNone(res.dispersion, "픽스처가 흩어짐을 못 냈다 — 이 테스트가 무의미해진다")
        self.assertIn(res.confidence_spread, ("높음", "중간", "낮음"))
        self.assertIn(res.confidence_cap, ("높음", "중간", "낮음"))
        # 최종 등급은 둘 중 낮은 쪽이어야 한다 — 화면이 이 관계를 전제로 문장을 만든다
        order = ["낮음", "중간", "높음"]
        self.assertEqual(res.confidence,
                         min(res.confidence_spread, res.confidence_cap,
                             key=order.index))

    def test_spread_grade_follows_the_dispersion_not_the_cap(self):
        # 상한이 등급을 내려도 `confidence_spread`는 흩어짐이 낸 등급을 그대로 유지해야
        # 한다. 이것이 깨지면 화면은 다시 ±%와 어긋나는 문장을 쓰게 된다.
        _final, spread, cap = self.grade(0.01, 2, n_eff=1.05, capped=True)
        self.assertEqual(spread, "높음", "흩어짐 등급이 상한에 오염됐다")
        self.assertEqual(cap, "낮음")

    def test_empty_rho_table_is_a_no_op(self):
        # METHOD_RHO를 채우기 전에는 아무 등급도 바뀌면 안 된다. 표를 채우는 것이
        # **이 기능을 켜는 스위치**라는 뜻이고, 잘못 채우면 그때 드러난다.
        from src.analysis.warranted import METHOD_RHO, effective_axes

        if not METHOD_RHO:
            n_eff, capped = effective_axes(["a", "b", "c"], "KR")
            self.assertFalse(capped)
            self.assertEqual(self.grade(0.01, 3, n_eff, capped)[0], "높음")


class MeasuredRhoTests(unittest.TestCase):
    """실측 상관표가 실제로 상한을 켜는가 (ADR-0022).

    위 `ConfidenceGradeTests`는 산식을 지어낸 값으로 검증한다. 여기서는 **저장소에
    들어 있는 실측 표**를 그대로 써서, 표가 비거나 쌍이 하나 빠졌을 때 상한이 조용히
    꺼지는 것을 잡는다 — `effective_axes`가 모르는 쌍을 만나면 `capped=False`를
    돌려주므로, 표가 망가져도 예외 없이 **아무 일도 안 일어나는 쪽**으로 실패한다.
    """

    def setUp(self):
        from src.analysis.valuation import FUNDAMENTAL_METHODS
        from src.analysis.warranted import METHOD_RHO
        self.methods = sorted(FUNDAMENTAL_METHODS)
        self.table = METHOD_RHO

    def test_both_markets_are_measured(self):
        # 미국이 빠져 있던 동안 미국 종목은 상한이 아예 안 걸렸다(옛 산식 그대로).
        self.assertEqual(sorted(self.table), ["KR", "US"])

    def test_every_pair_is_present_so_the_cap_actually_turns_on(self):
        from src.analysis.warranted import effective_axes

        for market in self.table:
            with self.subTest(market=market):
                n_eff, capped = effective_axes(self.methods, market)
                self.assertTrue(capped, f"{market}: 쌍이 빠져 상한이 꺼졌다")
                self.assertLess(n_eff, len(self.methods))

    def test_markets_are_measured_separately_not_copied(self):
        # 한 시장 값을 다른 시장에 옮겨 쓰지 않는다(ADR-0017). ①↔⑤가 KR +0.263 대
        # US +0.763, ③↔⑤가 KR −0.015 대 US +0.723으로 갈린다 — 복사했다면 신뢰도가
        # 통째로 틀렸을 크기의 차이다.
        self.assertNotEqual(self.table["KR"], self.table["US"])
        pair = ("업종 상대가치", "정규화 이익")
        self.assertGreater(self.table["US"][pair] - self.table["KR"][pair], 0.4)

    def test_aapl_shape_is_no_longer_high(self):
        """ADR-0022가 성패 판정으로 걸었던 기준. 이 표를 채운 이유가 이것이다.

        AAPL은 ①과 ⑤만 서는데 둘이 **같은 적정 PER**을 쓴다(ADR-0015). 값이 가까워
        흩어짐은 '높음'을 주지만(실측 disp 0.121 · 199 대 156), 실질 축은 1개 남짓이다.
        """
        from src.analysis.valuation import confidence_grade
        from src.analysis.warranted import effective_axes

        n_eff, capped = effective_axes(["업종 상대가치", "정규화 이익"], "US")
        final, spread, _cap = confidence_grade(0.121, 2, n_eff, capped)
        self.assertEqual(spread, "높음", "흩어짐만 보면 여전히 '높음'이다")
        self.assertLess(n_eff, 2.0)
        self.assertEqual(final, "낮음")


class RankAllowedWidthTests(unittest.TestCase):
    """가중치 폭이 **문헌 순위를 지키는** 범위인가 (ADR-0041).

    이 축의 위험은 값이 틀리는 것이 아니라 **주장과 값이 어긋나는 것**이다. 화면은
    *"문헌 순위가 허용하는 범위에서"*라고 적는데, 인계문이 쓰라던 두 탐침(동일가중 ·
    ③가중 2배)은 재정규화하면 각각 순위를 **동률**로 만들고 **역전**시킨다. 그 값으로
    저 문장을 쓰면 거짓이 되므로, 그 사실 자체를 테스트가 지킨다.
    """

    def _fv(self, method, mid):
        from src.analysis.valuation import FairValue
        return FairValue(method=method, low=mid * 0.9, mid=mid, high=mid * 1.1)

    def _trio(self, one=120.0, three=60.0, five=100.0):
        return [self._fv("업종 상대가치", one),
                self._fv("수익가치(RIM)", three),
                self._fv("정규화 이익", five)]

    def test_handoff_probes_break_the_ranking_they_claim(self):
        """인계문의 두 탐침이 왜 폭의 근거가 될 수 없는가 — 이것이 ADR-0041의 출발점이다."""
        from src.analysis.valuation import METHOD_WEIGHTS

        w1, w3 = METHOD_WEIGHTS["업종 상대가치"], METHOD_WEIGHTS["수익가치(RIM)"]
        self.assertLess(w3, w1, "출발점: 문헌 순위는 ③ < ①⑤ (강부등호)")

        # 동일가중 — ③이 ①⑤와 **동률**이 된다(경계 밖).
        self.assertEqual(1 / 3, 1 / 3)
        # ③가중 2배 → 재정규화하면 ③이 **최상위로 역전**된다.
        raw = {"업종 상대가치": w1, "수익가치(RIM)": w3 * 2, "정규화 이익": w1}
        tot = sum(raw.values())
        doubled = {k: v / tot for k, v in raw.items()}
        self.assertGreater(doubled["수익가치(RIM)"], doubled["업종 상대가치"],
                           "③가중 2배는 순위를 역전시킨다 — '순위가 허용하는 범위'가 아니다")

    def test_endpoints_really_are_the_extremes(self):
        """양 끝만 재는 것이 정당한가 — f(ρ)의 단조성을 촘촘한 훑기로 확인한다.

        단조성이 깨지면 최소·최대가 구간 **안쪽**에 생겨 화면의 폭이 조용히 좁아진다.
        """
        from src.analysis.valuation import METHOD_WEIGHTS, _rank_allowed_span

        for one, three, five in [(120, 60, 100), (60, 120, 80), (100, 100, 100),
                                 (50, 400, 70), (300, 10, 290)]:
            with self.subTest(legs=(one, three, five)):
                est = self._trio(one, three, five)
                lo, hi, _lower = _rank_allowed_span(est)
                top = [e.mid for e in est if METHOD_WEIGHTS[e.method] == 0.25]
                bot = [e.mid for e in est if METHOD_WEIGHTS[e.method] == 0.15]
                swept = [(sum(top) + rho * sum(bot)) / (len(top) + rho * len(bot))
                         for rho in np.linspace(0.0, 1.0, 2001)]
                self.assertAlmostEqual(lo, min(swept), places=9)
                self.assertAlmostEqual(hi, max(swept), places=9)

    def test_equal_weight_is_one_end_not_a_separate_number(self):
        """ρ=1이 곧 동일가중이다 — 화면이 두 값을 따로 말하면 같은 사실이 두 번 산다."""
        from src.analysis.valuation import _rank_allowed_span

        est = self._trio()
        lo, hi, _ = _rank_allowed_span(est)
        equal = float(np.mean([e.mid for e in est]))
        self.assertIn(round(equal, 9), (round(lo, 9), round(hi, 9)))

    def test_current_weights_sit_inside_the_span(self):
        """지금 화면에 뜨는 값이 폭 **안**에 있어야 한다 — 밖이면 문장이 자기를 부정한다."""
        from src.analysis.valuation import _rank_allowed_span, _weighted

        for legs in [(120, 60, 100), (60, 120, 80), (50, 400, 70)]:
            with self.subTest(legs=legs):
                est = self._trio(*legs)
                lo, hi, _ = _rank_allowed_span(est)
                _l, mid, _h, _w = _weighted(est)
                self.assertGreaterEqual(mid, lo - 1e-9)
                self.assertLessEqual(mid, hi + 1e-9)

    def test_one_tier_leaves_no_freedom(self):
        """①⑤만 서면 문헌이 자유도를 안 남긴다 — 폭을 지어내면 안 된다."""
        from src.analysis.valuation import _rank_allowed_span

        self.assertIsNone(_rank_allowed_span(
            [self._fv("업종 상대가치", 120), self._fv("정규화 이익", 100)]))
        self.assertIsNone(_rank_allowed_span([self._fv("업종 상대가치", 120)]))
        self.assertIsNone(_rank_allowed_span([self._fv("수익가치(RIM)", 60)]))
