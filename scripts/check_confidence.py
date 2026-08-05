"""신뢰도 산식이 무엇을 재고 있나 — 고치기 전에 재는 값 (docs/HANDOFF-CONFIDENCE.md).

    python scripts/check_confidence.py
    python scripts/check_confidence.py --limit 150 --dump conf.csv

현행 산식은 `valuation.py`의 세 줄이 전부다:

    disp = std(중심값들) / |mean(중심값들)|
    confidence = "높음" if disp < 0.15 else "중간" if disp < 0.35 else "낮음"

**이 산식이 재는 것은 '방법들이 서로 얼마나 가까운가'뿐이다.** 그런데 가까운 것이
'확실하다'가 아니라 **'같은 것을 두 번 쟀다'**일 수 있다. ADR-0012·0014·0015가 각각
경고했는데 산식은 그대로다. 이 스크립트는 그 경고가 실제로 몇 %에서 일어나는지 잰다.

## 다섯 가지를 잰다

    [A] 축 쌍별 상관 — ①②③⑤가 정말 독립인가. 인계문 1번
    [B] 방법 수(n)별 disp 분포 — 문턱 하나가 n마다 다른 것을 뜻하고 있나. 인계문 2번
    [C] '높음'의 내용물 — 높다고 말한 근거가 무엇인가. 인계문 3번
    [D] 신뢰도 × 절대가치 비중 교차표 — ADR-0018과 모순되는 조합. 인계문 4번
    [E] 가중 대 비가중 산포 — 인계문의 '(관찰) 셋째'

[E]는 인계문이 "의도인지 확인이 필요하다"고 남긴 것이다. 판정은 `_weighted(basis)`의
**가중평균**으로 내는데 `disp`는 분자도 분모도 **가중치를 무시한** 단순 통계다.
물어보기 전에 **얼마나 달라지는지부터** 재 둔다 — 차이가 없으면 물어볼 것도 없다.

## 판정 기준 — 전부 판단값이다

데이터로 추정한 값이 아니다(`METHOD_WEIGHTS`·`PBR_GATE`와 같은 성격). 그래서 값을 함께
찍어 읽는 사람이 직접 판단할 수 있게 한다.

    [A] 축 쌍 |상관| > 0.5면 '독립이 아니다' — check_dcf_viability.py의 검사 2와 같은 문턱
    [B] n=2와 n=4의 disp 중앙값이 1.5배 넘게 다르면 '문턱 하나로 재면 안 된다'
    [C] '높음'인데 실효 가중의 과반이 서로 상관된 축(①⑤)에 실린 비율 > 20%
    [D] '높음'인데 절대가치 축이 0%인 비율 > 20%
    [E] 가중/비가중이 신뢰도 **등급**을 바꾸는 비율 > 10%

네트워크가 필요하다(전 종목 재무·피어). CI가 아니라 `check_dcf_viability.py`와 같은
수동 계열이다. **KR만 잰다** — 표본 틀(`get_kr_listing`)이 한국뿐이라 US는 이 결과 밖이다.

종료 코드 1은 '문제 있음'이다.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.capital_cost import compute_capital_cost              # noqa: E402
from src.analysis.indicators import compute_indicators                  # noqa: E402
from src.analysis.valuation import compute_valuation                   # noqa: E402
from src.data.kr_provider import KRProvider                             # noqa: E402
from src.data.universe import get_kr_listing                            # noqa: E402
from src.data.universe_multiples import coefficients_or_none            # noqa: E402

RF, MRP = 0.035, 0.06          # KRProvider 기본 가정

# 판정에 들어가는 네 축. 라벨은 화면 번호와 짝이다.
AXES = {"업종 상대가치": "①", "역사적 밴드": "②",
        "수익가치(RIM)": "③", "정규화 이익": "⑤"}

# ①과 ⑤는 **같은 회귀 적정 배수**를 쓴다(ADR-0015). 서로 독립일 수 없는 쌍이라
# 따로 이름을 붙여 둔다 — [C]가 이 쌍에 실린 무게를 센다.
SHARED_PAIR = ("업종 상대가치", "정규화 이익")

CORR_LIMIT = 0.50        # [A]
DISP_RATIO_LIMIT = 1.5   # [B]
SHARED_SHARE_LIMIT = 0.20  # [C]
NO_INTRINSIC_LIMIT = 0.20  # [D]
REGRADE_LIMIT = 0.10     # [E]


def _grade(disp: float) -> str:
    """현행 문턱 그대로. 여기서 다시 쓰는 이유는 가중 산포로도 등급을 매겨 보기 위해서다."""
    return "높음" if disp < 0.15 else "중간" if disp < 0.35 else "낮음"


def _one(code: str, coef) -> dict | None:
    try:
        d = KRProvider().load(code, peer_count=9)
        ind = compute_indicators(d)
        cc = compute_capital_cost(d, rf=RF, mrp=MRP)
        v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
    except Exception:
        return None
    if v.verdict is None or not d.price or not v.fair_mid:
        return None

    mids = {e.method: e.mid for e in v.estimates}
    used = [m for m in v.weights if m in AXES]      # 판정에 실제로 들어간 축들
    if not used:
        return None

    row = {"code": code, "mcap": d.market_cap, "price": d.price,
           "n_methods": len(used), "confidence": v.confidence,
           "disp": v.dispersion, "intrinsic_share": v.intrinsic_share,
           "gap": v.gap}
    for m, sym in AXES.items():
        # 괴리율로 담는다 — 축끼리 비교하려면 주가로 정규화해야 한다. 중심값 자체는
        # 종목마다 자릿수가 달라 상관이 '주가가 비슷한가'를 재게 된다.
        row[f"gap{sym}"] = (mids[m] / d.price - 1) if m in mids else np.nan
        row[f"w{sym}"] = float(v.weights.get(m, 0.0))

    # 가중 산포 — 판정이 쓰는 가중평균 둘레의 산포다([E]).
    w = np.array([v.weights[m] for m in used], dtype=float)
    x = np.array([mids[m] for m in used], dtype=float)
    wmean = float(np.dot(w, x))                      # = fair_mid (재정규화돼 있다)
    wstd = float(np.sqrt(np.dot(w, (x - wmean) ** 2)))
    row["disp_w"] = wstd / abs(wmean) if wmean else np.nan

    # ①⑤가 함께 선 종목에서 그 둘이 가져간 실효 가중의 합([C]).
    row["shared_w"] = float(sum(v.weights.get(m, 0.0) for m in SHARED_PAIR)
                            if all(m in v.weights for m in SHARED_PAIR) else 0.0)
    return row


def _sec_corr(df: pd.DataFrame) -> int:
    print("[A] 축 쌍별 상관 — 이 넷이 정말 독립인가")
    print(f"    {'쌍':<10}{'n':>6}{'피어슨':>10}{'스피어만':>10}   판정")
    print("    " + "─" * 46)
    bad = 0
    for a, b in combinations(AXES.values(), 2):
        s = df[[f"gap{a}", f"gap{b}"]].dropna()
        if len(s) < 20:
            print(f"    {a}–{b:<7}{len(s):>6}{'—':>10}{'—':>10}   표본부족")
            continue
        r = float(s.corr().iloc[0, 1])
        rho = float(s.corr(method="spearman").iloc[0, 1])
        hit = abs(r) > CORR_LIMIT
        bad += hit
        print(f"    {a}–{b:<7}{len(s):>6}{r:>+10.3f}{rho:>+10.3f}   "
              f"{'[문제] 독립 아님' if hit else '[확인]'}")
    print(f"    → |상관| {CORR_LIMIT:.1f} 초과 {bad}쌍. 상관된 축이 가까이 서면 편차가 작아지고,")
    print("      현행 산식은 그것을 '확실하다'로 읽는다.\n")
    return bad


def _sec_disp_by_n(df: pd.DataFrame) -> int:
    print("[B] 방법 수(n)별 disp 분포 — 문턱 하나가 n마다 다른 것을 뜻하나")
    print(f"    {'n':<4}{'종목':>6}{'disp 중앙':>11}{'p25':>8}{'p75':>8}"
          f"{'높음':>8}{'중간':>8}{'낮음':>8}")
    print("    " + "─" * 61)
    med = {}
    for n, g in df.groupby("n_methods"):
        q = g["disp"].quantile([0.25, 0.50, 0.75])
        med[int(n)] = float(q[0.50])
        share = g["confidence"].value_counts(normalize=True)
        print(f"    {int(n):<4}{len(g):>6}{q[0.50]:>11.3f}{q[0.25]:>8.3f}{q[0.75]:>8.3f}"
              f"{share.get('높음', 0):>8.0%}{share.get('중간', 0):>8.0%}"
              f"{share.get('낮음', 0):>8.0%}")
    if 2 in med and 4 in med and min(med[2], med[4]) > 0:
        ratio = max(med[2], med[4]) / min(med[2], med[4])
        hit = ratio > DISP_RATIO_LIMIT
        print(f"    → n=2 중앙 {med[2]:.3f} 대 n=4 중앙 {med[4]:.3f} — {ratio:.2f}배 "
              f"{'[문제] 같은 자로 재고 있다' if hit else '[확인]'}")
        print("      np.std는 ddof=0이라 n=2면 |a−b|/2다. n이 늘면 같은 산포도 값이 달라진다.\n")
        return int(hit)
    print("    → n=2 또는 n=4 표본이 없어 비교하지 못했다\n")
    return 0


def _sec_high(df: pd.DataFrame) -> int:
    print("[C] '높음'의 내용물 — 높다고 말한 근거가 무엇인가")
    hi = df[df["confidence"] == "높음"]
    if hi.empty:
        print("    '높음'인 종목이 없다\n")
        return 0
    print(f"    '높음' {len(hi)}종목 ({len(hi) / len(df):.0%})")
    print(f"    방법 수 중앙 {hi['n_methods'].median():.0f} · "
          f"n=2인 비율 {(hi['n_methods'] == 2).mean():.0%}")
    # ①⑤는 같은 회귀 배수를 쓴다 — 둘이 과반을 가져가면 '두 축의 합의'가 아니다.
    heavy = hi["shared_w"] > 0.5
    print(f"    ①⑤가 함께 선 종목 {(hi['shared_w'] > 0).mean():.0%} · "
          f"그 둘이 실효 가중의 과반을 가져간 종목 **{heavy.mean():.0%}**")
    hit = float(heavy.mean()) > SHARED_SHARE_LIMIT
    print(f"    → {'[문제]' if hit else '[확인]'} ①과 ⑤는 같은 회귀 적정 배수를 쓴다"
          "(ADR-0015). 둘이 가까운 것은 합의가 아니라 같은 입력이다.\n")
    return int(hit)


def _sec_cross(df: pd.DataFrame) -> int:
    print("[D] 신뢰도 × 절대가치 비중 — ADR-0018과 나란히 서면 모순인 조합")
    df = df.assign(band=pd.cut(df["intrinsic_share"], [-0.01, 0.0001, 0.15, 1.0],
                               labels=["0% (절대축 없음)", "0~15%", "15%+"]))
    tab = pd.crosstab(df["confidence"], df["band"], normalize="all")
    print("    " + tab.to_string().replace("\n", "\n    "))
    none_intr = df["intrinsic_share"] <= 0.0001
    print(f"\n    절대가치 축이 하나도 없는 종목 {none_intr.mean():.0%}")
    hi_none = df[(df["confidence"] != "낮음") & none_intr]
    share = len(hi_none) / len(df)
    hit = share > NO_INTRINSIC_LIMIT
    print(f"    신뢰도가 '낮음'이 아닌데 절대가치 축이 0%인 종목 **{share:.0%}** "
          f"{'[문제]' if hit else '[확인]'}")
    print("    → 화면은 '전부 시장 배수에 기댄다'고 말하는데 신뢰도는 그 사실을 안 본다.\n")
    return int(hit)


def _sec_weighted(df: pd.DataFrame) -> int:
    print("[E] 가중 대 비가중 산포 — 판정은 가중평균인데 산포는 동일가중이다")
    s = df.dropna(subset=["disp", "disp_w"])
    if s.empty:
        print("    표본 없음\n")
        return 0
    diff = (s["disp_w"] - s["disp"])
    regrade = s["disp"].map(_grade) != s["disp_w"].map(_grade)
    print(f"    disp 중앙 {s['disp'].median():.3f} → 가중 {s['disp_w'].median():.3f} "
          f"(차이 중앙 {diff.median():+.3f} · p90 {diff.abs().quantile(0.90):.3f})")
    print(f"    신뢰도 **등급**이 바뀌는 종목 **{regrade.mean():.0%}** ({regrade.sum()}곳)")
    hit = float(regrade.mean()) > REGRADE_LIMIT
    print(f"    → {'[문제] 의도인지 확인이 필요하다' if hit else '[확인] 차이가 작다'}\n")
    return int(hit)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100,
                    help="표본 종목 수 (시총 5분위에 고르게 배분)")
    ap.add_argument("--dump", type=str, default=None,
                    help="종목별 원자료를 CSV로 저장 (표본 순회가 비싸 다시 재기 어렵다)")
    args = ap.parse_args()

    L = get_kr_listing()
    pool = L[L["is_common"] & (L["Marcap"] > 0)].copy()
    pool["q"] = pd.qcut(pool["Marcap"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"])
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
    if args.dump:
        df.to_csv(args.dump, index=False, encoding="utf-8-sig")
        print(f"원자료 {len(df)}행을 {args.dump}에 저장했다.\n")
    print(f"표본 {len(samp)}종목 · 판정이 나온 종목 {len(df)}")
    print("신뢰도 구성 — " + " · ".join(
        f"{k} {v:.0%}" for k, v in df["confidence"].value_counts(normalize=True).items()))
    print("축 개수 구성 — " + " · ".join(
        f"n={int(k)} {v:.0%}" for k, v in
        df["n_methods"].value_counts(normalize=True).sort_index().items()) + "\n")

    bad = (_sec_corr(df) + _sec_disp_by_n(df) + _sec_high(df)
           + _sec_cross(df) + _sec_weighted(df))
    print("=" * 68)
    print(f"문제 {bad}건")
    print("이 스크립트는 **산식을 고르지 않는다.** 무엇이 얼마나 망가졌는지만 재고,")
    print("고치는 방법은 ADR에서 정한다(docs/HANDOFF-CONFIDENCE.md의 '안 정해진 것' 참조).")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
