"""적정주가 삼각측량: ① 업종 상대가치 ② 역사적 밴드 ③ RIM ④ 선행 이익(컨센서스).

①~③은 과거(TTM) 실적 기반, ④는 애널리스트 컨센서스 12개월 선행 EPS 기반 —
증권사 목표주가의 지배적 산식(선행 EPS × 타깃 멀티플)을 따른다.
방법별 적정가 중심값을 **가격 설명력 순위 기반 가중평균**(METHOD_WEIGHTS)해
현재가와 비교해 5단계 판정을 내린다. 설계 근거·대안·한계는 docs/adr/0003 참고
(0003이 0001의 '동일가중 산술평균'을 대체함). 동일가중 결과도 함께 계산해
가중치가 결론을 좌우하지 않음을 화면에 병기한다(민감도 노출).
컨센서스 '목표주가' 자체는 종합에 섞지 않고 외부 교차검증치로만 쓴다.

**②와 ④는 서로 독립적인 관점이 아니다(R2 실측).** 둘은 같은 식이고 곱하는 EPS만 다르다 —
  ② = 자기 5년 PER 중앙값 × TTM EPS,  ④ = 자기 5년 PER 중앙값 × 컨센서스 선행 EPS
그래서 ④÷②는 항상 정확히 (선행EPS ÷ TTM EPS)다(10종목 패널 전부에서 확인).
'네 관점의 삼각측량'에서 ②·④는 같은 관점의 과거판·미래판이고, 종합 적정가의 실효
의존도는 명목 가중 0.60이 아니라 **중앙 76%**다(③이 꺼지면 더 오른다). 이 사실을 숨기면
"방법들이 서로 동의했다"로 오독되므로 `shared_multiple_share`로 계산해 화면에 밝힌다.
근거·실측은 docs/review/R2-가정적합성.md, 재현은 scripts/check_sensitivity.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.models import CompanyData, actual_prices, currency_mismatch
from .scoring import comparable_peers, peer_median, sanitize_peer_frame

VERDICTS = ["크게 저평가", "저평가", "적정 수준", "고평가", "크게 고평가"]


def _verdict(gap: float) -> str:
    """괴리율 → 5단계 판정 (±10%/±30% 기준)."""
    return (VERDICTS[0] if gap >= 0.30 else
            VERDICTS[1] if gap >= 0.10 else
            VERDICTS[2] if gap > -0.10 else
            VERDICTS[3] if gap > -0.30 else VERDICTS[4])

# 방법별 가중치 — 가격 설명력 순위(선행이익 > 이익 멀티플 > 장부가 기반)를 인코딩한 기본값.
# 근거: Liu·Nissim·Thomas(2002, JAR, 미국)와 그 국제 확장(2007, FAJ, 10개국)에서
# 선행EPS 멀티플이 현금흐름·배당·장부가를 모든 시장에서 압도했고, 국내 가치관련성
# 연구(Ohlson 모형 기반)도 이익>장부가 순위를 지지한다. 순위는 국제·국내 공통이나
# 절대 수치(35/25/25/15)는 한국 데이터로 추정한 값이 아니라 순위의 정성적 인코딩이다.
# ④는 국내 컨센서스 낙관편의(자본시장연구원 2025)에 노출되므로 '편향 없는 값'이 아니라
# '시장기대 앵커'로 읽어야 한다. 배수측은 애널리스트 목표주가의 내재 배수를 쓰지 않아
# **낙관편의가 직접 들어오지는 않지만**, R2 실측에서 자기 5년 PER 중앙값이 사이클 전환
# 종목에서 목표가 내재배수보다 45~49% 높게 나왔다 — '차단'이 아니라 '다른 종류의 오차로
# 바꾼 것'에 가깝다(_forward_value 도크스트링·이슈 #62).
# 상세·대안·한계·인용은 docs/adr/0003. 사용 가능한 방법만으로 재정규화해 합이 1이 되게 쓴다.
METHOD_WEIGHTS = {
    "선행 이익(컨센서스)": 0.35,
    "업종 상대가치": 0.25,
    "역사적 밴드": 0.25,
    "수익가치(RIM)": 0.15,
}


@dataclass
class FairValue:
    method: str
    low: float
    mid: float
    high: float
    note: str = ""


@dataclass
class ValuationNote:
    """판정을 **읽는 법**을 알리는 한 줄. 등급을 문장 안이 아니라 필드로 든다.

    이전에는 `"주의 · …"`처럼 **문자열 접두어**로 경고를 표시하고 화면 쪽에서 그 접두어를
    부분일치로 찾아 등급을 정했다. R3 발견 7이 정확히 그 방식으로 조용히 깨진 사례다 —
    파이썬은 `'순수한 저평가'`를 만드는데 자바스크립트는 `'순수 저평가'`를 찾아, 강조가
    첫 커밋부터 한 번도 켜진 적이 없었다(#68). 등급은 데이터로 든다.
    """
    kind: str   # warn(먼저 봐야 함) | info(알아두면 좋음)
    text: str


@dataclass
class ValuationResult:
    estimates: list = field(default_factory=list)   # [FairValue]
    fair_low: float | None = None
    fair_mid: float | None = None
    fair_high: float | None = None
    gap: float | None = None            # 적정가(mid)/현재가 - 1  (+면 상승여력)
    verdict: str | None = None
    confidence: str | None = None       # 높음/중간/낮음
    fair_mid_equal: float | None = None  # 동일가중 종합(민감도 비교용)
    gap_equal: float | None = None       # 동일가중 괴리율
    verdict_equal: str | None = None     # 동일가중 판정
    dispersion: float | None = None      # 방법 간 중심값 변동계수(σ/|μ|) — 신뢰도 산출 근거
    per_band: pd.DataFrame | None = None   # 밴드 차트용 (price + 분위선)
    pbr_band: pd.DataFrame | None = None
    per_percentile: float | None = None    # 현재 PER의 5년 내 백분위
    pbr_percentile: float | None = None
    per_q: dict | None = None              # 5년 PER 분위 배수 {10:.., 25:.., 50:..}
    pbr_q: dict | None = None
    rim_fair_pbr: float | None = None
    rim_roe: float | None = None
    rim_r: float | None = None
    forward_eps: float | None = None       # ④에 사용한 컨센서스 12개월 EPS
    forward_growth: float | None = None    # 선행 EPS / TTM EPS - 1 (내재 성장률)
    weights: dict = field(default_factory=dict)   # 종합에 쓴 방법별 가중치 (재정규화)
    skipped: list = field(default_factory=list)   # [(방법명, 건너뛴 사유)] — 번호 자리 유지용
    # ②·④가 함께 쓰는 '자기 5년 PER 중앙값' 하나에 종합 적정가가 실제로 얼마나 매달려 있나
    # (0~1). 명목 가중 합(0.60)이 아니라 **중심값 크기까지 반영한 실효 의존도**다 —
    # 이 배수가 10% 틀리면 종합도 이 비율만큼 틀린다.
    shared_multiple_share: float | None = None
    # [ValuationNote] — 계산이 스스로 남긴 '이 판정을 읽는 법'.
    # 지표 해설(commentary.py)과 성격이 다르다: 저건 판정의 **근거**고 이건 판정을 **읽는 법**이다.
    notes: list = field(default_factory=list)


# ── ① 업종 상대가치 ──────────────────────────────────────────────────
def _rel_fairs(peers, d: CompanyData, eps, bps, ebitda_ps, debt_ps, cash_ps,
               revenue_ps, min_n: int):
    """주어진 피어 프레임에서 배수별 적정가 후보 목록을 만든다."""
    fairs, used = [], []
    is_loss = not (eps and eps > 0)
    m = peer_median(peers, "per", min_n=min_n)
    if m and not is_loss:
        fairs.append(m * eps)
        used.append(f"PER {m:.1f}배")
    m = peer_median(peers, "pbr", min_n=min_n)
    if m and bps and bps > 0:
        fairs.append(m * bps)
        used.append(f"PBR {m:.2f}배")
    # 적자 기업은 이익 기반 배수를 못 쓰므로 매출 기반(PSR)을 보강
    if is_loss:
        m = peer_median(peers, "psr", min_n=min_n)
        if m and revenue_ps and revenue_ps > 0:
            fairs.append(m * revenue_ps)
            used.append(f"PSR {m:.1f}배")
    if not d.is_financial:
        m = peer_median(peers, "ev_ebitda", min_n=min_n)
        if m and ebitda_ps and ebitda_ps > 0:
            fair = m * ebitda_ps - (debt_ps or 0) + (cash_ps or 0)
            if fair > 0:
                fairs.append(fair)
                used.append(f"EV/EBITDA {m:.1f}배")
    return fairs, used


def _relative_value(d: CompanyData, eps, bps, ebitda_ps, debt_ps, cash_ps,
                    revenue_ps=None) -> FairValue | None:
    """규모 비교가능 피어(시총 1/20~20배) 우선 — 품질 필터를 거쳤으므로 표본 2개부터
    허용. 부족하면 전체 피어로 폴백하되 규모 차이 경고를 note에 남긴다
    (AI 피어에 초소형주가 섞이면 중앙값이 소형주 디스카운트에 오염되기 때문)."""
    sized = comparable_peers(d.peers, d.market_cap)
    fairs, used = _rel_fairs(sized, d, eps, bps, ebitda_ps, debt_ps, cash_ps,
                             revenue_ps, min_n=2)
    suffix = ""
    if not fairs:
        full = sanitize_peer_frame(d.peers)
        fairs, used = _rel_fairs(full, d, eps, bps, ebitda_ps, debt_ps, cash_ps,
                                 revenue_ps, min_n=3)
        suffix = " · 전체 피어(자사와 규모 차이 커 신뢰 주의)"
    if not fairs:
        return None
    return FairValue("업종 상대가치", min(fairs), float(np.median(fairs)), max(fairs),
                     note="피어 중앙값 " + ", ".join(used) + suffix)


# ── ② 역사적 밴드 ────────────────────────────────────────────────────
def _fundamental_daily(d: CompanyData, col: str, per_share: bool = True) -> pd.Series | None:
    """연간 값(EPS/BPS)을 '회계연도 종료 + 90일'부터 적용되는 일별 계단 시리즈로 변환."""
    fin = d.financials
    if col not in fin.columns or "fiscal_end" not in fin.columns:
        return None
    required = [col, "fiscal_end"]
    if per_share:
        if "shares_outstanding" not in fin.columns:
            return None
        required.append("shares_outstanding")
    vals = fin[required].dropna()
    if len(vals) < 2:
        return None
    values = vals[col]
    if per_share:
        shares = vals["shares_outstanding"].where(vals["shares_outstanding"] > 0)
        values = values / shares
    steps = pd.Series(
        values.values,
        index=pd.to_datetime(vals["fiscal_end"]) + pd.Timedelta(days=90),
    ).sort_index()
    daily = steps.reindex(d.prices.index, method="ffill")   # 거래일 축 정렬(값 자체는 무관)
    return daily


def _band(d: CompanyData, current_fund: float | None, kind: str):
    """(밴드 df, 현재 배수 백분위, FairValue 구성요소, 분위 배수 dict) — kind: 'per'|'pbr'"""
    col = "eps" if kind == "per" else "total_equity"
    per_share = kind == "pbr"  # eps는 이미 주당, equity는 주식수로 나눔
    daily = _fundamental_daily(d, col, per_share=per_share)
    if daily is None:
        return None, None, None, None
    daily = daily.where(daily > 0)
    # 과거 배수는 '그날 실제 주가 ÷ 그날 펀더멘털'이라 미조정 종가를 써야 한다. 수정종가는
    # 과거를 그 뒤 지급된 배당만큼 낮춰 잡아 과거 PER·PBR이 실제보다 낮게 깔리고, 그만큼
    # 적정가(분위 배수 × 현재 펀더멘털)가 낮아져 현재가가 늘 비싸 보인다. 고배당일수록 심하다.
    px = actual_prices(d)
    mult = (px / daily).dropna()
    if len(mult) < 200:
        return None, None, None, None
    q = mult.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    qdict = {int(p * 100): float(v) for p, v in q.items()}
    qdict["current"] = float(mult.iloc[-1])
    pct = float((mult < mult.iloc[-1]).mean() * 100)
    band = pd.DataFrame({"price": px})   # 밴드와 같은 기준이어야 차트에서 위치가 맞는다
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
    """지속계수 w ∈ {0.6, 0.8, 1.0} 시나리오, 중심 w=0.8.

    w=1: V = B·ROE/r (초과이익 영구 지속)
    w<1: V = B + B·(ROE-r)·w / (1 + r - w) (Ohlson α₁ — 초과이익이 매년 w배로 소멸)

    w를 (0.8, 0.9, 1.0)에서 (0.6, 0.8, 1.0)으로 낮췄다. w는 '초과이익이 얼마나 오래
    가는가'인데, 기존 하단(0.8)조차 공개된 실증 추정치(연구에 따라 0.27~0.73)보다
    높아 범위 전체가 낙관 쪽에 있었다. 감도가 커서 이 하나로 적정가가 크게 달라진다 —
    ROE 20%·r 10%면 w=0.6에서 112, w=0.9에서 145, w=1.0에서 200.
    범위를 넓힌 건 '어느 값이 맞다'가 아니라 이 가정의 불확실성 자체를 드러내기 위해서다.
    """
    if not bps or bps <= 0 or roe is None or roe <= 0 or r <= 0:
        return None, None
    vals = {}
    for w in (0.6, 0.8, 1.0):
        if w >= 1.0:
            v = bps * roe / r
        else:
            v = bps + bps * (roe - r) * w / (1 + r - w)
        vals[w] = max(v, 0.0)
    fair_pbr = (bps and vals[0.8] / bps) or None
    lo, hi = min(vals.values()), max(vals.values())
    return FairValue("수익가치(RIM)", lo, vals[0.8], hi,
                     note=f"ROE {roe:.1%}, r {r:.1%}, 지속계수 0.6~1.0"), fair_pbr


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


# ── ④ 선행 이익 (컨센서스 12개월 EPS × 타깃 멀티플) ─────────────────
def _forward_value(fwd_eps: float | None, peer_fwd_per: float | None,
                   per_q: dict | None) -> FairValue | None:
    """중심 = 타깃 멀티플 × 선행 EPS. 타깃 멀티플은 **자기 5년 PER 중앙값 우선**,
    없으면 피어 선행PER 폴백. 범위는 자기 5년 밴드 q25~q75.

    근거(실증): 11종목 횡단면 테스트(scripts/check_multiple_rules.py)에서 자기 5년
    중앙값이 |log(예측/현재가)| 최소(0.26)였고, 증권사 목표주가의 내재 멀티플과
    **중앙값 기준** +2% 이내로 일치했다. 피어 선행PER 중앙값은 AI 피어에 소형주가
    섞이면 체계적으로 과소 추정된다(오차 0.65). 규칙 선택 자체는
    2026-07-28 재실행에서도 유지됐다 — 가격오차 0.286으로 여전히 최소, 목표가 대비 +3.4%.

    **다만 '+2% 이내'는 중앙값 이야기이고 종목별로는 성립하지 않는다(R2 실측).**
    같은 재실행에서 종목별 배수 괴리(자기 5년 중앙 ÷ 목표가 내재배수 − 1)는
    절대값 중앙 21.9%, 범위 −62%~+49%였다. 어긋나는 방향에는 규칙이 있다 —
    **이익이 크게 움직이는 사이클 전환 종목에서 배수가 과대**하다. 이익이 눌려 있던
    기간은 PER이 높게 깔리는데(분모가 작아서), 그 배수를 회복된 선행 EPS에 곱하면
    한 번의 회복을 두 번 센다. 실측: 삼성전자 16.0배 vs 목표가 내재 11.0배(+45.6%),
    SK하이닉스 17.1배 vs 11.5배(+48.7%) — 둘 다 ④가 증권가 목표주가보다 높게 나온다.
    반대로 이익이 줄어드는 종목은 배수가 과소하다(현대차 −62.3%).

    근본 해결(정규화 이익 또는 배수·이익의 국면 맞추기)은 새 가정을 얹는 일이라 이슈 #62로
    분리했다. 여기서는 값을 그대로 두고 compute_valuation()이 위상 불일치를 화면에 경고한다.
    실측·재현은 docs/review/R2-가정적합성.md, scripts/check_sensitivity.py.
    """
    if not fwd_eps or fwd_eps <= 0:
        return None
    q25 = per_q.get(25) if per_q else None
    q50 = per_q.get(50) if per_q else None
    q75 = per_q.get(75) if per_q else None
    if q50 and q50 > 0:
        mult, label = q50, "자기 5년 PER 중앙값"
    elif peer_fwd_per and peer_fwd_per > 0:
        mult, label = peer_fwd_per, "피어 선행PER"
    else:
        return None
    mid = mult * fwd_eps
    lo = q25 * fwd_eps if q25 else mid
    hi = q75 * fwd_eps if q75 else mid
    # note는 요약 차트 라벨 폭(~34자)에 맞춰 짧게 유지한다
    return FairValue("선행 이익(컨센서스)", min(lo, mid), mid, max(hi, mid),
                     note=f"컨센서스 EPS × {label} {mult:.1f}배")


# ── 종합 ────────────────────────────────────────────────────────────
def compute_valuation(d: CompanyData, ind, r_equity: float) -> ValuationResult:
    """ind: Indicators, r_equity: RIM 요구수익률(기본 CAPM k_e)."""
    res = ValuationResult()

    # 재무 통화 ≠ 주가 통화(ADR 등)면 ①②③은 모두 '주가 ÷ 재무값' 비교라 성립하지 않는다
    # (실측: TSMC 재무 TWD·주가 USD → 자체 PER 0.93, 야후 공시 35.60). ④ 컨센서스 선행이익은
    # 야후가 주가 통화로 집계해 주므로 살아남는다. 틀린 값을 그럴듯하게 보여주느니 건너뛴다.
    mismatch = currency_mismatch(d)
    ccy_reason = f"재무 통화({mismatch}) ≠ 주가 통화({d.currency})" if mismatch else ""
    if mismatch:
        res.notes.append(ValuationNote(
            "warn",
            f"재무제표는 {mismatch}로 공시되는데 주가는 {d.currency}입니다(ADR 등). 주가를 재무 값으로 "
            "나누는 평가(업종 상대가치·역사적 밴드·RIM)는 환율만큼 어긋나 제외합니다 — 컨센서스 "
            "선행이익 방법만 사용하므로 판정 신뢰도가 낮습니다."))

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
    fv = None if mismatch else _relative_value(
        d, eps, bps, ebitda_ps, debt_ps, cash_ps, revenue_ps)
    if fv:
        res.estimates.append(fv)
    elif mismatch:
        res.skipped.append(("업종 상대가치", ccy_reason))
    else:
        res.skipped.append(("업종 상대가치", "피어 표본 부족"))
        res.notes.append(ValuationNote("info", "피어 표본이 부족해 상대가치 평가를 제외합니다."))

    # ② 역사적 밴드 (PER 우선, 적자면 PBR)
    # 통화가 섞이면 밴드 자체가 무의미하므로 계산하지 않는다 — 차트에도 그려지면 안 된다.
    per_fair = pbr_fair = None
    if not mismatch:
        res.per_band, res.per_percentile, per_fair, res.per_q = _band(
            d, eps if eps and eps > 0 else None, "per")
        res.pbr_band, res.pbr_percentile, pbr_fair, res.pbr_q = _band(d, bps, "pbr")
    fair = per_fair or pbr_fair
    if mismatch:
        res.skipped.append(("역사적 밴드", ccy_reason))
    elif fair:
        basis = "PER" if per_fair else "PBR(적자로 대체)"
        res.estimates.append(FairValue("역사적 밴드", fair[0], fair[1], fair[2],
                                       note=f"5년 {basis} 25~75분위 × 현재 펀더멘털"))
    else:
        res.skipped.append(("역사적 밴드", "상장기간 짧음 또는 적자 지속"))
        res.notes.append(ValuationNote(
            "info", "상장기간이 짧거나 적자가 길어 역사적 밴드를 계산하지 못했습니다."))

    # ③ RIM — 장부자본이 왜곡된 기업(대규모 자사주 매입 등)은 건너뜀
    ttm_roe = ind.profitability.get("roe")
    roe_raw = _recent_roe(d, ttm_roe)
    pbr_actual = d.market_cap / equity if equity and equity > 0 else None
    # RIM은 장부가를 닻으로 삼는 모델이라, 장부가가 회사의 실제 가치를 못 담으면 성립하지 않는다.
    # PBR이 높다는 건 그 신호다 — 브랜드·특허 같은 무형자산이 장부에 안 잡히거나, 자사주 매입으로
    # 자본이 줄었거나. 임계를 12→5로 낮춘다: 12는 너무 느슨해 정작 걸러야 할 종목이 통과했다
    # (실측: 코카콜라 PBR 10.5가 통과해 현재가의 1/4을, J&J PBR 7.6이 1/3을 적정가로 냈다).
    # PBR은 완벽한 판별자가 아니다 — 성장 기대가 높아 PBR이 높은 정상 케이스도 걸러진다.
    # 다만 그 경우 RIM을 빼도 나머지 세 방법이 남고, 틀린 값을 15% 가중으로 섞는 것보다 낫다.
    book_distorted = (roe_raw is not None and roe_raw > 0.6) or \
                     (pbr_actual is not None and pbr_actual > 5) or pbr_actual is None
    if mismatch:
        res.skipped.append(("수익가치(RIM)", ccy_reason))
        res.rim_r = r_equity
    elif book_distorted:
        res.skipped.append(("수익가치(RIM)", "장부가가 실제 가치를 담지 못함(무형자산·자사주)"))
        res.notes.append(ValuationNote("info", "무형자산이 장부에 잡히지 않거나 자사주 매입으로 장부자본이 작아 "
                         "RIM(장부가치 기반) 평가에서 제외합니다 — 나머지 방법으로 판정하며 "
                         "가중치는 다시 배분합니다."))
        res.rim_r = r_equity
    else:
        roe_used = float(np.clip(roe_raw, -0.5, 0.6)) if roe_raw is not None else None
        rim, fair_pbr = _rim(bps, roe_used, r_equity)
        res.rim_roe, res.rim_r, res.rim_fair_pbr = roe_used, r_equity, fair_pbr
        if rim:
            res.estimates.append(rim)
        else:
            res.skipped.append(("수익가치(RIM)", "ROE ≤ 0 (적자)"))
            res.notes.append(ValuationNote(
                    "info", "ROE가 0 이하라 RIM 평가를 제외합니다(적자 기업)."))

    # ④ 선행 이익 — 애널리스트 컨센서스가 있을 때만 (판정이 미래 추정을 반영하게)
    cons = d.consensus
    if cons is None or not cons.forward_eps or cons.forward_eps <= 0:
        res.skipped.append(("선행 이익(컨센서스)", "애널리스트 커버리지 없음"))
    else:
        peers = comparable_peers(d.peers, d.market_cap)   # 규모 비교가능 피어만
        fv4 = _forward_value(cons.forward_eps, peer_median(peers, "forward_per", min_n=2),
                             res.per_q)
        if fv4:
            res.estimates.append(fv4)
            res.forward_eps = cons.forward_eps
            # 컨센서스 선행 EPS는 주가 통화, 재무의 TTM EPS는 본국 통화라 통화가 섞이면
            # 둘의 비율이 환율배만큼 틀어진다(실측: TSM -95%로 표시됐다). 아예 내지 않는다.
            if eps and eps > 0 and not mismatch:
                res.forward_growth = cons.forward_eps / eps - 1
                res.notes.append(ValuationNote(
                    "info",
                    f"선행 이익 방법은 컨센서스 12개월 EPS(현 TTM 대비 "
                    f"{res.forward_growth:+.0%})를 사용합니다 — 시장의 실적 전망이 "
                    "빗나가면 함께 빗나갑니다."))
                # 이익이 크게 움직이면 배수와 이익이 서로 다른 국면을 보게 된다. 이익이
                # 눌려 있던 기간은 PER이 높게 깔리는데(분모가 작아서), 그 배수를 회복된
                # 이익에 곱하면 한 번의 회복을 두 번 센다. 임계 ±50%는 R2 패널 실측에서
                # 온 값이다 — 이 선을 넘은 두 종목(삼성전자 +227%·SK하이닉스 +199%)이
                # 배수 괴리 +45.6%·+48.7%로 목표가 내재배수를 크게 웃돌았고, 그 아래
                # 종목(J&J +47% 이하)은 배수 괴리가 10% 안쪽이었다. 근본 해결은 #62.
                if abs(res.forward_growth) >= 0.5:
                    updown = "늘어나는" if res.forward_growth > 0 else "줄어드는"
                    res.notes.append(ValuationNote(
                        "warn",
                        f"이익이 크게 {updown} 국면입니다(선행 EPS가 TTM 대비 "
                        f"{res.forward_growth:+.0%}). 곱하는 배수는 '지난 5년 PER의 "
                        "중앙값'이라 이익이 지금과 다르던 시기에서 나온 값입니다 — 두 값의 "
                        "국면이 어긋나 선행 이익 방법이 실제보다 낙관적(이익 증가 국면)이거나 "
                        "비관적(감소 국면)으로 나올 수 있습니다. 아래 컨센서스 목표주가와 "
                        "반드시 함께 보세요."))
        else:
            res.skipped.append(("선행 이익(컨센서스)", "밴드·피어 멀티플 부족"))

    # 종합 판정 — 방법별 **가중평균**. 가중치는 가격 설명력 순위(선행이익 > 이익 멀티플
    # > 장부가 기반)를 인코딩한 METHOD_WEIGHTS(근거·한계는 docs/adr/0003). 없는 방법은
    # 제외하고 재정규화한다. 동일가중 결과도 함께 계산해 가중치 민감도를 화면에 병기한다.
    if res.estimates:
        mids = [e.mid for e in res.estimates]
        w = np.array([METHOD_WEIGHTS.get(e.method, 0.25) for e in res.estimates])
        w = w / w.sum()
        res.weights = {e.method: float(wi) for e, wi in zip(res.estimates, w)}
        res.fair_low = float(np.dot(w, [e.low for e in res.estimates]))
        res.fair_mid = float(np.dot(w, mids))
        res.fair_high = float(np.dot(w, [e.high for e in res.estimates]))
        res.gap = res.fair_mid / d.price - 1
        res.verdict = _verdict(res.gap)
        # 동일가중(단순평균) 민감도 — 가중치 선택이 결론을 좌우하는지 투명하게 노출
        res.fair_mid_equal = float(np.mean(mids))
        res.gap_equal = res.fair_mid_equal / d.price - 1
        res.verdict_equal = _verdict(res.gap_equal)
        if res.verdict_equal != res.verdict:
            res.notes.append(ValuationNote(
                "warn",
                f"가중 방식에 따라 판정이 갈립니다(가중 '{res.verdict}' vs "
                f"동일가중 '{res.verdict_equal}'). 가중치는 순위 근거의 정성적 인코딩이니 "
                "참고로만 보세요."))
        # ②와 ④는 같은 식(자기 5년 PER 중앙값 × EPS)이라 이 배수 하나가 틀리면 둘이
        # 같은 방향으로 함께 틀린다. 명목 가중 합(0.60)이 아니라 **중심값 크기까지 반영한
        # 실효 의존도**를 계산해 화면에 밝힌다 — 배수가 10% 틀리면 종합도 이만큼 틀린다.
        shared = [(m, w) for m, w in res.weights.items()
                  if m in ("역사적 밴드", "선행 이익(컨센서스)")]
        if len(shared) == 2 and res.fair_mid:
            mid_of = {e.method: e.mid for e in res.estimates}
            res.shared_multiple_share = float(
                sum(w * mid_of[m] for m, w in shared) / res.fair_mid)
            res.notes.append(ValuationNote(
                "warn",
                f"② 역사적 밴드와 ④ 선행 이익은 같은 배수(자기 5년 PER 중앙값)에 각각 "
                f"TTM EPS와 컨센서스 EPS를 곱한 값입니다 — 서로 다른 관점이 아니라 같은 "
                f"관점의 과거판·미래판입니다. 그래서 종합 적정가의 "
                f"{res.shared_multiple_share:.0%}가 이 배수 하나에 의존합니다. 두 방법이 "
                "비슷하게 나와도 '독립적으로 합의했다'는 뜻이 아닙니다."))

        if len(mids) >= 2 and res.fair_mid:
            disp = float(np.std(mids) / abs(np.mean(mids)))
            res.dispersion = disp
            res.confidence = "높음" if disp < 0.15 else "중간" if disp < 0.35 else "낮음"
            if res.confidence == "낮음":
                res.notes.append(ValuationNote("warn", f"평가 방법 간 편차가 큽니다(±{disp:.0%}). "
                                 "판정을 보수적으로 해석하세요."))
        else:
            res.confidence = "낮음"
            res.notes.append(ValuationNote(
                "warn", "사용 가능한 평가 방법이 1개뿐이라 신뢰도가 낮습니다."))
    return res
