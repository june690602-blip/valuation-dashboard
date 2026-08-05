# EPV 지을 값이 있나 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EPV(정상 영업이익 × (1−t) ÷ WACC)가 판정 축으로 지을 값이 있는지 KR·US 두 시장에서 세 관문으로 재고, 합불을 ADR-0023에 남긴다. EPV를 판정에 넣지는 않는다.

**Architecture:** 순수 산식은 새 모듈 `src/analysis/epv.py`에 두고 단위 테스트로 못박는다(`compute_valuation`은 부르지 않는다 — 판정 경로 무변경). 네트워크 수집·표 출력은 `scripts/check_epv_viability.py`가 하고, 표본 층화와 게이트 분해는 기존 두 진단 스크립트에서 이름만 공개로 바꿔 그대로 가져다 쓴다(복제 금지).

**Tech Stack:** Python 3 · pandas · numpy · unittest · yfinance/OpenDART(기존 provider)

**설계문:** [`docs/superpowers/specs/2026-08-05-epv-viability-design.md`](../specs/2026-08-05-epv-viability-design.md)

**브랜치:** `diag/epv-viability` (origin/main `4b33fa4`에서 딴 것, 설계 커밋 `a9ee557`)

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/analysis/epv.py` | **신규.** 순수 함수 둘 — 정상 영업이익(창 규칙), EPV 주당가치(안 서면 None). 다른 어떤 모듈도 이것을 import하지 않는다 |
| `tests/test_epv.py` | **신규.** 위 두 함수의 단위 테스트. CI 관문에 들어간다 |
| `scripts/check_epv_viability.py` | **신규.** 표본 수집 → 검사 1·2·3 → 종합 합불 → 종료 코드. 네트워크 필요, CI 밖 |
| `scripts/check_confidence.py` | `_sample` → `sample_by_size` 이름만. 동작 무변경 |
| `scripts/check_dcf_viability.py` | `_gate_of` → `gate_of` 이름만. 동작 무변경 |
| `docs/adr/0023-….md` · `docs/adr/README.md` | 결과. 파일명은 합불이 정한다 |
| `docs/HANDOFF-CONFIDENCE.md` | 맨 위를 다음 사람용으로 교체 |

**건드리지 않는 것:** `src/analysis/valuation.py` · `src/analysis/warranted.py` · `web/` · `METHOD_WEIGHTS` · `METHOD_RHO` · `src/data/`

---

## Task 1: EPV 순수 산식 (`src/analysis/epv.py`)

**Files:**
- Create: `src/analysis/epv.py`
- Test: `tests/test_epv.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_epv.py` 전체:

```python
"""EPV 축 후보(ADR-0023 예정) 순수 함수 테스트.

실제 종목 전수 측정은 `scripts/check_epv_viability.py`가 한다(네트워크 필요).
여기서는 산식과 **안 서는 조건**만 본다 — 값을 못 내는 자리에서 억지로 숫자를
만들지 않는 것이 이 축의 핵심이다(ADR-0011).
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.analysis.epv import epv_per_share, normalized_operating_income


def _fin(values):
    """operating_income 열만 가진 연간 재무 프레임 (과거→최신)."""
    return pd.DataFrame({"operating_income": values},
                        index=range(2019, 2019 + len(values)))


class NormalizedOperatingIncomeTests(unittest.TestCase):
    def test_averages_the_last_five_years(self):
        # 6년이 있으면 마지막 5년만 쓴다 — `_normalized_earnings`와 같은 창 규칙이다
        oi, years = normalized_operating_income(_fin([9999., 100., 200., 300., 400., 500.]))
        self.assertEqual(years, 5)
        self.assertAlmostEqual(oi, 300.0)

    def test_uses_what_exists_when_history_is_short(self):
        oi, years = normalized_operating_income(_fin([100., 200., 300.]))
        self.assertEqual(years, 3)
        self.assertAlmostEqual(oi, 200.0)

    def test_refuses_when_fewer_than_min_years(self):
        oi, years = normalized_operating_income(_fin([100., 200.]))
        self.assertIsNone(oi)
        self.assertEqual(years, 2)

    def test_keeps_loss_years_in_the_window(self):
        # 적자 해를 빼면 정규화가 아니라 체리피킹이다. 사이클이 이 축이 감당할 대상이다.
        oi, years = normalized_operating_income(_fin([-200., 100., 200., 300., 400.]))
        self.assertEqual(years, 5)
        self.assertAlmostEqual(oi, 160.0)

    def test_drops_missing_and_infinite_inside_the_window(self):
        oi, years = normalized_operating_income(_fin([100., np.nan, np.inf, 300., 500.]))
        self.assertEqual(years, 3)
        self.assertAlmostEqual(oi, 300.0)

    def test_returns_none_without_the_column(self):
        oi, years = normalized_operating_income(pd.DataFrame({"net_income": [1., 2., 3.]}))
        self.assertIsNone(oi)
        self.assertEqual(years, 0)


class EpvPerShareTests(unittest.TestCase):
    def test_known_inputs(self):
        # 기업가치 100×0.8/0.08 = 1,000 · 주주가치 1,000 − 150 = 850 · 주당 85.0
        v = epv_per_share(op_income=100.0, tax_rate=0.20, wacc=0.08,
                          net_debt=150.0, shares=10.0)
        self.assertAlmostEqual(v, 85.0)

    def test_net_cash_raises_the_value(self):
        # 순부채가 음수(현금이 차입금보다 많음)면 주주가치가 기업가치보다 크다
        v = epv_per_share(op_income=100.0, tax_rate=0.20, wacc=0.08,
                          net_debt=-200.0, shares=10.0)
        self.assertAlmostEqual(v, 120.0)

    def test_refuses_when_operating_income_is_not_positive(self):
        for oi in (0.0, -50.0, None):
            with self.subTest(oi=oi):
                self.assertIsNone(epv_per_share(oi, 0.2, 0.08, 0.0, 10.0))

    def test_refuses_without_wacc(self):
        self.assertIsNone(epv_per_share(100.0, 0.2, None, 0.0, 10.0))

    def test_refuses_when_wacc_is_not_positive(self):
        # 할인율이 0 이하면 영구가치가 발산하거나 부호가 뒤집힌다
        for w in (0.0, -0.01):
            with self.subTest(wacc=w):
                self.assertIsNone(epv_per_share(100.0, 0.2, w, 0.0, 10.0))

    def test_refuses_without_usable_tax_rate(self):
        for t in (None, -0.1, 1.0, 1.5):
            with self.subTest(tax=t):
                self.assertIsNone(epv_per_share(100.0, t, 0.08, 0.0, 10.0))

    def test_refuses_when_debt_exceeds_enterprise_value(self):
        # 기업가치 1,000인데 순부채 1,200 → 주주가치 음수. 음수 적정주가는 판정에 못 쓴다.
        self.assertIsNone(epv_per_share(100.0, 0.20, 0.08, 1200.0, 10.0))

    def test_refuses_without_shares(self):
        for s in (None, 0.0, -10.0):
            with self.subTest(shares=s):
                self.assertIsNone(epv_per_share(100.0, 0.2, 0.08, 0.0, s))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_epv.py -q
```

기대: `ModuleNotFoundError: No module named 'src.analysis.epv'` — 수집 단계에서 실패한다.

- [ ] **Step 3: 최소 구현을 쓴다**

`src/analysis/epv.py` 전체:

```python
"""EPV(Earnings Power Value) 축 후보 — 순수 산식 (ADR-0016 결정 2).

