"""밸류에이션 신호 백테스트 — "우리 기준으로 크게 저평가였을 때 샀다면 수익이 어땠나?"

핵심 질문: 이 종목이 **적정가 대비 크게 저평가**(우리 판정과 같은 기준)였던 과거 시점에
샀다면, 이후 수익률이 실제로 좋았는가? 그렇다면 우리 툴의 '저평가' 판정이 과거에 유효했다는
근거가 된다.

방법(복원 가능한 레그 = ③ 역사적 밴드):
- 각 과거일 t에서 그 종목의 배수(PER/PBR)의 **롤링 중앙값**을 '정상 배수'로 보고,
  추정 적정가 = 정상배수 × 그 시점 펀더멘털. 괴리율(저평가율) = 적정가/주가 − 1.
- 신호 = 괴리율 ≥ 임계(기본 +30%, 앱 '크게 저평가' 기준과 동일).
- 이벤트 스터디: 신호가 뜬 모든 날의 이후 3/6/12개월 수익률(평균·중앙값·승률)을
  전체 기간 평균과 비교한다.

룩어헤드 방지: '정상 배수'는 그 시점까지의 과거 구간 롤링 중앙값만 사용한다.

한계(단일 종목): 표본이 적고 과최적화·생존편향에 취약하다. '과거의 평균회귀'가 미래를
보장하지 않으며, 전략 곡선은 거래비용·세금·슬리피지를 무시한 예시다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.models import CompanyData
from .valuation import _fundamental_daily

# 미래수익률 측정 구간 (거래일 기준)
HORIZONS = {"3개월": 63, "6개월": 126, "12개월": 252}


@dataclass
class BacktestResult:
    kind: str = "PER"
    ok: bool = False
    threshold: float = 0.30                    # 저평가 임계(괴리율)
    discount: pd.Series | None = None          # 일별 저평가율(적정가/주가 − 1)
    fair_price: pd.Series | None = None        # 추정 적정가(롤링 중앙값 배수 × 펀더멘털)
    signal_days: int = 0
    event_stats: dict = field(default_factory=dict)     # {구간: {mean, median, hit, n}}
    baseline_stats: dict = field(default_factory=dict)  # {구간: {mean, hit, n}} 전체 평균
    scatter: pd.DataFrame | None = None        # discount, fwd_252
    spearman: float | None = None              # 저평가율 vs 미래수익 순위상관(양수=툴 유효)
    equity: pd.DataFrame | None = None
    strategy_never_traded: bool = False
    cagr: dict = field(default_factory=dict)
    window_years: float = 1.5
    n_obs: int = 0
    warnings: list = field(default_factory=list)


def _cagr(equity: pd.Series) -> float | None:
    e = equity.dropna()
    if len(e) < 2 or e.iloc[0] <= 0:
        return None
    years = (e.index[-1] - e.index[0]).days / 365.25
    if years <= 0:
        return None
    return float((e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1)


def run_backtest(d: CompanyData, kind: str = "PER",
                 threshold: float = 0.30, window_years: float = 1.5) -> BacktestResult:
    res = BacktestResult(kind=kind, threshold=threshold, window_years=window_years)
    col, per_share = ("eps", False) if kind == "PER" else ("total_equity", True)
    daily = _fundamental_daily(d, col, per_share=per_share)
    if daily is None:
        res.warnings.append(f"{kind} 계산에 필요한 과거 펀더멘털이 부족해 백테스트를 건너뜁니다.")
        return res

    daily = daily.where(daily > 0)
    mult = (d.prices / daily).dropna()          # 일별 배수 (주가/펀더멘털)
    window = int(window_years * 252)
    minp = max(126, window // 2)
    if len(mult) < window + 126:
        res.warnings.append(
            f"유효 표본이 {len(mult)}일뿐이라 롤링 백테스트 신뢰도가 낮습니다 "
            f"(권장: {window + 126}일 이상). 결과를 참고용으로만 보세요.")

    # 정상 배수 = 과거 구간 롤링 중앙값 → 추정 적정가 = 정상배수 × 펀더멘털
    # 괴리율 = 적정가/주가 − 1 = 정상배수/현재배수 − 1
    fair_mult = mult.rolling(window, min_periods=minp).median()
    discount = (fair_mult / mult) - 1.0
    fair_price = fair_mult * daily.reindex(mult.index)

    df = pd.DataFrame({"price": d.prices.reindex(mult.index), "mult": mult,
                       "discount": discount}).dropna(subset=["discount", "price"])
    if df.empty:
        res.warnings.append("롤링 구간을 만들 만큼의 과거 데이터가 확보되지 않았습니다.")
        return res

    for h in HORIZONS.values():
        df[f"fwd_{h}"] = df["price"].shift(-h) / df["price"] - 1

    res.discount = df["discount"]
    res.fair_price = fair_price.reindex(df.index)

    # ── 이벤트 스터디: 신호(저평가) 시 vs 전체 ──
    sig = df["discount"] >= threshold
    res.signal_days = int(sig.sum())
    for label_kr, h in HORIZONS.items():
        c = f"fwd_{h}"
        allv = df[c].dropna()
        sigv = df.loc[sig, c].dropna()
        res.baseline_stats[label_kr] = {
            "mean": float(allv.mean()) if len(allv) else None,
            "hit": float((allv > 0).mean()) if len(allv) else None, "n": int(len(allv))}
        res.event_stats[label_kr] = {
            "mean": float(sigv.mean()) if len(sigv) else None,
            "median": float(sigv.median()) if len(sigv) else None,
            "hit": float((sigv > 0).mean()) if len(sigv) else None, "n": int(len(sigv))}
    if res.signal_days == 0:
        res.warnings.append(
            f"확보된 기간 동안 이 종목이 우리 기준 '저평가(적정가 대비 +{threshold*100:.0f}% 이상)'"
            "였던 적이 없습니다 — 이벤트 통계·전략을 평가할 수 없습니다. 임계값을 낮춰 보세요.")

    # ── 저평가율 vs 12개월 미래수익 순위상관 (양수 = 저평가일수록 이후 수익↑ = 툴 유효) ──
    sc = df.dropna(subset=["fwd_252"])[["discount", "fwd_252"]]
    if len(sc) >= 60:
        res.scatter = sc
        res.spearman = float(sc["discount"].rank().corr(sc["fwd_252"].rank()))

    # ── 예시 전략: 저평가 신호일 때만 보유(다음날 진입, 룩어헤드 방지), 아니면 현금 ──
    stock_ret = d.prices.pct_change()
    idx_ret = d.index_prices.pct_change().reindex(df.index)
    pos = sig.astype(float).shift(1)
    common = df.index
    strat = pos.reindex(common).fillna(0) * stock_ret.reindex(common).fillna(0)
    eq = pd.DataFrame({
        "저평가 매수 전략": (1 + strat).cumprod(),
        "단순 보유(Buy&Hold)": (1 + stock_ret.reindex(common).fillna(0)).cumprod(),
        f"{d.benchmark_name} 지수": (1 + idx_ret.reindex(common).fillna(0)).cumprod(),
    }, index=common)
    res.equity = eq
    res.cagr = {c: _cagr(eq[c]) for c in eq.columns}
    res.strategy_never_traded = res.signal_days == 0
    res.n_obs = len(df)
    res.ok = True
    return res
