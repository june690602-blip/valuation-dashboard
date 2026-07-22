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

from src.analysis.backtest import HORIZONS, run_backtest
from src.analysis.capital_cost import compute_capital_cost
from src.analysis.commentary import build_commentary
from src.analysis.indicators import compute_indicators
from src.analysis.scoring import (comparable_peers, peer_median,
                                   rank_peers_cheapness, sanitize_peer_frame)
from src.analysis.valuation import compute_valuation

MULTIPLE_LABELS = {"per": "PER", "pbr": "PBR", "psr": "PSR", "ev_ebitda": "EV/EBITDA",
                   "p_fcf": "P/FCF", "div_yield": "배당수익률", "peg": "PEG"}
CAT_ORDER = ["밸류에이션", "수익성", "성장성", "재무 안정성", "현금흐름"]


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
            from src.data.universe import get_kr_listing
            listing = get_kr_listing()
            if q.isdigit():
                m = listing[listing["Code"].astype(str).str.startswith(q)]
            else:
                m = listing[listing["Name"].str.contains(q, case=False, na=False, regex=False)]
            if "Marcap" in m.columns:
                m = m.sort_values("Marcap", ascending=False)
            out = []
            for _, r in m.head(limit).iterrows():
                mkt = str(r.get("Market", "") or "").upper()
                out.append({"code": str(r["Code"]), "name": str(r["Name"]),
                            "sub": "KOSDAQ" if mkt.startswith("KOSDAQ") else "KOSPI"})
            return out
        from src.data.universe import get_sp500
        sp = get_sp500()
        pref = sp[sp["Symbol"].str.upper().str.startswith(q.upper())]
        byname = sp[sp["Name"].str.contains(q, case=False, na=False, regex=False)]
        seen, out = set(), []
        for _, r in pd.concat([pref, byname]).iterrows():
            sym = str(r["Symbol"])
            if sym in seen:
                continue
            seen.add(sym)
            out.append({"code": sym, "name": str(r["Name"]),
                        "sub": str(r.get("Sector", "") or "S&P 500")})
            if len(out) >= limit:
                break
        return out
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

    trailing_52 = valid_close.tail(252)
    hi52 = num(trailing_52.max()) if len(trailing_52) else None
    lo52 = num(trailing_52.min()) if len(trailing_52) else None
    ret1y = (latest_close / num(trailing_52.iloc[0]) - 1
             if len(valid_close) >= 252 and latest_close is not None
             and num(trailing_52.iloc[0]) not in (None, 0) else None)
    pos52 = ((latest_close - lo52) / (hi52 - lo52) * 100
             if latest_close is not None and hi52 is not None and lo52 is not None
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
        "source": "Yahoo Finance · 수정주가",
        "delay_note": "무료 공개 시세로 실시간이 아니며 거래소·제공처 사정에 따라 지연될 수 있습니다.",
    }


def _band_one(band_df, q, pct, n=70) -> dict | None:
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
            "current": num(cur), "med": num(med), "own5y": num(band50.get(key)),
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


def _backtest(d, r_equity=None) -> dict | None:
    bt = run_backtest(d, kind="PER", threshold=0.30, r_equity=r_equity)
    if not bt.ok:
        return {"ok": False, "warnings": list(bt.warnings)}
    horizons = []
    for hz in HORIZONS.keys():
        ev, bs = bt.event_stats.get(hz, {}), bt.baseline_stats.get(hz, {})
        horizons.append({"h": hz, "ev_mean": num(ev.get("mean")), "ev_hit": num(ev.get("hit")),
                         "ev_n": int(ev.get("n", 0)), "base_mean": num(bs.get("mean"))})
    scatter = []
    if bt.scatter is not None:
        for _, r in bt.scatter.iterrows():
            scatter.append([num(r.get("discount") * 100), num(r.get("fwd_252") * 100)])
    equity = None
    if bt.equity is not None and not bt.equity.empty:
        eq = bt.equity
        step = max(1, len(eq) // 80)
        eq = eq.iloc[::step]
        cols = list(bt.equity.columns)
        equity = {"dates": [t.strftime("%y.%m") for t in eq.index],
                  "series": [{"name": c, "y": [num(v) for v in (eq[c] * 100).values]} for c in cols],
                  "cagr": {c: num(bt.cagr.get(c)) for c in cols}}
    ev12 = bt.event_stats.get("12개월", {})
    return {
        "ok": True, "kind": bt.kind, "threshold": num(bt.threshold),
        "methods_used": list(bt.methods_used),
        "weights": {k: num(v) for k, v in (bt.weights or {}).items()},
        "signal_days": int(bt.signal_days), "event_count": int(bt.event_count),
        "spearman": num(bt.spearman),
        "ret12": num(ev12.get("mean")), "hit12": num(ev12.get("hit")),
        "horizons": horizons, "scatter": scatter, "equity": equity,
        "never_traded": bool(bt.strategy_never_traded), "warnings": list(bt.warnings),
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
        per_q=val.per_q,
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
            "skipped": [{"method": m, "reason": r} for m, r in (val.skipped or [])],
            "estimates": [{"method": e.method, "low": num(e.low), "mid": num(e.mid),
                           "high": num(e.high), "note": e.note} for e in val.estimates],
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
        "commentary": [{"kind": c.kind, "text": c.text}
                       for c in build_commentary(d, ind, scores, cc, val)],
        "band": {
            "per": _band_one(val.per_band, val.per_q, val.per_percentile),
            "pbr": _band_one(val.pbr_band, val.pbr_q, val.pbr_percentile),
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