**이 모듈은 판정에 쓰이지 않는다.** `compute_valuation`이 부르지 않고, 지금은
`scripts/check_epv_viability.py`만 import한다. EPV를 축으로 지을 값이 있는지 재는
것이 먼저이고(ADR-0016 결정 2: *"통과하지 못하면 짓지 않는다"*), 재는 코드가 곧
근거가 되게 `src/`에 둔다 — ADR-0022 결정 5가 `loo_leg_error`에 같은 선택을 했다.
스크립트에 두면 시험할 자리가 없어진다(이 저장소의 테스트는 `scripts/`를 import하는
것이 하나도 없다).

EPV는 *"지금 버는 만큼만 영원히 번다"*고 볼 때의 값이라 **성장 가정이 0개**다.
그것이 ADR-0016이 DCF 대신 EPV를 후보로 고른 이유이므로, Greenwald 원안의 유지보수
capex·일회성 손익·초과현금 조정은 넣지 않는다. 조정을 넣는 순간 가정이 생기고
고른 이유가 사라진다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.analysis.valuation import NORMALIZE_MIN_YEARS, NORMALIZE_WINDOW


def normalized_operating_income(fin) -> tuple[float | None, int]:
    """(정상 영업이익, 평균에 쓴 연수). 창을 못 채우면 (None, 실제 개수).

    `_normalized_earnings`(⑤)와 **같은 창 규칙**을 영업이익에 쓴다 — 창 크기 상수를
    그쪽에서 가져오므로 한쪽만 바뀌는 일이 없다. **적자 연도를 빼지 않는 것도 그대로다.**
    빼면 정규화가 아니라 체리피킹이고, 사이클이 이 축이 감당해야 할 대상이다.
    """
    if fin is None or not hasattr(fin, "columns") or "operating_income" not in fin.columns:
        return None, 0
    win = pd.to_numeric(fin["operating_income"], errors="coerce").iloc[-NORMALIZE_WINDOW:]
    win = win[np.isfinite(win)]
    if len(win) < NORMALIZE_MIN_YEARS:
        return None, int(len(win))
    return float(win.mean()), int(len(win))


def epv_per_share(op_income: float | None, tax_rate: float | None,
                  wacc: float | None, net_debt: float,
                  shares: float | None) -> float | None:
    """EPV 주당가치. 설 수 없으면 **None** (ADR-0011: 오염된 값보다 계산 불가).

    ::

        기업가치 = 정상 영업이익 × (1 − 세율) ÷ WACC
        주주가치 = 기업가치 − 순부채
        주당     = 주주가치 ÷ 주식수

    `net_debt`은 총차입금 − 현금이다. 기존 EV/EBITDA 다리가 쓰는 다리 건너기와 같은
    식이라(`valuation.py`의 `m * ebitda_ps - debt_ps + cash_ps`) 같은 자리를 두 자로
    재지 않는다.

    할인율이 주주 요구수익률(k_e)이 아니라 **WACC**인 것은 세후 영업이익이 채권자와
    주주 모두에게 가는 흐름이기 때문이다. 그래서 순부채를 뒤에서 뺀다.

    안 서는 자리 넷 — 어느 것도 대체값으로 메우지 않는다:

    1. 정상 영업이익이 양수가 아님 (적자 회사는 이 축으로 못 구한다)
    2. WACC가 없거나 양수가 아님 (0 이하면 영구가치가 발산하거나 부호가 뒤집힌다)
    3. 세율이 없거나 [0, 1) 밖
    4. **주주가치가 양수가 아님** — 순부채가 기업가치보다 크다. 음수 적정주가는
       판정에 못 쓴다. ADR-0016의 `epv_ready`(정상 영업이익 > 0 & WACC 있음)에는
       없던 조건이라, 이번 커버리지가 그 문서의 64%보다 낮게 나올 수 있다
    """
    if op_income is None or not np.isfinite(op_income) or op_income <= 0:
        return None
    if wacc is None or not np.isfinite(wacc) or wacc <= 0:
        return None
    if tax_rate is None or not np.isfinite(tax_rate) or not 0.0 <= tax_rate < 1.0:
        return None
    if not shares or not np.isfinite(shares) or shares <= 0:
        return None
    equity = op_income * (1.0 - tax_rate) / wacc - float(net_debt or 0.0)
    if not np.isfinite(equity) or equity <= 0:
        return None
    return equity / float(shares)
