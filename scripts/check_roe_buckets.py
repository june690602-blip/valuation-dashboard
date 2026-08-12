# -*- coding: utf-8 -*-
"""ROE 최상위 칸을 닫아야 하나 — 한 스냅숏 위에서 후보 넷을 동시에 잰다.

    python scripts/check_roe_buckets.py KR
    python scripts/check_roe_buckets.py US

네트워크가 필요하다(전 종목 스냅숏). CI가 아니라 check_warranted.py와 같은 수동 계열이다.
사전등록: docs/review/2026-08-10-roe버킷-사전등록.md

## 왜 이 스크립트가 따로 있나

`check_warranted.py`를 **두 번 돌려 before/after를 비교하면 안 된다.** 그 사이 시장
스냅숏이 달라져 **다른 표본을 비교**하게 된다. ADR-0035가 *"패널이 재현되지 않는다"*고
잘못 적은 원인이 정확히 그것이었다(ADR-0037이 정정). 그래서 **한 번 받은 스냅숏 위에서**
네 안을 모두 적합한다.

## 무엇을 재나

`warranted.roe_bucket()`의 최상위 구간이 `>15%`로 **열려 있다.** 그래서 ROE가 15%를
넘으면 이익이 세 배가 돼도 적정 배수가 한 칸도 안 움직인다(ADR-0038 ㉮). 닫으면
나아지는지 **leave-one-out 오차**로 잰다 — 표본 밖 오차라 칸을 늘려 생기는 과적합이
자동으로 벌점을 받는다.

⚠ **LOO 오차만으로 채택하지 않는다.** 시장 배수를 더 잘 맞히는 모델은 진짜 미스프라이싱
까지 '적정'으로 흡수할 수 있다 — 오차는 줄고 판정은 무뎌진다. 백테스트가 두 번째
관문이다. 사전등록의 경고 절을 읽을 것.
"""
from __future__ import annotations

import contextlib
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis import warranted as W                           # noqa: E402
from src.analysis.warranted import _prep, loo_leg_error           # noqa: E402
from src.data.universe_multiples import (LEG_BOUNDS, collect_kr,  # noqa: E402
                                         collect_us)

# 사전등록한 후보 넷 — **측정 뒤에 늘리지 않는다.** 최상위 칸만 쪼갠다.
BASE_EDGES = [-math.inf, -0.20, -0.05, 0.0, 0.05, 0.10, 0.15, math.inf]
VARIANTS = {
    "A_현행": [],
    "B_+25%": [0.25],
    "C_+25/40%": [0.25, 0.40],
    "D_+25/50/100%": [0.25, 0.50, 1.00],
}
WORSE_LIMIT = 0.02   # LOO 오차가 상대 이만큼 나빠지면 탈락 (사전등록)

# ADR-0038이 잰 값 — 현재 ROE와 선행(컨센서스) ROE. 국면 전환을 칸이 표현하는지 본다.
# 여기 박아 두는 이유: 이 표를 다시 만들려면 종목마다 파이프라인을 돌려야 하는데(네트워크),
# 이 스크립트가 답하는 질문은 "칸이 이 값들을 가르나"라 값 자체는 입력이지 측정 대상이 아니다.
# 출처: docs/review/2026-08-10-forward-multiple-측정.md §4 (2026-08-10 실측).
ROE_PAIRS = [
    ("삼성전자", 0.176, 0.591), ("SK하이닉스", 0.457, 1.538),
    ("NAVER", 0.062, 0.063), ("현대차", 0.072, 0.065), ("카카오", 0.043, 0.058),
    ("Apple", 1.199, 1.291), ("Microsoft", 0.302, 0.394), ("Coca-Cola", 0.396, 0.420),
    ("J&J", 0.248, 0.352), ("Walmart", 0.241, 0.277), ("Alphabet", 0.381, 0.135),
]
PHASE_SHIFT_STOCKS = {"삼성전자", "SK하이닉스", "Alphabet"}   # |선행/TTM−1| ≥ 50%


def labels_for(edges: list[float]) -> list[str]:
    """구간 라벨을 만든다. 더미 이름이라 겹치지만 않으면 된다."""
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        if lo == -math.inf:
            out.append(f"≤{hi:.0%}")
        elif hi == math.inf:
            out.append(f">{lo:.0%}")
        else:
            out.append(f"{lo:.0%}~{hi:.0%}")
    return out


