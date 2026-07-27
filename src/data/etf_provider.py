"""미국 ETF 데이터 수집 — yfinance 단일 소스.

기업(CompanyData)과 출력 모델이 달라(ETFData, 재무제표 없음) us_provider.py와 분리한다.
무료 데이터라 결측·레이트리밋이 흔하므로 선택 필드는 실패해도 예외를 던지지 않고
warnings에 사유만 남긴다 — analysis 계층(src/analysis/etf.py)은 순수 함수로 이 결측을
그대로 흡수해 크래시 없이 N/A 처리한다. 시세(가격 이력) 자체가 없을 때만 ValueError.
"""
from __future__ import annotations

import pandas as pd
import yfinance as yf

from .base import fetch_index_prices, fetch_prices, fetch_prices_raw
from .models import ETFData

# 벤치마크 선택 키워드 — category(예: 'Large Blend'/'Long Government') 우선,
# 없으면 name으로 폴백. 원자재는 비교할 벤치마크가 없다(밸류에이션 자체를 스킵).
_BOND_KW = ("bond", "government", "treasury", "muni", "credit", "fixed income")
_COMMODITY_KW = ("commodit", "precious", "gold", "natural resources")
_FOREIGN_KW = ("foreign", "world", "emerging", "international",
              "china", "japan", "europe", "pacific")

# yfinance quoteType이 이 값이면 ETF가 아닌 게 확실한 경우만 걸러낸다(보수적 판정 —
# quoteType이 비어 있거나 애매하면 통과시켜 결측 때문에 정상 ETF를 막지 않는다).
_NOT_ETF_TYPES = {"EQUITY", "INDEX", "CURRENCY", "CRYPTOCURRENCY", "FUTURE", "OPTION"}


def _benchmark_for(category: str | None, name: str) -> tuple[str | None, str]:
    """ETF 유형(category, 없으면 name)에 맞는 비교 벤치마크 ETF와 한국어 라벨."""
    text = (category or "").strip().lower() or (name or "").lower()
    if any(k in text for k in _BOND_KW):
        return "AGG", "미국 종합채권(AGG)"
    if any(k in text for k in _COMMODITY_KW):
        return None, ""
    if any(k in text for k in _FOREIGN_KW):
        return "VXUS", "전세계 제외미국(VXUS)"
    return "VTI", "미국 전체시장(VTI)"


