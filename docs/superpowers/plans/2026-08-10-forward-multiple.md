# PR 1 — ④ 타깃 배수 진단 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ④의 곱하는 배수를 회귀 적정 PER로 바꿔야 하는지를 **재고**, 시나리오 폭에 쓸 회귀 잔차 사분위를 **측정한다.** 판정 경로는 한 줄도 건드리지 않는다.

**Architecture:** 진단 전용 변경 셋. (1) `loo_leg_error()`가 이미 만드는 LOO 잔차에서 q25/q75를 함께 돌려준다 — 새 계산이 아니라 이미 있는 벡터에서 두 값을 더 꺼내는 것이다. (2) `check_warranted.py`가 그 두 값을 붙여넣을 줄로 찍는다. (3) `check_multiple_rules.py`에 후보 F1·F2를 더하고 채점을 ㉠·㉡ 둘로 가른다. 기존 후보 다섯은 **그대로 둔다** — 2026-07-19 숫자가 재현되는지가 곧 이 측정의 위생 검사다.

**Tech Stack:** Python 3.14 · numpy · pandas · unittest. 계수는 `src/analysis/warranted.py`, 진단 스크립트는 `scripts/`.

**사전등록:** [`docs/superpowers/specs/2026-08-10-forward-multiple-design.md`](../specs/2026-08-10-forward-multiple-design.md) — **측정 뒤에 고치지 않는다.**

---

## 파일 구조

| 파일 | 책임 | 이 PR에서 |
|---|---|---|
| `src/analysis/warranted.py` | 회귀 적합·적정 배수·오차 측정 | `loo_leg_error()` 반환에 `resid_q25`·`resid_q75` 추가 (Task 1) |
| `tests/test_warranted.py` | 위 측정이 맞는지 못박음 | 테스트 둘 추가 (Task 1) |
| `scripts/check_warranted.py` | 회귀 대 피어 수동 진단 · `LEG_MAE`를 만드는 자리 | 사분위를 붙여넣을 줄로 출력 (Task 2) |
| `scripts/check_multiple_rules.py` | ④ 타깃 배수 규칙 시합 | 후보 F1·F2 · 채점 ㉠㉡ · 국면 부분집합 · 성공선 판정 (Task 3) |

**새 파일은 만들지 않는다.** 넷 다 이미 자기 책임이 분명하고, 이 작업은 각각의 책임 안에 들어간다.

---

## Task 0: 워크트리 준비

**Files:** 없음 (환경만)

- [ ] **Step 1: 캐시를 복사한다**

`data/cache/`는 gitignore라 워크트리가 빈 채로 시작한다. 그대로 두면 US 종목이 `YFRateLimitError`로 죽고 화면에는 *"상장폐지·거래정지"*로 뜬다 — ADR-0027의 인증서 문제와 **증상이 같지만 원인이 다르다.**

```bash
cp -r "C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/data/cache" "C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.claude/worktrees/forward-multiple/data/cache"
```

- [ ] **Step 2: 인터프리터가 도는지 확인한다**

`.venv`는 메인 트리에만 있다. 스크립트들이 `ROOT = Path(__file__).resolve().parents[1]`이라 워크트리가 ROOT가 된다 — 절대경로로 부르기만 하면 된다.

Run:
```bash
"C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" -c "import numpy, pandas; print('ok')"
```
Expected: `ok`

---

## Task 1: `loo_leg_error()`가 잔차 사분위를 함께 돌려준다

**Files:**
- Modify: `src/analysis/warranted.py:356-392` (`loo_leg_error`)
- Test: `tests/test_warranted.py:686-731` (`LooLegErrorTests`)

**왜 필요한가:** 시나리오의 비관/낙관 폭이 여기서 나온다. `LEG_MAE`를 그대로 쓰면 KR PER 기준 비관 배수 ×0.558 · 낙관 ×1.791로 벌어져 시나리오 표가 사실상 아무 말도 하지 않게 된다. MAE는 **평균 절대오차**지 분위가 아니다.

