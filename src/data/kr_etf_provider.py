"""한국 ETF 데이터 수집 — 네이버 금융(모바일 API) + yfinance 시세.

미국판(etf_provider.py)과 동일하게 ETFData 하나로 표준화한다. 한국은 운용사가 네이버를 통해
괴리율·추적오차·총보수를 직접 공시하므로(추정치인 미국판보다 정확) 그 공시값을 그대로 쓴다.

단, 네이버의 두 소스는 NAV 시점이 다르다 — etfItemList.nhn(당일 시세 목록)의 nav는 price와
같은 날짜 쌍이지만, etfAnalysis의 nav는 하루 뒤처진 값이라 price와 짝지으면 괴리가 완전히
틀어진다(실측: KODEX200 현재가 106,365 vs etfAnalysis.nav 113,375 → 괴리 -6.18%로 왜곡,
실제 공시 괴리율은 -0.13%). 그래서 etfAnalysis.nav는 절대 쓰지 않고, 괴리 계산용 (price, nav)
쌍은 etfItemList의 (nowVal, nav)로만 채운다. 공시 괴리율(deviationRate)은 etfAnalysis에서
별도로 가져와 deviation_rate에 담는다 — 분석 계층은 이 공시값이 있으면 우선 사용한다.

무료·비공식 API라 결측·구조 변경이 흔하므로, 개별 지표 실패는 예외 없이 None/빈 값 +
warnings 사유로 흡수한다. 시세(가격 이력) 자체를 못 받을 때만 ValueError.
"""
from __future__ import annotations

import re
import time

import pandas as pd
import requests
import yfinance as yf

from .base import fetch_index_prices, fetch_prices, fetch_prices_raw
from .models import ETFData
from .naver import fetch_naver_fundamental

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_HEADERS_LIST = {**_HEADERS, "Referer": "https://finance.naver.com/"}

_ITEM_LIST_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
_ANALYSIS_URL = "https://m.stock.naver.com/api/stock/{code}/etfAnalysis"

_LIST_TTL_SEC = 300.0  # 5분 — etfItemList가 전체 ETF(~1,150개) 배열이라 무거움

_STOCK_CODE_RE = re.compile(r"^\d{6}$")
_LEV_KW = ("레버리지", "인버스")
# 한 글자('금'·'은')로 잡으면 "TIGER 금융"·"KODEX 은행"까지 원자재로 오분류된다.
# 실제 원자재 ETF 이름에만 나오는 두 글자 이상 표현으로만 판정한다
# (반대로 "글로벌금채굴기업"처럼 금광 '주식'을 담는 ETF는 여기 걸리지 않아 주식형으로 남는다).
_COMMODITY_KW = (
    "금현물", "금선물", "골드", "gold", "은현물", "은선물", "실버", "silver",
    "귀금속", "백금", "팔라듐", "원유", "wti", "crude", "천연가스", "가스선물",
    "구리", "니켈", "알루미늄", "농산물", "옥수수", "대두", "commodity",
)
# 국가 비중을 못 받았을 때만 쓰는 이름 기반 해외형 보조 판정(비중 데이터가 있으면 그쪽이 우선).
_FOREIGN_KW = (
    "미국", "s&p", "나스닥", "다우", "중국", "차이나", "항셍", "일본", "니케이",
    "인도", "베트남", "유럽", "유로", "선진국", "신흥국", "글로벌", "글로발",
    "아시아", "대만", "필리핀", "인도네시아", "브라질", "멕시코", "리츠부동산",
)

# 모듈 레벨 캐시(프로세스 내 재사용, TTL 5분) — {종목코드: item dict}
_list_cache: dict = {"ts": 0.0, "items": {}}


# ── 파싱 헬퍼 ────────────────────────────────────────────────────────
def _normalize_code(code: str) -> str:
    """숫자만 남겨 종목코드로 정규화."""
    return re.sub(r"\D", "", str(code or ""))


