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

from src.analysis.warranted import fit_leg, loo_leg_error         # noqa: E402
from src.data.universe_multiples import (LEG_BOUNDS, collect_kr,  # noqa: E402
                                         collect_us)

# ADR-0014 실측 기준선(한국). 회귀가 이보다 나빠지면 회귀가 아니다.
BASELINE_R2 = {"pbr": 0.434, "per": 0.395, "psr": 0.351, "ev_ebitda": 0.181}
# `warranted.LEG_MAE`에 지금 박혀 있는 값. 새로 잰 값과 나란히 찍어 **무엇이 얼마나
# 달라졌는지** 보이게 한다 — #115가 규모 항을 스플라인으로 바꾼 뒤 이 표를 다시 재는 것이
# 이 스크립트를 고친 이유다(ADR-0022).
CURRENT_LEG_MAE = {
    "KR": {"pbr": 0.563, "per": 0.572, "psr": 0.921, "ev_ebitda": 0.639},
    "US": {"pbr": 0.470, "per": 0.374},
}


def main() -> int:
    market = (sys.argv[1] if len(sys.argv) > 1 else "KR").upper()
    snap = collect_kr() if market == "KR" else collect_us()
    print(f"{market} 스냅숏 {len(snap)}종목\n")
    # β를 함께 찍는다 — ADR-0014가 규모 효과의 크기를 근거로 결론을 내는데, 그 숫자를
    # 만든 스크립트가 저장소에 없었다. 여기서 찍어야 재현 절이 실제로 재현이 된다.
    print(f"{'다리':<12}{'표본':>7}{'중앙값 R²':>11}{'회귀 R²':>10}{'β(시총)':>10}{'판정':>8}"
          f"{'현 MAE':>9}{'새 MAE':>9}{'(내부표본)':>11}")
    print("─" * 90)
    bad = 0
    new_mae: dict[str, float] = {}
    for leg, (lo, hi) in LEG_BOUNDS.items():
        v = pd.to_numeric(snap.get(leg), errors="coerce")
        if v is None:
            continue
        d = snap.assign(multiple=v)[(v > lo) & (v < hi)
                                    & (snap["mcap"] > 0)].dropna(subset=["multiple"])
        d = d.reset_index(drop=True)
        coef = fit_leg(d, leg=leg)
        if coef is None:
            print(f"{leg:<12}{len(d):>7}{'—':>11}{'—':>10}{'—':>10}{'표본부족':>8}")
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
        # 다리별 leave-one-out 절대오차 — `LEG_MAE`에 넣을 값이다(ADR-0017·0022).
        lo = loo_leg_error(d)
        cur = CURRENT_LEG_MAE.get(market, {}).get(leg)
        if lo is None:
            mae_txt, ins_txt = f"{'—':>9}", f"{'—':>11}"
        else:
            new_mae[leg] = lo["mae"]
            mae_txt, ins_txt = f"{lo['mae']:>9.3f}", f"{lo['mae_in_sample']:>11.3f}"
        print(f"{leg:<12}{len(d):>7}{rm:>11.3f}{rr:>10.3f}"
              f"{coef['beta_size']:>+10.3f}{'[확인]' if good else '[문제]':>8}"
              f"{(f'{cur:.3f}' if cur is not None else '없음'):>9}{mae_txt}{ins_txt}")

    print(f"\n문제 {bad}건")
    if new_mae:
        print("\n새 MAE는 leave-one-out(PRESS 잔차)이고, `warranted.LEG_MAE`에 그대로 넣는 값이다.")
        print("'(내부표본)'은 같은 데이터로 적합하고 같은 데이터를 예측한 값 — 항상 더 좋아")
        print("보이므로 **쓰지 말 것**. 둘의 차이가 클수록 표본 대비 모형이 복잡하다는 뜻이다.")
        print(f'\n    "{market}": {{' + ", ".join(f'"{k}": {v:.3f}' for k, v in new_mae.items()) + "},")
        for leg, v in new_mae.items():
            cur = CURRENT_LEG_MAE.get(market, {}).get(leg)
            if cur is not None and abs(v - cur) / cur >= 0.05:
                print(f"    ※ {leg}: {cur:.3f} → {v:.3f} ({(v/cur - 1):+.0%}) — 화면 문구가 바뀐다")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