```

- [ ] **Step 4: 통과를 확인한다**

```bash
.venv/Scripts/python.exe -m pytest tests/test_epv.py -q
```

기대: `19 passed` 대의 결과에 실패 0. (subTest가 있어 표시 개수는 다를 수 있다 — **실패 0인지만 본다.**)

- [ ] **Step 5: 판정이 안 바뀐 것을 확인한다**

```bash
.venv/Scripts/python.exe scripts/check_all.py
```

기대: `통과 11 · 실패 0 · 건너뜀 0`. 새 모듈을 아무도 부르지 않으므로 기존 동작은 그대로여야 한다.

- [ ] **Step 6: 커밋**

```bash
git add src/analysis/epv.py tests/test_epv.py
git commit -m "feat(epv): EPV 순수 산식을 넣는다 — 판정은 아직 부르지 않는다"
```

커밋 본문에 적을 것: 왜 `src/`인가(ADR-0022 결정 5와 같은 이유) · 안 서는 조건 넷 · 4번(주주가치 음수)이 ADR-0016 `epv_ready`에 없던 조건이라 커버리지가 낮아질 수 있다는 것.

---

## Task 2: 기존 두 스크립트에서 재사용할 이름을 공개로 바꾼다

복제를 막으려는 것뿐이다. **동작은 한 글자도 바뀌지 않는다.**

**Files:**
- Modify: `scripts/check_confidence.py` (`_sample` 정의 1곳 + 호출 1곳)
- Modify: `scripts/check_dcf_viability.py` (`_gate_of` 정의 1곳 + 호출 1곳)

- [ ] **Step 1: `check_confidence.py`의 `_sample`을 공개 이름으로**

정의부(`def _sample(market: str, limit: int) -> pd.DataFrame:`)를 바꾼다:

```python
def sample_by_size(market: str, limit: int) -> pd.DataFrame:
```

docstring 첫 줄 끝에 한 문장을 덧붙인다:

```python
    """(code, 층) — **규모로 층화한** 표본. 상위 N만 쓰면 대형주만 잡혀 상관이 왜곡된다.

    `check_epv_viability.py`가 같은 표본을 써야 두 진단을 나란히 읽을 수 있어 공개 이름이다.
```

호출부 한 곳(`main()` 안 `samp = _sample(market, args.limit)`)을 바꾼다:

```python
    samp = sample_by_size(market, args.limit)
```

- [ ] **Step 2: `check_dcf_viability.py`의 `_gate_of`를 공개 이름으로**

정의부와 호출부:

```python
def gate_of(reason: str) -> str:
    """RIM 제외 사유 문장을 게이트 라벨로. `check_epv_viability.py`도 같은 분류를 쓴다."""
    for label, needle in GATES:
        if needle in reason:
            return label
    return f"미분류: {reason}"
```

호출부(`_one()` 안):

```python
        "gate": "" if has_rim else gate_of(rim_reason),
```

- [ ] **Step 3: 남은 옛 이름이 없는지 확인한다**

```bash
grep -rn "_sample(\|_gate_of(" scripts/
```

기대: **출력 없음.**

- [ ] **Step 4: 관문을 돌린다**

```bash
.venv/Scripts/python.exe scripts/check_all.py
```

기대: `통과 11 · 실패 0`.

- [ ] **Step 5: 커밋**

```bash
git add scripts/check_confidence.py scripts/check_dcf_viability.py
git commit -m "refactor(scripts): 재사용할 표본·게이트 함수를 공개 이름으로 (동작 무변경)"
```

---

## Task 3: 수집 골격 + 표본 구성 + 검사 1 (커버리지)

**Files:**
- Create: `scripts/check_epv_viability.py`

- [ ] **Step 1: 파일을 만든다 — 수집까지**

```python
"""EPV를 축으로 지을 값이 있나 — 관문 셋 (ADR-0016 결정 2, ADR-0023 예정).

    python scripts/check_epv_viability.py KR --limit 200
    python scripts/check_epv_viability.py US --limit 200

ADR-0016은 EPV를 **채택**한 것이 아니라 **후보로 지정**했다. 원문이 조건을 달았다 —
*"짓기 전에 검사 2를 EPV에 대해 돌린다 … 통과하지 못하면 짓지 않는다."* 같은 문서가
DCF를 그렇게 기각했다(검사 1 미달 29%). **기각도 정당한 결과다.**

관문 셋. 재기 전에 정한 값이고, 나온 숫자를 보고 바꾸지 않는다:

  검사 1  RIM이 빠진 종목 중 EPV가 서는 비율          ≥ 50%          ADR-0016 원문
  검사 2  기존 4방법과의 상관 최대 |피어슨| · 1.5배 밖  ≤ 0.5 · ≥ 25%  ADR-0016 원문
  검사 3  EPV 넣기 전/후 n_eff 중앙값                  안 내려갈 것    ADR-0023에서 신설

**검사 3은 ADR-0022가 만든 조건이다.** EPV는 ③ RIM과 같은 칸(이익 기반 영구가치)이고,
미국에서 그 칸의 두 방법(③ RIM ↔ ⑤ 정규화 이익)이 **+0.723**이다. 설계효과 식은 평균
상관만 보므로 EPV가 검사 2를 통과해도 `n_eff`를 내릴 수 있다 — ADR-0022 한계 절의
*"방법을 더 쓴 종목이 더 낮게 나올 수 있다"*.

**시장마다 따로 잰다.** ADR-0022에서 시장이 결론을 두 번 뒤집었다(①↔⑤ 한국 +0.263 대
미국 +0.763 · ③↔⑤ −0.015 대 +0.723). 한쪽 값을 다른 쪽에 옮겨 쓰지 않는다(ADR-0017).

네트워크가 필요하다. CI가 아니라 check_dcf_viability.py·check_confidence.py와 같은
수동 계열이다. 종료 코드 1은 "관문 미달"이라는 뜻이고, 그것도 결과다.
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from check_confidence import sample_by_size                            # noqa: E402
from check_dcf_viability import gate_of                                # noqa: E402
from src.analysis.capital_cost import compute_capital_cost             # noqa: E402
from src.analysis.epv import (epv_per_share,                           # noqa: E402
                              normalized_operating_income)
from src.analysis.indicators import compute_indicators                 # noqa: E402
from src.analysis.valuation import (FUNDAMENTAL_METHODS,               # noqa: E402
                                    METHOD_WEIGHTS, compute_valuation)
from src.analysis.warranted import METHOD_RHO, effective_axes          # noqa: E402
from src.data.kr_provider import KRProvider                            # noqa: E402
from src.data.universe_multiples import coefficients_or_none           # noqa: E402
from src.data.us_provider import USProvider                            # noqa: E402

# check_analysis.py·check_confidence.py와 **같은 가정**. 갈라지면 진단이 화면과 다른
# 자본비용으로 재게 되고, EPV는 WACC에 직접 매달린다.
RF_MRP = {"KR": (0.035, 0.06), "US": (0.045, 0.05)}
MAX_WORKERS = 2                # yfinance 한도를 덜 때린다
RETRIES, BACKOFF = 3, 4.0      # 401 Invalid Crumb 재시도 (초)
MIN_PAIR_N = 30                # 이보다 적은 쌍은 상관을 내지 않는다 (ADR-0011)

EPV = "EPV"                    # 상관표·n_eff에서 쓸 방법 이름
RIM = "수익가치(RIM)"

