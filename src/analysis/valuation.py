"""적정주가 삼각측량: ① 업종 상대가치 ② 역사적 밴드 ③ RIM(잔여이익모델).

세 방법의 적정가 범위를 현재가와 비교해 5단계 판정을 내린다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.models import CompanyData
from .scoring import peer_median, sanitize_peer_frame

VERDICTS = ["크게 저평가", "저평가", "적정 수준", "고평가", "크게 고평가"]


@dataclass
class FairValue:
    method: str
    low: float
    mid: float
    high: float
    note: str = ""


@dataclass
class ValuationResult:
    estimates: list = field(default_factory=list)   # [FairValue]
    fair_low: float | None = None
    fair_mid: float | None = None
    fair_high: float | None = None
    gap: float | None = None            # 적정가(mid)/현재가 - 1  (+면 상승여력)
    verdict: str | None = None
    confidence: str | None = None       # 높음/중간/낮음
    per_band: pd.DataFrame | None = None   # 밴드 차트용 (price + 분위선)
    pbr_band: pd.DataFrame | None = None
    per_percentile: float | None = None    # 현재 PER의 5년 내 백분위
    pbr_percentile: float | None = None
    per_q: dict | None = None              # 5년 PER 분위 배수 {10:.., 25:.., 50:..}
    pbr_q: dict | None = None
    rim_fair_pbr: float | None = None
    rim_roe: float | None = None
    rim_r: float | None = None
    notes: list = field(default_factory=list)


# ── ① 업종 상대가치 ──────────────────────────────────────────────────
def _relative_value(d: CompanyData, eps, bps, ebitda_ps, debt_ps, cash_ps,
                    revenue_ps=None) -> FairValue | None:
    peers = sanitize_peer_frame(d.peers)
    fairs, used = [], []
    is_loss = not (eps and eps > 0)
    m = peer_median(peers, "per")
    if m and not is_loss:
        fairs.append(m * eps)
        used.append(f"PER {m:.1f}배")
    m = peer_median(peers, "pbr")
    if m and bps and bps > 0:
        fairs.append(m * bps)
        used.append(f"PBR {m:.2f}배")
    # 적자 기업은 이익 기반 배수를 못 쓰므로 매출 기반(PSR)을 보강
    if is_loss:
        m = peer_median(peers, "psr")
        if m and revenue_ps and revenue_ps > 0:
            fairs.append(m * revenue_ps)
            used.append(f"PSR {m:.1f}배")
    if not d.is_financial:
        m = peer_median(peers, "ev_ebitda")
        if m and ebitda_ps and ebitda_ps > 0:
            fair = m * ebitda_ps - (debt_ps or 0) + (cash_ps or 0)
            if fair > 0:
                fairs.append(fair)
                used.append(f"EV/EBITDA {m:.1f}배")
    if not fairs:
        return None
    return FairValue("업종 상대가치", min(fairs), float(np.median(fairs)), max(fairs),
                     note="피어 중앙값 " + ", ".join(used))


# ── ② 역사적 밴드 ────────────────────────────────────────────────────
def _fundamental_daily(d: CompanyData, col: str, per_share: bool = True) -> pd.Series | None:
    """연간 값(EPS/BPS)을 '회계연도 종료 + 90일'부터 적용되는 일별 계단 시리즈로 변환."""
    fin = d.financials
    if col not in fin.columns or "fiscal_end" not in fin.columns:
        return None
    vals = fin[[col, "fiscal_end"]].dropna()
    if len(vals) < 2:
        return None
    steps = pd.Series(
        vals[col].values,
        index=pd.to_datetime(vals["fiscal_end"]) + pd.Timedelta(days=90),
    ).sort_index()
    daily = steps.reindex(d.prices.index, method="ffill")
    return daily / d.shares_outstanding if per_share else daily


def _band(d: CompanyData, current_fund: float | None, kind: str):
    """(밴드 df, 현재 배수 백분위, FairValue 구성요소, 분위 배수 dict) — kind: 'per'|'pbr'"""
    col = "eps" if kind == "per" else "total_equity"
    per_share = kind == "pbr"  # eps는 이미 주당, equity는 주식수로 나눔
    daily = _fundamental_daily(d, col, per_share=per_share)
    if daily is None:
        return None, None, None, None
    daily = daily.where(daily > 0)
    mult = (d.prices / daily).dropna()
    if len(mult) < 200:
        return None, None, None, None
    q = mult.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    qdict = {int(p * 100): float(v) for p, v in q.items()}
    qdict["current"] = float(mult.iloc[-1])
    pct = float((mult < mult.iloc[-1]).mean() * 100)
    band = pd.DataFrame({"price": d.prices})
    for p, v in q.items():
        band[f"q{int(p * 100)}"] = daily * v
    band = band.dropna(subset=["price"])
    if not current_fund or current_fund <= 0:
        return band, pct, None, qdict
    fair = (float(q.loc[0.25]) * current_fund,
            float(q.loc[0.50]) * current_fund,
            float(q.loc[0.75]) * current_fund)
    return band, pct, fair, qdict


# ── ③ RIM (잔여이익모델 간이형) ──────────────────────────────────────
def _rim(bps: float | None, roe: float | None, r: float):
    """지속계수 w ∈ {0.8, 0.9, 1.0} 시나리오.

    w=1: V = B·ROE/r (초과이익 영구 지속)
    w<1: V = B + B·(ROE-r)·w / (1 + r - w) (초과이익이 매년 w배로 소멸)
    """
    if not bps or bps <= 0 or roe is None or roe <= 0 or r <= 0:
        return None, None
    vals = {}
    for w in (0.8, 0.9, 1.0):
        if w >= 1.0:
            v = bps * roe / r
        else:
            v = bps + bps * (roe - r) * w / (1 + r - w)
        vals[w] = max(v, 0.0)
    fair_pbr = (bps and vals[0.9] / bps) or None
    lo, hi = min(vals.values()), max(vals.values())
    return FairValue("수익가치(RIM)", lo, vals[0.9], hi,
                     note=f"ROE {roe:.1%}, r {r:.1%}, 지속계수 0.8~1.0"), fair_pbr


def _recent_roe(d: CompanyData, ttm_roe: float | None) -> float | None:
    """TTM과 최근 3개년 평균을 절반씩 섞은 ROE (클리핑 없이 원값 반환)."""
    fin = d.financials
    eq = fin["total_equity"]
    avg_eq = ((eq + eq.shift(1)) / 2).fillna(eq)
    s = (fin["net_income"] / avg_eq).dropna().tail(3)
    hist = float(s.mean()) if len(s) else None
    if ttm_roe is not None and hist is not None:
        return 0.5 * ttm_roe + 0.5 * hist
    return ttm_roe if ttm_roe is not None else hist


# ── 종합 ────────────────────────────────────────────────────────────
def compute_valuation(d: CompanyData, ind, r_equity: float) -> ValuationResult:
    """ind: Indicators, r_equity: RIM 요구수익률(기본 CAPM k_e)."""
    res = ValuationResult()
    shares = d.shares_outstanding
    eps = d.latest("eps")
    equity = d.latest("total_equity")
    bps = equity / shares if equity else None
    ebitda = d.latest("ebitda")
    ebitda_ps = ebitda / shares if ebitda else None
    debt_ps = (d.latest("total_debt") or 0) / shares
    cash_ps = (d.latest("cash") or 0) / shares

    # ① 상대가치
    revenue = d.latest("revenue")
    revenue_ps = revenue / shares if revenue else None
    fv = _relative_value(d, eps, bps, ebitda_ps, debt_ps, cash_ps, revenue_ps)
    if fv:
        res.estimates.append(fv)
    else:
        res.notes.append("피어 표본이 부족해 상대가치 평가를 건너뜁니다.")

    # ② 역사적 밴드 (PER 우선, 적자면 PBR)
    res.per_band, res.per_percentile, per_fair, res.per_q = _band(
        d, eps if eps and eps > 0 else None, "per")
    res.pbr_band, res.pbr_percentile, pbr_fair, res.pbr_q = _band(d, bps, "pbr")
    fair = per_fair or pbr_fair
    if fair:
        basis = "PER" if per_fair else "PBR(적자로 대체)"
        res.estimates.append(FairValue("역사적 밴드", fair[0], fair[1], fair[2],
                                       note=f"5년 {basis} 25~75분위 × 현재 펀더멘털"))
    else:
        res.notes.append("상장기간이 짧거나 적자가 길어 역사적 밴드를 계산하지 못했습니다.")

    # ③ RIM — 장부자본이 왜곡된 기업(대규모 자사주 매입 등)은 건너뜀
    ttm_roe = ind.profitability.get("roe")
    roe_raw = _recent_roe(d, ttm_roe)
    pbr_actual = d.market_cap / equity if equity and equity > 0 else None
    book_distorted = (roe_raw is not None and roe_raw > 0.6) or \
                     (pbr_actual is not None and pbr_actual > 12) or pbr_actual is None
    if book_distorted:
        res.notes.append("자사주 매입 등으로 장부자본이 극단적으로 작아 "
                         "RIM(장부가치 기반) 평가는 신뢰할 수 없어 건너뜁니다.")
        res.rim_r = r_equity
    else:
        roe_used = float(np.clip(roe_raw, -0.5, 0.6)) if roe_raw is not None else None
        rim, fair_pbr = _rim(bps, roe_used, r_equity)
        res.rim_roe, res.rim_r, res.rim_fair_pbr = roe_used, r_equity, fair_pbr
        if rim:
            res.estimates.append(rim)
        else:
            res.notes.append("ROE가 0 이하라 RIM 평가를 건너뜁니다 (적자 기업).")

    # 종합 판정
    if res.estimates:
        mids = [e.mid for e in res.estimates]
        res.fair_low = float(np.mean([e.low for e in res.estimates]))
        res.fair_mid = float(np.mean(mids))
        res.fair_high = float(np.mean([e.high for e in res.estimates]))
        res.gap = res.fair_mid / d.price - 1
        g = res.gap
        res.verdict = (VERDICTS[0] if g >= 0.30 else
                       VERDICTS[1] if g >= 0.10 else
                       VERDICTS[2] if g > -0.10 else
                       VERDICTS[3] if g > -0.30 else VERDICTS[4])
        if len(mids) >= 2 and res.fair_mid:
            disp = float(np.std(mids) / abs(np.mean(mids)))
            res.confidence = "높음" if disp < 0.15 else "중간" if disp < 0.35 else "낮음"
            if res.confidence == "낮음":
                res.notes.append(f"평가 방법 간 편차가 큽니다(±{disp:.0%}). "
                                 "판정을 보수적으로 해석하세요.")
        else:
            res.confidence = "낮음"
            res.notes.append("사용 가능한 평가 방법이 1개뿐이라 신뢰도가 낮습니다.")
    return res
