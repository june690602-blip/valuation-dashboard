"""분석 엔진 → JSON 직렬화 (웹 프런트엔드용).

stock.py 렌더러와 동일한 순수 파이프라인(load → indicators → scores → capital_cost →
valuation → commentary → backtest)을 돌려, Meridian 웹페이지가 그대로 그릴 수 있는
JSON 사전을 만든다. 부작용 없음 — 입력은 (market, query)뿐.

무료 데이터라 결측이 흔하므로 개별 섹션은 try/except로 감싸 하나가 실패해도 나머지는 살린다.
값이 없으면 None(→ JSON null) — 프런트가 "—"로 표기.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime
from functools import lru_cache

import numpy as np
import pandas as pd

from src.analysis.backtest import run_backtest
from src.analysis.capital_cost import compute_capital_cost
from src.analysis.commentary import GROUP_BASIS, build_commentary, verdict_conflict
from src.analysis.etf import compute_etf
from src.analysis.indicators import compute_indicators
from src.analysis.scoring import (comparable_peers, peer_median,
                                   rank_peers_cheapness, sanitize_peer_frame)
from src.analysis.valuation import CONSENSUS_METHOD as VAL_CONSENSUS_METHOD
from src.analysis.valuation import compute_valuation
from src.data.models import actual_prices

MULTIPLE_LABELS = {"per": "PER", "pbr": "PBR", "psr": "PSR", "ev_ebitda": "EV/EBITDA",
                   "p_fcf": "P/FCF", "div_yield": "배당수익률", "peg": "PEG"}
CAT_ORDER = ["밸류에이션", "수익성", "성장성", "재무 안정성", "현금흐름"]

# ETF 섹터·자산군 키(yfinance funds_data 표준 키) → 한국어 라벨
# 섹터·자산군 코드는 출처마다 표기가 달라(미국 yfinance=소문자 스네이크, 한국 네이버=대문자
# GICS 코드) 한 사전에 둘 다 담는다 — 없는 코드는 영어가 그대로 새어나가므로 추가 시 주의.
ETF_SECTOR_LABELS = {
    # 미국(yfinance)
    "technology": "정보기술", "financial_services": "금융", "healthcare": "헬스케어",
    "consumer_cyclical": "경기소비재", "communication_services": "커뮤니케이션",
    "industrials": "산업재", "consumer_defensive": "필수소비재", "energy": "에너지",
    "utilities": "유틸리티", "realestate": "부동산", "basic_materials": "소재",
    # 한국(네이버)
    "IT": "정보기술", "FINANCIALS": "금융", "HEALTHCARE": "헬스케어", "HEALTH_CARE": "헬스케어",
    "CONSUMER_DISCRETIONARY": "경기소비재", "COMMUNICATION": "커뮤니케이션",
    "INDUSTRIALS": "산업재", "CONSUMER_STAPLES": "필수소비재", "ENERGY": "에너지",
    "UTILITIES": "유틸리티", "REAL_ESTATE": "부동산", "MATERIALS": "소재",
    "UNCLASSIFIED": "기타·미분류",
}
ETF_ASSET_CLASS_LABELS = {
    # 미국(yfinance)
    "stockPosition": "주식", "bondPosition": "채권", "cashPosition": "현금",
    "preferredPosition": "우선주", "convertiblePosition": "전환사채", "otherPosition": "기타",
    # 한국(네이버)
    "EQUITY": "주식", "BOND": "채권", "CASH": "현금",
    "DERIVATIVES": "파생상품", "OTHERS": "기타",
}
# 국가 비중(한국 전용) — 네이버가 ISO 코드로 주고, MISC는 소액 다국가 합산.
ETF_COUNTRY_LABELS = {
    "KR": "한국", "US": "미국", "JP": "일본", "CN": "중국", "HK": "홍콩", "TW": "대만",
    "IN": "인도", "VN": "베트남", "GB": "영국", "DE": "독일", "FR": "프랑스",
    "CA": "캐나다", "BR": "브라질", "MISC": "기타 국가",
}


# ── 숫자·시리즈 정리 ────────────────────────────────────────────────
def num(v):
    """None/NaN/Inf/numpy → 순수 float | None (JSON 안전)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def series_list(s: pd.Series) -> dict:
    """pandas Series → {x:[라벨], y:[값]} (결측 제거)."""
    s = s.dropna()
    return {"x": [str(i) for i in s.index], "y": [num(v) for v in s.values]}


def _load(market: str, query: str, peer_count: int,
          exclude: tuple = (), extra: tuple = ()):
    if market == "KR":
        from src.data.kr_provider import KRProvider
        return KRProvider().load(query, peer_count, exclude=exclude, extra=extra)
    from src.data.us_provider import USProvider
    return USProvider().load(query, peer_count, exclude=exclude, extra=extra)


def suggest(market: str, q: str, limit: int = 8) -> list[dict]:
    """종목 자동완성 후보 — 증권사 검색창처럼 타이핑에 맞는 종목을 골라 준다.

    코드/심볼 prefix 또는 종목명 부분일치를 찾아, 시총(있으면)순으로 상위 limit개를
    [{"code", "name", "sub"}]로 반환한다(sub=KOSPI/KOSDAQ 또는 GICS 섹터).
    분석 파이프라인과 분리된 가벼운 조회 — 짧은 입력·실패 시 빈 목록(절대 예외 전파 안 함).
    """
    q = (q or "").strip()
    if not q:
        return []
    market = (market or "KR").upper()
    try:
        if market == "KR":
            from src.data.universe import get_kr_etf, get_kr_listing
            listing, etfs = get_kr_listing(), get_kr_etf()
            if q.isdigit():
                s = listing[listing["Code"].astype(str).str.startswith(q)]
                e = etfs[etfs["Code"].astype(str).str.startswith(q)] if len(etfs) else etfs
            else:
                s = listing[listing["Name"].str.contains(q, case=False, na=False, regex=False)]
                e = etfs[etfs["Name"].str.contains(q, case=False, na=False, regex=False)] if len(etfs) else etfs
            if "Marcap" in s.columns:
                s = s.sort_values("Marcap", ascending=False)
            if len(e) and "MarCap" in e.columns:
                e = e.sort_values("MarCap", ascending=False)
            out = []
            for _, r in s.iterrows():
                mkt = str(r.get("Market", "") or "").upper()
                out.append({"code": str(r["Code"]), "name": str(r["Name"]),
                            "sub": "KOSDAQ" if mkt.startswith("KOSDAQ") else "KOSPI", "kind": "stock"})
            for _, r in e.iterrows():
                out.append({"code": str(r["Code"]), "name": str(r["Name"]), "sub": "ETF", "kind": "etf"})
            return out[:limit]
        from src.data.universe import get_sp500, get_us_etf
        sp, etfs = get_sp500(), get_us_etf()
        qu = q.upper()
        seen, out = set(), []
        stock_hits = pd.concat([sp[sp["Symbol"].str.upper().str.startswith(qu)],
                                sp[sp["Name"].str.contains(q, case=False, na=False, regex=False)]])
        for _, r in stock_hits.iterrows():
            sym = str(r["Symbol"])
            if sym in seen:
                continue
            seen.add(sym)
            out.append({"code": sym, "name": str(r["Name"]),
                        "sub": str(r.get("Sector", "") or "S&P 500"), "kind": "stock"})
        etf_hits = pd.concat([etfs[etfs["Symbol"].str.upper().str.startswith(qu)],
                              etfs[etfs["Name"].str.contains(q, case=False, na=False, regex=False)]]) if len(etfs) else etfs
        for _, r in (etf_hits.iterrows() if len(etf_hits) else []):
            sym = str(r["Symbol"])
            if sym in seen:
                continue
            seen.add(sym)
            out.append({"code": sym, "name": str(r["Name"]), "sub": "ETF", "kind": "etf"})
        return out[:limit]
    except Exception:
        return []


