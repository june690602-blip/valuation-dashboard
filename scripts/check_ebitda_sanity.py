"""EBITDA가 영업이익보다 작은 종목을 전수로 센다 (#116).

    python scripts/check_ebitda_sanity.py            # 기본 표본
    python scripts/check_ebitda_sanity.py US INTC    # 한 종목만

## 무엇을 재나

EBITDA는 영업이익에 감가상각비(D&A)를 **다시 더한** 값이라, 정의상 영업이익보다 작을 수
없다(D&A는 음수가 아니다). 그런데 무료 데이터가 그런 값을 준다.

    INTC 2026Q2 실측 — 영업이익 +1,966M · EBITDA -7,274M

TTM은 분기 4개를 더하므로 한 분기의 오염이 그대로 들어온다. 인텔은 TTM EBITDA가
3,676M인데 TTM 영업이익이 4,308M이었고, 그 결과 EV/EBITDA가 **144배**로 나왔다
(공시값 29.42배 · 업종 중앙 29.52배). 화면이 업종 평균 수준인 회사를 '극도로 비쌈'으로
보여줄 수 있었다.

이 스크립트는 **인텔 하나인지 구조적인지**를 가른다. 네트워크가 필요해 수동 계열이다.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import yfinance as yf

from src.data.base import _ITEM_CANDIDATES, _pick

# 대형·사이클·설비집약을 섞는다. D&A가 큰 업종일수록 이 결함의 영향이 크다.
SAMPLE_US = ["INTC", "AAPL", "MSFT", "AMD", "MU", "TSLA", "F", "T", "BA", "XOM"]
SAMPLE_KR = ["005930.KS", "000660.KS", "005380.KS", "051910.KS", "035420.KS"]


def quarters(tk, col):
    s = _pick(tk.quarterly_income_stmt, _ITEM_CANDIDATES.get(col, []))
    return None if s is None else s.sort_index().dropna()


def one(sym: str) -> dict | None:
    tk = yf.Ticker(sym)
    eb, oi = quarters(tk, "ebitda"), quarters(tk, "operating_income")
    if eb is None or oi is None or len(eb) < 4 or len(oi) < 4:
        return {"sym": sym, "skip": "분기 EBITDA·영업이익 4개를 못 채움"}
    ttm_eb, ttm_oi = float(eb.iloc[-4:].sum()), float(oi.iloc[-4:].sum())
    # 분기 단위로도 본다 — TTM이 멀쩡해도 특정 분기만 오염된 경우가 있다.
    common = eb.index.intersection(oi.index)
    bad_q = [(str(i.date()), float(eb[i]), float(oi[i])) for i in common if eb[i] < oi[i]]
    return {"sym": sym, "ttm_eb": ttm_eb, "ttm_oi": ttm_oi,
            "ttm_bad": ttm_eb < ttm_oi, "bad_q": bad_q, "n_q": len(common)}


def main() -> int:
    args = sys.argv[1:]
    syms = [args[1]] if len(args) >= 2 else SAMPLE_US + SAMPLE_KR

    print(f"{'종목':<12}{'TTM EBITDA':>18}{'TTM 영업이익':>18}{'TTM 역전':>10}{'오염 분기':>12}")
    print("─" * 76)
    rows, skipped = [], []
    for s in syms:
        try:
            r = one(s)
        except Exception as e:
            skipped.append(f"{s}: {type(e).__name__}: {e}")
            continue
        if r.get("skip"):
            skipped.append(f"{r['sym']}: {r['skip']}")
            continue
        rows.append(r)
        qmark = f"{len(r['bad_q'])}/{r['n_q']}"
        print(f"{r['sym']:<12}{r['ttm_eb']:>18,.0f}{r['ttm_oi']:>18,.0f}"
              f"{('예' if r['ttm_bad'] else '—'):>10}{qmark:>12}")

    print("─" * 76)
    ttm_bad = [r for r in rows if r["ttm_bad"]]
    q_bad = [r for r in rows if r["bad_q"]]
    print(f"  잰 종목 {len(rows)}개")
    print(f"  TTM EBITDA < TTM 영업이익 : {len(ttm_bad)}개  "
          f"{' · '.join(r['sym'] for r in ttm_bad) if ttm_bad else ''}")
    print(f"  분기 하나라도 역전        : {len(q_bad)}개  "
          f"{' · '.join(r['sym'] for r in q_bad) if q_bad else ''}")
    for r in q_bad:
        for d, e, o in r["bad_q"]:
            print(f"      {r['sym']:<8}{d}  EBITDA {e:>16,.0f}  영업이익 {o:>16,.0f}")
    if skipped:
        print("\n  건너뜀:")
        for s in skipped:
            print(f"    {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
