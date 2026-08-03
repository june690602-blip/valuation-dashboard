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
