"""축 조합을 바꾸면 무슨 일이 일어나나 — 커버리지·독립성·화면 등급을 함께 본다.

    python scripts/check_axis_combos.py KR --limit 200
    python scripts/check_axis_combos.py US --limit 200

`check_epv_viability.py`가 *"EPV를 **더할까**"*를 물었다면 이쪽은 *"무엇을 **쓸까**"*를
묻는다. 축을 빼거나 갈아 끼우는 선택지까지 같은 표본에서 나란히 잰다.

**`n_eff`만 보면 방법을 줄일수록 좋아 보인다.** 겹치는 축을 빼면 실질 축 수가 오르기
때문이다. 그러나 그것은 눈금을 좋게 만들려고 정보를 버리는 것이다. 세 가지를 함께
봐야 선택이 정해진다:

  1. **커버리지** — 이 조합으로 판정이 아예 안 나오는 종목이 몇 %인가
  2. **독립성**   — 실질 축 수(`n_eff`, ADR-0022)
  3. **화면 결과** — 신뢰도 등급이 실제로 어떻게 붙는가. 절대가치 축이 있는 비율은
                    얼마인가(ADR-0018), 판정 하나가 방법 하나에만 매달리는 비율은

**가격이 약분되므로 로그 괴리율만으로 등급까지 낼 수 있다.** 흩어짐은
`std(mid)/|mean(mid)|`인데 `mid = price × exp(g)`이므로 `price`가 분자·분모에서
사라진다 — 변동계수는 척도 불변이다. 무작위 10,000회로 확인했다(최대 절대오차 6.7e-16,
부동소수점 한계). 그래서 수집한 로그 괴리율만으로 **각 조합에서 화면이 실제로 뭐라고
말할지**를 재구성할 수 있다.

등급은 `confidence_grade`를, 실질 축 수는 `effective_axes`를 **그대로 부른다.** 다시
구현하면 "진단은 통과인데 화면은 다른 말"이 된다.

네트워크가 필요하다. CI가 아니라 check_epv_viability.py와 같은 수동 계열이다.
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from check_epv_viability import EPV, MIN_PAIR_N, RIM, _collect           # noqa: E402
from src.analysis.valuation import (FUNDAMENTAL_METHODS,                 # noqa: E402
                                    confidence_grade)
from src.analysis.warranted import effective_axes                        # noqa: E402
from src.data.universe_multiples import coefficients_or_none             # noqa: E402

ALL = (*FUNDAMENTAL_METHODS, EPV)
SHORT = {"업종 상대가치": "①상대", "역사적 밴드": "②밴드", RIM: "③RIM",
         "정규화 이익": "⑤정규", EPV: "EPV"}
CURRENT = frozenset(FUNDAMENTAL_METHODS)
# 값을 회사가 벌 것과 할인율에서 만드는 축 (ADR-0018의 분류에 EPV를 더한 것)
INTRINSIC = {RIM, EPV}


def _corr_table(df: pd.DataFrame) -> dict:
    """이번 표본에서 전 쌍 상관. **조합 비교는 한 표본 안에서** 해야 공평하다 —
    기존 6쌍만 `METHOD_RHO`(다른 표본)에서 가져오면 EPV에만 이번 잡음이 실린다."""
    table = {}
    for a, b in itertools.combinations(sorted(ALL), 2):
        if a not in df.columns or b not in df.columns:
            continue
        both = df[[a, b]].dropna()
        if len(both) >= MIN_PAIR_N:
            table[(a, b)] = float(both[a].corr(both[b]))
    return table


def _evaluate(df: pd.DataFrame, combo: tuple, rho: dict, market: str) -> dict:
    """이 조합을 썼다면 무슨 일이 일어나나 — 종목 하나하나 되짚는다."""
    n_verdict = single = abs_have = 0
    neffs, disps, grades = [], [], []
    for _i, r in df.iterrows():
        stand = [m for m in combo
                 if (m in r["methods"]) or (m == EPV and r["epv_ready"])]
        # 로그 괴리율이 실제로 있는 것만 — 값이 없으면 흩어짐을 못 낸다
        stand = [m for m in stand if m in r.index and pd.notna(r.get(m))]
        if not stand:
            continue
        n_verdict += 1
        single += len(stand) == 1
        abs_have += any(m in INTRINSIC for m in stand)
        rel = np.exp(np.array([float(r[m]) for m in stand]))   # price 약분
        disp = float(np.std(rel) / abs(np.mean(rel))) if len(rel) >= 2 else None
        ne, capped = effective_axes(stand, market, rho)
        neffs.append(ne)
        if disp is not None:
            disps.append(disp)
        grades.append(confidence_grade(disp, len(stand), ne, capped)[0])
    g = pd.Series(grades, dtype=object)
    d = max(1, n_verdict)
    return {"combo": combo, "판정": n_verdict / len(df),
            "절대축": abs_have / d, "1개뿐": single / d,
            "n_eff": float(np.median(neffs)) if neffs else np.nan,
            "흩어짐": float(np.median(disps)) if disps else np.nan,
            "높음": float((g == "높음").mean()) if len(g) else 0.0,
            "중간": float((g == "중간").mean()) if len(g) else 0.0,
            "낮음": float((g == "낮음").mean()) if len(g) else 0.0}


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
        print(f"판정이 난 종목이 {len(df)}곳뿐이다 — 조합을 비교할 표본이 아니다.")
        return 1
    print(f"{market} — 판정 {len(df)} / 시도 {tried} (성공률 {len(df)/tried:.0%})")
    rho = _corr_table(df)

    rows = []
    for size in (3, 4, 5):
        for combo in itertools.combinations(ALL, size):
            # 상관을 모르는 쌍이 있는 조합은 뺀다 — `effective_axes`가 상한을 통째로
            # 꺼서(ADR-0022 결정 3) 그 조합만 등급이 후해진다. 비교가 공평하지 않다.
            if any((a, b) not in rho
                   for a, b in itertools.combinations(sorted(combo), 2)):
                continue
            rows.append(_evaluate(df, combo, rho, market))
    if not rows:
        print("상관을 낸 쌍이 부족해 비교할 조합이 없다 — --limit을 올려라.")
        return 1

    hdr = (f"{'구성':<26}{'판정':>7}{'절대축':>8}{'1개뿐':>8}"
           f"{'n_eff':>8}{'흩어짐':>8}{'높음':>7}{'중간':>7}{'낮음':>7}")
    for size in (3, 4, 5):
        sub = [r for r in rows if len(r["combo"]) == size]
        if not sub:
            continue
        sub.sort(key=lambda r: (-r["판정"], -r["n_eff"]))
        print(f"\n[{size}개 조합] — 판정 커버리지 순\n{hdr}\n" + "─" * 86)
        for r in sub:
            mark = "  ←지금" if frozenset(r["combo"]) == CURRENT else ""
            name = " ".join(SHORT[m] for m in r["combo"])
            print(f"{name:<26}{r['판정']:>7.0%}{r['절대축']:>8.0%}{r['1개뿐']:>8.0%}"
                  f"{r['n_eff']:>8.2f}{r['흩어짐']:>8.3f}"
                  f"{r['높음']:>7.0%}{r['중간']:>7.0%}{r['낮음']:>7.0%}{mark}")

    print("\n판정 = 전체 수집 종목 중 판정이 나온 비율.")
    print("절대축·1개뿐·등급 = 판정이 난 종목 대비. '1개뿐'은 방법 하나에만 매달린 판정이다.")
    print("**어느 조합이 맞는지 이 표는 답하지 않는다** — 적중률을 재는 수단이 없다"
          "(ADR-0009). 재는 것은 얼마나 많이 서나·얼마나 겹치나·화면이 뭐라 말하나뿐이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
