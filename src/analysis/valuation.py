"""적정주가 삼각측량: ① 업종 상대가치 ② 역사적 밴드 ③ RIM ④ 선행 이익(컨센서스)
⑤ 정규화 이익. **판정에 드는 것은 ①③⑤ 셋뿐이다**(②는 ADR-0035, ④는 ADR-0006).

**종합을 두 개 낸다(ADR-0006, 0003을 대체).**

- **펀더멘털 적정가** = ①③⑤ 가중평균. 이것이 `fair_mid`이고 **판정(`verdict`)의 근거**다.
  전부 회사가 이미 낸 실적·자산에서 나온 값이라, 시장이 지금 무엇을 기대하든 그것과
  독립적으로 계산된다.
- **컨센서스 반영 적정가** = ①③⑤④ 가중평균. `fair_mid_consensus`로 따로 들고 화면에
  나란히 보여준다. 판정에는 쓰지 않는다.

두 값의 차이가 정확히 **④의 몫**이어야 하므로, 판정에서 뺀 방법은 병기에서도 뺀다 —
②를 한쪽에만 남기면 "이 차이가 곧 시장 기대분"이라는 화면의 문장이 거짓이 된다.

왜 갈랐나. ④는 애널리스트의 12개월 선행 EPS 추정을 입력으로 쓴다. 그 자체가 틀린
재료라서가 아니다 — 내재가치는 원래 미래지향적이고, 선행 이익 배수가 가격 설명력이
가장 높다는 실증도 확고하다(Liu·Nissim·Thomas 2002·2007). 문제는 **④를 종합에 섞으면
도구의 판정이 시장 기대를 얼마나 따라갔는지 아무도 볼 수 없게 된다**는 것이다.
이 도구가 만드는 유일한 상품은 '가격과 독립된 의견'인데, 그 의견 안에 시장 기대를
녹여 넣으면 남는 정보가 없다. 갈라 놓으면 **두 값의 차이 자체가 정보**가 된다 —
"지금 주가가 정당화되려면 시장이 기대하는 만큼의 실적 개선이 실제로 와야 한다"는 크기다.

갈라야 할 이유가 세 개 더 있다.

1. **④는 독립된 네 번째 관점이 아니다(R2 실측).** ②와 ④는 같은 식이고 곱하는 EPS만 다르다 —
   ② = 자기 과거 PER 중앙값 × TTM EPS,  ④ = 자기 과거 PER 중앙값 × 컨센서스 선행 EPS.
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

`shared_multiple_share`는 이제 **컨센서스 반영 값**이 자기 과거 PER 중앙값 하나에 얼마나
매달려 있는지를 잰다(펀더멘털 종합에는 ②만 들어가 이 문제가 없다).
근거·실측은 docs/review/R2-가정적합성.md, 재현은 scripts/check_sensitivity.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.models import CompanyData, actual_prices, currency_mismatch
from .scoring import comparable_peers, peer_median
from .warranted import NEFF_LOW, NEFF_MID, effective_axes, leg_error

VERDICTS = ["크게 저평가", "저평가", "적정 수준", "고평가", "크게 고평가"]
# 신뢰도 등급 — **순서가 곧 크기다**(ADR-0022). 흩어짐이 낸 등급과 실질 축 수가 씌운
# 상한 중 낮은 쪽을 쓰므로, 두 값을 비교할 자가 필요하다.
CONFIDENCE_ORDER = ["낮음", "중간", "높음"]


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
    # ⑤는 ①②와 같은 칸이다(ADR-0015). LNT(2002)의 순위에서 '과거 이익 멀티플'이고,
    # 그 칸 **안에서** Anderson & Brooks(2006, JBFA 33(7-8) 1063-1086)가 8년 평균 이익
    # PER이 단년보다 낫다고 보고했다 — ①② 아래로 둘 문헌 근거가 없다.
    # 우리 표본으로 맞춘 값이 아니다(ADR-0003: 순위 인코딩 ≠ 가중치 추정).
    "정규화 이익": 0.25,
    "수익가치(RIM)": 0.15,
}

# 회사가 이미 낸 실적·자산만으로 서는 방법들 — **판정은 이 셋으로만 낸다**(ADR-0006·0015·0035).
# 위 가중치를 이 셋에 대해 재정규화해 쓴다(0.25/0.15/0.25 → 0.385/0.231/0.385).
# 순위(이익 멀티플 > 장부가)는 그대로 유지되고, 빠지는 것은 ②와 ④의 몫이다.
#
# **② 역사적 밴드가 여기서 빠진 것이 ADR-0035다.** 넷 중 유일하게 논문 계보가 없었고
# (이슈 #132), 백테스트에서 단독 IC 0.030(t=1.59)으로 예측력이 확인되지 않았다.
# 계산은 그대로 하고 화면·차트에도 남는다 — **판정에 넣지 않을 뿐이다.**
FUNDAMENTAL_METHODS = ("업종 상대가치", "수익가치(RIM)", "정규화 이익")
CONSENSUS_METHOD = "선행 이익(컨센서스)"

# 방법 → 화면 번호. **번호가 연속하지 않는 것은 의도다** — ④를 옮기면 ADR-0003·0006의
# 서술이 통째로 어긋난다. 화면(`stock.js`의 `METHOD_TAB`)이 같은 매핑을 갖고 있고,
# 그쪽은 `v.weights`에서 유도해 그린다.
#
# 파이썬에도 두는 이유: 화면에 나가는 문장 중 **판정 축 목록을 말하는 것**이 있는데
# ("…방법(①③⑤)이 전부 계산되지 않아…"), 그것을 손으로 적으면 축이 바뀔 때마다 썩는다.
# 실제로 "①②③"이 여덟 자리에 손으로 적힌 채 실제 구성(①②③⑤)과 어긋나 있었다.
METHOD_MARKS = {
    "업종 상대가치": "①", "역사적 밴드": "②", "수익가치(RIM)": "③",
    CONSENSUS_METHOD: "④", "정규화 이익": "⑤",
}


def marks_of(methods) -> str:
    """['업종 상대가치', '정규화 이익'] → '①⑤'. 번호 순으로 정렬한다."""
    got = [METHOD_MARKS[m] for m in methods if m in METHOD_MARKS]
    return "".join(sorted(got, key="①②③④⑤".index))

# 값은 내지만 판정 종합에 넣지 않는 방법과 **그 사유**. 화면이 아니라 여기 사는 이유는
# 사유가 ADR의 주장이기 때문이다 — 판정 구성이 바뀔 때 이 표와 ADR을 함께 고치면 되고,
# 화면은 문자열을 받아 그리기만 한다. 손으로 적힌 문구는 구성이 바뀔 때 조용히 썩는다.
EXCLUSION_REASONS = {
    "역사적 밴드":
        "값은 계산했지만 판정에는 넣지 않습니다 — 이 방법만 문헌 계보가 없는 실무 관행이고, "
        "한국 1,288종목·10시점 백테스트에서 단독 예측력이 확인되지 않았습니다"
        "(IC 0.030 · t=1.59). 차트와 백분위는 그대로 두었습니다 — '자기 역사 대비 지금 "
        "어디인가'는 '미래를 맞히는가'와 다른 질문입니다. 상세 · docs/adr/0035",
    CONSENSUS_METHOD:
        "컨센서스 선행 이익은 시장의 실적 기대를 입력으로 씁니다. 판정을 시장 기대와 "
        "독립적으로 유지하려고 종합에서 빼고, '컨센서스 반영' 값으로 따로 병기합니다. "
        "상세 · docs/adr/0006",
}

# 값을 **무엇에서** 만드는가 (ADR-0018). 이 분류가 판정의 성격을 가른다.
#   절대가치 — 회사가 벌 것과 할인율에서 만든다. 지금은 ③ RIM뿐이다.
#   상대가치 — 시장이 매긴 배수가 입력이다. ①은 다른 회사가 시장에서 받는 배수,
#              ②는 자기가 과거에 시장에서 받던 배수, ⑤도 ①의 회귀 배수를 쓴다.
# `scripts/check_valuation_basis.py`가 이것을 가져다 쓴다 — 진단과 화면이 같은 분류를
# 봐야 "실측은 9.3%인데 화면은 다른 말"이 되지 않는다.
# 이 둘은 **판정에 드는 방법을 남김없이 가르는 것**이 계약이다(테스트가 그 등식을 지킨다).
# 그래서 ②가 판정에서 빠질 때 여기서도 함께 빠진다 — ②가 상대가치가 아니게 된 것이
# 아니라, 이 분류가 답하는 질문("이 판정이 무엇에 기대는가")에 ②가 더 이상 참여하지
# 않기 때문이다.
INTRINSIC_METHODS = ("수익가치(RIM)",)
RELATIVE_METHODS = ("업종 상대가치", "정규화 이익")


def confidence_grade(dispersion: float | None, n_methods: int,
                     n_eff: float | None = None,
                     capped: bool = False) -> tuple[str, str, str]:
    """(최종 등급, 흩어짐 등급, 상한) — 신뢰도를 정한다 (ADR-0022).

    **흩어짐만 보면 '방법이 서로 독립'이라는 전제가 깔린다.** 그 전제가 틀렸다 — ①과 ⑤는
    같은 적정 배수를 쓰고(ADR-0015 · AAPL), 구조적으로도 괴리율의 55~60%를
    `−log(현재배수)`라는 공통 항이 설명한다(ADR-0014). 방법이 겹치면 값이 가깝게 나오는데,
    그것은 '확실하다'가 아니라 **'같은 자로 여러 번 쟀다'**다.

    그래서 흩어짐이 낸 등급에 **실질 축 수가 씌우는 상한**을 걸고 둘 중 낮은 쪽을 쓴다.
    현행이 "방법이 1개뿐이면 무조건 낮음"으로 못박은 규칙에서 `1`을 실질 축 수로 바꾼 것이
    설계의 전부다.

    **곱셈 보정이 아닌 이유가 있다.** 처음에는 `disp`를 겹침만큼 부풀리려 했는데, ①과 ⑤가
    같은 배수를 쓰면 두 값이 거의 같아 `disp ≈ 0`이고 **0에는 무엇을 곱해도 0**이다.
    보정이 가장 필요한 자리에서 아무 일도 하지 않는다.

    `capped`가 False면 상한을 걸지 않는다 — 상관을 모르는 방법 쌍이 있다는 뜻이고,
    지어낸 상관으로 등급을 깎느니 안 깎는 쪽이 정직하다(ADR-0011).
    """
    if n_methods < 2 or dispersion is None:
        return "낮음", "낮음", "낮음"
    spread = "높음" if dispersion < 0.15 else "중간" if dispersion < 0.35 else "낮음"
    cap = "높음"
    if capped and n_eff is not None:
        cap = "낮음" if n_eff < NEFF_LOW else "중간" if n_eff < NEFF_MID else "높음"
    return min(spread, cap, key=CONFIDENCE_ORDER.index), spread, cap


def psr_error_phrase(leg_err: dict | None) -> str:
    """PSR 오차 폭을 말하는 구절 — **잰 값이 없으면 숫자를 대지 않는다** (ADR-0017·0022).

    예전에는 `"±150% 수준"`이 문장에 하드코딩돼 있었다. 그 값은 **한국** PSR MAE
    0.921에서 나온 것인데 문구에 시장 구분이 없어 **미국 종목 화면에도 한국 숫자가
    떴다.** 미국 PSR은 0.656(±93%)이라 다른 값이고, 바로 옆 ADR-0017이 *"한국 값을
    대신 쓰는 것은 지어내는 것"*이라며 금지한 그 행위다 — 실화면(WBD)에서 "측정된 적이
    없습니다"와 "±150% 수준"이 몇 줄 간격으로 나란히 섰다.

    그래서 이 시장에서 실제로 잰 값(`leg_error`)만 읽는다. 그 값은 **회귀를 재서 나온
    것**이라 피어 중앙값 폴백 경로에는 없다(`res.leg_error`가 그때 비어 있다). 없으면
    폭을 말하지 않고 없다고 말한다.
    """
    psr = next((m for m in (leg_err or {}).get("measured", [])
                if m["leg"] == "psr"), None)
    if psr is None:
        return "실측 오차가 가장 큰 배수인데 이 경로의 오차는 아직 측정된 적이 없어서"
    return f"실측 오차가 가장 커서(전 종목 검증에서 ±{psr['up']:.0%} 수준)"


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
    # ── 펀더멘털 종합 (①③⑤) — 판정의 근거 ──
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
    # 실질 축 수 (ADR-0022). 방법을 몇 개 썼든 서로 겹치면 이 값이 그보다 작다.
    # 신뢰도 등급의 **상한**이 여기서 나온다 — 흩어짐만으로 낸 등급과 둘 중 낮은 쪽을 쓴다.
    n_eff: float | None = None
    # `confidence`가 어느 쪽에서 나왔는지 — 화면이 **숫자와 설명을 맞추려면** 둘이 필요하다.
    # `dispersion`(±%)이 뜻하는 등급은 `confidence_spread`이고, `n_eff`가 씌운 상한은
    # `confidence_cap`이며, 최종 `confidence`는 둘 중 낮은 쪽이다. 상한이 등급을 내린
    # 종목에서 ±%만 보고 설명을 고르면 **숫자와 문장이 서로 모순된다**(PR #130이 찾은 버그).
    # 임계(0.15/0.35 · NEFF_LOW/MID)를 JS에 옮겨 적지 않으려고 등급 자체를 내보낸다 —
    # 같은 수식이 두 언어에 사는 것이 #84·ADR-0019가 잡은 문제다.
    confidence_spread: str | None = None   # 흩어짐만으로 낸 등급
    confidence_cap: str | None = None      # 실질 축 수가 씌운 상한 등급
    # ── 컨센서스 반영 종합 (①③⑤④) — 병기용, 판정에는 쓰지 않는다 ──
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
    per_percentile: float | None = None    # 현재 PER의 관측 창 내 백분위
    pbr_percentile: float | None = None
    per_q: dict | None = None              # 관측 창 PER 분위 배수 {10:.., 25:.., 50:..}
    pbr_q: dict | None = None
    # 판정에서 빠진 다리의 배수는 **가격을 만드는 어느 경로에도** 넘기지 않는다(ADR-0012).
    # ④ 선행 이익과 시나리오 표가 둘 다 per_q를 타깃 배수로 쓰기 때문에 여기서 한 번만
    # 끊는다. 밴드 차트는 판정과 무관하게 그려야 하므로 그쪽은 `per_q`를 그대로 쓴다.
    per_q_pricing: dict | None = None
    # 다리별 밴드 품질(ADR-0012) — {"per"|"pbr": {n, years, corr, sd_fund, price_band,
    # usable, short, detail}}. 판정에서 왜 뺐는지의 실측 근거라 화면에 그대로 내보낸다.
    # 창은 종목마다 다르다(실측 2.8~5.0년) — "5년"이라고 쓰지 말고 `years`를 쓸 것.
    band_quality: dict = field(default_factory=dict)
    rim_fair_pbr: float | None = None
    rim_roe: float | None = None
    rim_r: float | None = None
    # 장부가 품질 판별 결과(ADR-0007) — {pbr, intangible_share, buyback_ratio, years,
    # distorted, short, detail}. RIM을 왜 쓰거나 뺐는지의 실측 근거라 화면에 그대로 내보낸다.
    book_quality: dict = field(default_factory=dict)
    forward_eps: float | None = None       # ④에 사용한 컨센서스 12개월 EPS
    forward_growth: float | None = None    # 선행 EPS / TTM EPS - 1 (내재 성장률)
    # ①의 중앙값이 다리 하나에 얼마나 매달려 있나 — 다리를 하나씩 빼봤을 때의 최대 변화율.
    # 값을 고치지 않고 밝히기만 한다(다리를 빼면 범위가 좁아져 신뢰도만 부풀기 때문).
    relative_legs: int | None = None
    relative_leg_sensitivity: float | None = None
    # ①이 회귀(ADR-0014)로 나왔는지 피어 중앙값 폴백인지. 화면이 근거를 다르게 쓴다.
    relative_basis: str | None = None            # "regression" | "peer_median"
    # 다리별 계수 분해 — [{leg, multiple, sector_base, size_adj, roe_adj,
    # below_range, beta_size, n}]. 화면에서 접어 둔다(ADR-0014 결정 다섯).
    relative_parts: list = field(default_factory=list)
    # 판정이 무엇에 기대는가 — 실효 가중 기준 절대/상대 비중(ADR-0018).
    # 둘의 합은 1이거나(펀더멘털 종합) 0이다(④만 남은 경우). `intrinsic_share`가 0이면
    # **절대가치 축이 하나도 안 섰다**는 뜻이고, 실측상 그런 종목이 53%다.
    intrinsic_share: float | None = None
    relative_share: float | None = None
    # ①에 쓰인 다리들의 **실측 오차**와 거기서 유도한 안전마진 문턱(ADR-0017).
    # `warranted.leg_error()`의 반환 그대로. 회귀 경로에서만 채워진다 — 상수가 회귀의
    # 것이라 피어 중앙값 폴백에는 쓸 수 없다.
    leg_error: dict = field(default_factory=dict)
    # ⑤ 정규화 이익(ADR-0015). normalized_ratio가 이 축의 핵심이다 — 1보다 작으면
    # 현재 이익이 정상보다 높다는 뜻이고, 그것이 ⑤가 다른 축과 갈리는 이유 전부다.
    normalized_eps: float | None = None      # 정상 EPS
    normalized_years: int | None = None      # 평균에 실제로 쓴 연수 ("5년"이라 단정하지 말 것)
    normalized_ratio: float | None = None    # 정상이익 / 현재이익 (현재이익 ≤ 0이면 None)
    normalized_per: float | None = None      # 적용한 회귀 적정 PER
    weights: dict = field(default_factory=dict)   # 펀더멘털 종합에 쓴 가중치 (재정규화)
    skipped: list = field(default_factory=list)   # [(방법명, 건너뛴 사유)] — 번호 자리 유지용
    # [(방법명, 판정에서 뺀 사유)] — **`skipped`와 다르다.** `skipped`는 계산이 성립하지
    # 않아 값이 없는 것이고, 이것은 **값은 있는데 판정 종합에 넣지 않은** 것이다(②·④).
    # 사유 문자열을 여기서 내보내는 이유: 그 사유는 ADR-0006·0035의 주장이라 ADR과 같은
    # 곳에 살아야 한다. 화면이 손으로 적으면 판정 구성이 바뀔 때마다 조용히 썩는다 —
    # ①②③이 여덟 자리에 손으로 적혀 있다가 실제 구성과 어긋난 것이 바로 그 사고다.
    excluded_from_verdict: list = field(default_factory=list)
    # ②·④가 함께 쓰는 '자기 과거 PER 중앙값' 하나에 **컨센서스 반영 적정가**가 실제로 얼마나
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


def _warranted_fairs(coef: dict, mcap, sector, roe, eps, bps, ebitda_ps,
                     debt_ps, cash_ps, revenue_ps, is_loss, is_financial):
    """회귀 적정 배수로 다리별 적정가를 만든다 (ADR-0014).

    `_rel_fairs`와 다리 구성 규칙(적자면 PER 대신 PSR, 금융은 EV/EBITDA 제외)은 **같다**.
    다른 것은 배수를 어디서 얻느냐뿐이다 — 피어 중앙값이 아니라 회귀 적합값이다.
    반환의 셋째 값 `parts`는 화면에 계수 분해를 접어 두기 위한 것이다(ADR-0014 결정 다섯).
    """
    from .warranted import warranted_multiple

    fairs, used, parts = [], [], []

    def add(leg, fmt, to_price):
        w = warranted_multiple(coef.get(leg), mcap, sector, roe)
        if w["multiple"] is None:
            return
        price = to_price(w["multiple"])
        if price is None or price <= 0:
            return
        fairs.append(price)
        used.append(fmt.format(w["multiple"]))
        parts.append({**w, "leg": leg})

    if not is_loss and eps and eps > 0:
        add("per", "PER {:.1f}배", lambda m: m * eps)
    if bps and bps > 0:
        add("pbr", "PBR {:.2f}배", lambda m: m * bps)
    if is_loss and revenue_ps and revenue_ps > 0:
        add("psr", "PSR {:.1f}배", lambda m: m * revenue_ps)
    if not is_financial and ebitda_ps and ebitda_ps > 0:
        add("ev_ebitda", "EV/EBITDA {:.1f}배",
            lambda m: m * ebitda_ps - (debt_ps or 0) + (cash_ps or 0))
    return fairs, used, parts


def _relative_value(d: CompanyData, eps, bps, ebitda_ps, debt_ps, cash_ps,
                    revenue_ps=None, coef: dict | None = None
                    ) -> tuple[FairValue | None, dict]:
    """적정 배수를 회귀로 구한다(ADR-0014). 계수가 없으면 피어 중앙값으로 폴백한다.

    회귀로 가는 이유는 커버리지와 편향 둘 다다 — 전 종목 실측에서 피어 중앙값은
    ①이 44%(최소형주 80%) 빠졌고, 남은 값도 규모와 붙어 있었다(rho -0.141).
    회귀는 커버리지 100%에 rho -0.045다. 네 다리 모두에서 회귀가 이긴다.

    폴백 경로(계수 캐시 실패·표본 부족)는 종전 그대로다 — 규모 비교가능 피어
    (시총 1/5~5배)만 쓰고 부족하면 ①을 제외한다(ADR-0011). 예전에는 규모 필터 없는
    전체 피어로 한 번 더 내려갔는데, 그 경로가 측정상 가장 나빴다(rho −0.353).
    """
    if coef:
        roe = None
        if bps and bps > 0 and eps is not None:
            roe = eps / bps
        fairs, used, parts = _warranted_fairs(
            coef, d.market_cap, d.sector, roe, eps, bps, ebitda_ps,
            debt_ps, cash_ps, revenue_ps,
            is_loss=not (eps and eps > 0), is_financial=d.is_financial)
        if fairs:
            note = f"업종·규모·수익성 회귀 {', '.join(used)} · 다리 {len(fairs)}개"
            return (FairValue("업종 상대가치", min(fairs), float(np.median(fairs)),
                              max(fairs), note=note),
                    {"legs": len(fairs), "sensitivity": _leg_sensitivity(fairs),
                     "basis": "regression", "parts": parts})

    sized = comparable_peers(d.peers, d.market_cap)
    fairs, used = _rel_fairs(sized, d, eps, bps, ebitda_ps, debt_ps, cash_ps,
                             revenue_ps, min_n=2)
    if not fairs:
        return None, {}
    note = f"피어 중앙값 {', '.join(used)} · 다리 {len(fairs)}개"
    return (FairValue("업종 상대가치", min(fairs), float(np.median(fairs)), max(fairs),
                      note=note),
            {"legs": len(fairs), "sensitivity": _leg_sensitivity(fairs),
             "basis": "peer_median", "parts": []})


def _leg_sensitivity(fairs: list[float]) -> float | None:
    """다리 하나를 빼면 ①의 중앙값이 최대 몇 % 움직이나. 다리가 1개면 None.

    ①은 다리(PER·PBR·EV/EBITDA)가 2~3개뿐이라 **중앙값이 이상치 하나에 그대로 끌려간다.**
    20종목 실측에서 다리 하나를 빼자 중앙값이 최대 99% 움직였다(LG화학 +99.1%,
    삼성바이오 +66.8%, 위메이드 +65.1%).

    그렇다고 다리를 빼는 것은 답이 아니다 — 같은 실측에서 다리를 빼면 ①의 범위폭이
    1.00에서 0.05로 무너졌다. **근거는 줄었는데 확신만 커지는** 모양이고, 방법 간 편차로
    계산하는 신뢰도가 그만큼 부풀려진다. 그래서 값도 신뢰도 산식도 건드리지 않고,
    이 값이 얼마나 다리 하나에 매달려 있는지만 화면에 밝힌다.
    """
    if len(fairs) < 2:
        return None
    base = float(np.median(fairs))
    if base <= 0:
        return None
    return max(abs(float(np.median(fairs[:i] + fairs[i + 1:])) / base - 1)
               for i in range(len(fairs)))


# ── ⑤ 정규화 이익 ────────────────────────────────────────────────────
# 창은 문헌과 같은 8년이다 — Anderson & Brooks(2006, JBFA 33(7-8) 1063-1086)가 쓴 창이고,
# ADR-0015는 *"DART 이력이 6년이라 그 이상은 못 만든다"*는 이유로 5년에 머물렀다.
# **그 이유가 사실이 아니었다**(ADR-0025) — 6년은 DART가 아니라 `opendart.py`가 보고서를
# 2개만 받아서 나온 값이고, 실제 이력은 중앙 13년이다(`scripts/check_dart_depth.py`).
# 창을 늘리는 대가는 재서 확인했다: 커버리지 62%→62%, 종목당 호출 3→5회(하루 1회, 캐시).
# 최소 3년은 '평균'이라 부를 수 있는 하한이다.
# **이 값을 올리려면 `opendart.HISTORY_YEARS`도 함께 올려야 한다** — 데이터가 그만큼
# 안 오면 창은 조용히 짧아진다. `tests/test_normalized_earnings.py`가 그것을 막는다.
NORMALIZE_WINDOW = 8
NORMALIZE_MIN_YEARS = 3


def _normalized_earnings(fin) -> tuple[float | None, int]:
    """(정상 이익, 평균에 쓴 연수). 창을 못 채우면 (None, 실제 개수).

    창은 프레임의 **마지막 NORMALIZE_WINDOW 행**이다(과거→최신 정렬 전제).
    창 안 결측·무한대는 빼고 남은 것으로 평균한다 — 0으로 채우면 없는 이익을 지어낸다.
    **적자 연도는 빼지 않는다.** 빼면 정규화가 아니라 체리피킹이고, 사이클 저점을
    창에 담는 것이 이 축의 목적이다.
    """
    if fin is None or not hasattr(fin, "columns") or "net_income" not in fin.columns:
        return None, 0
    win = pd.to_numeric(fin["net_income"], errors="coerce").iloc[-NORMALIZE_WINDOW:]
    win = win[np.isfinite(win)]
    if len(win) < NORMALIZE_MIN_YEARS:
        return None, int(len(win))
    return float(win.mean()), int(len(win))


def _normalized_value(fin, shares, equity, mcap, sector, coef) -> tuple:
    """(FairValue | None, 메타) — 정상 이익 × 회귀 적정 PER (ADR-0015).

    ①②③이 셋 다 `배수 × 현재 회계값` 구조라 현재 EPS·BPS를 공통 입력으로 쓰고 서로
    상관 0.74~0.84다. 이 축은 그 구조 밖에 있다 — 정규화 항 `log(정상이익/현재이익)`이
    기존 축과 상관 **−0.407**로 음수라, 사이클 정점에서 현재 이익이 부풀어 기존 축이
    '싸다'고 말할 때 그것을 되돌린다.

    **ROE도 정규화 값을 넣는다.** 현재 적자면 ROE가 음수라 ADR-0014의 U자 더미가 배수를
    크게 올리는데(실측 +110%), 이익만 정규화하고 수익성은 현재 값을 쓰면 하필 이 축이
    가장 쓸모 있는 자리(현재 적자·정상 흑자)에서 정확히 틀린다.

    **계수가 없으면 폴백하지 않는다.** 이 축의 정체가 '회귀 배수 × 정규화 이익'이라
    배수를 바꾸면 다른 방법이 된다. 값을 지어내느니 축을 뺀다(ADR-0011).
    """
    from .warranted import warranted_multiple

    blank = {"eps": None, "years": 0, "ratio": None, "per": None, "reason": ""}
    ni, years = _normalized_earnings(fin)
    if ni is None:
        return None, {**blank, "years": years,
                      "reason": f"이익 이력 부족({years}년 · 최소 {NORMALIZE_MIN_YEARS}년)"}
    if ni <= 0:
        return None, {**blank, "years": years, "reason": f"{years}년 평균 이익이 적자"}
    if not shares or shares <= 0 or not equity or equity <= 0:
        return None, {**blank, "years": years, "reason": "주식수 또는 자기자본 없음"}

    per = warranted_multiple((coef or {}).get("per"), mcap, sector, ni / equity)["multiple"]
    if per is None:
        return None, {**blank, "years": years, "reason": "적정 배수 계수 없음"}

    eps = ni / shares
    mid = per * eps
    # math이 아니라 np를 쓴다 — 이 모듈은 math을 import하지 않는다(numpy·pandas만).
    if not np.isfinite(mid) or mid <= 0:
        return None, {**blank, "years": years, "reason": "계산 불가"}

    # 범위는 **창 안 이익의 흩어짐**에서 온다 — '정상 이익을 얼마로 보느냐'가 이 축의
    # 유일한 자유도이므로, 사이클 폭을 그대로 보여주는 것이 정직하다. 하위 분위가
    # 적자면 음수 적정가가 나오므로 그때는 범위를 좁혀 중심값에 붙인다.
    win = pd.to_numeric(fin["net_income"], errors="coerce").iloc[-NORMALIZE_WINDOW:]
    win = win[np.isfinite(win)]
    q25, q75 = float(win.quantile(0.25)), float(win.quantile(0.75))
    lo = per * q25 / shares if q25 > 0 else mid
    hi = per * q75 / shares if q75 > 0 else mid
    lo, hi = min(lo, mid), max(hi, mid)

    cur = float(win.iloc[-1]) if len(win) else None
    ratio = (ni / cur) if (cur is not None and cur > 0) else None
    note = f"{years}년 평균 이익 × 적정 PER {per:.1f}배"
    return (FairValue("정규화 이익", lo, mid, hi, note=note),
            {"eps": eps, "years": years, "ratio": ratio, "per": per, "reason": ""})


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


BAND_CORR_LIMIT = 0.90   # 이 이상이면 배수의 분위가 주가의 분위와 같은 말이다
BAND_MIN_YEARS = 3.0     # '자기 과거 분위'라 부르려면 이만큼은 봐야 한다
BAND_MIN_OBS = 200       # 기간은 길어도 관측이 드물면 분포가 몇몇 날에 좌우된다

# 창을 **명시적으로** 정한다 (ADR-0026). 예전에는 이 상수가 없었고, 밴드의 창은
# `base.py`의 `fetch_price_frame(..., period="5y")` 기본 인자가 정하고 있었다 —
# 즉 "5년"은 결정이 아니라 **부작용**이었다(ADR-0012가 라벨을 실측값으로 바꾼 이유이기도 하다).
# 7년인 근거는 `scripts/check_band_window.py`가 KR 127종목에서 잰 표다: 창이 길수록
# 펀더멘털이 움직일 시간이 생겨 `BAND_CORR_LIMIT` 탈락(=배수가 아니라 주가의 분위)이
# 21%→13%로 줄고, **5년에서 7년까지는 이력 부족 손실이 0**이며, 8년을 넘으면 개선이
# 멈추고 커버리지만 깎인다.
BAND_WINDOW_YEARS = 7.0

# ADR-0010의 RIM 게이트가 쓰는 PBR 중앙값의 창. **밴드 창과 일부러 다르다.**
# 그 게이트의 임계 `BOOK_REJECTED_PBR`은 5년 중앙값을 보고 정한 판단값이라, 창을 바꾸면
# 재본 적 없는 임계가 된다. 창을 옮기려면 임계를 다시 재고 새 ADR을 써야 한다(ADR-0026).
GATE_PBR_WINDOW_YEARS = 5.0


def _band_quality(mult: pd.Series, px: pd.Series, fund: pd.Series) -> dict:
    """이 밴드가 배수를 재는가, 주가를 다시 쓴 것인가 (ADR-0012).

    배수는 `주가 ÷ 펀더멘털`이라, 펀더멘털이 거의 움직이지 않으면 배수의 분위는
    **주가의 분위와 같은 말**이 된다. 그러면 ②는 '자기 역사 대비 싼가'가 아니라
    '주가가 자기 역사 대비 낮은가'를 답하는데, 한국 소형주는 98%가 자기 5년
    주가중앙값 아래라 답이 정해져 있다(`scripts/check_size_bias.py`).

    `corr`은 추세를 가진 두 로그 시계열 사이의 값이라 **유의성으로 읽으면 안 되는
    기술통계**다 — "배수가 주가와 같이 움직인 정도"로만 쓴다. 임계 0.90도 추정값이
    아니라 판단값이다(`PBR_GATE`와 같은 성격).

    `short`는 방법표의 '제외 사유' 칸(짧게), `detail`은 해설 카드에 들어간다.
    둘 다 **원인을 단정하지 않고 잰 값을 말한다**.
    """
    m = mult.where(mult > 0).dropna()
    p, f = px.reindex(m.index), fund.reindex(m.index)
    keep = (p > 0) & (f > 0)
    m, p, f = m[keep], p[keep], f[keep]

    n = int(len(m))
    years = float((m.index[-1] - m.index[0]).days / 365.25) if n > 1 else 0.0
    lm, lp, lf = (np.log(s) if n > 1 else None for s in (m, p, f))
    sd_fund = float(lf.std()) if n > 1 else float("nan")
    # 배수나 주가가 한 번도 안 변하면 상관은 정의되지 않는다(0으로 나눈다). 미리 끊는다.
    varies = n > 1 and float(lm.std()) > 0 and float(lp.std()) > 0
    corr = float(lm.corr(lp)) if varies else float("nan")
    out = {"n": n, "years": years, "corr": corr, "sd_fund": sd_fund}

    def done(price_band: bool, short: str, detail: str) -> dict:
        out.update({"price_band": price_band, "usable": short == "",
                    "short": short, "detail": detail})
        return out

    reweight = "판정에서 빼고 나머지 방법으로 판정하며 가중치는 다시 배분합니다."
    if n < BAND_MIN_OBS:
        return done(False, f"관측일 {n}일 — {BAND_MIN_OBS}일 미만",
                    f"배수를 관측한 날이 {n}일뿐입니다. 기간은 {years:.1f}년이지만 거래가 드물어 "
                    f"분위가 몇몇 날에 좌우됩니다. {reweight}")
    if years < BAND_MIN_YEARS:
        return done(False, f"창 {years:.1f}년 — {BAND_MIN_YEARS:g}년 미만",
                    f"배수를 관측한 기간이 {years:.1f}년입니다. '자기 과거 분위'라 부르려면 "
                    f"{BAND_MIN_YEARS:g}년 이상은 봐야 합니다. {reweight}")
    if not np.isfinite(corr):
        return done(True, "배수가 사실상 변하지 않음",
                    f"{years:.1f}년간 배수가 거의 그대로여서 분위를 만들 수 없습니다. {reweight}")
    if corr >= BAND_CORR_LIMIT:
        return done(True, f"배수가 주가와 함께 움직임 (상관 {corr:+.2f})",
                    f"{years:.1f}년간 펀더멘털의 변동이 로그 표준편차 {sd_fund:.3f}에 그쳐, 배수가 "
                    f"주가와 상관 {corr:+.2f}로 함께 움직였습니다. 이 밴드의 분위는 배수의 분위가 "
                    f"아니라 **주가의 분위**입니다 — 자기 역사 대비 싼지가 아니라 주가가 예전보다 "
                    f"낮은지를 말합니다. {reweight}")
    return done(False, "",
                f"{years:.1f}년간 펀더멘털이 로그 표준편차 {sd_fund:.3f}만큼 움직였고, 배수와 주가의 "
                f"상관은 {corr:+.2f}입니다. 주가의 분위와 구분되는 배수의 분위로 봅니다.")


def _last_years(s: pd.Series, years: float) -> pd.Series:
    """마지막 `years`년만 남긴다. 이미 그보다 짧으면 **손대지 않는다**.

    자를 것이 있을 때만 `Timedelta`를 만든다 — 창을 아주 크게 두면 `Timedelta`가
    범위를 넘어 터진다(실측: 999년). 상수를 잘못 두는 것이 예외로 죽을 일은 아니다.
    """
    if not len(s):
        return s
    win_days = int(365.25 * years)
    if (s.index[-1] - s.index[0]).days <= win_days:
        return s
    return s[s.index > s.index[-1] - pd.Timedelta(days=win_days)]


def _band(d: CompanyData, current_fund: float | None, kind: str):
    """(밴드 df, 현재 배수 백분위, FairValue 구성요소, 분위 배수 dict, 품질 dict) — kind: 'per'|'pbr'

    **판정에 쓸지 말지는 여기서 정하지 않는다.** 품질만 재서 함께 돌려주고
    `compute_valuation()`이 거른다(ADR-0012) — 판정에서 빠진 밴드도 차트에는 남기 때문이다.
    """
    col = "eps" if kind == "per" else "total_equity"
    per_share = kind == "pbr"  # eps는 이미 주당, equity는 주식수로 나눔
    daily = _fundamental_daily(d, col, per_share=per_share)
    if daily is None:
        return None, None, None, None, None
    daily = daily.where(daily > 0)
    # 과거 배수는 '그날 실제 주가 ÷ 그날 펀더멘털'이라 미조정 종가를 써야 한다. 수정종가는
    # 과거를 그 뒤 지급된 배당만큼 낮춰 잡아 과거 PER·PBR이 실제보다 낮게 깔리고, 그만큼
    # 적정가(분위 배수 × 현재 펀더멘털)가 낮아져 현재가가 늘 비싸 보인다. 고배당일수록 심하다.
    px = actual_prices(d)
    # 창을 여기서 자른다 — 안 자르면 밴드의 창이 '주가를 몇 년 받았나'가 되어, 데이터
    # 수집 쪽을 건드리는 순간 판정이 **말없이** 바뀐다(ADR-0026). 자를 것이 없으면
    # (주가가 창보다 짧으면) 그대로다. `_band_quality`가 실측 창을 다시 재서 화면에 낸다.
    px = _last_years(px, BAND_WINDOW_YEARS)
    mult = (px / daily.reindex(px.index)).dropna()
    quality = _band_quality(mult, px, daily) if len(mult) else None
    if len(mult) < BAND_MIN_OBS:
        return None, None, None, None, quality
    q = mult.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    qdict = {int(p * 100): float(v) for p, v in q.items()}
    qdict["current"] = float(mult.iloc[-1])
    # ADR-0010의 RIM 게이트는 이 dict의 50분위를 읽는다. **그 임계(BOOK_REJECTED_PBR)는
    # 5년 중앙값에서 정해진 값**이라, 밴드 창을 7년으로 옮겼다고 게이트까지 따라가면
    # 재본 적 없는 임계로 RIM을 빼게 된다. 그래서 게이트 몫은 5년으로 따로 잰다
    # (ADR-0026 결정 3). 게이트를 옮기려면 임계를 다시 재고 별도 ADR을 써야 한다.
    gate_mult = _last_years(mult, GATE_PBR_WINDOW_YEARS)
    qdict["gate_median"] = float(gate_mult.median()) if len(gate_mult) else None
    qdict["gate_years"] = (float((gate_mult.index[-1] - gate_mult.index[0]).days / 365.25)
                           if len(gate_mult) > 1 else None)
    pct = float((mult < mult.iloc[-1]).mean() * 100)
    band = pd.DataFrame({"price": px})   # 밴드와 같은 기준이어야 차트에서 위치가 맞는다
    for p, v in q.items():
        band[f"q{int(p * 100)}"] = daily * v
    band = band.dropna(subset=["price"])
    if not current_fund or current_fund <= 0:
        return band, pct, None, qdict, quality
    fair = (float(q.loc[0.25]) * current_fund,
            float(q.loc[0.50]) * current_fund,
            float(q.loc[0.75]) * current_fund)
    return band, pct, fair, qdict, quality


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

# ── 반대편 — 시장이 장부가를 오래 거부한 경우 (ADR-0010) ─────────────
# ADR-0007은 '장부가가 실제보다 작은' 쪽(㉠)만 막았다. 그 대칭이 비어 있었다.
#
# RIM은 ROE가 r에 가까우면 V ≈ B로 수렴한다. 그러면 괴리율이 사실상 1/PBR − 1이 되어
# **PBR 0.5인 회사는 계산하기 전에 이미 '+100% 저평가'**다. 전 종목(2,688) 실측에서
# ③ 괴리율과 1/PBR의 순위상관이 +0.973이었다 — 독립된 관점이 아니라 PBR을 되읽고 있었다
# (재현: python scripts/check_size_bias.py). 국내 PBR이 시총을 따라 0.50↔1.44로 갈리므로,
# 이 되읽기가 그대로 규모 편향이 된다.
#
# 오늘 하루 장부가 아래인 것은 RIM이 말할 자리다 — 그게 이 모형의 존재 이유다.
# 그러나 시장이 **5년 내내** 장부가 아래로 매겨 왔고 회사가 그동안 **자본비용도 못 벌었다면**,
# 그 장부가는 되찾을 수 있는 값이 아니다. 그때 RIM의 답은 밸류에이션이 아니라
# '시장이 5년째 틀렸다'는 주장이고, 삼각측량의 독립된 한 표로 세울 수 없다.
#
# 둘 다 만족할 때만 제외한다(하나로는 부족하다 — 싼 것과 값어치 없는 것은 다르다).
# 임계는 ADR-0007의 값들과 같은 성격이다. 국내 시장 분포를 보고 정한 **판단값**이지
# 데이터로 추정한 값이 아니다.
BOOK_REJECTED_PBR = 0.7    # 5년 PBR 중앙값이 이 아래 = 오늘의 일시적 하락이 아니다
UNDEREARN_YEARS = 2        # 최근 3개년 중 ROE가 자본비용에 못 미친 해가 이 이상


def _underearning(d: CompanyData, r: float | None) -> tuple[int | None, int]:
    """최근 3개년 중 ROE가 자본비용 r에 못 미친 해의 수 → (미달 연수, 센 연수).

    잴 수 없으면 (None, 0). 못 재는 것과 '미달 0년'은 다르다 — 앞은 게이트를 열지 않는다.
    """
    fin = d.financials
    if r is None or not {"net_income", "total_equity"} <= set(fin.columns):
        return None, 0
    eq = fin["total_equity"]
    avg = ((eq + eq.shift(1)) / 2).fillna(eq)
    s = (fin["net_income"] / avg.where(avg > 0)).dropna().tail(3)
    if len(s) < 2:                     # 2개년도 못 보면 '지속'을 말할 수 없다
        return None, 0
    return int((s < r).sum()), len(s)


def _book_quality(d: CompanyData, pbr: float | None, roe: float | None,
                  r: float | None = None, pbr_q: dict | None = None) -> dict:
    """장부가가 회사 가치를 담고 있는가 → {distorted, short, detail, 근거 수치}.

    `short`는 방법표의 '제외 사유' 칸(짧게), `detail`은 해설 카드에 들어간다.
    둘 다 **원인을 단정하지 않고 잰 값을 말한다** — 판별에 쓴 수치를 문장에 그대로 넣는다.

    두 방향을 본다: 장부가가 실제보다 **작은** 경우(ADR-0007)와, 시장이 그 장부가를
    **오래 거부한** 경우(ADR-0010). 둘 다 RIM의 닻이 성립하지 않는 자리다.
    """
    fin = d.financials
    ta, eq = d.latest("total_assets"), d.latest("total_equity")
    intan = d.latest("intangibles")
    share = (intan / ta) if (intan is not None and ta and ta > 0) else None
    bb = fin["buyback"].dropna() if "buyback" in fin.columns else None
    ratio = (float(bb.sum()) / eq) if (bb is not None and len(bb) and eq and eq > 0) else None
    # **밴드의 50분위가 아니라 게이트용 5년 중앙값을 읽는다**(ADR-0026 결정 3). 밴드 창이
    # 7년으로 옮겨 갔어도 이 게이트의 임계는 5년에서 정해진 값이라 따라가지 않는다.
    # 옛 캐시·옛 호출로 `gate_median`이 없으면 50분위로 물러선다(예전 동작).
    pbr_5y = (pbr_q or {}).get("gate_median")
    if pbr_5y is None:
        pbr_5y = (pbr_q or {}).get(50)
    under, of_years = _underearning(d, r)
    out = {"pbr": pbr, "intangible_share": share, "buyback_ratio": ratio,
           "years": int(len(bb)) if bb is not None else 0,
           "pbr_5y_median": pbr_5y, "underearn_years": under, "underearn_of": of_years}

    def done(distorted, short, detail):
        out.update({"distorted": distorted, "short": short, "detail": detail})
        return out

    if pbr is None:
        return done(True, "자기자본을 확인하지 못함",
                    "자기자본(장부가)을 받지 못해 장부가 기반 모형(RIM)을 적용할 수 없습니다.")

    # ── 반대편 게이트 (ADR-0010) — 둘 다여야 한다 ──
    # 잴 수 없으면 열지 않는다. '시장이 거부했다'는 근거 없이 방법을 빼면,
    # 상장기간이 짧다는 이유만으로 RIM이 사라진다.
    if (pbr_5y is not None and pbr_5y < BOOK_REJECTED_PBR
            and under is not None and under >= UNDEREARN_YEARS):
        return done(True, f"5년 PBR 중앙값 {pbr_5y:.2f}배 · {of_years}년 중 {under}년 자본비용 미달",
                    f"시장이 최근 5년 내내 이 회사를 장부가의 {pbr_5y:.2f}배 수준으로 매겨 왔고"
                    f"(오늘 {pbr:.2f}배), 최근 {of_years}개년 중 {under}년은 ROE가 자본비용"
                    f"({r:.1%})에 못 미쳤습니다. RIM은 장부가를 닻으로 삼는 모형이라 "
                    "이 조건에서는 '장부가만큼은 된다'는 답을 내는데, 그건 밸류에이션이 아니라 "
                    "5년치 시장 판단이 틀렸다는 주장에 가깝습니다. 장부가를 되찾을 수 있다고 "
                    "볼 근거를 찾지 못해 RIM을 제외하고 나머지 방법으로 판정하며, 가중치는 "
                    "다시 배분합니다. 자산 매각·구조조정처럼 장부가가 실현될 계기가 있다면 "
                    "이 판단은 보수적인 쪽으로 틀린 것입니다.")

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
    """중심 = 타깃 멀티플 × 선행 EPS. 타깃 멀티플은 **자기 과거 PER 중앙값 우선**,
    없으면 피어 선행PER 폴백. 범위는 자기 과거 밴드 q25~q75. 창의 길이는 종목마다
    다르고(실측 2.8~5.0년) 가격 밴드로 판별된 다리는 아예 넘어오지 않는다(ADR-0012).

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
        mult, label = q50, "자기 과거 PER 중앙값"
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
def compute_valuation(d: CompanyData, ind, r_equity: float,
                      warranted_coef: dict | None = None) -> ValuationResult:
    """ind: Indicators, r_equity: RIM 요구수익률(기본 CAPM k_e).

    warranted_coef: 시장의 다리별 회귀 계수(ADR-0014). 기본 None이라 넘기지 않으면
    ①은 종전대로 피어 중앙값으로 간다 — 호출부를 하나씩 옮길 수 있게 한 것이다.
    """
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
    fv, rel_meta = (None, {}) if mismatch else _relative_value(
        d, eps, bps, ebitda_ps, debt_ps, cash_ps, revenue_ps, coef=warranted_coef)
    res.relative_legs = rel_meta.get("legs")
    res.relative_leg_sensitivity = rel_meta.get("sensitivity")
    res.relative_basis = rel_meta.get("basis")
    res.relative_parts = rel_meta.get("parts") or []
    # 오차 상수는 회귀(D2)를 재서 나온 값이라 폴백 경로에는 쓸 수 없다. 폴백의 오차는
    # ADR-0014의 '현행 A' 열에 따로 있지만 그건 다른 계산의 값이다 — 섞지 않는다.
    if res.relative_basis == "regression" and res.relative_parts:
        res.leg_error = leg_error(d.market, [p["leg"] for p in res.relative_parts])
    if fv:
        res.estimates.append(fv)
        # 값도 신뢰도 산식도 건드리지 않는다 — 이 값이 다리 하나에 얼마나 매달려 있는지만
        # 밝힌다. 다리를 빼면 범위가 좁아져 신뢰도가 오히려 부풀기 때문이다(20종목 실측).
        if (s := res.relative_leg_sensitivity) is not None and s >= 0.30:
            res.notes.append(ValuationNote(
                "info",
                f"① 업종 상대가치는 배수 {res.relative_legs}개의 중앙값입니다. 그중 하나만 빼도 "
                f"중앙값이 {s:.0%} 움직입니다 — 다리가 적어 배수 하나가 결론을 좌우한다는 뜻이니, "
                "①의 중심값 하나보다 범위와 다른 방법과의 차이를 함께 보세요."))
        # PSR은 실측 오차가 가장 큰 다리인데 하필 적자 기업 전용이다. 정밀해 보이면
        # 안 된다는 ADR-0014 한계 절이 이걸 밝히라고 요구한다.
        if any(p["leg"] == "psr" for p in res.relative_parts):
            res.notes.append(ValuationNote(
                "info",
                f"이 종목은 적자라 ①에 매출 기준 배수(PSR)가 들어갔습니다. "
                f"PSR이 {psr_error_phrase(res.leg_error)}, ①의 중심값보다 범위와 다른 "
                "방법과의 차이를 함께 보세요."))
    elif mismatch:
        res.skipped.append(("업종 상대가치", ccy_reason))
    else:
        res.skipped.append(("업종 상대가치", "규모 비교가능 피어 부족"))
        res.notes.append(ValuationNote(
            "info", "시가총액이 이 회사의 1/5~5배인 동종 기업을 2곳 이상 찾지 못해 상대가치 "
            "평가를 제외합니다. 규모가 크게 다른 기업의 배수를 끌어오면 적정가가 그 규모 "
            "차이만큼 밀리기 때문에, 넓혀서 계산하는 대신 이 방법을 빼고 나머지로 판정합니다."))

    # ② 역사적 밴드 (PER 우선, 적자면 PBR)
    # 통화가 섞이면 밴드 자체가 무의미하므로 계산하지 않는다 — 차트에도 그려지면 안 된다.
    per_fair = pbr_fair = per_qual = pbr_qual = None
    if not mismatch:
        res.per_band, res.per_percentile, per_fair, res.per_q, per_qual = _band(
            d, eps if eps and eps > 0 else None, "per")
        res.pbr_band, res.pbr_percentile, pbr_fair, res.pbr_q, pbr_qual = _band(d, bps, "pbr")
        res.band_quality = {k: v for k, v in (("per", per_qual), ("pbr", pbr_qual)) if v}
        if per_qual and per_qual["usable"]:
            res.per_q_pricing = res.per_q
    # 다리 선택은 그대로 둔다(흑자면 PER, 적자면 PBR). 관문은 **고른 다리를 쓸 수 있는가**만
    # 정한다 — PER이 걸렸다고 PBR로 넘어가면 화면의 'PBR(적자로 대체)'가 사실이 아니게 된다.
    fair = per_fair or pbr_fair
    qual = per_qual if per_fair else pbr_qual
    if mismatch:
        res.skipped.append(("역사적 밴드", ccy_reason))
    elif fair and qual and qual["usable"]:
        basis = "PER" if per_fair else "PBR(적자로 대체)"
        res.estimates.append(FairValue(
            "역사적 밴드", fair[0], fair[1], fair[2],
            note=f"{qual['years']:.1f}년 {basis} 25~75분위 × 현재 펀더멘털"))
    elif fair and qual:
        # 계산은 됐지만 그 분위가 배수의 분위가 아니거나 창이 짧다 (ADR-0012).
        res.skipped.append(("역사적 밴드", qual["short"]))
        res.notes.append(ValuationNote("info", qual["detail"]))
    else:
        res.skipped.append(("역사적 밴드", "상장기간 짧음 또는 적자 지속"))
        res.notes.append(ValuationNote(
            "info", "상장기간이 짧거나 적자가 길어 역사적 밴드를 계산하지 못했습니다."))

    # ③ RIM — 장부가가 회사 가치를 담고 있을 때만 쓴다(ADR-0007).
    ttm_roe = ind.profitability.get("roe")
    roe_raw = _recent_roe(d, ttm_roe)
    pbr_actual = d.market_cap / equity if equity and equity > 0 else None
    # pbr_q는 위 ②에서 이미 만든 5년 PBR 분위다 — 새로 받아올 것이 없다.
    book = _book_quality(d, pbr_actual, roe_raw, r_equity, res.pbr_q)
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

    # ⑤ 정규화 이익 — 최근 몇 해 평균 이익에 회귀 적정 PER을 곱한다(ADR-0015).
    # 통화 불일치면 ①②③과 같은 이유로 성립하지 않는다(주가 ÷ 재무값 비교다).
    if mismatch:
        res.skipped.append(("정규화 이익", ccy_reason))
    else:
        fv5, nm = _normalized_value(d.financials, shares, equity, d.market_cap,
                                    d.sector, warranted_coef)
        res.normalized_eps = nm["eps"]
        res.normalized_years = nm["years"] or None
        res.normalized_ratio = nm["ratio"]
        res.normalized_per = nm["per"]
        if fv5:
            res.estimates.append(fv5)
            # 이 축을 넣은 이유가 '기존 축과 갈리는 것'이므로, 갈릴 때 왜인지 밝힌다.
            # 감추면 사용자는 ⑤만 혼자 다른 값을 내는 것을 오류로 읽는다.
            r5 = nm["ratio"]
            if r5 is not None and (r5 < 1 / 1.5 or r5 > 1.5):
                direction = ("현재 이익이 정상보다 높습니다" if r5 < 1
                             else "현재 이익이 정상보다 낮습니다")
                res.notes.append(ValuationNote(
                    "info",
                    f"최근 {nm['years']}년 평균 순이익이 현재의 {r5:.2f}배입니다 — {direction}. "
                    "⑤ 정규화 이익은 이 차이를 되돌린 값이라 다른 방법과 갈릴 수 있고, "
                    "그 갈림 자체가 이 방법이 말하려는 것입니다."))
        else:
            res.skipped.append(("정규화 이익", nm["reason"]))

    # ④ 선행 이익 — 애널리스트 컨센서스가 있을 때만. **판정에는 들어가지 않는다**(ADR-0006):
    # 계산해서 estimates에 넣되, 종합은 '컨센서스 반영' 값에만 반영해 병기한다.
    cons = d.consensus
    if cons is None or not cons.forward_eps or cons.forward_eps <= 0:
        res.skipped.append(("선행 이익(컨센서스)", "애널리스트 커버리지 없음"))
    else:
        peers = comparable_peers(d.peers, d.market_cap)   # 규모 비교가능 피어만
        # ④는 ②와 **같은** 자기 과거 PER 중앙값을 타깃 배수로 쓴다. ②를 판정에서 뺐는데
        # 그 배수를 그대로 넘기면 방금 믿을 수 없다고 판단한 값이 병기값으로 돌아온다(ADR-0012).
        fv4 = _forward_value(cons.forward_eps, peer_median(peers, "forward_per", min_n=2),
                             res.per_q_pricing)
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
                        f"{res.forward_growth:+.0%}). 곱하는 배수는 '자기 과거 PER의 "
                        "중앙값'이라 이익이 지금과 다르던 시기에서 나온 값입니다 — 두 값의 "
                        "국면이 어긋나 선행 이익 방법이 실제보다 낙관적(이익 증가 국면)이거나 "
                        "비관적(감소 국면)으로 나올 수 있습니다. 판정은 이 방법을 쓰지 않지만, "
                        "'컨센서스 반영' 값은 이만큼 흔들립니다 — 아래 컨센서스 목표주가와 "
                        "반드시 함께 보세요."))
        else:
            res.skipped.append(("선행 이익(컨센서스)", "밴드·피어 멀티플 부족"))

    # ── 종합 (ADR-0006) ─────────────────────────────────────────────
    # 두 개를 낸다. **판정은 ①③⑤(펀더멘털)으로만** 내고, ④를 얹은 값은 따로 들어
    # 화면에 나란히 세운다. 가중치는 둘 다 METHOD_WEIGHTS를 각자의 방법 집합에 대해
    # 재정규화해 쓴다 — 순위(선행이익 > 이익 멀티플 > 장부가)는 그대로다.
    core = [e for e in res.estimates if e.method in FUNDAMENTAL_METHODS]
    # 병기는 **판정 방법 + ④**여야 한다. `res.estimates`를 그대로 쓰면 ②까지 섞이는데,
    # ADR-0035로 ②가 판정에서 빠진 뒤에는 두 값의 차이가 더 이상 '④의 몫'이 아니게 된다.
    # 그러면 바로 아래 주석이 약속하는 문장("이 차이가 곧 시장 기대분")이 거짓이 된다.
    # ②를 판정에서만 빼고 병기에 남겨 두는 것은 **선택지가 아니라 버그**다.
    with_fwd = core + [e for e in res.estimates if e.method == CONSENSUS_METHOD]
    if with_fwd:
        # 컨센서스 반영 종합 — ④가 실제로 있을 때만 펀더멘털과 다른 값이 된다.
        (res.fair_low_consensus, res.fair_mid_consensus,
         res.fair_high_consensus, res.weights_consensus) = _weighted(with_fwd)
        res.gap_consensus = res.fair_mid_consensus / d.price - 1
        res.verdict_consensus = _verdict(res.gap_consensus)

    # ①③⑤가 하나도 없으면(통화 불일치 등으로 전부 제외) 판정을 낼 재료가 ④뿐이다.
    # 이때는 화면을 비우는 대신 ④에 기대되, 그 사실을 감추지 않는다.
    #
    # **폴백은 `res.estimates`가 아니라 `with_fwd`다.** ②가 판정에 들기 전에는 둘이
    # 같았다(core가 ①②③⑤였으니 나머지가 ④뿐). ADR-0035로 ②가 core에서 빠지자
    # `res.estimates`에 ②가 남아, **폴백을 타는 종목에서만 ②가 판정을 끌게 된다** —
    # 예측력이 없다고 판단해 뺀 축이 뒷문으로 돌아오는 것이고, 바로 위 주석의
    # "재료가 ④뿐이다"도 거짓이 된다. 재료가 정말 없으면 판정을 내지 않는 쪽이 맞다.
    res.fundamental_only = bool(core)
    basis = core or with_fwd
    if basis:
        mids = [e.mid for e in basis]
        res.fair_low, res.fair_mid, res.fair_high, res.weights = _weighted(basis)
        res.gap = res.fair_mid / d.price - 1
        res.verdict = _verdict(res.gap)
        # 값은 냈는데 판정에 안 들어간 방법과 그 사유. 화면의 '판정 제외 · 참고' 배지가
        # 이걸 그대로 읽는다 — 가중 칸을 빈칸으로 두면 '빠뜨렸나'로 읽히기 때문이다.
        #
        # **판정 축 목록이 아니라 `res.weights`에서 뺀다.** 둘은 같지 않다: 펀더멘털이
        # 하나도 안 서면 판정을 ④로 내는데(`basis = res.estimates`), 그때 ④는 가중을
        # 갖는다. 상수 목록으로 재면 그 종목에서 ④가 **가중도 있고 제외 목록에도 오르는**
        # 모순이 된다. 화면도 `v.weights`로 배지를 켜므로 같은 것을 보게 맞춘다.
        #
        # `res.estimates`만 돈다 — 값을 못 낸 방법은 `skipped`의 몫이고, 둘이 겹치면
        # 같은 사실이 두 곳에 살게 된다(화면은 그 사유를 그리지도 않는다).
        for e in res.estimates:
            if e.method in res.weights:
                continue
            res.excluded_from_verdict.append((e.method, EXCLUSION_REASONS.get(
                e.method, "값은 계산했지만 판정 종합에는 넣지 않았습니다.")))
        # 이 판정이 무엇에 기대는가 (ADR-0018). **명목 가중이 아니라 실효 가중**이다 —
        # 방법이 빠지고 재정규화된 뒤의 값이라, 141종목 실측에서 절대가치 중앙값이
        # 0.0%였다(명목 16.7%). 평균 9.3%가 그 사실을 가리고 있었다.
        res.intrinsic_share = sum(w for m, w in res.weights.items()
                                  if m in INTRINSIC_METHODS)
        res.relative_share = sum(w for m, w in res.weights.items()
                                 if m in RELATIVE_METHODS)
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
                f"회사 실적·자산으로 서는 방법({marks_of(FUNDAMENTAL_METHODS)})이 전부 "
                f"계산되지 않아, 판정을 컨센서스 선행 이익({METHOD_MARKS[CONSENSUS_METHOD]}) "
                "하나에만 의존해 냈습니다 — 이 판정은 시장 기대와 독립적이지 않습니다. "
                "보수적으로 해석하세요."))

        if len(mids) >= 2 and res.fair_mid:
            disp = float(np.std(mids) / abs(np.mean(mids)))
            res.dispersion = disp
            n_eff, capped = effective_axes(list(res.weights or {}), d.market)
            res.n_eff = n_eff
            res.confidence, spread, cap = confidence_grade(
                disp, len(mids), n_eff, capped)
            res.confidence_spread, res.confidence_cap = spread, cap
            if res.confidence == "낮음":
                res.notes.append(ValuationNote("warn", f"평가 방법 간 편차가 큽니다(±{disp:.0%}). "
                                 "판정을 보수적으로 해석하세요."))
            if cap != spread and res.confidence == cap:
                # 상한이 실제로 등급을 내린 경우에만 말한다. 안 그러면 "흩어짐은 작은데
                # 신뢰도가 낮다"가 이유 없이 보인다.
                res.notes.append(ValuationNote(
                    "info",
                    f"이 판정에 쓴 방법 {len(res.weights or {})}개는 서로 겹칩니다 — 실질적으로 "
                    f"{n_eff:.1f}개 몫입니다. 값이 가깝게 나온 것이 '여러 방법이 독립적으로 "
                    "합의했다'는 뜻은 아닙니다."))
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
            f"판정은 회사가 이미 낸 실적·자산({marks_of(res.weights or {})})만으로 냈습니다. "
            f"애널리스트 컨센서스 선행 이익({METHOD_MARKS[CONSENSUS_METHOD]})까지 넣으면 "
            f"적정가가 {res.consensus_premium:+.0%} "
            f"달라집니다" + (f" — 판정도 '{res.verdict_consensus}'로 갈립니다." if flip else ".") +
            " 이 차이가 곧 '지금 주가가 정당화되려면 시장이 기대하는 만큼의 실적 변화가 "
            "실제로 와야 하는 크기'입니다."))
        # ②와 ④는 같은 식(자기 과거 PER 중앙값 × EPS)이라 이 배수 하나가 틀리면 둘이
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
                f"배수(자기 과거 PER 중앙값)에 각각 TTM EPS와 컨센서스 EPS를 곱한 값입니다 — "
                f"서로 다른 관점이 아니라 같은 관점의 과거판·미래판이라, 그 값의 "
                f"{res.shared_multiple_share:.0%}가 이 배수 하나에 의존합니다. 두 방법이 "
                "비슷하게 나와도 '독립적으로 합의했다'는 뜻이 아닙니다."))
    return res
