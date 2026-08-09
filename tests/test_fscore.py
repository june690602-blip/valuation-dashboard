"""Piotroski F-Score 순수 함수 테스트.

실제 종목 전수 측정과 가설 검정은 백테스트 쪽이 한다(B1 개정 4). 여기서는 **산식과
못 세우는 조건**만 본다 — 신호를 못 세울 때 0점을 주지 않는 것이 이 모듈의 핵심이다
(0으로 두면 '데이터 없음'이 '나쁨'으로 읽힌다, ADR-0011).

기초 총자산 정규화를 쓰므로 **t년 점수는 회계연도 셋(t·t−1·t−2)을 요구**한다.
아래 픽스처가 3년인 이유다.
"""
from __future__ import annotations

import unittest
import unittest.mock

import pandas as pd

from src.analysis.fscore import (EQ_OFFER_TOL, FSCORE_MIN_SIGNALS,
                                 SIGNAL_NAMES, fscore, missing_inputs)

# 9개 신호가 **전부 1점**이 되는 3개년. 아래 테스트들은 이 프레임에서 한 항목씩만
# 뒤집어 그 신호가 실제로 그 항목을 보는지 확인한다.
#   index      = 2023 · 2024 · 2025 (과거→최신)
#   기초자산    = t 2024말(1,000) · t−1 2023말(900)
#   ROA        = 120/1000 = 0.120  >  90/900 = 0.100   → ΔROA 1
#   CFO        = 150/1000 = 0.150 > 0                  → CFO 1, ACCRUAL(150>120) 1
#   차입금비율  = 200/1000 = 0.200 <  250/900 = 0.278   → ΔLEVER 1 (줄었다)
#   유동비율    = 600/300 = 2.00  >  500/400 = 1.25     → ΔLIQUID 1
#   주식수      = 100 = 100                            → EQ_OFFER 1
#   매출총이익률 = 400/1000 = 0.40 >  300/900 = 0.333   → ΔMARGIN 1
#   자산회전율  = 1000/1000 = 1.00 >  900/900 = 1.00 ✗  → 아래에서 매출을 키워 1로 만든다
_GOOD = {
    "total_assets":        [800.0, 900.0, 1000.0],
    "net_income":          [50.0, 90.0, 120.0],
    "ocf":                 [60.0, 100.0, 150.0],
    "revenue":             [800.0, 900.0, 1100.0],
    "gross_profit":        [240.0, 300.0, 440.0],
    "total_debt":          [260.0, 250.0, 200.0],
    "current_assets":      [400.0, 500.0, 600.0],
    "current_liabilities": [400.0, 400.0, 300.0],
    "shares_outstanding":  [100.0, 100.0, 100.0],
}


def _fin(**overrides) -> pd.DataFrame:
    """3개년 재무 프레임. 키워드로 열 하나를 갈아끼운다."""
    data = {k: list(v) for k, v in _GOOD.items()}
    data.update({k: list(v) for k, v in overrides.items()})
    return pd.DataFrame(data, index=[2023, 2024, 2025])


class PerfectScoreTests(unittest.TestCase):
    def test_all_nine_signals_stand_and_score_nine(self):
        got = fscore(_fin())
        self.assertIsNotNone(got)
        self.assertEqual(got["max_score"], 9)
        self.assertEqual(got["score"], 9, got["signals"])
        self.assertEqual(got["fiscal_year"], 2025)
        self.assertEqual(sorted(got["signals"]), sorted(SIGNAL_NAMES))

    def test_ex_equity_scale_drops_only_the_share_signal(self):
        got = fscore(_fin())
        self.assertEqual(got["max_ex_equity"], 8)
        self.assertEqual(got["score_ex_equity"], 8)


