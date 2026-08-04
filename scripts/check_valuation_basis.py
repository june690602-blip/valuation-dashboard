"""판정이 실제로 무엇에 기대고 있나 — 절대가치 대 상대가치의 **실효** 비중.

    python scripts/check_valuation_basis.py
    python scripts/check_valuation_basis.py --limit 60

명목 가중(① 38.5 · ② 38.5 · ③ 23.1)이 아니라, 방법이 제외되고 재정규화된 뒤의
**실제 가중**(`ValuationResult.weights`)을 종목마다 모아 평균한다. 둘은 크게 다르다 —
③ RIM이 장부가 게이트(ADR-0007·0010)에 자주 걸려 빠지기 때문이다.

가르는 기준:
  절대가치(내재가치) — 회사가 벌 것과 할인율에서 값을 만든다. 지금은 ③ RIM뿐이다.
  상대가치           — 시장이 매긴 배수가 입력이다. ①은 다른 회사가 시장에서 받는
                       배수, ②는 자기가 과거에 시장에서 받던 배수다. ⑤(ADR-0015)도
                       회귀 배수를 쓰므로 상대가치 쪽이다.

네트워크가 필요하다(전 종목 재무·피어). CI가 아니라 check_size_bias.py와 같은 수동 계열이다.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.capital_cost import compute_capital_cost          # noqa: E402
from src.analysis.indicators import compute_indicators              # noqa: E402
from src.analysis.valuation import (INTRINSIC_METHODS,              # noqa: E402
                                    RELATIVE_METHODS, compute_valuation)
from src.data.kr_provider import KRProvider                         # noqa: E402
from src.data.universe import get_kr_listing                        # noqa: E402
from src.data.universe_multiples import coefficients_or_none        # noqa: E402

# KRProvider 기본 가정 (US는 0.045/0.05). rf·mrp는 필수 인자다.
RF, MRP = 0.035, 0.06

# 분류는 `src/analysis/valuation.py`가 갖는다(ADR-0018) — 화면이 같은 값을 내므로
# 진단이 따로 들고 있으면 둘이 어긋난다.
INTRINSIC = set(INTRINSIC_METHODS)
RELATIVE = set(RELATIVE_METHODS)


def _one(code: str, coef) -> dict | None:
    try:
        d = KRProvider().load(code, peer_count=9)
        ind = compute_indicators(d)
        cc = compute_capital_cost(d, rf=RF, mrp=MRP)
        v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
    except Exception:
        return None
    if v.verdict is None:
        return None
    w = v.weights or {}
    return {"code": code, "name": d.name, "n_method": len(w),
            "intrinsic": sum(x for m, x in w.items() if m in INTRINSIC),
            "relative": sum(x for m, x in w.items() if m in RELATIVE)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40,
                    help="표본 종목 수 (시총 5분위에 고르게 배분)")
    args = ap.parse_args()

    L = get_kr_listing()
    pool = L[L["is_common"] & (L["Marcap"] > 0)].copy()
    pool["q"] = pd.qcut(pool["Marcap"], 5, labels=["Q1 최소형", "Q2", "Q3", "Q4", "Q5 최대형"])
    per_q = max(1, args.limit // 5)
    samp = (pool.groupby("q", observed=True)
                .apply(lambda g: g.sample(min(per_q, len(g)), random_state=11),
                       include_groups=False)
                .reset_index())

    coef = coefficients_or_none("KR")
    with ThreadPoolExecutor(6) as ex:
        rows = [r for r in ex.map(lambda c: _one(c, coef), samp["Code"]) if r]
    if not rows:
        print("판정이 나온 종목이 없다 — 네트워크나 캐시를 확인하라.")
        return 1

    df = pd.DataFrame(rows)
    df["q"] = samp.set_index("Code").loc[df["code"], "q"].to_numpy()

    print(f"표본 {len(samp)}종목 · 판정이 나온 종목 {len(df)}\n")
    print(f"{'구간':<10}{'n':>4}{'방법수중앙':>11}{'상대가치':>10}{'절대가치':>10}{'RIM 제외':>10}")
    print("─" * 56)
    for q in ["Q1 최소형", "Q2", "Q3", "Q4", "Q5 최대형"]:
        g = df[df["q"] == q]
        if not len(g):
            continue
        print(f"{q:<10}{len(g):>4}{g['n_method'].median():>11.0f}"
              f"{g['relative'].mean():>10.1%}{g['intrinsic'].mean():>10.1%}"
              f"{(g['intrinsic'] == 0).mean():>10.0%}")

    rel, intr = df["relative"].mean(), df["intrinsic"].mean()
    gone = (df["intrinsic"] == 0).mean()
    print(f"\n전체 · 상대가치 {rel:.1%} · 절대가치 {intr:.1%} · "
          f"절대가치가 아예 빠진 종목 {gone:.0%}")
    print(f"방법 수 중앙 {df['n_method'].median():.0f}개 "
          "('삼각측량'이라 부르지만 실제로 몇 개인지 여기서 본다)")
    print("\n이 값이 명목 가중과 크게 다르면, 화면이 말하는 판정 근거와 실제가 어긋난 것이다.\n"
          "자세한 것은 docs/HANDOFF-NEXT.md 참조.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
