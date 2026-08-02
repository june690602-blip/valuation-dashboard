"""자본비용(베타 회귀 → 하마다 → CAPM → WACC) 순수 함수 테스트.

R5 조서(`docs/review/R5-코드구조.md`) 발견 ㉴ · 이슈 #85.
`src/analysis/capital_cost.py`는 182줄인데 단위 테스트가 없었다. 이 모듈은
자본비용(WACC) 탭 전체와 **RIM 적정가(③)의 할인율**을 만든다 — 적정주가 4방법 중
하나가 테스트 없는 코드에 기대고 있었다.

**여기 적힌 기대값은 "이래야 한다"가 아니라 "지금 이렇다"이다.** 가정 값(MRP·법정세율
근사·베타 클립 범위·회귀 기간)은 R2가 검토하고 문서화한 것이라 이 테스트가 바꾸지
않는다. 지금 동작을 못 박아, 나중에 누가 바꾸면 **그것이 의도된 변경인지 묻게** 하는 것이
목적이다.

## 이 테스트가 회귀를 실제로 잡는지 확인한 방법

"통과하는 테스트"와 "회귀를 잡는 테스트"는 다르다(R5 조서 한계 3). 그래서 `capital_cost.py`를
한 곳씩 일부러 망가뜨리고 이 파일이 실패하는지 확인했다 — **10종 전부 잡는다**:

| 망가뜨린 것 | 잡는 테스트 |
|---|---|
| 베타 클립 범위 (0.4, 2.5) → (0.3, 3.0) | `AssumptionConstantTests` |
| 회귀 최소 표본 40 → 20 | `AssumptionConstantTests` · `test_short_history_*` |
| 하마다 식에서 세금방패 (1−t) 제거 | `test_hamada_unlevering_matches_formula` |
| 적자 해를 버리는 가드 제거 (PR #88이 고친 자리) | `test_loss_year_is_discarded` |
| 유효세율의 법정세율 상한 제거 | `test_measured_rate_is_capped_at_statutory` |
| WACC에서 세금방패 제거 | `test_wacc_is_weighted_average_with_tax_shield` |
| 타인자본비용 폴백 rf+2%p → rf+3%p | `test_missing_data_falls_back_to_rf_plus_2pt` |
| ROIC를 세전 영업이익으로 | `test_roic_uses_after_tax_*` |
| CAPM에서 MRP 누락 | `test_capm_is_rf_plus_beta_times_mrp` |
| 금융업 조기 반환 제거 | `test_financial_company_stops_before_wacc` |

첫 시도에서는 위 둘(베타 클립·최소 표본)을 **놓쳤다.** 기대값에 모듈 상수를 그대로 써서
상수가 바뀌면 기대값도 같이 움직였기 때문이다 — 아무것도 검증하지 않는 테스트였다.
그래서 가정 상수는 `AssumptionConstantTests`에서 **값으로** 못 박는다.

CI(quality.yml)는 `python -m unittest discover`로 실행하므로 unittest 규약을 따른다.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.analysis.capital_cost import (BETA_CLIP, MIN_WEEKS, _cost_of_debt,
                                       _effective_tax_rate, _tax_pair,
                                       compute_capital_cost, estimate_beta)
from src.data.models import FIN_COLUMNS, CompanyData

RF, MRP = 0.035, 0.06


# ── 픽스처 ───────────────────────────────────────────────────────────
def _price_pair(beta: float, n_weeks: int = 60):
    """(종목 시세, 지수 시세) — 주간 수익률이 정확히 stock = beta × market이 되게 만든다.

    회귀 결과를 예측할 수 있어야 "베타가 맞게 나오는가"를 단정으로 쓸 수 있다.
    난수를 쓰되 시드를 고정해 매번 같은 값이 나오게 한다.
    """
    idx = pd.date_range(end="2026-06-26", periods=n_weeks, freq="W-FRI")
    rng = np.random.default_rng(0)
    m_ret = rng.normal(0.0, 0.02, n_weeks - 1)
    s_ret = beta * m_ret
    market = pd.Series(100.0 * np.cumprod(np.r_[1.0, 1.0 + m_ret]), index=idx)
    stock = pd.Series(50.0 * np.cumprod(np.r_[1.0, 1.0 + s_ret]), index=idx)
    return stock, market


def _financials(years=4, *, tax_rows=None, interest=8.0, debt=200.0,
                operating_income=150.0, equity=1_000.0, cash=0.0) -> pd.DataFrame:
    """FIN_COLUMNS를 모두 가진 연간 재무 — 실제 provider가 만드는 모양과 같게.

    `base.py`·`kr_provider.py`가 FIN_COLUMNS를 돌며 없는 열을 NaN으로 채우므로,
    운영에서 열 자체가 빠지는 일은 없다. 결측은 열이 아니라 값(NaN)으로 온다.
    """
    yrs = list(range(2026 - years, 2026))
    fin = pd.DataFrame({c: [np.nan] * years for c in FIN_COLUMNS}, index=yrs)
    fin["operating_income"] = operating_income
    fin["total_equity"] = equity
    fin["total_debt"] = debt
    fin["interest_expense"] = interest
    fin["cash"] = cash
    fin["fiscal_end"] = [pd.Timestamp(f"{y}-12-31") for y in yrs]
    if tax_rows is not None:
        fin["tax_expense"] = [t for t, _ in tax_rows]
        fin["pretax_income"] = [p for _, p in tax_rows]
    return fin


def _company(*, beta=1.0, market_cap=1_000.0, market="KR", is_financial=False,
             n_weeks=60, **fin_kw) -> CompanyData:
    stock, index = _price_pair(beta, n_weeks)
    return CompanyData(
        ticker="T", yahoo_ticker="T", name="테스트", market=market,
        currency="KRW" if market == "KR" else "USD", sector="", industry="",
        price=float(stock.iloc[-1]), market_cap=market_cap, shares_outstanding=100.0,
        financials=_financials(**fin_kw), ttm=None, prices=stock, index_prices=index,
        benchmark_name="KOSPI", peers=pd.DataFrame(), is_financial=is_financial,
    )


# ── 베타 회귀 ────────────────────────────────────────────────────────
class BetaTests(unittest.TestCase):
    def test_regression_recovers_known_beta(self):
        """종목 수익률을 시장의 정확히 1.4배로 만들면 회귀 베타도 1.4여야 한다."""
        beta, r2, n, label, _ = estimate_beta(*_price_pair(1.4))
        self.assertAlmostEqual(beta, 1.4, places=10)
        self.assertAlmostEqual(r2, 1.0, places=10)   # 완전 선형이므로 설명력 1
        self.assertEqual(n, 59)                      # 60개 시세 → 수익률 59개
        self.assertIn("주간수익률 59개", label)

    def test_short_history_returns_none_not_crash(self):
        """상장기간이 짧으면 베타를 만들어 내지 않는다 — 최소 표본 미달.

        기대값을 MIN_WEEKS로 쓰지 않고 40이라고 적는다. 모듈 상수를 그대로 참조하면
        상수가 바뀔 때 기대값도 같이 움직여 **아무것도 검증하지 않는 테스트**가 된다
        (돌연변이 검사에서 실제로 놓쳤던 자리다).
        """
        beta, r2, n, label, _ = estimate_beta(*_price_pair(1.2, n_weeks=40))
        self.assertIsNone(beta)          # 수익률 39개 < 최소 40개
        self.assertIsNone(r2)
        self.assertEqual(n, 39)
        self.assertEqual(label, "")

        # 한 주만 더 있으면 추정된다 — 경계가 40인지 확인한다.
        beta_ok, _, n_ok, _, _ = estimate_beta(*_price_pair(1.2, n_weeks=41))
        self.assertIsNotNone(beta_ok)
        self.assertEqual(n_ok, 40)

    def test_flat_market_returns_none(self):
        """시장이 전혀 움직이지 않으면 분모(분산)가 0 — 베타를 낼 수 없다."""
        idx = pd.date_range(end="2026-06-26", periods=60, freq="W-FRI")
        flat = pd.Series([100.0] * 60, index=idx)
        moving = pd.Series(np.linspace(50, 80, 60), index=idx)
        self.assertIsNone(estimate_beta(moving, flat)[0])

    def test_short_history_falls_back_to_beta_one_with_warning(self):
        """베타를 못 구하면 β=1로 가정하고, 그 사실을 경고로 남긴다."""
        cc = compute_capital_cost(_company(n_weeks=40), RF, MRP)
        self.assertEqual(cc.beta_l, 1.0)
        self.assertIsNone(cc.beta_l_raw)
        self.assertTrue(any("β=1로 가정" in w for w in cc.warnings))

    def test_extreme_beta_is_clipped_with_warning(self):
        """극단 베타는 범위로 자르고 원값을 함께 남긴다 — 잘랐다는 사실이 숨지 않게."""
        cc = compute_capital_cost(_company(beta=4.0), RF, MRP)
        self.assertAlmostEqual(cc.beta_l_raw, 4.0, places=8)
        self.assertEqual(cc.beta_l, 2.5)     # 상수를 참조하지 않고 값을 적는다
        self.assertTrue(any("클리핑" in w for w in cc.warnings))

        low = compute_capital_cost(_company(beta=0.1), RF, MRP)
        self.assertEqual(low.beta_l, 0.4)
        self.assertTrue(any("클리핑" in w for w in low.warnings))


class AssumptionConstantTests(unittest.TestCase):
    """R2가 검토한 가정 상수를 값으로 못 박는다.

    이 단정들은 "이 값이 옳다"가 아니라 **"바뀌면 알아차리자"**는 뜻이다. 베타 클립 범위와
    회귀 최소 표본은 R2의 민감도 분석이 전제로 삼은 값이라, 조용히 바뀌면 그 조서의
    결론이 근거를 잃는다. 바꿔야 한다면 이 줄을 함께 고치면서 왜 바꾸는지를 남기면 된다.
    """

    def test_beta_clip_range(self):
        self.assertEqual(BETA_CLIP, (0.4, 2.5))

    def test_minimum_regression_sample(self):
        self.assertEqual(MIN_WEEKS, 40)      # 주간 40개 ≈ 10개월


# ── CAPM · 하마다 언레버링 ───────────────────────────────────────────
class CapmAndHamadaTests(unittest.TestCase):
    def test_capm_is_rf_plus_beta_times_mrp(self):
        cc = compute_capital_cost(_company(beta=1.0, debt=0.0), RF, MRP)
        self.assertAlmostEqual(cc.k_e, RF + MRP, places=10)   # β=1이면 시장수익률

        cc2 = compute_capital_cost(_company(beta=1.5, debt=0.0), RF, MRP)
        self.assertAlmostEqual(cc2.k_e, RF + 1.5 * MRP, places=10)

    def test_no_debt_means_unlevered_equals_levered(self):
        """부채가 없으면 재무위험이 없다 — β_U = β_L, 재무위험 프리미엄 0."""
        cc = compute_capital_cost(_company(beta=1.2, debt=0.0), RF, MRP)
        self.assertAlmostEqual(cc.de_ratio, 0.0, places=12)
        self.assertAlmostEqual(cc.beta_u, cc.beta_l, places=12)
        self.assertAlmostEqual(cc.financial_risk_premium, 0.0, places=12)

    def test_hamada_unlevering_matches_formula(self):
        """β_U = β_L / (1 + (1-t)·D/E) — 부채가 있으면 영업위험 베타가 더 낮다."""
        cc = compute_capital_cost(_company(beta=1.2, market_cap=1_000.0, debt=500.0),
                                  RF, MRP, tax_override=0.25)
        self.assertAlmostEqual(cc.de_ratio, 0.5, places=12)
        expected = 1.2 / (1 + (1 - 0.25) * 0.5)
        self.assertAlmostEqual(cc.beta_u, expected, places=12)
        self.assertLess(cc.beta_u, cc.beta_l)
        self.assertGreater(cc.financial_risk_premium, 0.0)

    def test_more_leverage_lowers_unlevered_beta(self):
        """레버리지가 커질수록 같은 β_L이 더 낮은 영업위험을 뜻하게 된다."""
        low = compute_capital_cost(_company(beta=1.2, debt=200.0), RF, MRP, tax_override=0.25)
        high = compute_capital_cost(_company(beta=1.2, debt=2_000.0), RF, MRP, tax_override=0.25)
        self.assertEqual(low.beta_l, high.beta_l)
        self.assertGreater(low.beta_u, high.beta_u)


# ── WACC 가중 ────────────────────────────────────────────────────────
class WaccTests(unittest.TestCase):
    def test_no_debt_means_wacc_equals_cost_of_equity(self):
        cc = compute_capital_cost(_company(beta=1.0, debt=0.0), RF, MRP, tax_override=0.24)
        self.assertAlmostEqual(cc.we, 1.0, places=12)
        self.assertAlmostEqual(cc.wd, 0.0, places=12)
        self.assertAlmostEqual(cc.wacc, cc.k_e, places=12)

    def test_wacc_is_weighted_average_with_tax_shield(self):
        """WACC = w_e·k_e + w_d·k_d·(1-t). 가중치는 시가 기준(시총 : 차입금)."""
        cc = compute_capital_cost(
            _company(beta=1.0, market_cap=1_000.0, debt=1_000.0, interest=40.0),
            RF, MRP, tax_override=0.25)
        self.assertAlmostEqual(cc.we, 0.5, places=12)
        self.assertAlmostEqual(cc.wd, 0.5, places=12)
        expected = 0.5 * cc.k_e + 0.5 * cc.k_d * (1 - 0.25)
        self.assertAlmostEqual(cc.wacc, expected, places=12)

    def test_higher_tax_rate_lowers_wacc(self):
        """세율이 높을수록 이자의 절세 효과가 커져 WACC가 내려간다 — 세금방패."""
        lo = compute_capital_cost(_company(debt=1_000.0, interest=40.0), RF, MRP, tax_override=0.10)
        hi = compute_capital_cost(_company(debt=1_000.0, interest=40.0), RF, MRP, tax_override=0.40)
        self.assertLess(hi.wacc, lo.wacc)

    def test_financial_company_stops_before_wacc(self):
        """금융업은 차입이 영업 그 자체 — 하마다·WACC를 내지 않고 k_e만 준다."""
        cc = compute_capital_cost(_company(is_financial=True, debt=5_000.0), RF, MRP)
        self.assertIsNotNone(cc.k_e)
        self.assertIsNone(cc.wacc)
        self.assertIsNone(cc.beta_u)
        self.assertIsNone(cc.roic)
        self.assertTrue(any("금융업" in w for w in cc.warnings))


# ── 타인자본비용 ─────────────────────────────────────────────────────
class CostOfDebtTests(unittest.TestCase):
    def test_uses_interest_over_average_debt(self):
        d = _company(debt=1_000.0, interest=40.0)
        kd, source, warns = _cost_of_debt(d, RF)
        self.assertAlmostEqual(kd, 0.04, places=12)   # 40 / 평균차입금 1,000
        self.assertIn("재무제표", source)
        self.assertEqual(warns, [])

    def test_missing_data_falls_back_to_rf_plus_2pt(self):
        """이자비용이 결측이면 만들어 내지 않고 가정값으로 — 그 사실을 경고로 남긴다."""
        d = _company()
        d.financials["interest_expense"] = np.nan
        kd, source, warns = _cost_of_debt(d, RF)
        self.assertAlmostEqual(kd, RF + 0.02, places=12)
        self.assertIn("가정", source)
        self.assertTrue(any("불충분" in w for w in warns))

    def test_out_of_range_rate_falls_back(self):
        """비정상적으로 높은 비율(차입금 대비 이자 30%)은 쓰지 않는다."""
        d = _company(debt=100.0, interest=30.0)
        kd, source, _ = _cost_of_debt(d, RF)
        self.assertAlmostEqual(kd, RF + 0.02, places=12)
        self.assertIn("가정", source)

    def test_high_but_usable_rate_warns(self):
        """범위 안이지만 무위험이자율보다 6%p 넘게 높으면 섞임 가능성을 알린다."""
        d = _company(debt=1_000.0, interest=120.0)   # 12% — 범위(0.5~15%) 안
        kd, _, warns = _cost_of_debt(d, RF)
        self.assertAlmostEqual(kd, 0.12, places=12)
        self.assertTrue(any("기타 금융비용" in w for w in warns))


# ── 유효세율 (PR #88이 고친 자리) ────────────────────────────────────
class EffectiveTaxRateTests(unittest.TestCase):
    def test_loss_year_is_discarded(self):
        """적자 해는 버린다 — 음수÷음수가 그럴듯한 양수 비율을 만든다.

        롯데케미칼 2025년: 세전 -2.71조 · 법인세 -0.23조 → 8.4%가 찍힌다.
        세금방패에 쓸 세율로는 뜻이 없는 값이고, 범위 검사(3~45%)는 부호를 안 보므로
        그대로 통과해 버린다. 이것이 PR #88이 고친 자리다.
        """
        self.assertIsNone(_tax_pair(-0.23e12, -2.71e12))
        self.assertIsNone(_tax_pair(1.0, 0.0))        # 세전 0도 버린다
        self.assertIsNone(_tax_pair(None, 100.0))
        self.assertIsNone(_tax_pair(np.nan, 100.0))
        self.assertAlmostEqual(_tax_pair(24.0, 100.0), 0.24, places=12)

    def test_all_loss_years_fall_back_to_statutory(self):
        """쓸 수 있는 해가 하나도 없으면 법정세율 근사치로 — ok=False로 알린다."""
        d = _company(tax_rows=[(-10.0, -100.0)] * 4)
        rate, ok, raw = _effective_tax_rate(d, 0.24)
        self.assertAlmostEqual(rate, 0.24, places=12)
        self.assertFalse(ok)
        self.assertIsNone(raw)

    def test_measured_rate_is_capped_at_statutory(self):
        """실효세율이 법정세율보다 높아도 세금방패는 법정세율까지만 — 원값은 함께 준다.

        방패의 t는 '이자를 1원 더 낼 때 아끼는 세금'이라 법정세율을 넘을 수 없다.
        초과분은 손금불산입에서 오는 것이라 이자의 절세 효과를 키우지 않는다.
        """
        d = _company(tax_rows=[(35.0, 100.0)] * 4)
        rate, ok, raw = _effective_tax_rate(d, 0.24)
        self.assertAlmostEqual(rate, 0.24, places=12)   # 잘렸다
        self.assertTrue(ok)
        self.assertAlmostEqual(raw, 0.35, places=12)    # 원값은 남는다

    def test_normal_rate_is_used_as_is(self):
        d = _company(tax_rows=[(18.0, 100.0)] * 4)
        rate, ok, raw = _effective_tax_rate(d, 0.24)
        self.assertAlmostEqual(rate, 0.18, places=12)
        self.assertTrue(ok)
        self.assertAlmostEqual(raw, 0.18, places=12)

    def test_capped_rate_is_explained_in_warnings(self):
        """잘랐으면 화면이 그 이유를 말할 수 있어야 한다 — 경고로 남는다."""
        cc = compute_capital_cost(_company(tax_rows=[(35.0, 100.0)] * 4), RF, MRP)
        self.assertAlmostEqual(cc.tax_rate, 0.24, places=12)
        self.assertTrue(any("손금불산입" in w for w in cc.warnings))

    def test_override_skips_measurement(self):
        """호출부가 세율을 지정하면 측정하지 않는다 — 민감도 분석이 이 경로를 쓴다."""
        cc = compute_capital_cost(_company(tax_rows=[(35.0, 100.0)] * 4), RF, MRP,
                                  tax_override=0.11)
        self.assertAlmostEqual(cc.tax_rate, 0.11, places=12)


# ── ROIC · 스프레드 ──────────────────────────────────────────────────
class RoicTests(unittest.TestCase):
    def test_roic_uses_after_tax_operating_income_over_invested_capital(self):
        """ROIC = 세후영업이익 / (자본 + 차입금 − 현금)."""
        cc = compute_capital_cost(
            _company(operating_income=150.0, equity=1_000.0, debt=200.0, cash=100.0),
            RF, MRP, tax_override=0.20)
        invested = 1_000.0 + 200.0 - 100.0
        self.assertAlmostEqual(cc.roic, 150.0 * 0.8 / invested, places=12)
        self.assertAlmostEqual(cc.spread, cc.roic - cc.wacc, places=12)

    def test_no_roic_without_operating_income(self):
        d = _company()
        d.financials["operating_income"] = np.nan
        cc = compute_capital_cost(d, RF, MRP)
        self.assertIsNone(cc.roic)
        self.assertIsNone(cc.spread)


# ── 결측·경계값 — CLAUDE.md "절대 크래시 내지 말 것" ─────────────────
class MissingDataTests(unittest.TestCase):
    """무료 데이터라 결측이 흔하다. 값이 없으면 None이지 예외가 아니다."""

    def test_zero_market_cap_yields_none_not_zero_division(self):
        cc = compute_capital_cost(_company(market_cap=0.0, debt=500.0), RF, MRP)
        self.assertIsNone(cc.de_ratio)     # 0으로 나누지 않는다
        self.assertIsNone(cc.beta_u)
        self.assertIsNotNone(cc.k_e)       # k_e는 자본구조와 무관하므로 살아 있다
        self.assertIsNotNone(cc.wacc)      # 총자본 = 차입금뿐이라 계산은 된다

    def test_everything_missing_does_not_raise(self):
        """재무가 전부 NaN이어도 예외 없이 k_e까지는 나온다."""
        d = _company()
        for col in ("operating_income", "total_equity", "total_debt",
                    "interest_expense", "cash", "tax_expense", "pretax_income"):
            d.financials[col] = np.nan
        cc = compute_capital_cost(d, RF, MRP)
        self.assertIsNotNone(cc.k_e)
        self.assertIsNone(cc.roic)
        self.assertAlmostEqual(cc.debt, 0.0, places=12)   # 결측 차입금은 0으로

    def test_empty_financials_does_not_raise(self):
        """행이 하나도 없는 재무제표(신규 상장 등)에서도 죽지 않는다."""
        d = _company()
        d.financials = d.financials.iloc[0:0]
        cc = compute_capital_cost(d, RF, MRP)
        self.assertIsNotNone(cc.k_e)
        self.assertIsNone(cc.roic)

    def test_zero_and_full_tax_rates_are_handled(self):
        """세율 0과 1이라는 극단 입력에서도 식이 성립한다."""
        zero = compute_capital_cost(_company(debt=1_000.0, interest=40.0), RF, MRP,
                                    tax_override=0.0)
        self.assertAlmostEqual(zero.wacc, zero.we * zero.k_e + zero.wd * zero.k_d, places=12)
        self.assertAlmostEqual(zero.beta_u, zero.beta_l / (1 + zero.de_ratio), places=12)

        full = compute_capital_cost(_company(debt=1_000.0, interest=40.0), RF, MRP,
                                    tax_override=1.0)
        self.assertAlmostEqual(full.wacc, full.we * full.k_e, places=12)  # 방패가 k_d를 전부 상쇄
        self.assertAlmostEqual(full.beta_u, full.beta_l, places=12)       # (1-t)=0 → 레버리지 무효


# ── 판정 경로와의 연결 — 이 모듈이 무엇을 먹이는가 ───────────────────
class DownstreamContractTests(unittest.TestCase):
    """k_e는 RIM(③)의 할인율이 된다. 그 계약이 깨지지 않았는지 본다."""

    def test_cost_of_equity_is_always_produced(self):
        """RIM이 쓸 할인율은 어떤 결측 조합에서도 나와야 한다 — 베타 폴백이 그것을 보장한다."""
        cases = [
            _company(),                                   # 정상
            _company(n_weeks=40),                         # 베타 표본 부족
            _company(market_cap=0.0),                     # 시총 결측
            _company(is_financial=True),                  # 금융업
        ]
        for d in cases:
            cc = compute_capital_cost(d, RF, MRP)
            self.assertIsNotNone(cc.k_e)
            self.assertGreater(cc.k_e, RF)                # β는 최소 0.4라 항상 R_f 위
            self.assertLess(cc.k_e, RF + 2.5 * MRP + 1e-12)


if __name__ == "__main__":
    unittest.main()
