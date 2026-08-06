"""미국 시점 데이터 수집 — SEC EDGAR XBRL. 한국 수집기와 **같은 스키마**로 낸다.

    python scripts/backtest_collect_us.py --limit 1500

사전등록: `docs/review/B1-백테스트-사전등록.md` **개정 1**.
가설 H1~H4는 이 데이터를 받기 전에 확정됐다.

왜 EDGAR인가
-----------
yfinance의 미국 연간 재무는 **4행뿐**이라(ADR-0025) 창을 늘려도 같은 4년을 쓴다.
EDGAR `companyfacts`는 **2007년부터 20년**을 주고(실측: AAPL 2006~2025),
각 수치에 **`filed` 날짜가 붙어 있다** — 시점 데이터가 저절로 풀린다.
키가 필요 없고 무료이며, `User-Agent`에 연락처만 넣으면 된다(초당 10회 제한).

한국과 **일부러 다르게** 한 것 하나
----------------------------------
같은 회계연도가 여러 번 공시된다(원공시 + 이후 보고서의 비교표시). 한국 수집기는
**최신 보고서를 우선**했지만(재작성 반영), 여기서는 **가장 먼저 공시된 값**을 쓴다 —
`filed`가 가장 이른 것. 재작성된 값은 그 시점에 알 수 없었으므로 시점 데이터로는
원공시가 맞다. **미국 쪽이 이 점에서 더 엄격하다**는 것을 ADR에 적는다.

한국과 **같게** 한 것 하나 — 시가총액
------------------------------------
사전등록 개정 1은 *"EDGAR가 시점별 주식수를 주니 한국의 근사가 여기선 없다"*고 적었다.
**받아 보니 그 기대가 틀렸다.** EDGAR의 주식수도 **공시 당시 기준**이라(액면분할 전)
분할 수정된 주가와 곱하면 어긋난다 — 한국 DART EPS와 정확히 같은 문제다.
그래서 **양쪽 시장에 같은 근사**를 쓴다: `시총_t = 현재주식수 × 수정주가_t`.
ADR-0017이 요구하는 '시장마다 따로 재되 같은 자로 재는 것'에도 이쪽이 맞다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.data import ca_bundle                                        # noqa: E402
from src.data.universe import get_sp1500                              # noqa: E402

ca_bundle.install()

OUT = ROOT / "data" / "backtest_us"
RAW = OUT / "raw"
UA = {"User-Agent": "valuation-dashboard research june690602@gmail.com"}
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{:010d}.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
FLOOR_YEAR = 2007
WORKERS = 5               # SEC 초당 10회 제한 — 여유를 둔다
RETRIES, BACKOFF = 3, 2.0

# 표준 컬럼 → us-gaap 개념 후보(앞에서부터 찾는다). 회사마다 쓰는 태그가 달라
# 폴백이 필요하다 — 하나만 걸면 매출이 통째로 비는 회사가 나온다.
CONCEPTS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
                      "ExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
                      "MinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax_expense": ["IncomeTaxExpenseBenefit"],
    "interest_expense": ["InterestExpense", "InterestIncomeExpenseNet",
                         "InterestExpenseDebt"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "gross_profit": ["GrossProfit"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "total_equity": ["StockholdersEquity",
                     "StockholdersEquityIncludingPortionAttributableTo"
                     "NoncontrollingInterest"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill",
                    "FiniteLivedIntangibleAssetsNet"],
    "buyback": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
}
# 총차입금 — 더해서 만든다(한국 수집기의 DEBT_PARTS와 같은 취급)
DEBT_PARTS = [["LongTermDebtNoncurrent", "LongTermDebt"],
              ["LongTermDebtCurrent"],
              ["ShortTermBorrowings", "OtherShortTermBorrowings"]]


def _get(url: str) -> dict | None:
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(BACKOFF * (attempt + 1))
    return None


def _annual(us: dict, names: list[str], unit: str = "USD") -> dict[int, tuple]:
    """{회계연도: (값, 최초공시일)}. **가장 먼저 공시된 값**을 쓴다 — 재작성은 그때 몰랐다."""
    out: dict[int, tuple] = {}
    for name in names:
        units = (us.get(name) or {}).get("units", {})
        for x in units.get(unit, []):
            if x.get("form") not in ("10-K", "10-K/A"):
                continue
            end, start, filed = x.get("end"), x.get("start"), x.get("filed")
            if not end or not filed:
                continue
            if start:                       # 기간 항목 — 연간만 남긴다
                try:
                    days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
                except ValueError:
                    continue
                if not 350 <= days <= 380:
                    continue
            # 회계연도 라벨: 6월 이전 결산은 전년도로 본다(한국의 12월 결산과 맞추려는 것이 아니라
            # 같은 회사 안에서 연도가 어긋나지 않게 하려는 것이다)
            yr = int(end[:4]) if end[5:7] >= "06" else int(end[:4]) - 1
            if yr < FLOOR_YEAR:
                continue
            prev = out.get(yr)
            if prev is None or filed < prev[1]:
                out[yr] = (float(x["val"]), filed)
        if out:                             # 첫 번째로 값이 나온 개념만 쓴다
            break
    return out


def _frame(us: dict) -> pd.DataFrame:
    cols: dict[str, dict] = {}
    filed: dict[int, str] = {}
    for col, names in CONCEPTS.items():
        a = _annual(us, names, "shares" if col == "shares" else "USD")
        cols[col] = {y: v for y, (v, _f) in a.items()}
        for y, (_v, f) in a.items():
            if y not in filed or f < filed[y]:
                filed[y] = f

    debt: dict[int, float] = {}
    for part in DEBT_PARTS:
        for y, (v, _f) in _annual(us, part).items():
            debt[y] = debt.get(y, 0.0) + v
    cols["total_debt"] = debt

    df = pd.DataFrame(cols)
    if df.empty:
        return df
    df = df.sort_index()
    df["filed"] = pd.to_datetime(pd.Series(filed).reindex(df.index), errors="coerce")
    # 결산월이 회사마다 달라 회계연도말을 값에서 되살릴 수 없다 — 공시일에서 근사한다.
    # `backtest_panel`은 `filed`를 쓰므로 이 열은 폴백 경로에서만 쓰인다.
    df["fiscal_end"] = df["filed"] - pd.Timedelta(days=75)
    keep = pd.Series(False, index=df.index)
    for c in ("revenue", "total_assets", "net_income"):
        if c in df.columns:
            keep = keep | df[c].notna()
    return df[keep] if keep.any() else df


def _shares(facts: dict) -> float | None:
    """현재 발행주식수 — 표지(dei)의 가장 최근 값."""
    u = ((facts.get("facts", {}).get("dei", {})
          .get("EntityCommonStockSharesOutstanding") or {}).get("units", {}))
    best = None
    for arr in u.values():
        for x in arr:
            if not x.get("val") or not x.get("end"):
                continue
            if best is None or x["end"] > best[0]:
                best = (x["end"], float(x["val"]))
    return best[1] if best else None


def _prices(sym: str) -> pd.Series:
    import yfinance as yf
    h = yf.Ticker(sym).history(period="max", auto_adjust=False)
    if not len(h):
        return pd.Series(dtype=float)
    s = h["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s


def _one(rec: dict) -> dict:
    sym, cik = rec["code"], rec["cik"]
    fin_p, px_p = RAW / f"fin_{sym}.parquet", RAW / f"px_{sym}.parquet"
    meta_p = RAW / f"meta_{sym}.json"
    if fin_p.exists() and px_p.exists() and meta_p.exists():
        return {"code": sym, "status": "cached"}

    facts = _get(FACTS.format(int(cik)))
    if not facts:
        return {"code": sym, "status": "no_facts"}
    us = facts.get("facts", {}).get("us-gaap")
    if not us:
        return {"code": sym, "status": "no_usgaap"}
    fin = _frame(us)
    if fin.empty or len(fin) < 3:
        return {"code": sym, "status": "too_short"}
    shares = _shares(facts)
    if not shares or not np.isfinite(shares) or shares <= 0:
        return {"code": sym, "status": "no_shares"}

    for attempt in range(RETRIES):
        try:
            px = _prices(sym)
            break
        except Exception as e:
            if attempt == RETRIES - 1:
                return {"code": sym, "status": f"px_fail:{type(e).__name__}"}
            time.sleep(BACKOFF * (attempt + 1))
    if not len(px):
        return {"code": sym, "status": "px_empty"}

    fin.index.name = "fiscal_year"
    fin.to_parquet(fin_p)
    px.rename("close").to_frame().to_parquet(px_p)
    meta_p.write_text(json.dumps({
        "code": sym, "name": rec["name"], "sector": rec["sector"], "market": "US",
        "listed": True, "delisted_on": None, "shares": float(shares),
        "years": int(len(fin)), "filed_known": int(fin["filed"].notna().sum()),
        "px_from": str(px.index[0].date()), "px_to": str(px.index[-1].date()),
    }, ensure_ascii=False), encoding="utf-8")
    return {"code": sym, "status": "ok"}


def universe(limit: int) -> list[dict]:
    """S&P 1500 + SEC 티커→CIK 매핑. 폐지 종목은 못 넣는다(사전등록 개정 1 한계 1)."""
    tk = _get(TICKERS) or {}
    cik = {v["ticker"].upper(): v["cik_str"] for v in tk.values()}
    sp = get_sp1500()
    recs = []
    for r in sp.itertuples():
        s = str(r.Symbol).upper().replace(".", "-")
        if s not in cik:
            continue
        recs.append({"code": s, "cik": cik[s], "name": getattr(r, "Name", s),
                     "sector": getattr(r, "Sector", None)})
    return recs[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1500)
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)

    recs = universe(args.limit)
    print(f"유니버스 {len(recs)}종목 (S&P 1500 ∩ SEC CIK)\n")

    t0, done, stats = time.time(), 0, {}
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = [ex.submit(_one, r) for r in recs]
        for f in as_completed(futs):
            r = f.result()
            k = r["status"].split(":")[0]
            stats[k] = stats.get(k, 0) + 1
            done += 1
            if done % 50 == 0 or done == len(recs):
                el = time.time() - t0
                print(f"  {done}/{len(recs)}  {el:.0f}초  "
                      f"(남은 예상 {el / done * (len(recs) - done):.0f}초)  {stats}")
    print(f"\n수집 결과: {stats}\n저장 위치: {RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