# 관문 — 앞의 둘은 ADR-0016이 정한 값을 그대로 옮긴 것이다.
COMPLEMENT_FLOOR = 0.50        # 검사 1
CORR_CEILING = 0.50            # 검사 2
DIVERGE_FLOOR = 0.25           # 검사 2
DIVERGE_FOLD = 1.5             # '갈렸다'고 볼 배수


def _one(code: str, coef, market: str) -> dict | None:
    """한 종목의 판정 + 방법별 로그 괴리율 + EPV. 실패하면 None.

    **재시도가 있는 이유가 있다.** yfinance가 `401 Invalid Crumb`으로 자주 거절하는데,
    재시도 없이 돌렸더니 50종목 중 **0곳**이 나온 적이 있다(check_confidence.py 주석).
    """
    provider = KRProvider() if market == "KR" else USProvider()
    rf, mrp = RF_MRP[market]
    for attempt in range(RETRIES):
        try:
            d = provider.load(code, peer_count=9)
            ind = compute_indicators(d)
            cc = compute_capital_cost(d, rf=rf, mrp=mrp)
            v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
            break
        except Exception:
            if attempt == RETRIES - 1:
                return None
            time.sleep(BACKOFF * (attempt + 1))
    if v.verdict is None or not d.price or not v.fundamental_only:
        return None

    oi, oi_years = normalized_operating_income(d.financials)
    net_debt = (d.latest("total_debt") or 0.0) - (d.latest("cash") or 0.0)
    epv = epv_per_share(oi, cc.tax_rate, cc.wacc, net_debt, d.shares_outstanding)

    mids = {e.method: e.mid for e in v.estimates if e.method in FUNDAMENTAL_METHODS}
    has_rim = RIM in mids
    row = {
        "code": code, "mcap": d.market_cap, "fair_mid": v.fair_mid,
        "has_rim": has_rim,
        "gate": "" if has_rim else gate_of(
            next((r for m, r in v.skipped if m == RIM), "")),
        # 성립 조건을 갈라서 센다 — 어디서 막혔는지 모르면 고칠 자리를 못 찾는다
        "oi_pos": bool(oi is not None and oi > 0),
        "wacc_ok": cc.wacc is not None,
        "epv": epv,
        "epv_ready": epv is not None,
        "methods": [m for m in (v.weights or {}) if m in FUNDAMENTAL_METHODS],
        "oi_years": oi_years,
    }
    # **로그 괴리율.** 로그인 이유는 ADR-0014·0022와 같다 — 배수는 곱셈 스케일이라
    # 원 스케일에서 재면 비싼 종목의 큰 괴리가 상관을 끌고 간다.
    for m, mid in mids.items():
        if mid and mid > 0:
            row[m] = float(np.log(mid / d.price))
    if epv and epv > 0:
        row[EPV] = float(np.log(epv / d.price))
    return row


def _collect(market: str, limit: int, coef) -> tuple[pd.DataFrame, int]:
    """(수집 결과, 시도 종목 수). 표본 틀은 check_confidence.py와 **같은 것**을 쓴다."""
    samp = sample_by_size(market, limit)
    print(f"{market} {len(samp)}종목 수집 중…\n")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        rows = [r for r in ex.map(lambda c: _one(c, coef, market), samp["code"]) if r]
    if not rows:
        return pd.DataFrame(), len(samp)
    return pd.DataFrame(rows).merge(samp, on="code", how="left"), len(samp)
```

- [ ] **Step 2: 표본 구성 + 검사 1 출력을 더한다**

같은 파일 아래에 이어 쓴다:

```python
def _print_sample(df: pd.DataFrame, tried: int) -> None:
    """표본이 어디서 왔나. 성공률을 반드시 함께 찍는다 — 원천이 불안정해 실행마다 다르고,
    그 사실을 모르면 숫자를 과신하게 된다."""
    print(f"판정 {len(df)}종목 / 시도 {tried}종목 (성공률 {len(df)/tried:.0%})")
    # 층이 실제로 규모를 갈랐나 — **주장하지 않고 실현 시총으로 보인다.** 미국은 층을
    # 시총이 아니라 지수로 만들었으므로 이 확인이 특히 필요하다.
    print(f"\n{'표본 층':<12}{'종목':>6}{'시총 중앙값(10억)':>20}")
    print("─" * 38)
    for tier, g in df.groupby("층", observed=True):
        print(f"{tier:<12}{len(g):>6}{g['mcap'].median() / 1e9:>20,.1f}")


def _check1(df: pd.DataFrame) -> tuple[bool, float]:
    """검사 1 — RIM이 빠진 자리에서 EPV가 서는가. (합불, 비율)"""
    no_rim = df[~df["has_rim"]]
    has = df[df["has_rim"]]
    print("\n[검사 1] 커버리지 상보성 — EPV는 RIM이 빠진 그 자리에서 서는가")
    print(f"    RIM이 선 종목 {int(df['has_rim'].sum())} · "
          f"빠진 종목 {len(no_rim)} ({1 - df['has_rim'].mean():.0%})")
    if len(no_rim):
        print("\n    ③ RIM 제외 사유:")
        for label, n in no_rim["gate"].value_counts().items():
            print(f"      {n:>3}곳 ({n / len(df):>4.0%})  {label}")
    print(f"\n    {'항목':<26}{'전체':>10}{'RIM 빠짐':>12}{'RIM 있음':>12}")
    print("    " + "─" * 60)
    for label, col in [("정상 영업이익 > 0", "oi_pos"), ("WACC 계산됨", "wacc_ok"),
                       ("EPV 설 수 있음", "epv_ready")]:
        b = no_rim[col].mean() if len(no_rim) else np.nan
        c = has[col].mean() if len(has) else np.nan
        print(f"    {label:<26}{df[col].mean():>10.0%}{b:>12.0%}{c:>12.0%}")
    comp = float(no_rim["epv_ready"].mean()) if len(no_rim) else float("nan")
    ok = bool(pd.notna(comp) and comp >= COMPLEMENT_FLOOR)
    print(f"\n    ★ RIM이 빠진 {len(no_rim)}종목 중 EPV가 서는 비율 {comp:.0%}  "
          f"{'[확인]' if ok else '[문제]'} (기준선 {COMPLEMENT_FLOOR:.0%})")
    print("      ADR-0016은 이 검사로 DCF를 기각했다(29%). 같은 문서 표로 EPV를 계산하면\n"
          "      31/75 = 41%인데, 그 문서는 이 각도로 EPV를 본 적이 없다.\n"
          "      **여기 나온 값이 그 41%보다 우선한다** — 순부채 조건이 더 붙었다.")
    return ok, comp
