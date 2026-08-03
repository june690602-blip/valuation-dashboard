# ⑤ 정규화 이익 구현 계획 (ADR-0015)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 최근 5년 평균 순이익에 ADR-0014의 회귀 적정 PER을 곱한 값을 판정의 네 번째 축으로 넣는다.

**Architecture:** `valuation.py`에 순수 함수 `_normalized_earnings()`(창에서 정상 이익을 뽑는다)와
`_normalized_value()`(적정가를 만든다)를 더한다. 계수는 호출부가 이미 넣어 주는
`warranted_coef`를 그대로 탄다(ADR-0014 경로). `METHOD_WEIGHTS`에 0.25로 등록하면
기존 재정규화가 나머지를 처리한다. 네트워크·새 의존성 없음.

**Tech Stack:** Python 3.14, numpy, pandas, unittest+pytest

**전제:** 브랜치 `fix/peer-selection-by-size`. 설계 합의문은
[`docs/superpowers/specs/2026-08-04-normalized-earnings-design.md`](../specs/2026-08-04-normalized-earnings-design.md).

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/analysis/valuation.py` (수정) | 정상 이익 추출·적정가 계산·가중 등록·필드·호출 |
| `src/web/serialize.py` (수정) | ⑤ 근거를 프런트로 |
| `web/assets/stock.js` (수정) | ⑤ 행과 근거 접기 |
| `docs/adr/0015-normalized-earnings-axis.md` (신설) | 결정 기록 |
| `docs/adr/0006-*.md` (수정) | 머리에 포인터 인용문 한 줄 |
| `docs/adr/README.md`·`CLAUDE.md` (수정) | 인덱스·개요 |
| `tests/test_normalized_earnings.py` (신설) | 순수 함수 회귀 테스트 |
| `scripts/check_normalized.py` (신설) | 커버리지·효과 진단 |

---

## Task 1: 창에서 정상 이익을 뽑는다

**Files:**
- Modify: `src/analysis/valuation.py`
- Test: `tests/test_normalized_earnings.py` (신설)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_normalized_earnings.py`를 새로 만든다:

```python
"""정규화 이익 축(ADR-0015) 순수 함수 테스트."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.analysis.valuation import (NORMALIZE_MIN_YEARS, NORMALIZE_WINDOW,
                                    _normalized_earnings)


def _fin(values):
    """net_income 열만 가진 연간 재무 프레임 (과거→최신)."""
    return pd.DataFrame({"net_income": values},
                        index=range(2019, 2019 + len(values)))


class NormalizedEarningsTests(unittest.TestCase):
    def test_averages_the_last_five_years(self):
        # 6년이 있으면 **마지막 5년만** 쓴다 — 창은 최신 쪽으로 고정이다
        ni, years = _normalized_earnings(_fin([1000., 100., 200., 300., 400., 500.]))
        self.assertEqual(years, 5)
        self.assertAlmostEqual(ni, 300.0)     # (100+200+300+400+500)/5

    def test_uses_what_exists_when_history_is_short(self):
        ni, years = _normalized_earnings(_fin([100., 200., 300.]))
        self.assertEqual(years, 3)
        self.assertAlmostEqual(ni, 200.0)

    def test_refuses_when_fewer_than_min_years(self):
        ni, years = _normalized_earnings(_fin([100., 200.]))
        self.assertIsNone(ni)
        self.assertEqual(years, 2)

    def test_drops_missing_inside_the_window(self):
        # 창 안 결측은 빼고 남은 것으로 평균한다. 0으로 채우면 이익을 지어내는 것이다.
        ni, years = _normalized_earnings(_fin([100., np.nan, 300., 400., 500.]))
        self.assertEqual(years, 4)
        self.assertAlmostEqual(ni, 325.0)     # (100+300+400+500)/4

    def test_refuses_when_too_many_missing(self):
        ni, years = _normalized_earnings(_fin([100., np.nan, np.nan, np.nan, 500.]))
        self.assertIsNone(ni)
        self.assertEqual(years, 2)

    def test_keeps_negative_years(self):
        # 적자 연도를 빼면 정규화가 아니라 체리피킹이다. 사이클 저점이 창에 들어와야 한다.
        ni, years = _normalized_earnings(_fin([-100., -50., 100., 200., 350.]))
        self.assertEqual(years, 5)
        self.assertAlmostEqual(ni, 100.0)

    def test_handles_missing_column_and_empty_frame(self):
        self.assertEqual(_normalized_earnings(None), (None, 0))
        self.assertEqual(_normalized_earnings(pd.DataFrame()), (None, 0))
        self.assertEqual(_normalized_earnings(pd.DataFrame({"revenue": [1.0]})), (None, 0))

    def test_drops_infinities(self):
        ni, years = _normalized_earnings(_fin([np.inf, 100., 200., 300., 400.]))
        self.assertEqual(years, 4)
        self.assertAlmostEqual(ni, 250.0)

    def test_window_and_minimum_are_five_and_three(self):
        self.assertEqual(NORMALIZE_WINDOW, 5)
        self.assertEqual(NORMALIZE_MIN_YEARS, 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalized_earnings.py -q`
