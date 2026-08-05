"""신뢰도 산식이 무엇을 재고 있나 — 방법 사이의 겹침을 실측한다 (ADR-0022).

    python scripts/check_confidence.py KR --limit 200
    python scripts/check_confidence.py US --limit 200

지금 신뢰도는 `disp = std(중심값)/|mean(중심값)|` 하나로 낸다. **방법이 서로 독립이라는
전제**가 깔려 있는데 그 전제가 틀렸다 — ①과 ⑤는 같은 적정 배수를 쓰고(ADR-0015 · AAPL),
②도 배수 기반이며, 구조적으로도 괴리율의 55~60%를 `−log(현재배수)`가 설명한다(ADR-0014).
값이 가깝게 나오는 것이 '확실하다'가 아니라 **'같은 자로 여러 번 쟀다'**일 수 있다.

이 스크립트가 재는 것 넷:

  A. 방법 쌍별 상관 — 로그 괴리율 기준. `warranted.METHOD_RHO`에 넣을 값이다
  B. 실질 축 수 분포 — 상한 문턱(NEFF_LOW·NEFF_MID)이 실제로 무엇을 가르나
  C. 등급 이동표 — 상한을 씌우면 몇 %가 내려가나
  D. 신뢰도 × 절대가치 비중 — ADR-0018이 만든 모순 조합이 줄어드나

**상관은 종목들 사이에서만 잴 수 있다.** 종목 하나에는 방법마다 값이 하나씩뿐이라
종목별 상관이라는 것이 없다. 그래서 전 종목에서 재서 개별 종목에 적용한다 — 근사이고,
이 한계는 ADR-0022에 그대로 적는다.

**시장마다 따로 잰다. 한국 값을 미국에 옮겨 쓰지 않는다.** 오차표에서 이미 같은 것을
봤다 — PSR MAE가 한국 0.897 대 미국 0.656으로 크게 다르다(ADR-0022). 상관이라고 같으리라
볼 근거가 없고, ADR-0017이 *"한국 값을 대신 쓰는 것은 지어내는 것"*이라고 못박았다.

네트워크가 필요하다. CI가 아니라 check_dcf_viability.py와 같은 수동 계열이다.
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.capital_cost import compute_capital_cost              # noqa: E402
from src.analysis.indicators import compute_indicators                  # noqa: E402
from src.analysis.valuation import (CONFIDENCE_ORDER,                   # noqa: E402
                                    FUNDAMENTAL_METHODS, compute_valuation,
                                    confidence_grade)
from src.analysis.warranted import (NEFF_LOW, NEFF_MID,                 # noqa: E402
                                    effective_axes)
from src.data.kr_provider import KRProvider                             # noqa: E402
from src.data.universe import get_kr_listing, get_sp1500                # noqa: E402
from src.data.universe_multiples import coefficients_or_none            # noqa: E402
from src.data.us_provider import USProvider                             # noqa: E402

# 무위험수익률·시장위험프리미엄 — check_analysis.py와 **같은 가정**을 쓴다. 갈라지면
# 진단이 화면과 다른 자본비용으로 재게 되고, ③ RIM이 그 값에 직접 매달린다.
RF_MRP = {"KR": (0.035, 0.06), "US": (0.045, 0.05)}
MAX_WORKERS = 2                # yfinance 한도를 덜 때리려고 낮게 잡는다
RETRIES, BACKOFF = 3, 4.0      # 401 Invalid Crumb 재시도 (초)
MIN_PAIR_N = 30                # 이보다 적은 쌍은 상관을 내지 않는다
GRADES = CONFIDENCE_ORDER      # 진단과 실제 코드가 **같은 자**를 쓴다

# 미국 표본 틀의 규모 층. 지수 자체가 대·중·소형 구분이라 그대로 층이 된다
# (`get_sp1500`의 `Index` 열). 한국처럼 시총 5분위를 자르려면 1,500종목의 시총을
# 따로 받아야 하는데, 이미 나뉘어 온 것을 버리고 다시 살 이유가 없다.
US_TIERS = {"S&P 600": "Q1 소형", "S&P 400": "Q3 중형", "S&P 500": "Q5 대형"}


def _one(code: str, coef, market: str) -> dict | None:
    """한 종목의 판정과 방법별 로그 괴리율. 실패하면 None.

    **재시도가 있는 이유가 있다.** yfinance가 `401 Invalid Crumb`으로 자주 거절하는데
    (HANDOFF.md: 전 종목 수집에서 62%만 성공), 재시도가 없으면 이 진단이 표본을
    통째로 잃는다. 실제로 재시도 없이 돌렸더니 50종목 중 **0곳**이 나왔다.
    한도를 더 때리지 않게 동시 요청도 낮춰 둔다(`MAX_WORKERS`).
    """
    provider = KRProvider() if market == "KR" else USProvider()
    rf, mrp = RF_MRP[market]
    for attempt in range(RETRIES):
        try:
            d = provider.load(code, peer_count=9)
            ind = compute_indicators(d)
            cc = compute_capital_cost(d, rf=rf, mrp=mrp)
            v = compute_valuation(d, ind, r_equity=cc.k_e, warranted_coef=coef)
            break
        except Exception:
            if attempt == RETRIES - 1:
                return None
            time.sleep(BACKOFF * (attempt + 1))
    if v.verdict is None or not d.price or not v.fundamental_only:
        return None
    row = {"code": code, "conf": v.confidence, "disp": v.dispersion,
           "intrinsic": v.intrinsic_share, "mcap": d.market_cap,
           "methods": [m for m in (v.weights or {}) if m in FUNDAMENTAL_METHODS]}
    # 방법별 **로그 괴리율**. 로그로 재는 이유는 ADR-0014와 같다 — 배수는 곱셈 스케일이고,
    # 원 스케일에서 재면 비싼 종목의 큰 괴리가 상관을 끌고 간다.
    for e in v.estimates:
        if e.method in FUNDAMENTAL_METHODS and e.mid and e.mid > 0:
            row[e.method] = float(np.log(e.mid / d.price))
    return row


def _corr_table(df: pd.DataFrame) -> tuple[dict, list]:
    """방법 쌍별 피어슨 상관. 표본이 얇은 쌍은 내지 않는다(ADR-0011)."""
    table, rows = {}, []
    for a, b in itertools.combinations(sorted(FUNDAMENTAL_METHODS), 2):
        if a not in df.columns or b not in df.columns:
            rows.append((a, b, 0, None))
            continue
        both = df[[a, b]].dropna()
        if len(both) < MIN_PAIR_N:
            rows.append((a, b, len(both), None))
            continue
        r = float(both[a].corr(both[b]))
        table[(a, b)] = r
        rows.append((a, b, len(both), r))
    return table, rows


def _sample(market: str, limit: int) -> pd.DataFrame:
    """(code, 층) — **규모로 층화한** 표본. 상위 N만 쓰면 대형주만 잡혀 상관이 왜곡된다.

    층을 만드는 방법이 시장마다 다르다.

    - **한국**은 상장 목록에 시가총액이 있어 5분위로 자른다. `check_dcf_viability.py`와
      같은 표집이라 두 진단의 결과를 나란히 읽을 수 있다.
    - **미국**은 표본 틀(S&P 1500)에 시총 열이 없다. 대신 **지수 자체가 규모 층**이라
      (500 대형 · 400 중형 · 600 소형) 그것을 3층으로 쓴다. 5층이 아니라 3층인 것이
      이 방법의 대가이고, 층이 실제로 규모를 갈랐는지는 수집한 뒤 **실현 시총으로
      확인한다**(main의 표본 구성 표).
    """
    if market == "KR":
        L = get_kr_listing()
        pool = L[L["is_common"] & (L["Marcap"] > 0)].copy()
        pool["층"] = pd.qcut(pool["Marcap"], 5,
                            labels=["Q1 최소형", "Q2", "Q3", "Q4", "Q5 최대형"])
        pool["code"] = pool["Code"]
    else:
        pool = get_sp1500().copy()
        pool["층"] = pool["Index"].map(US_TIERS)
        pool["code"] = pool["Symbol"]
        pool = pool.dropna(subset=["층"])
    per = max(1, limit // pool["층"].nunique())
    return (pool.groupby("층", observed=True)
                .apply(lambda g: g.sample(min(per, len(g)), random_state=11),
                       include_groups=False)
                .reset_index()[["code", "층"]])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("market", nargs="?", default="KR", choices=["KR", "US"])
    ap.add_argument("--limit", type=int, default=150)
    args = ap.parse_args()
    market = args.market

    coef = coefficients_or_none(market)
    if coef is None:
        print("계수를 만들지 못했다 — 회귀 경로가 아니면 이 측정은 뜻이 없다.")
        return 1
    samp = _sample(market, args.limit)
    print(f"{market} {len(samp)}종목 수집 중…\n")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        rows = [r for r in ex.map(lambda c: _one(c, coef, market), samp["code"]) if r]
    if len(rows) < MIN_PAIR_N:
        print(f"판정이 난 종목이 {len(rows)}곳뿐이다 — 상관을 낼 표본이 아니다.")
        return 1
    df = pd.DataFrame(rows).merge(samp, on="code", how="left")
    # 표본이 몇 곳에서 왔는지 반드시 함께 찍는다 — 데이터 원천이 불안정해 성공률이
    # 실행마다 다르고, 그 사실을 모르면 숫자를 과신하게 된다.
    print(f"판정 {len(df)}종목 / 시도 {len(samp)}종목 (성공률 {len(df)/len(samp):.0%})")
    # 층이 실제로 규모를 갈랐나 — **주장하지 않고 실현 시총으로 보인다.** 미국은 층을
    # 시총이 아니라 지수로 만들었으므로 이 확인이 특히 필요하다.
    print(f"\n{'표본 층':<12}{'종목':>6}{'시총 중앙값(10억)':>20}")
    print("─" * 38)
    for tier, g in df.groupby("층", observed=True):
        print(f"{tier:<12}{len(g):>6}{g['mcap'].median() / 1e9:>20,.1f}")
    print()

    # ── A. 방법 쌍별 상관 ──────────────────────────────────────────
    print("A. 방법 쌍별 상관 (로그 괴리율)")
    print(f"{'방법 A':<14}{'방법 B':<14}{'표본':>7}{'상관':>9}")
    print("─" * 46)
    table, corr_rows = _corr_table(df)
    for a, b, n, r in corr_rows:
        print(f"{a:<14}{b:<14}{n:>7}{(f'{r:+.3f}' if r is not None else '표본부족'):>9}")

    # ── B. 실질 축 수 분포 ────────────────────────────────────────
    neff, capped = zip(*[effective_axes(m, market, table) for m in df["methods"]])
    df["n"] = [len(m) for m in df["methods"]]
    df["n_eff"], df["capped"] = neff, capped
    print(f"\nB. 실질 축 수 — 방법 수 중앙 {df['n'].median():.1f}개 대비")
    print(f"{'방법 수':>7}{'종목':>7}{'n_eff 중앙':>12}{'n_eff 최소':>12}{'n_eff 최대':>12}")
    print("─" * 50)
    for n, g in df.groupby("n"):
        print(f"{n:>7}{len(g):>7}{g['n_eff'].median():>12.2f}"
              f"{g['n_eff'].min():>12.2f}{g['n_eff'].max():>12.2f}")
    lo = float((df["n_eff"] < NEFF_LOW).mean())
    mid = float(((df["n_eff"] >= NEFF_LOW) & (df["n_eff"] < NEFF_MID)).mean())
    print(f"\n  n_eff < {NEFF_LOW} (상한 '낮음'): {lo:.1%}"
          f" · {NEFF_LOW}~{NEFF_MID} (상한 '중간'): {mid:.1%}"
          f" · {NEFF_MID} 이상: {1 - lo - mid:.1%}")
    if not all(capped):
        print(f"  ※ 상관을 모르는 쌍이 있어 상한을 안 건 종목 {sum(not c for c in capped)}곳")

    # ── C. 등급 이동표 ────────────────────────────────────────────
    # 등급 규칙을 여기서 다시 구현하지 않는다 — `confidence_grade`를 그대로 부른다.
    # 갈라지면 "진단은 통과인데 화면은 다른 말"이 된다.
    graded = [confidence_grade(d_, n, e, c)
              for d_, n, e, c in zip(df["disp"], df["n"], df["n_eff"], df["capped"])]
    df["new"] = [g[0] for g in graded]
    df["now"] = [g[1] for g in graded]        # 상한을 안 씌운 값 = 현행 산식
    print("\nC. 등급 이동 — 행이 현행, 열이 새 산식")
    print(f"{'현행':<8}" + "".join(f"{g:>8}" for g in GRADES) + f"{'합':>8}")
    print("─" * 42)
    for g in GRADES:
        sub = df[df["now"] == g]
        print(f"{g:<8}" + "".join(f"{int((sub['new'] == h).sum()):>8}" for h in GRADES)
              + f"{len(sub):>8}")
    moved = float((df["now"] != df["new"]).mean())
    print(f"\n  등급이 내려간 종목 {moved:.1%}"
          f" · '높음' {float((df['now'] == '높음').mean()):.1%} → {float((df['new'] == '높음').mean()):.1%}")

    # ── D. 신뢰도 × 절대가치 비중 ─────────────────────────────────
    # ADR-0018이 만든 어색함 — 신뢰도는 '중간'인데 화면은 "전부 시장 배수"라고 말한다.
    print("\nD. 절대가치 축이 없는 종목(intrinsic=0)의 신뢰도")
    zero = df[df["intrinsic"].fillna(0) <= 0]
    print(f"  대상 {len(zero)}종목 ({len(zero)/len(df):.0%})")
    for label, col in (("현행", "now"), ("새 산식", "new")):
        cnt = {g: int((zero[col] == g).sum()) for g in GRADES}
        bad = cnt["높음"] + cnt["중간"]
        print(f"  {label:<8}" + " · ".join(f"{g} {cnt[g]}" for g in reversed(GRADES))
              + f"   ← '전부 시장 배수'인데 낮음이 아닌 것 {bad}곳")

    # ── E. 붙여넣을 줄 ────────────────────────────────────────────
    if table:
        print("\nE. `warranted.METHOD_RHO`에 넣을 값")
        print(f'    "{market}": {{')
        for (a, b), r in table.items():
            print(f'        ("{a}", "{b}"): {r:.3f},')
        print("    },")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
