"""⑤ 정규화 이익 — 커버리지와 효과 (ADR-0015).

    python scripts/check_normalized.py
    python scripts/check_normalized.py --limit 150

네트워크가 필요하다(전 종목 재무). CI가 아니라 check_size_bias.py와 같은 수동 계열이다.

성패 판정(ADR-0015 · 설계 합의문):
  - 커버리지: 무작위 전 종목 표본에서 ⑤가 서는 비율 50% 이상
              (설계 시 측정한 63.6%는 '이력 6년 이상' 안에서의 값이라 그대로 못 쓴다)
  - 효과    : normalized_ratio가 1.5배 밖인 종목에서 ⑤와 ①의 괴리율이 갈려야 한다
  - 보존    : ratio가 1에 가까운 종목에서는 ⑤와 ①이 비슷해야 한다
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.capital_cost import compute_capital_cost        # noqa: E402
from src.analysis.indicators import compute_indicators            # noqa: E402
from src.analysis.valuation import compute_valuation              # noqa: E402
from src.data.kr_provider import KRProvider                       # noqa: E402
from src.data.universe import get_kr_listing                      # noqa: E402
from src.data.universe_multiples import coefficients_or_none      # noqa: E402

COVERAGE_FLOOR = 0.50
RF, MRP = 0.035, 0.06          # KRProvider 기본 가정. rf·mrp는 필수 인자다.


def one(code: str, coef) -> dict | None:
    try:
        d = KRProvider().load(code, peer_count=9)
        ind = compute_indicators(d)
        cc = compute_capital_cost(d, rf=RF, mrp=MRP)
        v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
    except Exception:
        return None
    if not d.price:
        return None
    mids = {e.method: e.mid for e in v.estimates}
    return {
        "code": code, "name": d.name,
        "has5": "정규화 이익" in mids,
        "years": v.normalized_years,
        "ratio": v.normalized_ratio,
        "gap5": (mids["정규화 이익"] / d.price - 1) if "정규화 이익" in mids else np.nan,
        "gap1": (mids["업종 상대가치"] / d.price - 1) if "업종 상대가치" in mids else np.nan,
        "skip": next((r for m, r in v.skipped if m == "정규화 이익"), ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()

    L = get_kr_listing()
    pool = L[L["is_common"] & (L["Marcap"] > 0)]
    codes = pool.sample(args.limit, random_state=3)["Code"].tolist()
    coef = coefficients_or_none("KR")

    with ThreadPoolExecutor(6) as ex:
        rows = [r for r in ex.map(lambda c: one(c, coef), codes) if r]
    if not rows:
        print("분석에 성공한 종목이 없다 — 네트워크나 캐시를 확인하라.")
        return 1
    df = pd.DataFrame(rows)
    print(f"표본 {len(codes)}종목 · 분석 성공 {len(df)}종목\n")

    cov = df["has5"].mean()
    mark = "[확인]" if cov >= COVERAGE_FLOOR else "[문제]"
    print(f"{mark} 커버리지 {cov:.1%} (기준선 {COVERAGE_FLOOR:.0%})")
    miss = df.loc[~df["has5"], "skip"]
    if len(miss):
        print("      제외 사유:")
        for reason, n in miss.value_counts().items():
            print(f"        {n:>3}곳  {reason}")
    used = df.loc[df["has5"], "years"].dropna()
    if len(used):
        print(f"      평균에 쓴 연수 중앙 {used.median():.0f}년 "
              f"(5년을 채운 종목 {(used >= 5).mean():.0%})")

    both = df[df["has5"] & df["gap1"].notna() & df["gap5"].notna()].copy()
    bad = 0 if cov >= COVERAGE_FLOOR else 1
    if len(both):
        both["diff"] = (both["gap5"] - both["gap1"]).abs()
        has_r = both["ratio"].notna()
        far = both[has_r & ((both["ratio"] < 1 / 1.5) | (both["ratio"] > 1.5))]
        near = both[has_r & (both["ratio"] >= 1 / 1.5) & (both["ratio"] <= 1.5)]
        print(f"\n효과 — ⑤와 ①의 괴리율 차이 (n={len(both)})")
        if len(far):
            print(f"  정규화가 크게 되돌린 종목(ratio 1.5배 밖, n={len(far)}) "
                  f"차이 중앙 {far['diff'].median():.1%}")
        if len(near):
            print(f"  되돌릴 게 적은 종목(ratio 1.5배 안, n={len(near)}) "
                  f"차이 중앙 {near['diff'].median():.1%}")
        if len(far) and len(near):
            ok = far["diff"].median() > near["diff"].median()
            bad += 0 if ok else 1
            print(f"  {'[확인]' if ok else '[문제]'} "
                  "정규화 폭이 큰 쪽에서 더 갈려야 한다 "
                  "— 안 갈리면 축을 더한 값이 없다")

        print("\n가장 크게 갈린 5곳:")
        for r in both.nlargest(5, "diff").itertuples():
            rt = f"{r.ratio:.2f}" if pd.notna(r.ratio) else "—"
            print(f"  {r.code} {str(r.name)[:12]:<14} ratio {rt:>5}  "
                  f"① {r.gap1:+7.1%}  ⑤ {r.gap5:+7.1%}")

    print(f"\n문제 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