**부호 규약(중요):** `e_loo = log(실제 배수) − log(적합 배수)`다. 따라서 `실제 = 적합 × exp(e)`이고, 배수의 아래쪽은 `× exp(q25)`, 위쪽은 `× exp(q75)`다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_warranted.py`의 `LooLegErrorTests` 클래스 안, `test_reports_saturated_points` 바로 뒤(731줄 다음)에 붙인다:

```python
    def test_residual_quartiles_match_press_quantiles(self):
        # 사분위는 MAE와 **같은 잔차 벡터**에서 나와야 한다. 다른 데서 재면 시나리오 폭과
        # 오차 문구가 서로 다른 것을 말하게 된다 — 화면 두 곳이 갈리는 고전적인 자리다.
        from src.analysis.warranted import _design_matrix, _prep, loo_leg_error

        df = _noisy()
        d = _prep(df)
        X, *_ = _design_matrix(d)
        y = np.log(d["multiple"].to_numpy(float))
        beta, _r, _rk, _s = np.linalg.lstsq(X, y, rcond=None)
        hat = np.einsum("ij,jk,ik->i", X, np.linalg.pinv(X.T @ X), X)
        press = (y - X @ beta) / (1 - hat)

        out = loo_leg_error(df)
        self.assertAlmostEqual(out["resid_q25"], float(np.quantile(press, 0.25)), places=9)
        self.assertAlmostEqual(out["resid_q75"], float(np.quantile(press, 0.75)), places=9)

    def test_quartile_band_is_narrower_than_the_mae_band(self):
        # 설계 결정의 근거를 못박는다(2026-08-10 설계): LEG_MAE를 시나리오 폭에 그대로 쓰면
        # 너무 넓어진다. 사분위 폭은 2×MAE보다 좁아야 한다 — 뒤집히면 둘 중 하나가 틀렸다.
        out = loo_leg_error(_noisy())
        self.assertLess(out["resid_q25"], out["resid_q75"])
        self.assertLess(out["resid_q75"] - out["resid_q25"], 2 * out["mae"])
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run:
```bash
"C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" -m pytest tests/test_warranted.py -k "quartile" -v
```
Expected: **FAIL** — `KeyError: 'resid_q25'` 두 건.

- [ ] **Step 3: 최소 구현**

`src/analysis/warranted.py`의 `loo_leg_error()` 반환 딕셔너리(385~391줄)를 이렇게 바꾼다:

```python
    return {
        "n": len(d),
        "mae": float(np.mean(np.abs(e_loo))),          # ← LEG_MAE에 넣는 값
        "mae_in_sample": float(np.mean(np.abs(resid))),  # 부풀려진 값 — 대조용
        # 시나리오의 비관/낙관 폭은 **여기서** 온다. MAE가 아니라 사분위인 이유는
        # 화면이 '25 · 50 · 75분위'라고 적기 때문이다 — MAE는 평균 절대오차라 분위가
        # 아니고, 그대로 쓰면 KR PER 기준 배수 ×0.558~×1.791로 벌어져 시나리오 표가
        # 아무 말도 하지 않게 된다.
        # 부호 규약: e = log(실제) − log(적합)이므로 `실제 = 적합 × exp(e)`다.
        # 배수의 아래쪽이 `× exp(resid_q25)`, 위쪽이 `× exp(resid_q75)`다.
        "resid_q25": float(np.quantile(e_loo, 0.25)),
        "resid_q75": float(np.quantile(e_loo, 0.75)),
        "r2_loo": float(1 - np.sum(e_loo ** 2) / ss) if ss > 0 else float("nan"),
        "saturated": saturated,
    }
```

그리고 도크스트링 끝(368줄 *"이 차이를 숨기지 않는다."* 뒤)에 한 문단을 더한다:

```python
    반환에는 잔차의 **사분위**도 담는다(`resid_q25`·`resid_q75`). `mae`는 오차의 크기를
    한 숫자로 말하는 값이라 화면의 '25~75분위' 문구에 쓸 수 없다 — 자의 종류가 다르다.
    같은 잔차 벡터에서 두 값을 함께 내보내 시나리오 폭과 오차 문구가 갈리지 않게 한다.
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run:
```bash
"C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" -m pytest tests/test_warranted.py -v
```
Expected: **PASS** — `LooLegErrorTests` 6건 포함 전부 통과, 실패 0.

- [ ] **Step 5: 커밋**

```bash
git add src/analysis/warranted.py tests/test_warranted.py
git commit -m "feat(warranted): loo_leg_error가 잔차 사분위를 함께 돌려준다

