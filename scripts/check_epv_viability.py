"""EPV를 축으로 지을 값이 있나 — 관문 셋 (ADR-0016 결정 2, ADR-0023 예정).

    python scripts/check_epv_viability.py KR --limit 200
    python scripts/check_epv_viability.py US --limit 200

ADR-0016은 EPV를 **채택**한 것이 아니라 **후보로 지정**했다. 원문이 조건을 달았다 —
*"짓기 전에 검사 2를 EPV에 대해 돌린다 … 통과하지 못하면 짓지 않는다."* 같은 문서가
DCF를 그렇게 기각했다(검사 1 미달 29%). **기각도 정당한 결과다.**

관문 셋. 재기 전에 정한 값이고, 나온 숫자를 보고 바꾸지 않는다:

  검사 1  RIM이 빠진 종목 중 EPV가 서는 비율          ≥ 50%          ADR-0016 원문
  검사 2  기존 4방법과의 상관 최대 |피어슨| · 1.5배 밖  ≤ 0.5 · ≥ 25%  ADR-0016 원문
  검사 3  EPV 넣기 전/후 n_eff 중앙값                  안 내려갈 것    ADR-0023에서 신설

**검사 3은 ADR-0022가 만든 조건이다.** EPV는 ③ RIM과 같은 칸(이익 기반 영구가치)이고,
미국에서 그 칸의 두 방법(③ RIM ↔ ⑤ 정규화 이익)이 **+0.723**이다. 설계효과 식은 평균
상관만 보므로 EPV가 검사 2를 통과해도 `n_eff`를 내릴 수 있다 — ADR-0022 한계 절의
*"방법을 더 쓴 종목이 더 낮게 나올 수 있다"*.

**시장마다 따로 잰다.** ADR-0022에서 시장이 결론을 두 번 뒤집었다(①↔⑤ 한국 +0.263 대
미국 +0.763 · ③↔⑤ −0.015 대 +0.723). 한쪽 값을 다른 쪽에 옮겨 쓰지 않는다(ADR-0017).

네트워크가 필요하다. CI가 아니라 check_dcf_viability.py·check_confidence.py와 같은
수동 계열이다. 종료 코드 1은 "관문 미달"이라는 뜻이고, 그것도 결과다.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from check_confidence import sample_by_size                            # noqa: E402
from check_dcf_viability import gate_of                                # noqa: E402
from src.analysis.capital_cost import compute_capital_cost             # noqa: E402
from src.analysis.epv import (epv_per_share,                           # noqa: E402
                              normalized_operating_income)
from src.analysis.indicators import compute_indicators                 # noqa: E402
from src.analysis.valuation import (FUNDAMENTAL_METHODS,               # noqa: E402
                                    compute_valuation)
from src.data.kr_provider import KRProvider                            # noqa: E402
from src.data.universe_multiples import coefficients_or_none           # noqa: E402
from src.data.us_provider import USProvider                            # noqa: E402

# check_analysis.py·check_confidence.py와 **같은 가정**. 갈라지면 진단이 화면과 다른
# 자본비용으로 재게 되고, EPV는 WACC에 직접 매달린다.
RF_MRP = {"KR": (0.035, 0.06), "US": (0.045, 0.05)}
MAX_WORKERS = 2                # yfinance 한도를 덜 때린다
RETRIES, BACKOFF = 3, 4.0      # 401 Invalid Crumb 재시도 (초)
MIN_PAIR_N = 30                # 이보다 적은 쌍은 상관을 내지 않는다 (ADR-0011)

EPV = "EPV"                    # 상관표·n_eff에서 쓸 방법 이름
RIM = "수익가치(RIM)"

# 관문 — 앞의 둘은 ADR-0016이 정한 값을 그대로 옮긴 것이다.
COMPLEMENT_FLOOR = 0.50        # 검사 1
CORR_CEILING = 0.50            # 검사 2
DIVERGE_FLOOR = 0.25           # 검사 2
DIVERGE_FOLD = 1.5             # '갈렸다'고 볼 배수


def _one(code: str, coef, market: str) -> dict | None:
    """한 종목의 판정 + 방법별 로그 괴리율 + EPV. 실패하면 None.

    **재시도가 있는 이유가 있다.** yfinance가 `401 Invalid Crumb`으로 자주 거절하는데,
    재시도 없이 돌렸더니 50종목 중 **0곳**이 나온 적이 있다(check_confidence.py 주석).
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

    oi, oi_years = normalized_operating_income(d.financials)
    net_debt = (d.latest("total_debt") or 0.0) - (d.latest("cash") or 0.0)
    epv = epv_per_share(oi, cc.tax_rate, cc.wacc, net_debt, d.shares_outstanding)

    mids = {e.method: e.mid for e in v.estimates if e.method in FUNDAMENTAL_METHODS}
    has_rim = RIM in mids
    row = {
        "code": code, "mcap": d.market_cap, "fair_mid": v.fair_mid,
        "has_rim": has_rim,
        "gate": "" if has_rim else gate_of(
            next((r for m, r in v.skipped if m == RIM), "")),
        # 성립 조건을 갈라서 센다 — 어디서 막혔는지 모르면 고칠 자리를 못 찾는다
        "oi_pos": bool(oi is not None and oi > 0),
        "wacc_ok": cc.wacc is not None,
        "epv": epv,
        "epv_ready": epv is not None,
        "methods": [m for m in (v.weights or {}) if m in FUNDAMENTAL_METHODS],
        "oi_years": oi_years,
    }
    # **로그 괴리율.** 로그인 이유는 ADR-0014·0022와 같다 — 배수는 곱셈 스케일이라
    # 원 스케일에서 재면 비싼 종목의 큰 괴리가 상관을 끌고 간다.
    for m, mid in mids.items():
        if mid and mid > 0:
            row[m] = float(np.log(mid / d.price))
    if epv and epv > 0:
        row[EPV] = float(np.log(epv / d.price))
    return row