Expected: FAIL — `ImportError: cannot import name '_normalized_earnings'`

- [ ] **Step 3: 구현**

`src/analysis/valuation.py`의 `_leg_sensitivity()` 함수 **아래**, `# ── ② 역사적 밴드` 주석
**위**에 넣는다:

```python
# ── ⑤ 정규화 이익 ────────────────────────────────────────────────────
# 창을 5년으로 두는 이유 — 문헌(Anderson & Brooks 2006, JBFA 33(7-8) 1063-1086)은 8년을
# 쓰지만 DART 이력이 6년이라 그 이상은 만들 수 없다. 효과가 그만큼 약해지는 것을 ADR-0015
# 한계에 적었다. 최소 3년은 '평균'이라 부를 수 있는 하한이다.
NORMALIZE_WINDOW = 5
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
```

`numpy as np`·`pandas as pd`는 `valuation.py:45-46`에 이미 있다. 새 import는 필요 없다.

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalized_earnings.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/analysis/valuation.py tests/test_normalized_earnings.py
git commit -m "feat(valuation): 최근 5년 평균 순이익을 뽑는다 (ADR-0015)"
```

---

## Task 2: 정상 이익으로 적정가를 만든다

**Files:**
- Modify: `src/analysis/valuation.py`
- Test: `tests/test_normalized_earnings.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_normalized_earnings.py`의 `NormalizedEarningsTests` 클래스 **아래**에 추가한다.
파일 상단 import에 `_normalized_value`와 `fit_leg`를 더한다:

```python
from src.analysis.valuation import (NORMALIZE_MIN_YEARS, NORMALIZE_WINDOW,
                                    _normalized_earnings, _normalized_value)
from src.analysis.warranted import fit_leg
```

그리고 클래스를 추가한다:

```python
def _synthetic_per(n=600, beta=0.30, seed=0):
    """log(PER) = -6 + 0.30·log(시총) + 업종효과 인 합성 데이터 (test_warranted와 같은 형태)."""
    rng = np.random.default_rng(seed)
    mcap = np.exp(rng.uniform(np.log(1e10), np.log(1e13), n))
    sector = rng.choice(["A", "B", "C"], n)
    eff = {"A": 0.0, "B": 0.5, "C": -0.4}
    roe = rng.uniform(0.0, 0.20, n)
    y = -6.0 + beta * np.log(mcap) + np.array([eff[s] for s in sector])
    return pd.DataFrame({"multiple": np.exp(y), "mcap": mcap,
                         "sector": sector, "roe": roe})


