"""DataProvider 인터페이스 + yfinance 공통 헬퍼.

yfinance 재무제표는 항목명이 종목/버전마다 달라서 후보 이름 목록으로 방어적으로 추출한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf

from .cache import file_cache
from .models import FIN_COLUMNS, PEER_COLUMNS, CompanyData

# 피어 info 동시 다운로드 워커 수 — I/O 바운드(yfinance .info)라 병렬이 큰 이득.
# 야후 레이트리밋을 자극하지 않는 선(5~8)에서 8로 둔다. 빈 응답은 file_cache의
# validate가 걸러 stale 폴백하므로 과도한 동시성만 피하면 안전하다.
PEER_FETCH_WORKERS = 8

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
    "shares_outstanding": ["Basic Average Shares", "Diluted Average Shares"],
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
    # 장부가 왜곡 판별용(RIM 적용 여부). 항목이 없는 것과 못 받은 것은 뜻이 다르다 —
    # 대차대조표·현금흐름표 자체는 받았는데 이 줄만 없으면 그 회사에 그 항목이 없다는 뜻이다.
    # 그 구분은 extract_financials가 아래에서 0으로 채우며 처리한다.
    "goodwill": ["Goodwill"],
    "intangibles": ["Goodwill And Other Intangible Assets"],
    "buyback": ["Repurchase Of Capital Stock", "Common Stock Payments"],
}
# 손익·현금흐름(연간 합산형) vs 재무상태(시점형) 구분 — TTM 계산 방식이 다름
_FLOW_COLS = ["revenue", "gross_profit", "operating_income", "net_income", "ebitda",
              "pretax_income", "tax_expense", "interest_expense", "eps",
              "ocf", "capex", "fcf", "da", "dividends_paid", "buyback"]
_STOCK_COLS = ["total_assets", "total_equity", "total_liabilities",
               "current_assets", "current_liabilities", "total_debt", "cash",
               "goodwill", "intangibles"]


def _sane_ebitda(ebitda, operating_income, da) -> tuple[float | None, str | None]:
    """EBITDA가 영업이익보다 작으면 되돌린다 — `(값, 조치)`. 조치는 None·"rebuilt"·"dropped".

    EBITDA는 영업이익에 감가상각비를 **다시 더한** 값이라, 정의상 영업이익보다 작을 수
    없다(D&A는 음수가 아니다). 그런데 무료 데이터가 그런 값을 준다 — 인텔 2026Q2는
    영업이익 +1,966M인데 EBITDA가 −7,274M이었고, 그 한 분기가 TTM을 끌어내려
    EV/EBITDA가 **144배**로 튀었다(공시값 29.4배 · 업종 중앙 29.5배).

    인텔만의 일이 아니다. 15종목 표본에서 TTM 역전 2곳(INTC·NAVER)·분기 역전 4곳이었다
    (`scripts/check_ebitda_sanity.py`). 되돌릴 재료(D&A)가 없으면 **버린다** —
    오염된 값보다 '없음'이 정직하다(ADR-0011).

    연간 프레임과 TTM이 같은 규칙을 쓰도록 여기 한 곳에 둔다.
    """
    if ebitda is None or pd.isna(ebitda) or operating_income is None or pd.isna(operating_income):
        return (None if ebitda is None or pd.isna(ebitda) else float(ebitda)), None
    if float(ebitda) >= float(operating_income):
        return float(ebitda), None
    if da is not None and not pd.isna(da):
        rebuilt = float(operating_income) + float(da)
        if rebuilt >= float(operating_income):          # D&A가 음수로 오는 경우까지 막는다
            return rebuilt, "rebuilt"
    return None, "dropped"


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
        raise ValueError("연간 손익계산서를 가져오지 못했습니다 — ETF·펀드처럼 재무제표가 없는 "
                         "상품이거나, 데이터 원천(yfinance)이 일시적으로 응답하지 않았을 수 있어요.")

    periods = sorted(inc.columns)  # Timestamp 목록
    rows = {}
    for col in FIN_COLUMNS:
        src = inc if col in ("revenue", "gross_profit", "operating_income", "net_income",
                             "ebitda", "pretax_income", "tax_expense",
                             "interest_expense", "eps", "shares_outstanding") else \
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
    # 있어도 말이 되는지 본다 — 영업이익보다 작은 EBITDA가 실제로 온다(_sane_ebitda 참조).
    _reb, _drop = 0, 0
    for _t in fin.index:
        _v, _act = _sane_ebitda(fin.at[_t, "ebitda"], fin.at[_t, "operating_income"],
                                fin.at[_t, "da"])
        if _act:
            fin.at[_t, "ebitda"] = np.nan if _v is None else _v
            _reb, _drop = _reb + (_act == "rebuilt"), _drop + (_act == "dropped")
    if _reb or _drop:
        warnings.append(
            f"연간 EBITDA {_reb + _drop}개 연도가 영업이익보다 작게 와서 "
            f"{'영업이익+감가상각비로 다시 계산' if _reb else ''}"
            f"{'·' if _reb and _drop else ''}{'결측 처리' if _drop else ''}했습니다.")
    # capex/배당/자사주는 yfinance에서 음수(유출) → 양수로 정규화
    fin["capex"] = fin["capex"].abs()
    fin["dividends_paid"] = fin["dividends_paid"].abs()
    fin["buyback"] = fin["buyback"].abs()
    if fin["fcf"].isna().all():
        fin["fcf"] = fin["ocf"] - fin["capex"]

    # 무형자산·영업권·자사주는 **'없음'과 '못 받음'을 갈라야** 한다.
    # 애플은 영업권 줄이 아예 없는데, 그건 데이터 결측이 아니라 영업권이 실제로 없다는 뜻이다.
    # 이걸 결측으로 두면 "판별 불가"가 되어 장부가 왜곡 판정이 PBR 단독 규칙으로 되돌아간다.
    # 판별 기준: **그 표를 읽어내긴 했는가**(총자산이 있으면 대차대조표, ocf가 있으면 현금흐름표).
    # 표를 읽었는데 그 줄이 없으면 0으로 채우고, 표 자체를 못 받았으면 결측으로 남긴다.
    got_bs, got_cf = fin["total_assets"].notna(), fin["ocf"].notna()
    for col, got in (("goodwill", got_bs), ("intangibles", got_bs), ("buyback", got_cf)):
        fin.loc[got & fin[col].isna(), col] = 0.0
    # 'Goodwill And Other Intangible Assets'가 없고 'Goodwill'만 있는 회사가 있다 — 둘 중 큰 쪽을
    # 무형자산 총액으로 삼는다(영업권은 정의상 무형자산에 포함되므로 합계보다 클 수 없다).
    fin["intangibles"] = fin[["intangibles", "goodwill"]].max(axis=1)

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
    # 분기 합이라 한 분기의 오염이 그대로 TTM에 들어온다 — 이 자리가 EV/EBITDA 144배의
    # 출처였다(#116). 규칙은 _sane_ebitda 한 곳에 있다.
    if "ebitda" in ttm.index:
        _v, _act = _sane_ebitda(ttm.get("ebitda"), ttm.get("operating_income"), ttm.get("da"))
        if _act == "rebuilt":
            ttm["ebitda"] = _v
            warnings.append("TTM EBITDA가 영업이익보다 작게 와서 '영업이익 + 감가상각비'로 "
                            "다시 계산했습니다.")
        elif _act == "dropped":
            # TTM에서 빼면 CompanyData.latest()가 최근 **연간** EBITDA로 내려간다
            # ("TTM 우선, 없으면 최근 연간 값"). N/A가 되는 것은 연간도 없을 때뿐이다.
            # 실제로 NAVER가 이 경로다 — 분기는 깨졌고 연간 네 해는 전부 정상이었다.
            ttm = ttm.drop("ebitda")
            warnings.append("TTM EBITDA가 영업이익보다 작게 오고 분기 감가상각비도 없어 "
                            "TTM에서 제외했습니다 — 최근 연간 EBITDA로 대체합니다.")
    # EPS(TTM)는 분기 EPS 합보다 순이익/주식수가 안정적
    if shares and "net_income" in ttm.index:
        ttm["eps"] = ttm["net_income"] / shares

    if "net_income" not in ttm.index:
        return None, ["분기 손익 데이터가 부족해 TTM 대신 최근 연간 값을 사용합니다."]
    return ttm, warnings


# 시세 캐시 수명. 마지막 행은 장중이면 **아직 안 끝난 봉**이라 '종가'가 아니다.
# 12시간이면 오전에 받은 장중가가 장 마감 뒤까지 종가 행세를 하므로 1시간으로 둔다.
# (예전엔 fetch_prices만 캐시가 없어 매 분석마다 5년치를 다시 받았다 — 1시간 캐시는
#  일관성뿐 아니라 호출 수에서도 이득이다.)
PRICE_TTL_HOURS = 1


@file_cache("price_frame", ttl_hours=PRICE_TTL_HOURS)
def fetch_price_frame(yahoo_ticker: str, period: str = "5y") -> pd.DataFrame:
    """미조정·수정 종가와 OHLCV를 **한 번의 조회로** 받아 하나의 스냅샷으로 캐시한다.

    예전에는 수정종가(fetch_prices, 캐시 없음)와 미조정(fetch_prices_raw, 12시간 캐시)이
    따로 조회·캐시됐다. 그래서 장중에 미조정 캐시가 채워지면 **같은 날짜에 두 개의 가격**이
    생겼다 — 실측(2026-07-28 15:31): 삼성전자 수정 219,500 vs 미조정 230,000(-4.57%).
    화면의 현재가와 밴드가 쓰는 주가가 서로 달랐다는 뜻이다.

    yfinance는 auto_adjust=False 한 번에 미조정 종가(Close)와 수정종가(Adj Close)를 함께
    준다. 둘을 한 프레임으로 받아 같이 캐시하면 계열 간 어긋남이 구조적으로 불가능해진다.
    (실측 확인: Adj Close는 auto_adjust=True의 Close와 소수점 이하까지 일치하고,
     OHLC는 adj/close 비율을 곱하면 정확히 같다. 거래량은 auto_adjust가 건드리지 않는다.)
    """
    h = yf.Ticker(yahoo_ticker).history(period=period, auto_adjust=False)
    if h is None or h.empty:
        raise ValueError(f"{yahoo_ticker} 시세를 가져오지 못했습니다")
    h = h.dropna(subset=["Close"]).copy()
    h.index = pd.to_datetime(h.index).tz_localize(None)
    h.index.name = "date"
    adj = h["Adj Close"] if "Adj Close" in h.columns else h["Close"]
    out = pd.DataFrame({
        "close": h["Close"],          # 미조정 — 그날 실제로 붙어 있던 가격
        "adj_close": pd.to_numeric(adj, errors="coerce"),   # 수정종가
        "open": h.get("Open"), "high": h.get("High"), "low": h.get("Low"),
        "volume": h.get("Volume"),
    }, index=h.index)
    return out.dropna(subset=["close", "adj_close"])


def fetch_prices(yahoo_ticker: str, period: str = "5y") -> pd.Series:
    """일별 **수정종가** — 배당 재투자 기준. 수익률·베타·상대성과·추적오차용."""
    s = fetch_price_frame(yahoo_ticker, period)["adj_close"]
    s.name = yahoo_ticker
    return s


def fetch_prices_raw(yahoo_ticker: str, period: str = "5y") -> pd.Series:
    """일별 **미조정** 종가 — 그날 실제로 화면에 찍혀 있던 가격.

    수정종가는 과거 가격을 그 뒤 지급된 배당만큼 낮춰 잡는다. 총수익 기준 비교에는 그게
    맞지만, '그때 이 가격이면 PER가 몇 배였나 / 배당수익률이 몇 %였나'를 묻는 계산에 쓰면
    과거 값이 배당 누적분만큼 왜곡된다(실측: TLT 5년 최대 +18.7%, KB금융 5년 -21.0%).
    그런 계산은 이 함수를 쓴다.

    액면분할은 auto_adjust와 무관하게 이미 반영돼 있어(실측: NVDA 10:1 분할 전 종가가
    $1,150이 아니라 $115) 따로 처리하지 않는다.
    """
    s = fetch_price_frame(yahoo_ticker, period)["close"]
    s.name = yahoo_ticker
    return s


def fetch_index_prices(symbol: str, period: str = "5y") -> pd.Series:
    """벤치마크 지수 종가 — 상대성과·베타용이라 수정종가 계열."""
    return fetch_prices(symbol, period)


def fetch_ohlcv(yahoo_ticker: str, period: str = "5y") -> pd.DataFrame:
    """일별 OHLCV (수정주가, tz 제거) — 주가차트용.

    fetch_price_frame의 같은 스냅샷에서 만든다. 미조정 OHLC에 adj_close/close 비율을
    곱한 값은 yfinance auto_adjust=True의 결과와 소수점까지 일치한다(실측). 거래량은
    auto_adjust가 조정하지 않으므로 그대로 둔다.
    """
    f = fetch_price_frame(yahoo_ticker, period)
    ratio = (f["adj_close"] / f["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    out = pd.DataFrame({
        "Open": f["open"] * ratio, "High": f["high"] * ratio,
        "Low": f["low"] * ratio, "Close": f["adj_close"], "Volume": f["volume"],
    }, index=f.index)
    out.index.name = "date"
    return out


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


def _info_has_substance(m: dict) -> bool:
    """야후가 레이트리밋으로 빈 info를 성공처럼 줄 때를 걸러낸다.
    시총·가격·PER·PBR이 전부 없으면 실질 없는 응답으로 본다
    (정상 응답은 결측이 있어도 이 중 하나는 있다)."""
    return isinstance(m, dict) and any(
        m.get(k) is not None for k in ("market_cap", "price", "per", "pbr"))


@file_cache("peer_info_v3", ttl_hours=24, validate=_info_has_substance)
def fetch_info_metrics(yahoo_ticker: str) -> dict:
    """한 종목의 info 기반 비교 지표 (json 캐시). 빈 응답은 캐시하지 않는다."""
    info = yf.Ticker(yahoo_ticker).info or {}
    g = info.get
    mcap = g("marketCap")
    fcf, ocf = g("freeCashflow"), g("operatingCashflow")
    return {
        "name": g("longName") or g("shortName") or yahoo_ticker,
        "market_cap": mcap,
        "per": g("trailingPE"),
        "forward_per": g("forwardPE"),
        "forward_eps": g("forwardEps"),
        "target_mean": g("targetMeanPrice"),
        "target_high": g("targetHighPrice"),
        "target_low": g("targetLowPrice"),
        "n_analysts": g("numberOfAnalystOpinions"),
        "recomm_mean_yf": g("recommendationMean"),   # 1=적극매수 (야후 척도, provider에서 뒤집음)
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
        # 재무제표 공시 통화. 주가 통화와 다르면(ADR) 주가÷재무 지표가 환율배만큼
        # 틀어진다 — models.currency_mismatch()가 이 값을 본다. v3로 올린 건
        # 이 키가 없는 옛 캐시를 통화 일치로 오인하지 않기 위해서다.
        "financial_currency": g("financialCurrency"),
        "currency": g("currency"),
    }


# 재무 통화 ≠ 주가 통화(ADR)인 종목에서 야후가 **직접 계산해 주는 값도 이미 틀려 있는**
# 지표들. 분자는 시총·주가(표시 통화)인데 분모가 재무값(본국 통화)이라 환율배만큼 어긋난다.
# 실측(TSM, 재무 TWD·주가 USD): pbr 83.2배 · psr 0.47 · ev_ebitda 4.43 — 전부 비현실적.
# trailingPE·dividendYield는 야후가 주가 통화로 환산해 주므로 남긴다(TSM per 35.6 = 정상).
_FX_MIXED_PEER_COLS = ["pbr", "psr", "ev_ebitda", "fcf_yield", "ocf_yield"]


def _mask_fx_mixed_peers(df: pd.DataFrame) -> pd.DataFrame:
    """ADR 등 통화가 섞인 행의 오염된 배수를 결측 처리한다.

    자사뿐 아니라 **피어로 섞여 들어온 ADR**도 막아야 한다 — 하나만 남아도 업종 중앙값이
    그쪽으로 끌려가고, 그 중앙값이 적정가 ①의 입력이 된다.
    """
    if df.empty or "financial_currency" not in df.columns:
        return df
    fc = df["financial_currency"].astype("string").str.upper()
    pc = df["currency"].astype("string").str.upper() if "currency" in df.columns else None
    if pc is None:
        return df
    mixed = fc.notna() & pc.notna() & (fc != pc)
    if mixed.any():
        for col in _FX_MIXED_PEER_COLS:
            if col in df.columns:
                df.loc[mixed, col] = np.nan
    return df


def build_peer_table(yahoo_tickers: list[str], self_ticker: str,
                     labels: dict[str, str] | None = None) -> pd.DataFrame:
    """피어들의 info 지표를 모아 표준 피어 테이블 생성. labels로 표시명 덮어쓰기 가능.

    각 피어의 info 조회는 독립적인 네트워크 호출이라 **스레드풀로 동시 다운로드**한다
    (순차 30초대 → 병렬 6~8초대). file_cache가 피어마다 다른 키 파일에 쓰므로 안전하다.
    진행은 progress.report()로 알린다(리포터가 없으면 no-op).
    """
    from .progress import report

    rows = {}
    uniq = list(dict.fromkeys(t for t in yahoo_tickers if t))  # 중복 제거·순서 보존
    total = len(uniq)
    if uniq:
        report("피어 수집", 0, total)
        with ThreadPoolExecutor(max_workers=min(PEER_FETCH_WORKERS, len(uniq))) as ex:
            futures = {ex.submit(fetch_info_metrics, t): t for t in uniq}
            done = 0
            for fut in as_completed(futures):
                done += 1
                report("피어 수집", done, total)
                try:
                    rows[futures[fut]] = fut.result()
                except Exception:
                    continue
    df = pd.DataFrame.from_dict(rows, orient="index")
    if df.empty:
        return pd.DataFrame(columns=PEER_COLUMNS)
    df = _mask_fx_mixed_peers(df)
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


def fill_self_from_financials(peers: pd.DataFrame, self_ticker: str,
                              fin: pd.DataFrame, market_cap,
                              fx_mixed: bool = False) -> pd.DataFrame:
    """야후 info에 없는 자사 성장률·안정성·현금흐름 지표를 자사 연간 재무제표로 보완.

    일부 종목(특히 코스닥)은 야후가 revenueGrowth·debtToEquity·freeCashflow를
    제공하지 않아 자사 값 결측만으로 업종 상대점수 축 전체가 죽는다.
    피어 값(야후 TTM 근사)과 산출 기준이 완전히 같지는 않지만 결측보다 낫다.
    이미 값이 있는 지표는 건드리지 않는다.
    """
    if peers.empty or fin is None or fin.empty or self_ticker not in peers.index:
        return peers
    df = peers.copy()

    def _series(col):
        if col not in fin.columns:
            return None
        s = pd.to_numeric(fin[col], errors="coerce").dropna()
        return s if len(s) else None

    def _growth(col):
        s = _series(col)
        if s is None or len(s) < 2:
            return None
        prev, cur = float(s.iloc[-2]), float(s.iloc[-1])
        return (cur / prev - 1.0) if prev > 0 else None

    def _ratio(num_col, den_col, scale=1.0):
        n, dv = _series(num_col), _series(den_col)
        if n is None or dv is None:
            return None
        den = float(dv.iloc[-1])
        return (float(n.iloc[-1]) / den * scale) if den > 0 else None

    mcap = float(market_cap) if market_cap else 0.0

    def _yield(col):
        s = _series(col)
        return (float(s.iloc[-1]) / mcap) if s is not None and mcap > 0 else None

    fills = {
        "rev_growth": _growth("revenue"),
        "earnings_growth": _growth("net_income"),
        "debt_to_equity": _ratio("total_debt", "total_equity", 100.0),  # yfinance % 관례
        "current_ratio": _ratio("current_assets", "current_liabilities"),
        # 아래 둘만 분모가 시가총액(표시 통화)이라, 재무 통화가 다르면 환율배만큼 틀어진다.
        # 위 넷은 재무끼리의 비율이라 통화가 약분돼 통화 혼재와 무관하다.
        "fcf_yield": None if fx_mixed else _yield("fcf"),
        "ocf_yield": None if fx_mixed else _yield("ocf"),
    }
    for col, val in fills.items():
        if val is None:
            continue
        if col not in df.columns:
            df[col] = np.nan
        cur = df.at[self_ticker, col]
        if cur is None or (isinstance(cur, float) and np.isnan(cur)):
            df.at[self_ticker, col] = float(val)
    return df


def trim_peers(df: pd.DataFrame, self_ticker: str, n: int,
               self_mcap: float | None = None) -> pd.DataFrame:
    """자사와 **시총이 가까운** 순으로 n개로 축소하되 자기 자신은 항상 유지 (ADR-0013).

    예전에는 '시총 상위 n'이었다. 그 뒤에 `comparable_peers`가 1/5~5배 창으로 걸렀는데,
    **편향된 표본추출 아래에 필터를 달면 정확해지는 것이 아니라 비어 버린다** — 소형주
    기준으로 창을 통과할 회사는 이 함수가 이미 잘라낸 뒤였다. 실측에서 참엔지니어링의
    피어 배율 중앙이 108.6배(창 안 1곳/9)였고, 반대로 삼성전자는 피어가 자사의 수백분의
    1이라 역시 창 안이 2곳뿐이었다. **양 끝단이 같은 이유로 망가진다.**

    거리는 `|log(피어 시총 / 자사 시총)|`로 잰다. 배수 세계에서 2배와 1/2배는 같은
    거리인데 선형 차이로 재면 큰 쪽만 뽑히기 때문이다.

    `self_mcap`을 인자로 받는 이유 — 표 안의 자사 행에서 읽으면 그 값이 결측일 수 있고
    (상장목록에 없는 종목·미국 경로), 규모를 모르는 채로 '인접'을 정할 수는 없다.
    **없거나 비양수면 종전 '시총 상위 n' 동작을 그대로 유지한다**(ADR-0011의 원칙:
    오염된 값보다 계산 불가가 정직하다 — 여기서는 '종전 동작'이 그 자리를 맡는다).

    시총이 결측인 피어는 버리지 않고 **뒤로 민다.** 버리면 표본이 더 얇아지는데,
    이 함수가 고치려는 것이 바로 표본이 비는 문제다.
    """
    if df.empty:
        return df
    try:
        anchor = float(self_mcap)
    except (TypeError, ValueError):
        anchor = 0.0
    if anchor > 0 and np.isfinite(anchor):
        mc = pd.to_numeric(df["market_cap"], errors="coerce")
        dist = (np.log(mc.where(mc > 0)) - np.log(anchor)).abs()
        df = df.assign(_d=dist).sort_values("_d", na_position="last").drop(columns="_d")
    else:
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
