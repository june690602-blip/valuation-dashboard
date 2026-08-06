"""축을 하나 더할 값이 있나 — **임의 임계 대신 표준 검정으로** 묻는다.

    python scripts/check_incremental.py
    python scripts/check_incremental.py --market US --add EPV

ADR-0016은 이 질문에 *"③이 빠진 종목 중 새 축이 서는 비율 ≥ 50%"*라는 관문을 썼다.
그 문서가 스스로 *"임계는 판단값이다"*라고 적었고, 실측해 보니 **지표가 뒤집혀 있다** —
한국은 관문 미달(43.4%)인데 커버리지를 **더 많이**(+22.9%p) 얻고, 미국은 통과(58.4%)인데
**덜** 얻는다(+20.7%p). 조건부 비율은 *"남은 문제의 몇 %를 푸나"*라서, ③이 이미 많이
덮은 시장일수록 분모가 작아져 쉽게 통과한다. **얻는 양과 반대로 움직인다.**

그래서 질문을 문헌이 이미 정리해 둔 형태로 바꾼다.

**Biddle·Seow·Siegel (1995)**은 두 질문을 갈랐다:
  - *relative*    — 둘 중 어느 것이 나은가 (배타적 선택)
  - *incremental* — A가 있는 상태에서 **B가 뭔가를 더하나** (A는 주어진 것)
EPV의 질문은 명백히 **incremental**이다. 그리고 그 검정은 **회귀계수가 0이냐**를 묻지,
커버리지 비율을 묻지 않는다.

예측 쪽 문헌에서는 같은 것을 **forecast encompassing**이라 부른다 — A가 B를 '포함'하면
B를 더해도 오차가 안 준다. 둘 다 **임의 임계가 없다.** 통계적 유의성이 기준이다.

구현은 **Fama-MacBeth**다: 시점마다 횡단면 회귀를 돌려 계수를 얻고, 그 계수들의
시계열에 t검정을 한다. 이 패널이 종목은 많고 **시점이 한 자릿수**라 딱 맞는 방법이다
(종목 수로 t검정하면 표본을 1,000배 부풀린 거짓이 된다 — ADR-0028 한계 1).
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

from check_backtest_combos import AXES, EPV, RIM, W, _plain  # noqa: E402

DIRS = {"KR": "backtest", "US": "backtest_us"}


def _z(s: pd.Series) -> pd.Series:
    """횡단면 순위를 표준화. 순위를 쓰는 이유는 IC와 같다 — 괴리율 꼬리가 회귀를 끈다."""
    r = s.rank()
    return (r - r.mean()) / r.std(ddof=0) if r.std(ddof=0) > 0 else r * 0.0


def fama_macbeth(panel: pd.DataFrame, base_fn, add_col: str, ret: str) -> dict:
    """시점별 횡단면 회귀 → 계수 시계열 → t검정.

        수익률 = a + b1·(기존 종합) + b2·(더할 축)

    b2가 0과 다르면 그 축은 **기존 종합이 담지 못한 정보**를 갖고 있다는 뜻이다.
    """
    rows = []
    for t, sub in panel.groupby("date"):
        d = sub.copy()
        d["base"] = d.apply(base_fn, axis=1)
        d = d.dropna(subset=["base", add_col, ret])
        if len(d) < 50:
            continue
        X = np.column_stack([np.ones(len(d)), _z(d["base"]), _z(d[add_col])])
        # **수익률도 시점 안에서 표준화한다.** 안 하면 계수가 그 해의 횡단면 수익률
        # 분산에 비례해 버려서(2020년은 분산이 유별났다) 계수 시계열이 들쭉날쭉해지고
        # t가 눌린다 — 실제로 그렇게 재니 한국 종합조차 t=0.49로 나왔다(IC로는 4.66).
        # 표준화하면 계수가 **편상관**이 되어 시점끼리 비교 가능해지고, IC와 같은 눈금이 된다.
        y = _z(d[ret]).to_numpy(float)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        rows.append({"date": t, "n": len(d), "b_base": beta[1], "b_add": beta[2]})
    if not rows:
        return {}
    df = pd.DataFrame(rows)

    def t_of(x):
        x = np.asarray(x, float)
        x = x[np.isfinite(x)]
        if len(x) < 2 or x.std(ddof=1) == 0:
            return np.nan, np.nan
        return float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))

    mb, tb = t_of(df["b_base"])
    ma, ta = t_of(df["b_add"])
    return {"k": len(df), "n_med": int(df["n"].median()),
            "b_base": mb, "t_base": tb, "b_add": ma, "t_add": ta}


def coverage(panel: pd.DataFrame, have: str, add: str) -> dict:
    """커버리지 — **조건부 비율이 아니라 절대 증가분**으로 낸다."""
    h, a = panel[have].notna(), panel[add].notna()
    return {"before": h.mean(), "after": (h | a).mean(),
            "gain_pp": (a & ~h).mean() * 100,
            "cond": (a & ~h).sum() / max(1, (~h).sum()),
            "naked_before": (~h).mean(), "naked_after": (~(h | a)).mean()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="both", choices=["KR", "US", "both"])
    ap.add_argument("--add", default=EPV, help="더할 축 이름")
    ap.add_argument("--horizon", default="fwd_12m")
    args = ap.parse_args()
    markets = ["KR", "US"] if args.market == "both" else [args.market]

    print("■ 검정 1 — 증분 정보량 (Biddle·Seow·Siegel 1995 / forecast encompassing)")
    print("   수익률 = a + b1·(현행 4축 종합) + b2·(EPV)   ← b2가 0이면 더할 것이 없다")
    print("   Fama-MacBeth: 시점별 회귀 → 계수 시계열에 t검정\n")
    print(f"{'시장':<6}{'시점':>5}{'종목중앙':>9}{'b1(종합)':>10}{'t':>7}"
          f"{'b2(EPV)':>10}{'t':>7}{'판정':>12}")
    print("─" * 68)
    for mk in markets:
        p = pd.read_parquet(ROOT / "data" / DIRS[mk] / "panel.parquet")
        p["date"] = pd.to_datetime(p["date"])
        for m in AXES:
            if m not in p.columns:
                p[m] = np.nan
        r = fama_macbeth(p, _plain(W), args.add, args.horizon)
        if not r:
            continue
        verdict = "정보 있음" if abs(r["t_add"]) > 2 else "**더할 것 없음**"
        print(f"{mk:<6}{r['k']:>5}{r['n_med']:>9}{r['b_base']:>10.4f}{r['t_base']:>7.2f}"
              f"{r['b_add']:>10.4f}{r['t_add']:>7.2f}{verdict:>12}")

    print("\n■ 검정 2 — 커버리지 (조건부 비율이 아니라 **절대 증가분**)")
    print("   재는 것: '절대가치 축 없이 내려지는 판정'의 비율이 얼마나 주나\n")
    print(f"{'시장':<6}{'③만':>8}{'③+EPV':>9}{'증가':>9}"
          f"{'│ 근거없는판정 전':>17}{'후':>7}{'│ 옛관문':>9}")
    print("─" * 68)
    for mk in markets:
        p = pd.read_parquet(ROOT / "data" / DIRS[mk] / "panel.parquet")
        for m in AXES:
            if m not in p.columns:
                p[m] = np.nan
        c = coverage(p, RIM, args.add)
        old = f"{'통과' if c['cond'] >= 0.5 else '미달'} ({c['cond']:.0%})"
        print(f"{mk:<6}{c['before']:>8.1%}{c['after']:>9.1%}{c['gain_pp']:>8.1f}pp"
              f"{c['naked_before']:>17.1%}{c['naked_after']:>7.1%}{old:>13}")

    print("\n옛 관문(조건부 ≥50%)은 **분모가 작을수록 쉽게 통과**한다 — ③이 이미 많이"
          "\n덮은 시장이 유리해진다. 그래서 얻는 양과 반대로 움직인다. 절대 증가분에는"
          "\n그 뒤집힘이 없다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
