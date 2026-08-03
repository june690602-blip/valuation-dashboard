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


# 계수를 만들 최소 표본. 이보다 적으면 만들지 않고 호출부가 피어 중앙값으로 폴백한다.
# 근거: 상위 N종목만으로 적합해 나머지를 예측시키는 실험에서 β가 0.066(N=65) →
# 0.161(N=500) → 0.276(전체)로 단조 증가했다. 표본이 좁으면 규모 계수를 과소추정하고,
# 그 편향이 전부 양수라 소형주를 체계적으로 '저평가'로 밀어 올린다.
MIN_FIT_SAMPLE = 300


def fit_leg(df: pd.DataFrame, leg: str) -> dict | None:
    """한 다리의 계수를 적합한다. 표본이 모자라면 None.

    df: multiple·mcap·sector·roe 열을 가진 프레임. 결측·비양수 배수는 버린다.
    반환: {leg, intercept, beta_size, roe_coef, sector_coef, n, mcap_min, mcap_max,
           sector_median_mcap, sector_median_roe_coef}
    """
    d = df[["multiple", "mcap", "sector", "roe"]].copy()
    d["multiple"] = pd.to_numeric(d["multiple"], errors="coerce")
    d["mcap"] = pd.to_numeric(d["mcap"], errors="coerce")
    d = d[(d["multiple"] > 0) & (d["mcap"] > 0)].dropna(subset=["multiple", "mcap"])
    if len(d) < MIN_FIT_SAMPLE:
        return None

    d["rb"] = d["roe"].map(roe_bucket)
    d["sec"] = sector_labels(d["sector"].astype(str))
    y = np.log(d["multiple"].to_numpy(float))
    lm = np.log(d["mcap"].to_numpy(float))

    rb_levels = [x for x in ROE_LABELS if x in set(d["rb"].dropna())][1:]
    sec_levels = sorted(set(d["sec"]))[1:]
    cols = [np.ones(len(d)), lm]
    cols += [(d["rb"] == lv).to_numpy(float) for lv in rb_levels]
    cols += [(d["sec"] == lv).to_numpy(float) for lv in sec_levels]
    beta, *_ = np.linalg.lstsq(np.column_stack(cols), y, rcond=None)

    roe_coef = {lv: 0.0 for lv in ROE_LABELS}
    for lv, b in zip(rb_levels, beta[2:2 + len(rb_levels)]):
        roe_coef[lv] = float(b)
    sector_coef = {sorted(set(d["sec"]))[0]: 0.0}
    for lv, b in zip(sec_levels, beta[2 + len(rb_levels):]):
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
        "beta_size": float(beta[1]),
        "roe_coef": roe_coef,
        "sector_coef": sector_coef,
        "n": int(len(d)),
        "mcap_min": float(d["mcap"].min()),
        "mcap_max": float(d["mcap"].max()),
        "sector_median_mcap": {k: float(v) for k, v in med_mcap.items()},
        "sector_median_roe_coef": {k: float(v) for k, v in med_roe.items()},
    }