```

- [ ] **Step 3: `main()`을 임시로 두고 검사 1까지 돌려 본다**

파일 끝에 더한다(뒤 Task에서 채운다):

```python
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("market", nargs="?", default="KR", choices=["KR", "US"])
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    market = args.market

    coef = coefficients_or_none(market)
    if coef is None:
        print("계수를 만들지 못했다 — 회귀 경로가 아니면 이 측정은 뜻이 없다.")
        return 1
    df, tried = _collect(market, args.limit, coef)
    if len(df) < MIN_PAIR_N:
        print(f"판정이 난 종목이 {len(df)}곳뿐이다 — 관문을 걸 표본이 아니다.")
        return 1
    _print_sample(df, tried)
    ok1, _ = _check1(df)
    return 0 if ok1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 작은 표본으로 실제 실행한다**

```bash
.venv/Scripts/python.exe scripts/check_epv_viability.py KR --limit 40
```

기대: 표본 층 표 + 제외 사유 + 검사 1의 `[확인]`/`[문제]` 한 줄이 찍힌다. **네트워크가 필요하고 몇 분 걸린다.** 성공률이 0%면 재시도·워커 설정을 확인한다.

- [ ] **Step 5: 커밋**

```bash
git add scripts/check_epv_viability.py
git commit -m "diag(epv): 표본 수집과 검사 1(커버리지 상보성)"
```

---

## Task 4: 검사 2 (독립성)

**Files:**
- Modify: `scripts/check_epv_viability.py`

- [ ] **Step 1: 상관표와 1.5배 밖을 재는 함수를 더한다**

`_check1` 아래에 이어 쓴다:

```python
def _corr_table(df: pd.DataFrame) -> tuple[dict, list]:
    """방법 쌍별 (피어슨, 스피어만, n). EPV를 포함한 5방법 전 쌍.

    키 순서는 `effective_axes`가 만드는 것과 같아야 한다 — 그쪽이 `sorted(set(methods))`
    뒤 `(ms[i], ms[j])`로 찾으므로 여기서도 정렬 뒤 조합한다. 어긋나면 상관을 넣어 놓고
    '모르는 쌍'으로 처리돼 상한이 조용히 꺼진다(ADR-0022 결정 3).
    """
    table, rows = {}, []
    for a, b in itertools.combinations(sorted((*FUNDAMENTAL_METHODS, EPV)), 2):
        if a not in df.columns or b not in df.columns:
            rows.append((a, b, 0, None, None))
            continue
        both = df[[a, b]].dropna()
        if len(both) < MIN_PAIR_N:
            rows.append((a, b, len(both), None, None))
            continue
        r = float(both[a].corr(both[b]))
        s = float(both[a].rank().corr(both[b].rank()))
        table[(a, b)] = r
        rows.append((a, b, len(both), r, s))
    return table, rows


def _check2(df: pd.DataFrame, rows: list) -> tuple[bool, dict]:
    """검사 2 — EPV가 기존 축과 다른 말을 하는가. (합불, 메타)

    **'1.5배 밖'은 ADR-0016과 다른 통계량이다.** 그 문서는 DCF 값을 못 내 재료의 흩어짐을
    쟀다(`log(정상FCF/정상순이익)`, std 1.040 · 59%). EPV는 가정이 0개라 축 자체를 낼 수
    있으므로, 이 축이 판정을 실제로 움직이는지를 직접 잰다. 기준선 25%는 그대로 둔다 —
    재기 전에 정한 값을 나온 숫자를 보고 바꾸지 않는다.
    """
    print("\n[검사 2] 독립성 — EPV가 기존 축과 다른 말을 하는가")
    print(f"    {'상대 축':<16}{'표본':>7}{'피어슨':>10}{'스피어만':>11}")
    print("    " + "─" * 44)
    epv_rows = [r for r in rows if EPV in (r[0], r[1])]
    worst, measured, missing = 0.0, 0, []
    for a, b, n, r, s in epv_rows:
        other = b if a == EPV else a
        if r is None:
            missing.append(other)
            print(f"    {other:<16}{n:>7}{'표본부족':>10}{'—':>11}")
            continue
        measured += 1
        worst = max(worst, abs(r))
        print(f"    {other:<16}{n:>7}{r:>+10.3f}{s:>+11.3f}")
    if missing:
        print(f"    ※ 표본이 {MIN_PAIR_N} 미만이라 뺀 쌍: {' · '.join(missing)}")

    # **`to_numeric`가 필요하다.** `epv` 열은 EPV가 안 서는 종목에서 None이라 dtype이
    # object가 되고, object 열에 `> 0`을 하면 TypeError가 난다.
    sub = pd.DataFrame({"epv": pd.to_numeric(df["epv"], errors="coerce"),
                        "fair_mid": pd.to_numeric(df["fair_mid"], errors="coerce")}).dropna()
    sub = sub[(sub["epv"] > 0) & (sub["fair_mid"] > 0)]
    far = (float((np.abs(np.log(sub["epv"] / sub["fair_mid"])) > np.log(DIVERGE_FOLD)).mean())
           if len(sub) else float("nan"))
    print(f"\n    EPV가 종합 판정과 {DIVERGE_FOLD}배 넘게 갈린 비율 "
          f"{far:.0%}  (n={len(sub)})")

    if measured == 0:
        print(f"\n    [문제] EPV 쌍이 전부 표본부족이다 — 검사 2는 **판정 불가**이고 "
              "통과로 치지 않는다.")
        return False, {"worst": None, "far": far, "measured": 0}
    ok = bool(worst <= CORR_CEILING and pd.notna(far) and far >= DIVERGE_FLOOR)
    print(f"    {'[확인]' if ok else '[문제]'} 최대 |피어슨| {worst:.3f} (≤{CORR_CEILING}) · "
          f"{DIVERGE_FOLD}배 밖 {far:.0%} (≥{DIVERGE_FLOOR:.0%})")
    print("      상관이 높으면 EPV는 기존 축과 같은 말을 한다 — 축을 더해도 정보가 안 는다.\n"
          "      안 갈리면 판정이 안 바뀐다. ADR-0015가 ⑤에 걸었던 것과 같은 조건이다.")
    return ok, {"worst": worst, "far": far, "measured": measured}


def _print_all_pairs(rows: list) -> None:
    """기존 6쌍도 함께 찍는다 — **EPV에만 0.5를 들이대고 있다는 사실이 보여야 한다.**
    ADR-0022 실측에서 ③RIM↔① 한국 +0.787 · 미국 +0.706으로 이미 상한을 넘는다.
    합불은 이 줄들로 가르지 않는다."""
    print("\n[참고] 기존 6쌍 — 합불에는 안 쓴다")
    print(f"    {'방법 A':<16}{'방법 B':<16}{'표본':>7}{'피어슨':>10}")
    print("    " + "─" * 49)
    for a, b, n, r, _s in rows:
        if EPV in (a, b):
            continue
        print(f"    {a:<16}{b:<16}{n:>7}"
              f"{(f'{r:+.3f}' if r is not None else '표본부족'):>10}")
```

