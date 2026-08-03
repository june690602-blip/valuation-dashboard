# 회귀 기반 ① 업종 상대가치 (ADR-0014) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ①의 적정 배수를 피어 중앙값이 아니라 업종·규모·수익성 회귀의 적합값으로 구한다.

**Architecture:** 순수 함수 모듈 `src/analysis/warranted.py`가 회귀 적합·예측·분해를 담당하고,
`src/data/universe_multiples.py`가 전 종목 배수 스냅숏을 모아 **계수만** 24시간 캐시한다.
`valuation.py::_relative_value()`는 계수가 있으면 회귀로, 없으면 기존 피어 중앙값으로 간다.
계수는 다리(PBR·PER·PSR·EV/EBITDA)마다 따로 적합한다.

**Tech Stack:** Python 3.14, numpy(최소제곱), pandas, 기존 `file_cache`, unittest+pytest

**전제:** 브랜치 `fix/peer-selection-by-size`에서 작업한다(ADR-0013·0014 문서가 이미 커밋됨).

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/analysis/warranted.py` (신설) | 회귀 적합·예측·분해. **순수 함수**, 네트워크 없음 |
| `src/data/universe_multiples.py` (신설) | 전 종목 배수 수집 + 계수 캐시. 네트워크 담당 |
| `src/data/universe.py` (수정) | `get_sp1500()` 추가 — S&P 400·600 목록 |
| `src/analysis/valuation.py` (수정) | `_relative_value()`가 계수를 쓰도록. 폴백 유지 |
| `src/web/serialize.py` (수정) | 분해 내역을 프런트로 |
| `web/assets/stock.js` (수정) | `fold()`로 접어서 표시 |
| `scripts/check_warranted.py` (신설) | 회귀 대 중앙값 비교 진단 |
| `tests/test_warranted.py` (신설) | 순수 함수 회귀 테스트 |

---

## Task 1: ROE 구간·업종 더미를 만드는 설계행렬

**Files:**
- Create: `src/analysis/warranted.py`
- Test: `tests/test_warranted.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_warranted.py`:

```python
"""회귀 기반 적정 배수(ADR-0014) 순수 함수 테스트."""
from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from src.analysis.warranted import ROE_EDGES, roe_bucket, sector_labels


class BucketTests(unittest.TestCase):
    def test_roe_bucket_edges(self):
        # ADR-0014: ROE와 배수는 U자라 반드시 구간 더미로 넣는다
        self.assertEqual(roe_bucket(-0.50), "≤-20%")
        self.assertEqual(roe_bucket(-0.20), "≤-20%")     # 경계는 아래 구간에 넣는다
        self.assertEqual(roe_bucket(-0.10), "-20~-5%")
        self.assertEqual(roe_bucket(-0.01), "-5~0%")
        self.assertEqual(roe_bucket(0.03), "0~5%")
        self.assertEqual(roe_bucket(0.12), "10~15%")
        self.assertEqual(roe_bucket(0.40), ">15%")

    def test_roe_bucket_missing_is_none(self):
        self.assertIsNone(roe_bucket(None))
        self.assertIsNone(roe_bucket(float("nan")))

    def test_roe_edges_are_seven_buckets(self):
        self.assertEqual(len(ROE_EDGES) - 1, 7)

    def test_sector_labels_pools_thin_sectors(self):
        # 표본 10곳 미만 업종은 '기타'로 묶는다 — 셀당 표본이 얇으면 더미가 불안정하다
        s = pd.Series(["반도체"] * 12 + ["조선"] * 3 + ["제약"] * 10)
        out = sector_labels(s, min_n=10)
        self.assertEqual(out.tolist().count("반도체"), 12)
        self.assertEqual(out.tolist().count("제약"), 10)
        self.assertEqual(out.tolist().count("기타"), 3)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.analysis.warranted'`

- [ ] **Step 3: 최소 구현**

`src/analysis/warranted.py`:

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/analysis/warranted.py tests/test_warranted.py
git commit -m "feat(valuation): 적정 배수 회귀의 ROE 구간·업종 더미 규칙 (ADR-0014)"
```

---

## Task 2: 계수 적합 (`fit_leg`)

**Files:**
- Modify: `src/analysis/warranted.py`
- Test: `tests/test_warranted.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_warranted.py`에 추가(파일 상단 import에 `fit_leg`, `MIN_FIT_SAMPLE` 추가):

```python
from src.analysis.warranted import (MIN_FIT_SAMPLE, ROE_EDGES, fit_leg,
                                    roe_bucket, sector_labels)


def _synthetic(n=600, beta=0.30, seed=0):
    """log(배수) = -6 + 0.30·log(시총) + 업종효과 인 합성 데이터."""
    rng = np.random.default_rng(seed)
    mcap = np.exp(rng.uniform(np.log(1e10), np.log(1e13), n))
    sector = rng.choice(["A", "B", "C"], n)
    eff = {"A": 0.0, "B": 0.5, "C": -0.4}
    roe = rng.uniform(0.0, 0.20, n)
    y = -6.0 + beta * np.log(mcap) + np.array([eff[s] for s in sector])
    return pd.DataFrame({"multiple": np.exp(y), "mcap": mcap,
                         "sector": sector, "roe": roe})


