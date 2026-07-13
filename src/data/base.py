"""DataProvider 인터페이스 + yfinance 공통 헬퍼.

yfinance 재무제표는 항목명이 종목/버전마다 달라서 후보 이름 목록으로 방어적으로 추출한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd
import yfinance as yf

from .cache import file_cache
from .models import FIN_COLUMNS, PEER_COLUMNS, CompanyData

# ── 재무제표 항목명 후보 (yfinance 항목명 → 표준 컬럼) ──────────────────
_ITEM_CANDIDATES: dict[str, list[str]] = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "gross_profit": ["Gross Profit"],
    "operating_income": ["Operating Income", "Total Operating Income As Reported", "EBIT"],
    "net_income": ["Net Income", "Net Income Common Stockholders",
                   "Net Income Continuous Operations"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "pretax_income": ["Pretax Income"],
    "tax_expense": ["Tax Provision"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
    "eps": ["Basic EPS", "Diluted EPS"],
    "total_assets": ["Total Assets"],
    "total_equity": ["Stockholders Equity", "Common Stock Equity",
                     "Total Equity Gross Minority Interest"],
    "total_liabilities": ["Total Liabilities Net Minority Interest"],
    "current_assets": ["Current Assets"],
    "current_liabilities": ["Current Liabilities"],
    "total_debt": ["Total Debt"],
    "cash": ["Cash Cash Equivalents And Short Term Investments",
             "Cash And Cash Equivalents"],
    "ocf": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure"],
    "fcf": ["Free Cash Flow"],
    "da": ["Depreciation And Amortization", "Depreciation Amortization Depletion",
           "Depreciation"],
    "dividends_paid": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
}
# 손익·현금흐름(연간 합산형) vs 재무상태(시점형) 구분 — TTM 계산 방식이 다름
_FLOW_COLS = ["revenue", "gross_profit", "operating_income", "net_income", "ebitda",
              "pretax_income", "tax_expense", "interest_expense", "eps",
              "ocf", "capex", "fcf", "da", "dividends_paid"]
_STOCK_COLS = ["total_assets", "total_equity", "total_liabilities",
               "current_assets", "current_liabilities", "total_debt", "cash"]


def _pick(df: pd.DataFrame, names: list[str]) -> pd.Series | None:
    """항목명 후보 중 첫 매칭 행을 반환 (컬럼=기간)."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            s = pd.to_numeric(df.loc[n], errors="coerce")
            if isinstance(s, pd.DataFrame):  # 중복 인덱스 방어
                s = s.iloc[0]
            if s.notna().any():
                return s
    return None


def extract_financials(tk: yf.Ticker) -> tuple[pd.DataFrame, list[str]]:
    """연간 재무제표 → 표준 컬럼 DataFrame (index=회계연도, 과거→최신)."""
    warnings: list[str] = []
    inc, bs, cf = tk.income_stmt, tk.balance_sheet, tk.cashflow
    if inc is None or inc.empty:
        raise ValueError("연간 손익계산서를 가져오지 못했습니다 (yfinance 응답 없음)")

    periods = sorted(inc.columns)  # Timestamp 목록
    rows = {}
    for col in FIN_COLUMNS:
        src = inc if col in ("revenue", "gross_profit", "operating_income", "net_income",
                             "ebitda", "pretax_income", "tax_expense",
                             "interest_expense", "eps") else \
              bs if col in _STOCK_COLS else cf
        s = _pick(src, _ITEM_CANDIDATES.get(col, []))
        rows[col] = s

    fin = pd.DataFrame(index=periods)
    for col, s in rows.items():
        fin[col] = s.reindex(periods) if s is not None else np.nan

    # 파생·보정
    if fin["total_liabilities"].isna().all():
        fin["total_liabilities"] = fin["total_assets"] - fin["total_equity"]
    if fin["ebitda"].isna().all():
        fin["ebitda"] = fin["operating_income"] + fin["da"]
        if fin["ebitda"].isna().all():
            warnings.append("EBITDA 항목이 없어 관련 지표(EV/EBITDA 등)는 N/A 처리됩니다.")
    # capex/배당은 yfinance에서 음수 → 양수로 정규화
    fin["capex"] = fin["capex"].abs()
    fin["dividends_paid"] = fin["dividends_paid"].abs()
    if fin["fcf"].isna().all():
        fin["fcf"] = fin["ocf"] - fin["capex"]

    fin["fiscal_end"] = periods  # 역사적 밴드 계산용 회계연도 종료일
    fin.index = [p.year for p in periods]
    fin = fin[~fin["revenue"].isna() | ~fin["total_assets"].isna()]
    # 같은 연도 중복(회계기간 변경 등) 시 마지막 값 사용
    fin = fin[~fin.index.duplicated(keep="last")]
    if len(fin) < 3:
        warnings.append(f"연간 재무제표가 {len(fin)}개년뿐이라 성장률·추세 분석의 신뢰도가 낮습니다.")
    return fin, warnings


