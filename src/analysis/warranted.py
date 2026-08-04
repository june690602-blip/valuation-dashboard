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
# ADR-0014의 leave-one-out 실측을 **그대로 옮긴 상수**다. 여기서 추정하는 값이 아니라서
# 계수를 다시 학습해도 따라 바뀌지 않는다 — ADR-0014를 다시 재면 이 표도 손으로 고쳐야 한다.
#
# 이 값이 재는 것은 '적정가가 맞았나'가 **아니라** '회귀가 그 종목의 시장 배수를 얼마나
# 맞히나'다. 적정가의 정확도는 원리적으로 못 잰다(ADR-0009). 그럼에도 화면에 내는 이유는,
# 적정 배수가 이만큼 흔들리면 적정가도 **최소** 그만큼 흔들리기 때문이다.
#
# **미측정 다리는 넣지 않는다.** 미국 PSR·EV/EBITDA는 ADR-0014가 재지 않았고, 한국 값을
# 대신 쓰는 것은 지어내는 것이다(ADR-0011: 오염된 값보다 '없음').
LEG_MAE = {
    "KR": {"pbr": 0.563, "per": 0.572, "psr": 0.921, "ev_ebitda": 0.639},
    "US": {"pbr": 0.470, "per": 0.374},
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


# ── 규모 항의 마디 (ADR-0020) ────────────────────────────────────────
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


def fit_leg(df: pd.DataFrame, leg: str) -> dict | None:
    """한 다리의 계수를 적합한다. 표본이 모자라면 None.

    df: multiple·mcap·sector·roe 열을 가진 프레임. 결측·비양수 배수는 버린다.
    반환: {leg, intercept, beta_size, roe_coef, sector_coef, n, mcap_min, mcap_max,
           sector_median_mcap, sector_median_roe_coef}
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
        # 규모 항만 스플라인이다(ADR-0020). 마디가 없으면 s(x) = β·x라 현행과 같고,
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