- [ ] **Step 2: `main()`에 검사 2를 끼운다**

`ok1, _ = _check1(df)` 다음 줄부터 교체:

```python
    ok1, _ = _check1(df)
    table, rows = _corr_table(df)
    ok2, _meta2 = _check2(df, rows)
    _print_all_pairs(rows)
    bad = (0 if ok1 else 1) + (0 if ok2 else 1)
    return 1 if bad else 0
```

- [ ] **Step 3: 돌려 본다**

```bash
.venv/Scripts/python.exe scripts/check_epv_viability.py KR --limit 60
```

기대: 검사 1 → 검사 2 표(EPV 4쌍) → 1.5배 밖 비율 → 기존 6쌍 참고표. 표본이 얇으면 '표본부족'이 뜨는 것이 정상이다.

- [ ] **Step 4: 커밋**

```bash
git add scripts/check_epv_viability.py
git commit -m "diag(epv): 검사 2(독립성) — 상관 4쌍과 1.5배 밖"
```

---

## Task 5: 검사 3 (n_eff 전후)

**Files:**
- Modify: `scripts/check_epv_viability.py`

- [ ] **Step 1: 검사 3을 더한다**

`_print_all_pairs` 아래에 이어 쓴다:

```python
def _check3(df: pd.DataFrame, table: dict, market: str) -> tuple[bool, dict]:
    """검사 3 — EPV를 넣으면 실질 축 수가 내려가지 않는가. (합불, 메타)

    ADR-0022가 만든 조건이다. EPV는 ③ RIM과 같은 칸이고 미국에서 그 칸의 두 방법이
    +0.723이다. 설계효과 식은 **평균 상관**만 보므로, 겹치는 축을 더하면 `n_eff`가
    내려갈 수 있다 — 그 문서 한계 절의 *"방법을 더 쓴 종목이 더 낮게 나올 수 있다"*.

    `effective_axes`를 **다시 구현하지 않고 그대로 부른다.** 갈라지면 "진단은 통과인데
    화면은 다른 말"이 된다(check_confidence.py가 `confidence_grade`에 같은 선택을 했다).
    """
    print("\n[검사 3] 정직도 — EPV를 넣으면 실질 축 수가 내려가지 않는가")
    need = [(a, b) for a, b in table if EPV in (a, b)]
    if len(need) < len(FUNDAMENTAL_METHODS):
        print(f"    [문제] EPV 쌍 {len(FUNDAMENTAL_METHODS)}개 중 {len(need)}개만 쟀다 — "
              "모르는 쌍이 있으면 `effective_axes`가 상한을 통째로 끄므로(ADR-0022 결정 3)\n"
              "    비교가 뜻을 잃는다. **판정 불가**이고 통과로 치지 않는다.")
        return False, {"before": None, "after": None}

    # 기존 6쌍은 이미 채택된 `METHOD_RHO`를 쓴다. 이번에 잰 값으로 바꾸면 검사 3이
    # 상관 재측정까지 겸하게 되고, 그건 이 관문이 묻는 것이 아니다.
    merged = dict(METHOD_RHO.get(market.upper(), {}))
    merged.update({k: v for k, v in table.items() if EPV in k})

    # **EPV가 서는 종목에서만 비교한다.** 안 서는 종목은 정의상 전=후라 중앙값을 희석시킨다.
    sub = df[df["epv_ready"]].copy()
    if len(sub) < MIN_PAIR_N:
        print(f"    [문제] EPV가 서는 종목이 {len(sub)}곳뿐이다 — 비교할 표본이 아니다.")
        return False, {"before": None, "after": None}

    before, after = [], []
    for ms in sub["methods"]:
        b, b_ok = effective_axes(ms, market, merged)
        a, a_ok = effective_axes([*ms, EPV], market, merged)
        before.append(b if b_ok else np.nan)
        after.append(a if a_ok else np.nan)
    sub["before"], sub["after"] = before, after
    sub["n"] = sub["methods"].map(len)
    sub = sub.dropna(subset=["before", "after"])
    m_before, m_after = float(sub["before"].median()), float(sub["after"].median())
    up = int((sub["after"] > sub["before"] + 1e-9).sum())
    down = int((sub["after"] < sub["before"] - 1e-9).sum())

    print(f"    대상 {len(sub)}종목 (EPV가 서는 종목)")
    print(f"    {'방법 수':>7}{'종목':>7}{'전 중앙':>10}{'후 중앙':>10}{'차이':>10}")
    print("    " + "─" * 44)
    for n, g in sub.groupby("n"):
        print(f"    {n:>7}{len(g):>7}{g['before'].median():>10.2f}"
              f"{g['after'].median():>10.2f}"
              f"{g['after'].median() - g['before'].median():>+10.2f}")
    print(f"\n    전체 중앙 {m_before:.2f} → {m_after:.2f} "
          f"({m_after - m_before:+.2f}) · 오른 종목 {up} · 내린 종목 {down}")
    ok = bool(m_after >= m_before)
    print(f"    {'[확인]' if ok else '[문제]'} 중앙값이 "
          f"{'내려가지 않았다' if ok else '내려갔다'}")
    print("      내려가면 EPV는 축을 늘리면서 '이 판정이 독립적 근거 위에 있다'는 말을\n"
          "      오히려 약하게 만든다. 그 자리에서 축을 더할 이유가 없다.")
    return ok, {"before": m_before, "after": m_after, "up": up, "down": down}
```

> `before`/`after`가 NaN인 행은 버린다 — `effective_axes`가 `capped=False`를 돌려준
> 경우이고, 위에서 EPV 쌍을 다 확인했으므로 정상적으로는 안 나온다. 방어선으로 남긴다.