def edges_for(extra: list[float]) -> list[float]:
    return sorted(set(BASE_EDGES[:-1] + list(extra))) + [math.inf]


@contextlib.contextmanager
def roe_edges(extra: list[float]):
    """ROE 구간을 임시로 갈아끼운다 — 진단 전용.

    `roe_bucket`·`fit_leg`이 모듈 전역을 **호출 시점에** 읽으므로 이렇게 바꿔 끼울 수 있다.
    빈 리스트면 아무것도 건드리지 않는다 — 그래야 A(현행)가 **진짜 현행 코드**로 잰 값이 된다.
    """
    if not extra:
        yield BASE_EDGES
        return
    old_e, old_l = W.ROE_EDGES, W.ROE_LABELS
    new_e = edges_for(extra)
    W.ROE_EDGES, W.ROE_LABELS = new_e, labels_for(new_e)
    try:
        yield new_e
    finally:
        W.ROE_EDGES, W.ROE_LABELS = old_e, old_l


def leg_frame(snap: pd.DataFrame, leg: str, lo: float, hi: float):
    """check_warranted.py와 **같은** 방식으로 다리 표본을 만든다 — 안 그러면 비교가 안 된다."""
    v = pd.to_numeric(snap.get(leg), errors="coerce")
    if v is None:
        return None
    d = snap.assign(multiple=v)[(v > lo) & (v < hi) & (snap["mcap"] > 0)]
    return d.dropna(subset=["multiple"]).reset_index(drop=True)


def measure(snap: pd.DataFrame) -> dict:
    """{다리: {후보: {mae, r2_loo, saturated, n_buckets, thin}}}"""
    out: dict = {}
    for leg, (lo, hi) in LEG_BOUNDS.items():
        d = leg_frame(snap, leg, lo, hi)
        if d is None or d.empty:
            continue
        out[leg] = {}
        for name, extra in VARIANTS.items():
            with roe_edges(extra):
                res = loo_leg_error(d)
                prepped = _prep(d)
                counts = (prepped["rb"].value_counts().to_dict()
                          if prepped is not None else {})
            out[leg][name] = {
                "res": res,
                "counts": counts,
                # 표본 30 미만인 칸 — 더미 하나가 소수의 종목에 매달린다
                "thin": sorted(k for k, n in counts.items() if n < 30),
            }
    return out


def print_errors(market: str, table: dict):
    print(f"\n===== {market} · leave-one-out 절대오차 (낮을수록 좋다) =====")
    print(f"{'다리':<12}{'후보':<16}{'표본':>7}{'LOO MAE':>10}{'vs A':>9}"
          f"{'R²(LOO)':>10}{'포화':>6}  얇은 칸(n<30)")
    print("─" * 92)
    for leg, per_var in table.items():
        base = per_var["A_현행"]["res"]
        for name in VARIANTS:
            v = per_var[name]
            r = v["res"]
            if r is None:
                print(f"{leg:<12}{name:<16}{'—':>7}{'적합 실패':>10}")
                continue
            if name == "A_현행" or base is None:
                delta = "기준"
            else:
                delta = f"{(r['mae'] / base['mae'] - 1):+.1%}"
            thin = ", ".join(v["thin"]) if v["thin"] else "—"
            print(f"{leg:<12}{name:<16}{r['n']:>7}{r['mae']:>10.3f}{delta:>9}"
                  f"{r['r2_loo']:>10.3f}{r['saturated']:>6}  {thin}")
        print()


def print_counts(market: str, table: dict):
    """최상위 칸에 표본이 얼마나 있나 — 쪼개서 얻을 것이 있는지를 이 숫자가 정한다.

    한국은 ROE가 낮은 시장이라(코리아 디스카운트) `>15%` 칸이 얇을 수 있다.
    얇으면 쪼개도 나눌 것이 없다. 미국은 반대일 수 있다. 그 차이를 눈으로 보게 한다.
    """
    print(f"===== {market} · 최상위 칸(>15%)에 표본이 얼마나 있나 =====")
    print(f"{'다리':<12}{'전체':>7}{'>15% 칸':>10}{'비중':>8}   B로 쪼갠 뒤")
    print("─" * 70)
    for leg, per_var in table.items():
        a, b = per_var["A_현행"]["counts"], per_var["B_+25%"]["counts"]
        top = a.get(">15%", 0)
        total = sum(a.values()) or 1
        split = " · ".join(f"{k} {b.get(k, 0)}" for k in ("15%~25%", ">25%"))
        print(f"{leg:<12}{total:>7}{top:>10}{top / total:>8.1%}   {split}")
    print()


