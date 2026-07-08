"""종목 유니버스: 상장 목록·섹터 분류·피어 후보 선정.

- KR: FinanceDataReader KRX 목록(시총·시장) + KRX-DESC(섹터/업종) 병합, pykrx로 공식 멀티플
- US: 위키피디아 S&P 500 구성종목 (GICS Sector / Sub-Industry)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .cache import file_cache

# ── 한국 ────────────────────────────────────────────────────────────────
@file_cache("kr_listing", ttl_hours=24)
def get_kr_listing() -> pd.DataFrame:
    """KRX 상장 목록: Code, Name, Market, Sector(업종분류), SubSector, Marcap, Stocks, Close.

    KRX-DESC의 'Industry' 컬럼이 실제 산업분류(96% 채워짐)이므로 이를 Sector로 사용한다.
    (KRX-DESC의 'Sector' 컬럼은 67%만 채워진 보조 분류 → SubSector)
    """
    import FinanceDataReader as fdr

    base = fdr.StockListing("KRX")          # 공식 종가·시총·주식수
    base = base.rename(columns={"Symbol": "Code"})
    keep = [c for c in ["Code", "Name", "Market", "Marcap", "Stocks", "Close"] if c in base.columns]
    base = base[keep].copy()

    try:
        desc = fdr.StockListing("KRX-DESC")
        desc = desc.rename(columns={"Symbol": "Code",
                                    "Industry": "Sector", "Sector": "SubSector"})
        cols = [c for c in ["Code", "Sector", "SubSector"] if c in desc.columns]
        if "Sector" in cols:
            base = base.merge(desc[cols], on="Code", how="left")
    except Exception:
        pass
    for c in ("Sector", "SubSector"):
        if c not in base.columns:
            base[c] = np.nan

    base = base.dropna(subset=["Code", "Name"])
    base["Code"] = base["Code"].astype(str).str.zfill(6)
    # 우선주·스팩 등 코드 끝자리가 0이 아닌 종목은 피어 후보에서 제외
    base["is_common"] = base["Code"].str.endswith("0") & ~base["Name"].str.contains("스팩", na=False)
    return base.reset_index(drop=True)


def yahoo_ticker_kr(code: str, market: str) -> str:
    return f"{code}.KQ" if str(market).upper().startswith("KOSDAQ") else f"{code}.KS"


def find_kr(query: str) -> pd.DataFrame:
    """6자리 코드 또는 종목명(부분 일치)으로 검색. 정확명 일치 우선."""
    listing = get_kr_listing()
    q = query.strip()
    if q.isdigit() and len(q) <= 6:
        return listing[listing["Code"] == q.zfill(6)]
    exact = listing[listing["Name"] == q]
    if len(exact) > 0:
        return exact
    part = listing[listing["Name"].str.contains(q, case=False, na=False, regex=False)]
    return part.sort_values("Marcap", ascending=False) if "Marcap" in part.columns else part


def select_peers_kr(code: str, n: int = 10) -> list[str]:
    """같은 섹터에서 시총 상위 n개 (자기 자신 포함, 보통주만)."""
    listing = get_kr_listing()
    me = listing[listing["Code"] == code]
    if me.empty:
        return [code]
    sector = me.iloc[0].get("Sector")
    pool = listing[listing["is_common"]]
    if isinstance(sector, str) and sector.strip():
        pool = pool[pool["Sector"] == sector]
    else:
        return [code]
    if "Marcap" in pool.columns:
        pool = pool.sort_values("Marcap", ascending=False)
    codes = pool["Code"].tolist()
    if code not in codes:
        codes = [code] + codes
    top = codes[:n]
    if code not in top:
        top = [code] + top[: n - 1]
    return top


# ── 미국 ────────────────────────────────────────────────────────────────
@file_cache("sp500", ttl_hours=24 * 7)
def get_sp500() -> pd.DataFrame:
    """S&P 500 구성종목: Symbol, Name, Sector(GICS), SubIndustry."""
    import io

    import requests

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                        timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    df = df.rename(columns={
        "Symbol": "Symbol", "Security": "Name",
        "GICS Sector": "Sector", "GICS Sub-Industry": "SubIndustry",
    })
    df = df[["Symbol", "Name", "Sector", "SubIndustry"]].dropna(subset=["Symbol"])
    # 야후 표기 (BRK.B → BRK-B)
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
    return df.reset_index(drop=True)


def find_us(query: str) -> pd.DataFrame:
    """심볼 또는 회사명으로 S&P500 내 검색. 없으면 빈 DF (직접 티커 사용은 허용)."""
    sp = get_sp500()
    q = query.strip()
    exact = sp[sp["Symbol"].str.upper() == q.upper()]
    if len(exact) > 0:
        return exact
    return sp[sp["Name"].str.contains(q, case=False, na=False, regex=False)]


def select_peers_us(symbol: str, n: int = 10) -> tuple[list[str], str | None]:
    """S&P500 내 같은 GICS Sub-Industry 우선, 부족하면 같은 Sector로 보충.

    반환: (야후 티커 후보 목록, 피어 기준 설명)
    """
    symbol = symbol.upper()
    sp = get_sp500()
    me = sp[sp["Symbol"] == symbol]
    if me.empty:
        return [symbol], None  # S&P500 밖 → provider에서 info 섹터로 재시도
    sub, sec = me.iloc[0]["SubIndustry"], me.iloc[0]["Sector"]
    cands = sp[sp["SubIndustry"] == sub]["Symbol"].tolist()
    basis = f"GICS 세부산업 '{sub}'"
    if len(cands) < max(4, min(n, 6)):
        extra = sp[(sp["Sector"] == sec) & (~sp["Symbol"].isin(cands))]["Symbol"].tolist()
        cands = cands + extra[: max(0, int(n * 1.5) - len(cands))]
        basis = f"GICS 섹터 '{sec}' (세부산업 표본 부족으로 확장)"
    if symbol not in cands:
        cands = [symbol] + cands
    return cands, basis


def peers_us_by_sector(symbol: str, sector: str, n: int = 10) -> tuple[list[str], str | None]:
    """S&P500 밖 종목: info 섹터와 GICS 섹터를 매칭해 후보 구성 (근사)."""
    sp = get_sp500()
    # yahoo 섹터명 → GICS 섹터명 근사 매핑
    m = {
        "Technology": "Information Technology", "Financial Services": "Financials",
        "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples",
        "Healthcare": "Health Care", "Communication Services": "Communication Services",
        "Industrials": "Industrials", "Energy": "Energy", "Utilities": "Utilities",
        "Basic Materials": "Materials", "Real Estate": "Real Estate",
    }
    gics = m.get(sector, sector)
    pool = sp[sp["Sector"] == gics]["Symbol"].tolist()
    if not pool:
        return [symbol], None
    cands = [symbol] + pool[: int(n * 1.5)]
    return cands, f"GICS 섹터 '{gics}' (S&P500 밖 종목이라 섹터 기준 근사)"


# ── 공통 ────────────────────────────────────────────────────────────────
KR_FINANCIAL_KEYWORDS = ["은행", "보험", "금융", "증권", "신탁", "투자", "캐피탈", "카드"]
US_FINANCIAL_SECTORS = ["Financial Services", "Financials"]


def detect_financial(sector: str | None, industry: str | None, market: str) -> bool:
    text = f"{sector or ''} {industry or ''}"
    if market == "KR":
        return any(k in text for k in KR_FINANCIAL_KEYWORDS)
    return (sector or "") in US_FINANCIAL_SECTORS or "Insurance" in text or "Bank" in text
