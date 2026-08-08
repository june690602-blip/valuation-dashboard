"""F-Score 커버리지 진단 — **가설을 재기 전에 돌린다** (B1 개정 4).

    python scripts/check_fscore.py                 # 한국 200종목
    python scripts/check_fscore.py --limit 60
    python scripts/check_fscore.py --market US --limit 60

네트워크가 필요하다(전 종목 재무). CI가 아니라 `check_size_bias.py`·`check_normalized.py`와
같은 **수동 계열**이다.

사전등록이 *"가설을 재기 전에 신호별 커버리지를 먼저 낸다"*고 적은 이유는, "5번이 안 선다"
만으로는 고칠 방법을 모르기 때문이다 — `total_debt`가 없는 것과 총자산이 없는 것은
다른 문제다. 그래서 **못 세운 신호마다 원인 컬럼을 이름으로 낸다**(`missing_inputs`).

**이 스크립트는 수익률을 보지 않는다.** 커버리지는 가설의 결과가 아니라 재기 위한
전제라서 지금 봐도 사전등록을 어기지 않는다. F1·F2·F3는 백테스트 패널에서 잰다.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.analysis.fscore import (FSCORE_MIN_SIGNALS, SIGNAL_NAMES,  # noqa: E402
                                 fscore, missing_inputs)
from src.data.models import FIN_COLUMNS                             # noqa: E402

WORKERS = 8


def _kr_financials(code: str) -> pd.DataFrame | None:
    """생산 경로와 같은 병합 재무 — DART(우선) + yfinance(보완).

    `total_debt`가 **yfinance에서만** 온다는 것이 이 진단의 핵심 관찰점이다
    (`kr_provider.merge_financials` 도크스트링). 백테스트 패널은 DART만 쓰므로
    화면과 백테스트에서 F-Score의 분모가 달라질 수 있다.
    """
    import yfinance as yf

    from src.data.base import extract_financials
    from src.data.kr_provider import merge_financials
    from src.data.opendart import get_dart_financials
    from src.data.universe import yahoo_ticker_kr

    dart, _, _ = get_dart_financials(code)
    if dart is None or dart.empty:
        return None
    try:
        yf_fin, _ = extract_financials(yf.Ticker(yahoo_ticker_kr(code, "KOSPI")))
    except Exception:  # noqa: BLE001 — 한 원천이 죽어도 다른 쪽으로 진단한다
        yf_fin = pd.DataFrame(columns=FIN_COLUMNS)
    return merge_financials(dart, yf_fin)


def _us_financials(ticker: str) -> pd.DataFrame | None:
    import yfinance as yf

    from src.data.base import extract_financials
    fin, _ = extract_financials(yf.Ticker(ticker))
    return fin if fin is not None and not fin.empty else None


def one(market: str, code: str) -> dict | None:
    try:
        fin = _kr_financials(code) if market == "KR" else _us_financials(code)
    except Exception:  # noqa: BLE001
        return None
    if fin is None or fin.empty:
        return None
    got = fscore(fin)
    row = {"code": code, "years": int(len(fin)), "stood": got is not None,
           "score": got["score"] if got else None,
           "max_score": got["max_score"] if got else None,
           "score_ex_equity": got["score_ex_equity"] if got else None,
           "gaps": missing_inputs(fin)}
    row["signals"] = got["signals"] if got else {n: None for n in SIGNAL_NAMES}
    return row


def universe(market: str, limit: int) -> list[str]:
    if market == "KR":
        from src.data.universe import get_kr_listing
        listing = get_kr_listing()
        pool = listing[listing["is_common"] & (listing["Marcap"] > 0)]
        pool = pool.sort_values("Marcap", ascending=False)
        # 시총 전 구간을 고르게 — 상위만 보면 커버리지가 낙관적으로 나온다
        idx = np.linspace(0, len(pool) - 1, min(limit, len(pool))).astype(int)
        return list(pool.iloc[idx]["Code"])
    from src.data.universe import get_sp1500
    pool = get_sp1500()
    idx = np.linspace(0, len(pool) - 1, min(limit, len(pool))).astype(int)
    return list(pool.iloc[idx]["Symbol"])


def report(rows: list[dict], market: str) -> None:
    n = len(rows)
    stood = [r for r in rows if r["stood"]]
    print(f"\n=== F-Score 커버리지 · {market} · 표본 {n}종목 ===\n")
    print(f"점수가 선 종목: {len(stood)}/{n} = {len(stood) / n:.1%}"
          f"   (하한 {FSCORE_MIN_SIGNALS}개 신호)")

    print("\n[신호별 커버리지] — 이 신호가 선 종목의 비율")
    for name in SIGNAL_NAMES:
        ok = sum(1 for r in rows if r["signals"].get(name) is not None)
        ones = sum(1 for r in rows if r["signals"].get(name) == 1)
        share = f"{ones / ok:.1%}" if ok else "—"
        print(f"  {name:<14} {ok / n:>6.1%}   1점 비율 {share:>7}")

    print("\n[못 세운 원인] — 없는 컬럼 이름별 종목 수")
    causes: Counter = Counter()
    for r in rows:
        for cols in r["gaps"].values():
            for c in cols:
                causes[c] += 1
    if not causes:
        print("  없음 — 모든 신호가 모든 종목에서 섰다")
    for col, cnt in causes.most_common():
        print(f"  {col:<22} {cnt:>4}종목")

    if stood:
        sc = pd.Series([r["score"] for r in stood])
        mx = pd.Series([r["max_score"] for r in stood])
        print(f"\n[점수 분포] 중앙 {sc.median():.0f} · 평균 {sc.mean():.2f}"
              f" · 분모 중앙 {mx.median():.0f}")
        print("  " + "  ".join(f"{k}점:{v}" for k, v in
                               sorted(Counter(sc).items())))
        ex = pd.Series([r["score_ex_equity"] for r in stood])
        print(f"  7번(신주발행) 뺀 8점 척도 — 중앙 {ex.median():.0f}"
              f" · 평균 {ex.mean():.2f}")

    short = [r for r in rows if not r["stood"]]
    if short:
        yrs = pd.Series([r["years"] for r in short])
        print(f"\n[안 선 {len(short)}종목의 회계연도 수] 중앙 {yrs.median():.0f}"
              f" · 최소 {yrs.min()} · 최대 {yrs.max()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KR", choices=("KR", "US"))
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    codes = universe(args.market, args.limit)
    print(f"{args.market} {len(codes)}종목 재무 수집…", flush=True)
    rows: list[dict] = []
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = [ex.submit(one, args.market, c) for c in codes]
        for i, fut in enumerate(as_completed(futs), 1):
            got = fut.result()
            if got:
                rows.append(got)
            if i % 25 == 0:
                print(f"  {i}/{len(codes)} (성공 {len(rows)})", flush=True)
    if not rows:
        print("재무를 하나도 못 받았다 — 네트워크나 API 키를 확인할 것")
        return
    report(rows, args.market)


if __name__ == "__main__":
    main()
