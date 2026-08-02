"""적정주가 삼각측량: ① 업종 상대가치 ② 역사적 밴드 ③ RIM ④ 선행 이익(컨센서스).

**종합을 두 개 낸다(ADR-0006, 0003을 대체).**

- **펀더멘털 적정가** = ①②③ 가중평균. 이것이 `fair_mid`이고 **판정(`verdict`)의 근거**다.
  전부 회사가 이미 낸 실적·자산에서 나온 값이라, 시장이 지금 무엇을 기대하든 그것과
  독립적으로 계산된다.
- **컨센서스 반영 적정가** = ①②③④ 가중평균. `fair_mid_consensus`로 따로 들고 화면에
  나란히 보여준다. 판정에는 쓰지 않는다.

왜 갈랐나. ④는 애널리스트의 12개월 선행 EPS 추정을 입력으로 쓴다. 그 자체가 틀린
재료라서가 아니다 — 내재가치는 원래 미래지향적이고, 선행 이익 배수가 가격 설명력이
가장 높다는 실증도 확고하다(Liu·Nissim·Thomas 2002·2007). 문제는 **④를 종합에 섞으면
도구의 판정이 시장 기대를 얼마나 따라갔는지 아무도 볼 수 없게 된다**는 것이다.
이 도구가 만드는 유일한 상품은 '가격과 독립된 의견'인데, 그 의견 안에 시장 기대를
녹여 넣으면 남는 정보가 없다. 갈라 놓으면 **두 값의 차이 자체가 정보**가 된다 —
"지금 주가가 정당화되려면 시장이 기대하는 만큼의 실적 개선이 실제로 와야 한다"는 크기다.

갈라야 할 이유가 세 개 더 있다.

1. **④는 독립된 네 번째 관점이 아니다(R2 실측).** ②와 ④는 같은 식이고 곱하는 EPS만 다르다 —
   ② = 자기 5년 PER 중앙값 × TTM EPS,  ④ = 자기 5년 PER 중앙값 × 컨센서스 선행 EPS.
   그래서 ④÷②는 항상 정확히 (선행EPS ÷ TTM EPS)다(10종목 패널 전부에서 확인).
   ④를 빼면 잃는 것은 '한 관점'이 아니라 '②의 미래판'이다. 남은 ①②③이 오히려 서로
   더 다른 것을 본다(피어 / 자기 역사 / 장부가).
2. **④의 고장이 사이클 종목에 몰려 있다.** 이익이 눌렸던 시기의 높은 PER을 회복된 선행
   EPS에 곱해 한 번의 회복을 두 번 센다(`_forward_value` 독스트링의 실측: 삼성전자 +45.6%,
   SK하이닉스 +48.7%). 이 결함이 종합에 섞이면 판정 자체가 오염된다.
3. **④는 사후검증이 불가능하다(ADR-0004).** 시점별 컨센서스 빈티지가 무료 데이터에 없다.
   백테스트는 ②+③만 복원해 검증한다 — 판정을 ①②③으로 내리면 **화면이 보여주는 판정과
   백테스트가 검증하는 신호가 훨씬 가까워진다**(종합가중 기준 0.40 → 0.615).

동일가중 결과(`fair_mid_equal`)도 함께 계산해 가중치가 결론을 좌우하지 않음을 화면에
병기한다(민감도 노출). 컨센서스 '목표주가' 자체는 어느 종합에도 섞지 않고 외부
교차검증치로만 쓴다.

`shared_multiple_share`는 이제 **컨센서스 반영 값**이 자기 5년 PER 중앙값 하나에 얼마나
매달려 있는지를 잰다(펀더멘털 종합에는 ②만 들어가 이 문제가 없다).
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
# 상세·대안·한계·인용은 docs/adr/0006(0003을 대체). 사용 가능한 방법만으로 재정규화해
# 합이 1이 되게 쓴다. ④의 가중 0.35는 **컨센서스 반영 종합에서만** 쓰이고, 판정을 내는
# 펀더멘털 종합에는 들어가지 않는다 — 위 낙관편의·국면 불일치가 판정을 오염시키지
# 않게 하려는 것이 ADR-0006의 요지다.
METHOD_WEIGHTS = {
    "선행 이익(컨센서스)": 0.35,
    "업종 상대가치": 0.25,
    "역사적 밴드": 0.25,
    "수익가치(RIM)": 0.15,
}

# 회사가 이미 낸 실적·자산만으로 서는 방법들 — **판정은 이 셋으로만 낸다**(ADR-0006).
# 위 가중치를 이 셋에 대해 재정규화해 쓴다(0.25/0.25/0.15 → 0.385/0.385/0.231).
# 순위(이익 멀티플 > 장부가)는 그대로 유지되고, 빠지는 것은 ④의 몫뿐이다.
FUNDAMENTAL_METHODS = ("업종 상대가치", "역사적 밴드", "수익가치(RIM)")
CONSENSUS_METHOD = "선행 이익(컨센서스)"


def _weighted(estimates: list) -> tuple[float, float, float, dict]:
    """(low, mid, high, 재정규화 가중치) — 주어진 방법들만으로 가중평균한다."""
    w = np.array([METHOD_WEIGHTS.get(e.method, 0.25) for e in estimates], dtype=float)
    w = w / w.sum()
    return (float(np.dot(w, [e.low for e in estimates])),
            float(np.dot(w, [e.mid for e in estimates])),
            float(np.dot(w, [e.high for e in estimates])),
            {e.method: float(wi) for e, wi in zip(estimates, w)})


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
    estimates: list = field(default_factory=list)   # [FairValue] — ④ 포함, 전부
    # ── 펀더멘털 종합 (①②③) — 판정의 근거 ──
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
    # ── 컨센서스 반영 종합 (①②③④) — 병기용, 판정에는 쓰지 않는다 ──
    # 값 자체보다 `consensus_premium`(펀더멘털 대비 얼마나 위인가)이 읽을 거리다:
    # "지금 주가가 정당화되려면 시장이 기대하는 실적 개선이 실제로 와야 한다"는 크기.
    fair_low_consensus: float | None = None
    fair_mid_consensus: float | None = None
    fair_high_consensus: float | None = None
    gap_consensus: float | None = None
    verdict_consensus: str | None = None
    weights_consensus: dict = field(default_factory=dict)
    consensus_premium: float | None = None   # fair_mid_consensus / fair_mid - 1
    # ④가 판정에서 빠졌는데도 헤드라인이 ④에 기대고 있는 예외 상황(①②③이 전부 계산
    # 불가능해 ④만 남은 경우). True면 화면이 "판정이 컨센서스에 의존한다"고 밝혀야 한다.
    fundamental_only: bool = True
    per_band: pd.DataFrame | None = None   # 밴드 차트용 (price + 분위선)
    pbr_band: pd.DataFrame | None = None
    per_percentile: float | None = None    # 현재 PER의 5년 내 백분위
    pbr_percentile: float | None = None
    per_q: dict | None = None              # 5년 PER 분위 배수 {10:.., 25:.., 50:..}
    pbr_q: dict | None = None
    rim_fair_pbr: float | None = None
    rim_roe: float | None = None
    rim_r: float | None = None
    # 장부가 품질 판별 결과(ADR-0007) — {pbr, intangible_share, buyback_ratio, years,
    # distorted, short, detail}. RIM을 왜 쓰거나 뺐는지의 실측 근거라 화면에 그대로 내보낸다.
    book_quality: dict = field(default_factory=dict)
    forward_eps: float | None = None       # ④에 사용한 컨센서스 12개월 EPS
    forward_growth: float | None = None    # 선행 EPS / TTM EPS - 1 (내재 성장률)
    weights: dict = field(default_factory=dict)   # 펀더멘털 종합에 쓴 가중치 (재정규화)
    skipped: list = field(default_factory=list)   # [(방법명, 건너뛴 사유)] — 번호 자리 유지용
    # ②·④가 함께 쓰는 '자기 5년 PER 중앙값' 하나에 **컨센서스 반영 적정가**가 실제로 얼마나
    # 매달려 있나(0~1). 명목 가중 합(0.60)이 아니라 **중심값 크기까지 반영한 실효 의존도**다 —
    # 이 배수가 10% 틀리면 그 값도 이 비율만큼 틀린다. 펀더멘털 종합에는 ②만 들어가므로
    # 이 지표는 병기값 쪽에만 붙는다.
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


# ── 장부가 품질 — RIM을 쓸 수 있는 회사인가 (ADR-0007) ───────────────
# RIM은 장부가를 닻으로 삼는 모형이라, 장부가가 회사 가치를 못 담으면 성립하지 않는다.
# 예전에는 그 신호로 **실제 PBR 5배 초과** 하나만 봤다. 그런데 PBR이 높은 이유는 둘로 갈린다:
#   ㉠ 장부가가 실제보다 작다 — 브랜드·특허가 장부에 없거나(무형자산), 자사주로 자본이 줄었거나
#   ㉡ 장부가는 멀쩡한데 이익이 크다 — 이익 사이클 고점이거나 성장 기대가 실렸거나
# ㉠에서는 RIM이 성립하지 않지만 **㉡에서는 RIM이 오히려 필요한 반대 목소리**다.
# PBR 하나로는 둘을 못 가르는데(예전 코드 주석도 이 한계를 적어 두었다), 화면에는
# "무형자산·자사주 때문"이라고 원인을 단정해 내보내고 있었다 — SK하이닉스가 그 오진의 실례다
# (무형자산 자산의 1.8%, 자사주 0, 유형자산 45% — 장부가가 오히려 잘 측정되는 회사).
# 그래서 원인을 **직접 잰다**. 임계는 국내외 10여 종목 패널을 보고 정한 판단값이지
# 데이터로 추정한 값이 아니다(METHOD_WEIGHTS와 같은 성격).
PBR_GATE = 5.0                  # 이 아래면 애초에 따지지 않는다
INTANGIBLE_SHARE_LIMIT = 0.15   # 무형자산(영업권 포함) / 총자산
BUYBACK_RATIO_LIMIT = 0.30      # 가용 연도 누적 자사주매입 / 현재 자기자본
EXTREME_ROE = 0.60              # 자기자본이 이익 규모 대비 비정상적으로 작다는 신호


def _book_quality(d: CompanyData, pbr: float | None, roe: float | None) -> dict:
    """장부가가 회사 가치를 담고 있는가 → {distorted, short, detail, 근거 수치}.

    `short`는 방법표의 '제외 사유' 칸(짧게), `detail`은 해설 카드에 들어간다.
    둘 다 **원인을 단정하지 않고 잰 값을 말한다** — 판별에 쓴 수치를 문장에 그대로 넣는다.
    """
    fin = d.financials
    ta, eq = d.latest("total_assets"), d.latest("total_equity")
    intan = d.latest("intangibles")
    share = (intan / ta) if (intan is not None and ta and ta > 0) else None
    bb = fin["buyback"].dropna() if "buyback" in fin.columns else None
    ratio = (float(bb.sum()) / eq) if (bb is not None and len(bb) and eq and eq > 0) else None
    out = {"pbr": pbr, "intangible_share": share, "buyback_ratio": ratio,
           "years": int(len(bb)) if bb is not None else 0}

    def done(distorted, short, detail):
        out.update({"distorted": distorted, "short": short, "detail": detail})
        return out

    if pbr is None:
        return done(True, "자기자본을 확인하지 못함",
                    "자기자본(장부가)을 받지 못해 장부가 기반 모형(RIM)을 적용할 수 없습니다.")
    if pbr <= PBR_GATE and (roe is None or roe <= EXTREME_ROE):
        return done(False, "", "")
    if share is None or ratio is None:
        return done(True, f"실제 PBR {pbr:.1f}배 · 원인 판별 자료 없음",
                    f"실제 PBR이 {pbr:.1f}배로 높은데, 그 원인이 장부가 과소평가(무형자산·자사주)인지 "
                    "이익 사이클·성장 기대인지 가릴 재무 항목을 받지 못했습니다. 장부가 기반 "
                    "모형(RIM)을 보수적으로 제외합니다 — 나머지 방법으로 판정하며 가중치는 다시 배분합니다.")
    if share >= INTANGIBLE_SHARE_LIMIT:
        return done(True, f"무형자산이 자산의 {share:.0%} — 장부가 과소",
                    f"무형자산(영업권 포함)이 총자산의 {share:.0%}입니다. 브랜드·특허처럼 장부에 "
                    f"온전히 잡히지 않는 가치가 커서 장부가를 닻으로 삼는 RIM이 성립하지 않습니다"
                    f"(실제 PBR {pbr:.1f}배). 나머지 방법으로 판정하며 가중치는 다시 배분합니다.")
    if ratio >= BUYBACK_RATIO_LIMIT:
        return done(True, f"누적 자사주매입이 자본의 {ratio:.0%} — 장부가 과소",
                    f"최근 {out['years']}개년 자사주 매입액 합계가 현재 자기자본의 {ratio:.0%}입니다. "
                    f"자사주를 사면 그만큼 자본이 줄어 장부가가 회사 규모를 대변하지 못합니다"
                    f"(실제 PBR {pbr:.1f}배). RIM을 제외하고 가중치는 다시 배분합니다.")
    if roe is not None and roe > EXTREME_ROE:
        return done(True, f"ROE {roe:.0%} — 자기자본이 이익 대비 비정상적으로 작음",
                    f"ROE가 {roe:.0%}입니다. 이익이 뛰어난 것일 수도 있지만, 자기자본이 이익 규모에 "
                    "비해 지나치게 작다는 뜻이기도 해서 장부가를 닻으로 삼는 RIM이 불안정해집니다. "
                    "RIM을 제외하고 가중치는 다시 배분합니다.")
    # 살아남았다 — PBR은 높은데 장부가가 작아진 흔적은 못 찾았다.
    return done(False, "",
                f"실제 PBR이 {pbr:.1f}배로 높지만, 무형자산은 총자산의 {share:.0%}뿐이고 최근 "
                f"{out['years']}개년 누적 자사주 매입도 자본의 {ratio:.0%}에 그쳐 **장부가가 작아진 "
                "흔적은 찾지 못했습니다**. 높은 PBR이 이익 사이클 고점이나 성장 기대에서 온 것으로 "
                "보여 RIM을 그대로 적용합니다 — 다만 RIM은 '지금 수준의 초과이익이 서서히 사라진다'는 "
                "가정이라 이런 종목에서 보수적인(낮은) 값을 냅니다. 반대편 시각으로 읽으세요.")


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

    # ③ RIM — 장부가가 회사 가치를 담고 있을 때만 쓴다(ADR-0007).
    ttm_roe = ind.profitability.get("roe")
    roe_raw = _recent_roe(d, ttm_roe)
    pbr_actual = d.market_cap / equity if equity and equity > 0 else None
    book = _book_quality(d, pbr_actual, roe_raw)
    res.book_quality = book
    if mismatch:
        res.skipped.append(("수익가치(RIM)", ccy_reason))
        res.rim_r = r_equity
    elif book["distorted"]:
        res.skipped.append(("수익가치(RIM)", book["short"]))
        res.notes.append(ValuationNote("info", book["detail"]))
        res.rim_r = r_equity
    else:
        # PBR이 높은데도 살아남은 경우 — 장부가 왜곡 근거를 실제로 찾지 못했다는 뜻이다.
        # 이때 RIM은 '지금 이익이 지속된다면' 기준의 보수적인 값을 내므로 그 성격을 먼저 밝힌다.
        if pbr_actual is not None and pbr_actual > PBR_GATE:
            res.notes.append(ValuationNote("info", book["detail"]))
        roe_used = float(np.clip(roe_raw, -0.5, 0.6)) if roe_raw is not None else None
        rim, fair_pbr = _rim(bps, roe_used, r_equity)
        res.rim_roe, res.rim_r, res.rim_fair_pbr = roe_used, r_equity, fair_pbr
        if rim:
            res.estimates.append(rim)
        else:
            res.skipped.append(("수익가치(RIM)", "ROE ≤ 0 (적자)"))
            res.notes.append(ValuationNote(
                    "info", "ROE가 0 이하라 RIM 평가를 제외합니다(적자 기업)."))

    # ④ 선행 이익 — 애널리스트 컨센서스가 있을 때만. **판정에는 들어가지 않는다**(ADR-0006):
    # 계산해서 estimates에 넣되, 종합은 '컨센서스 반영' 값에만 반영해 병기한다.
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
                    f"④ 선행 이익 방법은 컨센서스 12개월 EPS(현 TTM 대비 "
                    f"{res.forward_growth:+.0%})를 사용합니다 — 시장의 실적 전망이 "
                    "빗나가면 함께 빗나갑니다. 그래서 판정에는 넣지 않고 '컨센서스 반영' "
                    "값으로만 병기합니다."))
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
                        "비관적(감소 국면)으로 나올 수 있습니다. 판정은 이 방법을 쓰지 않지만, "
                        "'컨센서스 반영' 값은 이만큼 흔들립니다 — 아래 컨센서스 목표주가와 "
                        "반드시 함께 보세요."))
        else:
            res.skipped.append(("선행 이익(컨센서스)", "밴드·피어 멀티플 부족"))

    # ── 종합 (ADR-0006) ─────────────────────────────────────────────
    # 두 개를 낸다. **판정은 ①②③(펀더멘털)으로만** 내고, ④를 얹은 값은 따로 들어
    # 화면에 나란히 세운다. 가중치는 둘 다 METHOD_WEIGHTS를 각자의 방법 집합에 대해
    # 재정규화해 쓴다 — 순위(선행이익 > 이익 멀티플 > 장부가)는 그대로다.
    core = [e for e in res.estimates if e.method in FUNDAMENTAL_METHODS]
    if res.estimates:
        # 컨센서스 반영 종합 — ④가 실제로 있을 때만 펀더멘털과 다른 값이 된다.
        (res.fair_low_consensus, res.fair_mid_consensus,
         res.fair_high_consensus, res.weights_consensus) = _weighted(res.estimates)
        res.gap_consensus = res.fair_mid_consensus / d.price - 1
        res.verdict_consensus = _verdict(res.gap_consensus)

    # ①②③이 하나도 없으면(통화 불일치 등으로 전부 제외) 판정을 낼 재료가 ④뿐이다.
    # 이때는 화면을 비우는 대신 ④에 기대되, 그 사실을 감추지 않는다.
    res.fundamental_only = bool(core)
    basis = core or res.estimates
    if basis:
        mids = [e.mid for e in basis]
        res.fair_low, res.fair_mid, res.fair_high, res.weights = _weighted(basis)
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
        if not res.fundamental_only:
            res.notes.append(ValuationNote(
                "warn",
                "회사 실적·자산으로 서는 방법(①②③)이 전부 계산되지 않아, 판정을 "
                "컨센서스 선행 이익(④) 하나에만 의존해 냈습니다 — 이 판정은 시장 기대와 "
                "독립적이지 않습니다. 보수적으로 해석하세요."))

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

    # 두 종합의 차이 — 이 도구에서 읽을 거리가 가장 많은 숫자다.
    # ④가 없는 종목(커버리지 없음)이면 두 값이 같으므로 아무 말도 하지 않는다.
    has_fwd = any(e.method == CONSENSUS_METHOD for e in res.estimates)
    if has_fwd and res.fundamental_only and res.fair_mid and res.fair_mid_consensus:
        res.consensus_premium = res.fair_mid_consensus / res.fair_mid - 1
        flip = res.verdict_consensus != res.verdict
        res.notes.append(ValuationNote(
            "info",
            f"판정은 회사가 이미 낸 실적·자산(①②③)만으로 냈습니다. 애널리스트 컨센서스 "
            f"선행 이익(④)까지 넣으면 적정가가 {res.consensus_premium:+.0%} "
            f"달라집니다" + (f" — 판정도 '{res.verdict_consensus}'로 갈립니다." if flip else ".") +
            " 이 차이가 곧 '지금 주가가 정당화되려면 시장이 기대하는 만큼의 실적 변화가 "
            "실제로 와야 하는 크기'입니다."))
        # ②와 ④는 같은 식(자기 5년 PER 중앙값 × EPS)이라 이 배수 하나가 틀리면 둘이
        # 같은 방향으로 함께 틀린다. 명목 가중 합(0.60)이 아니라 **중심값 크기까지 반영한
        # 실효 의존도**를 계산해 화면에 밝힌다. 펀더멘털 종합에는 ②만 들어가므로 이 경고는
        # 병기하는 컨센서스 반영 값에만 붙는다.
        shared = [(m, w) for m, w in res.weights_consensus.items()
                  if m in ("역사적 밴드", CONSENSUS_METHOD)]
        if len(shared) == 2:
            mid_of = {e.method: e.mid for e in res.estimates}
            res.shared_multiple_share = float(
                sum(w * mid_of[m] for m, w in shared) / res.fair_mid_consensus)
            res.notes.append(ValuationNote(
                "info",
                f"위 '컨센서스 반영' 값을 읽을 때: ② 역사적 밴드와 ④ 선행 이익은 같은 "
                f"배수(자기 5년 PER 중앙값)에 각각 TTM EPS와 컨센서스 EPS를 곱한 값입니다 — "
                f"서로 다른 관점이 아니라 같은 관점의 과거판·미래판이라, 그 값의 "
                f"{res.shared_multiple_share:.0%}가 이 배수 하나에 의존합니다. 두 방법이 "
                "비슷하게 나와도 '독립적으로 합의했다'는 뜻이 아닙니다."))
    return res