시나리오의 비관/낙관 폭이 여기서 나온다. LEG_MAE를 그대로 쓰면 KR PER 기준
배수 x0.558~x1.791로 벌어져 시나리오 표가 아무 말도 하지 않게 된다 — MAE는
평균 절대오차라 화면이 적는 '25~75분위'와 자의 종류가 다르다.

이미 만들고 있는 LOO 잔차 벡터에서 두 값을 더 꺼낼 뿐이다. 새 계산이 아니다.
같은 벡터에서 내보내야 시나리오 폭과 오차 문구가 갈리지 않는다 — 테스트가
그 등식을 지킨다.

부호 규약: e = log(실제) - log(적합)이므로 실제 = 적합 x exp(e)다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: `check_warranted.py`가 사분위를 붙여넣을 줄로 찍는다

**Files:**
- Modify: `scripts/check_warranted.py:76-97`

**왜 필요한가:** `LEG_MAE`가 그랬듯 사분위도 **측정해서 상수로 박아야 한다.** 런타임에 LOO를 돌릴 수 없다. 이 스크립트가 이미 `loo_leg_error`를 부르고 있으므로 출력만 늘린다.

- [ ] **Step 1: 수집 딕셔너리를 하나 더 둔다**

`scripts/check_warranted.py`의 `new_mae: dict[str, float] = {}`(46줄 근처) 바로 다음 줄에 추가:

```python
    new_q: dict[str, tuple[float, float]] = {}
```

- [ ] **Step 2: 값을 담는다**

`new_mae[leg] = lo["mae"]`가 있는 줄(82줄 근처) 바로 다음에 추가:

```python
            new_q[leg] = (lo["resid_q25"], lo["resid_q75"])
```

- [ ] **Step 3: 붙여넣을 줄을 찍는다**

`for leg, v in new_mae.items():` 루프가 끝난 자리, `return 1 if bad else 0` 바로 앞에 추가:

```python
    if new_q:
        print("\n잔차 사분위 — **시나리오 폭**이 여기서 온다(설계 2026-08-10). MAE와 다른 자다.")
        print("배수는 `적정배수 × exp(q25)` ~ `적정배수 × exp(q75)`로 벌어진다.")
        parts = ", ".join(f'"{k}": ({a:.3f}, {b:.3f})' for k, (a, b) in new_q.items())
        print(f'\n    "{market}": {{' + parts + "},")
        for leg, (a, b) in new_q.items():
            mae = new_mae.get(leg)
            mae_txt = f" (2×MAE로 재면 ×{np.exp(-mae):.3f}~×{np.exp(mae):.3f})" if mae else ""
            print(f"    ※ {leg}: 배수 ×{np.exp(a):.3f} ~ ×{np.exp(b):.3f}{mae_txt}")
```

`np`는 이미 임포트돼 있다(15줄). 새 임포트가 필요 없다.

- [ ] **Step 4: 문법과 임포트를 확인한다**

이 스크립트는 네트워크(전 종목 스냅숏)가 필요해 여기서 끝까지 돌리지 않는다. Task 4에서 돌린다. 지금은 임포트가 되는지만 본다.

Run:
```bash
"C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" -c "import ast, pathlib; ast.parse(pathlib.Path('scripts/check_warranted.py').read_text(encoding='utf-8')); print('parse ok')"
```
Expected: `parse ok`

- [ ] **Step 5: 커밋**