- [ ] **Step 2: `main()`에 끼운다**

```python
    ok1, _ = _check1(df)
    table, rows = _corr_table(df)
    ok2, _meta2 = _check2(df, rows)
    _print_all_pairs(rows)
    ok3, _meta3 = _check3(df, table, market)
    bad = (0 if ok1 else 1) + (0 if ok2 else 1) + (0 if ok3 else 1)
    return 1 if bad else 0
```

- [ ] **Step 3: 돌려 본다**

```bash
.venv/Scripts/python.exe scripts/check_epv_viability.py KR --limit 60
```

기대: 검사 3 표가 방법 수별로 나오고 마지막에 `[확인]`/`[문제]` 한 줄.

- [ ] **Step 4: 커밋**

```bash
git add scripts/check_epv_viability.py
git commit -m "diag(epv): 검사 3(정직도) — EPV 전후 실질 축 수"
```

---

## Task 6: 실효 비중·붙여넣을 줄·종합 합불

**Files:**
- Modify: `scripts/check_epv_viability.py`

- [ ] **Step 1: 참고 절과 종합을 더한다**

`_check3` 아래:

```python
# EPV의 가중은 **③과 같은 칸이므로 ③의 값을 가정으로 쓴다.** 지어낸 값이 아니라 '같은
# 성격의 방법에 이미 준 값'이고, ADR-0016의 WCOL이 같은 가정을 썼다. 가정임을 밝힌다.
EPV_WEIGHT = METHOD_WEIGHTS[RIM]
INTRINSIC = {RIM, EPV}


def _abs_share(methods, with_epv: bool) -> float:
    """이 종목의 **실효** 절대가치 비중 (빠진 방법은 재정규화)."""
    w = {m: METHOD_WEIGHTS[m] for m in methods}
    if with_epv:
        w[EPV] = EPV_WEIGHT
    s = sum(w.values())
    if not s:
        return float("nan")
    return sum(v for m, v in w.items() if m in INTRINSIC) / s


def _print_share(df: pd.DataFrame) -> None:
    """합불은 안 가르지만 ADR에 적을 것 — EPV가 절대가치 비중을 얼마나 올리나."""
    print("\n[참고] 절대가치 실효 비중 — 합불에는 안 쓴다")
    print(f"    {'축 구성':<18}{'절대축 있음':>12}{'실효 비중 평균':>16}{'중앙':>8}")
    print("    " + "─" * 54)
    for label, with_epv in (("현재 ①②③⑤", False), ("현재 + EPV", True)):
        have, shares = [], []
        for _i, r in df.iterrows():
            ms = list(r["methods"])
            ok_epv = with_epv and bool(r["epv_ready"])
            have.append(bool(RIM in ms or ok_epv))
            shares.append(_abs_share(ms, ok_epv))
        s = pd.Series(shares)
        print(f"    {label:<18}{np.mean(have):>12.0%}{s.mean():>16.1%}{s.median():>8.1%}")
    print(f"    ※ EPV 가중은 ③의 {EPV_WEIGHT}을 빌린 **가정**이다(ADR-0016과 같다).")

    left = df[~df["has_rim"] & ~df["epv_ready"]]
    if len(left):
        print(f"\n    끝내 절대가치가 없는 {len(left)}곳({len(left)/len(df):.0%})의 정체:")
        for label, n in left["gate"].value_counts().items():
            print(f"      {n:>3}곳  {label}")
        print("      모형으로 풀 자리가 아니다 — 화면이 그 사실을 말해야 한다(ADR-0016 결정 4).")


def _print_rho_lines(table: dict, market: str) -> None:
    """**지을 때 붙여넣을 줄.** 이번에 축을 짓지는 않지만, 측정을 코드로 남기는 것이
    이 저장소의 규칙이다 — LEG_MAE의 원래 값을 만든 스크립트가 없어서 ADR-0014의 β
    불일치를 끝내 못 밝힌 적이 있다(ADR-0022 결정 5)."""
    pairs = {k: v for k, v in table.items() if EPV in k}
    if not pairs:
        return
    print(f"\n[E] EPV를 지을 때 `warranted.METHOD_RHO[\"{market}\"]`에 더할 줄")
    for (a, b), r in pairs.items():
        print(f'        ("{a}", "{b}"): {r:.3f},')
```

- [ ] **Step 2: `main()`을 최종형으로**

```python
    _print_sample(df, tried)
    ok1, _ = _check1(df)
    table, rows = _corr_table(df)
    ok2, _m2 = _check2(df, rows)
    _print_all_pairs(rows)
    ok3, _m3 = _check3(df, table, market)
    _print_share(df)
    _print_rho_lines(table, market)

    bad = sum(0 if ok else 1 for ok in (ok1, ok2, ok3))
    print(f"\n문제 {bad}건 — {market}에서 EPV를 지을 근거가 서려면 셋 다 [확인]이어야 한다.")
    print("이 결과는 ADR로 남긴다. 통과든 아니든, 재고 나서 정했다는 것이 기록이다.")
    return 1 if bad else 0