def print_gate(market: str, table: dict):
    """사전등록한 관문을 그대로 적용한다 — 판단을 사람에게 미루지 않는다."""
    print(f"===== {market} · 관문 (사전등록: 상대 +{WORSE_LIMIT:.0%} 이상 나빠지면 탈락) =====")
    verdict = {}
    for name in VARIANTS:
        if name == "A_현행":
            continue
        worst_leg, worst = None, -1.0
        for leg, per_var in table.items():
            base, cur = per_var["A_현행"]["res"], per_var[name]["res"]
            if base is None or cur is None:
                continue
            rel = cur["mae"] / base["mae"] - 1
            if rel > worst:
                worst_leg, worst = leg, rel
        ok = worst < WORSE_LIMIT
        verdict[name] = ok
        mark = "통과" if ok else "탈락"
        print(f"  {name:<16} 가장 나쁜 다리 {worst_leg or '—'} {worst:+.1%}  → {mark}")
    return verdict


def print_expressibility():
    """진짜 질문 — 국면 전환 종목의 현재/선행 ROE가 다른 칸에 떨어지나."""
    print("\n===== 국면 맞추기를 칸이 표현하나 (★가 국면 전환 종목) =====")
    head = f"{'종목':<14}{'ROE(현재)':>10}{'ROE(선행)':>11}"
    print(head + "".join(f"{n:>16}" for n in VARIANTS))
    print("─" * (len(head) + 16 * len(VARIANTS)))
    split = {n: 0 for n in VARIANTS}
    for label, now, fwd in ROE_PAIRS:
        star = "★" if label in PHASE_SHIFT_STOCKS else " "
        cells = []
        for name, extra in VARIANTS.items():
            with roe_edges(extra):
                differs = W.roe_bucket(now) != W.roe_bucket(fwd)
            cells.append("갈림" if differs else "같은 칸")
            if differs and label in PHASE_SHIFT_STOCKS:
                split[name] += 1
        print(f"{star}{label:<13}{now:>10.1%}{fwd:>11.1%}"
              + "".join(f"{c:>16}" for c in cells))
    n_shift = len(PHASE_SHIFT_STOCKS)
    print(f"\n국면 전환 {n_shift}종목 중 갈리는 수: "
          + " · ".join(f"{n} {split[n]}/{n_shift}" for n in VARIANTS))
    return split


def main() -> int:
    market = (sys.argv[1] if len(sys.argv) > 1 else "KR").upper()
    snap = collect_kr() if market == "KR" else collect_us()
    print(f"{market} 스냅숏 {len(snap)}종목 — 이 하나 위에서 후보 넷을 모두 적합한다")
    assert len(labels_for(W.ROE_EDGES)) == len(W.ROE_LABELS), "라벨 생성기가 현행과 어긋난다"

    table = measure(snap)
    print_counts(market, table)
    print_errors(market, table)
    verdict = print_gate(market, table)
    split = print_expressibility()

    print("\n===== 사전등록한 고르는 법 =====")
    print("관문을 통과하는 것 중 **가장 단순한 것**(칸 수 최소). 점수가 제일 좋은 것을")
    print("고르지 않는다 — 그것이 ADR-0003이 금지한 그 일이다.")
    passed = [n for n in VARIANTS if n != "A_현행" and verdict.get(n)
              and split[n] > 0]
    if not passed:
        print("\n→ **관문+목적을 함께 통과한 후보가 없다. A(현행)를 유지한다.**")
        print("   (오차가 나빠졌거나, 갈리지 않아 칸을 쪼갠 의미가 없다)")
    else:
        print(f"\n→ 이 시장에서 통과: {' · '.join(passed)}  |  가장 단순한 것: **{passed[0]}**")
        print("   ⚠ 두 시장 모두에서 통과해야 하고, 그 다음 백테스트 관문이 남아 있다.")
    print("\n⚠ LOO 오차만으로 채택하지 않는다 — 시장 배수를 더 잘 맞히는 모델은 진짜")
    print("   미스프라이싱까지 '적정'으로 흡수할 수 있다. 백테스트가 두 번째 관문이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