def _defaults(market: str):
    """시장별 기본 R_f·MRP (analyze·AI 헬퍼가 같은 값을 써야 파이프라인 캐시가 적중)."""
    market = (market or "KR").upper()
    return (0.035 if market == "KR" else 0.045, 0.06 if market == "KR" else 0.05)


@lru_cache(maxsize=8)
def _pipeline(market: str, query: str, peer_count: int, rf: float, mrp: float,
              exclude: tuple = (), extra: tuple = ()):
    """load → indicators → scores → capital_cost → valuation. 순수 파이프라인(캐시).

    analyze()와 AI 헬퍼(ai_news·ai_opinion)가 공유 — 분석 직후 AI 버튼은 캐시 적중으로 빠르다.
    exclude/extra(피어 사용자 편집)는 정렬된 튜플로 받아 캐시 키에 포함된다.
    """
    from src.analysis.scoring import compute_scores
    d = _load(market, query, peer_count, exclude, extra)
    ind = compute_indicators(d)
    scores = compute_scores(d.peers, d.yahoo_ticker, d.is_financial)
    cc = compute_capital_cost(d, rf=rf, mrp=mrp)
    val = compute_valuation(d, ind, r_equity=cc.k_e)
    return d, ind, scores, cc, val


# ── 분석 진행 상태 (피어 수집 등 느린 단계를 프런트에 알림) ─────────
_PROGRESS: dict = {}
_PROGRESS_LOCK = threading.Lock()


def _progress_key(market: str, query: str) -> str:
    return f"{(market or 'KR').upper()}:{query.strip().upper()}"


def get_progress(market: str, query: str) -> dict | None:
    """진행 중이면 {'stage','done','total'}, 아니면 None. server의 /api/progress가 사용."""
    with _PROGRESS_LOCK:
        v = _PROGRESS.get(_progress_key(market, query))
        return dict(v) if v else None


def _edit_tuple(csv: str) -> tuple:
    """'a, b,' 같은 콤마 문자열 → 캐시 키로 쓸 정렬 튜플."""
    return tuple(sorted({p.strip() for p in str(csv or "").split(",") if p.strip()}))


def _ai_available() -> bool:
    try:
        from src.data.gemini import is_available
        return bool(is_available())
    except Exception:
        return False


# ── 섹션별 직렬화 ───────────────────────────────────────────────────
def _price(d) -> dict:
    """5년 수정 OHLCV와 차트 요약치를 JSON 안전 형태로 직렬화한다.

    ``dates``와 모든 시계열 배열은 같은 길이·순서를 유지한다. 무료 데이터에서는
    일부 OHLCV 컬럼이나 벤치마크 날짜가 빠질 수 있으므로, 행을 제거해 정렬을 깨는
    대신 해당 위치를 ``None``으로 남긴다. 종가는 차트와 등락 계산의 기준이라 마지막
    유효 수정종가를 사용하고, 종가가 전혀 없을 때만 기존 ``CompanyData.price``를
    현재가 폴백으로 사용한다.
    """
    from src.data.base import fetch_ohlcv

    raw = fetch_ohlcv(d.yahoo_ticker, period="5y")
    frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame()

    # fetch_ohlcv가 보장하는 DatetimeIndex를 다시 한 번 방어적으로 정규화한다.
    # 잘못된 날짜 행과 중복 날짜만 제거하며, 개별 값의 결측은 그대로 보존한다.
    if len(frame):
        try:
            idx = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
            valid_dates = ~idx.isna()
            frame = frame.iloc[np.flatnonzero(valid_dates)].copy()
            idx = idx[valid_dates]
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            frame.index = idx
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        except (TypeError, ValueError):
            frame = pd.DataFrame()

    idx = pd.DatetimeIndex(frame.index)

    def column(name: str) -> pd.Series:
        """대소문자 차이를 허용하고 누락·비수치 값을 NaN 시리즈로 만든다."""
        key = next((col for col in frame.columns
                    if str(col).casefold() == name.casefold()), None)
        if key is None:
            return pd.Series(np.nan, index=idx, dtype=float)
        values = frame[key]
        if isinstance(values, pd.DataFrame):  # 중복 컬럼명 방어
            values = values.iloc[:, -1]
        values = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
        return pd.Series(values.to_numpy(), index=idx, dtype=float)

    open_ = column("Open")
    high = column("High")
    low = column("Low")
    close = column("Close")
    vol = column("Volume")
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()

    # 벤치마크는 휴장일 차이 때문에 종목 거래일과 정확히 일치하지 않을 수 있다.
    # 합집합에서 먼저 전방 채운 뒤 종목 거래일로 되돌려 첫 행 누락도 최소화한다.
    raw_bench = getattr(d, "index_prices", None)
    if isinstance(raw_bench, pd.DataFrame):
        bench_key = next((key for key in ("close", "Close") if key in raw_bench), None)
        raw_bench = raw_bench[bench_key] if bench_key else None
    if isinstance(raw_bench, pd.Series) and len(raw_bench):
        try:
            bench_idx = pd.DatetimeIndex(pd.to_datetime(raw_bench.index, errors="coerce"))
            valid_dates = ~bench_idx.isna()
            bench = pd.to_numeric(raw_bench.iloc[np.flatnonzero(valid_dates)], errors="coerce")
            bench_idx = bench_idx[valid_dates]
            if bench_idx.tz is not None:
                bench_idx = bench_idx.tz_localize(None)
            bench.index = bench_idx
            bench = bench[~bench.index.duplicated(keep="last")].sort_index()
            bench = bench.replace([np.inf, -np.inf], np.nan)
            union_idx = bench.index.union(idx)
            bench = bench.reindex(union_idx).sort_index().ffill().reindex(idx)
        except (TypeError, ValueError):
            bench = pd.Series(np.nan, index=idx, dtype=float)
    else:
        bench = pd.Series(np.nan, index=idx, dtype=float)

    valid_close = close.dropna()
    latest_close = num(valid_close.iloc[-1]) if len(valid_close) else None
    previous_close = num(valid_close.iloc[-2]) if len(valid_close) >= 2 else None
    current = latest_close if latest_close is not None else num(getattr(d, "price", None))
    change = (latest_close - previous_close
              if latest_close is not None and previous_close is not None else None)
    change_pct = (change / previous_close
                  if change is not None and previous_close not in (None, 0) else None)

    # 52주 최고·최저는 신문·증권사와 같은 관례로 **실제 거래가격** 기준이다. 차트용
    # close는 수정종가라 과거 저점이 배당 누적분만큼 낮게 찍히고, 그만큼 현재가가 밴드
    # 위쪽에 있는 것처럼 보인다(실측 저점 괴리: KB금융 -3.13%, 코카콜라 -2.01%, 밴드 내
    # 위치 최대 3.4%p 이동). ETF 쪽(analysis/etf.py:_trend)은 이미 실거래가를 쓴다.
    band_close = valid_close
    if getattr(d, "prices", None) is not None:
        raw = actual_prices(d)
        if raw is not None and len(raw.dropna()):
            band_close = raw.dropna()
    trailing_52 = band_close.tail(252)
    hi52 = num(trailing_52.max()) if len(trailing_52) else None
    lo52 = num(trailing_52.min()) if len(trailing_52) else None
    band_cur = num(trailing_52.iloc[-1]) if len(trailing_52) else latest_close
    # 1년 수익률은 반대로 총수익 기준이라 수정종가가 맞다(배당까지 받은 실제 성과).
    adj_52 = valid_close.tail(252)
    ret1y = (latest_close / num(adj_52.iloc[0]) - 1
             if len(valid_close) >= 252 and latest_close is not None
             and num(adj_52.iloc[0]) not in (None, 0) else None)
    pos52 = ((band_cur - lo52) / (hi52 - lo52) * 100
             if band_cur is not None and hi52 is not None and lo52 is not None
             and hi52 > lo52 else None)
    asof = valid_close.index[-1].strftime("%Y-%m-%d") if len(valid_close) else None

    def arr(series: pd.Series) -> list[float | None]:
        return [num(value) for value in series.reindex(idx).values]

    return {
        # 기존 키(dates/close/vol/MA/bench/요약치)를 유지하면서 5년 전체를 제공한다.
        "dates": [date.strftime("%Y-%m-%d") for date in idx],
        "open": arr(open_), "high": arr(high), "low": arr(low),
        "close": arr(close), "vol": arr(vol),
        "ma20": arr(ma20), "ma60": arr(ma60), "ma120": arr(ma120),
        "bench": arr(bench),
        "cur": num(current), "prev_close": num(previous_close),
        "change": num(change), "change_pct": num(change_pct),
        "hi52": hi52, "lo52": lo52, "ret1y": num(ret1y), "pos52": num(pos52),
        "asof": asof,
        # 이 payload 하나에 두 계열이 섞여 있다 — 차트선·이동평균·벤치마크·1년 수익률은
        # 수정주가(총수익 기준), 52주 최고·최저와 밴드 내 위치는 실제 거래가격이다.
        # 출처 문구가 "수정주가" 하나로 전부를 설명하면 절반만 참이 되므로 범위를 밝히고,
        # 기준이 갈리는 52주 쪽은 화면에서 따로 안내한다(R3 발견 6 · #57).
        "source": "Yahoo Finance · 차트는 수정주가",
        "basis_note": "52주 최고·최저와 밴드 내 위치는 실제 거래가격 기준입니다"
                      "(신문·증권사와 같은 관례). 차트선과 최근 1년 수익률은 배당까지 "
                      "반영한 수정주가라 기준이 다릅니다.",
        "delay_note": "무료 공개 시세로 실시간이 아니며 거래소·제공처 사정에 따라 지연될 수 있습니다.",
    }