class FitTests(unittest.TestCase):
    def test_recovers_known_size_coefficient(self):
        coef = fit_leg(_synthetic(), leg="pbr")
        self.assertIsNotNone(coef)
        self.assertAlmostEqual(coef["beta_size"], 0.30, places=2)

    def test_records_training_range_and_sample(self):
        df = _synthetic()
        coef = fit_leg(df, leg="pbr")
        self.assertEqual(coef["n"], len(df))
        self.assertAlmostEqual(coef["mcap_min"], df["mcap"].min(), places=0)
        self.assertAlmostEqual(coef["mcap_max"], df["mcap"].max(), places=0)
        self.assertEqual(coef["leg"], "pbr")

    def test_returns_none_when_sample_too_small(self):
        # 표본이 얇으면 계수를 만들지 않는다 — 만들면 과적합이다.
        # (실측: 상위 65종목만으로 적합하면 β가 0.066으로 4배 작게 나왔다)
        self.assertIsNone(fit_leg(_synthetic(n=MIN_FIT_SAMPLE - 1), leg="pbr"))

    def test_drops_nonpositive_and_nan_multiples(self):
        df = _synthetic(n=MIN_FIT_SAMPLE + 50)
        df.loc[df.index[:10], "multiple"] = 0.0
        df.loc[df.index[10:20], "multiple"] = np.nan
        coef = fit_leg(df, leg="pbr")
        self.assertEqual(coef["n"], len(df) - 20)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py -q`
Expected: FAIL — `ImportError: cannot import name 'fit_leg'`

- [ ] **Step 3: 구현**

`src/analysis/warranted.py`에 추가:

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/analysis/warranted.py tests/test_warranted.py
git commit -m "feat(valuation): 다리별 적정 배수 계수를 적합한다 (ADR-0014)"
```

---

## Task 3: 예측과 분해 (`warranted_multiple`)

**Files:**
- Modify: `src/analysis/warranted.py`
- Test: `tests/test_warranted.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

import에 `EXTRAPOLATION_LIMIT`, `warranted_multiple` 추가 후:

```python
class PredictTests(unittest.TestCase):
    def setUp(self):
        self.coef = fit_leg(_synthetic(), leg="pbr")

    def test_prediction_matches_generating_process(self):
        # 합성 데이터의 참값: log(배수) = -6 + 0.30·log(시총) + 업종효과(B=+0.5)
        mcap = 1e11
        out = warranted_multiple(self.coef, mcap=mcap, sector="B", roe=0.10)
        expected = math.exp(-6.0 + 0.30 * math.log(mcap) + 0.5)
        self.assertAlmostEqual(out["multiple"], expected, delta=expected * 0.02)

    def test_decomposition_multiplies_back_to_multiple(self):
        # 화면에 '업종기준 × 시총조정 × ROE조정'으로 풀어 쓰므로 정확히 복원돼야 한다
        out = warranted_multiple(self.coef, mcap=5e10, sector="A", roe=0.08)
        recomposed = (out["sector_base"]
                      * (1 + out["size_adj"])
                      * (1 + out["roe_adj"]))
        self.assertAlmostEqual(recomposed, out["multiple"], delta=out["multiple"] * 1e-6)

    def test_below_training_range_is_flagged(self):
        out = warranted_multiple(self.coef, mcap=self.coef["mcap_min"] / 2,
                                 sector="A", roe=0.08)
        self.assertTrue(out["below_range"])
        self.assertFalse(out["too_small"])

    def test_far_below_training_range_is_unusable(self):
        # 학습 하한의 1/EXTRAPOLATION_LIMIT 미만이면 쓰지 않는다
        out = warranted_multiple(self.coef,
                                 mcap=self.coef["mcap_min"] / (EXTRAPOLATION_LIMIT + 1),
                                 sector="A", roe=0.08)
        self.assertTrue(out["too_small"])
        self.assertIsNone(out["multiple"])

    def test_unknown_sector_falls_back_to_other(self):
        out = warranted_multiple(self.coef, mcap=5e10, sector="없는업종", roe=0.08)
        self.assertIsNotNone(out["multiple"])
        self.assertEqual(out["sector_used"], OTHER_SECTOR)
```

`OTHER_SECTOR`도 import에 추가한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py -q`
Expected: FAIL — `ImportError: cannot import name 'warranted_multiple'`

- [ ] **Step 3: 구현**