def _num(v) -> float | None:
    """int/float/문자열(쉼표 포함) → float | None. 원천이 JSON 숫자라도 방어적으로 변환."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s or s in ("-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pct(v) -> float | None:
    """공시 퍼센트값(예 0.15=0.15%) → 소수(0.0015)."""
    n = _num(v)
    return (n / 100.0) if n is not None else None


def _strip_html(text: str | None) -> str | None:
    """etfSummary의 <br> 등 HTML 태그 제거 + 공백 정리."""
    if not text:
        return None
    t = re.sub(r"<[^>]+>", " ", str(text))
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def _weights_dict(raw: list | None) -> dict:
    """[{detailTypeCode, weight}] → {코드: weight/100}. sector/asset/country 공통 파서."""
    out: dict = {}
    for it in raw or []:
        key = it.get("detailTypeCode")
        w = _num(it.get("weight"))
        if key is None or w is None:
            continue
        out[str(key)] = w / 100.0
    return out


def _parse_top_holdings(raw: list | None) -> list[dict]:
    """etfTop10MajorConstituentAssets → [{'symbol','name','weight'}].

    etfWeight는 "32.77%" 문자열이며 해외/채권형 구성자산은 "-"로 온다(비중 결측) → weight=None.
    """
    out = []
    for it in (raw or [])[:10]:
        w_raw = it.get("etfWeight")
        weight = None
        if isinstance(w_raw, str):
            s = w_raw.strip().rstrip("%")
            if s and s != "-":
                weight = _num(s)
                if weight is not None:
                    weight /= 100.0
        out.append({"symbol": it.get("itemCode"), "name": it.get("itemName"), "weight": weight})
    return out


def _fetch_etf_item_list() -> tuple[dict, str | None]:
    """전체 ETF 시세 목록(네이버) → {종목코드: item}. 모듈 레벨 TTL 캐시(5분)로 재사용.

    실패해도 예외를 던지지 않는다 — 이전 캐시(만료 포함)가 있으면 그걸 쓰고, 없으면
    빈 dict를 반환해 호출부가 price/nav/aum을 다른 소스로 대체하거나 N/A 처리하게 한다.
    """
    now = time.time()
    if _list_cache["items"] and (now - _list_cache["ts"]) < _LIST_TTL_SEC:
        return _list_cache["items"], None
    try:
        r = requests.get(_ITEM_LIST_URL, headers=_HEADERS_LIST, timeout=15)
        r.raise_for_status()
        items = ((r.json() or {}).get("result") or {}).get("etfItemList") or []
        by_code = {str(it["itemcode"]): it for it in items if it.get("itemcode")}
        if not by_code:
            raise ValueError("빈 목록 응답")
        _list_cache["items"] = by_code
        _list_cache["ts"] = now
        return by_code, None
    except Exception:
        if _list_cache["items"]:
            return _list_cache["items"], ("네이버 ETF 시세 목록 갱신에 실패해 이전 캐시(다소 "
                                          "지연된 값일 수 있음)를 사용합니다.")
        return {}, ("네이버 ETF 시세 목록을 가져오지 못했습니다 — 현재가는 야후 종가로 대체되고 "
                    "NAV·순자산총액은 N/A로 표시됩니다.")


def _infer_category(name: str, asset_classes: dict, countries: dict) -> str:
    """분석 계층(src/analysis/etf.py classify())이 보는 영어 카테고리 문자열로 유형 판정."""
    nm = (name or "").lower()
    if any(k in nm for k in _LEV_KW) or "2x" in nm or "3x" in nm:
        return "Leveraged"
    bond_w = asset_classes.get("BOND") if asset_classes else None
    if bond_w is not None and bond_w > 0.5:
        return "Bond"
    if any(k in nm for k in _COMMODITY_KW):
        return "Commodity"
    if countries:
        return "Korea Equity" if countries.get("KR", 0.0) >= 0.5 else "Foreign Equity"
    # 국가 비중이 없을 때(상세 조회 실패)는 이름으로 추정 — 국내형으로 잘못 두면
    # 화면 라벨('국내 주식형')과 벤치마크(코스피)가 함께 틀어지기 때문.
    return "Foreign Equity" if any(k in nm for k in _FOREIGN_KW) else "Korea Equity"


# 해외형 벤치마크 후보 — (검색 키워드, 야후 심볼, 이름, 한국어 라벨). 위에서부터 먼저 맞는 것.
_BENCH_RULES = (
    (("나스닥", "nasdaq", "ndx"), "^IXIC", "NASDAQ", "나스닥 종합"),
    (("s&p 500", "s&p500", "sp500", "미국"), "^GSPC", "S&P 500", "S&P 500"),
    (("니케이", "nikkei", "일본"), "^N225", "NIKKEI", "닛케이 225"),
    (("항셍", "hang seng", "홍콩"), "^HSI", "HSI", "항셍지수"),
    (("차이나", "중국", "csi"), "000300.SS", "CSI300", "CSI 300"),
)


def _benchmark_for(category: str, name: str, base_index: str | None):
    """(야후 심볼, benchmark_name, 한국어 라벨) 또는 None(=비교 대상 없음).

    기초지수명을 먼저 보고(운용사 공시라 가장 정확), 없으면 ETF 이름으로 추정한다.
    채권·원자재·레버리지는 견줄 지수가 마땅치 않아 비교하지 않는다.
    """
    if category == "Korea Equity":
        return "^KS11", "KOSPI", "코스피(KOSPI)"
    if category != "Foreign Equity":
        return None
    hay = f"{base_index or ''} {name or ''}".lower()
    for keys, sym, bname, label in _BENCH_RULES:
        if any(k in hay for k in keys):
            return sym, bname, label
    return None


def _perf_map(raw: list | None) -> dict:
    """returnPerformanceList → {기간코드: 소수수익률}. 원본은 퍼센트(4.54 = 4.54%).

    네이버는 1년 이하(D1~Y1)는 누적, 1년 초과(Y3·Y5·Y10)는 연평균으로 준다 —
    미국판(yfinance threeYearAverageReturn=연평균, ytdReturn=누적)과 같은 관례라
    ETFData의 ret_* 의미가 시장 간에 어긋나지 않는다.
    """
    out: dict = {}
    for it in raw or []:
        key, val = it.get("periodTypeCode"), _pct(it.get("value"))
        if key and val is not None:
            out[str(key)] = val
    return out


def _basket_multiples(top_holdings: list[dict]) -> tuple:
    """국내주식형 바스켓 PER/PBR — 상위 보유종목 비중가중평균(비중은 모인 종목 합으로 정규화).

    종목별 조회는 기존 fetch_naver_fundamental()을 그대로 쓰고, 실패한 종목은 예외 없이
    건너뛴다. 반환: (basket_pe, basket_pb, basket_note, warnings).
    """
    warn: list[str] = []
    per_pairs: list[tuple[float, float]] = []
    pb_pairs: list[tuple[float, float]] = []

    for h in top_holdings:
        sym, w = h.get("symbol"), h.get("weight")
        if not sym or not _STOCK_CODE_RE.match(str(sym)) or w is None:
            continue
        try:
            nv = fetch_naver_fundamental(str(sym))
        except Exception:
            continue
        per, pbr = nv.get("per"), nv.get("pbr")
        if per is not None and per > 0:
            per_pairs.append((float(w), float(per)))
        if pbr is not None and pbr > 0:
            pb_pairs.append((float(w), float(pbr)))

    def _wavg(pairs):
        wsum = sum(w for w, _ in pairs)
        return (sum(w * v for w, v in pairs) / wsum, wsum) if wsum > 0 else (None, 0.0)

    basket_pe, pe_wsum = _wavg(per_pairs)
    basket_pb, _ = _wavg(pb_pairs)

    note = None
    if basket_pe is not None:
        note = (f"상위 {len(per_pairs)}종목(비중 합 {pe_wsum * 100:.1f}%) 가중평균 — "
                "펀드 전체가 아닌 상위 보유종목 기준 추정치")
    elif not top_holdings:
        warn.append("보유종목 정보가 없어 바스켓 PER·PBR을 계산하지 못했습니다.")
    else:
        warn.append("상위 보유종목의 PER·PBR 조회에 실패해 바스켓 지표를 계산하지 못했습니다.")

    return basket_pe, basket_pb, note, warn


# ── 메인 엔트리 ──────────────────────────────────────────────────────
def fetch_kr_etf(code: str) -> ETFData:
    """한국 ETF 종목코드 하나 → ETFData.

    개별 지표(NAV·괴리율·섹터·바스켓 등)는 실패해도 None/빈 값으로 두고 warnings에 남길 뿐
    예외를 던지지 않는다. 시세 이력(yfinance)을 못 받으면 분석 자체가 불가능하므로 그때만
    ValueError로 안내한다(kr_provider.py·etf_provider.py와 같은 방식).
    """
    code = _normalize_code(code)
    if not code:
        raise ValueError("ETF 종목코드를 입력하세요.")
    warnings: list[str] = []

    # 소스 C(yfinance) — 시세는 유일하게 실패 시 크래시하는 소스라 먼저 확보한다.
    tk = yahoo_ticker = prices = None
    for suffix in (".KS", ".KQ"):
        sym = f"{code}{suffix}"
        try:
            cand = fetch_prices(sym)
        except Exception:
            continue
        tk, yahoo_ticker, prices = yf.Ticker(sym), sym, cand
        break
    if prices is None:
        raise ValueError(f"'{code}' 시세를 찾을 수 없습니다 — 상장폐지·거래정지 상태이거나 "
                         "잘못된 종목코드일 수 있어요.")

    # 미조정 종가 — 배당수익률·52주 밴드용(수정종가를 쓰면 과거가 배당만큼 부풀려진다).
    # 실패해도 분석 계층이 수정종가로 폴백하므로 경고만 남기고 넘어간다.
    try:
        prices_raw = fetch_prices_raw(yahoo_ticker)
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

    # 소스 A(etfItemList.nhn) — 현재가·NAV는 반드시 이 쌍으로만(같은 날 시점).
    item_map, list_warn = _fetch_etf_item_list()
    if list_warn:
        warnings.append(list_warn)
    item = item_map.get(code)
    if item is None and item_map:
        warnings.append(f"네이버 ETF 시세 목록에서 '{code}'를 찾지 못했습니다 — "
                        "현재가는 야후 종가로, NAV·순자산총액은 N/A로 대체합니다.")

    price = _num(item.get("nowVal")) if item else None
    if price is None:
        price = float(prices.iloc[-1])
    nav = _num(item.get("nav")) if item else None
    aum = None
    if item and item.get("marketSum") is not None:
        ms = _num(item["marketSum"])
        aum = ms * 1e8 if ms is not None else None

    # 소스 B(etfAnalysis) — 공시 괴리율·총보수·섹터·바스켓 재료. nav 필드는 절대 쓰지 않는다
    # (시점이 하루 뒤처져 소스 A의 price와 짝지으면 괴리가 왜곡된다 — 모듈 docstring 참고).
    analysis: dict = {}
    try:
        r = requests.get(_ANALYSIS_URL.format(code=code), headers=_HEADERS, timeout=15)
        r.raise_for_status()
        analysis = r.json() or {}
    except Exception:
        warnings.append("ETF 상세 지표(etfAnalysis) 조회에 실패했습니다 — 총보수·괴리율·섹터·"
                        "구성종목 등 다수 항목이 N/A로 표시됩니다.")

    name = (item.get("itemname") if item else None) or analysis.get("itemName") or code

    expense_ratio = _pct(analysis.get("totalFee"))
    div_yield = _pct((analysis.get("dividend") or {}).get("dividendYieldTtm"))
    tracking_error_pub = _pct(analysis.get("chaseErrorRate"))

    deviation_rate = None
    dev_raw = _pct(analysis.get("deviationRate"))
    if dev_raw is not None:
        deviation_rate = -dev_raw if analysis.get("deviationSign") == "-" else dev_raw

    base_index = analysis.get("etfBaseIndex")
    issuer = analysis.get("issuerName")
    listed_date = analysis.get("listedDate")
    summary = _strip_html(analysis.get("etfSummary"))
    net_inflow = analysis.get("cumulativeNetInflowList") or {}

    perf = _perf_map(analysis.get("returnPerformanceList"))

    sectors = _weights_dict(analysis.get("sectorPortfolioList"))
    asset_classes = _weights_dict(analysis.get("assetPortfolioList"))
    countries = _weights_dict(analysis.get("countryPortfolioList"))
    top_holdings = _parse_top_holdings(analysis.get("etfTop10MajorConstituentAssets"))

    category = _infer_category(name, asset_classes, countries)

    # 벤치마크: 그 ETF가 실제로 따라가는 시장을 골라야 상대성과가 뜻을 갖는다. 국내형은
    # 코스피, 해외형은 기초지수·이름으로 추정한다(미국 S&P500 ETF를 코스피와 견주면 무의미).
    # 마땅한 지수를 못 고르면 자기 시세로 두어 비교 자체를 숨긴다(미국판과 같은 관례).
    benchmark_name = bench_label = ""
    index_prices = prices
    bench = _benchmark_for(category, name, base_index)
    if bench:
        sym, bname, blabel = bench
        try:
            index_prices = fetch_index_prices(sym)
        except Exception:
            warnings.append(f"{blabel} 지수 시세를 가져오지 못해 상대성과를 계산하지 않습니다.")
        else:
            benchmark_name, bench_label = bname, blabel
            # 원화로 사는 해외 ETF를 현지 통화 지수와 견주면 환율 등락이 성과 차이에 섞인다.
            if category == "Foreign Equity":
                warnings.append("원화로 거래되는 해외 ETF라, 지수 대비 성과에는 환율 변동 효과가 "
                                "함께 반영돼 있습니다.")

    basket_pe = basket_pb = basket_note = None
    if category == "Korea Equity":
        basket_pe, basket_pb, basket_note, warn2 = _basket_multiples(top_holdings)
        warnings.extend(warn2)

    return ETFData(
        ticker=code, yahoo_ticker=yahoo_ticker, name=name, market="KR", currency="KRW",
        price=price, prices=prices, prices_raw=prices_raw,
        dividends=divs, index_prices=index_prices,
        benchmark_name=benchmark_name, bench_label=bench_label,
        nav=nav, category=category,
        basket_pe=basket_pe, basket_pb=basket_pb, basket_note=basket_note,
        div_yield=div_yield, aum=aum, expense_ratio=expense_ratio,
        bench_pe=None, bench_yield=None,
        top_holdings=top_holdings, sectors=sectors, asset_classes=asset_classes,
        ret_ytd=perf.get("YTD"), ret_3y=perf.get("Y3"), ret_5y=perf.get("Y5"),
        deviation_rate=deviation_rate, tracking_error_pub=tracking_error_pub,
        base_index=base_index, issuer=issuer, listed_date=listed_date, summary=summary,
        countries=countries, net_inflow=net_inflow,
        warnings=warnings,
    )
