"""전 종목 배수 스냅숏 — ①의 회귀 계수(ADR-0014)를 만들 학습 표본.

한국은 두 원천이 **정확히 상보적**이다:
    네이버   → per·pbr·roe   (yfinance는 한국 종목의 per·pbr을 주지 않는다: 실측 0%)
    yfinance → psr·ev_ebitda (네이버에 없다)
미국은 yfinance 하나로 넷 다 나온다.

yfinance 전 종목 수집은 불안정하다 — 한국 2,688종목 시도에서 1,655종목(62%)만 성공했고
나머지는 401 Invalid Crumb(레이트리밋)이었다. 그래서 **부분 수집을 정상으로 취급**하고,
표본이 모자라는 다리만 계수를 만들지 않는다(호출부가 피어 중앙값으로 폴백한다).
한 원천이 죽어도 다른 원천 값은 살린다 — try를 원천별로 따로 두는 이유다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

UNIVERSE_WORKERS = 12


def _num(v):
    """숫자로 못 읽히거나 유한하지 않으면 None. 무료 데이터에는 둘 다 섞인다."""
    x = pd.to_numeric(v, errors="coerce")
    try:
        return None if not np.isfinite(x) else x
    except TypeError:
        return None


# 원천 호출을 모듈 수준 얇은 래퍼로 둔다 — 테스트가 patch할 수 있고, 지연 임포트라
# 서버 기동에 임포트 비용을 얹지 않는다.
def _kr_listing():
    from .universe import get_kr_listing
    return get_kr_listing()


def _naver_fundamental(code: str) -> dict:
    from .naver import fetch_naver_fundamental
    return fetch_naver_fundamental(code)


def _info_metrics(ticker: str) -> dict:
    from .base import fetch_info_metrics
    return fetch_info_metrics(ticker)


def _us_universe():
    from .universe import get_sp1500
    return get_sp1500()


def _kr_ticker(code: str, market: str) -> str:
    from .universe import yahoo_ticker_kr
    return yahoo_ticker_kr(code, market)


def collect_kr() -> pd.DataFrame:
    """한국 보통주 전 종목의 per·pbr·psr·ev_ebitda·roe·mcap·sector."""
    listing = _kr_listing()
    pool = listing[listing["is_common"] & listing["Sector"].notna()
                   & (listing["Marcap"] > 0)]

    def one(row):
        base = {"code": row.Code, "sector": row.Sector, "mcap": float(row.Marcap),
                "per": None, "pbr": None, "psr": None, "ev_ebitda": None, "roe": None}
        # 원천마다 try를 따로 둔다 — yfinance가 레이트리밋으로 죽어도 네이버 값은 남는다
        try:
            nv = _naver_fundamental(row.Code)
            base.update(per=_num(nv.get("per")), pbr=_num(nv.get("pbr")),
                        roe=_num(nv.get("roe_approx")))
        except Exception:
            pass
        try:
            yv = _info_metrics(_kr_ticker(row.Code, row.Market))
            base.update(psr=_num(yv.get("psr")), ev_ebitda=_num(yv.get("ev_ebitda")))
        except Exception:
            pass
        return base

    with ThreadPoolExecutor(UNIVERSE_WORKERS) as ex:
        rows = list(ex.map(one, list(pool.itertuples())))
    return pd.DataFrame(rows)


def collect_us() -> pd.DataFrame:
    """S&P 1500의 per·pbr·psr·ev_ebitda·roe·mcap·sector."""
    def one(row):
        base = {"code": row.Symbol, "sector": row.Sector, "mcap": None,
                "per": None, "pbr": None, "psr": None, "ev_ebitda": None, "roe": None}
        try:
            v = _info_metrics(row.Symbol)
            base.update(mcap=_num(v.get("market_cap")), per=_num(v.get("per")),
                        pbr=_num(v.get("pbr")), psr=_num(v.get("psr")),
                        ev_ebitda=_num(v.get("ev_ebitda")), roe=_num(v.get("roe")))
        except Exception:
            pass
        return base

    with ThreadPoolExecutor(UNIVERSE_WORKERS) as ex:
        rows = list(ex.map(one, list(_us_universe().itertuples())))
    return pd.DataFrame(rows)
