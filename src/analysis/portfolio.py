"""포트폴리오 분석 — 월간 수익률 통계·평균-분산·성과지표·세금 분해 (순수 함수).

관례:
- 모든 수익률은 **원화 환산**(달러 자산은 환율 변화 포함 = 환노출 그대로).
- μ·σ는 월간 수익률의 산술평균×12, 표준편차×√12 (평면·샤프 계산 관례).
- 예금 같은 무위험 자산은 상수 월수익(금리/12) 시리즈로 합류 → σ·공분산이 자연히 0.

세금(참고용, 2026 개인 기준·금투세 폐지 반영):
- 국내상장주식 매매차익 비과세(소액주주), 배당 15.4%
- 해외주식 양도 22%(연 250만 공제는 단순화로 무시·명시), 배당 15%(미국 원천)
- 국내주식형 ETF: 매매차익 비과세, 분배금 15.4%
- 국내 기타 ETF(채권·금·리츠·해외지수): 매매차익은 과표기준가 증분과 실제 차익 중
  작은 금액에 15.4%, 분배금 15.4% (과표기준가가 없어 상한 추정으로 표시)
- 예금·채권 이자 15.4% / 개인의 채권 매매차익·환차익은 비과세
- 금융소득 연 2천만 초과 시 종합과세 대상(계산하지 않고 안내만)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MONTHS_PER_YEAR = 12

# 자산 유형 → (과세 설명, 자본이득 세율, 인컴(배당·이자·분배) 세율)
TAX_RULES = {
    "국내주식":   ("매매차익 비과세 · 배당 15.4%", 0.0, 0.154),
    "해외주식":   ("양도 22%(공제 무시) · 배당 15%", 0.22, 0.15),
    "국내주식형ETF": ("매매차익 비과세 · 분배금 15.4%", 0.0, 0.154),
    "국내기타ETF": ("매매차익 과표기준가 한도 15.4% · 분배금 15.4% (상한 추정)", 0.154, 0.154),
    "국내ETF":    ("유형 미분류 국내 ETF · 기타 ETF 기준 15.4% 상한 추정", 0.154, 0.154),
    "해외ETF":    ("양도 22%(공제 무시) · 분배 15%", 0.22, 0.15),
    "달러현금":   ("개인 환차익 비과세", 0.0, 0.0),
    "예금":       ("이자 15.4%", 0.0, 0.154),
}

# 인컴(배당·이자·분배) 수익률 기본 가정 — 세금 분해용 (총 기대수익 중 인컴 부분)
DEFAULT_INCOME_YIELD = {
    "국내주식": 0.020, "해외주식": 0.013, "국내주식형ETF": 0.020,
    "국내기타ETF": 0.030, "국내ETF": 0.030, "해외ETF": 0.025,
    "달러현금": 0.0, "예금": None,  # 예금은 전액 이자
}


# ── 수익률 통계 ─────────────────────────────────────────────────────
def monthly_returns_krw(prices: dict[str, pd.Series], fx: pd.Series | None,
                        currencies: dict[str, str], months: int = 60,
                        cash_rates: dict[str, float] | None = None) -> pd.DataFrame:
    """자산별 일별 종가 → 원화 환산 → 월말 리샘플 → 월간 수익률 정렬표.

    prices: {자산키: 일별 종가}, currencies: {자산키: "KRW"|"USD"},
    cash_rates: {자산키: 연 금리} — 예금처럼 시계열 없는 자산(상수 월수익으로 합류).
    마지막 월은 진행 중(부분 월)이라 제외.
    """
    cols = {}
    for key, px in prices.items():
        if px is None or len(px) < 40:
            continue
        s = px.copy()
        if currencies.get(key) == "USD" and fx is not None and len(fx):
            aligned_fx = fx.reindex(s.index).ffill()
            s = s * aligned_fx
        m = s.resample("ME").last().pct_change(fill_method=None).dropna()
        if len(m) >= 12:
            cols[key] = m
    df = pd.DataFrame(cols).dropna(how="all")
    if not df.empty:
        df = df.iloc[:-1]  # 부분 월 제외
        df = df.tail(months).dropna()
    for key, rate in (cash_rates or {}).items():
        if df.empty:
            continue
        df[key] = rate / MONTHS_PER_YEAR  # 상수 월수익 → σ·공분산 0
    return df


def annualize(monthly: pd.DataFrame) -> dict:
    """월간 수익률표 → 연율화 μ·σ·공분산·상관.

    반환: {mu: Series, sigma: Series, cov: DataFrame, corr: DataFrame, n_months: int}
    """
    mu = monthly.mean() * MONTHS_PER_YEAR
    sigma = monthly.std(ddof=1) * np.sqrt(MONTHS_PER_YEAR)
    cov = monthly.cov(ddof=1) * MONTHS_PER_YEAR
    corr = monthly.corr().fillna(0.0)
    return {"mu": mu, "sigma": sigma, "cov": cov, "corr": corr, "n_months": len(monthly)}


def portfolio_point(weights: pd.Series, mu: pd.Series, cov: pd.DataFrame) -> dict:
    """비중 벡터 → 포트폴리오 기대수익·변동성. {er, sigma}"""
    w = weights.reindex(mu.index).fillna(0.0).values
    er = float(np.dot(w, mu.values))
    var = float(w @ cov.values @ w)
    return {"er": er, "sigma": float(np.sqrt(max(var, 0.0)))}


def portfolio_series(weights: pd.Series, monthly: pd.DataFrame) -> pd.Series:
    """고정 비중(월간 리밸런싱 가정) 포트폴리오의 월간 수익률 시계열."""
    w = weights.reindex(monthly.columns).fillna(0.0)
    return (monthly * w).sum(axis=1)


# ── 성과지표 ────────────────────────────────────────────────────────
def performance(port_m: pd.Series, bench_m: pd.Series, rf: float) -> dict:
    """샤프·트레이너·젠센알파·M²·베타 — 월간 시계열 기반, 연율 기준.

    반환 값은 전부 연율(소수). 표본이 부족하면 해당 지표 None.
    """
    df = pd.concat([port_m.rename("p"), bench_m.rename("b")], axis=1).dropna()
    if len(df) < 12:
        return {k: None for k in ("sharpe", "beta", "treynor", "jensen", "m2",
                                  "er_p", "sigma_p", "er_b", "sigma_b", "n")}
    er_p = float(df["p"].mean() * MONTHS_PER_YEAR)
    er_b = float(df["b"].mean() * MONTHS_PER_YEAR)
    sd_p = float(df["p"].std(ddof=1) * np.sqrt(MONTHS_PER_YEAR))
    sd_b = float(df["b"].std(ddof=1) * np.sqrt(MONTHS_PER_YEAR))
    var_b = float(df["b"].var(ddof=1))
    beta = float(df["p"].cov(df["b"]) / var_b) if var_b > 0 else None

    sharpe = (er_p - rf) / sd_p if sd_p > 0 else None
    treynor = (er_p - rf) / beta if beta not in (None, 0.0) else None
    jensen = er_p - (rf + beta * (er_b - rf)) if beta is not None else None
    m2 = rf + sharpe * sd_b if sharpe is not None else None
    return {"sharpe": sharpe, "beta": beta, "treynor": treynor, "jensen": jensen,
            "m2": m2, "er_p": er_p, "sigma_p": sd_p, "er_b": er_b, "sigma_b": sd_b,
            "n": len(df)}


# ── 세금 분해 (참고용) ──────────────────────────────────────────────
def after_tax_row(asset_type: str, mu: float, income_yield: float | None = None) -> dict:
    """자산 유형별 세전 μ → (자본이득, 인컴) 분해 후 예상 실효세율·세후 μ.

    income_yield가 None이면 유형 기본값 사용. 예금은 전액 이자 취급.
    반환: {rule, capital, income, eff_rate, mu_after}
    """
    rule, cap_tax, inc_tax = TAX_RULES.get(asset_type, ("기타 — 15.4% 가정", 0.154, 0.154))
    if asset_type == "예금":
        income = mu
    else:
        base_inc = DEFAULT_INCOME_YIELD.get(asset_type, 0.0) if income_yield is None else income_yield
        income = min(max(base_inc, 0.0), max(mu, 0.0)) if mu > 0 else 0.0
    capital = mu - income
    # 자본이득 과세는 이익(+)일 때만; 손실이면 세금 0으로 단순화
    tax = (max(capital, 0.0) * cap_tax) + (income * inc_tax)
    mu_after = mu - tax
    eff = tax / mu if mu > 0 else 0.0
    return {"rule": rule, "capital": capital, "income": income,
            "eff_rate": eff, "mu_after": mu_after}
