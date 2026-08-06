"""시점 데이터 수집 — 백테스트의 재료를 모아 parquet 셋으로 굳힌다.

    python scripts/backtest_collect.py --limit 1000        # 상장 표본 크기
    python scripts/backtest_collect.py --resume            # 중단된 수집 이어받기

사전등록: `docs/review/B1-백테스트-사전등록.md` §1. **거기 적힌 것만 받는다.**

무엇을 받나
-----------
- **재무** OpenDART 사업보고서, 회계연도 2013~2025. 보고서 하나에 3년이 들어 있어
  4년 간격 대신 [최신, −3, −6, −9, 2015]를 받으면 빈 해 없이 13년을 덮는다.
- **공시일** `list.json`(`pblntf_detail_ty=A001`)의 `rcept_dt`. 지금 판정 경로가 쓰는
  `fiscal_end + 90일` **근사를 대체한다** — 실측에서 그 근사는 옛 연도에 1~2일
  미리 알고 있었다(FY2017: 근사 2018-03-31 vs 실제 2018-04-02).
- **주가** 상장은 yfinance `period='max'`, 폐지는 FinanceDataReader.
  **둘 다 `Close`(분할 수정 · 배당 미수정)로 통일한다** — 기준이 섞이면 배수가
  배당 누적분만큼 틀어진다(`models.actual_prices`가 적어 둔 그 이유).
- **주식수** 상장은 KRX 상장목록, 폐지는 DART `stockTotqySttus.json`.

`total_debt`를 여기서 따로 뽑는 이유
-----------------------------------
`opendart.DART_MAP`에 차입금 항목이 **없다**(grep 0). 그래서 옛 연도에서 EPV의 순부채가
못 선다(인계문 함정 5). 원문에는 있으므로 **같은 응답에서 한 번 더 훑어** 뽑는다 —
`opendart.py`를 고치면 판정 경로가 바뀌어 별도 ADR이 필요한 변경이 되기 때문이다.
연구 스크립트가 제 재료를 스스로 마련하고 제품은 그대로 둔다.

감가상각비는 본문에 없어(주석 항목) `ebitda`는 만들지 않는다. 그래서 옛 연도의 ①은
EV/EBITDA 다리 없이 선다 — 사전등록 §6 한계 6이 그것이다.

네트워크가 필요하다. CI가 아니라 `check_epv_viability.py`와 같은 수동 계열이다.
종목별로 캐시하므로 중간에 끊겨도 `--resume`으로 이어받는다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import src.data.opendart as od                                        # noqa: E402
from src.data import ca_bundle                                        # noqa: E402
from src.data.universe import get_kr_listing, yahoo_ticker_kr         # noqa: E402

ca_bundle.install()   # 한글 경로에서 libcurl이 인증서를 못 연다 (ADR-0027)

OUT = ROOT / "data" / "backtest"
RAW = OUT / "raw"

# 회계연도 하한. 2015년 사업보고서의 '전전기'가 DART 전체 재무제표 API의 바닥이다
# (실측: bsns_year=2014·2013은 응답 없음).
FLOOR_YEAR = 2013
DART_WORKERS = 6          # 실측 24종목 14초 — 더 올리면 DART가 간헐적으로 거절한다
PRICE_WORKERS = 4
RETRIES, BACKOFF = 3, 3.0

# 차입금 — 원문에는 있는데 표준 매핑에 없는 것들. 넷을 **더해서** 총차입금을 만든다.
DEBT_PARTS = (
    (("BS",), ["ifrs-full_ShorttermBorrowings", "dart_ShortTermBorrowings"],
     ["단기차입금"]),
    (("BS",), ["ifrs-full_LongtermBorrowings", "dart_LongTermBorrowings"],
     ["장기차입금"]),
    (("BS",), ["ifrs-full_BondsIssued", "dart_BondsIssued"], ["사채"]),
    (("BS",), ["dart_CurrentPortionOfLongTermliabilities"], ["유동성장기부채"]),
)


def _report_years(base: int) -> list[int]:
    """받을 사업연도 — 3년 간격 + 바닥(2015)을 반드시 포함해 2013까지 덮는다."""
    ys = [base - 3 * i for i in range(4)]          # 예: 2025 2022 2019 2016
    if FLOOR_YEAR + 2 not in ys:
        ys.append(FLOOR_YEAR + 2)                  # 2015 — 전전기가 2013
    return [y for y in ys if y >= FLOOR_YEAR + 2]


def _total_debt(rows: list[dict], field: str) -> float | None:
    """한 기간의 총차입금. 하나도 못 찾으면 None(0으로 채우면 무차입으로 둔갑한다)."""
    total, found = 0.0, False
    for sj, ids, kws in DEBT_PARTS:
        row = od._find_row(rows, set(sj), ids, [k.replace(" ", "") for k in kws])
        if row is None:
            continue
        v = od._num(row.get(field))
        if v is not None and not pd.isna(v):
            total += float(v)
            found = True
    return total if found else None


def _financials(code: str, key: str, corp: str,
                last_year: int | None = None) -> pd.DataFrame:
    """회계연도 index의 재무 프레임 + `total_debt`. 최신 보고서 우선(재작성 반영).

    `last_year`는 **마지막으로 사업보고서를 냈을 법한 해**다. 폐지 종목에 반드시 넘겨야
    한다 — 2018년에 상장폐지된 회사는 2024·2025년 보고서가 없어서, 최신 연도만 찾으면
    *"사업보고서 없음"*으로 통째로 버려진다. 그러면 생존편향을 없애려고 폐지 종목을
    모은 것이 무의미해진다(실측: 이 인자가 없을 때 폐지 370곳 중 307곳이 버려졌다).
    """
    top = min(2025, last_year if last_year else 2025)
    base, reports = None, {}
    for y in range(top, FLOOR_YEAR + 1, -1):        # 최신 사업보고서 연도 찾기
        j = od._fetch_report(key, corp, y)
        if j:
            base, reports[y] = y, j
            break
    if base is None:
        raise ValueError("no annual report")
    fs = reports[base].get("_fs", "CFS")
    for y in _report_years(base)[1:]:
        j = od._fetch_report(key, corp, y, fs_div=fs)
        if j:
            reports[y] = j

    data: dict[int, dict] = {}
    for ry in sorted(reports, reverse=True):        # 최신 우선 — 재작성본이 이긴다
        j = reports[ry]
        parsed = od._parse_report(j, ry)
        rows = j.get("list", [])
        for yr, fld in ((ry, "thstrm_amount"), (ry - 1, "frmtrm_amount"),
                        (ry - 2, "bfefrmtrm_amount")):
            td = _total_debt(rows, fld)
            if td is not None:
                parsed.setdefault(yr, {})["total_debt"] = td
        for yr, vals in parsed.items():
            slot = data.setdefault(yr, {})
            for c, v in vals.items():
                if (c not in slot or pd.isna(slot.get(c))) and not pd.isna(v):
                    slot[c] = v

    df = pd.DataFrame.from_dict(data, orient="index").sort_index()
    df = df[df.index >= FLOOR_YEAR]
    keep = pd.Series(False, index=df.index)
    for c in ("revenue", "total_assets", "net_income"):
        if c in df.columns:
            keep = keep | df[c].notna()
    df = df[keep] if keep.any() else df
    fallback = pd.Series([pd.Timestamp(int(y), 12, 31) for y in df.index], index=df.index)
    df["fiscal_end"] = (pd.to_datetime(df.get("fiscal_end"), errors="coerce").fillna(fallback)
                        if "fiscal_end" in df.columns else fallback)
    df.attrs["fs_div"] = fs
    return df


def _filing_dates(key: str, corp: str) -> dict[int, str]:
    """{회계연도: 실제 접수일 YYYY-MM-DD}. 사업보고서 이름의 '(2024.12)'에서 연도를 읽는다."""
    import re

    import requests
    r = requests.get(f"{od.BASE}/list.json", params={
        "crtfc_key": key, "corp_code": corp, "bgn_de": f"{FLOOR_YEAR}0101",
        "end_de": pd.Timestamp.today().strftime("%Y%m%d"),
        "pblntf_detail_ty": "A001", "page_count": 100}, timeout=40)
    j = r.json()
    if j.get("status") != "000":
        return {}
    out = {}
    for it in j.get("list", []):
        m = re.search(r"\((\d{4})\.\d{2}\)", it.get("report_nm", ""))
        d = str(it.get("rcept_dt") or "")
        if m and len(d) == 8:
            out[int(m.group(1))] = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return out


def _issued_shares(key: str, corp: str) -> float | None:
    """폐지 종목용 보통주 발행총수. 최신 연도부터 내려가며 숫자가 나오는 첫 해를 쓴다."""
    import requests
    for y in range(2025, 2015, -1):
        try:
            r = requests.get(f"{od.BASE}/stockTotqySttus.json", params={
                "crtfc_key": key, "corp_code": corp, "bsns_year": str(y),
                "reprt_code": "11011"}, timeout=30)
            j = r.json()
        except Exception:
            continue
        if j.get("status") != "000":
            continue
        for it in j.get("list", []):
            if "보통주" not in str(it.get("se", "")):
                continue
            v = od._num(str(it.get("istc_totqy", "")).replace(",", ""))
            if v and v > 0:
                return float(v)
    return None


def _prices(code: str, market: str, listed: bool) -> pd.Series:
    """분할 수정 · 배당 미수정 종가. 상장=yfinance(26년), 폐지=FDR(정리매매일까지)."""
    if listed:
        import yfinance as yf
        h = yf.Ticker(yahoo_ticker_kr(code, market)).history(period="max", auto_adjust=False)
        if len(h):
            s = h["Close"].dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            return s
    import FinanceDataReader as fdr
    px = fdr.DataReader(code, f"{FLOOR_YEAR - 1}-01-01")
    return px["Close"].dropna() if len(px) else pd.Series(dtype=float)


def _one(rec: dict, key: str, cmap: pd.DataFrame) -> dict:
    """종목 하나를 끝까지. 종목별 캐시가 있으면 건너뛴다(--resume)."""
    code = rec["code"]
    fin_p, px_p = RAW / f"fin_{code}.parquet", RAW / f"px_{code}.parquet"
    meta_p = RAW / f"meta_{code}.json"
    if fin_p.exists() and px_p.exists() and meta_p.exists():
        return {"code": code, "status": "cached"}
    if code not in cmap.index:
        return {"code": code, "status": "no_corp_code"}
    corp = cmap.at[code, "corp_code"]
    if isinstance(corp, pd.Series):
        corp = corp.iloc[0]

    # 폐지 종목은 폐지 **직전** 회계연도가 마지막 사업보고서다. 폐지일이 3월 이전이면
    # 그 전해 보고서도 안 냈을 수 있어 한 해 더 물러선다.
    last_year = None
    if rec.get("delisted_on"):
        dd = pd.Timestamp(rec["delisted_on"])
        last_year = dd.year - 1 if dd.month >= 4 else dd.year - 2

    for attempt in range(RETRIES):
        try:
            fin = _financials(code, key, corp, last_year=last_year)
            break
        except ValueError as e:
            # '사업보고서가 없다'는 결정적 실패다 — 다시 물어도 같은 답이 온다.
            # 여기서 재시도하면 소형주 꼬리에서 종목당 18초를 태운다(실측).
            return {"code": code, "status": f"dart_none:{e}"}
        except Exception as e:
            if attempt == RETRIES - 1:
                return {"code": code, "status": f"dart_fail:{type(e).__name__}"}
            time.sleep(BACKOFF * (attempt + 1))
    if fin.empty:
        return {"code": code, "status": "dart_empty"}

    filed = _filing_dates(key, corp)
    shares = rec.get("shares")
    if not shares or not np.isfinite(shares) or shares <= 0:
        shares = _issued_shares(key, corp)
    if not shares:
        return {"code": code, "status": "no_shares"}

    for attempt in range(RETRIES):
        try:
            px = _prices(code, rec["market"], rec["listed"])
            break
        except Exception as e:
            if attempt == RETRIES - 1:
                return {"code": code, "status": f"px_fail:{type(e).__name__}"}
            time.sleep(BACKOFF * (attempt + 1))
    if not len(px):
        return {"code": code, "status": "px_empty"}

    fin = fin.copy()
    fin["filed"] = pd.to_datetime(pd.Series(
        {y: filed.get(int(y)) for y in fin.index}), errors="coerce")
    fin.index.name = "fiscal_year"
    fin.to_parquet(fin_p)
    px.rename("close").to_frame().to_parquet(px_p)
    meta_p.write_text(json.dumps({
        "code": code, "name": rec["name"], "sector": rec["sector"],
        "market": rec["market"], "listed": rec["listed"],
        "delisted_on": rec.get("delisted_on"), "shares": float(shares),
        "fs_div": fin.attrs.get("fs_div", ""), "years": int(len(fin)),
        "filed_known": int(fin["filed"].notna().sum()),
        "px_from": str(px.index[0].date()), "px_to": str(px.index[-1].date()),
    }, ensure_ascii=False), encoding="utf-8")
    return {"code": code, "status": "ok"}


def universe(limit: int) -> list[dict]:
    """시총 층화 상장 표본 + 2017년 이후 폐지 보통주 전량 (사전등록 §1)."""
    import FinanceDataReader as fdr

    L = get_kr_listing()
    pool = L[L["is_common"] & L["Sector"].notna() & (L["Marcap"] > 0)]
    pool = pool.sort_values("Marcap", ascending=False).reset_index(drop=True)
    idx = np.unique(np.linspace(0, len(pool) - 1, min(limit, len(pool))).astype(int))
    recs = [{"code": r.Code, "name": r.Name, "sector": r.Sector, "market": r.Market,
             "listed": True, "shares": float(r.Stocks) if r.Stocks else None,
             "delisted_on": None}
            for r in pool.iloc[idx].itertuples()]

    d = fdr.StockListing("KRX-DELISTING")
    d["DelistingDate"] = pd.to_datetime(d["DelistingDate"], errors="coerce")
    d = d[(d["DelistingDate"] >= "2017-01-01") & (d["SecuGroup"] == "주권")]
    have = {r["code"] for r in recs}
    for r in d.itertuples():
        if r.Symbol in have:
            continue
        recs.append({"code": r.Symbol, "name": r.Name,
                     "sector": getattr(r, "Industry", None), "market": r.Market,
                     "listed": False, "shares": None,
                     "delisted_on": str(r.DelistingDate.date())})
    return recs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000, help="상장 표본 크기")
    ap.add_argument("--resume", action="store_true", help="(기본 동작 — 항상 이어받는다)")
    args = ap.parse_args()

    key = od.get_api_key()
    if not key:
        print("OPENDART_API_KEY가 없다 — 이 수집은 키 없이는 불가능하다.")
        return 1
    RAW.mkdir(parents=True, exist_ok=True)

    recs = universe(args.limit)
    n_listed = sum(r["listed"] for r in recs)
    print(f"유니버스 {len(recs)}종목 — 상장 {n_listed} · 폐지 {len(recs) - n_listed}")
    print(f"DART 예상 호출 약 {len(recs) * 6:,}회 (일일 한도 20,000)\n")

    cmap = od.get_corp_code_map()
    t0, done, stats = time.time(), 0, {}
    with ThreadPoolExecutor(DART_WORKERS) as ex:
        futs = [ex.submit(_one, r, key, cmap) for r in recs]
        for f in as_completed(futs):
            r = f.result()
            stats[r["status"].split(":")[0]] = stats.get(r["status"].split(":")[0], 0) + 1
            done += 1
            if done % 50 == 0 or done == len(recs):
                el = time.time() - t0
                print(f"  {done}/{len(recs)}  {el:.0f}초  "
                      f"(남은 예상 {el / done * (len(recs) - done):.0f}초)  {stats}")

    print(f"\n수집 결과: {stats}")
    print(f"저장 위치: {RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