class SignalWiringTests(unittest.TestCase):
    """신호 하나가 실제로 그 항목을 보는가 — 한 항목만 뒤집어 그 신호만 0이 되는지."""

    def _score_of(self, name, **overrides):
        got = fscore(_fin(**overrides))
        self.assertIsNotNone(got, f"{name}: 프레임이 아예 안 섰다")
        return got["signals"][name], got

    def test_each_signal_flips_alone(self):
        """신호 하나를 겨냥한 변경이 **의도한 신호만** 떨어뜨리는가.

        `also_zero`는 **입력을 공유해서 같이 떨어지는 것이 옳은** 신호들이다. 한 항목을
        건드려 두 신호가 움직이는 것 자체가 배선 오류는 아니다 — 예를 들어 당기 순손실은
        ROA를 0으로 만들면서 ΔROA도 필연적으로 0으로 만든다. 그것을 여기 **명시**해
        두면, 목록에 없는 신호가 움직였을 때만 실패한다.
        """
        cases = [
            # (신호, 뒤집는 변경, 같이 0이 되어 옳은 신호, 설명)
            ("ROA", {"net_income": [50.0, 90.0, -10.0]}, ["DELTA_ROA"],
             "당기 순손실 — ROA가 음수면 ΔROA도 필연적으로 하락"),
            ("CFO", {"ocf": [60.0, 100.0, -5.0]}, ["ACCRUAL"],
             "영업현금흐름 음수 — 순이익(120)보다 작아 발생액 신호도 0"),
            ("DELTA_ROA", {"net_income": [50.0, 90.0, 95.0]}, [],
             "ROA 0.100 → 0.095로 하락 (당기 흑자라 ROA 신호는 유지)"),
            ("DELTA_LEVER", {"total_debt": [260.0, 250.0, 400.0]}, [],
             "차입금비율 0.278 → 0.400로 상승"),
            ("DELTA_LIQUID", {"current_liabilities": [400.0, 400.0, 500.0]}, [],
             "유동비율 1.25 → 1.20으로 하락"),
            ("EQ_OFFER", {"shares_outstanding": [100.0, 100.0, 130.0]}, [],
             "30% 증자"),
            ("DELTA_MARGIN", {"gross_profit": [240.0, 300.0, 330.0]}, [],
             "매출총이익률 0.333 → 0.300으로 하락"),
            ("DELTA_TURN", {"revenue": [800.0, 900.0, 950.0],
                            "gross_profit": [240.0, 300.0, 400.0]}, [],
             "회전율 1.000 → 0.950으로 하락"),
        ]
        for name, override, also_zero, why in cases:
            with self.subTest(signal=name, why=why):
                value, got = self._score_of(name, **override)
                self.assertEqual(value, 0, f"{name}({why})이 0이 아니다")
                allowed = {name, *also_zero}
                strays = sorted(k for k, v in got["signals"].items()
                                if k not in allowed and v != 1)
                self.assertEqual(strays, [], f"{name} 변경이 {strays}까지 건드렸다")
                for shared in also_zero:
                    self.assertEqual(got["signals"][shared], 0,
                                     f"{name} 변경이 {shared}를 떨어뜨릴 줄 알았는데 아니다")

    def test_accrual_reads_cash_versus_earnings(self):
        # 순이익이 영업현금흐름을 넘으면 0점 — 이익의 질이 나쁜 쪽
        value, _ = self._score_of("ACCRUAL", net_income=[50.0, 90.0, 200.0],
                                  ocf=[60.0, 100.0, 150.0])
        self.assertEqual(value, 0)

    def test_lever_signal_rewards_a_decrease(self):
        # 부호를 뒤집어 쓰기 쉬운 자리라 방향을 따로 못 박는다
        self.assertEqual(fscore(_fin())["signals"]["DELTA_LEVER"], 1)
        worse = fscore(_fin(total_debt=[260.0, 250.0, 300.0]))
        self.assertEqual(worse["signals"]["DELTA_LEVER"], 0)


