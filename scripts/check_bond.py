"""채권 헤드리스 검증 — 수학 자체검증(교과서 값 대조) + 데이터 수집 확인.

실행: python scripts/check_bond.py           (수학만, 네트워크 불필요)
      python scripts/check_bond.py --data    (금리 수집까지)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.analysis.bond_math import (bond_metrics, bond_price, price_yield_points,
                                    rate_scenarios)


def check_math() -> int:
    fails = 0

    def ok(name, cond, detail=""):
        nonlocal fails
        print(f"  {'✓' if cond else '✗ FAIL'} {name} {detail}")
        if not cond:
            fails += 1

    print("[1] 액면발행: 쿠폰=YTM이면 가격=액면(100)")
    for cpn in (0.02, 0.04, 0.06):
        for yrs in (3, 10, 30):
            p = bond_price(100, cpn, cpn, yrs, freq=2)
            ok(f"쿠폰 {cpn*100:.0f}% {yrs}년", abs(p - 100) < 1e-9, f"→ {p:.6f}")

    print("[2] 무이표채: 맥컬리 듀레이션 = 잔존만기")
    for yrs in (5, 10):
        m = bond_metrics(100, 0.0, 0.04, yrs, freq=2)
        ok(f"제로쿠폰 {yrs}년", abs(m["macaulay"] - yrs) < 1e-9, f"→ D={m['macaulay']:.6f}")

    print("[3] 교과서 값: 4% 쿠폰·4% YTM·10년·반기")
    m = bond_metrics(100, 0.04, 0.04, 10, freq=2)
    ok("맥컬리 8.1~8.5년", 8.1 < m["macaulay"] < 8.5, f"→ {m['macaulay']:.3f}")
    ok("수정 = 맥컬리/(1+y)", abs(m["modified"] - m["macaulay"] / 1.02) < 1e-12,
       f"→ {m['modified']:.3f}")
    ok("볼록성 > 0", m["convexity"] > 0, f"→ {m['convexity']:.1f}")
    ok("DV01 ≈ P·MD·1e-4", abs(m["dv01"] - m["price"] * m["modified"] * 1e-4) < 1e-12)

    print("[4] 시나리오: 근사 오차·비대칭(볼록성)")
    rows = rate_scenarios(100, 0.04, 0.04, 10, freq=2)
    r = {row["shock_bp"]: row for row in rows}
    up, dn = r[+100], r[-100]
    ok("금리↓ 이득 > 금리↑ 손실 (볼록성 비대칭)", abs(dn["exact_pct"]) > abs(up["exact_pct"]),
       f"→ −100bp {dn['exact_pct']*100:+.2f}% vs +100bp {up['exact_pct']*100:+.2f}%")
    err_dur = abs(up["dur_pct"] - up["exact_pct"])
    err_dc = abs(up["durconv_pct"] - up["exact_pct"])
    ok("볼록성 보정이 듀레이션 근사보다 정확", err_dc < err_dur,
       f"→ 오차 {err_dur*100:.3f}%p → {err_dc*100:.3f}%p")

    print("[5] price-yield 곡선 단조감소")
    import numpy as np
    grid = np.linspace(0.005, 0.10, 40)
    prices = price_yield_points(100, 0.04, 10, 2, grid)
    ok("YTM↑ → 가격↓", bool(np.all(np.diff(prices) < 0)))

    return fails


def check_data() -> int:
    fails = 0
    from src.data.bonds import fetch_policy_rates, fetch_yield_curve, fetch_yield_history

    kr = fetch_yield_curve("KR")
    print(f"KR 곡선: {len(kr)}개 테너 {list(kr.index)}")
    print(kr.to_string() if not kr.empty else "  (비어 있음)")
    if kr.empty:
        fails += 1

    us = fetch_yield_curve("US")
    print(f"US 곡선: {len(us)}개 테너 {list(us.index)}")
    if us.empty:
        fails += 1

    hist = fetch_yield_history("KR", 10, days=120)
    print(f"KR 10년 시계열: {len(hist)}일 "
          f"({hist.index[0].date()} ~ {hist.index[-1].date()})" if len(hist) else "KR 10년 시계열: 없음")
    if len(hist) < 30:
        fails += 1

    pol = fetch_policy_rates()
    print(f"기준금리: {pol}")
    if not pol:
        fails += 1
    return fails


if __name__ == "__main__":
    total = check_math()
    if "--data" in sys.argv:
        print()
        total += check_data()
    print(f"\n{'모든 검증 통과 ✓' if total == 0 else f'실패 {total}건 ✗'}")
    sys.exit(1 if total else 0)