class NormalizedValueTests(unittest.TestCase):
    def setUp(self):
        self.coef = {"per": fit_leg(_synthetic_per(), leg="per")}
        self.mcap = 1e11
        self.shares = 1_000_000.0
        self.equity = 5e10

    def _call(self, values, **kw):
        kw.setdefault("shares", self.shares)
        kw.setdefault("equity", self.equity)
        kw.setdefault("mcap", self.mcap)
        kw.setdefault("sector", "B")
        kw.setdefault("coef", self.coef)
        return _normalized_value(_fin(values), **kw)

    def test_fair_value_is_regression_per_times_normalized_eps(self):
        fv, meta = self._call([100e8, 200e8, 300e8, 400e8, 500e8])
        self.assertIsNotNone(fv)
        self.assertEqual(fv.method, "정규화 이익")
        ni = 300e8
        self.assertAlmostEqual(meta["eps"], ni / self.shares, places=6)
        self.assertAlmostEqual(fv.mid, meta["per"] * meta["eps"], delta=fv.mid * 1e-9)
        self.assertEqual(meta["years"], 5)

    def test_stands_when_current_year_is_a_loss_but_average_is_positive(self):
        # 이 축의 존재 이유다 — 현재 적자라 ①은 부정확한 PSR로 가고 ②는 아예 빠진다.
        fv, meta = self._call([500e8, 400e8, 300e8, 200e8, -100e8])
        self.assertIsNotNone(fv)
        self.assertAlmostEqual(meta["eps"], 260e8 / self.shares, places=6)
        self.assertIsNone(meta["ratio"])      # 현재이익 ≤ 0이면 비율이 뜻을 잃는다

    def test_refused_when_average_is_a_loss(self):
        fv, meta = self._call([-100e8, -200e8, -300e8, -400e8, -500e8])
        self.assertIsNone(fv)
        self.assertIn("적자", meta["reason"])

    def test_refused_when_history_is_short(self):
        fv, meta = self._call([100e8, 200e8])
        self.assertIsNone(fv)
        self.assertIn("이력", meta["reason"])

    def test_refused_without_per_coefficient(self):
        # 계수가 없으면 피어 중앙값으로 폴백하지 않는다 — 배수를 바꾸면 다른 방법이다.
        fv, meta = self._call([100e8, 200e8, 300e8, 400e8, 500e8], coef={})
        self.assertIsNone(fv)
        self.assertIn("계수", meta["reason"])

    def test_refused_without_shares_or_equity(self):
        for kw in ({"shares": 0}, {"equity": 0}, {"shares": None}, {"equity": None}):
            with self.subTest(**kw):
                fv, _ = self._call([100e8, 200e8, 300e8, 400e8, 500e8], **kw)
                self.assertIsNone(fv)

    def test_uses_normalized_roe_not_current_roe(self):
        # 현재 적자여도 정상 이익이 흑자면 ROE도 흑자 구간으로 넣는다. 현재 ROE를 쓰면
        # ADR-0014의 U자 더미가 '대규모 적자' 칸을 골라 배수를 크게 올린다(실측 +110%).
        losses = [500e8, 400e8, 300e8, 200e8, -100e8]
        fv, meta = self._call(losses)
        expected_roe = (sum(losses) / 5) / self.equity
        from src.analysis.warranted import warranted_multiple
        want = warranted_multiple(self.coef["per"], self.mcap, "B", expected_roe)["multiple"]
        self.assertAlmostEqual(meta["per"], want, delta=want * 1e-9)

    def test_ratio_reports_how_far_normalization_moved_it(self):
        fv, meta = self._call([100e8, 200e8, 300e8, 400e8, 500e8])
        self.assertAlmostEqual(meta["ratio"], 300e8 / 500e8, places=6)   # 0.6

    def test_range_comes_from_the_windows_own_spread(self):
        fv, _ = self._call([100e8, 200e8, 300e8, 400e8, 500e8])
        self.assertLessEqual(fv.low, fv.mid)
        self.assertLessEqual(fv.mid, fv.high)
        self.assertLess(fv.low, fv.high)

    def test_range_degenerates_rather_than_going_negative(self):
        # 창 하위 분위가 적자면 low를 음수로 두지 않는다 — 음수 적정가는 뜻이 없다.
        fv, _ = self._call([-400e8, -200e8, 300e8, 600e8, 900e8])
        self.assertGreater(fv.low, 0)
        self.assertLessEqual(fv.low, fv.mid)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalized_earnings.py::NormalizedValueTests -q`
Expected: FAIL — `ImportError: cannot import name '_normalized_value'`

- [ ] **Step 3: 구현** — Task 1에서 넣은 `_normalized_earnings()` **아래**에 추가

```python
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
    # math이 아니라 np를 쓴다 — valuation.py는 math을 import하지 않는다(numpy·pandas만).
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalized_earnings.py -q`
Expected: PASS (19 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/analysis/valuation.py tests/test_normalized_earnings.py
git commit -m "feat(valuation): 정상 이익 × 회귀 적정 PER로 적정가를 만든다 (ADR-0015)"
```

---

## Task 3: 판정 축으로 등록한다

**Files:**
- Modify: `src/analysis/valuation.py:75-86` (`METHOD_WEIGHTS`·`FUNDAMENTAL_METHODS`)
- Modify: `src/analysis/valuation.py`의 `ValuationResult`
- Test: `tests/test_normalized_earnings.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_normalized_earnings.py` 끝에 추가한다:

```python
class WeightRegistrationTests(unittest.TestCase):
    def test_registered_at_the_same_tier_as_relative_and_band(self):
        # 가중치는 우리 측정이 아니라 문헌 순위의 인코딩이다(ADR-0003의 원칙).
        # LNT(2002)의 '과거 이익 멀티플' 칸이고, 그 칸 안에서 Anderson&Brooks(2006)가
        # 장기평균이 단년보다 낫다고 보고했으므로 ①②보다 낮출 근거가 없다.
        from src.analysis.valuation import FUNDAMENTAL_METHODS, METHOD_WEIGHTS

        self.assertEqual(METHOD_WEIGHTS["정규화 이익"], 0.25)
        self.assertEqual(METHOD_WEIGHTS["정규화 이익"], METHOD_WEIGHTS["업종 상대가치"])
        self.assertIn("정규화 이익", FUNDAMENTAL_METHODS)
        self.assertNotIn("선행 이익(컨센서스)", FUNDAMENTAL_METHODS)

    def test_renormalized_weights_match_the_design(self):
        from src.analysis.valuation import FairValue, _weighted

        est = [FairValue(m, 1.0, 1.0, 1.0) for m in
               ("업종 상대가치", "역사적 밴드", "수익가치(RIM)", "정규화 이익")]
        _lo, _mid, _hi, w = _weighted(est)
        self.assertAlmostEqual(w["업종 상대가치"], 0.2778, places=3)
        self.assertAlmostEqual(w["역사적 밴드"], 0.2778, places=3)
        self.assertAlmostEqual(w["수익가치(RIM)"], 0.1667, places=3)
        self.assertAlmostEqual(w["정규화 이익"], 0.2778, places=3)

    def test_result_carries_the_normalization_evidence(self):
        from src.analysis.valuation import ValuationResult

        r = ValuationResult()
        self.assertIsNone(r.normalized_eps)
        self.assertIsNone(r.normalized_years)
        self.assertIsNone(r.normalized_ratio)
        self.assertIsNone(r.normalized_per)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normalized_earnings.py::WeightRegistrationTests -q`