```bash
git add scripts/check_warranted.py
git commit -m "feat(check_warranted): 잔차 사분위를 붙여넣을 줄로 찍는다

LEG_MAE가 그랬듯 사분위도 측정해서 상수로 박아야 한다 — 런타임에 LOO를
돌릴 수 없다. 이 스크립트가 이미 loo_leg_error를 부르고 있어 출력만 늘렸다.

2xMAE로 쟀을 때의 폭을 나란히 찍는다. 둘이 얼마나 다른지가 설계에서 사분위를
고른 이유다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: `check_multiple_rules.py`에 후보 F1·F2와 성공선 판정을 넣는다

**Files:**
- Modify: `scripts/check_multiple_rules.py` (전체 교체)

**왜 필요한가:** 지금 시합의 후보 다섯에 **회귀 적정 PER이 없다.** 그 측정은 2026-07-19이고 회귀는 ADR-0014로 그 뒤에 들어왔다.

**기존 후보 다섯을 지우지 않는다** — 재실행에서 B가 여전히 0.26 근처인지가 이 측정의 위생 검사다. 크게 움직였으면 후보 비교 이전에 그것부터 봐야 한다.

- [ ] **Step 1: 파일을 통째로 바꾼다**

`scripts/check_multiple_rules.py`의 내용 전부를 아래로 교체:

```python
# -*- coding: utf-8 -*-
"""④ 선행 이익의 타깃 멀티플 규칙 실증 비교: python scripts/check_multiple_rules.py

2026-07-19 실행 결과(11종목): 자기 5년 PER 중앙값이 가격오차 0.26으로 최소,
목표가 내재 멀티플과 중앙값 +0.02 일치 → valuation._forward_value의 규칙 근거.
피어 선행PER 원본은 AI 피어에 소형주가 섞이면 체계적 과소(오차 0.65).

**2026-08-10 — 후보 둘(F1·F2)을 더하고 채점을 둘로 갈랐다.**
그 시합에 ①의 회귀 적정 PER이 없었다. 회귀는 ADR-0014로 이 측정 **뒤에** 들어왔다.
사전등록: docs/superpowers/specs/2026-08-10-forward-multiple-design.md

    ㉠ |log(예측가 / 컨센서스 목표주가)|   ← **채택 문턱은 여기에만 건다**
    ㉡ |log(예측가 / 현재가)| (LNT 2002)   ← 함께 보고하되 채택 기준으로 쓰지 않는다

㉡에는 구조적 함정이 있다. 자기 과거 PER 중앙값은 **현재가에 붙는다** — PER이
평균회귀하면 `자기중앙값 × 현재EPS ≈ 현재가`이기 때문이다. 적정가 방법을 ㉡으로
고르면 "현재가를 가장 잘 맞히는 것"을 뽑게 되고, 그러면 ④는 늘 "적정"이라고 말한다.
체온계를 고르면서 "지금 체온과 똑같이 나오는 것"을 고르는 것과 같다.

㉡을 함께 내는 것은 현재가에서 얼마나 떨어졌는지를 **숨기지 않기 위해서**지 고르기
위해서가 아니다. 어느 지표에 문턱을 걸지를 결과 보고 정하면 사전등록이 아니다.

기존 후보 다섯(A·B·C·E·G)은 **지우지 않는다.** 재실행에서 B가 여전히 0.26 근처인지가
이 측정의 위생 검사다 — 크게 움직였으면 후보 비교보다 그것을 먼저 봐야 한다.
"""
import math
import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.scoring import peer_median, sanitize_peer_frame  # noqa: E402
from src.analysis.warranted import warranted_multiple             # noqa: E402
from src.data.universe_multiples import coefficients_or_none      # noqa: E402
from src.web.serialize import _defaults, _pipeline                # noqa: E402

STOCKS = [("KR", "005930"), ("KR", "035420"), ("KR", "005380"), ("KR", "000660"),
          ("KR", "035720"), ("KR", "051910"),
          ("US", "AAPL"), ("US", "MSFT"), ("US", "KO"), ("US", "JNJ"),
          ("US", "WMT"), ("US", "GOOGL")]

SIZE_FACTOR = 20.0   # 시총이 자사 대비 1/20~20배 밖이면 비교 부적격

BASE_KEY = "B_자기5년중앙"        # 기준선 — 현행 규칙
NEW_KEYS = ("F1_회귀PER_현ROE", "F2_회귀PER_선행ROE")
KEYS = ("A_피어원본", BASE_KEY, "C_min(A,B)", "E_피어사이즈필터", "G_중간(E,B)") + NEW_KEYS

# 사전등록한 성공선 — **측정 뒤에 고치지 않는다.**
ADOPT_GAP = 0.05   # ㉠이 B 대비 이만큼 낮아야 채택. 미달이면 '구별되지 않는다'
GUARD_GAP = 0.30   # ㉠은 통과했는데 ㉡이 이만큼 나빠지면 보류하고 원인부터 본다
PHASE_SHIFT = 0.5  # |선행EPS/TTM − 1| 이 값 이상이면 '국면 전환' (R2 실측에서 온 문턱)


def size_filtered_fwd(sp, self_mcap):
    m = (~sp["is_self"].astype(bool)) & sp["market_cap"].notna() \
        & (sp["market_cap"] >= self_mcap / SIZE_FACTOR) \
        & (sp["market_cap"] <= self_mcap * SIZE_FACTOR)
    v = sp.loc[m, "forward_per"].dropna()
    return (float(v.median()), int(len(v))) if len(v) >= 2 else (None, int(len(v)))


