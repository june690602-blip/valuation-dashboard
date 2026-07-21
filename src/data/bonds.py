"""국고채·미 국채 금리 수집 — 네이버 시장지표 front-api(무키) + FRED 무키 CSV.

- 네이버: KR{n}YT=RR / US{n}YT=RR (category=bond) 현재값·일별 시계열, 기준금리(standardInterest)
- FRED: fredgraph.csv?id=DGS… — 미국 장기 시계열(키 불필요)
- 전부 file_cache. 실패하면 예외 대신 빈 값 — 호출부(채권 페이지)는 수동 입력 폴백을 제공한다.
"""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from .cache import file_cache

_NAVER = "https://m.stock.naver.com/front-api/marketIndex"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# (만기 년, 네이버 코드) — 2026-07 실검증 완료
KR_TENORS: tuple = ((1, "KR1YT=RR"), (2, "KR2YT=RR"), (3, "KR3YT=RR"), (5, "KR5YT=RR"),
                    (10, "KR10YT=RR"), (20, "KR20YT=RR"), (30, "KR30YT=RR"))
US_TENORS: tuple = ((2, "US2YT=RR"), (10, "US10YT=RR"), (30, "US30YT=RR"))

# FRED 시리즈 (미국 곡선 보강: 단기 포함)
FRED_SERIES = {0.25: "DGS3MO", 1: "DGS1", 2: "DGS2", 3: "DGS3", 5: "DGS5",
               7: "DGS7", 10: "DGS10", 20: "DGS20", 30: "DGS30"}


def _naver_prices(code: str, page: int = 1, page_size: int = 100) -> list[dict]:
    """네이버 채권 일별 시세 1페이지. pageSize 하한 10, 상한은 서버가 거부하면 축소 재시도."""
    for ps in (page_size, 10):
        r = requests.get(f"{_NAVER}/prices", headers=_HEADERS, timeout=15,
                         params={"category": "bond", "reutersCode": code,
                                 "page": page, "pageSize": ps})
        if r.status_code == 200:
            return r.json().get("result") or []
        if ps == 10:
            break
    return []


@file_cache("bond_curve", ttl_hours=6)
def fetch_yield_curve(market: str) -> pd.DataFrame:
    """수익률곡선 스냅샷: index=만기(년), columns=[yield(%), asof]. 실패 시 빈 DF."""
    rows = []
    if market == "KR":
        for yrs, code in KR_TENORS:
            items = _naver_prices(code, page_size=10)
            if items:
                try:
                    rows.append({"tenor": yrs, "yield": float(items[0]["closePrice"]),
                                 "asof": str(items[0].get("localTradedAt", ""))[:10]})
                except (KeyError, ValueError, TypeError):
                    continue
    else:  # US — FRED 우선(테너 촘촘), 실패 시 네이버 3개 테너
        for yrs, sid in FRED_SERIES.items():
            s = _fred_series(sid)
            if s is not None and len(s):
                rows.append({"tenor": yrs, "yield": float(s.iloc[-1]),
                             "asof": s.index[-1].strftime("%Y-%m-%d")})
        if not rows:
            for yrs, code in US_TENORS:
                items = _naver_prices(code, page_size=10)
                if items:
                    try:
                        rows.append({"tenor": yrs, "yield": float(items[0]["closePrice"]),
                                     "asof": str(items[0].get("localTradedAt", ""))[:10]})
                    except (KeyError, ValueError, TypeError):
                        continue
    if not rows:
        return pd.DataFrame(columns=["yield", "asof"])
    df = pd.DataFrame(rows).set_index("tenor").sort_index()
    return df


def _fred_series(series_id: str) -> pd.Series | None:
    """FRED 무키 CSV → 일별 금리(%) 시리즈. 결측('.') 제거."""
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                         params={"id": series_id}, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), na_values=".")
        date_col, val_col = df.columns[0], df.columns[-1]
        df[date_col] = pd.to_datetime(df[date_col])
        s = df.set_index(date_col)[val_col].dropna()
        return s if len(s) else None
    except Exception:
        return None


@file_cache("bond_history_v2", ttl_hours=6)  # v2: 3년 창(days=780)으로 확장 — 옛 500일 캐시 무시
def fetch_yield_history(market: str, tenor_years: int, days: int = 780) -> pd.DataFrame:
    """일별 금리 시계열(오름차순): columns=[yield]. KR=네이버 페이지 병렬, US=FRED.

    days 기본 780 ≈ 3년(거래일). 한·미를 같은 창으로 tail해 대칭 비교가 되게 한다.
    """
    if market == "US":
        sid = FRED_SERIES.get(tenor_years)
        s = _fred_series(sid) if sid else None
        if s is None:
            return pd.DataFrame(columns=["yield"])
        return s.tail(days).to_frame("yield")

    code = dict((y, c) for y, c in KR_TENORS).get(tenor_years)
    if not code:
        return pd.DataFrame(columns=["yield"])
    # 네이버는 pageSize 요청과 무관하게 **페이지당 10건**만 준다(실측). days건을 채우려면
    # 약 days/10 페이지가 필요해 순차로는 느리다 → 페이지를 병렬로 받는다. KR 국고채 이력은
    # 약 85페이지(≈3.3년)까지 존재하며, 그 너머 페이지는 빈 응답이라 무해하다.
    max_pages = min(days // 10 + 6, 90)
    recs: list[tuple] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        pages = ex.map(lambda pg: _naver_prices(code, page=pg), range(1, max_pages + 1))
    for items in pages:
        for it in items:
            try:
                recs.append((str(it["localTradedAt"])[:10], float(it["closePrice"])))
            except (KeyError, ValueError, TypeError):
                continue
    if not recs:
        return pd.DataFrame(columns=["yield"])
    df = pd.DataFrame(recs, columns=["date", "yield"]).drop_duplicates("date")
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index().tail(days)


def current_riskfree(market: str) -> tuple[float | None, str]:
    """무위험이자율 R_f 기본값용 — 10년물 국채 수익률(소수)과 출처 라벨.

    수익률곡선에서 10년물(없으면 10에 최근접 만기)을 뽑는다. 여러 페이지(주식 WACC·
    성향테스트 CML·포트폴리오 CML)가 이 한 값을 공유해 탭 간 가정을 일치시킨다.
    실패 시 (None, "") — 호출부가 정적 기본값으로 폴백.
    """
    try:
        curve = fetch_yield_curve(market)
        if curve is None or curve.empty:
            return None, ""
        tenor = 10 if 10 in curve.index else min(curve.index, key=lambda t: abs(t - 10))
        rate = float(curve.loc[tenor, "yield"]) / 100.0
        nm = "한국 국고채" if market == "KR" else "미국 국채"
        return rate, f"{nm} {tenor:g}년"
    except Exception:
        return None, ""


@file_cache("policy_rates", ttl_hours=12)
def fetch_policy_rates() -> dict:
    """중앙은행 기준금리 {'한국은행': %, '미국 연준': %} — 실패 시 빈 dict."""
    try:
        r = requests.get(f"{_NAVER}/majors", headers=_HEADERS, timeout=15,
                         params={"category": "bond"})
        r.raise_for_status()
        out = {}
        for it in (r.json().get("result", {}).get("standardInterest") or []):
            name, price = it.get("name", ""), it.get("closePrice")
            if price is None:
                continue
            if "한국" in name:
                out["한국은행"] = float(price)
            elif "미국" in name or "연방" in name:
                out["미국 연준"] = float(price)
        return out
    except Exception:
        return {}