```

- [ ] **Step 3: 돌려 본다**

```bash
.venv/Scripts/python.exe scripts/check_epv_viability.py KR --limit 60
```

기대: 여섯 절이 순서대로 나오고 마지막 줄이 `문제 N건`.

- [ ] **Step 4: 관문 + 커밋**

```bash
.venv/Scripts/python.exe scripts/check_all.py
```

기대: `통과 11 · 실패 0`.

```bash
git add scripts/check_epv_viability.py
git commit -m "diag(epv): 실효 비중·붙여넣을 줄·종합 합불"
```

---

## Task 7: 두 시장 본측정 + ADR-0023

**Files:**
- Create: `docs/adr/0023-….md` (이름은 **결과가 정한다** — 통과면 `0023-epv-as-fifth-axis.md`, 기각이면 `0023-no-epv-either.md` 같은 식. 이 저장소 ADR 제목은 무엇을 정했는지를 말한다)
- Modify: `docs/adr/README.md` (표 끝에 한 줄. `merge=union`이라 순서를 맞추려 애쓰지 않는다)

- [ ] **Step 1: 본측정을 돌린다 — 두 시장, 출력을 파일로 남긴다**

출력을 반드시 파일로 남긴다 — 표본 순회가 비싸 다시 재기 어렵다. **저장소 안에 쓰지
않는다**(실수로 커밋된다). 세션 스크래치패드나 OS 임시 디렉터리를 쓴다:

```bash
.venv/Scripts/python.exe scripts/check_epv_viability.py KR --limit 200 2>&1 | tee "$TEMP/epv_kr.txt"
```

```bash
.venv/Scripts/python.exe scripts/check_epv_viability.py US --limit 200 2>&1 | tee "$TEMP/epv_us.txt"
```

**각각 수십 분 걸린다**(종목당 왕복 + 재시도 백오프). 성공률을 반드시 확인한다 — KR은 87%, US는 100%가 직전 실측이었다. 크게 낮으면 재시도 설정을 보고 다시 돌린다.

- [ ] **Step 2: ADR 번호가 아직 비었는지 확인한다**

```bash
ls docs/adr/ | tail -3 && gh pr list --state open
```

기대: `0022-…`가 마지막이고 열린 PR이 없다. **#115와 #117이 둘 다 0020을 써서 겹친 적이 있다** — 열린 PR을 함께 봐야 한다.

- [ ] **Step 3: ADR을 쓴다**

기존 ADR의 절 구성을 그대로 따른다(`0016`·`0022`가 본보기): 맥락 → 측정 → 결정 → 근거 → 검토한 대안 → **한계 — 숨기지 말 것** → 재현.

반드시 담을 것:

1. **세 관문의 KR·US 합불 표.** 두 시장이 갈리면 갈린 채로 적는다
2. **ADR-0016 표 재계산 41%**와 이번 실측의 차이. 순부채 조건이 더 붙었다는 것
3. **기존 축이 이미 검사 2의 0.5를 넘는다**(③↔① KR +0.787 · US +0.706). EPV에만
   그 자를 대는 것이 신참에게만 엄하다는 지적, 그럼에도 기준을 유지한 이유
4. **'1.5배 밖'이 ADR-0016과 다른 통계량**이라는 것(재료 → 축 자체)
5. **검사 3은 이번에 신설**했고 "중앙값이 안 내려갈 것"은 판단값이라는 것
6. 한계: Greenwald 원안의 단순형 · 상관은 종목들 사이에서 재서 하나에 적용(ADR-0022와
   같은 근사) · 표본 하나(`random_state=11`) · 어느 판정이 맞는지는 여전히 모름(ADR-0009)
7. 재현 명령 두 줄

`docs/adr/README.md` 표 끝에 한 줄을 더한다.

- [ ] **Step 4: 관문 + 커밋**

```bash
.venv/Scripts/python.exe scripts/check_all.py
```

기대: `통과 11 · 실패 0`. `check_adr_index.py`가 파일과 표를 대조하므로 여기서 잡힌다.

```bash
git add docs/adr/
git commit -m "docs: ADR-0023 — EPV를 재서 <결과>"
```

---

## Task 8: 인계문 교체 + PR

**Files:**
- Modify: `docs/HANDOFF-CONFIDENCE.md` (맨 위 인계 블록)
- Modify: `CLAUDE.md` (맨 위 '다음 작업' 안내 + 테스트 수)

- [ ] **Step 1: 인계문 맨 위를 다음 사람용으로 갈아 끼운다**

지금 맨 위 블록은 *"다음 사람이 이어받을 일 — 4번 EPV"*다. 이 작업이 그것이므로 교체한다. 새 블록이 답할 것:

- **무엇을 쟀고 무엇이 나왔나** (세 관문 × 두 시장)
- **통과면** — EPV를 축으로 짓는 일이 남았다. 붙여넣을 `METHOD_RHO` 줄이 어디 있나,
  `METHOD_WEIGHTS`를 어떻게 정할 것인가(ADR-0003이 "순위 인코딩 ≠ 가중치 추정"이라고
  못박았다), 화면 어디가 움직이나
- **기각이면** — 절대가치 축을 늘리는 길이 DCF·EPV 둘 다 닫혔다는 뜻이다. ADR-0016
  결정 4(*"끝내 절대가치가 없는 29%는 모형이 아니라 문구로 다룬다"*)가 남은 길이다
- **어느 쪽이든 열려 있는 것** — 미국 배지가 100% '낮음'이라 아무것도 구별해 주지
  않는 문제(ADR-0022 한계). 화면 판단이고 아직 아무도 안 정했다

- [ ] **Step 2: `CLAUDE.md` 맨 위 블록을 갱신한다**

지금 *"▶ 다음 작업은 4번 'EPV'입니다"*로 시작한다. 이 작업의 결과에 맞춰 바꾸고, 테스트 수를 실제 값으로 고친다:

```bash
.venv/Scripts/python.exe -m pytest tests/ -q | tail -1
```

이 출력의 숫자를 그대로 쓴다. **직전 값(347 passed, 87 subtests)을 그대로 두지 않는다** — Task 1이 테스트를 늘렸다.

- [ ] **Step 3: 관문 + 커밋 + 푸시**

```bash
.venv/Scripts/python.exe scripts/check_all.py
```

```bash
git add docs/HANDOFF-CONFIDENCE.md CLAUDE.md
git commit -m "docs: 인계문을 EPV 측정 결과 뒤로 넘긴다"
```

```bash
git push -u origin diag/epv-viability
```

- [ ] **Step 4: PR을 연다**

```bash
gh pr create --base main --title "EPV를 재봤습니다 — <한 줄 결과> (ADR-0023)" --body-file -
```

본문에 담을 것: 무엇을 쟀나 · 세 관문 × 두 시장 결과표 · **ADR-0016 41% 재계산** · 판정 경로가 안 바뀐 증거(`check_all.py` 11/11, `check_analysis.py` 실행 결과) · 재현 명령.

- [ ] **Step 5: base가 살아 있는지 확인한다**

```bash
gh pr view --json baseRefName,mergeStateStatus
```

기대: `"baseRefName": "main"`. #31·#36에서 base를 잘못 잡아 변경이 사라진 적이 있다.

---

## 완료 조건

- [ ] `tests/test_epv.py` 통과, `check_all.py` **11/11**
- [ ] `check_epv_viability.py`가 KR·US **각각** 완주하고 성공률이 찍힌다
- [ ] ADR-0023에 세 관문 × 두 시장 합불이 있고, 41%·0.5 초과·통계량 변경이 모두 적혀 있다
- [ ] `git diff origin/main...HEAD -- src/analysis/valuation.py src/analysis/warranted.py web/`가 **비어 있다** (판정 경로 무변경)
- [ ] PR base가 `main`