def reg_per(coef, d, roe):
    """회귀 적정 PER. 계수가 없거나 규모가 학습 범위 밖이면 None (원 코드와 같은 규약)."""
    if not coef:
        return None
    return warranted_multiple(coef.get("per"), d.market_cap, d.sector, roe)["multiple"]


def _num(v, sign=False):
    if v is None:
        return "—"
    return f"{v:+.3f}" if sign else f"{v:.3f}"


def summarize(rows, title):
    """후보별 ㉠·㉡ 중앙값 표를 찍고 통계를 돌려준다."""
    print(f"\n===== {title} (n={len(rows)}) =====")
    print(f"{'후보':<22}{'n':>4}{'㉠|log(예측/목표가)|':>22}{'vs B':>9}"
          f"{'㉡|log(예측/현재가)|':>22}{'㉠부호':>10}")
    print("─" * 89)
    stat = {}
    for k in KEYS:
        tgt = [r[k + "_tgt_abs"] for r in rows if r.get(k + "_tgt_abs") is not None]
        prc = [r[k + "_err"] for r in rows if r.get(k + "_err") is not None]
        sgn = [r[k + "_vs_tgt"] for r in rows if r.get(k + "_vs_tgt") is not None]
        stat[k] = {"n": len(tgt),
                   "tgt": float(np.median(tgt)) if tgt else None,
                   "prc": float(np.median(prc)) if prc else None,
                   "sgn": float(np.median(sgn)) if sgn else None}
    base = stat[BASE_KEY]["tgt"]
    for k in KEYS:
        s = stat[k]
        if k == BASE_KEY:
            d_txt = "기준"
        elif s["tgt"] is not None and base is not None:
            d_txt = f"{s['tgt'] - base:+.3f}"
        else:
            d_txt = "—"
        print(f"{k:<22}{s['n']:>4}{_num(s['tgt']):>22}{d_txt:>9}"
              f"{_num(s['prc']):>22}{_num(s['sgn'], sign=True):>10}")
    return stat


def verdict(stat):
    """사전등록한 성공선을 그대로 적용한다. 판단을 사람에게 미루지 않는다."""
    print("\n===== 사전등록 성공선 =====")
    print(f"채택 : ㉠이 B 대비 {ADOPT_GAP:.2f} 이상 낮을 것")
    print(f"보류 : ㉠은 통과했는데 ㉡이 B 대비 {GUARD_GAP:.2f} 이상 나빠지면 원인부터 본다")
    print("근거 : docs/superpowers/specs/2026-08-10-forward-multiple-design.md")
    print("한계 : 표본 10~12종목이라 통계 검정이 아니다 · 목표주가에는 낙관 편향이 있다")
    base = stat[BASE_KEY]
    if base["tgt"] is None:
        print("\n기준선 B의 ㉠을 내지 못했다 — 판정 불가. 원인부터 본다.")
        return
    for k in NEW_KEYS:
        s = stat[k]
        if s["tgt"] is None:
            print(f"\n{k}: 값 없음 — 판정 불가")
            continue
        gain = base["tgt"] - s["tgt"]
        drift = (s["prc"] - base["prc"]) if (s["prc"] is not None
                                             and base["prc"] is not None) else None
        if gain < ADOPT_GAP:
            call = f"미달 (문턱 {ADOPT_GAP:.2f}) — 구별되지 않는다. 현행 유지"
        elif drift is not None and drift >= GUARD_GAP:
            call = f"통과했으나 ㉡이 {drift:+.3f} — **보류**. 계수 오염부터 확인"
        else:
            call = "통과"
        print(f"\n{k}: ㉠ 개선 {gain:+.3f} → {call}")
        if s["n"] < base["n"]:
            print(f"    ⚠ 커버리지 {s['n']} < B {base['n']} — ④가 사라지면 "
                  "'컨센서스 반영' 병기값도 함께 사라진다(ADR-0006 불변식)")