class BeginningAssetsTests(unittest.TestCase):
    def test_roa_normalizes_by_prior_year_end_assets(self):
        # 최신 총자산을 바꿔도 당기 ROA는 안 움직인다 — 기초자산(전기말)을 쓰기 때문이다.
        # 대신 다음 해의 분모가 될 값이므로 회전율 신호는 움직인다.
        base = fscore(_fin())
        moved = fscore(_fin(total_assets=[800.0, 900.0, 5000.0]))
        self.assertEqual(base["signals"]["ROA"], moved["signals"]["ROA"])
        self.assertEqual(base["signals"]["DELTA_ROA"], moved["signals"]["DELTA_ROA"])

    def test_two_years_are_not_enough_for_a_score(self):
        """2개년으로는 F-Score가 **안 선다** — 사전등록이 3개 회계연도를 요구한 이유다.

        t−1의 기초자산(=t−2 기말)이 없어 전기 비율을 못 만들므로 ΔROA·ΔLEVER·ΔTURN
        셋이 죽는다. 남는 신호가 6개라 `FSCORE_MIN_SIGNALS`(7)에 미달한다.
        **이것이 미국(yfinance 연간 4행)에서 커버리지가 얇아지는 경로다.**
        """
        two = _fin().iloc[1:]
        self.assertIsNone(fscore(two))

    def test_two_years_kill_exactly_the_three_prior_ratio_signals(self):
        # 위 테스트가 None만 보므로, 무엇이 죽었는지는 하한을 낮춰 직접 확인한다
        two = _fin().iloc[1:]
        with unittest.mock.patch("src.analysis.fscore.FSCORE_MIN_SIGNALS", 1):
            got = fscore(two)
        self.assertIsNotNone(got)
        for dead in ("DELTA_ROA", "DELTA_LEVER", "DELTA_TURN"):
            self.assertIsNone(got["signals"][dead], dead)
        self.assertEqual(got["max_score"], 6)


class RefusalTests(unittest.TestCase):
    def test_refuses_when_too_few_signals_stand(self):
        # 총자산이 없으면 ROA·CFO·ΔROA·ΔLEVER·ΔTURN 다섯이 죽어 4개만 남는다
        got = fscore(_fin().drop(columns=["total_assets"]))
        self.assertIsNone(got)

    def test_missing_signal_is_none_not_zero(self):
        # 이 모듈의 요지 — 없는 것을 0점으로 주지 않는다
        got = fscore(_fin().drop(columns=["total_debt"]))
        self.assertIsNotNone(got)
        self.assertIsNone(got["signals"]["DELTA_LEVER"])
        self.assertEqual(got["max_score"], 8)
        self.assertEqual(got["score"], 8)

    def test_min_signals_constant_is_the_gate(self):
        got = fscore(_fin().drop(columns=["total_debt"]))
        self.assertGreaterEqual(got["max_score"], FSCORE_MIN_SIGNALS)

    def test_never_crashes_on_junk(self):
        junk = [None, pd.DataFrame(), _fin().iloc[:1], "not a frame", 42,
                _fin(total_assets=[0.0, 0.0, 0.0]),
                _fin(revenue=["", None, "x"])]
        for bad in junk:
            with self.subTest(bad=type(bad).__name__):
                try:
                    fscore(bad)
                except Exception as e:  # noqa: BLE001
                    self.fail(f"크래시: {type(e).__name__}: {e}")

    def test_non_positive_denominators_do_not_produce_signals(self):
        # 총자산 0은 결측과 같이 다룬다 — 0으로 나눈 inf가 '좋다'로 새면 안 된다
        got = fscore(_fin(total_assets=[800.0, 0.0, 1000.0]))
        if got is not None:
            self.assertIsNone(got["signals"]["ROA"])


class ShareToleranceTests(unittest.TestCase):
    def test_tolerance_absorbs_average_share_count_noise(self):
        # 기간 평균 주식수의 잡음(0.5%)은 증자로 보지 않는다
        noise = 100.0 * (1.0 + EQ_OFFER_TOL / 2)
        self.assertEqual(
            fscore(_fin(shares_outstanding=[100.0, 100.0, noise]))["signals"]["EQ_OFFER"], 1)

    def test_tolerance_does_not_absorb_a_real_offering(self):
        real = 100.0 * (1.0 + EQ_OFFER_TOL * 5)
        self.assertEqual(
            fscore(_fin(shares_outstanding=[100.0, 100.0, real]))["signals"]["EQ_OFFER"], 0)


class MissingInputsTests(unittest.TestCase):
    def test_names_the_absent_column(self):
        gaps = missing_inputs(_fin().drop(columns=["total_debt"]))
        self.assertIn("DELTA_LEVER", gaps)
        self.assertIn("total_debt", gaps["DELTA_LEVER"])

    def test_perfect_frame_has_no_gaps(self):
        self.assertEqual(missing_inputs(_fin()), {})

    def test_none_frame_reports_everything(self):
        self.assertEqual(sorted(missing_inputs(None)), sorted(SIGNAL_NAMES))


if __name__ == "__main__":
    unittest.main()