Expected: FAIL — `KeyError: '정규화 이익'`

- [ ] **Step 3: 구현 — 가중치**

`src/analysis/valuation.py`의 `METHOD_WEIGHTS`를 아래로 **교체**한다:

```python
METHOD_WEIGHTS = {
    "선행 이익(컨센서스)": 0.35,
    "업종 상대가치": 0.25,
    "역사적 밴드": 0.25,
    # ⑤는 ①②와 같은 칸이다(ADR-0015). LNT(2002)의 순위에서 '과거 이익 멀티플'이고,
    # 그 칸 **안에서** Anderson & Brooks(2006)가 8년 평균 이익 PER이 단년보다 낫다고
    # 보고했다 — ①② 아래로 둘 문헌 근거가 없다. 우리 표본으로 맞춘 값이 아니다.
    "정규화 이익": 0.25,
    "수익가치(RIM)": 0.15,
}
```

그리고 `FUNDAMENTAL_METHODS`를 아래로 **교체**한다:

```python
# 회사가 이미 낸 실적·자산만으로 서는 방법들 — **판정은 이 넷으로만 낸다**(ADR-0006·0015).
# 위 가중치를 이 넷에 대해 재정규화해 쓴다(0.25/0.25/0.15/0.25 → 0.278/0.278/0.167/0.278).
# 순위(이익 멀티플 > 장부가)는 그대로 유지되고, 빠지는 것은 ④의 몫뿐이다.
FUNDAMENTAL_METHODS = ("업종 상대가치", "역사적 밴드", "수익가치(RIM)", "정규화 이익")
```

- [ ] **Step 4: 구현 — 결과 필드**

`ValuationResult`의 `relative_parts: list = field(default_factory=list)` 줄 **아래**에 추가한다:

```python
    # ⑤ 정규화 이익(ADR-0015). normalized_ratio가 이 축의 핵심이다 — 1보다 작으면
    # 현재 이익이 정상보다 높다는 뜻이고, 그것이 ⑤가 다른 축과 갈리는 이유 전부다.
    normalized_eps: float | None = None      # 정상 EPS
    normalized_years: int | None = None      # 평균에 실제로 쓴 연수 ("5년"이라 단정하지 말 것)
    normalized_ratio: float | None = None    # 정상이익 / 현재이익 (현재이익 ≤ 0이면 None)
    normalized_per: float | None = None      # 적용한 회귀 적정 PER
```

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — 기존 테스트가 하나도 깨지지 않아야 한다. 깨지면 **여기서 멈추고 원인을 본다**
(가중치 변경이 기존 판정 테스트를 흔들 수 있다).

- [ ] **Step 6: 커밋**

```bash
git add src/analysis/valuation.py tests/test_normalized_earnings.py
git commit -m "feat(valuation): ⑤를 판정 축으로 등록한다 — 가중 0.25 (ADR-0015)"
```

---

## Task 4: `compute_valuation`에 연결한다

**Files:**
- Modify: `src/analysis/valuation.py`의 `compute_valuation()` — ③ 블록 끝(`res.skipped.append(("수익가치(RIM)", "ROE ≤ 0 (적자)"))` 이 있는 else 블록) **아래**, `# ④ 선행 이익` 주석 **위**

- [ ] **Step 1: 구현**

아래를 그 자리에 넣는다:

```python
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
            r = nm["ratio"]
            if r is not None and (r < 1 / 1.5 or r > 1.5):
                direction = ("현재 이익이 정상보다 높습니다" if r < 1
                             else "현재 이익이 정상보다 낮습니다")
                res.notes.append(ValuationNote(
                    "info",
                    f"최근 {nm['years']}년 평균 순이익이 현재의 {r:.2f}배입니다 — {direction}. "
                    "⑤ 정규화 이익은 이 차이를 되돌린 값이라 다른 방법과 갈릴 수 있고, "
                    "그 갈림 자체가 이 방법이 말하려는 것입니다."))
        else:
            res.skipped.append(("정규화 이익", nm["reason"]))
```

- [ ] **Step 2: 문법과 전체 테스트를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 3: 진단 스크립트가 제외 사유와 방법 수를 정직하게 찍게 한다**

`scripts/check_analysis.py`는 지금 **제외 사유를 출력하지 않고**, 종합 라벨에 `①②③`이
하드코딩돼 있어 ⑤가 들어가면 거짓이 된다. 아래 두 줄을

```python
    print(f"  펀더멘털 종합(①②③ · 판정 근거): {fmt(val.fair_mid)} | 괴리율 "
          f"{fmt(val.gap, 'pct')} → {val.verdict} (신뢰도 {val.confidence})")
```

아래로 **교체**한다:

```python
    for m, r in val.skipped:
        print(f"  {m}: 제외 — {r}")
    used = "·".join(val.weights) if val.weights else "없음"
    print(f"  펀더멘털 종합({used} · 판정 근거): {fmt(val.fair_mid)} | 괴리율 "
          f"{fmt(val.gap, 'pct')} → {val.verdict} (신뢰도 {val.confidence})")
```