def main():
    rows, coefs = [], {}
    for market, q in STOCKS:
        try:
            rf, mrp = _defaults(market)
            d, ind, scores, cc, val = _pipeline(market, q, 9, rf, mrp)
            c = d.consensus
            fwd = c.forward_eps if c else None
            tgt = c.target_mean if c else None
            if not fwd or fwd <= 0:
                print(f"[skip] {q}: 선행 EPS 없음")
                continue
            if market not in coefs:
                coefs[market] = coefficients_or_none(market)
            coef = coefs[market]

            sp = sanitize_peer_frame(d.peers)
            peer_all = peer_median(sp, "forward_per")
            peer_sz, n_sz = size_filtered_fwd(sp, d.market_cap)
            q50 = (val.per_q or {}).get(50)

            # ①이 쓰는 것과 **같은** ROE 정의다 (valuation._relative_value: eps / bps).
            eps_ttm = d.latest("eps")
            equity = d.latest("total_equity")
            shares = d.shares_outstanding
            bps = (equity / shares) if (equity and shares) else None
            roe_now = (eps_ttm / bps) if (eps_ttm is not None and bps) else None
            # F2는 ⑤의 규칙을 그대로 옮긴 것이다 — 배수를 만드는 수익성도 곱해질 이익과
            # 같은 국면에서 낸다(valuation._normalized_value 도크스트링). ROE 정의상
            # 선행EPS × 주식수 ÷ 자기자본 = 선행EPS / bps로 같다.
            roe_fwd = (fwd / bps) if bps else None

            cands = {
                "A_피어원본": peer_all,
                BASE_KEY: q50,
                "C_min(A,B)": min([x for x in (peer_all, q50) if x], default=None),
                "E_피어사이즈필터": peer_sz,
                "G_중간(E,B)": (0.5 * (peer_sz + q50)) if (peer_sz and q50) else (peer_sz or q50),
                "F1_회귀PER_현ROE": reg_per(coef, d, roe_now),
                "F2_회귀PER_선행ROE": reg_per(coef, d, roe_fwd),
            }
            growth = (fwd / eps_ttm - 1) if (eps_ttm and eps_ttm > 0) else None
            rec = {"종목": f"{d.name}({q})", "price": d.price, "growth": growth,
                   "street_mult": (tgt / fwd) if tgt else None}
            for k, mult in cands.items():
                rec[k + "_err"] = abs(math.log(mult * fwd / d.price)) if mult else None
                v = math.log(mult * fwd / tgt) if (mult and tgt) else None
                rec[k + "_vs_tgt"] = v
                rec[k + "_tgt_abs"] = abs(v) if v is not None else None
            rows.append(rec)
            g_txt = f"{growth:+.0%}" if growth is not None else "—"
            street = rec["street_mult"]
            print(f"[ok] {rec['종목']}: 선행/TTM {g_txt} · 목표가 내재 "
                  f"{round(street, 1) if street else '—'}배 | "
                  + " ".join(f"{k}={round(v, 1) if v else None}" for k, v in cands.items()))
        except Exception as e:
            print(f"[err] {q}: {e}")
            traceback.print_exc(limit=1)

    if not rows:
        print("표본이 하나도 없다 — 캐시·네트워크를 먼저 본다.")
        return
    stat = summarize(rows, "규칙별 요약 — 전체")

    # 부 지표 — R2가 지목한 바로 그 자리다. 전체에서 좋아졌는데 여기서 안 좋아지면
    # 원인이 우리가 생각한 것(배수·이익의 국면 불일치)이 아니라는 뜻이다.
    shifted = [r for r in rows
               if r.get("growth") is not None and abs(r["growth"]) >= PHASE_SHIFT]
    if shifted:
        summarize(shifted, f"국면 전환 종목만 (|선행/TTM−1| ≥ {PHASE_SHIFT:.0%})")
    else:
        print(f"\n국면 전환 종목(|선행/TTM−1| ≥ {PHASE_SHIFT:.0%})이 표본에 없다 — "
              "부 지표는 판정 불가. 그 사실을 ADR에 적을 것.")

    verdict(stat)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 임포트와 문법을 확인한다**

Run:
```bash
"C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" -c "import importlib.util as u; s=u.spec_from_file_location('m','scripts/check_multiple_rules.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print('import ok ·', len(m.KEYS), '후보')"
```
Expected: `import ok · 7 후보`

- [ ] **Step 3: 커밋**

