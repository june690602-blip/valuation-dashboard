"""자본비용 분석: 회귀 베타 → 하마다 언레버링 → CAPM → WACC.

- β_L: 최근 5년(최대 260주) 주간 수익률을 시장지수에 OLS 회귀
- β_U = β_L / (1 + (1-t)·D/E)  … 재무레버리지를 벗긴 순수 영업위험 베타 (하마다 식)
- k_U = R_f + β_U·MRP  … 영업위험만 반영된 자본비용
- k_e = R_f + β_L·MRP  … 재무위험 포함 자기자본비용 (k_e - k_U = 재무위험 프리미엄)
- k_d = 이자비용 / 평균 이자부차입금 (연간 재무제표 기준)
- WACC = k_e·E/(D+E) + k_d·(1-t)·D/(D+E)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.models import CompanyData

BETA_CLIP = (0.4, 2.5)
MIN_WEEKS = 40


@dataclass
class CapitalCost:
    # 베타 회귀
    beta_l: float | None = None      # 레버드(회귀) 베타
    beta_l_raw: float | None = None  # 클리핑 전 원값
    beta_u: float | None = None      # 무부채(영업위험) 베타
    r2: float | None = None
    n_obs: int = 0
    period_label: str = ""
    reg_points: pd.DataFrame | None = None   # 주간 수익률 산점도용

    # 가정·재무 입력
    rf: float = 0.035
    mrp: float = 0.06
    tax_rate: float | None = None
    debt: float | None = None
    equity_mv: float | None = None
    de_ratio: float | None = None

    # 자본비용
    k_u: float | None = None         # 영업위험만 반영된 자본비용
    k_e: float | None = None         # 자기자본비용
    financial_risk_premium: float | None = None
    k_d: float | None = None
    k_d_source: str = ""
    we: float | None = None
    wd: float | None = None
    wacc: float | None = None

    # 가치창출 (EVA 관점)
    roic: float | None = None
    spread: float | None = None      # ROIC - WACC
    roic_series: pd.Series | None = None

    warnings: list = field(default_factory=list)


def _weekly_returns(prices: pd.Series, index_prices: pd.Series) -> pd.DataFrame:
    df = pd.concat([prices.rename("stock"), index_prices.rename("market")], axis=1)
    weekly = df.resample("W-FRI").last().dropna()
    rets = weekly.pct_change().dropna()
    return rets.tail(260)  # 최대 5년


def estimate_beta(prices: pd.Series, index_prices: pd.Series):
    """(beta_raw, r2, n_obs, period_label, reg_points)"""
    rets = _weekly_returns(prices, index_prices)
    n = len(rets)
    if n < MIN_WEEKS:
        return None, None, n, "", rets
    x, y = rets["market"].values, rets["stock"].values
    var = np.var(x, ddof=1)
    if var <= 0:
        return None, None, n, "", rets
    beta = float(np.cov(x, y, ddof=1)[0, 1] / var)
    corr = np.corrcoef(x, y)[0, 1]
    r2 = float(corr ** 2)
    label = f"{rets.index[0]:%Y-%m} ~ {rets.index[-1]:%Y-%m} 주간수익률 {n}개"
    return beta, r2, n, label, rets


def _effective_tax_rate(d: CompanyData, default: float) -> tuple[float, bool]:
    """최근 TTM/연간 유효세율 (법인세/세전이익), 비정상이면 기본값."""
    pairs = []
    tt, tp = d.latest("tax_expense"), d.latest("pretax_income")
    if tt is not None and tp and tp > 0:
        pairs.append(tt / tp)
    fin = d.financials
    s = (fin["tax_expense"] / fin["pretax_income"]).replace([np.inf, -np.inf], np.nan).dropna()
    pairs += list(s.tail(2))
    valid = [t for t in pairs if 0.03 <= t <= 0.45]
    if valid:
        return float(np.clip(np.mean(valid), 0.05, 0.40)), True
    return default, False


def _cost_of_debt(d: CompanyData, rf: float) -> tuple[float | None, str, list]:
    """k_d = 연간 이자비용 / 평균 이자부차입금. 비정상이면 rf+2% 폴백."""
    warns = []
    fin = d.financials
    ie = fin["interest_expense"].dropna()
    debt = fin["total_debt"].dropna()
    if len(ie) >= 1 and len(debt) >= 1:
        last_ie = float(ie.iloc[-1])
        avg_debt = float(debt.tail(2).mean())
        if avg_debt > 0 and last_ie > 0:
            kd = last_ie / avg_debt
            if 0.005 <= kd <= 0.15:
                if kd > rf + 0.06:
                    warns.append("이자비용 항목에 기타 금융비용이 섞여 타인자본비용이 "
                                 "높게 추정됐을 수 있습니다.")
                return kd, "재무제표(이자비용/평균차입금)", warns
    warns.append("이자비용 데이터가 불충분해 타인자본비용을 무위험이자율+2%p로 가정합니다.")
    return rf + 0.02, "가정(R_f + 2%p)", warns


def compute_capital_cost(d: CompanyData, rf: float, mrp: float,
                         tax_override: float | None = None) -> CapitalCost:
    cc = CapitalCost(rf=rf, mrp=mrp)

    # 1) 베타 회귀
    beta_raw, r2, n, label, rets = estimate_beta(d.prices, d.index_prices)
    cc.beta_l_raw, cc.r2, cc.n_obs, cc.period_label, cc.reg_points = beta_raw, r2, n, label, rets
    if beta_raw is None:
        cc.warnings.append(f"주간 수익률 표본이 {n}개뿐이라 베타를 추정하지 못했습니다. "
                           "β=1로 가정합니다 (상장기간이 짧은 종목).")
        cc.beta_l = 1.0
    else:
        cc.beta_l = float(np.clip(beta_raw, *BETA_CLIP))
        if cc.beta_l != beta_raw:
            cc.warnings.append(f"회귀 베타 {beta_raw:.2f}가 극단값이라 "
                               f"{cc.beta_l:.2f}로 클리핑했습니다.")
        if r2 is not None and r2 < 0.10:
            cc.warnings.append(f"베타 회귀 설명력이 낮습니다(R²={r2:.2f}). "
                               "시장 대비 개별 요인의 영향이 큰 종목입니다.")

    # 2) 세율·자본구조
    default_tax = 0.24 if d.market == "KR" else 0.21
    if tax_override is not None:
        cc.tax_rate = tax_override
    else:
        cc.tax_rate, ok = _effective_tax_rate(d, default_tax)
        if not ok:
            cc.warnings.append(f"유효세율을 계산하지 못해 법정세율 근사치 {default_tax:.0%}를 사용합니다.")
    t = cc.tax_rate

    cc.debt = d.latest("total_debt") or 0.0
    cc.equity_mv = d.market_cap
    cc.de_ratio = cc.debt / cc.equity_mv if cc.equity_mv else None

    # 3) 하마다 언레버링 → 자본비용 (금융업은 무의미)
    cc.k_e = rf + cc.beta_l * mrp
    if d.is_financial:
        cc.warnings.append("금융업은 차입이 영업 그 자체라 하마다 언레버링과 WACC가 "
                           "의미를 갖지 않습니다. 자기자본비용(k_e)만 참고하세요.")
        return cc
    if cc.de_ratio is not None:
        cc.beta_u = cc.beta_l / (1 + (1 - t) * cc.de_ratio)
        cc.k_u = rf + cc.beta_u * mrp
        cc.financial_risk_premium = cc.k_e - cc.k_u

    # 4) 타인자본비용 → WACC
    cc.k_d, cc.k_d_source, w = _cost_of_debt(d, rf)
    cc.warnings += w
    total = cc.equity_mv + cc.debt
    if total and total > 0:
        cc.we, cc.wd = cc.equity_mv / total, cc.debt / total
        cc.wacc = cc.we * cc.k_e + cc.wd * cc.k_d * (1 - t)

    # 5) ROIC vs WACC (가치창출 스프레드)
    oi = d.latest("operating_income")
    equity_bv = d.latest("total_equity")
    cash = d.latest("cash") or 0.0
    if oi is not None and equity_bv:
        invested = equity_bv + cc.debt - cash
        if invested > 0:
            cc.roic = oi * (1 - t) / invested
            if cc.wacc is not None:
                cc.spread = cc.roic - cc.wacc
    return cc