그리고 그 아래 `컨센서스 반영(①②③④ · 병기)` 문구의 `①②③④`를 `+④`로 바꾼다:

```python
        print(f"  컨센서스 반영(위 + ④ · 병기): {fmt(val.fair_mid_consensus)} | 괴리율 "
```

- [ ] **Step 4: 헤드리스로 실제 동작을 확인한다**

Run: `.venv/Scripts/python.exe scripts/check_analysis.py KR 005930`
Expected: `[적정주가]` 목록에 `정규화 이익: … (N년 평균 이익 × 적정 PER n.n배)` 행이 뜨고,
종합 라벨에 `정규화 이익`이 포함된다. 안 뜨면 바로 위에 `정규화 이익: 제외 — …`가 찍히므로
사유를 읽는다.

Run: `.venv/Scripts/python.exe scripts/check_analysis.py US AAPL`
Expected: 오류 없이 완료. (미국도 `net_income` 이력이 있으므로 ⑤가 서는 것이 정상이다.)

- [ ] **Step 5: 커밋**

```bash
git add src/analysis/valuation.py scripts/check_analysis.py
git commit -m "feat(valuation): ⑤를 판정 계산에 연결한다 (ADR-0015)"
```

---

## Task 5: 진단 스크립트 — 커버리지와 효과를 잰다

**Files:**
- Create: `scripts/check_normalized.py`

- [ ] **Step 1: 구현**

```python
"""⑤ 정규화 이익 — 커버리지와 효과 (ADR-0015).

    python scripts/check_normalized.py          # 기본 60종목
    python scripts/check_normalized.py --limit 150

네트워크가 필요하다(전 종목 재무). CI가 아니라 check_size_bias.py와 같은 수동 계열이다.

성패 판정(설계 합의문):
  - 커버리지: 무작위 전 종목 표본에서 ⑤가 서는 비율 50% 이상
  - 효과    : normalized_ratio가 1.5배 밖인 종목에서 ⑤와 ①의 괴리율이 갈려야 한다
  - 보존    : ratio가 1에 가까운 종목에서는 ⑤와 ①이 비슷해야 한다
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.capital_cost import compute_capital_cost        # noqa: E402
from src.analysis.indicators import compute_indicators            # noqa: E402
from src.analysis.valuation import compute_valuation              # noqa: E402
from src.data.kr_provider import KRProvider                       # noqa: E402
from src.data.universe import get_kr_listing                      # noqa: E402
from src.data.universe_multiples import coefficients_or_none      # noqa: E402

COVERAGE_FLOOR = 0.50


def one(code: str, coef) -> dict | None:
    try:
        d = KRProvider().load(code, peer_count=9)
        ind = compute_indicators(d)
        cc = compute_capital_cost(d)
        v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
    except Exception:
        return None
    mids = {e.method: e.mid for e in v.estimates}
    if not d.price:
        return None
    return {
        "code": code, "name": d.name,
        "has5": "정규화 이익" in mids,
        "ratio": v.normalized_ratio,
        "gap5": (mids["정규화 이익"] / d.price - 1) if "정규화 이익" in mids else np.nan,
        "gap1": (mids["업종 상대가치"] / d.price - 1) if "업종 상대가치" in mids else np.nan,
        "skip": next((r for m, r in v.skipped if m == "정규화 이익"), ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()

    L = get_kr_listing()
    pool = L[L["is_common"] & (L["Marcap"] > 0)]
    codes = pool.sample(args.limit, random_state=3)["Code"].tolist()
    coef = coefficients_or_none("KR")

    with ThreadPoolExecutor(6) as ex:
        rows = [r for r in ex.map(lambda c: one(c, coef), codes) if r]
    df = pd.DataFrame(rows)
    print(f"표본 {len(codes)}종목 · 분석 성공 {len(df)}종목\n")

    cov = df["has5"].mean()
    mark = "[확인]" if cov >= COVERAGE_FLOOR else "[문제]"
    print(f"{mark} 커버리지 {cov:.1%} (기준선 {COVERAGE_FLOOR:.0%})")
    if (df["has5"] == False).any():                                  # noqa: E712
        print("      제외 사유:")
        for reason, n in df.loc[~df["has5"], "skip"].value_counts().items():
            print(f"        {n:>3}곳  {reason}")

    both = df[df["has5"] & df["gap1"].notna() & df["gap5"].notna()].copy()
    if len(both):
        both["diff"] = (both["gap5"] - both["gap1"]).abs()
        far = both[both["ratio"].notna() &
                   ((both["ratio"] < 1 / 1.5) | (both["ratio"] > 1.5))]
        near = both[both["ratio"].notna() &
                    (both["ratio"] >= 1 / 1.5) & (both["ratio"] <= 1.5)]
        print(f"\n효과 — ⑤와 ①의 괴리율 차이 (n={len(both)})")
        if len(far):
            print(f"  정규화가 크게 되돌린 종목(ratio 1.5배 밖, n={len(far)}) "
                  f"차이 중앙 {far['diff'].median():.1%}")
        if len(near):
            print(f"  되돌릴 게 적은 종목(ratio 1.5배 안, n={len(near)}) "
                  f"차이 중앙 {near['diff'].median():.1%}")
        if len(far) and len(near):
            ok = far["diff"].median() > near["diff"].median()
            print(f"  {'[확인]' if ok else '[문제]'} "
                  "정규화 폭이 큰 쪽에서 더 갈려야 한다")

        print("\n가장 크게 갈린 5곳:")
        for r in both.nlargest(5, "diff").itertuples():
            rt = f"{r.ratio:.2f}" if r.ratio == r.ratio else "—"
            print(f"  {r.code} {str(r.name)[:12]:<14} ratio {rt:>5}  "
                  f"① {r.gap1:+7.1%}  ⑤ {r.gap5:+7.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 실행해 성패 기준을 확인한다**

Run: `.venv/Scripts/python.exe scripts/check_normalized.py --limit 60`
Expected: 커버리지 `[확인]`(50% 이상), 효과 절의 `[확인]`.
`[문제]`가 나오면 **여기서 멈추고** 제외 사유 표를 읽는다 — 커버리지가 낮으면 설계
한계(5년 평균 적자)인지 구현 버그인지 사유가 갈라 준다.

- [ ] **Step 3: 커밋**

```bash
git add scripts/check_normalized.py
git commit -m "chore: ⑤ 커버리지·효과 진단 스크립트 (ADR-0015)"
```

---

## Task 6: 화면에 싣는다

**Files:**
- Modify: `src/web/serialize.py`의 `"relative_parts": [...]` 블록 **아래**
- Modify: `web/assets/stock.js:938-939` (`METHOD_TAB`·`CANON`)
- Modify: `web/assets/stock.js`의 근거 칸(`var why = ...` 부분)

- [ ] **Step 1: serialize에 필드를 싣는다**

`src/web/serialize.py`에서 `for p in val.relative_parts],` 줄 **아래**에 추가한다:

```python
            "normalized": {
                "eps": num(val.normalized_eps), "years": val.normalized_years,
                "ratio": num(val.normalized_ratio), "per": num(val.normalized_per),
            },