```bash
git add scripts/check_multiple_rules.py
git commit -m "feat(check_multiple_rules): 회귀 적정 PER을 후보로 넣고 채점을 둘로 가른다

이 시합의 후보 다섯에 회귀 적정 PER이 없었다. 측정은 2026-07-19이고 회귀는
ADR-0014로 그 뒤에 들어왔다. 그냥 교체하면 측정으로 이긴 규칙을 측정 없이
빼는 모양이 된다.

## 후보를 둘 더한다

F1 회귀PER(현재 ROE) - 인계문 원안. 배수의 출처만 바꾼다
F2 회귀PER(선행이익 ROE) - 5가 이미 쓴 규칙. 배수를 만드는 수익성도 곱해질
이익과 같은 국면에서 낸다

R2가 지목한 실패는 배수와 이익의 국면 불일치다. F1은 국면을 여전히 어긋난 채
둔다. 둘을 가르지 않으면 어느 쪽이 효과를 냈는지 알 수 없다.

기존 다섯은 지우지 않았다 - 재실행에서 B가 여전히 0.26 근처인지가 이 측정의
위생 검사다.

## 채점을 둘로 가르고 문턱은 하나에만 건다

가 |log(예측/목표가)| - 채택 문턱은 여기에만
나 |log(예측/현재가)| - 함께 보고하되 채택 기준으로 쓰지 않는다

나에는 구조적 함정이 있다. 자기 과거 PER 중앙값은 현재가에 붙는다(PER이
평균회귀하면 자기중앙값 x 현재EPS 는 현재가에 가깝다). 적정가 방법을 그 자로
고르면 현재가를 가장 잘 맞히는 것을 뽑게 되고, 4는 늘 '적정'이라고 말한다.

성공선과 한계를 스크립트가 직접 찍는다 - 판단을 사람에게 미루지 않는다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: 측정을 실제로 돌리고 결과를 남긴다

**Files:**
- Create: `docs/review/2026-08-10-forward-multiple-측정.md`

**⚠ 이 Task가 이 PR의 목적이다.** 앞 셋은 도구를 만든 것뿐이다.

- [ ] **Step 1: 규칙 시합을 돌린다**

회귀계수 콜드 빌드가 7분 43초 걸릴 수 있다(이슈 #131). `PYTHONUNBUFFERED=1`을 붙여야 진행이 보인다.

Run:
```bash
SCRATCH="C:/Users/bogeun/AppData/Local/Temp/claude/C--Users-bogeun-OneDrive-Desktop-----/03edf6f2-94ec-48a5-b558-842c044d9361/scratchpad"; PYTHONUNBUFFERED=1 "C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" scripts/check_multiple_rules.py 2>&1 | tee "$SCRATCH/multiple_rules.log"
```
Expected: `[ok]` 줄이 10~12개, 표 둘, `===== 사전등록 성공선 =====` 절.

**`[err]`가 나오면 멈추고 원인부터 본다.** `possibly delisted; no price data found`는 Yahoo가 아니라 캐시(Task 0 Step 1)나 인증서(ADR-0027)일 수 있다. 확인:
```bash
"C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" -c "from curl_cffi import requests as r; print(r.get('https://fc.yahoo.com', impersonate='chrome').status_code)"
```
`curl: (77)`이면 인증서, 404면 인증서는 멀쩡하고 레이트리밋이다.

- [ ] **Step 2: 잔차 사분위를 잰다**

Run:
```bash
SCRATCH="C:/Users/bogeun/AppData/Local/Temp/claude/C--Users-bogeun-OneDrive-Desktop-----/03edf6f2-94ec-48a5-b558-842c044d9361/scratchpad"; PY="C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe"; PYTHONUNBUFFERED=1 "$PY" scripts/check_warranted.py KR 2>&1 | tee "$SCRATCH/warranted_kr.log"; PYTHONUNBUFFERED=1 "$PY" scripts/check_warranted.py US 2>&1 | tee "$SCRATCH/warranted_us.log"
```
Expected: `잔차 사분위 —` 절과 붙여넣을 `"KR": {...}` / `"US": {...}` 줄.

- [ ] **Step 3: 결과를 문서로 남긴다**

`docs/review/2026-08-10-forward-multiple-측정.md`를 만들고 아래 뼈대에 **실제 출력을 채운다.** 숫자를 지어내지 말 것 — 로그에서 그대로 옮긴다.

```markdown
# 측정 — ④의 타깃 배수를 회귀로 바꿔야 하나

