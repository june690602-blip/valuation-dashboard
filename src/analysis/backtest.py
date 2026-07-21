"""밸류에이션 신호 백테스트 — "우리 기준으로 크게 저평가였을 때 샀다면 수익이 어땠나?"

핵심 질문: 이 종목이 **적정가 대비 크게 저평가**(우리 판정과 같은 기준)였던 과거 시점에
샀다면, 이후 수익률이 실제로 좋았는가? 그렇다면 우리 툴의 '저평가' 판정이 과거에 유효했다는
근거가 된다.

**어느 방법까지 사후검증할 수 있나 — 4방법 중 복원 가능한 ②+③만 종합한다:**
- ② 역사적 밴드: 자기 과거 배수(PER/PBR)의 롤링 중앙값 = '정상 배수'. 완전 복원 가능.
- ③ 수익가치(RIM): 그 시점 BPS·ROE로 적정 PBR을 되살림(자본비용 r은 상수 근사). 복원 가능.
- ① 업종 상대가치: 피어 목록이 **현재** 시점에 구성돼(생존·선택편향) 과거로 소급하면 룩어헤드가
  껴서 제외. ④ 선행이익(컨센서스): 과거 **시점별** 컨센서스 EPS 빈티지가 무료데이터에 없어 제외.
- 그래서 백테스트 신호 = ②·③ 괴리율을 기본가중(0.25:0.15 → 재정규화)으로 합친 **종합 괴리율**.
  ①④가 사후검증 밖이므로 종합 판정 전체가 아니라 그 하위집합의 검증임을 화면에 명시한다.

방법:
- 각 과거일 t에서 종합 괴리율(저평가율) = 종합 적정가/주가 − 1.
- 신호 = 종합 괴리율 ≥ 임계(기본 +30%, 앱 '크게 저평가' 기준과 동일).
- 이벤트 스터디: 신호가 뜬 모든 날의 이후 3/6/12개월 수익률(평균·중앙값·승률)을
  전체 기간 평균과 비교한다.

룩어헤드 방지: '정상 배수'·ROE·BPS 모두 그 시점까지의 과거 데이터만 쓴다(r만 상수 근사).

한계(단일 종목): 표본이 적고 과최적화·생존편향에 취약하다. RIM 적정가는 완만히 움직여
밴드보다 평균회귀 성격이 약하다. '과거의 평균회귀'가 미래를 보장하지 않으며, 전략 곡선은
거래비용·세금·슬리피지를 무시한 예시다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.models import CompanyData
from .valuation import METHOD_WEIGHTS, _fundamental_daily

# 미래수익률 측정 구간 (거래일 기준)
HORIZONS = {"3개월": 63, "6개월": 126, "12개월": 252}


@dataclass
class BacktestResult:
    kind: str = "PER"                          # 밴드 레그(②) 기준 배수 (PER/PBR)
    ok: bool = False
    threshold: float = 0.30                    # 저평가 임계(괴리율)
    methods_used: list = field(default_factory=list)   # 종합에 실제로 합류한 레그
    weights: dict = field(default_factory=dict)        # 레그별 재정규화 가중치
    discount: pd.Series | None = None          # 일별 저평가율(종합 적정가/주가 − 1)
    fair_price: pd.Series | None = None        # 추정 적정가(롤링 중앙값 배수 × 펀더멘털, 밴드 레그)
    signal_days: int = 0
    event_count: int = 0                       # 12개월 기준 비중복 신호 표본 수
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


def _non_overlapping_values(values: pd.Series, eligible: pd.Series, horizon: int) -> pd.Series:
    """Select forward-return observations whose holding windows do not overlap."""
    mask = eligible.reindex(values.index).fillna(False) & values.notna()
    chosen: list[float] = []
    next_allowed = 0
    for pos, ok in enumerate(mask.to_numpy(dtype=bool)):
        if ok and pos >= next_allowed:
            chosen.append(float(values.iloc[pos]))
            next_allowed = pos + horizon
    return pd.Series(chosen, dtype=float)


def _annual_daily(d: CompanyData, annual: pd.Series) -> pd.Series | None:
    """연간 비율(예: ROE) → '회계연도 종료 + 90일'부터 적용되는 일별 계단 시리즈.

    _fundamental_daily가 단일 컬럼용이라, 비율 시계열은 여기서 별도로 계단화한다."""
    fin = d.financials
    if "fiscal_end" not in fin.columns:
        return None
    df = pd.DataFrame({"v": annual, "fiscal_end": fin["fiscal_end"]}).dropna()
    if len(df) < 2:
        return None
    steps = pd.Series(df["v"].to_numpy(),
                      index=pd.to_datetime(df["fiscal_end"]) + pd.Timedelta(days=90)).sort_index()
    return steps.reindex(d.prices.index, method="ffill")


def _default_r_equity(market: str) -> float:
    """RIM 레그의 상수 자본비용 근사(베타=1 가정, R_f + MRP). 시장별 기본값."""
    return (0.035 + 0.06) if (market or "KR").upper() == "KR" else (0.045 + 0.05)


def _rim_discount(d: CompanyData, r_equity: float) -> pd.Series | None:
    """③ RIM 적정가의 일별 복원 → 저평가율(적정가/주가 − 1). ROE>0에서만 정의.

    지속계수 0.9 시나리오의 적정 PBR = 1 + (ROE−r)·0.9/(0.1+r) (valuation._rim과 동일 식).
    BPS·ROE는 그 시점 재무만 쓰고 r만 상수 근사 → 룩어헤드 없음."""
    bps = _fundamental_daily(d, "total_equity", per_share=True)
    fin = d.financials
    if bps is None or not {"net_income", "total_equity"}.issubset(fin.columns):
        return None
    roe_annual = (fin["net_income"] / fin["total_equity"]).replace([np.inf, -np.inf], np.nan)
    roe = _annual_daily(d, roe_annual)
    if roe is None:
        return None
    roe = roe.clip(-0.5, 0.6)
    bps = bps.where(bps > 0)
    fair_pbr = 1.0 + (roe - r_equity) * 0.9 / (0.1 + r_equity)
    fair = (bps * fair_pbr).where((roe > 0) & bps.notna())
    disc = (fair / d.prices.reindex(fair.index)) - 1.0
    return disc.where(fair > 0)


def _composite_discount(band: pd.Series, rim: pd.Series | None) -> tuple[pd.Series, list, dict]:
    """②·③ 저평가율을 기본가중(재정규화)으로 합친 종합 저평가율.

    두 레그가 다 있는 날은 가중평균, 한쪽만 있는 날은 그 값만 쓴다."""
    wb = METHOD_WEIGHTS["역사적 밴드"]
    wr = METHOD_WEIGHTS["수익가치(RIM)"]
    if rim is None:
        return band, ["역사적 밴드"], {"역사적 밴드": 1.0}
    rim = rim.reindex(band.index)
    mb, mr = band.notna(), rim.notna()
    wsum = wb * mb + wr * mr
    comp = (wb * band.where(mb, 0.0) + wr * rim.where(mr, 0.0))
    discount = (comp / wsum).where(wsum > 0)
    denom = wb + wr
    return discount, ["역사적 밴드", "수익가치(RIM)"], {"역사적 밴드": wb / denom,
                                                        "수익가치(RIM)": wr / denom}


def run_backtest(d: CompanyData, kind: str = "PER", threshold: float = 0.30,
                 window_years: float = 1.5, r_equity: float | None = None) -> BacktestResult:
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

    # ② 밴드 레그: 정상 배수 = 과거 구간 롤링 중앙값 → 적정가 = 정상배수 × 펀더멘털
    # 저평가율 = 적정가/주가 − 1 = 정상배수/현재배수 − 1
    fair_mult = mult.rolling(window, min_periods=minp).median()
    discount_band = (fair_mult / mult) - 1.0
    fair_price = fair_mult * daily.reindex(mult.index)

    # ③ RIM 레그: 복원 가능하면 종합에 합류(안 되면 밴드 단독)
    if r_equity is None:
        r_equity = _default_r_equity(d.market)
    discount_rim = _rim_discount(d, r_equity)
    discount, res.methods_used, res.weights = _composite_discount(
        discount_band, discount_rim.reindex(mult.index) if discount_rim is not None else None)
    if discount_rim is None:
        res.warnings.append("RIM(③) 복원에 필요한 ROE·장부가가 부족해 역사적 밴드(②) 단독으로 "
                            "검증합니다. 종합 판정의 일부만 사후검증됨에 유의하세요.")

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
        allv = _non_overlapping_values(df[c], pd.Series(True, index=df.index), h)
        sigv = _non_overlapping_values(df[c], sig, h)
        res.baseline_stats[label_kr] = {
            "mean": float(allv.mean()) if len(allv) else None,
            "hit": float((allv > 0).mean()) if len(allv) else None, "n": int(len(allv))}
        res.event_stats[label_kr] = {
            "mean": float(sigv.mean()) if len(sigv) else None,
            "median": float(sigv.median()) if len(sigv) else None,
            "hit": float((sigv > 0).mean()) if len(sigv) else None, "n": int(len(sigv))}
    res.event_count = int(res.event_stats.get("12개월", {}).get("n", 0))
    if res.signal_days == 0:
        res.warnings.append(
            f"확보된 기간 동안 이 종목이 우리 기준 '저평가(적정가 대비 +{threshold*100:.0f}% 이상)'"
            "였던 적이 없습니다 — 이벤트 통계·전략을 평가할 수 없습니다. 임계값을 낮춰 보세요.")
    elif res.event_count < 5:
        res.warnings.append(
            f"12개월 비중복 신호 표본이 {res.event_count}개뿐이라 평균수익·승률의 신뢰도가 낮습니다.")

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
