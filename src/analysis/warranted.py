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
    if math.isnan(v):
        return None
    for i in range(len(ROE_EDGES) - 1):
        if ROE_EDGES[i] < v <= ROE_EDGES[i + 1]:
            return ROE_LABELS[i]
    return ROE_LABELS[0]


def sector_labels(sectors: pd.Series, min_n: int = SECTOR_MIN_N) -> pd.Series:
    """표본이 min_n 미만인 업종을 '기타'로 묶은 라벨 시리즈."""
    counts = sectors.value_counts()
    keep = set(counts[counts >= min_n].index)
    return sectors.where(sectors.isin(keep), OTHER_SECTOR)