> 2026-08-10 실행. 사전등록: [`../superpowers/specs/2026-08-10-forward-multiple-design.md`](../superpowers/specs/2026-08-10-forward-multiple-design.md)
> **사전등록 문서는 이 측정 뒤에 고치지 않았다.**

## 재현

    python scripts/check_multiple_rules.py
    python scripts/check_warranted.py KR
    python scripts/check_warranted.py US

## 위생 검사 — B가 2026-07-19와 같은 자리에 있나

| | 2026-07-19 | 2026-08-10 |
|---|---|---|
| B 자기5년중앙 · ㉡ 가격오차 | 0.26 | (채운다) |
| B 자기5년중앙 · ㉠ 목표가 부호 | +0.02 | (채운다) |

(크게 움직였으면 후보 비교보다 이것을 먼저 설명한다)

## 전체

(표를 그대로 옮긴다)

## 국면 전환 종목만

(표를 그대로 옮긴다 · 표본에 없었으면 없었다고 적는다)

## 사전등록 성공선 적용

(스크립트가 찍은 판정을 그대로 옮긴다)

## 잔차 사분위 — 시나리오 폭

| 시장 | q25 | q75 | 배수 폭 | 2×MAE로 재면 |
|---|---|---|---|---|
| KR per | | | | |
| US per | | | | |

## 한계 — 사전등록에 적은 그대로

1. 표본 10~12종목이라 통계 검정이 아니다. 방향과 크기만 본다
2. 이 측정은 "맞히는가"가 아니라 "증권가 관행과 얼마나 다른가"를 잰다 —
   과거 시점 컨센서스가 패널에 없다

## 이 측정으로 알 수 없는 것

(돌려 보고 알게 된 한계를 여기 적는다. 없으면 "없다"고 적는다)
```

- [ ] **Step 4: 커밋**

```bash
git add docs/review/2026-08-10-forward-multiple-측정.md
git commit -m "docs: ④ 타깃 배수 측정 결과를 남긴다

사전등록한 성공선을 그대로 적용한 결과다. 사전등록 문서는 이 측정 뒤에
고치지 않았다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: 관문 전체 · PR

**Files:** 없음

- [ ] **Step 1: 관문 11개를 전부 돌린다**

**한 관문만 골라 돌리지 않는다.** CLAUDE.md가 적은 사고가 그것이다.

Run:
```bash
"C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" scripts/check_all.py
```
Expected: **통과 11 · 실패 0**

이 PC에서는 11개가 **전부 실제로 돈다** — `%TEMP%`가 ASCII이고 `node`도 있다. *"로컬 2건은 원래 빨갛다"*를 믿고 넘기면 진짜 실패를 놓친다.

- [ ] **Step 2: 판정 경로가 안 움직였는지 확인한다**

이 PR은 진단 전용이다. 실제 종목의 판정이 바뀌면 무언가 잘못된 것이다.

Run:
```bash
"C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" scripts/check_analysis.py KR 005930
"C:/Users/bogeun/OneDrive/Desktop/투자지표/valuation-dashboard/.venv/Scripts/python.exe" scripts/check_analysis.py US AAPL
```
Expected: 판정 축이 **①③⑤** 그대로. AAPL은 **①⑤ 둘뿐**. 적정주가·판정이 main과 같아야 한다.

- [ ] **Step 3: PR을 올린다**

```bash
git push -u origin diag/forward-multiple-rules
```

PR 본문에 반드시 넣을 것:
- **base는 `main`** (CLAUDE.md 브랜치 규칙 · `pr-base-guard.yml`이 검사한다)
- 측정 결과 표와 **성공선 판정**(통과/미달)
- 통과했으면 **PR 2에서 무엇을 고칠지**, 미달이면 **3단계를 접는다**는 결론
- 한계 둘(표본 크기 · "맞히는가"를 못 잰다)
- `check_all.py` 통과 11 · 실패 0

---

## 이 PR에서 하지 않는 것

- **`_forward_value()`를 건드리지 않는다.** PR 2다
- **`scenario.py`를 건드리지 않는다.** PR 2다
- **`LEG_RESID_Q` 상수를 박지 않는다.** 측정값이 나온 뒤 PR 2에서 박는다
- **ADR-0038을 쓰지 않는다.** 결정을 적는 문서라 채택/기각이 정해진 뒤에 쓴다
- **표본을 12종목에서 늘리지 않는다.** 늘려야 한다는 판단이 서면 별건으로 연다
