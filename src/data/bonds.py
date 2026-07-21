"""국고채·미 국채 금리 수집 — 네이버 시장지표 front-api(무키).

- 네이버: KR{n}YT=RR / US{n}YT=RR (category=bond) 현재값·일별 시계열, 기준금리(standardInterest)
- **한·미 모두 네이버 한 소스로 통일**. FRED는 데이터센터 IP(예: Render)에서 타임아웃돼
  배포 시 미국 금리가 통째로 비었었다 → 두 시장 모두 네이버로 받아 만기·창(≈3년)을 대칭 제공.
- 전부 file_cache. 실패하면 예외 대신 빈 값 — 호출부(채권 페이지)는 수동 입력 폴백을 제공한다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from .cache import file_cache

_NAVER = "https://m.stock.naver.com/front-api/marketIndex"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 네이버 front-api가 한 요청에 돌려주는 최대 건수 — 실측(2026-07): pageSize 60까지 200,
# 61 이상은 400. 예전엔 100을 요청해 매번 400을 맞고 10건짜리로 폴백해 페이지 수가 6배 많았다.
_MAX_PAGE_SIZE = 60

# (만기 년, 네이버 코드) — 2026-07 실검증 완료. 한·미 모두 네이버가 전 만기·약 3년 이력 제공.
KR_TENORS: tuple = ((1, "KR1YT=RR"), (2, "KR2YT=RR"), (3, "KR3YT=RR"), (5, "KR5YT=RR"),
                    (10, "KR10YT=RR"), (20, "KR20YT=RR"), (30, "KR30YT=RR"))
US_TENORS: tuple = ((1, "US1YT=RR"), (2, "US2YT=RR"), (3, "US3YT=RR"), (5, "US5YT=RR"),
                    (7, "US7YT=RR"), (10, "US10YT=RR"), (20, "US20YT=RR"), (30, "US30YT=RR"))


def _naver_prices(code: str, page: int = 1, page_size: int = _MAX_PAGE_SIZE) -> list[dict]:
    """네이버 채권 일별 시세 1페이지. 요청한 pageSize가 거부되면 10건으로 축소 재시도."""
    for ps in (page_size, 10):
        r = requests.get(f"{_NAVER}/prices", headers=_HEADERS, timeout=15,
                         params={"category": "bond", "reutersCode": code,
                                 "page": page, "pageSize": ps})
        if r.status_code == 200:
            return r.json().get("result") or []
        if ps == 10:
            break
    return []


def _tenor_codes(market: str) -> tuple:
    """(만기, 네이버 코드) 목록 — 시장별."""
    return KR_TENORS if (market or "KR").upper() == "KR" else US_TENORS


@file_cache("bond_curve_v2", ttl_hours=6)  # v2: 한·미 모두 네이버(전 만기) — FRED 제거
def fetch_yield_curve(market: str) -> pd.DataFrame:
    """수익률곡선 스냅샷: index=만기(년), columns=[yield(%), asof]. 실패 시 빈 DF.

    한·미 모두 네이버에서 만기별 최신 종가를 병렬로 모은다."""
    codes = _tenor_codes(market)
    with ThreadPoolExecutor(max_workers=min(8, len(codes))) as ex:
        snaps = list(ex.map(lambda yc: (yc[0], _naver_prices(yc[1], page_size=10)), codes))
    rows = []
    for yrs, items in snaps:
        if not items:
            continue
        try:
            rows.append({"tenor": yrs, "yield": float(items[0]["closePrice"]),
                         "asof": str(items[0].get("localTradedAt", ""))[:10]})
        except (KeyError, ValueError, TypeError):
            continue
    if not rows:
        return pd.DataFrame(columns=["yield", "asof"])
    return pd.DataFrame(rows).set_index("tenor").sort_index()


def _naver_history_df(code: str, days: int) -> pd.DataFrame:
    """네이버 채권 일별 시세를 페이지 병렬로 모아 오름차순 시계열(columns=[yield]).

    네이버 front-api는 **한 요청에 최대 60건**을 준다(실측; `_MAX_PAGE_SIZE`). days건을 채우려면
    약 days/60 페이지면 돼, 페이지당 10건씩 받던 예전보다 요청 수가 6배 이상 줄었다. 국고채·
    미국채 모두 약 3년(≈13페이지)까지 존재하며, 그 너머 페이지는 빈 응답이라 무해하다."""
    per = _MAX_PAGE_SIZE
    max_pages = min(days // per + 2, 16)
    recs: list[tuple] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        pages = ex.map(lambda pg: _naver_prices(code, page=pg, page_size=per),
                       range(1, max_pages + 1))
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


@file_cache("bond_history_v3", ttl_hours=6)  # v3: 한·미 모두 네이버(3년 대칭) — FRED 제거
def fetch_yield_history(market: str, tenor_years: int, days: int = 780) -> pd.DataFrame:
    """일별 금리 시계열(오름차순): columns=[yield]. 한·미 모두 네이버 페이지 병렬.

    days 기본 780 ≈ 3년(거래일). 한·미를 같은 창으로 tail해 대칭 비교가 되게 한다.
    """
    code = dict((y, c) for y, c in _tenor_codes(market)).get(tenor_years)
    if not code:
        return pd.DataFrame(columns=["yield"])
    return _naver_history_df(code, days)


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