def fetch_etf(symbol: str) -> ETFData:
    """ETF 티커 하나 → ETFData (현재가·5년 시세·배당이력·유형별 벤치마크 지표).

    개별 지표(NAV·배당·벤치마크 등)는 실패해도 None/빈 값으로 두고 warnings에 남길 뿐
    예외를 던지지 않는다. 다만 시세 이력을 못 받으면 분석 자체가 불가능하므로 그때만
    ValueError로 명확히 안내한다(상장폐지·오타 등 — kr_provider/us_provider와 같은 방식).
    """
    sym = (symbol or "").strip().upper().replace(".", "-")
    if not sym:
        raise ValueError("ETF 티커를 입력하세요.")
    warnings: list[str] = []

    tk = yf.Ticker(sym)
    try:
        info = tk.info or {}
    except Exception:
        info = {}
        warnings.append("ETF 기본 정보(운용사 제공 지표)를 가져오지 못했습니다 — 일부 항목이 N/A로 표시됩니다.")

    qtype = str(info.get("quoteType") or "").upper()
    if qtype in _NOT_ETF_TYPES:
        raise ValueError(f"'{sym}'은(는) ETF가 아닌 것으로 보입니다 — 일반 종목 검색을 이용해 주세요.")

    try:
        prices = fetch_prices(sym)
    except Exception:
        raise ValueError(f"'{sym}' 시세를 찾을 수 없습니다 — 상장폐지·거래정지 상태이거나 "
                         "잘못된 ETF 티커일 수 있어요.")

    # 미조정 종가 — 배당수익률·52주 밴드용(수정종가를 쓰면 과거가 배당만큼 부풀려진다).
    # 실패해도 분석 계층이 수정종가로 폴백하므로 경고만 남기고 넘어간다.
    try:
        prices_raw = fetch_prices_raw(sym)
    except Exception:
        prices_raw = None
        warnings.append("미조정 시세를 받지 못해 배당수익률·52주 밴드를 수정주가로 계산합니다 "
                        "— 과거 구간이 다소 낙관적으로(현재가 비싸 보이게) 나올 수 있습니다.")

    try:
        divs = tk.dividends
        if divs is None:
            divs = pd.Series(dtype=float)
    except Exception:
        divs = pd.Series(dtype=float)
        warnings.append("배당 지급 이력을 가져오지 못했습니다 — 배당수익률 역사밴드를 만들 수 없습니다.")

    name = info.get("longName") or info.get("shortName") or sym
    category = info.get("category")
    bench_sym, bench_label = _benchmark_for(category, name)

    bench_pe = bench_yield = None
    index_prices = prices   # 벤치마크가 없거나 실패하면 자기 시세로 대체(상대성과가 자연히 ~0)
    if bench_sym:
        try:
            bi = yf.Ticker(bench_sym).info or {}
            bench_pe = bi.get("trailingPE")
            bench_yield = bi.get("yield")
        except Exception:
            warnings.append(f"벤치마크({bench_sym}) 지표를 가져오지 못해 상대 비교 일부를 생략합니다.")
        try:
            index_prices = fetch_index_prices(bench_sym)
        except Exception:
            warnings.append(f"벤치마크({bench_sym}) 시세를 가져오지 못해 상대성과를 계산하지 않습니다.")

    price = info.get("regularMarketPrice") or info.get("previousClose")
    price = float(price) if price is not None else float(prices.iloc[-1])

    # 보유종목·섹터·자산군 (funds_data는 한 번만 받아 재사용 — 개별 항목은 실패해도 무시)
    top_holdings: list = []
    sectors: dict = {}
    asset_classes: dict = {}
    try:
        fd = tk.funds_data
    except Exception:
        fd = None
        warnings.append("펀드 구성 데이터(보유종목·섹터·자산군)를 가져오지 못했습니다.")

    if fd is not None:
        try:
            th = fd.top_holdings
            if th is not None and len(th):
                for sym_h, row in th.head(10).iterrows():
                    top_holdings.append({
                        "symbol": str(sym_h),
                        "name": row.get("Name"),
                        "weight": float(row.get("Holding Percent")),
                    })
        except Exception:
            pass  # 채권형 등은 종목 보유가 없어 흔히 비어 있음(정상)

        try:
            sw = fd.sector_weightings
            if sw:
                sectors = dict(sw)
        except Exception:
            pass

        try:
            ac = fd.asset_classes
            if ac:
                asset_classes = dict(ac)
        except Exception:
            pass

    # 총보수: yfinance가 info의 annualReportExpenseRatio를 없애서 두 곳을 순서대로 본다.
    # funds_data 쪽은 우리와 같은 소수 단위(SPY 0.000945=0.0945%)이고,
    # info의 netExpenseRatio는 퍼센트 단위(SPY 0.0945)라 /100이 필요하다.
    expense_ratio = None
    if fd is not None:
        try:
            ops = fd.fund_operations
            v = ops.loc["Annual Report Expense Ratio", sym]
            expense_ratio = float(v) if pd.notna(v) else None
        except Exception:
            pass
    if expense_ratio is None and info.get("netExpenseRatio") is not None:
        try:
            expense_ratio = float(info["netExpenseRatio"]) / 100.0
        except (TypeError, ValueError):
            pass
    if expense_ratio is None:
        warnings.append("총보수(운용 수수료) 정보를 무료 소스에서 받지 못했습니다.")

    # 수익률: yfinance는 3y/5y는 소수(0.19=19%), ytdReturn만 % 단위(10.16=10.16%)로 준다
    # — 단위가 섞여 있어 ytd만 항상 /100. (가격으로 실측 교차검증: TLT 0.93=0.93%, SPY 10.16=~10%)
    ret_3y = info.get("threeYearAverageReturn")
    ret_5y = info.get("fiveYearAverageReturn")
    ret_ytd = info.get("ytdReturn")
    if ret_ytd is not None:
        ret_ytd = ret_ytd / 100.0

    return ETFData(
        ticker=sym, yahoo_ticker=sym, name=name, market="US", currency="USD",
        price=price, prices=prices, prices_raw=prices_raw,
        dividends=divs, index_prices=index_prices,
        benchmark_name=bench_sym or "",
        nav=info.get("navPrice"), category=category,
        basket_pe=info.get("trailingPE"), basket_pb=info.get("priceToBook"),
        div_yield=info.get("yield"), aum=info.get("totalAssets"),
        expense_ratio=expense_ratio,
        bench_pe=bench_pe, bench_yield=bench_yield, bench_label=bench_label,
        top_holdings=top_holdings, sectors=sectors, asset_classes=asset_classes,
        ret_ytd=ret_ytd, ret_3y=ret_3y, ret_5y=ret_5y,
        warnings=warnings,
    )