def extract_ttm(tk: yf.Ticker, shares: float | None) -> tuple[pd.Series | None, list[str]]:
    """TTM: 손익·현금흐름은 최근 4개 분기 합, 재무상태는 최근 분기 값."""
    warnings: list[str] = []
    try:
        qinc, qbs, qcf = tk.quarterly_income_stmt, tk.quarterly_balance_sheet, tk.quarterly_cashflow
    except Exception:
        return None, ["분기 재무제표를 가져오지 못해 최근 연간 값으로 대체합니다."]
    if qinc is None or qinc.empty:
        return None, ["분기 재무제표가 없어 최근 연간 값으로 대체합니다."]

    ttm = pd.Series(dtype=float)
    for col in _FLOW_COLS:
        src = qinc if col in ("revenue", "gross_profit", "operating_income", "net_income",
                              "ebitda", "pretax_income", "tax_expense",
                              "interest_expense", "eps") else qcf
        s = _pick(src, _ITEM_CANDIDATES.get(col, []))
        if s is None:
            continue
        s = s.sort_index().dropna()
        if len(s) >= 4:
            ttm[col] = float(s.iloc[-4:].sum())
        # 분기 4개 미만이면 합산 왜곡 → 해당 항목은 연간 값 폴백에 맡김
    for col in _STOCK_COLS:
        s = _pick(qbs, _ITEM_CANDIDATES.get(col, []))
        if s is not None:
            s = s.sort_index().dropna()
            if len(s) > 0:
                ttm[col] = float(s.iloc[-1])

    if "capex" in ttm.index:
        ttm["capex"] = abs(ttm["capex"])
    if "dividends_paid" in ttm.index:
        ttm["dividends_paid"] = abs(ttm["dividends_paid"])
    if "ocf" in ttm.index and "capex" in ttm.index:
        ttm["fcf"] = ttm["ocf"] - ttm["capex"]
    if "ebitda" not in ttm.index and {"operating_income", "da"} <= set(ttm.index):
        ttm["ebitda"] = ttm["operating_income"] + ttm["da"]
    # EPS(TTM)는 분기 EPS 합보다 순이익/주식수가 안정적
    if shares and "net_income" in ttm.index:
        ttm["eps"] = ttm["net_income"] / shares

    if "net_income" not in ttm.index:
        return None, ["분기 손익 데이터가 부족해 TTM 대신 최근 연간 값을 사용합니다."]
    return ttm, warnings


def fetch_prices(yahoo_ticker: str, period: str = "5y") -> pd.Series:
    """일별 수정종가 (tz 제거)."""
    h = yf.Ticker(yahoo_ticker).history(period=period, auto_adjust=True)
    if h is None or h.empty:
        raise ValueError(f"{yahoo_ticker} 시세를 가져오지 못했습니다")
    s = h["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    s.name = yahoo_ticker
    return s


@file_cache("index_prices", ttl_hours=12)
def fetch_index_prices_df(symbol: str, period: str = "5y") -> pd.DataFrame:
    return fetch_prices(symbol, period).to_frame("close")


def fetch_index_prices(symbol: str, period: str = "5y") -> pd.Series:
    return fetch_index_prices_df(symbol, period)["close"]


@file_cache("ohlcv", ttl_hours=12)
def fetch_ohlcv(yahoo_ticker: str, period: str = "5y") -> pd.DataFrame:
    """일별 OHLCV (수정주가, tz 제거) — 주가차트용."""
    h = yf.Ticker(yahoo_ticker).history(period=period, auto_adjust=True)
    if h is None or h.empty:
        raise ValueError(f"{yahoo_ticker} 시세를 가져오지 못했습니다")
    h = h[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]).copy()
    h.index = pd.to_datetime(h.index).tz_localize(None)
    h.index.name = "date"
    return h


# ── 피어 지표 (yfinance info 기반, 종목·일 단위 캐시) ────────────────────
def _norm_div_yield(v):
    """yfinance dividendYield 정규화 → 소수(fraction).

    버전에 따라 0.021(소수) 또는 2.1(퍼센트)로 오므로,
    현실적으로 존재하기 어려운 25% 초과 값은 퍼센트로 간주해 /100.
    """
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    v = float(v)
    return v / 100.0 if v > 0.25 else v


