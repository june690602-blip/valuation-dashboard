"""5개 카테고리 지표 계산 — CompanyData만 입력받는 순수 함수.

값이 없거나 무의미하면(적자 PER 등) None으로 두고 절대 예외를 내지 않는다.
금융업은 차입 구조가 본업이라 일부 지표를 마스킹한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.models import CompanyData

# 금융업(은행·보험·증권)에서 의미 없는 지표
FINANCIAL_MASK = ["psr", "ev_ebitda", "p_fcf", "debt_ratio", "current_ratio",
                  "interest_coverage", "net_debt_ebitda", "fcf_yield", "ocf_ni"]


@dataclass
class Indicators:
    valuation: dict = field(default_factory=dict)
    profitability: dict = field(default_factory=dict)
    growth: dict = field(default_factory=dict)
    stability: dict = field(default_factory=dict)
    cashflow: dict = field(default_factory=dict)
    series: dict = field(default_factory=dict)   # 연도별 시계열 (차트용)
    masked: list = field(default_factory=list)   # 마스킹된 지표명

    def flat(self) -> dict:
        out = {}
        for cat in (self.valuation, self.profitability, self.growth,
                    self.stability, self.cashflow):
            out.update(cat)
        return out


def _safe_div(a, b, allow_neg_num=True):
    if a is None or b is None or b == 0 or np.isnan(b) or np.isnan(a):
        return None
    if not allow_neg_num and a <= 0:
        return None
    return a / b


def _pos(v):
    """양수만 유효한 값 (적자 PER 등 방지)."""
    return v if v is not None and v > 0 else None


def _growth_rate(s: pd.Series, years: int = 3):
    """연평균 성장률(CAGR). 시작값이 0 이하이면 None."""
    s = s.dropna()
    if len(s) < 2:
        return None
    n = min(years, len(s) - 1)
    start, end = s.iloc[-1 - n], s.iloc[-1]
    if start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / n) - 1


def _yoy(s: pd.Series):
    s = s.dropna()
    if len(s) < 2 or s.iloc[-2] <= 0:
        return None
    return s.iloc[-1] / s.iloc[-2] - 1


def compute_indicators(d: CompanyData) -> Indicators:
    ind = Indicators()
    fin, mcap, price = d.financials, d.market_cap, d.price
    shares = d.shares_outstanding

    eps = _pos(d.latest("eps"))
    equity = _pos(d.latest("total_equity"))
    revenue = _pos(d.latest("revenue"))
    ebitda = _pos(d.latest("ebitda"))
    fcf = d.latest("fcf")
    ocf = d.latest("ocf")
    ni = d.latest("net_income")
    oi = d.latest("operating_income")
    debt = d.latest("total_debt") or 0.0
    cash = d.latest("cash") or 0.0
    bps = _safe_div(equity, shares)

    # ── ① 밸류에이션 ──────────────────────────────────────────────
    per = _safe_div(price, eps)
    pbr = _safe_div(mcap, equity)
    psr = _safe_div(mcap, revenue)
    ev = mcap + debt - cash
    ev_ebitda = _safe_div(ev, ebitda) if ebitda else None
    p_fcf = _safe_div(mcap, _pos(fcf))
    div_yield = d.official.get("DIV")
    if div_yield is None:
        div_paid = d.latest("dividends_paid")
        div_yield = _safe_div(div_paid, mcap) if div_paid else None

    eps_growth = _growth_rate(fin["eps"] if "eps" in fin else pd.Series(dtype=float)) \
        or _growth_rate(fin["net_income"])
    peg = _safe_div(per, eps_growth * 100) if per and eps_growth and eps_growth > 0 else None

    ind.valuation = {"per": per, "pbr": pbr, "psr": psr, "ev_ebitda": ev_ebitda,
                     "p_fcf": p_fcf, "div_yield": div_yield, "peg": peg}

    # ── ② 수익성 ──────────────────────────────────────────────────
    eq_series = fin["total_equity"].dropna()
    avg_equity = float(np.mean([equity, eq_series.iloc[-1]])) if equity and len(eq_series) else equity
    assets = _pos(d.latest("total_assets"))
    as_series = fin["total_assets"].dropna()
    avg_assets = float(np.mean([assets, as_series.iloc[-1]])) if assets and len(as_series) else assets
    gp = d.latest("gross_profit")

    ind.profitability = {
        "roe": _safe_div(ni, avg_equity),
        "roa": _safe_div(ni, avg_assets),
        "gross_margin": _safe_div(gp, revenue),
        "op_margin": _safe_div(oi, revenue),
        "net_margin": _safe_div(ni, revenue),
    }

    # ── ③ 성장성 ──────────────────────────────────────────────────
    ind.growth = {
        "rev_yoy": _yoy(fin["revenue"]),
        "rev_cagr3": _growth_rate(fin["revenue"]),
        "op_cagr3": _growth_rate(fin["operating_income"]),
        "eps_cagr3": eps_growth,
    }

    # ── ④ 재무 안정성 ─────────────────────────────────────────────
    liabilities = d.latest("total_liabilities")
    ca, cl = d.latest("current_assets"), d.latest("current_liabilities")
    int_exp = _pos(d.latest("interest_expense"))
    ind.stability = {
        "debt_ratio": _safe_div(liabilities, equity),          # 한국식 부채비율(부채총계/자본총계)
        "current_ratio": _safe_div(ca, cl),
        "interest_coverage": _safe_div(oi, int_exp) if int_exp else None,
        "net_debt_ebitda": _safe_div(debt - cash, ebitda) if ebitda else None,
    }

    # ── ⑤ 현금흐름·이익의 질 ──────────────────────────────────────
    ind.cashflow = {
        "ocf": ocf, "fcf": fcf,
        "fcf_yield": _safe_div(fcf, mcap),
        "ocf_ni": _safe_div(ocf, _pos(ni)),
    }

    # ── 연도별 시계열 (차트용) ─────────────────────────────────────
    s = {}
    for col in ("revenue", "operating_income", "net_income", "ocf", "fcf",
                "total_debt", "cash"):
        s[col] = fin[col].dropna()
    s["op_margin"] = (fin["operating_income"] / fin["revenue"]).dropna()
    s["net_margin"] = (fin["net_income"] / fin["revenue"]).dropna()
    s["debt_ratio"] = (fin["total_liabilities"] / fin["total_equity"]).dropna()
    s["current_ratio"] = (fin["current_assets"] / fin["current_liabilities"]).dropna()
    # 연도별 ROE (평균자본 기준)
    eq = fin["total_equity"]
    avg_eq = (eq + eq.shift(1)) / 2
    avg_eq = avg_eq.fillna(eq)
    s["roe"] = (fin["net_income"] / avg_eq).dropna()
    # 연도별 ROIC = NOPAT / 투하자본
    tax_rate = (fin["tax_expense"] / fin["pretax_income"]).clip(0.0, 0.4)
    invested = fin["total_equity"] + fin["total_debt"].fillna(0) - fin["cash"].fillna(0)
    s["roic"] = (fin["operating_income"] * (1 - tax_rate.fillna(0.22)) / invested).dropna()
    ind.series = s

    # ── 금융업 마스킹 ─────────────────────────────────────────────
    if d.is_financial:
        for cat in (ind.valuation, ind.stability, ind.cashflow):
            for k in list(cat):
                if k in FINANCIAL_MASK:
                    cat[k] = None
        ind.masked = [k for k in FINANCIAL_MASK]
        ind.series.pop("roic", None)  # 투하자본 개념 부적합
    return ind