def _collect(market: str, limit: int, coef) -> tuple[pd.DataFrame, int]:
    """(수집 결과, 시도 종목 수). 표본 틀은 check_confidence.py와 **같은 것**을 쓴다."""
    samp = sample_by_size(market, limit)
    print(f"{market} {len(samp)}종목 수집 중…\n")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        rows = [r for r in ex.map(lambda c: _one(c, coef, market), samp["code"]) if r]
    if not rows:
        return pd.DataFrame(), len(samp)
    return pd.DataFrame(rows).merge(samp, on="code", how="left"), len(samp)


def _print_sample(df: pd.DataFrame, tried: int) -> None:
    """표본이 어디서 왔나. 성공률을 반드시 함께 찍는다 — 원천이 불안정해 실행마다 다르고,
    그 사실을 모르면 숫자를 과신하게 된다."""
    print(f"판정 {len(df)}종목 / 시도 {tried}종목 (성공률 {len(df)/tried:.0%})")
    # 층이 실제로 규모를 갈랐나 — **주장하지 않고 실현 시총으로 보인다.** 미국은 층을
    # 시총이 아니라 지수로 만들었으므로 이 확인이 특히 필요하다.
    print(f"\n{'표본 층':<12}{'종목':>6}{'시총 중앙값(10억)':>20}")
    print("─" * 38)
    for tier, g in df.groupby("층", observed=True):
        print(f"{tier:<12}{len(g):>6}{g['mcap'].median() / 1e9:>20,.1f}")


def _check1(df: pd.DataFrame) -> tuple[bool, float]:
    """검사 1 — RIM이 빠진 자리에서 EPV가 서는가. (합불, 비율)"""
    no_rim = df[~df["has_rim"]]
    has = df[df["has_rim"]]
    print("\n[검사 1] 커버리지 상보성 — EPV는 RIM이 빠진 그 자리에서 서는가")
    print(f"    RIM이 선 종목 {int(df['has_rim'].sum())} · "
          f"빠진 종목 {len(no_rim)} ({1 - df['has_rim'].mean():.0%})")
    if len(no_rim):
        print("\n    ③ RIM 제외 사유:")
        for label, n in no_rim["gate"].value_counts().items():
            print(f"      {n:>3}곳 ({n / len(df):>4.0%})  {label}")
    print(f"\n    {'항목':<26}{'전체':>10}{'RIM 빠짐':>12}{'RIM 있음':>12}")
    print("    " + "─" * 60)
    for label, col in [("정상 영업이익 > 0", "oi_pos"), ("WACC 계산됨", "wacc_ok"),
                       ("EPV 설 수 있음", "epv_ready")]:
        b = no_rim[col].mean() if len(no_rim) else np.nan
        c = has[col].mean() if len(has) else np.nan
        print(f"    {label:<26}{df[col].mean():>10.0%}{b:>12.0%}{c:>12.0%}")
    comp = float(no_rim["epv_ready"].mean()) if len(no_rim) else float("nan")
    ok = bool(pd.notna(comp) and comp >= COMPLEMENT_FLOOR)
    print(f"\n    ★ RIM이 빠진 {len(no_rim)}종목 중 EPV가 서는 비율 {comp:.0%}  "
          f"{'[확인]' if ok else '[문제]'} (기준선 {COMPLEMENT_FLOOR:.0%})")
    print("      ADR-0016은 이 검사로 DCF를 기각했다(29%). 같은 문서 표로 EPV를 계산하면\n"
          "      31/75 = 41%인데, 그 문서는 이 각도로 EPV를 본 적이 없다.\n"
          "      **여기 나온 값이 그 41%보다 우선한다** — 순부채 조건이 더 붙었다.")
    return ok, comp


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
        print(f"판정이 난 종목이 {len(df)}곳뿐이다 — 관문을 걸 표본이 아니다.")
        return 1
    _print_sample(df, tried)
    ok1, _ = _check1(df)
    return 0 if ok1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
