"""채권 수학 — 가격·듀레이션·볼록성·DV01·금리 시나리오 (순수 함수).

관례: 금리는 소수(0.04=4%), 이표는 연 freq회(기본 반기 2회) 지급, 만기 일시상환 액면채.

핵심 관계:
- 가격  P = Σ C/(1+y)ᵏ + F/(1+y)ᴺ                (y = YTM/freq, 기간 단위)
- 맥컬리 듀레이션 D = Σ tₖ·PVₖ / P                (tₖ는 '년' 단위)
- 수정 듀레이션  MD = D / (1+y)
- 볼록성 C* = Σ PVₖ·k(k+1) / (P·(1+y)²·freq²)     ('년²' 단위)
- 근사  ΔP/P ≈ −MD·Δy + ½·C*·Δy²                  (Δy는 연 단위 소수)
- DV01 = P·MD·0.0001                              (1bp당 가격 변화)
"""
from __future__ import annotations

import numpy as np


def _cashflow_pv(face: float, coupon_rate: float, ytm: float, years: float,
                 freq: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """(기간 인덱스 k=1..N, 각 기간 현금흐름의 PV). N은 최소 1.

    반올림은 `round()`가 아니라 floor(x+0.5) — 즉 '.5는 올림'이다. 파이썬의 round는
    은행가 반올림이라 2.5를 2로 내리는데, 프런트(finmath.js)의 Math.round는 3으로
    올린다. 채권 화면은 잔존만기를 0.5년 단위로 받으므로 연 1회 지급 + 2.5년 같은
    조합이 실제로 만들어지고, 그때 두 구현이 기간 수부터 갈렸다(가격 3.8% 차이).
    대조 검사(scripts/check_client_math.py)가 이 자리를 잡아냈다 — 어느 쪽이 옳다기보다
    **둘이 같아야** 하고, 흔한 관례(.5는 올림) 쪽으로 맞췄다.
    """
    n = max(int(np.floor(years * freq + 0.5)), 1)
    k = np.arange(1, n + 1, dtype=float)
    y = ytm / freq
    cf = np.full(n, face * coupon_rate / freq)
    cf[-1] += face
    pv = cf / (1.0 + y) ** k
    return k, pv


def bond_price(face: float, coupon_rate: float, ytm: float, years: float,
               freq: int = 2) -> float:
    """액면채 가격 (이표 freq회/년, 만기 일시상환)."""
    _, pv = _cashflow_pv(face, coupon_rate, ytm, years, freq)
    return float(pv.sum())


def bond_metrics(face: float, coupon_rate: float, ytm: float, years: float,
                 freq: int = 2) -> dict:
    """가격·맥컬리/수정듀레이션·볼록성·DV01을 한 번에.

    반환: {price, macaulay, modified, convexity, dv01}
    """
    k, pv = _cashflow_pv(face, coupon_rate, ytm, years, freq)
    price = float(pv.sum())
    y = ytm / freq
    t_years = k / freq
    macaulay = float((t_years * pv).sum() / price)
    modified = macaulay / (1.0 + y)
    convexity = float((pv * k * (k + 1.0)).sum() / (price * (1.0 + y) ** 2 * freq ** 2))
    dv01 = price * modified * 1e-4
    return {"price": price, "macaulay": macaulay, "modified": modified,
            "convexity": convexity, "dv01": dv01}


def rate_scenarios(face: float, coupon_rate: float, ytm: float, years: float,
                   freq: int = 2,
                   shocks_bp: tuple = (-100, -50, -25, +25, +50, +100)) -> list[dict]:
    """금리 충격별 가격 변화 — 듀레이션 근사 vs 볼록성 보정 vs 정확 재계산.

    반환 행: {shock_bp, exact_price, exact_pct, dur_pct, durconv_pct}
    (pct는 소수: 0.05 = +5%)
    """
    m = bond_metrics(face, coupon_rate, ytm, years, freq)
    p0, md, cv = m["price"], m["modified"], m["convexity"]
    rows = []
    for bp in shocks_bp:
        dy = bp / 1e4
        exact = bond_price(face, coupon_rate, max(ytm + dy, 0.0), years, freq)
        rows.append({
            "shock_bp": bp,
            "exact_price": exact,
            "exact_pct": exact / p0 - 1.0,
            "dur_pct": -md * dy,
            "durconv_pct": -md * dy + 0.5 * cv * dy ** 2,
        })
    return rows


def price_yield_points(face: float, coupon_rate: float, years: float, freq: int,
                       ytm_grid: np.ndarray) -> np.ndarray:
    """price-yield 곡선용 — YTM 격자에 대한 가격 배열."""
    return np.array([bond_price(face, coupon_rate, float(y), years, freq)
                     for y in np.asarray(ytm_grid)])