```python
# 학습 시총 하한의 몇 분의 1까지 외삽을 허용하는가. 그 아래는 계산하지 않는다.
# ADR-0011의 원칙("오염된 값보다 '계산 불가'가 정직하다")을 외삽에도 적용한다.
EXTRAPOLATION_LIMIT = 5.0


def warranted_multiple(coef: dict | None, mcap: float | None,
                       sector: str | None, roe: float | None) -> dict:
    """적정 배수와 그 분해. 계수가 없거나 규모가 학습 범위를 크게 벗어나면 multiple=None.

    분해는 **곱셈으로 정확히 복원**된다(로그 선형이므로):
        multiple = sector_base × (1 + size_adj) × (1 + roe_adj)
    """
    blank = {"multiple": None, "sector_base": None, "size_adj": None, "roe_adj": None,
             "sector_used": None, "below_range": False, "too_small": False,
             "beta_size": None, "n": None}
    if not coef or not mcap or mcap <= 0:
        return blank

    sec = sector if sector in coef["sector_coef"] else OTHER_SECTOR
    if sec not in coef["sector_coef"]:
        return blank
    too_small = mcap < coef["mcap_min"] / EXTRAPOLATION_LIMIT
    if too_small:
        return {**blank, "too_small": True, "sector_used": sec,
                "beta_size": coef["beta_size"], "n": coef["n"]}

    base_mcap = coef["sector_median_mcap"].get(sec) or mcap
    base_rc = coef["sector_median_roe_coef"].get(sec, 0.0)
    # ROE를 모르면 **조정하지 않는다** — 기준값을 그대로 써서 roe_adj가 정확히 0이 된다.
    # 0.0을 넣으면 '기준 구간과 같다'는 판단을 한 셈이 되는데, 우리는 그걸 모른다.
    # 규모는 항상 알므로 시총 조정은 그대로 적용된다.
    rb = roe_bucket(roe)
    rc = coef["roe_coef"].get(rb, base_rc) if rb else base_rc
    fitted = (coef["intercept"] + coef["beta_size"] * math.log(mcap)
              + rc + coef["sector_coef"][sec])

    base = math.exp(coef["intercept"] + coef["beta_size"] * math.log(base_mcap)
                    + base_rc + coef["sector_coef"][sec])
    return {
        "multiple": math.exp(fitted),
        "sector_base": base,
        "size_adj": math.exp(coef["beta_size"] * (math.log(mcap) - math.log(base_mcap))) - 1,
        "roe_adj": math.exp(rc - base_rc) - 1,
        "sector_used": sec,
        "below_range": mcap < coef["mcap_min"],
        "too_small": False,
        "beta_size": coef["beta_size"],
        "n": coef["n"],
    }
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py -q`
Expected: PASS (13 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/analysis/warranted.py tests/test_warranted.py
git commit -m "feat(valuation): 적정 배수 예측과 화면용 계수 분해 (ADR-0014)"
```

---

## Task 4: 미국 유니버스를 S&P 1500으로 넓힌다

**Files:**
- Modify: `src/data/universe.py` (`get_sp500()` 아래에 추가)
- Test: `tests/test_warranted.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_warranted.py`에 추가:

```python
from unittest.mock import patch


class Sp1500Tests(unittest.TestCase):
    def test_sp1500_concatenates_three_indices_without_duplicates(self):
        from src.data import universe

        def fake(url_key):
            return pd.DataFrame({
                "Symbol": {"500": ["AAPL", "MSFT"], "400": ["AAON"],
                           "600": ["AAON", "XPEL"]}[url_key],
                "Sector": ["Tech"] * len({"500": 2, "400": 1, "600": 2}[url_key]),
                "SubIndustry": ["X"] * {"500": 2, "400": 1, "600": 2}[url_key],
            })

        with patch.object(universe, "get_sp500", lambda: fake("500")), \
             patch.object(universe, "_wiki_index_table",
                          lambda url: fake("400" if "400" in url else "600")):
            out = universe.get_sp1500()
        self.assertEqual(sorted(out["Symbol"]), ["AAON", "AAPL", "MSFT", "XPEL"])
        self.assertEqual(len(out), 4)   # AAON 중복 제거
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py::Sp1500Tests -q`
Expected: FAIL — `AttributeError: module 'src.data.universe' has no attribute 'get_sp1500'`

- [ ] **Step 3: 구현**

`src/data/universe.py`의 `get_sp500()` 정의 **아래**에 추가:

```python
SP400_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
SP600_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"


def _wiki_index_table(url: str) -> pd.DataFrame:
    """위키백과 지수 구성종목 표 → Symbol·Sector·SubIndustry."""
    import io

    import requests

    resp = requests.get(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=30)
    resp.raise_for_status()
    for t in pd.read_html(io.StringIO(resp.text)):
        cols = [str(c) for c in t.columns]
        if "Symbol" in cols and "GICS Sector" in cols:
            out = t[["Symbol", "GICS Sector", "GICS Sub-Industry"]].copy()
            out.columns = ["Symbol", "Sector", "SubIndustry"]
            out["Symbol"] = out["Symbol"].astype(str).str.replace(".", "-", regex=False)
            return out
    raise RuntimeError(f"구성종목 표를 찾지 못했습니다: {url}")


@file_cache("sp1500", ttl_hours=24 * 7)
def get_sp1500() -> pd.DataFrame:
    """S&P 500 + 400 MidCap + 600 SmallCap = 약 1,500종목.

    ①의 회귀(ADR-0014)가 학습 표본으로 쓴다. S&P 500만 쓰면 시총 하한이 $7.0B이라
    그보다 작은 종목이 전부 외삽이 되는데, 한국 전 종목 실측에서 **대형주만으로 학습한
    계수는 규모 효과를 크게 과소추정**했다(β 0.066 vs 전 구간 0.276). 400·600을 더하면
    하한이 $0.54B로 내려가 외삽 구간이 대부분 사라진다.

    세 목록 모두 같은 위키백과 표 스키마라 새 의존성이 없다.
    """
    frames = [get_sp500()[["Symbol", "Sector", "SubIndustry"]]]
    for url in (SP400_URL, SP600_URL):
        try:
            frames.append(_wiki_index_table(url))
        except Exception:
            continue   # 한 지수를 못 받아도 나머지로 진행한다
    return pd.concat(frames, ignore_index=True).drop_duplicates("Symbol")
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py::Sp1500Tests -q`
Expected: PASS

- [ ] **Step 5: 실제 네트워크로 한 번 확인한다**

Run: `.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from src.data.universe import get_sp1500; d=get_sp1500(); print(len(d), d['Sector'].nunique())"`
Expected: `1500` 내외, `11`

- [ ] **Step 6: 커밋**

```bash
git add src/data/universe.py tests/test_warranted.py
git commit -m "feat(data): 미국 유니버스를 S&P 1500으로 넓힌다 (ADR-0014)"
```

---

## Task 5: 전 종목 배수 스냅숏 수집

**Files:**
- Create: `src/data/universe_multiples.py`

- [ ] **Step 1: 구현** (네트워크 함수라 단위 테스트 대신 Task 6에서 계수 캐시로 검증한다)

`src/data/universe_multiples.py`:

```python
"""전 종목 배수 스냅숏 — ①의 회귀 계수(ADR-0014)를 만들 학습 표본.

한국은 두 원천이 **정확히 상보적**이다:
    네이버   → per·pbr·roe   (yfinance는 한국 종목의 per·pbr을 주지 않는다: 실측 0%)
    yfinance → psr·ev_ebitda (네이버에 없다)
미국은 yfinance 하나로 넷 다 나온다.

yfinance 전 종목 수집은 불안정하다 — 한국 2,688종목 시도에서 1,655종목(62%)만 성공했고
나머지는 401 Invalid Crumb(레이트리밋)이었다. 그래서 **부분 수집을 정상으로 취급**하고,
표본이 모자라는 다리만 계수를 만들지 않는다(호출부가 피어 중앙값으로 폴백한다).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

UNIVERSE_WORKERS = 12


def _num(v):
    x = pd.to_numeric(v, errors="coerce")
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else x


def collect_kr() -> pd.DataFrame:
    """한국 보통주 전 종목의 per·pbr·psr·ev_ebitda·roe·mcap·sector."""
    from .base import fetch_info_metrics
    from .naver import fetch_naver_fundamental
    from .universe import get_kr_listing, yahoo_ticker_kr

    listing = get_kr_listing()
    pool = listing[listing["is_common"] & listing["Sector"].notna()
                   & (listing["Marcap"] > 0)]

    def one(row):
        base = {"code": row.Code, "sector": row.Sector, "mcap": float(row.Marcap),
                "per": None, "pbr": None, "psr": None, "ev_ebitda": None, "roe": None}
        try:
            nv = fetch_naver_fundamental(row.Code)
            base.update(per=_num(nv.get("per")), pbr=_num(nv.get("pbr")),
                        roe=_num(nv.get("roe_approx")))
        except Exception:
            pass
        try:
            yv = fetch_info_metrics(yahoo_ticker_kr(row.Code, row.Market))
            base.update(psr=_num(yv.get("psr")), ev_ebitda=_num(yv.get("ev_ebitda")))
        except Exception:
            pass
        return base

    with ThreadPoolExecutor(UNIVERSE_WORKERS) as ex:
        rows = list(ex.map(one, list(pool.itertuples())))
    return pd.DataFrame(rows)


def collect_us() -> pd.DataFrame:
    """S&P 1500의 per·pbr·psr·ev_ebitda·roe·mcap·sector."""
    from .base import fetch_info_metrics
    from .universe import get_sp1500

    def one(row):
        base = {"code": row.Symbol, "sector": row.Sector, "mcap": None,
                "per": None, "pbr": None, "psr": None, "ev_ebitda": None, "roe": None}
        try:
            v = fetch_info_metrics(row.Symbol)
            base.update(mcap=_num(v.get("market_cap")), per=_num(v.get("per")),
                        pbr=_num(v.get("pbr")), psr=_num(v.get("psr")),
                        ev_ebitda=_num(v.get("ev_ebitda")), roe=_num(v.get("roe")))
        except Exception:
            pass
        return base

    with ThreadPoolExecutor(UNIVERSE_WORKERS) as ex:
        rows = list(ex.map(one, list(get_sp1500().itertuples())))
    return pd.DataFrame(rows)
```

- [ ] **Step 2: 문법 확인**

Run: `.venv/Scripts/python.exe -c "import py_compile; py_compile.compile('src/data/universe_multiples.py', doraise=True); print('ok')"`
Expected: `ok`

- [ ] **Step 3: 실제로 한국 전 종목을 한 번 받아본다** (약 4분)

Run:
```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from src.data.universe_multiples import collect_kr; d=collect_kr(); print(len(d), d[['per','pbr','psr','ev_ebitda']].notna().sum().to_dict())"
```
Expected: 2,600대 행. `per`·`pbr`이 1,500 이상, `psr`·`ev_ebitda`가 900 이상.
(레이트리밋으로 `psr`·`ev_ebitda`가 더 낮게 나올 수 있다 — 정상이다.)

- [ ] **Step 4: 커밋**

```bash
git add src/data/universe_multiples.py
git commit -m "feat(data): 전 종목 배수 스냅숏 수집 (ADR-0014)"
```

---

## Task 6: 계수 캐시

**Files:**
- Modify: `src/data/universe_multiples.py`
- Test: `tests/test_warranted.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
class CoefficientTableTests(unittest.TestCase):
    def test_builds_only_legs_with_enough_sample(self):
        from src.data.universe_multiples import build_coefficients

        base = _synthetic(n=MIN_FIT_SAMPLE + 100)
        df = pd.DataFrame({
            "mcap": base["mcap"], "sector": base["sector"], "roe": base["roe"],
            "pbr": base["multiple"],
            "per": base["multiple"] * 10,
            "psr": [np.nan] * len(base),                       # 전부 결측 → 계수 없음
            "ev_ebitda": ([np.nan] * (len(base) - 50)
                          + list(base["multiple"][:50])),      # 50개뿐 → 계수 없음
        })
        out = build_coefficients(df)
        self.assertIn("pbr", out)
        self.assertIn("per", out)
        self.assertNotIn("psr", out)
        self.assertNotIn("ev_ebitda", out)
        self.assertAlmostEqual(out["pbr"]["beta_size"], 0.30, places=2)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py::CoefficientTableTests -q`
Expected: FAIL — `ImportError: cannot import name 'build_coefficients'`

- [ ] **Step 3: 구현** — `src/data/universe_multiples.py`에 추가

```python
LEGS = ("pbr", "per", "psr", "ev_ebitda")

# 다리별 유효 범위. 무료 데이터에는 배수가 터무니없이 큰 값이 섞이는데(적자 직전 EPS 등)
# 로그를 취하면 그 하나가 계수를 끌고 간다. 상·하한은 실측 분포를 보고 정한 판단값이다.
LEG_BOUNDS = {"pbr": (0.05, 20.0), "per": (1.0, 200.0),
              "psr": (0.02, 50.0), "ev_ebitda": (0.5, 100.0)}


def build_coefficients(snapshot: pd.DataFrame) -> dict:
    """스냅숏 → {다리: 계수}. 표본이 모자라는 다리는 넣지 않는다."""
    from ..analysis.warranted import fit_leg

    out = {}
    for leg in LEGS:
        if leg not in snapshot.columns:
            continue
        lo, hi = LEG_BOUNDS[leg]
        v = pd.to_numeric(snapshot[leg], errors="coerce")
        d = snapshot.assign(multiple=v)[(v > lo) & (v < hi)]
        coef = fit_leg(d, leg=leg)
        if coef:
            out[leg] = coef
    return out


@file_cache("warranted_coef_v1", ttl_hours=24,
            validate=lambda d: isinstance(d, dict) and "pbr" in d)
def get_coefficients(market: str) -> dict:
    """시장의 다리별 계수 (24시간 캐시).

    캐시하는 것은 **전 종목 테이블이 아니라 계수**다(숫자 약 70개). 적정 배수는
    `계수 × 자사 값`인데 계수는 시장 구조라 느리게 변하고 자사 시총·ROE는 매일 변한다.
    계수만 저장하면 어제 계수를 써도 오늘 주가 변동이 그대로 반영된다.

    file_cache가 원천 실패 시 만료된 캐시를 반환하므로(stale-ok) 장애에도 판정이 멈추지
    않는다. 'pbr' 계수조차 없으면 유효하지 않은 결과로 보고 캐시하지 않는다.
    """
    snap = collect_kr() if market.upper() == "KR" else collect_us()
    return build_coefficients(snap)
```

파일 상단 import에 `from .cache import file_cache`를 추가한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py -q`
Expected: PASS (15 tests)

- [ ] **Step 5: 실제 계수를 한 번 만들어 본다**

Run:
```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from src.data.universe_multiples import get_coefficients; c=get_coefficients('KR'); print({k:(round(v['beta_size'],3), v['n']) for k,v in c.items()})"
```
Expected: `pbr`·`per`가 반드시 있고 `beta_size`가 0.1~0.4 사이. 약 4분 소요.

- [ ] **Step 6: 커밋**

```bash
git add src/data/universe_multiples.py tests/test_warranted.py
git commit -m "feat(data): 다리별 회귀 계수를 24시간 캐시한다 (ADR-0014)"
```

---

## Task 7: `_relative_value`를 회귀로 바꾸고 폴백을 남긴다

**Files:**
- Modify: `src/analysis/valuation.py:187-234` (`_rel_fairs`·`_relative_value`)
- Modify: `src/analysis/valuation.py:120-186` (`ValuationResult`에 필드 추가)
- Test: `tests/test_warranted.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
class RelativeValueTests(unittest.TestCase):
    def test_warranted_fairs_uses_regression_not_peers(self):
        from src.analysis.valuation import _warranted_fairs

        coef = {"pbr": fit_leg(_synthetic(), leg="pbr")}
        fairs, used, parts = _warranted_fairs(
            coef, mcap=1e11, sector="B", roe=0.10,
            eps=None, bps=1000.0, ebitda_ps=None, debt_ps=0.0, cash_ps=0.0,
            revenue_ps=None, is_loss=True, is_financial=False)
        self.assertEqual(len(fairs), 1)
        m = math.exp(-6.0 + 0.30 * math.log(1e11) + 0.5)
        self.assertAlmostEqual(fairs[0], m * 1000.0, delta=m * 1000.0 * 0.02)
        self.assertTrue(used[0].startswith("PBR"))
        self.assertEqual(parts[0]["leg"], "pbr")

    def test_no_coefficients_yields_no_fairs(self):
        from src.analysis.valuation import _warranted_fairs

        fairs, used, parts = _warranted_fairs(
            {}, mcap=1e11, sector="B", roe=0.10, eps=100.0, bps=1000.0,
            ebitda_ps=None, debt_ps=0.0, cash_ps=0.0, revenue_ps=None,
            is_loss=False, is_financial=False)
        self.assertEqual(fairs, [])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py::RelativeValueTests -q`
Expected: FAIL — `ImportError: cannot import name '_warranted_fairs'`

- [ ] **Step 3: 구현** — `src/analysis/valuation.py`의 `_rel_fairs()` **아래**에 추가

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warranted.py::RelativeValueTests -q`
Expected: PASS

- [ ] **Step 5: `_relative_value`가 회귀를 먼저 시도하게 바꾼다**

`src/analysis/valuation.py:216`의 `_relative_value()` 본문을 아래로 **교체**한다
(도크스트링 포함. 시그니처에 `coef` 인자가 추가된다):

```python
def _relative_value(d: CompanyData, eps, bps, ebitda_ps, debt_ps, cash_ps,
                    revenue_ps=None, coef: dict | None = None
                    ) -> tuple[FairValue | None, dict]:
    """적정 배수를 회귀로 구한다(ADR-0014). 계수가 없으면 피어 중앙값으로 폴백한다.

    회귀로 가는 이유는 커버리지와 편향 둘 다다 — 전 종목 실측에서 피어 중앙값은
    ①이 44%(최소형주 80%) 빠졌고, 남은 값도 규모와 붙어 있었다(rho -0.141).
    회귀는 커버리지 100%에 rho -0.045다. 네 다리 모두에서 회귀가 이긴다.

    폴백 경로(계수 캐시 실패·표본 부족)는 종전 그대로다 — 규모 비교가능 피어
    (시총 1/5~5배)만 쓰고 부족하면 ①을 제외한다(ADR-0011).
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
```

- [ ] **Step 6: `ValuationResult`에 필드를 추가한다**

`src/analysis/valuation.py`의 `relative_leg_sensitivity` 줄 **아래**에 추가:

```python
    # ①이 회귀(ADR-0014)로 나왔는지 피어 중앙값 폴백인지. 화면이 근거를 다르게 쓴다.
    relative_basis: str | None = None            # "regression" | "peer_median"
    # 다리별 계수 분해 — [{leg, multiple, sector_base, size_adj, roe_adj,
    # below_range, beta_size, n}]. 화면에서 접어 둔다(ADR-0014 결정 다섯).
    relative_parts: list = field(default_factory=list)
```

- [ ] **Step 7: 호출부를 잇는다**

`src/analysis/valuation.py:626` 부근의 `fv, rel_meta = ... _relative_value(...)` 호출을
찾아 아래로 바꾼다:

```python
    fv, rel_meta = (None, {}) if mismatch else _relative_value(
        d, eps, bps, ebitda_ps, debt_ps, cash_ps, revenue_ps, coef=warranted_coef)
    res.relative_legs = rel_meta.get("legs")
    res.relative_leg_sensitivity = rel_meta.get("sensitivity")
    res.relative_basis = rel_meta.get("basis")
    res.relative_parts = rel_meta.get("parts") or []
```

같은 함수의 시그니처에 `warranted_coef: dict | None = None`을 추가한다(기본 None이라
기존 호출부는 그대로 동작한다).

- [ ] **Step 8: 헤드리스로 회귀 없음(폴백) 경로가 안 깨졌는지 확인한다**

Run: `.venv/Scripts/python.exe scripts/check_analysis.py KR 005930`
Expected: 오류 없이 완료. ① note가 `피어 중앙값 …`으로 나온다(아직 계수를 넘기지 않으므로).

- [ ] **Step 9: 커밋**

```bash
git add src/analysis/valuation.py tests/test_warranted.py
git commit -m "feat(valuation): ①을 회귀 적정 배수로 내고 피어 중앙값을 폴백으로 남긴다 (ADR-0014)"
```

---

## Task 8: provider가 계수를 실어 보낸다

**Files:**
- Modify: `src/analysis/valuation.py` (`compute_valuation` 진입부)
- Modify: `src/web/serialize.py:763` 부근

`src/analysis/`는 **순수 함수**라는 규약(CLAUDE.md)이 있으므로 `compute_valuation` 안에서
네트워크를 타지 않는다. 계수는 **호출부가 넣어 준다.**

호출부는 넷이다(`grep -rn "compute_valuation(" src/ scripts/`로 확인):

| 파일 | 줄 | 처리 |
|---|---|---|
| `src/web/serialize.py` | 172 | 계수 전달 (Meridian 웹 — 주 진입점) |
| `src/ui/pages/stock.py` | 931 | 계수 전달 (Streamlit) |
| `scripts/check_analysis.py` | 57 | 계수 전달 (헤드리스 검증) |
| `scripts/check_sensitivity.py` | 178·279 | **그대로 둔다** — 민감도는 가중치 실험이라 폴백으로 충분하다 |

- [ ] **Step 1: 계수를 읽는 헬퍼를 만든다**

`src/data/universe_multiples.py`에 추가:

```python
def coefficients_or_none(market: str) -> dict | None:
    """계수를 읽되 어떤 실패에도 판정을 멈추지 않는다 — 실패하면 피어 중앙값 폴백이다."""
    try:
        return get_coefficients(market) or None
    except Exception:
        return None
```

- [ ] **Step 2: 세 호출부를 잇는다**

`src/web/serialize.py:172`:

```python
    from src.data.universe_multiples import coefficients_or_none
    val = compute_valuation(d, ind, r_equity=cc.k_e,
                            warranted_coef=coefficients_or_none(d.market))
```

`src/ui/pages/stock.py:931`:

```python
    from src.data.universe_multiples import coefficients_or_none
    val = compute_valuation(d, ind, r_equity=custom_r or cc.k_e,
                            warranted_coef=coefficients_or_none(d.market))
```

`scripts/check_analysis.py:57`:

```python
    from src.data.universe_multiples import coefficients_or_none
    val = compute_valuation(d, ind, r_equity=cc.k_e,
                            warranted_coef=coefficients_or_none(d.market))
```

- [ ] **Step 3: PSR 다리가 쓰였으면 그 부정확함을 밝힌다**

ADR-0014 한계 절이 요구하는 것이다 — PSR은 회귀로도 MAE 0.921(±150%)로 넷 중 가장
부정확한데, 하필 적자 기업 전용 다리다. `compute_valuation`에서 ①을 `res.estimates`에
넣는 블록(`if fv:` 안, `relative_leg_sensitivity` 경고 **아래**)에 추가한다:

```python
        if any(p["leg"] == "psr" for p in res.relative_parts):
            res.notes.append(ValuationNote(
                "info",
                "이 종목은 적자라 ①에 매출 기준 배수(PSR)가 들어갔습니다. 네 배수 중 "
                "PSR이 실측 오차가 가장 커서(전 종목 검증에서 ±150% 수준), ①의 중심값보다 "
                "범위와 다른 방법과의 차이를 함께 보세요."))
```

- [ ] **Step 4: serialize에 근거를 싣는다**

`src/web/serialize.py:763`의 `"estimates": [...]` 바로 **아래**에 추가:

```python
            "relative_basis": val.relative_basis,
            "relative_parts": [
                {"leg": p["leg"], "multiple": num(p["multiple"]),
                 "sector_base": num(p["sector_base"]), "size_adj": num(p["size_adj"]),
                 "roe_adj": num(p["roe_adj"]), "below_range": bool(p["below_range"]),
                 "beta_size": num(p["beta_size"]), "n": p["n"]}
                for p in val.relative_parts],
```

- [ ] **Step 5: 실제로 회귀 경로가 도는지 확인한다**

Run: `.venv/Scripts/python.exe scripts/check_analysis.py KR 009310`
Expected: ① note가 `업종·규모·수익성 회귀 …`로 바뀌고, **참엔지니어링에서 ①이 성립한다**
(ADR-0013 측정에서 이 종목은 ①이 "규모 비교가능 피어 부족"으로 빠졌다).

- [ ] **Step 6: 커밋**

```bash
git add src/analysis/valuation.py src/web/serialize.py
git commit -m "feat(web): ①의 회귀 근거를 프런트로 내보낸다 (ADR-0014)"
```

---

## Task 9: 화면 — 기본 한 줄, 나머지는 접는다

**Files:**
- Modify: `web/assets/stock.js` (①의 근거를 그리는 자리)

- [ ] **Step 1: 근거 문자열을 만드는 곳을 찾는다**

Run: `grep -n "estimates\|note" web/assets/stock.js | head -20`

적정가 표에서 각 방법의 `note`를 그리는 함수를 찾는다.

- [ ] **Step 2: 분해를 접어서 붙인다**

해당 함수에서 ① 행을 그릴 때, `note` 뒤에 아래를 덧붙인다:

```javascript
  /* ①이 회귀로 나왔으면 계수 분해를 접어서 붙인다 — 기본은 결과 배수 한 줄이고,
     검증하려는 사람만 펼친다(ADR-0014 결정 다섯). fold()가 네이티브 <details>라
     렌더 경로가 늘어도 배선을 잊을 일이 없다. */
  function warrantedFold(parts) {
    if (!parts || !parts.length) return '';
    var LEGNAME = { pbr: 'PBR', per: 'PER', psr: 'PSR', ev_ebitda: 'EV/EBITDA' };
    var body = parts.map(function (p) {
      var pct = function (v) { return (v >= 0 ? '+' : '') + (v * 100).toFixed(0) + '%'; };
      return '<div style="margin-bottom:8px">'
        + '<div style="font-weight:600">' + esc(LEGNAME[p.leg] || p.leg) + '</div>'
        + '<div>업종 기준 ' + p.sector_base.toFixed(2) + '배</div>'
        + '<div>시총 조정 ' + pct(p.size_adj) + '</div>'
        + '<div>ROE 조정 ' + pct(p.roe_adj) + '</div>'
        + '<div style="border-top:1px solid var(--line);margin-top:4px;padding-top:4px">'
        + '적정 ' + esc(LEGNAME[p.leg] || p.leg) + ' ' + p.multiple.toFixed(2) + '배</div>'
        + (p.below_range ? '<div style="color:var(--ink-3)">※ 이 종목의 시총이 학습 표본의 '
            + '하한보다 작아 규모 조정이 범위 밖 추정입니다.</div>' : '')
        + '</div>';
    }).join('');
    var b = parts[0].beta_size, n = parts[0].n;
    body += '<div style="color:var(--ink-3);border-top:1px dashed var(--line);'
      + 'margin-top:6px;padding-top:6px">규모 계수 β=' + b.toFixed(2)
      + ' — 시총이 10배면 배수를 ' + (Math.pow(10, b)).toFixed(1) + '배로 봅니다'
      + ' (표본 ' + n + '종목). 이 계수는 <b>규모가 작으면 배수가 낮은 것이 정상</b>이라는'
      + ' 전제를 담고 있습니다.</div>';
    return fold('어떻게 나온 값인가', body);
  }
```

그리고 ① 행의 근거 칸에 `+ warrantedFold(D.verdict.relative_parts)`를 이어 붙인다
(`D.verdict.relative_basis === 'regression'`일 때만).

- [ ] **Step 3: 브라우저로 확인한다**

Run: `.venv/Scripts/python.exe server.py` 를 띄우고 `http://localhost:5178/stock.html?market=KR&q=009310` 를 연다.
Expected: ① 행에 `적정 PBR n.nn배`와 `> 어떻게 나온 값인가`가 보이고, 펼치면 분해가 나온다.

- [ ] **Step 4: 커밋**

```bash
git add web/assets/stock.js
git commit -m "feat(web): ①의 계수 분해를 접어서 보여준다 (ADR-0014)"
```

---

## Task 10: 진단 스크립트

**Files:**
- Create: `scripts/check_warranted.py`

- [ ] **Step 1: 구현**

```python
"""회귀 대 피어 중앙값 — ①의 적정 배수를 어느 쪽이 더 잘 맞히나 (ADR-0014).

    python scripts/check_warranted.py KR
    python scripts/check_warranted.py US

네트워크가 필요하다(전 종목 스냅숏). CI가 아니라 check_size_bias.py와 같은 수동 계열이다.
같은 종목의 **실제 배수**를 leave-one-out으로 예측해 비교한다. 기준선은 ADR-0014 실측이다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.warranted import fit_leg                       # noqa: E402
from src.data.universe_multiples import (LEG_BOUNDS, collect_kr,  # noqa: E402
                                         collect_us)

# ADR-0014 실측 기준선(한국). 회귀가 이보다 나빠지면 회귀가 아니다.
BASELINE_R2 = {"pbr": 0.434, "per": 0.395, "psr": 0.351, "ev_ebitda": 0.181}


def main() -> int:
    market = (sys.argv[1] if len(sys.argv) > 1 else "KR").upper()
    snap = collect_kr() if market == "KR" else collect_us()
    print(f"{market} 스냅숏 {len(snap)}종목\n")
    print(f"{'다리':<12}{'표본':>7}{'중앙값 R²':>11}{'회귀 R²':>10}{'판정':>8}")
    print("─" * 50)
    bad = 0
    for leg, (lo, hi) in LEG_BOUNDS.items():
        v = pd.to_numeric(snap.get(leg), errors="coerce")
        if v is None:
            continue
        d = snap.assign(multiple=v)[(v > lo) & (v < hi)
                                    & (snap["mcap"] > 0)].dropna(subset=["multiple"])
        d = d.reset_index(drop=True)
        coef = fit_leg(d, leg=leg)
        if coef is None:
            print(f"{leg:<12}{len(d):>7}{'—':>11}{'—':>10}{'표본부족':>8}")
            continue
        y = np.log(d["multiple"].to_numpy(float))
        mc = d["mcap"].to_numpy(float)
        sec = d["sector"].astype(str).to_numpy()
        med = np.full(len(d), np.nan)
        for i in range(len(d)):
            o = np.arange(len(d)) != i
            m = (sec == sec[i]) & o & (mc >= mc[i] / 5) & (mc <= mc[i] * 5)
            if m.sum() >= 2:
                med[i] = np.median(y[m])
        from src.analysis.warranted import warranted_multiple
        reg = np.array([np.log(warranted_multiple(coef, mc[i], sec[i],
                                                  d["roe"].iloc[i])["multiple"] or np.nan)
                        for i in range(len(d))])
        ok = ~np.isnan(med) & ~np.isnan(reg)
        r2 = lambda p: 1 - (p[ok] - y[ok]).var() / y[ok].var()   # noqa: E731
        rr, rm = r2(reg), r2(med)
        good = rr >= BASELINE_R2.get(leg, 0) * 0.8 and rr > rm
        bad += 0 if good else 1
        print(f"{leg:<12}{len(d):>7}{rm:>11.3f}{rr:>10.3f}{'[확인]' if good else '[문제]':>8}")
    print(f"\n문제 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 실행해 기준선을 넘는지 확인한다**

Run: `.venv/Scripts/python.exe scripts/check_warranted.py KR`
Expected: `pbr`·`per`가 `[확인]`. `문제 0건` (또는 psr·ev_ebitda가 표본부족이면 그것만 제외).

- [ ] **Step 3: 커밋**

```bash
git add scripts/check_warranted.py
git commit -m "chore: 회귀 대 피어 중앙값 진단 스크립트 (ADR-0014)"
```

---

## Task 11: 전체 검증과 ADR 상태 갱신

**Files:**
- Modify: `docs/adr/0014-relative-value-by-regression.md` (상태: 제안됨 → 채택됨)
- Modify: `docs/adr/README.md`
- Modify: `CLAUDE.md` (① 설명 한 줄)

- [ ] **Step 1: 전체 테스트를 돌린다**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 모두 통과. 실패가 있으면 **여기서 멈추고 고친다**.

- [ ] **Step 2: 헤드리스 검증 3종**

```bash
.venv/Scripts/python.exe scripts/check_analysis.py KR 005930
.venv/Scripts/python.exe scripts/check_analysis.py KR 009310
.venv/Scripts/python.exe scripts/check_analysis.py US AAPL
```
Expected: 셋 다 오류 없이 완료. 009310(참엔지니어링)에서 ①이 성립한다.

- [ ] **Step 3: ADR 상태를 채택됨으로 바꾼다**

`docs/adr/0014-relative-value-by-regression.md`의 `- 상태: 제안됨` → `- 상태: 채택됨`
`docs/adr/README.md`의 0014 행 상태도 `제안됨` → `채택됨`

Run: `.venv/Scripts/python.exe scripts/check_adr_index.py`
Expected: `문제 0`

- [ ] **Step 4: CLAUDE.md의 ① 설명을 고친다**

`CLAUDE.md`의 프로젝트 개요에서 *"업종 상대가치·역사적 밴드·RIM"* 을 설명하는 줄에
①의 계산 방식이 바뀐 것을 한 줄로 반영한다:

```
적정주가 **판정은 펀더멘털 3방법 삼각측량**(업종 상대가치·역사적 밴드·RIM)으로 내고,
①은 피어 중앙값이 아니라 **업종·규모·수익성 회귀**로 적정 배수를 구한다(ADR-0014).
```

- [ ] **Step 5: 커밋**

```bash
git add docs/adr CLAUDE.md
git commit -m "docs: ADR-0014를 채택됨으로 올리고 개요를 갱신한다"
```

---

## 검증 체크리스트

구현이 끝났다고 말하기 전에 아래를 **실행하고 출력을 읽는다**.

- [ ] `.venv/Scripts/python.exe -m pytest tests/ -q` — 전부 통과
- [ ] `.venv/Scripts/python.exe scripts/check_warranted.py KR` — 문제 0건
- [ ] `.venv/Scripts/python.exe scripts/check_analysis.py KR 009310` — ① 성립
- [ ] `.venv/Scripts/python.exe scripts/check_analysis.py US AAPL` — 오류 없음
- [ ] `.venv/Scripts/python.exe scripts/check_adr_index.py` — 문제 0
- [ ] 브라우저에서 ① 근거가 접힌 채 뜨고 펼치면 분해가 보인다
- [ ] 계수 캐시를 지우고(`data/cache/warranted_coef_v1_*.json`) 다시 조회하면
      **폴백 경로**(`피어 중앙값 …`)로 정상 동작한다
