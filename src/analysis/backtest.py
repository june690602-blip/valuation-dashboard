"""밸류에이션 신호 백테스트 — "싸다는 판단이 실제로 초과수익으로 이어졌나?"

검증 대상은 ③ 역사적 밴드 레그, 즉 **자기 자신의 PER/PBR이 과거 밴드에서 쌀 때
샀다면 이후 수익률이 좋았는가**이다. 종합 판정(피어 중앙값·RIM 포함)은 시점별
피어 데이터를 과거로 복원하기 어려워(생존편향·룩어헤드) 백테스트 대상에서 제외한다.

룩어헤드 방지: 각 시점의 '저평가 여부'는 그 시점까지의 **과거 데이터로만** 만든
롤링 백분위로 판단한다(전체표본 분위수를 쓰면 미래를 미리 본 셈이 됨).

한계(단일 종목 백테스트): 표본이 적고 과최적화에 취약하며, 지금 상장해 있다는
사실 자체가 생존편향이다. '과거의 평균회귀'가 미래를 보장하지 않는다.
전략 곡선은 거래비용·세금·슬리피지를 무시한 예시이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.models import CompanyData
from .valuation import _fundamental_daily

# 미래수익률 측정 구간 (거래일 기준)
HORIZONS = {"3개월": 63, "6개월": 126, "12개월": 252}
BUCKETS = ["저평가 구간", "중립 구간", "고평가 구간"]


@dataclass
class BacktestResult:
    kind: str = "PER"
    ok: bool = False
    multiple: pd.Series | None = None        # 일별 배수 (주가/펀더멘털)
    pct_rank: pd.Series | None = None         # 롤링 백분위(0~100)
    bucket_returns: pd.DataFrame | None = None  # index=구간, col=구간별 평균 미래수익
    bucket_hit: pd.DataFrame | None = None      # 구간별 플러스 확률
    bucket_counts: dict = field(default_factory=dict)
    scatter: pd.DataFrame | None = None       # pct, fwd(12M) 산점
    spearman: float | None = None             # 백분위 vs 미래수익 순위상관(음수면 평균회귀)
    equity: pd.DataFrame | None = None        # 타이밍/보유/지수 누적수익
    strategy_never_traded: bool = False
    cagr: dict = field(default_factory=dict)
    window_years: float = 2.0
    n_obs: int = 0
    cheap_th: float = 33.0
    rich_th: float = 66.0
    warnings: list = field(default_factory=list)


def _rolling_pct_rank(s: pd.Series, window: int, minp: int) -> pd.Series:
    """각 시점 값이 '직전 window 구간' 안에서 차지하는 백분위(0~100). 과거만 사용."""
    def rank(x):
        return float((x[:-1] < x[-1]).mean() * 100) if len(x) > 1 else np.nan
    return s.rolling(window, min_periods=minp).apply(rank, raw=True)


def _cagr(equity: pd.Series) -> float | None:
    e = equity.dropna()
    if len(e) < 2 or e.iloc[0] <= 0:
        return None
    years = (e.index[-1] - e.index[0]).days / 365.25
    if years <= 0:
        return None
    return float((e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1)


def run_backtest(d: CompanyData, kind: str = "PER",
                 cheap_th: float = 33.0, rich_th: float = 66.0,
                 window_years: float = 1.5) -> BacktestResult:
    res = BacktestResult(kind=kind, cheap_th=cheap_th, rich_th=rich_th,
                         window_years=window_years)
    col, per_share = ("eps", False) if kind == "PER" else ("total_equity", True)
    daily = _fundamental_daily(d, col, per_share=per_share)
    if daily is None:
        res.warnings.append(f"{kind} 계산에 필요한 과거 펀더멘털이 부족해 백테스트를 건너뜁니다.")
        return res

    daily = daily.where(daily > 0)
    mult = (d.prices / daily).dropna()
    window = int(window_years * 252)
    minp = max(126, window // 2)
    if len(mult) < window + 126:
        res.warnings.append(
            f"유효 표본이 {len(mult)}일뿐이라 롤링 백테스트 신뢰도가 낮습니다 "
            f"(권장: {window + 126}일 이상). 결과를 참고용으로만 보세요.")

    pct = _rolling_pct_rank(mult, window, minp)
    df = pd.DataFrame({"price": d.prices.reindex(mult.index), "mult": mult, "pct": pct})
    df = df.dropna(subset=["mult", "pct"])
    if df.empty:
        res.warnings.append("롤링 백분위를 만들 만큼의 과거 구간이 확보되지 않았습니다.")
        return res

    # 미래수익률
    for h in HORIZONS.values():
        df[f"fwd_{h}"] = df["price"].shift(-h) / df["price"] - 1

    # 구간 분류 (과거 기준 백분위)
    df["bucket"] = pd.cut(df["pct"], [-0.1, cheap_th, rich_th, 100.1], labels=BUCKETS)

    bret, bhit, counts = {}, {}, {}
    for label_kr, h in HORIZONS.items():
        col_h = f"fwd_{h}"
        valid = df.dropna(subset=[col_h])
        g = valid.groupby("bucket", observed=False)[col_h]
        bret[label_kr] = g.mean()
        bhit[label_kr] = g.apply(lambda x: (x > 0).mean() if len(x) else np.nan)
        if label_kr == "12개월":
            counts = {b: int(v) for b, v in g.count().items()}
    res.bucket_returns = pd.DataFrame(bret).reindex(BUCKETS)
    res.bucket_hit = pd.DataFrame(bhit).reindex(BUCKETS)
    res.bucket_counts = counts
    if counts.get("저평가 구간", 0) == 0:
        res.warnings.append(
            "확보된 기간 동안 이 종목의 {}이(가) 자기 하위 {:.0f}% 구간에 들어간 적이 없습니다 "
            "— '저평가 구간' 통계와 타이밍 전략은 평가할 수 없습니다.".format(kind, cheap_th))

    # 백분위 vs 12개월 미래수익 순위상관 (음수 = 쌀수록 이후 수익 높음 = 평균회귀)
    sc = df.dropna(subset=["fwd_252"])[["pct", "fwd_252"]]
    if len(sc) >= 60:
        res.scatter = sc
        # scipy 없이 순위상관: 순위로 바꾼 뒤 Pearson (= Spearman)
        res.spearman = float(sc["pct"].rank().corr(sc["fwd_252"].rank()))

    # 예시 타이밍 전략: '저평가 구간'일 때만 보유(다음날 진입, 룩어헤드 방지), 아니면 현금
    stock_ret = d.prices.pct_change()
    idx_ret = d.index_prices.pct_change().reindex(df.index)
    pos = (df["pct"] < cheap_th).astype(float).shift(1)  # 신호 다음날 반영
    common = df.index
    strat = (pos.reindex(common).fillna(0) * stock_ret.reindex(common).fillna(0))
    eq = pd.DataFrame({
        "밸류에이션 타이밍": (1 + strat).cumprod(),
        "단순 보유(Buy&Hold)": (1 + stock_ret.reindex(common).fillna(0)).cumprod(),
        f"{d.benchmark_name} 지수": (1 + idx_ret.reindex(common).fillna(0)).cumprod(),
    }, index=common)
    res.equity = eq
    res.cagr = {c: _cagr(eq[c]) for c in eq.columns}
    # 전략이 한 번도 투자하지 않았으면(=저평가 신호 전무) 곡선이 무의미
    res.strategy_never_traded = float(pos.reindex(common).fillna(0).sum()) == 0
    res.n_obs = len(df)
    res.ok = res.bucket_returns is not None
    return res