```

- [ ] **Step 2: 확인한다**

Run:
```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); from src.web.serialize import analyze; v=analyze('KR','005930')['verdict']; print(v['normalized'])"
```
Expected: `{'eps': ..., 'years': 5, 'ratio': ..., 'per': ...}` — 모두 None이 아니어야 한다.

- [ ] **Step 3: 표에 ⑤ 행을 만든다**

`web/assets/stock.js:938-939`의 두 줄을 아래로 **교체**한다:

```javascript
    var METHOD_TAB = { '업종 상대가치': ['①', 'peers'], '역사적 밴드': ['②', 'valuation'], '수익가치(RIM)': ['③', 'financials'], '선행 이익(컨센서스)': ['④', null], '정규화 이익': ['⑤', 'financials'] };
    /* 번호가 연속하지 않는 것은 의도다 — ④를 옮기면 ADR-0003·0006의 서술이 통째로
       어긋나고, ①②③⑤가 판정이고 ④만 병기라는 사실이 표에서 계속 드러난다. */
    var CANON = ['업종 상대가치', '역사적 밴드', '수익가치(RIM)', '정규화 이익', '선행 이익(컨센서스)'];
```

- [ ] **Step 4: 근거를 접어서 붙인다**

`web/assets/stock.js`의 `warrantedFold` 함수 **아래**에 추가한다:

```javascript
  /* ⑤의 근거 — 이 축이 다른 축과 갈리는 이유는 정규화 비율 하나다. 그것을 접어 둔다. */
  function normalizedFold(nz) {
    if (!nz || nz.eps == null || nz.per == null) return '';
    var body = '<div>' + nz.years + '년 평균 순이익으로 계산한 정상 EPS '
      + won(nz.eps) + '</div>'
      + '<div>× 적정 PER ' + nz.per.toFixed(1) + '배 (업종·규모·수익성 회귀)</div>';
    if (nz.ratio != null) {
      body += '<div style="border-top:1px solid var(--line);margin-top:4px;padding-top:4px">'
        + '정상 이익은 현재의 ' + nz.ratio.toFixed(2) + '배 — '
        + (nz.ratio < 1 ? '현재 이익이 정상보다 높습니다' : '현재 이익이 정상보다 낮습니다')
        + '</div>';
    }
    body += '<div style="color:var(--ink-3);border-top:1px dashed var(--line);'
      + 'margin-top:6px;padding-top:6px">' + nz.years + '개년 평균입니다. 근거 문헌'
      + '(Anderson &amp; Brooks 2006)은 8년을 쓰지만 공시 이력이 짧아 그만큼 효과가'
      + ' 약합니다. 5년이 통째로 호황이면 평균도 호황입니다 — 이 방법은 사이클을'
      + ' <b>완화할 뿐 제거하지 않습니다</b>.</div>';
    return fold('어떻게 나온 값인가', body);
  }