@file_cache("yf_profile", ttl_hours=168)
def fetch_company_profile(yahoo_ticker: str) -> dict:
    """기업 소개용 프로필 — 영문 사업 요약·홈페이지·직원수. 개요는 자주 안 바뀌므로 7일 캐시."""
    info = yf.Ticker(yahoo_ticker).info or {}
    g = info.get
    return {
        "summary": g("longBusinessSummary"),
        "website": g("website"),
        "employees": g("fullTimeEmployees"),
        "city": g("city"),
        "country": g("country"),
    }


@file_cache("peer_info", ttl_hours=24)
def fetch_info_metrics(yahoo_ticker: str) -> dict:
    """한 종목의 info 기반 비교 지표 (json 캐시)."""
    info = yf.Ticker(yahoo_ticker).info or {}
    g = info.get
    mcap = g("marketCap")
    fcf, ocf = g("freeCashflow"), g("operatingCashflow")
    return {
        "name": g("longName") or g("shortName") or yahoo_ticker,
        "market_cap": mcap,
        "per": g("trailingPE"),
        "forward_per": g("forwardPE"),
        "pbr": g("priceToBook"),
        "psr": g("priceToSalesTrailing12Months"),
        "ev_ebitda": g("enterpriseToEbitda"),
        "div_yield": _norm_div_yield(g("dividendYield")),
        "roe": g("returnOnEquity"),
        "roa": g("returnOnAssets"),
        "gross_margin": g("grossMargins"),
        "op_margin": g("operatingMargins"),
        "net_margin": g("profitMargins"),
        "rev_growth": g("revenueGrowth"),
        "earnings_growth": g("earningsGrowth"),
        "debt_to_equity": g("debtToEquity"),   # % 단위 (yfinance 관례)
        "current_ratio": g("currentRatio"),
        "fcf_yield": (fcf / mcap) if fcf and mcap else None,
        "ocf_yield": (ocf / mcap) if ocf and mcap else None,
        "beta": g("beta"),
        "sector": g("sector"),
        "industry": g("industry"),
        "shares": g("sharesOutstanding"),
        "price": g("currentPrice") or g("regularMarketPrice") or g("previousClose"),
    }


def build_peer_table(yahoo_tickers: list[str], self_ticker: str,
                     labels: dict[str, str] | None = None) -> pd.DataFrame:
    """피어들의 info 지표를 모아 표준 피어 테이블 생성. labels로 표시명 덮어쓰기 가능."""
    rows = {}
    for t in yahoo_tickers:
        try:
            rows[t] = fetch_info_metrics(t)
        except Exception:
            continue
    df = pd.DataFrame.from_dict(rows, orient="index")
    if df.empty:
        return pd.DataFrame(columns=PEER_COLUMNS)
    df["is_self"] = df.index == self_ticker
    if labels:
        for t, name in labels.items():
            if t in df.index:
                df.loc[t, "name"] = name
    for c in PEER_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    df = df[[c for c in PEER_COLUMNS if c in df.columns] + ["sector", "industry"]]
    numeric = [c for c in df.columns if c not in ("name", "sector", "industry", "is_self")]
    df[numeric] = df[numeric].apply(pd.to_numeric, errors="coerce")
    # 명백한 이상치 제거: 음수/0 시총
    df = df[(df["market_cap"].isna()) | (df["market_cap"] > 0)]
    return df.sort_values("market_cap", ascending=False)


def trim_peers(df: pd.DataFrame, self_ticker: str, n: int) -> pd.DataFrame:
    """시총 상위 n개로 축소하되 자기 자신은 항상 유지."""
    if df.empty:
        return df
    df = df.sort_values("market_cap", ascending=False, na_position="last")
    top = df.head(n)
    if self_ticker in df.index and self_ticker not in top.index:
        top = pd.concat([df.loc[[self_ticker]], top.head(n - 1)])
    return top


class DataProvider(ABC):
    """시장별 데이터 수집 인터페이스."""

    market: str
    benchmark_name: str

    @abstractmethod
    def resolve(self, query: str) -> dict:
        """사용자 입력(코드/이름) → {ticker, yahoo_ticker, name, sector, ...}"""

    @abstractmethod
    def load(self, query: str, peer_count: int = 10) -> CompanyData:
        """티커 하나로 CompanyData 전체 구성."""
