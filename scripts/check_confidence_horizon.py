"""신뢰도 배지가 자기가 주장하는 일을 하는가 — 등급별로 채점한다 (ADR-0043).

    python scripts/check_confidence_horizon.py            # KR
    python scripts/check_confidence_horizon.py --market US

## 무엇을 재나

배지는 *"신뢰도가 낮으니 판정을 보수적으로 해석하라"*고 말한다. 그 말이 참이려면
**'높음' 집단에서 판정이 더 잘 맞아야** 한다. 그래서 등급을 매기고, 등급 안에서만
판정의 예측력(IC · 5분위 간격)을 잰다.

주의 — **이 스크립트는 인과를 증명하지 않는다.** 등급 집단은 종목 구성이 서로 다르다
('낮음'이 더 작고 변동성 큰 종목이라면 무엇을 재도 간격이 넓다). 여기서 나오는 결론의
상한은 **"배지가 주장하는 것을 하지 못한다"**까지다. 대체 지표를 만들려면 사전등록이
먼저다([`docs/review/2026-08-12-문턱-사전등록.md`](../docs/review/2026-08-12-문턱-사전등록.md) 형식).

## 어떻게 재나 — 화면과 **같은 함수**를 부른다

숫자를 여기 옮겨 적으면 화면이 바뀔 때 이 진단만 썩는다(이 저장소가 반복해서 겪은 일).

    dispersion = std(exp(l)) / |mean(exp(l))|        ← valuation.py와 같은 식(ddof=0)
    n_eff      = warranted.effective_axes(methods, market)
    등급        = valuation.confidence_grade(disp, n, n_eff, capped)

`confidence_grade`는 **이 진단만을 위해 남아 있는 함수다**(ADR-0043). 제품 경로에서는
더 이상 부르지 않는다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "scripts"))

from check_backtest_combos import NORM, REL, RIM, _plain, score_date  # noqa: E402
from check_backtest_horizon import HORIZONS, MIN_T_DATES, _t, build  # noqa: E402

from src.analysis.valuation import METHOD_WEIGHTS, confidence_grade  # noqa: E402
from src.analysis.warranted import effective_axes  # noqa: E402

# 판정에 드는 축 — ①③⑤ (ADR-0035로 ②가 빠졌다). 배지도 이 축들에 대해서만 잰다.
VERDICT_AXES = (REL, RIM, NORM)
# **점수도 같은 축으로 매긴다.** `check_backtest_combos.W`는 ②를 포함한 옛 4축이라
# 여기 쓰면 '등급은 ①③⑤로 매기고 채점은 ①②③⑤으로 하는' 어긋난 자가 된다.
VERDICT_W = {m: METHOD_WEIGHTS[m] for m in VERDICT_AXES}
GRADES = ("높음", "중간", "낮음")


def grade_rows(panel: pd.DataFrame, market: str) -> pd.DataFrame:
    """각 행에 (흩어짐, 실질 축 수, 등급)을 붙인다. 화면과 같은 함수를 그대로 쓴다.

    ⚠ `itertuples`를 쓰지 않는다 — 축 컬럼명이 한글이라 `_3` 같은 위치 이름으로 바뀌고,
    `getattr(row, "업종 상대가치")`가 조용히 기본값으로 떨어진다. 넘파이 배열로 돈다.

    ⚠ **`effective_axes`에 `rho_table`을 넘기지 않는다.** 넘기면 이 패널로 방금 잰
    상관으로 등급이 매겨져 자기 자신을 채점하게 된다. 화면이 쓰던 것은 `METHOD_RHO`
    상수이므로 기본값(= 그 상수)을 그대로 써야 **화면과 같은 자**가 된다.
    """
    # 상관 표는 방법 조합마다 같으므로 조합 단위로 한 번만 잰다(7,656행 × 재계산 방지).
    axes_cache: dict[tuple, tuple[float, bool]] = {}
    mat = panel.reindex(columns=list(VERDICT_AXES)).to_numpy(dtype=float)
    disp, neff, grade = [], [], []
    for row in mat:
        ok = np.isfinite(row)
        methods = tuple(m for m, keep in zip(VERDICT_AXES, ok) if keep)
        if len(methods) < 2:
            disp.append(np.nan)
            neff.append(np.nan)
            grade.append(confidence_grade(None, len(methods))[0])
            continue
        # 로그 괴리율 → 원 스케일 적정가비. `valuation.py`가 mids(적정가)로 재는 것과
        # 같은 양이다 — 현재가가 공통 분모라 비율의 변동계수는 그대로 보존된다.
        vals = np.exp(row[ok])
        d = float(np.std(vals) / abs(np.mean(vals)))     # ddof=0 — valuation.py와 같다
        if methods not in axes_cache:
            axes_cache[methods] = effective_axes(list(methods), market)
        ne, capped = axes_cache[methods]
        disp.append(d)
        neff.append(ne)
        grade.append(confidence_grade(d, len(methods), ne, capped)[0])
    out = panel.copy()
    out["disp"], out["n_eff"], out["grade"] = disp, neff, grade
    return out


def _by_grade(panel: pd.DataFrame, col: str) -> dict:
    """등급별 (시점 평균 IC, 시점 평균 Q5−Q1, t값, 시점 수)."""
    fn = _plain(VERDICT_W)
    per_grade: dict[str, pd.DataFrame] = {}
    for g in GRADES:
        rows = []
        for t_date, sub in panel[panel["grade"] == g].groupby("date"):
            s = score_date(sub.apply(fn, axis=1), sub[col])
            s["date"] = t_date
            rows.append(s)
        per_grade[g] = (pd.DataFrame(rows).dropna(subset=["ic"])
                        if rows else pd.DataFrame(columns=["ic", "spread", "date"]))

    # **같은 시점끼리만 견준다.** 등급마다 채점된 시점 수가 다르면(5년에서 중간 7개 ·
    # 낮음 10개) 서로 다른 기간의 평균을 나란히 놓고 "이쪽이 낫다"고 말하게 된다.
    live = [g for g in GRADES if len(per_grade[g])]
    common = set.intersection(*[set(per_grade[g]["date"]) for g in live]) if live else set()
    out = {}
    for g in GRADES:
        d = per_grade[g]
        d = d[d["date"].isin(common)] if len(d) else d
        if d.empty:
            out[g] = (np.nan, np.nan, np.nan, 0)
            continue
        ic = d["ic"].to_numpy(float)
        tv = _t(ic) if len(d) >= MIN_T_DATES else np.nan
        out[g] = (float(ic.mean()), float(np.nanmean(d["spread"])), tv, len(d))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    args = ap.parse_args()
    base = ROOT / "data" / ("backtest" if args.market == "KR" else "backtest_us")
    p = base / "panel.parquet"
    if not p.exists():
        print(f"{p}가 없다 — 패널을 먼저 만들어라(backtest_collect.py → backtest_panel.py).")
        return 1

    panel = pd.read_parquet(p)
    panel["date"] = pd.to_datetime(panel["date"])
    for m in VERDICT_AXES:
        if m not in panel.columns:
            panel[m] = np.nan
    print(f"[{args.market}] 패널 {len(panel):,}행 · 시점 {panel['date'].nunique()}개")

    panel = build(panel, base / "raw")
    panel = grade_rows(panel, args.market)

    # ── A. 등급 분포 — 3단이라고 말하면서 실제로 몇 단인가 ──────────
    print("\nA. 등급 분포 — **3단 배지가 실제로 몇 단인가**")
    print(f"{'등급':<6}{'건수':>9}{'비율':>8}")
    print("─" * 23)
    n_all = len(panel)
    for g in GRADES:
        c = int((panel["grade"] == g).sum())
        print(f"{g:<6}{c:>9,}{c / n_all if n_all else 0:>8.1%}")

    # ── B. 등급별 예측력 — 배지가 주장하는 것을 하는가 ──────────────
    print("\nB. 등급 안에서 판정이 얼마나 맞나 — **'높음'이 더 잘 맞아야 배지가 참이다**")
    print(f"{'자':<6}{'등급':<6}{'시점':>5}{'IC':>8}{'t':>7}{'Q5−Q1':>9}")
    print("─" * 41)
    for label in HORIZONS:
        res = _by_grade(panel, f"fwd_{label}")
        for g in GRADES:
            ic, sp, tv, nd = res[g]
            if not nd:
                print(f"{label:<6}{g:<6}{0:>5}{'—':>8}{'—':>7}{'—':>9}")
                continue
            ts = f"{tv:>7.2f}" if np.isfinite(tv) else f"{'—':>7}"
            print(f"{label:<6}{g:<6}{nd:>5}{ic:>8.3f}{ts}{sp:>9.1%}")
        print()

    print("읽는 법 — 등급 순서(높음 > 중간 > 낮음)대로 IC와 간격이 줄어들어야 배지가 참이다.")
    print("⚠ 등급 집단은 종목 구성이 서로 다르다. 이 표는 연관만 보여 주며 원인을 밝히지 못한다.")
    print("⚠ 대체 지표를 즉석에서 만들지 마라 — 결과를 보고 기준을 만드는 일이다(사전등록 먼저).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