```

그리고 `var why = (name === '업종 상대가치' && ...)` 줄을 아래로 **교체**한다:

```javascript
        var why = (name === '업종 상대가치' && v.relative_basis === 'regression')
          ? warrantedFold(v.relative_parts)
          : (name === '정규화 이익' ? normalizedFold(v.normalized) : '');
```

- [ ] **Step 5: 브라우저로 확인한다**

`.venv/Scripts/python.exe server.py`를 띄우고 `http://localhost:5178/stock.html?market=KR&q=005930`을 연다.
Expected: 표에 `⑤ 정규화 이익` 행이 있고, 근거 칸에 `> 어떻게 나온 값인가`가 접혀 있다.
펼치면 정상 EPS·적정 PER·비율이 보인다. 콘솔 오류가 없어야 하고, **격자 자식이 4개로
유지돼야 한다**(5개가 되면 4열 표의 열이 밀린다 — CLAUDE.md 경고).

- [ ] **Step 6: 커밋**

```bash
git add src/web/serialize.py web/assets/stock.js
git commit -m "feat(web): ⑤ 정규화 이익을 표와 접힘 근거로 보여준다 (ADR-0015)"
```

---

## Task 7: ADR-0015와 문서

**Files:**
- Create: `docs/adr/0015-normalized-earnings-axis.md`
- Modify: `docs/adr/0006-fundamental-verdict-consensus-alongside.md` (머리에 포인터)
- Modify: `docs/adr/README.md`·`CLAUDE.md`

- [ ] **Step 1: ADR-0015를 쓴다**

**설계 합의문의 산문을 그대로 옮겨 온다** —
`docs/superpowers/specs/2026-08-04-normalized-earnings-design.md`에 맥락·측정·근거·대안·한계가
이미 완성된 문장으로 있다. 새로 쓰지 말고 아래 골격에 그 절들을 배치한다.

```markdown
# ADR-0015: 정규화 이익(5년 평균)을 판정의 네 번째 축으로 더한다

- 상태: 채택됨
- 날짜: 2026-08-04
- 관련: [ADR-0003](0003-fair-value-weighted-average.md)(가중치는 문헌 순위의 인코딩 —
  이 원칙을 그대로 따른다), [ADR-0006](0006-fundamental-verdict-consensus-alongside.md)(판정
  방법 집합을 넷으로 넓힌다 · ④의 병기는 그대로), [ADR-0011](0011-peer-size-window-and-no-fallback.md)(오염된
  값보다 계산 불가), [ADR-0014](0014-relative-value-by-regression.md)(적정 PER을 여기서 가져온다)
- 관련 코드: `src/analysis/valuation.py`의 `_normalized_earnings()`·`_normalized_value()`·
  `METHOD_WEIGHTS`, 진단은 `scripts/check_normalized.py`

## 맥락
  ← 합의문 '왜 필요한가' 절 (①②③이 현재 회계값을 공유해 상관 0.74~0.84 · 정규화 항이
    −0.407로 음수라 사이클 정점 함정을 되돌린다 · 현재 적자 종목에서 유일한 이익 기반 축)

## 측정
  ← 합의문 '무엇을 재서 그렇게 정했나' 절 전부 (176종목 평균 대 중앙값 표 ·
    정규화 항의 크기 · 이력 충분성)

## 결정
  ← 합의문 '설계' 절 전부 (계산식 · 성립 조건 표 · 번호를 ⑤로 두는 이유)
  가중치 표를 여기 넣는다:

  | 방법 | 원본 가중 | 재정규화(판정) |
  |---|---:|---:|
  | ① 업종 상대가치 | 0.25 | 27.8% |
  | ② 역사적 밴드 | 0.25 | 27.8% |
  | ③ 수익가치(RIM) | 0.15 | 16.7% |
  | ⑤ 정규화 이익 | 0.25 | 27.8% |
  | ④ 선행 이익(컨센서스) | 0.35 | 판정 제외 · 병기(ADR-0006) |

## 근거 — 가중치는 문헌에서 가져온다
  ← 합의문 '가중치는 문헌에서 가져온다' 절 전부. 서지는 아래 형태로 정확히 적는다:

  1. Liu, J., Nissim, D. & Thomas, J. (2002). "Equity Valuation Using Multiples."
     *Journal of Accounting Research* 40(1), 135–172.
  2. Anderson, K. & Brooks, C. (2006). "The Long-Term Price-Earnings Ratio."
     *Journal of Business Finance & Accounting* 33(7-8), 1063–1086.

## 검토한 대안
  ← 합의문 '검토한 대안' 절 전부 (병기만 · ②의 배수 · 중앙값 · 인플레 조정 ·
    우리 측정으로 가중 정하기)

## 한계
  ← 합의문 '한계' 절 전부. 일곱 가지를 하나도 빼지 않는다:
    ①⑤ 배수 공유 55.6% · 문헌 8년 대 우리 5년 · 두 문헌의 지표가 다름(가격오차 대
    수익률 프리미엄) · 한국 표본 아님 · 커버리지 63.6%는 '이력 6년 이상' 안의 값이라
    전 종목 기준은 더 낮음 · 회계 변경·합병에 취약 · 5년이 통째로 호황이면 평균도 호황

## 재현

```bash
python scripts/check_normalized.py --limit 80
python scripts/check_analysis.py KR 005930
```
```
- 재현: `python scripts/check_normalized.py`

