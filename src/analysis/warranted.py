"""적정 배수를 업종·규모·수익성 회귀로 구한다 (ADR-0014).

    log(배수) = α + β₁·log(시가총액) + Σ ROE구간더미 + Σ 업종더미

피어 중앙값이 하던 일을 회귀 적합값이 대신한다. 중앙값은 규모를 '거르는' 이분법이라
창 경계에서 정보가 끊기고 표본이 모자라면 다리가 통째로 사라지는데, 회귀는 규모를
**계수로 연속 반영**하므로 표본을 잃지 않는다(ADR-0014 실측: 커버리지 94% → 100%).

이 모듈은 **순수 함수**다 — 네트워크도 캐시도 없다. 데이터 수집은
`src/data/universe_multiples.py`가 맡는다.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# ROE 구간 경계. **선형으로 넣으면 안 된다** — 전 종목 실측에서 ROE와 PBR은 U자였다
# (대규모 적자 PBR 중앙 1.53 · ROE 0~5% 구간 0.48 · ROE>15% 1.59). 선형 항으로 넣으면
# 계수 부호가 이론과 반대로 나온다(t = -15.2). 흑자만 떼면 +3.20으로 정상이 된다.
ROE_EDGES = [-math.inf, -0.20, -0.05, 0.0, 0.05, 0.10, 0.15, math.inf]
ROE_LABELS = ["≤-20%", "-20~-5%", "-5~0%", "0~5%", "5~10%", "10~15%", ">15%"]

# 업종 더미를 자기 이름으로 세울 최소 표본. 이보다 얇으면 '기타'로 묶는다.
# 미국 실측에서 세부산업 127개(셀당 3곳)가 섹터 11개보다 **나빴다**(MAE 0.484 vs 0.470) —
# 잘게 쪼갤수록 좋은 것이 아니라 '표본이 받쳐주는 가장 세밀한 분류'가 좋다.
SECTOR_MIN_N = 10
OTHER_SECTOR = "기타"

# ── 다리별 실측 오차 (ADR-0017) ─────────────────────────────────────
# leave-one-out 실측을 옮긴 상수다. 계수를 다시 학습해도 따라 바뀌지 않으므로,
# **회귀의 함수형이 바뀌면 손으로 다시 재서 고쳐야 한다.**
#
#     python scripts/check_warranted.py KR      ← 붙여넣을 줄을 그대로 찍어 준다
#     python scripts/check_warranted.py US
#
# 2026-08-05 갱신(ADR-0022): #115가 규모 항을 스플라인으로 바꾼 뒤 다시 쟀다. 이전 값은
# 직선 함수형의 것이었다. 한국은 1~4% 안쪽으로 움직였고(꼬리만 고친 변경이라 전체 평균은
# 거의 그대로다), **미국 PSR·EV/EBITDA는 이번에 처음 쟀다** — 전에는 아예 없어서 화면이
# "측정된 적이 없습니다"라고 말하던 자리다.
#
# 이 값이 재는 것은 '적정가가 맞았나'가 **아니라** '회귀가 그 종목의 시장 배수를 얼마나
# 맞히나'다. 적정가의 정확도는 원리적으로 못 잰다(ADR-0009). 그럼에도 화면에 내는 이유는,
# 적정 배수가 이만큼 흔들리면 적정가도 **최소** 그만큼 흔들리기 때문이다.
#
# **여전히 미측정 다리는 넣지 않는다.** 시장이 늘면 그 시장 값을 재기 전까지 비워 둔다 —
# 다른 시장 값을 대신 쓰는 것은 지어내는 것이다(ADR-0011: 오염된 값보다 '없음').
# 미국 PSR이 한국의 0.897이 아니라 **0.656**으로 나온 것이 그 이유를 그대로 보여 준다.
LEG_MAE = {
    "KR": {"pbr": 0.570, "per": 0.583, "psr": 0.897, "ev_ebitda": 0.667},
    "US": {"pbr": 0.443, "per": 0.406, "psr": 0.656, "ev_ebitda": 0.379},
}
LEG_LABEL = {"per": "PER", "pbr": "PBR", "psr": "PSR", "ev_ebitda": "EV/EBITDA"}


def leg_error(market: str, legs) -> dict:
    """①에 쓰인 다리들의 실측 오차 → 원 스케일 폭과 안전마진 문턱 (ADR-0017).

    **합성 오차를 만들지 않는다.** ①은 다리별 적정가의 *중앙값*이라 다리 오차를 평균해도
    중앙값의 오차가 되지 않고, ②③⑤의 오차는 애초에 측정할 수 없다(ADR-0009). 그래서 잰
    것만 그대로 늘어놓고, **가장 나쁜 다리**를 기준으로 안전마진을 말한다(보수적인 쪽).

    로그 MAE `m`은 원 스케일에서 비대칭이다 — 위로 `exp(m)−1`, 아래로 `exp(−m)−1`이다.
    안전마진은 **아래쪽**을 쓴다: 실제 적정가가 추정치의 `exp(−m)`배일 수 있으므로 그보다
    싸야 오차를 감안하고도 싸다고 말할 수 있다.
    """
    table = LEG_MAE.get((market or "").upper(), {})
    seen, order = set(), []
    for lg in legs or []:
        if lg not in seen:
            seen.add(lg)
            order.append(lg)
    measured = [(lg, table[lg]) for lg in order if lg in table]
    out = {
        "measured": [{"leg": lg, "label": LEG_LABEL.get(lg, lg), "mae": m,
                      "up": math.exp(m) - 1, "down": math.exp(-m) - 1}
                     for lg, m in measured],
        "unmeasured": [LEG_LABEL.get(lg, lg) for lg in order if lg not in table],
        "worst_leg": None, "worst_mae": None, "up": None, "margin": None,
    }
    if measured:
        lg, m = max(measured, key=lambda x: x[1])
        out.update({"worst_leg": LEG_LABEL.get(lg, lg), "worst_mae": m,
                    "up": math.exp(m) - 1, "margin": math.exp(-m) - 1})
    return out


# ── 방법 사이의 겹침 (ADR-0022) ──────────────────────────────────────
# 판정 방법들이 서로 독립이라는 전제가 신뢰도 산식에 깔려 있는데, 그 전제가 틀렸다.
# ①과 ⑤는 **같은 적정 배수**를 쓰고(ADR-0015 · AAPL 실례), ②도 배수 기반이다.
# 구조적으로도 괴리율의 55~60%를 `−log(현재배수)`라는 공통 항이 설명한다(ADR-0014).
#
#     python scripts/check_confidence.py KR      ← 붙여넣을 줄을 그대로 찍어 준다
#     python scripts/check_confidence.py US
#
# 값은 **방법 쌍별 피어슨 상관**(로그 괴리율 기준, 전 종목)이다. 종목 하나 안에서는
# 상관을 잴 수 없어(방법마다 값이 하나뿐) 종목들 사이에서 재서 개별 종목에 적용한다 —
# **근사다.** 이 한계는 ADR-0022에 적어 뒀다.
#
# **시장마다 따로 잰다. 옮겨 쓰지 않는다.** 아래 두 표가 그 규칙의 근거다 — 같은 쌍이
# ①↔⑤에서 KR +0.263 대 US +0.763, ③↔⑤에서 KR −0.015 대 US +0.723으로 갈린다.
# 한쪽을 복사했으면 다른 쪽 신뢰도가 통째로 틀렸을 값이다(ADR-0017의 "빌려 쓰는 것은
# 지어내는 것"이 오차표에서 말한 것과 같은 말이다).
#
# 2026-08-05 실측 · KR 174종목(시총 5분위 층화) · US 198종목(S&P 500·400·600 3층).
#
# **KR에서 결과가 설계의 예상을 뒤집었다** — 설계는 ①②⑤(배수 기반)가 서로 겹치고
# ③ RIM이 독립일 것으로 봤는데, 한국에서는 **③ RIM과 ①이 +0.787로 가장 겹치고**
# ①과 ⑤는 +0.263에 그쳤다.
#
# **그리고 미국이 그 해석을 다시 뒤집었다.** ADR-0015가 "①과 ⑤가 같은 적정 PER을
# 쓴다"고 적은 것은 AAPL 한 종목 관찰이었고, 한국 106종목에서 안 보이길래 ADR-0022는
# 그것을 *한 종목 일반화의 실수*로 적었다. 그런데 **AAPL의 시장에서 재니 +0.763이다.**
# 관찰 자체는 맞았고, 틀린 것은 그것을 **다른 시장에 옮겨 놓고 반증한** 쪽이다.
# 일반화가 걸린 축이 종목 수가 아니라 **시장**이었다.
METHOD_RHO: dict[str, dict[tuple[str, str], float]] = {
    "KR": {
        ("수익가치(RIM)", "업종 상대가치"): 0.787,
        ("수익가치(RIM)", "역사적 밴드"): 0.325,
        ("수익가치(RIM)", "정규화 이익"): -0.015,
        ("업종 상대가치", "역사적 밴드"): 0.229,
        ("업종 상대가치", "정규화 이익"): 0.263,
        ("역사적 밴드", "정규화 이익"): -0.300,
    },
    "US": {
        ("수익가치(RIM)", "업종 상대가치"): 0.706,
        ("수익가치(RIM)", "역사적 밴드"): -0.031,
        ("수익가치(RIM)", "정규화 이익"): 0.723,
        ("업종 상대가치", "역사적 밴드"): 0.260,
        ("업종 상대가치", "정규화 이익"): 0.763,
        ("역사적 밴드", "정규화 이익"): -0.199,
    },
}

# 실질 축이 **가진 축 중 몇 몫인가**가 이 아래면 등급에 상한을 씌운다(ADR-0036).
# 현행 코드가 "방법이 1개뿐이면 무조건 낮음"으로 못박은 규칙(valuation.py)의 연장이다.
#
# ── 왜 절대 개수가 아니라 비율인가 ──────────────────────────────────
# 처음에는 절대 개수였다(`NEFF_LOW, NEFF_MID = 2.0, 2.8`). 그 값은 **축이 넷이던
# 세계에서** 정해졌고, 그때 파일에 이렇게 적혀 있었다:
#
#   "3.0으로 두면 '높음'이 수학적으로 도달 불가다 — 실측 상관에서 가능한 조합의
#    최댓값이 2.98(②역사적밴드 + ③RIM + ⑤정규화이익)이라 3단 배지가 사실상 2단이 된다."
#
# **그 2.98을 만든 것이 ②였다.** ②는 나머지 셋 모두와 독립인 유일한 축이었다(|r|≤0.27).
# ADR-0035가 ②를 판정에서 빼자 도달 가능한 최댓값이 한국 1.775 · 미국 1.219로 내려앉아,
# **문턱 2.0을 아무 조합도 넘지 못하게 됐다.** 실측: 패널 7,656건 전부가 상한 '낮음'.
# 흩어짐은 여전히 세 등급을 가르는데(낮음 46 · 중간 32 · 높음 22%) 상한이 전부 뭉갰다.
#
# 절대 개수는 **축이 몇 개인 세계인지에 따라 뜻이 달라진다** — "2.8개가 독립"은 넷 중
# 2.8이면 보통이고 셋 중 2.8이면 거의 불가능이다.
#
# ── 어느 비율인가: (n_eff − 1) / (n − 1) ────────────────────────────
# `n_eff`는 [1, n] 사이를 움직인다 — 완전히 겹치면 1, 완전히 독립이면 n. 그래서
# **1에서 n까지의 구간을 얼마나 올라왔는가**가 축 수와 무관한 자다:
#
#     독립분 = (n_eff − 1) / (n − 1)      0이면 사실상 한 축 · 1이면 전부 독립
#
# `n_eff / n`을 쓰면 안 된다. 그 값은 축이 둘일 때 **최솟값이 0.5**라, 두 방법이
# 완전히 같은 자여도 절반은 독립인 것처럼 나온다. 실제로 AAPL이 그 모양이고
# (①⑤가 같은 적정 PER을 쓴다, ADR-0015 · n_eff 1.13), ADR-0022가 만들어진 이유가
# 정확히 그 종목이다. 재보니 `n_eff/n` 규칙은 AAPL을 '중간'으로 올려 보냈다 —
# **고치려던 것을 되살리는 규칙은 고른 것이 아니다.**
#
# ── 0.333 · 0.600은 옛 문턱을 그대로 옮긴 값이다 ─────────────────────
# 축이 넷일 때 이 둘은 옛 문턱과 **정확히 같은 자리**다:
#
#     1 + 3 × 0.333 = 2.0 = 옛 NEFF_LOW
#     1 + 3 × 0.600 = 2.8 = 옛 NEFF_MID
#
# 즉 넷이 다 선 종목에서는 답이 바뀌지 않는다. 축이 둘·셋 선 종목에서만 달라지고,
# 그 자리가 바로 절대 개수가 뜻을 잃던 자리다.
#
# ⚠ **'높음'은 여전히 사실상 도달 불가다**(실측 KR 0%). 옛 주석이 그 사실을 알고도
# 값을 골랐다고 밝혔고, 이 변경도 그것을 바꾸지 않는다 — 고치는 것은 '높음이 드물다'가
# 아니라 **'낮음이 100%다'**이다(ADR-0035 뒤 7,656건 전부). 등급이 하나뿐인 배지는
# 아무 말도 하지 않는다. 비율로 바꾸면 낮음 49% · 중간 51%로 ②가 있던 시절의
# 2단 구조가 돌아온다. 상세: ADR-0036.
NEFF_FRAC_LOW, NEFF_FRAC_MID = 0.333, 0.600


def effective_axes(methods, market: str,
                   rho_table: dict | None = None) -> tuple[float, bool]:
    """(실질 축 수, 상한을 걸어도 되는가) — 겹치는 방법을 몇 개로 쳐야 하나 (ADR-0022).

    표본조사의 설계효과 식을 그대로 쓴다::

        n_eff = n / (1 + (n − 1) × ρ̄)

    `ρ̄`가 0이면 `n_eff = n`(전부 독립), 1이면 `1`(사실상 한 축)이다.

    둘째 반환값이 False면 **상한을 걸지 않는다.** 상관을 모르는 쌍이 하나라도 있으면
    그렇게 한다 — 지어낸 상관으로 등급을 깎느니 안 깎는 쪽이 정직하다(ADR-0011).
    EPV처럼 새 축이 들어왔는데 상관을 아직 안 잰 경우가 여기 걸린다.
    """
    ms = sorted(set(methods or []))
    n = len(ms)
    if n <= 1:
        # 쌍이 없다. 현행도 방법이 1개면 무조건 '낮음'이라 동작이 같다.
        return float(n), True
    table = (rho_table if rho_table is not None
             else METHOD_RHO.get((market or "").upper(), {}))
    rhos = []
    for i in range(n):
        for j in range(i + 1, n):
            key = (ms[i], ms[j])
            if key not in table:
                return float(n), False        # 모르는 쌍 → 상한 없음
            rhos.append(table[key])
    # 음의 상관은 0으로 자른다. n_eff > n은 "독립보다 더 독립"이라 뜻이 없다.
    rho_bar = max(0.0, float(np.mean(rhos)))
    return n / (1.0 + (n - 1) * rho_bar), True


def roe_bucket(roe: float | None) -> str | None:
    """ROE를 구간 라벨로. 결측·NaN이면 None."""
    if roe is None:
        return None
    try:
        v = float(roe)
    except (TypeError, ValueError):
        return None
    # 무한대도 결측으로 본다 — 자유 데이터에서 0으로 나눈 값이 이렇게 들어오는데,
    # 이 버킷은 회귀 더미가 되므로 쓰레기가 정상 카테고리로 둔갑하면 계수가 오염된다
    # (serialize.py의 num()도 같은 이유로 inf를 무효로 본다).
    if math.isnan(v) or math.isinf(v):
        return None
    for i in range(len(ROE_EDGES) - 1):
        if ROE_EDGES[i] < v <= ROE_EDGES[i + 1]:
            return ROE_LABELS[i]
    return ROE_LABELS[0]   # 도달 불가(위에서 비유한 값을 모두 걸렀다) — 방어선으로 남긴다


def sector_labels(sectors: pd.Series, min_n: int = SECTOR_MIN_N) -> pd.Series:
    """표본이 min_n 미만인 업종을 '기타'로 묶은 라벨 시리즈."""
    counts = sectors.value_counts()
    keep = set(counts[counts >= min_n].index)
    return sectors.where(sectors.isin(keep), OTHER_SECTOR)


# ── 규모 항의 마디 (ADR-0021) ────────────────────────────────────────
# log(시총)을 **직선 하나로** 두면 중간 구간에서 적합한 기울기가 꼬리까지 연장된다.
# 전 종목 실측(scripts/check_size_functional.py, 5겹 교차검증)에서 최상위 1%의 표본 외
# 평균 잔차가 한국 PBR −0.619 · PER −0.430이었다 — 회귀가 초대형주의 배수를 체계적으로
# **과대추정**하고, 적정배수가 과대라는 것은 그 종목이 '저평가'로 밀린다는 뜻이다.
# 삼성전자 적정 PBR이 17.96배(실제 3.30)였다.
#
# ROE에 대해서는 ADR-0014가 이미 같은 검사를 하고 구간 더미로 바꿨다. **규모에는 그
# 검사를 안 했던 것**이고, 여기서 마디를 둬 기울기가 꺾일 수 있게 한다.
#
# 순수 구간 더미(십분위)는 쓰지 않는다 — 구간 안에 기울기가 없어 **초대형주끼리 구별을
# 못 한다**. 실측에서 AAPL·MSFT·NVDA가 시총 3.6조·4.5조·5.0조 달러인데 적정 PER이
# 셋 다 39.07배로 같았다. 스플라인은 연속이고 구간 안에서도 기울기를 유지한다.
SIZE_KNOT_QS = (0.80, 0.95)

# 마디 위에 이만큼은 남아야 그 마디를 둔다. 얇은 꼬리에 마디를 세우면 몇 종목이
# 기울기를 정하게 된다 — MIN_FIT_SAMPLE(300)에서 0.95 마디는 15곳뿐이라 서지 않는다.
MIN_KNOT_TAIL = 30

# 계수를 만들 최소 표본. 이보다 적으면 만들지 않고 호출부가 피어 중앙값으로 폴백한다.
# 근거: 상위 N종목만으로 적합해 나머지를 예측시키는 실험에서 β가 0.066(N=65) →
# 0.161(N=500) → 0.276(전체)로 단조 증가했다. 표본이 좁으면 규모 계수를 과소추정하고,
# 그 편향이 전부 양수라 소형주를 체계적으로 '저평가'로 밀어 올린다.
MIN_FIT_SAMPLE = 300


def size_knots(lm: np.ndarray) -> list[float]:
    """규모 항의 마디(log 스케일). 위쪽 표본이 얇은 마디는 두지 않는다.

    마디를 못 두면 빈 목록이고, 그때 이 모듈은 **현행(직선 하나)과 정확히 같아진다.**
    표본이 얇을수록 항이 줄어드는 방향이라 안전하다.
    """
    out: list[float] = []
    for q in SIZE_KNOT_QS:
        k = float(np.quantile(lm, q))
        if int((lm > k).sum()) < MIN_KNOT_TAIL:
            continue
        if out and k <= out[-1]:
            continue    # 마디가 겹치면 두 열이 같아져 랭크가 무너진다
        out.append(k)
    return out


def size_term(coef: dict, lm: float) -> float:
    """s(x) = β·x + Σ γₖ·max(0, x − kₖ). 마디가 없으면 현행 β·x와 정확히 같다.

    `warranted_multiple`의 분해와 진단 스크립트가 **같은 함수**를 봐야 한다 —
    두 곳이 각자 계산하면 계수 스키마가 바뀔 때 조용히 어긋난다.
    """
    s = coef["beta_size"] * lm
    for k, g in zip(coef.get("size_knots") or (), coef.get("size_slopes") or ()):
        if lm > k:
            s += g * (lm - k)
    return s


def size_slope(coef: dict, lm: float) -> float:
    """그 규모에서의 **국소** 기울기.

    화면이 "규모 계수 β=0.30 — 시총이 10배면 배수를 2.0배로 봅니다"라고 쓰는데,
    스플라인에서는 β가 하나가 아니다. 그 회사가 선 구간의 기울기를 주어야 그 문장이
    참이 된다.
    """
    b = coef["beta_size"]
    for k, g in zip(coef.get("size_knots") or (), coef.get("size_slopes") or ()):
        if lm > k:
            b += g
    return b


def _design_matrix(d: pd.DataFrame) -> tuple[np.ndarray, list[float], list[str], list[str]]:
    """회귀 설계행렬을 만든다. 열 순서는 [절편, log(시총), 마디들, ROE더미들, 업종더미들]로
    고정되며, 이 순서가 fit_leg의 beta 슬라이싱과 짝이다 — 순서를 바꾸면 그쪽도 바꿔야 한다.

    d는 fit_leg에서 이미 rb(ROE 구간)·sec(업종 라벨) 열을 채운 뒤 넘어온다.
    기준(절편에 흡수되는) ROE 구간·업종은 정렬 순서로 임의로 정해진다. 더미 코딩이라
    기준을 무엇으로 잡아도 예측값은 같으므로 이대로 둔다(계수의 절대값이 아니라 차이만 뜻이 있다).
    """
    lm = np.log(d["mcap"].to_numpy(float))
    knots = size_knots(lm)
    rb_levels = [x for x in ROE_LABELS if x in set(d["rb"].dropna())][1:]
    sec_levels = sorted(set(d["sec"]))[1:]
    cols = [np.ones(len(d)), lm]
    cols += [np.maximum(0.0, lm - k) for k in knots]
    cols += [(d["rb"] == lv).to_numpy(float) for lv in rb_levels]
    cols += [(d["sec"] == lv).to_numpy(float) for lv in sec_levels]
    return np.column_stack(cols), knots, rb_levels, sec_levels


def _prep(df: pd.DataFrame) -> pd.DataFrame | None:
    """적합용 프레임 준비 — 결측·비양수·무한대를 걷고 ROE 구간·업종 라벨을 채운다.

    `fit_leg`와 `loo_leg_error`가 **같은 전처리를 쓰게 하려고** 빼냈다. 갈라지면 측정이
    적합과 다른 표본을 재게 되는데, 그러면 `LEG_MAE`가 실제 오차와 어긋난다.
    """
    d = df[["multiple", "mcap", "sector", "roe"]].copy()
    d["multiple"] = pd.to_numeric(d["multiple"], errors="coerce")
    d["mcap"] = pd.to_numeric(d["mcap"], errors="coerce")
    # 무한대를 반드시 함께 거른다. `inf > 0`이 True라 크기 비교만으로는 통과해 버리는데,
    # log를 씌우면 설계행렬이 오염돼 lstsq가 터지거나(mcap) 계수가 통째로 NaN이 된다
    # (multiple). 후자가 더 나쁘다 — 성공한 것처럼 보이는 계수가 전 종목에 실린다.
    # `> 0` 비교가 NaN을 이미 떨어뜨리므로 별도 dropna는 필요 없다.
    ok = (np.isfinite(d["multiple"]) & (d["multiple"] > 0)
          & np.isfinite(d["mcap"]) & (d["mcap"] > 0))
    d = d[ok]
    if len(d) < MIN_FIT_SAMPLE:
        return None
    d["rb"] = d["roe"].map(roe_bucket)
    # 결측 업종은 명시적으로 '기타'로 보낸다. astype(str)에 맡기면 pandas 2.x에서
    # None이 문자열 "None"이 되어 자기 더미를 갖고, 기타 통합을 우회한다(버전 의존).
    d["sec"] = sector_labels(d["sector"].fillna(OTHER_SECTOR).astype(str))
    return d


def loo_leg_error(df: pd.DataFrame) -> dict | None:
    """leave-one-out 절대오차 — `LEG_MAE`를 만드는 측정 자체다 (ADR-0017·0022).

    **이 함수가 저장소에 있는 이유가 있다.** `LEG_MAE`의 원래 값을 만든 스크립트가 없어서,
    ADR-0014의 β 불일치를 두고 *"그 스크립트가 저장소에 없어 특정하지 못했다"*로 끝난
    적이 있다(HANDOFF.md). 같은 일이 반복되지 않게 측정을 코드로 남긴다.

    재적합을 n번 하지 않고 **PRESS 잔차**로 낸다 — OLS에서 leave-one-out 잔차는
    `e_i / (1 − h_ii)`와 정확히 같다(`h_ii`는 해트 행렬 대각). 근사가 아니라 항등식이다.

    **다만 설계행렬은 전체 표본으로 한 번 정하고 고정한다** — 업종 라벨 통합(min_n),
    ROE 구간, 규모 마디(분위수)는 종목 하나를 빼도 다시 정하지 않는다. 즉 leave-one-out인
    것은 **계수**이지 특징 설계가 아니다. 이 차이를 숨기지 않는다.
    """
    d = _prep(df)
    if d is None:
        return None
    y = np.log(d["multiple"].to_numpy(float))
    X, *_ = _design_matrix(d)
    beta, _res, rank, _sv = np.linalg.lstsq(X, y, rcond=None)
    if rank < X.shape[1]:
        return None
    resid = y - X @ beta
    # h_ii = x_i (XᵀX)⁻¹ x_iᵀ. 랭크 경계에서도 견디게 pinv를 쓴다.
    hat = np.einsum("ij,jk,ik->i", X, np.linalg.pinv(X.T @ X), X)
    # h_ii → 1이면 그 점을 빼는 순간 적합이 무의미해진다(자기 자신이 유일한 근거).
    # 1e-9로 잘라 폭주를 막되, 몇 곳이 그랬는지 함께 돌려준다.
    saturated = int(np.sum(hat > 1 - 1e-6))
    e_loo = resid / (1 - np.clip(hat, 0.0, 1 - 1e-9))
    ss = float(np.sum((y - y.mean()) ** 2))
    return {
        "n": len(d),
        "mae": float(np.mean(np.abs(e_loo))),          # ← LEG_MAE에 넣는 값
        "mae_in_sample": float(np.mean(np.abs(resid))),  # 부풀려진 값 — 대조용
        "r2_loo": float(1 - np.sum(e_loo ** 2) / ss) if ss > 0 else float("nan"),
        "saturated": saturated,
    }


def fit_leg(df: pd.DataFrame, leg: str) -> dict | None:
    """한 다리의 계수를 적합한다. 표본이 모자라면 None.

    df: multiple·mcap·sector·roe 열을 가진 프레임. 결측·비양수 배수는 버린다.
    반환: {leg, intercept, beta_size, roe_coef, sector_coef, n, mcap_min, mcap_max,
           sector_median_mcap, sector_median_roe_coef}
    """
    d = _prep(df)
    if d is None:
        return None
    y = np.log(d["multiple"].to_numpy(float))

    X, knots, rb_levels, sec_levels = _design_matrix(d)
    beta, _res, rank, _sv = np.linalg.lstsq(X, y, rcond=None)
    # 랭크가 모자라면 lstsq는 예외 대신 최소노름 해를 낸다 — 그럴듯하지만 의미 없는
    # 값이다. 계수를 내지 않고 폴백시킨다(ADR-0011: 오염된 값보다 계산 불가가 정직하다).
    if rank < X.shape[1]:
        return None

    off = 2 + len(knots)      # 절편·기울기·마디들 다음이 ROE 더미다
    roe_coef = {lv: 0.0 for lv in ROE_LABELS}
    for lv, b in zip(rb_levels, beta[off:off + len(rb_levels)]):
        roe_coef[lv] = float(b)
    # 기준 업종 = _design_matrix가 더미를 세우지 않은 그 하나(sec_levels에 없는 업종).
    sec_base = next(iter(set(d["sec"]) - set(sec_levels)))
    sector_coef = {sec_base: 0.0}
    for lv, b in zip(sec_levels, beta[off + len(rb_levels):]):
        sector_coef[lv] = float(b)

    # 화면 분해용 기준점 — 업종별 '전형적인' 시총·ROE 효과(ADR-0014 결정 다섯).
    med_mcap = d.groupby("sec")["mcap"].median().to_dict()
    med_roe = (d.assign(c=d["rb"].map(lambda x: roe_coef.get(x, 0.0)))
                 .groupby("sec")["c"].median().to_dict())
    # 예측 시점에 학습에 없던 업종이 들어오면 '기타'로 보낸다. 그런데 학습 표본의 모든
    # 업종이 min_n을 넘겨 '기타'가 한 번도 안 만들어졌을 수 있다 — 그 자리를 비워 두면
    # 낯선 업종의 종목이 통째로 계산 불가가 된다. 효과 0(기준 업종과 같음)으로 채워 둔다.
    sector_coef.setdefault(OTHER_SECTOR, 0.0)
    med_mcap.setdefault(OTHER_SECTOR, float(d["mcap"].median()))
    med_roe.setdefault(OTHER_SECTOR, float(np.median(list(med_roe.values()) or [0.0])))
    return {
        "leg": leg,
        "intercept": float(beta[0]),
        # 첫 마디 아래 구간의 기울기다. 전체 기울기가 아니다 — 그 규모에서의 국소
        # 기울기는 `size_slope()`가 낸다(화면이 쓰는 값).
        "beta_size": float(beta[1]),
        "size_knots": [float(k) for k in knots],
        "size_slopes": [float(b) for b in beta[2:2 + len(knots)]],
        "roe_coef": roe_coef,
        "sector_coef": sector_coef,
        "n": int(len(d)),
        "mcap_min": float(d["mcap"].min()),
        "mcap_max": float(d["mcap"].max()),
        "sector_median_mcap": {k: float(v) for k, v in med_mcap.items()},
        "sector_median_roe_coef": {k: float(v) for k, v in med_roe.items()},
    }


# 학습 시총 하한의 몇 분의 1까지 외삽을 허용하는가. 그 아래는 계산하지 않는다.
# ADR-0011의 원칙("오염된 값보다 '계산 불가'가 정직하다")을 외삽에도 적용한다.
# 근거: 상위 N종목만 학습해 소형주를 예측시키면 편향이 전부 양수였다(+0.2~0.4) —
# 외삽은 소형주를 체계적으로 '저평가'로 밀어 올린다.
EXTRAPOLATION_LIMIT = 5.0

# fit_leg이 반드시 채우는 키들. 계수는 24시간 JSON 캐시를 거쳐 오므로, 스키마가 바뀐
# 뒤 남아 있는 옛 캐시가 얕은 검증(`"pbr" in d`)을 통과해 들어올 수 있다. 그때 예외로
# 판정을 무너뜨리는 대신 '계산 불가'로 떨어뜨린다(ADR-0011).
# 공개 이름인 것은 모듈 간 계약이기 때문이다 — `universe_multiples`가 캐시를 읽을 때
# 같은 기준으로 검증한다. 두 곳이 각자 키 목록을 갖고 있으면 언젠가 어긋난다.
REQUIRED_COEF_KEYS = frozenset((
    "intercept", "beta_size", "size_knots", "size_slopes", "roe_coef",
    "sector_coef", "n", "mcap_min", "sector_median_mcap", "sector_median_roe_coef"))


def warranted_multiple(coef: dict | None, mcap: float | None,
                       sector: str | None, roe: float | None) -> dict:
    """적정 배수와 그 분해. 계수가 없거나 규모가 학습 범위를 크게 벗어나면 multiple=None.

    분해는 **곱셈으로 정확히 복원**된다(로그 선형이므로):
        multiple = sector_base × (1 + size_adj) × (1 + roe_adj)
    화면(Task 9)이 이 셋을 그대로 풀어 쓴다.
    """
    blank = {"multiple": None, "sector_base": None, "size_adj": None, "roe_adj": None,
             "sector_used": None, "below_range": False, "too_small": False,
             "beta_size": None, "n": None}
    if not isinstance(coef, dict) or not REQUIRED_COEF_KEYS <= coef.keys():
        return blank
    try:
        mcap = float(mcap)
    except (TypeError, ValueError):
        return blank
    if not mcap or mcap <= 0 or not math.isfinite(mcap):
        return blank

    # fit_leg이 sector_coef에 OTHER_SECTOR를 항상 채우므로(그리고 위에서 스키마를
    # 확인했으므로) 이 조회는 반드시 성공한다.
    sec = sector if sector in coef["sector_coef"] else OTHER_SECTOR
    too_small = mcap < coef["mcap_min"] / EXTRAPOLATION_LIMIT
    if too_small:
        # 하한의 1/5 미만이면 하한 미만인 것도 당연히 참이다 — 두 값은 같은 축 위에 있다
        return {**blank, "too_small": True, "below_range": True, "sector_used": sec,
                "beta_size": coef["beta_size"], "n": coef["n"]}

    try:
        base_mcap = coef["sector_median_mcap"].get(sec) or mcap
        base_rc = coef["sector_median_roe_coef"].get(sec, 0.0)
        # ROE를 모르면 **조정하지 않는다** — 기준값을 그대로 써서 roe_adj가 정확히 0이 된다.
        # 0.0을 넣으면 '기준 구간과 같다'는 판단을 한 셈이 되는데, 우리는 그걸 모른다.
        # 규모는 항상 알므로 시총 조정은 그대로 적용된다.
        rb = roe_bucket(roe)
        rc = coef["roe_coef"].get(rb, base_rc) if rb else base_rc
        # 규모 항만 스플라인이다(ADR-0021). 마디가 없으면 s(x) = β·x라 현행과 같고,
        # 아래 곱셈 분해는 어느 쪽이든 **정확히** 복원된다 — 화면(ADR-0014 결정 다섯)이
        # sector_base × (1+size_adj) × (1+roe_adj)로 되풀어 쓰기 때문이다.
        s_self = size_term(coef, math.log(mcap))
        s_base = size_term(coef, math.log(base_mcap))
        fitted = coef["intercept"] + s_self + rc + coef["sector_coef"][sec]
        base = math.exp(coef["intercept"] + s_base + base_rc + coef["sector_coef"][sec])
        multiple = math.exp(fitted)
        size_adj = math.exp(s_self - s_base) - 1
        roe_adj = math.exp(rc - base_rc) - 1
        local_beta = size_slope(coef, math.log(mcap))
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        # 손상된 계수(음수 기준 시총, 발산하는 β 등) — 값을 지어내지 않고 물러난다
        return blank
    if not all(math.isfinite(v) for v in (multiple, base, size_adj, roe_adj, local_beta)):
        return blank

    return {
        "multiple": multiple,
        "sector_base": base,
        "size_adj": size_adj,
        "roe_adj": roe_adj,
        "sector_used": sec,
        "below_range": mcap < coef["mcap_min"],
        "too_small": False,
        # **이 회사 규모 구간의 기울기**다(coef["beta_size"]가 아니다). 화면이 이 값으로
        # "시총이 10배면 배수를 N배로 봅니다"를 쓰는데, 스플라인에서 그 문장이 참이려면
        # 전역 기울기가 아니라 국소 기울기여야 한다.
        "beta_size": local_beta,
        "n": coef["n"],
    }