def _band_one(band_df, q, pct, n=70, quality=None) -> dict | None:
    if band_df is None or band_df.empty:
        return None
    df = band_df.dropna(subset=["price"])
    if len(df) == 0:
        return None
    step = max(1, len(df) // n)
    df = df.iloc[::step]
    dates = [t.strftime("%y.%m") for t in df.index]
    out = {"dates": dates, "price": [num(v) for v in df["price"].values], "percentile": num(pct)}
    for col in ("q10", "q25", "q50", "q75", "q90"):
        if col in df.columns:
            out[col] = [num(v) for v in df[col].values]
    # 밴드 품질(ADR-0012) — 창은 종목마다 다르므로 화면이 "5년"이라 쓰지 않고 years를 쓴다.
    # usable=false면 판정에서 빠졌다는 뜻이고, 차트는 그대로 그리되 사유를 함께 보인다.
    if quality:
        out["quality"] = {"years": num(quality["years"]), "corr": num(quality["corr"]),
                          "sd_fund": num(quality["sd_fund"]), "n": quality["n"],
                          "price_band": quality["price_band"], "usable": quality["usable"],
                          "short": quality["short"], "detail": quality["detail"]}
    return out


def _multiples(d, ind, val) -> list:
    peers = sanitize_peer_frame(d.peers)
    band50 = {"per": (val.per_q or {}).get(50), "pbr": (val.pbr_q or {}).get(50)}
    rows = []
    for key in ("per", "pbr", "psr", "ev_ebitda", "p_fcf", "div_yield", "peg"):
        cur = ind.valuation.get(key)
        med = peer_median(peers, key)
        vs, cheaper = None, None
        if cur is not None and med:
            diff = cur / med - 1
            cheaper = (diff < 0) if key != "div_yield" else (diff > 0)
            vs = abs(diff) * 100
        rows.append({
            "key": key, "label": MULTIPLE_LABELS[key],
            "current": num(cur), "med": num(med), "own_band": num(band50.get(key)),
            "vs": num(vs), "cheaper": cheaper,
            "is_pct": key == "div_yield",
        })
    return rows


def _search_key(yt, market: str):
    """피어 인덱스(yahoo_ticker) → 재검색용 안정 키. KR: 6자리 코드, US: 심볼."""
    s = str(yt or "").strip()
    if not s:
        return None
    if market == "KR":
        base = s.split(".")[0]          # 005930.KS → 005930
        return base if base.isdigit() else s
    return s                            # US 심볼은 그대로(AAPL)


def _peers(d) -> dict:
    peers = d.peers.copy()
    rows = []
    if not peers.empty:
        # 클릭-검색(q)·hover 매칭(key)에 쓰도록 인덱스(yahoo_ticker)를 함께 실어 보낸다.
        for yt, p in peers.iterrows():
            rows.append({
                "name": p.get("name"), "market_cap": num(p.get("market_cap")),
                "per": num(p.get("per")), "pbr": num(p.get("pbr")),
                "roe": num(p.get("roe")), "op_margin": num(p.get("op_margin")),
                "rev_growth": num(p.get("rev_growth")), "div_yield": num(p.get("div_yield")),
                "is_self": bool(p.get("is_self", False)),
                "q": _search_key(yt, d.market), "key": str(yt),
            })
    sp = sanitize_peer_frame(d.peers)
    scatter = []
    for yt, p in sp.iterrows():
        per, roe = num(p.get("per")), num(p.get("roe"))
        if per is not None and roe is not None:
            scatter.append({"n": p.get("name"), "per": per, "roe": roe * 100,
                            "self": bool(p.get("is_self", False)),
                            "q": _search_key(yt, d.market), "key": str(yt)})
    rank = rank_peers_cheapness(d.peers, d.is_financial)
    ranking = []
    for yt, r in rank.iterrows():
        ranking.append({"rank": len(ranking) + 1, "name": r.get("name"),
                        "combined": num(r.get("combined")), "value": num(r.get("value_score")),
                        "quality": num(r.get("quality_score")), "per": num(r.get("per")),
                        "pbr": num(r.get("pbr")), "roe": num(r.get("roe")),
                        "is_self": bool(r.get("is_self", False)),
                        "q": _search_key(yt, d.market), "key": str(yt)})
    basis = next((w for w in d.warnings if w.startswith("피어 기준")), None)
    return {"rows": rows, "scatter": scatter, "ranking": ranking,
            "sector": d.sector or d.industry or "", "basis": basis}


def _financials(d, ind) -> dict:
    unit_div = 1e12 if d.market == "KR" else 1e9
    unit = "조" if d.market == "KR" else "B"
    fin = d.financials
    years = [str(int(y)) for y in fin.index]

    def col_scaled(c):
        return [num(v / unit_div) if pd.notna(v) else None for v in fin[c]] if c in fin else []

    def series_pct(key):
        s = ind.series.get(key)
        return series_list(s) if s is not None else None

    table = {"years": years, "rows": {
        "매출액": col_scaled("revenue"),
        "영업이익": col_scaled("operating_income"),
        "순이익": col_scaled("net_income"),
        "EPS": [num(v) if pd.notna(v) else None for v in fin["eps"]] if "eps" in fin else [],
    }}
    return {
        "unit": unit, "years": years,
        "revenue": col_scaled("revenue"), "operating_income": col_scaled("operating_income"),
        "net_income": col_scaled("net_income"),
        "ocf": col_scaled("ocf"), "fcf": col_scaled("fcf"),
        "op_margin": series_pct("op_margin"), "net_margin": series_pct("net_margin"),
        "debt_ratio": series_pct("debt_ratio"), "current_ratio": series_pct("current_ratio"),
        "roe": series_pct("roe"), "roic": series_pct("roic"),
        "table": table, "is_financial": d.is_financial,
    }


def _wacc(d, cc, ind) -> dict:
    reg = []
    if cc.reg_points is not None and not cc.reg_points.empty:
        rp = cc.reg_points
        for _, r in rp.iterrows():
            reg.append([num(r.get("market")), num(r.get("stock"))])
    roic_s = ind.series.get("roic")
    return {
        "beta_l": num(cc.beta_l), "beta_u": num(cc.beta_u), "tax": num(cc.tax_rate),
        "de": num(cc.de_ratio), "k_e": num(cc.k_e), "k_d": num(cc.k_d),
        "k_d_after": num(cc.k_d * (1 - cc.tax_rate)) if (cc.k_d is not None and cc.tax_rate is not None) else None,
        "k_u": num(cc.k_u), "frp": num(cc.financial_risk_premium),
        "wacc": num(cc.wacc), "roic": num(cc.roic), "spread": num(cc.spread),
        "rf": num(cc.rf), "mrp": num(cc.mrp), "r2": num(cc.r2),
        "period_label": cc.period_label, "beta_line": num(cc.beta_l_raw or cc.beta_l),
        "reg_points": reg,
        "roic_series": series_list(roic_s) if roic_s is not None else None,
        "is_financial": d.is_financial, "warnings": list(cc.warnings),
    }


def _signal_timeline(bt, d) -> dict | None:
    """일별 저평가율 + 그때의 주가·추정 적정가 (~200포인트로 다운샘플).

    ADR-0008로 이 탭의 중심이 된 그림이다. 통계를 주장하지 않고 **모형이 언제 싸다고
    말했고 그 뒤 주가가 어떻게 갔는지**만 보여준다 — 표본이 2~4개뿐이라 통계로는 아무것도
    말할 수 없지만, 시계열은 표본 수와 무관하게 사실 그대로다.
    """
    disc = bt.discount.dropna() if bt.discount is not None else None
    if disc is None or len(disc) < 10:
        return None
    # 저평가율의 분모는 미조정(그날 실제로 내야 했던) 주가다 — backtest._rim_discount·
    # run_backtest와 같은 기준이라야 두 값이 서로 환산된다.
    px = actual_prices(d).reindex(disc.index)
    # **적정가는 종합 저평가율에서 되돌려 만든다.** bt.fair_price는 ② 밴드 레그 전용값이라
    # ②+③ 종합인 discount와 다른 값이다 — 그대로 그리면 같은 그림의 위·아래 칸이 서로
    # 맞지 않는다(하이닉스 실측: 종합 −47.2%인데 밴드 적정가는 주가 대비 −26.8%).
    # 정의상 discount = 적정가/주가 − 1 이므로 적정가 = 주가 × (1 + discount)로 환산하면
    # 두 칸이 항상 일치한다.
    fair = px * (1.0 + disc)
    step = max(1, len(disc) // 200)
    # [::step]은 마지막 원소를 놓칠 수 있다 — 최신 상태가 화면에 안 나오면 안 되므로 붙인다.
    pos = list(range(0, len(disc), step))
    if pos[-1] != len(disc) - 1:
        pos.append(len(disc) - 1)
    idx = disc.index[pos]
    return {
        "dates": [t.strftime("%Y-%m-%d") for t in idx],
        "discount": [num(v) for v in disc.reindex(idx).values],
        "price": [num(v) for v in px.reindex(idx).values],
        "fair": [num(v) for v in fair.reindex(idx).values],
        # 판정(①②③)과 이 신호(②+③)가 같은 종목에 반대를 말할 수 있다 — 화면이 그 대비를
        # 보여주려면 '지금 이 신호가 뭐라고 하는가'를 숫자로 들고 있어야 한다.
        "latest_discount": num(disc.iloc[-1]),
        "latest_price": num(px.iloc[-1]),
        "latest_fair": num(fair.iloc[-1]),
    }


def _backtest(d, r_equity=None) -> dict | None:
    """과거 신호 관찰용 데이터.

    **이벤트 스터디 통계와 전략 자산곡선은 계산은 하되 화면으로 내보내지 않는다**(ADR-0008).
    단일 종목 4년치에서 12개월 비중복 표본은 구조적으로 0~4개라 평균수익·승률이 통계가 되지
    못하고, 전략 자산곡선은 신호 품질이 아니라 시장 노출 시간을 재기 때문이다. 두 값은
    `scripts/check_backtest.py`에서 여전히 볼 수 있다 — 없앤 것이 아니라 화면에서 내렸다.
    """
    bt = run_backtest(d, kind="PER", threshold=0.30, r_equity=r_equity)
    if not bt.ok:
        return {"ok": False, "warnings": list(bt.warnings)}
    scatter = []
    if bt.scatter is not None:
        for _, r in bt.scatter.iterrows():
            scatter.append([num(r.get("discount") * 100), num(r.get("fwd_252") * 100)])
    return {
        "ok": True, "kind": bt.kind, "threshold": num(bt.threshold),
        "methods_used": list(bt.methods_used),
        "weights": {k: num(v) for k, v in (bt.weights or {}).items()},
        "signal_days": int(bt.signal_days), "event_count": int(bt.event_count),
        "n_obs": int(bt.n_obs), "spearman": num(bt.spearman),
        "scatter": scatter, "timeline": _signal_timeline(bt, d),
        "warnings": list(bt.warnings),
    }


def _consensus(d, val) -> dict | None:
    """애널리스트 컨센서스 vs 우리 모형 교차검증 데이터. 커버리지 없으면 None."""
    c = d.consensus
    if c is None or not c.has_any():
        return None
    return {
        "forward_eps": num(c.forward_eps), "forward_per": num(c.forward_per),
        "target_mean": num(c.target_mean), "target_high": num(c.target_high),
        "target_low": num(c.target_low), "n_analysts": c.n_analysts,
        "recomm_score": num(c.recomm_score), "recomm_label": c.recomm_label,
        "as_of": c.as_of, "source": c.source,
        "implied_growth": num(val.forward_growth),   # 선행 EPS / TTM EPS - 1
        "target_upside": num(c.target_mean / d.price - 1)
        if c.target_mean and d.price else None,
        "model_vs_target": num(val.fair_mid / c.target_mean - 1)
        if val.fair_mid and c.target_mean else None,
    }


def _scenario(d, val) -> dict | None:
    """비관/기준/낙관 시나리오 + 민감도 그리드. 적자·데이터 부족이면 None."""
    from src.analysis.scenario import build_scenarios
    c = d.consensus
    res = build_scenarios(
        price=d.price,
        eps_fwd=c.forward_eps if c else None,
        eps_ttm=d.latest("eps"),
        per_q=val.per_q_pricing,
        peer_per=peer_median(comparable_peers(d.peers, d.market_cap), "per"))
    if res is None:
        return None
    grid = None
    if res.grid is not None:
        grid = {"eps_labels": list(res.grid.index),
                "mult_labels": list(res.grid.columns),
                "values": [[num(v) for v in row] for row in res.grid.values]}
    return {
        "eps_base": num(res.eps_base), "eps_basis": res.eps_basis,
        "multiple_basis": res.multiple_basis,
        "cases": [{"name": cs.name, "eps_delta": num(cs.eps_delta), "eps": num(cs.eps),
                   "multiple": num(cs.multiple), "price": num(cs.price),
                   "upside": num(cs.upside)} for cs in res.cases],
        "grid": grid, "notes": res.notes,
    }


def _company(d) -> dict | None:
    if d.market == "KR":
        from src.data.naver import fetch_company_overview
        ov = fetch_company_overview(d.ticker)
        return {"summary": ov.get("summary"), "source": ov.get("source"),
                "website": None, "employees": None}
    from src.data.base import fetch_company_profile
    prof = fetch_company_profile(d.yahoo_ticker)
    return {"summary": prof.get("summary"), "source": "Yahoo Finance",
            "website": prof.get("website"), "employees": prof.get("employees")}


def _gather_news_items(d) -> list:
    """기업(종목명)+산업(업종어)+거시 헤드라인을 모아 중복 제거한 원본 리스트."""
    from src.data.news import fetch_news, fetch_topic_news
    items = []
    try:
        items += fetch_news(d.name, d.market, d.yahoo_ticker)
    except Exception:
        pass
    sector_q = (d.sector or d.industry or "").strip()
    if sector_q:
        try:
            items += fetch_topic_news(f"{sector_q} 산업" if d.market == "KR" else f"{sector_q} industry",
                                      d.market, limit=5)
        except Exception:
            pass
    try:
        macro = "기준금리 OR 물가 OR 환율" if d.market == "KR" else "Fed OR inflation OR treasury yields"
        items += fetch_topic_news(macro, d.market, limit=5)
    except Exception:
        pass
    seen, uniq = set(), []
    for it in items:
        k = it.get("title", "")[:40]
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq


def _news(d) -> list:
    # 기본 페이로드는 키워드 규칙으로 즉시 분류(무키·무지연). 서술형 AI 분석은 /api/news_ai 버튼.
    from src.analysis.ai_analysis import keyword_classify_news
    uniq = _gather_news_items(d)
    classified = keyword_classify_news(d.name, d.sector or "", uniq)
    out = []
    for it in classified:
        out.append({"title": it.get("title"), "link": it.get("link"),
                    "source": it.get("source"), "date": it.get("date"),
                    "category": it.get("category"), "tags": it.get("tags", [])})
    return out


# ── 메인 ────────────────────────────────────────────────────────────
def analyze(market: str, query: str, peer_count: int = 9,
            rf: float | None = None, mrp: float | None = None,
            include_news: bool = True,
            exclude: str = "", extra: str = "") -> dict:
    """종목 하나 → 웹 프런트가 그릴 전체 분석 JSON 사전.

    exclude/extra: 피어 사용자 편집(콤마 구분 이름·코드). 파이프라인 캐시 키에 포함.
    """
    market = (market or "KR").upper()
    drf, dmrp = _defaults(market)
    rf = drf if rf is None else rf
    mrp = dmrp if mrp is None else mrp
    ex_t, add_t = _edit_tuple(exclude), _edit_tuple(extra)

    # 느린 단계(피어 수집) 진행을 프런트가 폴링할 수 있게 리포터를 건다
    from src.data.progress import set_reporter
    pkey = _progress_key(market, query)

    def _report(stage: str, done: int, total: int):
        with _PROGRESS_LOCK:
            _PROGRESS[pkey] = {"stage": stage, "done": done, "total": total}

    set_reporter(_report)
    try:
        d, ind, scores, cc, val = _pipeline(market, query.strip(), peer_count, rf, mrp,
                                            ex_t, add_t)
    finally:
        set_reporter(None)
        with _PROGRESS_LOCK:
            _PROGRESS.pop(pkey, None)

    asof = d.prices.index[-1].strftime("%Y-%m-%d") if len(d.prices) else datetime.now().strftime("%Y-%m-%d")
    quality = [w for w in d.warnings if not w.startswith(("피어 기준", "재무제표:"))]

    # 해설은 한 번만 만들고, 판정↔근거 충돌 판단에 같은 목록을 쓴다(두 번 만들면 갈릴 수 있다).
    _commentary = build_commentary(d, ind, scores, cc, val)
    _conflict = verdict_conflict(val, [c for c in _commentary if c.group == GROUP_BASIS])

    payload = {
        "meta": {
            "name": d.name, "ticker": d.ticker, "yahoo_ticker": d.yahoo_ticker,
            "market": d.market, "currency": d.currency, "sector": d.sector,
            "industry": d.industry, "benchmark": d.benchmark_name,
            "price": num(d.price), "market_cap": num(d.market_cap),
            "asof": asof, "is_financial": d.is_financial,
            "fin_source": d.official.get("재무출처", ""),
            "sources": d.official.get("데이터출처", {}),
            "ai_available": _ai_available(),
        },
        "warnings": quality,
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "verdict": {
            "verdict": val.verdict, "gap": num(val.gap), "confidence": val.confidence,
            "dispersion": num(getattr(val, "dispersion", None)),
            "fair_low": num(val.fair_low), "fair_mid": num(val.fair_mid),
            "fair_high": num(val.fair_high),
            "fair_mid_equal": num(val.fair_mid_equal), "gap_equal": num(val.gap_equal),
            "verdict_equal": val.verdict_equal,
            "weights": {k: num(v) for k, v in (val.weights or {}).items()},
            # 컨센서스 반영 종합(①②③④) — 판정에는 쓰지 않고 화면에 나란히 세우는 값(ADR-0006).
            "fair_low_consensus": num(val.fair_low_consensus),
            "fair_mid_consensus": num(val.fair_mid_consensus),
            "fair_high_consensus": num(val.fair_high_consensus),
            "gap_consensus": num(val.gap_consensus),
            "verdict_consensus": val.verdict_consensus,
            "weights_consensus": {k: num(v) for k, v in (val.weights_consensus or {}).items()},
            "consensus_premium": num(val.consensus_premium),
            "consensus_method": VAL_CONSENSUS_METHOD,
            "fundamental_only": bool(val.fundamental_only),
            "shared_multiple_share": num(val.shared_multiple_share),
            # RIM을 쓰거나 뺀 실측 근거(ADR-0007). 화면이 원인을 단정하지 않고 잰 값을 보이게 한다.
            "book_quality": {
                "pbr": num((val.book_quality or {}).get("pbr")),
                "intangible_share": num((val.book_quality or {}).get("intangible_share")),
                "buyback_ratio": num((val.book_quality or {}).get("buyback_ratio")),
                "years": (val.book_quality or {}).get("years"),
                # ADR-0010 — 시장이 장부가를 오래 거부했는지 판별에 쓴 수치
                "pbr_5y_median": num((val.book_quality or {}).get("pbr_5y_median")),
                "underearn_years": (val.book_quality or {}).get("underearn_years"),
                "underearn_of": (val.book_quality or {}).get("underearn_of"),
                "distorted": (val.book_quality or {}).get("distorted"),
                "detail": (val.book_quality or {}).get("detail") or "",
            },
            "skipped": [{"method": m, "reason": r} for m, r in (val.skipped or [])],
            "estimates": [{"method": e.method, "low": num(e.low), "mid": num(e.mid),
                           "high": num(e.high), "note": e.note} for e in val.estimates],
            # 판정과 근거가 반대를 말할 때만 채워진다. 없으면 화면은 아무 말도 하지 않는다(#69).
            "conflict": ({"short": _conflict.short, "detail": _conflict.detail}
                         if _conflict else None),
        },
        "tiles": {
            "market_cap": num(d.market_cap), "per": num(ind.valuation.get("per")),
            "pbr": num(ind.valuation.get("pbr")), "roe": num(ind.profitability.get("roe")),
            "beta": num(cc.beta_l), "wacc": num(cc.wacc),
        },
        "indicators": {
            "valuation": {k: num(v) for k, v in ind.valuation.items()},
            "profitability": {k: num(v) for k, v in ind.profitability.items()},
            "growth": {k: num(v) for k, v in ind.growth.items()},
            "stability": {k: num(v) for k, v in ind.stability.items()},
            "cashflow": {k: num(v) for k, v in ind.cashflow.items()},
        },
        "scores": {
            "overall": num(scores.overall), "n_peers": scores.n_peers,
            "cats": {k: num(v) for k, v in scores.scores.items()},
            "details": {cat: [{"key": key, "target": num(t), "med": num(m), "score": num(s), "n": n}
                              for (key, t, m, s, n) in rows]
                        for cat, rows in scores.details.items()},
        },
        "multiples": _multiples(d, ind, val),
        # group으로 두 무리를 구분해 내보낸다 — basis(판정의 근거) / reading(이 판정을 읽는 법).
        # 화면이 문장을 뒤져서 가르지 않도록 데이터로 든다(#68).
        "commentary": [{"kind": c.kind, "text": c.text, "group": c.group, "about": c.about}
                       for c in _commentary],
        "band": {
            "per": _band_one(val.per_band, val.per_q, val.per_percentile,
                             quality=val.band_quality.get("per")),
            "pbr": _band_one(val.pbr_band, val.pbr_q, val.pbr_percentile,
                             quality=val.band_quality.get("pbr")),
        },
    }

    # 섹션별 best-effort (실패해도 나머지 유지)
    for key, fn in (("price", lambda: _price(d)), ("financials", lambda: _financials(d, ind)),
                    ("peers", lambda: _peers(d)), ("wacc", lambda: _wacc(d, cc, ind)),
                    ("backtest", lambda: _backtest(d, cc.k_e)), ("company", lambda: _company(d)),
                    ("consensus", lambda: _consensus(d, val)),
                    ("scenario", lambda: _scenario(d, val))):
        try:
            payload[key] = fn()
        except Exception as e:
            payload[key] = {"error": str(e)}
    if include_news:
        try:
            payload["news"] = _news(d)
        except Exception as e:
            payload["news"] = {"error": str(e)}
    else:
        payload["news"] = []
    return payload


# ── ETF 적정가 ──────────────────────────────────────────────────────
def _etf_price_series(d) -> dict:
    """ETF 5년 종가를 라인차트용으로 다운샘플(~250포인트, 결측 제거).

    **수정종가(총수익)를 쓴다** — 주식 주가차트와 같은 원칙이다(#57). 이 선이 답하는
    질문은 "이 ETF를 들고 있었다면 얼마 벌었나"이고, 분배금이 잦은 ETF에서 실거래가로
    그리면 분배금만큼 우하향해 실제 성과보다 나빠 보인다.

    반대로 **배당 밴드·NAV 괴리·52주 위치는 실거래가**를 쓴다(`etf._dividend_band`·
    `etf._trend`). 같은 화면 안에서 두 계열이 사는 이유는 묻는 것이 다르기 때문이고,
    그 사실은 화면에 `basis_note`로 밝힌다 — 어느 한쪽으로 통일하면 반드시 한쪽이 틀린다.
    """
    px = d.prices.dropna() if d.prices is not None else pd.Series(dtype=float)
    if len(px) == 0:
        return {"x": [], "y": []}
    step = max(1, len(px) // 250)
    px = px.iloc[::step]
    return {"x": [t.strftime("%Y-%m-%d") for t in px.index], "y": [num(v) for v in px.values]}


def _etf_holdings(d) -> list:
    """상위 보유종목(비중 내림차순, provider가 이미 정렬해 넘김) — 채권형 등은 빈 리스트."""
    return [{"symbol": h.get("symbol"), "name": h.get("name"), "weight": num(h.get("weight"))}
            for h in (d.top_holdings or [])]


def _etf_sectors(d) -> list:
    """섹터 비중을 한국어 라벨로 붙여 비중 내림차순 정렬(0 이하·결측 제외)."""
    out = []
    for key, w in (d.sectors or {}).items():
        wv = num(w)
        if wv is None or wv <= 0:
            continue
        label = ETF_SECTOR_LABELS.get(key, key.replace("_", " ").title())
        out.append({"key": key, "label": label, "weight": wv})
    out.sort(key=lambda x: x["weight"], reverse=True)
    return out


def _etf_asset_classes(d) -> list:
    """자산군 비중(주식/채권/현금 등)을 한국어 라벨로 붙여 비중 내림차순 정렬."""
    out = []
    for key, w in (d.asset_classes or {}).items():
        wv = num(w)
        if wv is None or wv <= 0:
            continue
        out.append({"label": ETF_ASSET_CLASS_LABELS.get(key, key), "weight": wv})
    out.sort(key=lambda x: x["weight"], reverse=True)
    return out


def _etf_rel_series(d) -> dict:
    """ETF vs 벤치마크 누적성과 오버레이 — 공통 거래일만(내부조인) 남겨 첫날=100으로 정규화.

    벤치마크가 자기 자신으로 폴백된 경우(benchmark_name 없음)는 비교 자체가 무의미해
    빈 시리즈를 돌려준다 — 프런트는 이때 오버레이를 숨긴다.
    """
    empty = {"x": [], "etf": [], "bench": []}
    if not d.benchmark_name:
        return empty
    e = d.prices.dropna() if d.prices is not None else pd.Series(dtype=float)
    b = d.index_prices.dropna() if d.index_prices is not None else pd.Series(dtype=float)
    df = pd.concat([e.rename("e"), b.rename("b")], axis=1).dropna()
    if len(df) == 0:
        return empty
    step = max(1, len(df) // 250)
    df = df.iloc[::step]
    e0, b0 = float(df["e"].iloc[0]), float(df["b"].iloc[0])
    if e0 == 0 or b0 == 0:
        return empty
    x = [t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t) for t in df.index]
    return {"x": x, "etf": [num(v / e0 * 100.0) for v in df["e"].values],
            "bench": [num(v / b0 * 100.0) for v in df["b"].values]}


def _etf_distributions(d) -> list:
    """연간 배당 합계 — 진행 중인 올해는 제외한 최근 6개 완전연도, 오름차순. 배당 없으면 빈 리스트."""
    div = d.dividends
    if div is None or len(div) == 0:
        return []
    idx = pd.to_datetime(div.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s = pd.Series(div.values, index=idx)
    by_year = s.groupby(s.index.year).sum()
    cur_year = datetime.now().year
    by_year = by_year[by_year.index < cur_year]
    if by_year.empty:
        return []
    by_year = by_year.sort_index().tail(6)
    return [{"year": int(y), "amount": num(v)} for y, v in by_year.items()]


def _etf_news(d) -> list:
    """ETF명(티커 폴백)으로 최근 헤드라인을 모아 키워드 규칙으로 분류.

    기업 뉴스(_news)와 동일한 아이템 모양(title/link/source/date/category/tags)을 맞춘다.
    뉴스는 부가 정보라 실패해도 분석 자체를 막지 않게 예외를 삼킨다.
    """
    try:
        from src.analysis.ai_analysis import keyword_classify_news
        from src.data.news import fetch_topic_news
        query = d.name or d.ticker
        # 검색 언어·지역이 시장을 따라가야 한다 — 'KODEX 200'을 미국 구글뉴스에 물으면 빈 결과.
        items = fetch_topic_news(query, market=(d.market or "US"), limit=12)
        classified = keyword_classify_news(query, "", items)
        return [{"title": it.get("title"), "link": it.get("link"),
                "source": it.get("source"), "date": it.get("date"),
                "category": it.get("category"), "tags": it.get("tags", [])}
                for it in classified]
    except Exception:
        return []


def analyze_etf(market: str, query: str) -> dict:
    """ETF 하나 → 웹 프런트가 그릴 ETF 적정가 분석 JSON 사전 (한국·미국).

    기업(analyze())과 달리 재무제표·피어가 없어 세 축(괴리·상대·배당밴드) 요약만 돌려준다
    (src/analysis/etf.py 참고). 시장 차이는 수집기가 흡수하므로 아래 조립은 공통이고,
    출처 표기와 추적오차 설명만 시장에 따라 갈린다(한국은 공시값, 미국은 추정치).
    """
    market = (market or "US").upper()
    query = (query or "").strip()
    if not query:
        return {"error": "ETF 티커를 입력하세요."}
    # 시장별로 수집기만 다르고 분석 엔진(compute_etf)은 같다 — 미국은 yfinance 한 소스로
    # 충분하지만, 한국은 야후가 ETF 지표(NAV·보수·구성)를 주지 않아 네이버를 섞어 쓴다.
    if market == "KR":
        from src.data.kr_etf_provider import fetch_kr_etf  # 지연 임포트(서버 기동을 빠르게)
        d = fetch_kr_etf(query)
    else:
        from src.data.etf_provider import fetch_etf
        d = fetch_etf(query)
    # 10년 국채금리 → ERP(금리 대비 이익수익률) 참고 축용. 실패 시 (None,"")면 축은 조용히 빠진다.
    from src.data.bonds import current_riskfree
    rf, rf_label = current_riskfree(market)
    r = compute_etf(d, rf=rf)

    asof = (d.prices.index[-1].strftime("%Y-%m-%d") if len(d.prices)
            else datetime.now().strftime("%Y-%m-%d"))

    return {
        "kind": "etf",
        "symbol": r.symbol, "name": r.name, "currency": r.currency, "price": num(r.price),
        "asOf": {"price": asof},
        "fund_type": r.fund_type, "type_label": r.type_label,
        "verdict": r.verdict, "gap": num(r.gap), "confidence": r.confidence, "primary": r.primary,
        "nav": num(r.nav), "premium": num(r.premium),
        # pos·lead·weak = 화면이 축을 게이지로 그리기 위한 정규화 값(0=싼 구간·100=비싼 구간).
        "axes": [{"key": a.key, "title": a.title, "value": a.value, "note": a.note,
                  "available": a.available, "pos": num(a.pos), "lead": a.lead,
                  "weak": a.weak} for a in r.axes],
        "signalCounts": dict(r.signal_counts),
        "metrics": {
            "basket_pe": num(r.basket_pe), "basket_pb": num(r.basket_pb),
            "div_yield": num(r.div_yield), "div_pct": num(r.div_pct),
            "div_median": num(r.div_median), "div_gap": num(r.div_gap),
            "pe_vs_bench": num(r.pe_vs_bench), "bench_pe": num(r.bench_pe),
            "bench_label": r.bench_label, "aum": num(r.aum),
            "expense_ratio": num(r.expense_ratio),
            "earnings_yield": num(r.earnings_yield), "erp": num(r.erp),
            "rf": num(rf), "rf_label": rf_label,
        },
        "trend": {
            "w52_low": num(r.w52_low), "w52_high": num(r.w52_high),
            "w52_pos": num(r.w52_pos), "rel_1y": num(r.rel_1y),
        },
        # 상대·역사 위치 종합(판정 보류 주식형) — 적정가가 아니라 시장 대비 평균회귀 위치.
        "relative": {"stance": r.stance, "pos": num(r.stance_pos),
                     "ratioPct": num(r.rel_ratio_pct), "reasons": list(r.stance_reasons)},
        "priceSeries": _etf_price_series(d),
        # 주식 화면과 같은 안내(#57) — 이 화면에도 두 계열이 산다.
        "priceBasisNote": "종가 라인은 분배금까지 반영한 수정주가입니다(총수익 기준). "
                          "배당 밴드·NAV 괴리·52주 위치는 실거래가 기준이라 같은 날이라도 "
                          "값이 다를 수 있습니다 — 분배금이 많은 ETF일수록 차이가 큽니다.",
        "notes": list(r.notes),
        "masked": [[label, reason] for label, reason in r.masked],
        "holdings": _etf_holdings(d),
        "sectors": _etf_sectors(d),
        "assetClasses": _etf_asset_classes(d),
        "returns": {"ytd": num(d.ret_ytd), "y3": num(d.ret_3y), "y5": num(d.ret_5y)},
        "trackingError": num(r.tracking_error),
        # 같은 '추적오차'라도 출처가 달라 의미가 다르다 — 한국은 운용사 공시값(진짜 복제오차),
        # 미국은 기초지수 시세를 못 구해 대용 벤치마크로 낸 추정치다. 오해 없게 문구를 분리한다.
        "trackingErrorNote": (
            "운용사가 공시한 추적오차율입니다 — 이 ETF가 실제로 따라가기로 한 기초지수를 "
            "얼마나 정확히 복제했는지를 뜻합니다. 낮을수록 잘 따라간 것입니다."
            if d.tracking_error_pub is not None else
            "이 ETF와 비교 벤치마크(예: 미국 전체시장 VTI)의 일간 수익률 차이를 "
            "연율화한 값입니다. 펀드가 자기 기초지수를 복제하는 오차가 아니라, "
            "비교 벤치마크 대비 상대 변동성입니다."),
        "relSeries": _etf_rel_series(d),
        "distributions": _etf_distributions(d),
        "news": _etf_news(d),
        # 한국 전용 항목 — 미국(yfinance)은 주지 않아 None/빈 값으로 나가고 화면에서 숨는다.
        "fundInfo": {
            "base_index": d.base_index, "issuer": d.issuer,
            "listed_date": d.listed_date, "summary": d.summary,
            "basket_note": d.basket_note,
        },
        "countries": sorted(
            [{"label": ETF_COUNTRY_LABELS.get(k, k), "weight": num(v)}
             for k, v in (d.countries or {}).items() if num(v) and num(v) > 0],
            key=lambda x: x["weight"], reverse=True),
        "netInflow": dict(d.net_inflow or {}),
        "sources": (["네이버 금융", "Yahoo Finance"] if market == "KR" else ["Yahoo Finance"]),
    }


# ── AI(Gemini) 헬퍼 — 버튼 클릭 시 별도 엔드포인트에서 호출 ──────────
def ai_news(market: str, query: str, peer_count: int = 9) -> dict:
    """최근 헤드라인을 Gemini가 감성·핵심이슈·촉매·리스크로 분석한 마크다운."""
    from src.analysis.ai_analysis import analyze_news
    market = (market or "KR").upper()
    rf, mrp = _defaults(market)
    d, _ind, _scores, _cc, _val = _pipeline(market, query.strip(), peer_count, rf, mrp)
    items = _gather_news_items(d)
    return {"name": d.name, "markdown": analyze_news(d.name, items)}


def ai_opinion(market: str, query: str, peer_count: int = 9) -> dict:
    """대시보드 산출 사실을 근거로 한 Gemini 종합 투자평가 마크다운."""
    from src.analysis.ai_analysis import build_opinion_context, investment_opinion
    market = (market or "KR").upper()
    rf, mrp = _defaults(market)
    d, ind, scores, cc, val = _pipeline(market, query.strip(), peer_count, rf, mrp)
    ctx = build_opinion_context(d, ind, val, cc, scores, news_summary="", risk_profile=None)
    return {"name": d.name, "markdown": investment_opinion(ctx), "context": ctx}


# ── 채권 페이지 데이터 ───────────────────────────────────────────────
def _curve_json(market: str) -> dict:
    from src.data.bonds import fetch_yield_curve
    df = fetch_yield_curve(market)
    if df is None or df.empty:
        return {"tenors": [], "yields": [], "asof": None}
    asof = str(df["asof"].iloc[-1]) if "asof" in df.columns and len(df) else None
    return {"tenors": [float(t) for t in df.index],
            "yields": [num(v) for v in df["yield"].values], "asof": asof}


def bond_data() -> dict:
    """수익률곡선(KR·US) + 기준금리 + 금리 뉴스. 시나리오 계산은 프런트(JS)에서 수행."""
    from src.data.bonds import fetch_policy_rates
    kr, us = _curve_json("KR"), _curve_json("US")
    try:
        policy = fetch_policy_rates()
    except Exception:
        policy = {}
    news = []
    try:
        from src.analysis.ai_analysis import keyword_classify_news
        from src.data.news import fetch_topic_news
        items = fetch_topic_news("기준금리 OR 국고채 금리 OR 연준", "KR", limit=8)
        for it in keyword_classify_news("", "", items):
            news.append({"title": it.get("title"), "link": it.get("link"),
                         "source": it.get("source"), "date": it.get("date"),
                         "tags": it.get("tags", [])})
    except Exception:
        news = []
    return {"kr": kr, "us": us,
            "policy": {k: num(v) for k, v in policy.items()}, "news": news}


# ── 포트폴리오 페이지 ────────────────────────────────────────────────
_PF_MARKET = {"KR": {"label": "KOSPI200", "symbol": "^KS200", "mrp": 0.06, "sigma": 0.17},
              "US": {"label": "S&P 500", "symbol": "^GSPC", "mrp": 0.05, "sigma": 0.15}}


def _market_sigma_est(symbol: str, fallback: float) -> float:
    try:
        from src.data.base import fetch_index_prices
        px = fetch_index_prices(symbol, "10y")
        weekly = px.resample("W-FRI").last().pct_change(fill_method=None).dropna().iloc[:-1]
        if len(weekly) >= 100:
            return float(weekly.std() * np.sqrt(52))
    except Exception:
        pass
    return fallback


def market_params() -> dict:
    """시장별 R_f(10년 국채)·MRP·E(Rm)·σm — 투자성향 CML·포트폴리오 CML 공유."""
    from src.data.bonds import current_riskfree
    out = {}
    for mkt, md in _PF_MARKET.items():
        try:
            r, _lbl = current_riskfree(mkt)
            rf = r if r else (0.035 if mkt == "KR" else 0.045)
        except Exception:
            rf = 0.035 if mkt == "KR" else 0.045
        sig = _market_sigma_est(md["symbol"], md["sigma"])
        out[mkt] = {"label": md["label"], "rf": num(rf), "mrp": num(md["mrp"]),
                    "er_m": num(rf + md["mrp"]), "sigma_m": num(sig)}
    return out


def _cml_all(bench: str, bench_rf: float) -> dict:
    """시장별 CML 가정. 사용자가 조정한 R_f는 선택한 벤치마크에만 적용한다."""
    out = market_params()
    if bench in out:
        out[bench]["rf"] = num(bench_rf)
        out[bench]["er_m"] = num(bench_rf + _PF_MARKET[bench]["mrp"])
    return out


def portfolio_analyze(req: dict) -> dict:
    """바스켓(자산 목록·금액) → σ-E(r) 평면·상관·성과지표·세금. 프런트가 그대로 그린다."""
    from src.analysis.portfolio import (after_tax_row, annualize,
                                        monthly_returns_krw, performance,
                                        portfolio_point, portfolio_series)
    from src.data.base import fetch_prices
    months = int(req.get("months", 60))
    bench = (req.get("bench", "KR") or "KR").upper()
    assets = req.get("assets", []) or []
    rf = req.get("rf")
    if rf is None:
        try:
            from src.data.bonds import current_riskfree
            r, _lbl = current_riskfree(bench)
            rf = r if r else (0.032 if bench == "KR" else 0.043)
        except Exception:
            rf = 0.032 if bench == "KR" else 0.043
    rf = float(rf)

    # 시세 수집 (예금은 상수 월수익으로 합류)
    prices, currencies, cash_rates, meta = {}, {}, {}, {}
    for a in assets:
        key = a.get("key") or a.get("yahoo") or a.get("ticker")
        if not key:
            continue
        meta[key] = a
        if a.get("type") == "예금" or not a.get("yahoo"):
            cash_rates[key] = float(a.get("cash_rate", 0.03))
            continue
        try:
            px = fetch_prices(a["yahoo"], "5y")
        except Exception:
            px = None
        if px is not None and len(px):
            prices[key] = px
            currencies[key] = a.get("currency", "KRW")
    try:
        fx = fetch_prices("KRW=X", "5y")
    except Exception:
        fx = None

    monthly = monthly_returns_krw(prices, fx, currencies, months=months, cash_rates=cash_rates)
    if monthly.empty or len(monthly.columns) == 0:
        return {"error": "자산 시세를 가져오지 못해 통계를 계산할 수 없습니다."}
    cols = list(monthly.columns)
    excluded = [meta[k].get("name", k) for k in meta
                if k not in cols and meta[k].get("type") != "예금" and meta[k].get("yahoo")]

    stats = annualize(monthly)
    # 금액 → 비중 (포함된 자산에 한정해 정규화)
    amt = {k: float(meta.get(k, {}).get("amount", 500) or 0) for k in cols}
    tot = sum(amt.values()) or 1.0
    import pandas as _pd
    weights = _pd.Series({k: amt[k] / tot for k in cols})

    port = portfolio_point(weights, stats["mu"], stats["cov"])
    name_of = {k: meta.get(k, {}).get("name", k) for k in cols}

    asset_rows = [{"key": k, "name": name_of[k],
                   "class": meta.get(k, {}).get("class") or meta.get(k, {}).get("type"),
                   "type": meta.get(k, {}).get("type"),
                   "weight": num(weights[k]), "mu": num(stats["mu"][k]),
                   "sigma": num(stats["sigma"][k])} for k in cols]
    cov = [[num(stats["cov"].loc[a, b]) for b in cols] for a in cols]
    corr = [[num(stats["corr"].loc[a, b]) for b in cols] for a in cols]

    # 성과지표 vs 벤치마크
    perf = None
    md = _PF_MARKET[bench]
    try:
        bench_px = fetch_prices(md["symbol"], "10y")
        if bench_px is not None and len(bench_px):
            b = bench_px if bench == "KR" else bench_px * (fx.reindex(bench_px.index).ffill() if fx is not None else 1)
            bench_m = b.resample("ME").last().pct_change(fill_method=None).dropna()
            port_m = portfolio_series(weights, monthly)
            p = performance(port_m, bench_m, rf)
            if p.get("sharpe") is not None:
                perf = {k: num(v) if isinstance(v, (int, float)) else v for k, v in p.items()}
    except Exception:
        perf = None

    # 세금 (참고용)
    tax_rows, taxed_mu = [], 0.0
    for k in cols:
        a = meta.get(k, {})
        tr = after_tax_row(a.get("type", "국내기타ETF"), float(stats["mu"][k]))
        taxed_mu += weights[k] * tr["mu_after"]
        tax_rows.append({"name": name_of[k], "rule": tr["rule"], "mu": num(stats["mu"][k]),
                         "eff_rate": num(tr["eff_rate"]), "mu_after": num(tr["mu_after"])})

    return {
        "assets": asset_rows, "labels": [name_of[k] for k in cols],
        "cov": cov, "corr": corr, "n_months": stats["n_months"],
        "port": {"er": num(port["er"]), "sigma": num(port["sigma"])},
        "cml": _cml_all(bench, rf), "bench": bench, "bench_label": md["label"], "rf": num(rf),
        "performance": perf, "excluded": excluded,
        "tax": {"rows": tax_rows, "port_pretax": num(port["er"]), "port_aftertax": num(taxed_mu)},
    }


def bond_history(market: str, tenor: int) -> dict:
    from src.data.bonds import fetch_yield_history
    market = (market or "KR").upper()
    df = fetch_yield_history(market, int(tenor))
    if df is None or df.empty:
        return {"dates": [], "yields": [], "label": None}
    dates = [t.strftime("%Y-%m-%d") for t in df.index]
    ys = [num(v) for v in df["yield"].values]
    label = ("한국 국고채" if market == "KR" else "미국 국채") + f" {int(tenor)}년"
    change_bp = (ys[-1] - ys[0]) * 100 if len(ys) > 1 and ys[0] is not None and ys[-1] is not None else None
    return {"dates": dates, "yields": ys, "label": label,
            "change_bp": num(change_bp), "n": len(ys),
            "source": "네이버 시장지표"}