- [ ] **Step 2: ADR-0006 머리에 포인터를 단다**

채택된 ADR은 본문을 고치지 않는다. `docs/adr/0006-*.md`의 `## 맥락` **위**에 한 문단을 넣는다:

```markdown
> **보완 (2026-08-04).** [ADR-0015](0015-normalized-earnings-axis.md)가 **⑤ 정규화 이익**을
> 판정 축으로 더했다. 이 ADR의 핵심 결정(④ 컨센서스를 판정에서 빼고 병기한다)은 그대로이고,
> 바뀐 것은 판정에 드는 방법이 셋에서 **넷**이 됐다는 것뿐이다. 아래 본문의 "3방법"은
> 이제 "4방법"으로 읽는다. 재정규화 가중은 ① 27.8 · ② 27.8 · ③ 16.7 · ⑤ 27.8%다.
```

- [ ] **Step 3: 인덱스와 개요를 고친다**

`docs/adr/README.md` 표 끝에 한 줄을 붙인다(순서를 맞추려 애쓰지 않아도 된다 — `merge=union`):

```markdown
| [0015](0015-normalized-earnings-axis.md) | 정규화 이익(5년 평균)을 판정의 네 번째 축으로 더한다 | 채택됨 |
```

`CLAUDE.md`의 프로젝트 개요에서 아래 줄을

```
적정주가 **판정은 펀더멘털 3방법 삼각측량**(업종 상대가치·역사적 밴드·RIM)으로 내고,
```

아래로 바꾼다:

```
적정주가 **판정은 펀더멘털 4방법 삼각측량**(업종 상대가치·역사적 밴드·RIM·정규화 이익)으로 내고,
```

- [ ] **Step 4: ADR 인덱스를 확인한다**

Run: `.venv/Scripts/python.exe scripts/check_adr_index.py`
Expected: `문제 0`

- [ ] **Step 5: 커밋**

```bash
git add docs/adr CLAUDE.md
git commit -m "docs: ADR-0015 — 정규화 이익을 판정의 네 번째 축으로"
```

---

## Task 8: 전체 검증

- [ ] **Step 1: 전체 테스트**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 전부 통과. 실패가 있으면 **여기서 멈추고 고친다**.

- [ ] **Step 2: 헤드리스 3종**

```bash
.venv/Scripts/python.exe scripts/check_analysis.py KR 005930
.venv/Scripts/python.exe scripts/check_analysis.py KR 009310
.venv/Scripts/python.exe scripts/check_analysis.py US AAPL
```
Expected: 셋 다 오류 없이 완료.

- [ ] **Step 3: 성패 판정**

Run: `.venv/Scripts/python.exe scripts/check_normalized.py --limit 80`
Expected: 커버리지 `[확인]`, 효과 `[확인]`.

- [ ] **Step 4: 회귀 확인 — 기존 판정이 이유 없이 흔들리지 않았나**

Run: `.venv/Scripts/python.exe scripts/check_warranted.py KR`
Expected: `문제 0건` (⑤는 이 진단과 무관하므로 종전과 같아야 한다)

Run: `.venv/Scripts/python.exe scripts/check_design.py`
Expected: `문제 2` — **이 작업 전과 같은 2건**(네비 링크 규격·카드 그림자)이어야 한다.
3건이 되면 `stock.js` 변경이 새 문제를 만든 것이다.

- [ ] **Step 5: HANDOFF 갱신과 커밋**

`docs/HANDOFF.md`의 '다음에 할 것'에서 정규화 이익 항목을 지우고, '실행해서 확인한 것'
표에 커버리지와 효과 수치를 더한다. 한 줄 요약과 테스트 수도 갱신한다.

```bash
git add docs/HANDOFF.md
git commit -m "docs: ⑤ 정규화 이익까지 끝난 상태로 인계 문서를 갱신한다"
```

---

## 검증 체크리스트

구현이 끝났다고 말하기 전에 아래를 **실행하고 출력을 읽는다**.

- [ ] `.venv/Scripts/python.exe -m pytest tests/ -q` — 전부 통과
- [ ] `.venv/Scripts/python.exe scripts/check_normalized.py --limit 80` — 커버리지·효과 [확인]
- [ ] `.venv/Scripts/python.exe scripts/check_analysis.py KR 005930` — ⑤ 행이 뜬다
- [ ] `.venv/Scripts/python.exe scripts/check_analysis.py US AAPL` — 오류 없음
- [ ] `.venv/Scripts/python.exe scripts/check_adr_index.py` — 문제 0
- [ ] `.venv/Scripts/python.exe scripts/check_design.py` — 문제 2 (작업 전과 동일)
- [ ] 브라우저에서 ⑤ 행이 뜨고 근거가 접힌 채 보이며 4열 격자가 유지된다
- [ ] `normalized_ratio`가 1에 가까운 종목에서 판정이 종전과 크게 다르지 않다
